"""ことはの姿。いまどの絵で、どう振る舞っているか。

時間帯の区切りは、`chat.situation()` と `static/app.js` の `getAvatarGroup()` に
**二重にあった**。片方だけ直すと、画面に出る絵と口に出す言葉が食い違う。
ここを唯一の持ち主にする。言葉が要るほうは `situation()`、絵が要るほうは
`sprite()` を呼ぶ。区切りの表は1つしかない。

器（会話画面・タスクトレイのドット）は、受け取った名前を描くだけで何も
決めない。**だから器が増えても食い違わない。**

外を見ない。DBも触らない。材料は引数で渡してもらう。そのぶん、時刻と機嫌を
与えるだけで戻り値を確かめられる。
"""

from datetime import datetime

from .. import config

# 時間帯ごとの、様子と立ち姿の候補。区切りは group() が持つ。
# 候補が複数ある時間帯は、その日ぶんを日付で1つに決める（situation / sprite を参照）。
# **絵は様子と同じ数・同じ順に並べる。** 同じ式で選ぶので、i 番目の様子のときは i 番目の絵が出る。
# 数がずれると、言っていることと違う絵が出る日ができる（tests/test_figure.py が見張っている）。
# 合う絵が無い様子は、近い絵を重ねて置く（おやつ2つに snack など）。
# 様子は毎日同じだと「14時は必ずおやつ」になり、生活ではなく時刻表に見える。
# 人格（persona.txt）の範囲で数個ずつ置き、その日はそれで通す。
# **絵の名前は sprites.json のものと揃えること**（tests/test_sprites.py が見張っている）。
GROUPS = {
    "morning": ((
        "起きたばかりで、頭がぼーっとしている",
        "コーヒーだけ飲んで、二度寝しようか迷っている",
        "布団から出たくなくて、スマホをだらだら見ている",
        "起きてはいるが、まだ何もしていない",
        "歯を磨きながら、ぼーっとしている",
    ), ("daydream", "coffee", "phone", "wave", "brush")),
    "day": ((
        "家でダラダラしている",
        "洗い物をあとでやろうと思って、先延ばしにしている",
        "昼ごはんを何にするか、まだ決めていない",
        "ノートPCを開いたまま、特に何もしていない",
    ), ("chin", "dishes", "think", "laptop")),
    "afternoon": ((
        "昼寝やおやつでだらけている",
        "プリンをいま食べるか、夕飯のあとに取っておくか迷っている",
        "昼寝から起きたばかりで、まだ半分寝ている",
        "床に寝転がって、天井を見ている",
    ), ("snack", "snack", "nap", "floor")),
    "evening": ((
        "風呂や夕食をすませたあと",
        "夕飯は食べたが、お風呂はまだ入っていない",
        "洗濯物を畳まないまま、山にしてある",
        "夕飯のあとで、甘いものが食べたくなっている",
        "お風呂あがりで、頭にタオルをのせたまま水を飲んでいる",
    ), ("book", "bored", "phone", "snack", "bath")),
    "night": ((
        "夜更かし中で、ゲームかスマホを触っている",
        "寝ようと思いつつ、スマホを見続けている",
        "ゲームがきりのいいところまで行かなくて、やめられない",
        "明日こそ早く起きようと思っている",
    ), ("cards", "phone", "laugh", "write")),
    "sleep": (("本当はもう寝ている時間",), ("sleep",)),
}

# いまの振る舞い。上から順に、最初に当てはまったものを返す（act を参照）。
ACTS = ("talk", "sleep", "doze", "worry", "sulk", "happy", "idle")

# 振る舞いに専用の絵があるもの。無いもの（idle）は、その時間帯の立ち姿のまま。
ACT_SPRITES = {"talk": "talk", "sleep": "sleep", "doze": "doze", "worry": "worry", "sulk": "sulk", "happy": "happy"}

# 言い終わってから、こちらを向いている時間。返事の顔もこのあいだ残る。
# 30秒だと、返事を読み終わる前に元の姿へ戻って、絵がせわしなく替わって見えた（2026-09-24）。
TALK_SECONDS = 90

# 返事のときの顔。返答の [FACE:] のラベルと、その絵。**効くのは話した直後（talk）だけ。**
# 顔が来なければ talk のまま。絵がまだ無い顔も talk に置いておき、絵ができたら右を書き換える。
# ラベルを変えるときは fixed_rules.txt の一覧も揃えること（tests/test_figure.py が見張っている）。
FACES = {
    "笑う": "laugh",
    "嬉しい": "happy",
    "照れる": "fidget_shy",
    "考える": "think",
    "困る": "worry",
    "むっとする": "sulk",
    "驚く": "surprised",
}

# 所作（fidget_*）の出しどころ。**載っていない所作は、いつ出してもよい。**
# 書いた条件だけで絞る。when は時間帯（group の名前）、months は月、moods は機嫌のラベル。
# 出すかどうかと間は器が決める。脳は「いまは合わないもの」を渡すだけ（fidgets_off）。
_CHEERFUL = ("ふつう", "機嫌がいい")
FIDGET_FIT = {
    "fidget_yawn":    dict(when=("morning", "afternoon", "night")),
    "fidget_stretch": dict(when=("morning", "afternoon")),
    "fidget_pillow":  dict(when=("afternoon", "night")),
    "fidget_hungry":  dict(when=("day", "evening")),
    "fidget_cold":    dict(months=(11, 12, 1, 2, 3)),
    "fidget_fan":     dict(months=(6, 7, 8, 9)),
    # はしゃぐ所作は、疲れているときには出さない
    "fidget_spin":    dict(moods=_CHEERFUL),
    "fidget_giggle":  dict(moods=_CHEERFUL),
    "fidget_hum":     dict(moods=_CHEERFUL),
    "fidget_kick":    dict(moods=_CHEERFUL),
    # ため息は、機嫌がいいときには出さない
    "fidget_sigh":    dict(moods=("ふつう", "疲れ気味")),
}

# 絵ごとの、いま何をしているかの一言。**画面に出ている姿を、本人に言わせるため。**
# 「プリン食べてる？」に「そうだよー」と返せるように、脳が最後に押し出した絵の説明を
# プロンプトに1行載せる（chat.build_prompt）。所作（fidget_*）も載せておく: 返事の顔
# （fidget_shy）や、器が数秒出す所作を脳が拾う道が将来できても、表はここ1つで済む。
# **sprites.json の全部の絵に1行ある**こと（tests/test_sprites.py が見張っている）。
LOOKS = {
    "talk": "立って、こちらを向いて話している",
    "wave": "立って、手を振っている",
    "daydream": "立ったまま上を見て、ぼーっとしている",
    "laptop": "床に座って、開いたノートPCの画面を見ている",
    "write": "低い机に向かって、ノートにペンで何か書いている",
    "snack": "床に座って、スプーンでプリンを食べている",
    "bored": "床にうつ伏せで、あごを手にのせて退屈そうにしている",
    "book": "座って、両手で本を持って読んでいる",
    "think": "立って、指をあごに当てて、よそを見ながら考えている",
    "cards": "座って、トランプを手に持ってにやにやしている",
    "laugh": "立って、口元に手を当てて目を閉じて笑っている",
    "sleep": "枕に横になって、目を閉じて眠っている",
    "worry": "立って、胸の前で手を組んで、困った顔をしている",
    "sulk": "立って、腕を組んで頬をふくらませ、そっぽを向いている",
    "happy": "目を閉じて満面の笑みで、両手を少し上げてつま先で跳ねている",
    "surprised": "目を見開いて口を小さく開け、胸の前で両手を上げてのけぞっている",
    "doze": "床に座って薄紫の毛布にくるまり、頭を垂れてうとうとしている",
    "coffee": "白いマグを両手で持って、眠そうな半目で小さくあくびをしている",
    "phone": "床にうつ伏せで足を上げて、スマホを見ている",
    "dishes": "重なった食器を横目に見て、頬をかきながら苦笑いしている",
    "nap": "床に座って、寝癖のまま片目をこすっている",
    "floor": "床に仰向けで手足を投げ出し、天井を見ている",
    "walk": "机の端をゆっくり歩いている",
    "chin": "低い机に肘をついて頬杖をつき、半目でぼんやりしている",
    "brush": "立って、歯ブラシで歯を磨きながらぼんやりしている",
    "bath": "お風呂あがりで頭に白いタオルをのせ、水の入ったコップを持っている",
    "fidget_sigh": "目を閉じて肩を落とし、片手を腰に当ててため息をついている",
    "fidget_stretch": "両腕を頭の上に伸ばして、目を閉じて大きくあくびをしている",
    "fidget_look": "手を額にかざして、肩越しに横を見ている",
    "fidget_yawn": "片手で口を覆って、目を閉じてあくびをしている",
    "fidget_hair": "指で髪をくるくる巻きながら、下を見ている",
    "fidget_sway": "後ろで手を組んで、かかとで体を揺らしながら鼻歌を歌っている",
    "fidget_hood": "両手でパーカーのフードを頭にかぶっている",
    "fidget_shoes": "ポケットに手を入れて、自分のスニーカーを見ながら足先を鳴らしている",
    "fidget_sleeve": "長い袖に両手を隠して、眠そうに笑っている",
    "fidget_spin": "髪をなびかせて、その場でくるっと回っている",
    "fidget_peek": "体を傾けて、両手を目のまわりに当てて横をのぞいている",
    "fidget_hum": "目を閉じて首をかしげ、片手を上げて鼻歌を歌っている",
    "fidget_pocket": "パーカーのポケットに両手を入れて、少し猫背でよそを見ている",
    "fidget_giggle": "片手で口を隠して、目を閉じて肩を上げてくすくす笑っている",
    "fidget_shy": "パーカーの紐をいじりながら下を向いて、少し赤くなっている",
    "fidget_nod": "腕を組んで目を閉じ、満足そうにうなずいている",
    "fidget_hungry": "両手でお腹を押さえて、食べ物のことを考えて口をとがらせている",
    "fidget_cold": "自分の腕を抱えて、パーカーを引き寄せて少し震えている",
    "fidget_fan": "片手で顔をあおいで、暑そうに半目になっている",
    "fidget_sit": "床に座って膝を抱え、小さく笑っている",
    "fidget_lean": "床に座って両手を後ろについて、上を見ている",
    "fidget_pillow": "床に座って薄紫の枕を抱え、あごをのせて眠そうにしている",
    "fidget_cross": "床にあぐらで座って膝に手を置き、目を閉じて落ち着いている",
    "fidget_roll": "床に横向きで丸くなって、腕を枕にしている",
    "fidget_kick": "床にうつ伏せであごを手にのせ、足をぱたぱたさせている",
}

# 絵が変わる機嫌。**chat.MOODS のラベルと同じ文字でなければ効かない**
# （tests/test_figure.py で見張っている）。
SLEEPY_MOOD = "眠い"
SULKY_MOOD = "すねている"
HAPPY_MOOD = "機嫌がいい"

# 申告が無いときの、時間帯ぶんの機嫌。**chat.MOODS のラベルと同じ文字**で
# なければ効かない（tests/test_figure.py が見張っている）。
# 効かせるのは言葉だけ。絵は group() が決めるので、ここに「眠い」を置くのは
# **すでに布団の時間帯だけ**にしてある。PRIOR で絵が変わることはない。
MOOD_PRIOR = {
    "morning": "ふつう",
    "day": "ふつう",
    "afternoon": "ふつう",
    "evening": "ふつう",
    "night": "ふつう",
    "sleep": SLEEPY_MOOD,
}

# 「眠い」を引きずってよい時間帯。ここを出たら、その申告は持ち越さない。
SLEEPY_GROUPS = ("night", "sleep")


def group(hour: int) -> str:
    """その時刻の時間帯。夜更かしは日付をまたぐので、ここだけ書き方が違う。"""
    if 6 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "day"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    if hour >= 21 or hour < 2:
        return "night"
    return "sleep"


def _pick(name: str, choices, now: datetime):
    """その日ぶんを1つに決める。**その日のあいだは変わらない。**

    見るたびに変わると、同じ時間帯なのに様子や姿が入れ替わって落ち着かない。
    日付と時間帯から決める。様子と絵は同じ式で選ぶので、揃って変わる。
    """
    return choices[(now.day + len(name)) % len(choices)]


def situation(hour: int, now: datetime = None) -> str:
    """その時間のことはの様子。プロンプトに入れる言葉。

    時刻は引数で受ける（時計に関係なく確かめられるように）。日付は now で
    渡し、渡さなければ今日。同じ日・同じ時間帯なら、何度呼んでも同じ。
    """
    now = now or datetime.now()
    name = group(hour)
    return _pick(name, GROUPS[name][0], now)


def prior(hour: int) -> str:
    """申告が無いときの機嫌。時間帯から埋める。

    生活の型（朝はぼーっと、日中はダラダラ、深夜は眠い）は、もともと
    `situation()` が言葉で持っている。ここはその機嫌ぶんの写しではなく、
    **穴埋めの既定値**として使う。
    """
    return MOOD_PRIOR[group(hour)]


def keeps(hour: int, mood: str) -> bool:
    """その時刻に、その機嫌を引きずってよいか。

    **「眠い」は起きている時間帯まで持ち越さない。** 深夜に一度そう言うと、
    薄れるまでの6時間、昼になっても眠いままだった。話しているあいだは薄れが
    進まない（chat._finish）ので、朝から晩まで眠いと言い、布団の絵が続く。
    時刻で外すほうが、寝起きが時間帯どおりになる。
    """
    if mood == SLEEPY_MOOD:
        return group(hour) in SLEEPY_GROUPS
    return True


def sprite(now: datetime = None) -> str:
    """いまの立ち姿。**その日のあいだは変わらない。**

    見るたびに変わると、同じ時間帯なのに姿が入れ替わって落ち着かない。
    日付と時間帯から1枚に決める。選び方は app.js が先にやっていたものを
    そのまま持ってきてある（片方を置き換えるまで、両方が同じ絵を出す）。
    """
    now = now or datetime.now()
    name = group(now.hour)
    return _pick(name, GROUPS[name][1], now)


def act(hour: int, mood: str = "", said_ago: float = None,
        streak_hours: float = None) -> str:
    """いまの振る舞い。上から順に、最初に当てはまったもの。

    said_ago は自発発言からの経過秒、streak_hours は同じアプリを続けて
    触っている時間。分からないものは None で渡してよい。
    """
    if said_ago is not None and said_ago < TALK_SECONDS:
        return "talk"
    if group(hour) == "sleep":
        return "sleep"
    # 「眠い」は布団ではなく、起きたままうとうと。寝ている時間帯（上）とは分ける
    if mood == SLEEPY_MOOD:
        return "doze"
    if streak_hours is not None and streak_hours >= config.LOOKOUT_SIT_HOURS:
        return "worry"
    if mood == SULKY_MOOD:
        return "sulk"
    if mood == HAPPY_MOOD:
        return "happy"
    return "idle"


def look(hour: int, now: datetime = None, face: str = "", **mood_and_so_on):
    """いまの姿。(描く1枚, 振る舞い) を返す。

    器はこれを受け取って描くだけ。**どちらを出すかの判断を器に持たせない。**
    face は直前の返事の顔（FACES のラベル）。話した直後のあいだだけ、talk の代わりに出す。
    """
    name = act(hour, **mood_and_so_on)
    if name == "talk" and FACES.get(face):
        return FACES[face], name
    return ACT_SPRITES.get(name) or sprite(now), name


def fidgets_off(now: datetime = None, mood: str = "") -> list:
    """いまは合わない所作の名前。器はこれを除いた中から選ぶ。

    「合うもの」ではなく「合わないもの」を渡すのは、所作の一覧を持っているのが
    器（sprites.json）だから。表に無い所作は、足せばそのまま出る。
    """
    now = now or datetime.now()
    off = []
    for name, fit in FIDGET_FIT.items():
        if (group(now.hour) not in fit.get("when", (group(now.hour),))
                or now.month not in fit.get("months", (now.month,))
                or mood not in fit.get("moods", (mood,))):
            off.append(name)
    return off


def describe(picture: str) -> str:
    """その絵で、いま何をしているか。知らない絵なら空（その行は載せない）。"""
    return LOOKS.get(picture or "", "")
