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
# 様子は毎日同じだと「14時は必ずおやつ」になり、生活ではなく時刻表に見える。
# 人格（persona.txt）の範囲で数個ずつ置き、その日はそれで通す。
# **絵の名前は sprites.json のものと揃えること**（tests/test_sprites.py が見張っている）。
GROUPS = {
    "morning": ((
        "起きたばかりで、頭がぼーっとしている",
        "コーヒーだけ飲んで、二度寝しようか迷っている",
        "布団から出たくなくて、スマホをだらだら見ている",
        "起きてはいるが、まだ何もしていない",
    ), ("wave", "daydream")),
    "day": ((
        "家でダラダラしている",
        "洗い物をあとでやろうと思って、先延ばしにしている",
        "昼ごはんを何にするか、まだ決めていない",
        "ノートPCを開いたまま、特に何もしていない",
    ), ("laptop", "write")),
    "afternoon": ((
        "昼寝やおやつでだらけている",
        "プリンをいま食べるか、夕飯のあとに取っておくか迷っている",
        "昼寝から起きたばかりで、まだ半分寝ている",
        "床に寝転がって、天井を見ている",
    ), ("snack", "bored")),
    "evening": ((
        "風呂や夕食をすませたあと",
        "夕飯は食べたが、お風呂はまだ入っていない",
        "洗濯物を畳まないまま、山にしてある",
        "夕飯のあとで、甘いものが食べたくなっている",
    ), ("book", "think")),
    "night": ((
        "夜更かし中で、ゲームかスマホを触っている",
        "寝ようと思いつつ、スマホを見続けている",
        "ゲームがきりのいいところまで行かなくて、やめられない",
        "明日こそ早く起きようと思っている",
    ), ("cards", "laugh")),
    "sleep": (("本当はもう寝ている時間",), ("sleep",)),
}

# いまの振る舞い。上から順に、最初に当てはまったものを返す（act を参照）。
ACTS = ("talk", "sleep", "worry", "sulk", "idle")

# 振る舞いに専用の絵があるもの。無いもの（idle）は、その時間帯の立ち姿のまま。
ACT_SPRITES = {"talk": "talk", "sleep": "sleep", "worry": "worry", "sulk": "sulk"}

# 言い終わってから、こちらを向いている時間。
TALK_SECONDS = 30

# 絵が変わる機嫌。**chat.MOODS のラベルと同じ文字でなければ効かない**
# （tests/test_figure.py で見張っている）。
SLEEPY_MOOD = "眠い"
SULKY_MOOD = "すねている"

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
    if group(hour) == "sleep" or mood == SLEEPY_MOOD:
        return "sleep"
    if streak_hours is not None and streak_hours >= config.LOOKOUT_SIT_HOURS:
        return "worry"
    if mood == SULKY_MOOD:
        return "sulk"
    return "idle"


def look(hour: int, now: datetime = None, **mood_and_so_on):
    """いまの姿。(描く1枚, 振る舞い) を返す。

    器はこれを受け取って描くだけ。**どちらを出すかの判断を器に持たせない。**
    """
    name = act(hour, **mood_and_so_on)
    return ACT_SPRITES.get(name) or sprite(now), name
