"""作る絵の表。**ここに1行足せば、次の `python -m tools.sprite_gen` で1枚増える。**

name:   img/dot/<name>.png の名前。figure.py と sprites.json で使う名前と同じ
pose:   正プロンプトの {pose}。姿勢と表情だけ書く（髪や服は共通部分にある）
source: 元にする絵。"self" なら img/dot/<name>.png（既存の絵を同じ筆致に描き直す）。
        それ以外は「新しいポーズ」で、その名前の**描き直し済みのドット絵**を元に1回だけ回す。
        LoRA ありでは Denoise 1.0（実質 t2i）なので、元絵は大きさと位置の手がかりにしかならない。
        姿勢の近いものを選ぶ（立ち→talk、座り→snack、寝→bored）
blink:  まばたき差分を作るか。寝ている絵は要らない
frames: 動きのコマ。{タグ: 足す言葉} で、本体を元に frame_window の中だけ塗り直す。
        `<name>-<タグ>.png` になり、器がコマ送りに使う（歩きの足など）
eye_window / frame_window: 塗り直す窓（中身の幅・高さに対する 左, 右, 上, 下）

名前の決まり（器が名前で見分ける）:
  walk       歩き。frames に f1, f2（足の前後）
  fidget_*   手持ちぶさたの所作。器が暇なときに数秒だけ出して戻す
"""

# 絵柄の参照（IP-Adapter）。**この絵の筆致に全部を寄せる。** 描き直した talk を使う。
# weight は 1.0 前後。上げすぎると構図まで talk に引かれてポーズが出ない。
# **試したが逆効果だった（色が濁り輪郭が崩れる）。0 で切ってある。** 節の実装は残す。
STYLE_REF = "talk"
STYLE_WEIGHT = 0
STYLE_TYPE = "style transfer"

# 本人 LoRA（tools/sprite_gen/lora で学習。docs/08「LoRA を作る」）。ComfyUI の Lora フォルダからの相対名。
# 2026-09-23 の1周目は final（6 epoch）を採用。強さは 1.0。空文字にすると LoRA 無しで回る。
LORA = "kotoha" + chr(92) + "kotoha.safetensors"
LORA_STRENGTH = 1.0
# LoRA のトリガー語。プロンプトの先頭に置く。
TRIGGER = "kotoha, "

# 新しいポーズに足す頭身の指定。**LoRA だけだと元の絵より頭が小さく、背が高く出る**
# （頭が全身の 35% 前後。talk は 45% 前後）。2026-09-23 の surprised / doze から。
PROPORTION = ", (chibi:1.3), (big head:1.2), 2 heads tall, short body, short legs"
# 色味の基準。新しいポーズは髪と肌をこの絵の明るさに寄せる（tone.py）。空文字で切る。
TONE_REF = "talk"

# 全体で1つの Seed。**変えると全部の絵の筆致が変わる**ので、変えるなら全部作り直す。
SEED = 20260923

# 既存の絵を元にするときの Denoise。0.6 は「線と色をそろえて、ポーズと構図は残す」。
RESTYLE_DENOISE = 0.6
# 新しいポーズを、描き直し済みのドット絵から起こすときの Denoise。
# 0.8 で姿勢が変わり、筆致は残る。**0.85 で顔が崩れて荒い 8bit 調になる**ので上げない。
# 姿勢をもっと変えたいときは Denoise ではなく、**姿勢の近い既存の絵を source に選ぶ。**
# 寝そべり（daydream / bored 元）は崩れやすいので 0.65 に落とす（poses の denoise）。
# **2段（線画でポーズ→ドット化→整える）は試して捨てた。** 線が太く黒い線画のまま残る。
POSE_DENOISE = 0.8
# LoRA ありのときの新ポーズ。0.8 では元絵の姿勢が残りすぎて動かないので、1.0（実質 t2i）で起こす。
# 元絵は構図の座標（大きさ・位置）の手がかりにしかならない。
if LORA:
    POSE_DENOISE = 1.0
# まばたき。目の窓だけ塗り直す。0.6 では閉じない絵が多く、0.75〜0.85 で両目が閉じる。窓が狭いので顔の他は変わらない。
BLINK_DENOISE = 0.8
# 目の窓（中身の幅・高さに対する 左, 右, 上, 下）。寝そべっている絵は poses の eye_window で下げる。
# 幅を狭めるのは、髪まで塗り直すと窓の中だけ髪の色が変わるから。下を 0.40 で止めるのは口を変えないため。
EYE_WINDOW = (0.28, 0.72, 0.20, 0.40)
LOW_EYE_WINDOW = (0.28, 0.72, 0.45, 0.68)
# 口の窓（口パク差分 `-mouth.png`）。目の下から顎まで。目の窓と同じく、顔が低い絵は poses の mouth_window で下げる。
# 差分を作るのは mouth=True の絵だけ（talk と返事の顔）。会話画面が声を出しているあいだ本体と交互に出す。
MOUTH_WINDOW = (0.32, 0.68, 0.36, 0.52)
MOUTH_DENOISE = 0.8
# 脚の窓。歩きのコマで足の前後だけを描き直す。
LEGS_WINDOW = (0.15, 0.85, 0.62, 1.0)
# コマを描き直すときの Denoise。まばたきより広い窓なので少し高め。
FRAME_DENOISE = 0.75

POSES = {
    # 既存（ポーズはそのまま、筆致をそろえる）
    "talk":     dict(mouth=True, pose="standing, one hand near mouth, small open mouth, talking", source="self"),
    "wave":     dict(pose="standing, waving one hand, cheerful", source="self"),
    "daydream": dict(pose="standing, looking up, blank sleepy expression, daydreaming", source="self"),
    "laptop":   dict(pose="sitting on the floor with an open laptop, looking at the screen", source="self"),
    "write":    dict(pose="sitting at a low table, writing in a notebook with a pen", source="self"),
    "snack":    dict(pose="sitting on the floor, eating pudding with a spoon, happy open mouth", source="self"),
    "bored":    dict(pose="lying on stomach on the floor, chin on hands, bored expression", source="self", eye_window=LOW_EYE_WINDOW),
    "book":     dict(pose="sitting, reading a book held in both hands", source="self"),
    "think":    dict(mouth=True, pose="standing, one finger on chin, looking aside, thinking", source="self"),
    "cards":    dict(pose="sitting, holding playing cards, playful grin", source="self"),
    "laugh":    dict(mouth="closed", pose="standing, laughing with eyes closed, one hand near mouth", source="self"),
    "sleep":    dict(pose="lying down asleep on a pillow, eyes closed, peaceful", source="self", blink=False),
    "worry":    dict(mouth=True, pose="standing, hands clasped in front, worried expression, sweat drop", source="self"),
    # sulk は横顔で口が小さく、口パク差分は髪のほうが動いて見えたので付けない（2026-09-23）
    "sulk":     dict(pose="standing, arms crossed, cheeks puffed, pouting, looking away", source="self"),
    # 機嫌ぶん（新規）。tired は 2026-09-23 に不採用（「疲れ気味」は絵を持たず、時間帯の立ち姿のまま）
    "happy":    dict(mouth="closed", pose="big smile, eyes closed in joy, both hands raised slightly, bouncing on toes", source="wave"),
    # 返事の顔ぶん（figure.FACES）。話した直後だけ出る
    # 頭を大きくしたので目が 32〜41% に下がる。既定の目の窓（20〜40%）では片目が閉じきらない
    "surprised": dict(mouth="closed", pose="surprised, wide open eyes, small open mouth, both hands raised near chest, leaning back slightly", source="talk",
                      eye_window=(0.25, 0.75, 0.26, 0.44)),
    # 夜に「眠い」とき。布団の sleep とは別の、起きているがうとうとしている絵
    # 毛布は出なかった（床にぺたんと座る）。座りで目が 40〜48% にあるので目の窓を下げる。
    # Denoise 0.9 は元絵（snack）の配置を少し残したほうが頭身が締まったため。
    # まばたきは pose から「drowsy half-closed eyes」を外して作った（残すと閉じない）
    "doze":     dict(pose="sitting on the floor wrapped in a lavender blanket, nodding off, head drooping, drowsy half-closed eyes", source="snack",
                     denoise=0.9, eye_window=(0.25, 0.75, 0.33, 0.52)),
    # 様子ぶん（新規）。laundry は 2026-09-23 に不採用（洗濯物の様子は phone の絵で出す）
    "coffee":   dict(pose="holding a white mug with both hands, sleepy half-closed eyes, small yawn", source="talk"),
    "phone":    dict(pose="lying on stomach on the floor, holding a smartphone, bored expression, feet up", source="daydream", denoise=0.65, eye_window=LOW_EYE_WINDOW),
    "dishes":   dict(pose="looking sideways at a small stack of dishes, awkward smile, one hand scratching cheek", source="think"),
    "nap":      dict(pose="sitting on the floor, rubbing one eye, messy hair, half asleep", source="snack"),
    "floor":    dict(pose="lying on back on the floor, arms spread, looking up, blank expression", source="daydream", denoise=0.65, eye_window=LOW_EYE_WINDOW),
    # 動き（器が自分の都合で使う。脳の表には載せない）。1周目の LoRA では横向きが出ず未採用
    # 2周目の試み: 真横ではなく斜め前向き（three-quarter）で歩かせる。トレイの 120 ドットなら横に動けば歩いて見える
    # 2026-09-23 に斜め前向きの本体（Seed 20260928）を採用。**脚のコマは作らない**: 脚の窓の塗り直しは
    # Denoise 0.75 でも 0.95 でも本体とほぼ同じ足にしかならなかった。歩きの動きはトレイが一歩おきに
    # 1 ドット浮かせて出す（mascot.STROLL_BOB）。コマを試すなら frames={"f1": ...} を戻す
    "walk":     dict(pose="walking, three-quarter view, body turned to the right, facing slightly right, mid step, arms relaxed at sides, calm", source="talk"),
    # 2026-09-23 の2周目。様子ぶん（表に足す）と所作
    # 座りで目が 30〜45% にあるので目の窓を下げる（既定では半目にしかならなかった）
    "chin":     dict(pose="sitting at a low table, chin resting on one hand, elbow on the table, bored half-closed eyes, idle", source="write",
                     eye_window=(0.25, 0.75, 0.30, 0.48)),
    "brush":    dict(pose="standing, brushing teeth with a toothbrush in one hand, sleepy half-closed eyes, blank face", source="talk"),
    # 1回目（首にタオル）はタオルが出ず「水を飲んでいるだけ」になったので、頭に被せる言い方にした
    "bath":     dict(pose="standing, (white towel on head:1.3), towel draped over hair, after bath, damp hair, relaxed content smile, holding a glass of water", source="talk"),
    # proud（ドヤ顔）と fidget_tilt（首かしげ）は 2026-09-23 に候補4つずつ出したが、ドヤにも傾きにも見えず不採用
    "proud":    dict(mouth=True, pose="smug proud face, chest out, one hand on hip, other hand on chest, confident closed-eye grin", source="wave"),
    "fidget_sigh": dict(pose="sighing, eyes closed, shoulders dropped, small exhale, one hand on hip, tired", source="worry"),
    "fidget_tilt": dict(pose="head tilted to one side, curious expression, hands clasped behind back, small smile", source="talk"),
    # 所作（fidget_*）。暇なときに器が数秒だけ出す。**穏やかな動きだけ**にする（驚いた顔などは
    # 唐突に見える）。立っているものは talk、座っているものは snack、寝ているものは bored を元にする。
    "fidget_stretch": dict(pose="stretching both arms up above head, eyes closed, big yawn", source="wave"),
    "fidget_look":    dict(pose="looking over shoulder to the side, curious, one hand raised to brow", source="think"),
    "fidget_hair":    dict(pose="twirling a strand of hair with one finger, looking down, relaxed", source="talk"),
    "fidget_yawn":    dict(pose="yawning with one hand over mouth, eyes closed, sleepy", source="think"),
    "fidget_sway":    dict(pose="rocking on heels, hands clasped behind back, humming, content smile", source="talk"),
    "fidget_hood":    dict(pose="pulling the hoodie hood up over head with both hands, playful", source="think"),
    "fidget_shoes":   dict(pose="looking down at own sneakers, one foot tapping, hands in hoodie pocket", source="talk"),
    "fidget_sleeve":  dict(pose="hands hidden in oversized sleeves, sleeves pulled over hands, sleepy smile", source="talk"),
    "fidget_spin":    dict(pose="twirling around once, hair flowing outward, arms slightly out, cheerful", source="laugh"),
    "fidget_peek":    dict(pose="leaning to one side, peeking sideways, hands cupped around eyes", source="think"),
    "fidget_hum":     dict(pose="eyes closed, humming a tune, head tilted, small smile, one hand raised", source="laugh"),
    "fidget_pocket":  dict(pose="both hands in hoodie pockets, slouching slightly, relaxed, looking away", source="talk"),
    "fidget_giggle":  dict(pose="giggling behind one hand, eyes closed, shoulders raised", source="laugh"),
    "fidget_shy":     dict(mouth=True, pose="shy, fidgeting with hoodie drawstring, looking down, light blush", source="talk"),
    "fidget_nod":     dict(pose="nodding, eyes closed, satisfied smile, arms folded", source="wave"),
    "fidget_hungry":  dict(pose="holding own stomach with both hands, thinking about food, slight pout", source="worry"),
    "fidget_cold":    dict(pose="hugging own arms, shivering slightly, hoodie pulled tight, cheeks flushed", source="worry"),
    "fidget_fan":     dict(pose="fanning face with one hand, warm, tired half-closed eyes", source="think"),
    "fidget_sit":     dict(pose="sitting on the floor hugging knees, relaxed, small smile", source="sulk"),
    "fidget_lean":    dict(pose="sitting on the floor leaning back on both hands, looking up, relaxed", source="snack"),
    "fidget_pillow":  dict(pose="sitting on the floor hugging a lavender pillow, chin on pillow, sleepy", source="book"),
    "fidget_cross":   dict(pose="sitting cross-legged on the floor, hands on knees, calm, eyes closed", source="write"),
    "fidget_roll":    dict(pose="lying on side on the floor, curled up, one arm under head, relaxed",
                           source="bored", denoise=0.65, eye_window=LOW_EYE_WINDOW),
    "fidget_kick":    dict(pose="lying on stomach on the floor, feet kicking up in the air, chin on hands, content",
                           source="daydream", denoise=0.65, eye_window=LOW_EYE_WINDOW),
}

# まばたき差分に足す言葉。
# "eyes closed" だけだと片目をつぶる（ウインク）ことが多い。
# 「まつ毛が二重」対策: 閉じた目を1本の線にする言い方。上まつ毛が開いた目のまま残らないように。
BLINK = "(closed eyes:1.4), both eyes closed, eyes shut, closed eyes drawn as a single thin curved line, no upper eyelashes, sleepy"
# まばたきの段では、共通プロンプトから外す言葉。開いた目の指定が残っていると閉じない。
BLINK_DROP = "red-brown eyes, "
# 口パク差分に足す言葉。口の窓だけ塗り直す。目は窓の外なので変わらない。
MOUTH = "(open mouth:1.4), mouth wide open, talking, small round open mouth"
# 本体がもう口を開けている絵（laugh / happy / surprised）は、逆に閉じた口の差分を作る（mouth="closed"）。
# 交互に出したときに動いて見えるのは、本体と差分の口が違うときだけ。
MOUTH_CLOSED = "(closed mouth:1.4), mouth closed, lips closed, small gentle smile"
# 口の段では、口の開け閉めや笑いの指定が pose に残っていると効かないので外す言葉。
MOUTH_DROP_WORDS = ("small open mouth", "open mouth", "closed mouth", "pouting", "cheeks puffed",
                    "laughing", "big smile", "happy")

POSITIVE = (
    TRIGGER + "{style}chibi, 1girl, solo, full body, plain white background, "
    "very long wavy pale beige blonde hair, ash blonde, bangs over eyes, red-brown eyes, "
    "purple crescent moon hair ornament with tassel on left side, "
    "oversized black hoodie with lavender drawstrings, light grey shorts, "
    "white sneakers with purple accents, "
    "clean thick outlines, flat pastel colors, limited palette, cute, soft expression, "
    "{pose}"
)
NEGATIVE = (
    "realistic, 3d, photo, multiple girls, 2girls, text, watermark, signature, "
    "extra limbs, deformed hands, bad anatomy, blurry, jpeg artifacts, "
    "gradient background, complex background, scenery, cropped, out of frame, "
    "purple hair, gradient hair, multicolored hair, yellow hair, golden hair, saturated hair, purple eyes, blue eyes, "
    "floating symbols, sparkles"
)
# 2段目（筆致をそろえる）はドット絵の指定を付ける。1段目（ポーズを作る）は付けない。
# Animagine は "pixel art" を高い Denoise で回すと荒い 8bit 調に崩れる。
PIXEL_STYLE = "pixel art, "
