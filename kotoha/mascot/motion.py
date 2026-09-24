"""暇なときの動き。1つの動きは1つのクラス。

以前は `__main__.py` の `draw()`・`_move()`・`_stop_motion()` に動きの種類ごとの
分岐があり、動きを1つ足すと3か所を触った。分岐を1つ忘れると「終わらない動き」や
「間が更新されない」になり、テストでは捕まえにくかった（2026-09-24）。

ここでは動きが自分のことを全部持つ。器（`Mascot`）は **どれを始めるかと、
終わったら片づける** ことしかしない。

    class Nod(Motion):
        kind = "nod"                 # 名前。重複しない
        gap = (20.0, 60.0)           # 終わってから次に始めるまでの間（秒）
        length = 1.0                 # 続く長さ（秒）。None なら自分で終わりを決める（Stroll）

        @classmethod
        def begin(cls, me, now):     # 始められるなら実体を返す。素材が無いなどなら None
            return cls(now)

        def shift(self, now):        # 行の帯のずらし (上, 下, dx)。無ければ None
            ...

足すときは、このファイルにクラスを書いて `MOTIONS` に並べるだけ。並び順が優先順位
（先に書いたものから試す）。**`tests/test_motion.py` が、並べた動きに必要なものが
揃っているかを見張る。**

動きは「絵を替える」「行の帯をずらす」「窓を動かす」の3つの手しか使わない
（`picture` / `shift` / `advance`）。脳は関わらない（[11 動き]）。
"""

import math
import random

from . import sheet

# 散歩。机の端を少し歩いて止まる。
STROLL_PX_MIN, STROLL_PX_MAX = 60, 220       # 一度に歩く距離
STROLL_SPEED = 1                             # 1ティック（40ms）に進むドット
STROLL_STEP_SECONDS = 0.26                   # 足のコマ・足踏みを替える間
STROLL_BOB = 1                               # 一歩おきに浮くドット
STROLL_STEP_DX = 1                           # 一歩おきに脚の帯をずらすドット（0 で止める）
# 歩きの絵が向いている側。向いていない側へ行くときは返す。
# いまの walk.png は**左**を向いている。True のままだと両方向とも後ろ歩きになる（2026-09-24）。
WALK_FACES_RIGHT = False

# 所作。fidget_* の絵を数秒だけ出して戻る。
FIDGET_SECONDS = 3.2

# 小さな動き。絵を替えずに、上半身を揺らす・首をかしげる。
SWAY_SECONDS, TILT_SECONDS = 2.4, 2.0


class Motion:
    """1つの動き。始まりの時刻を持ち、いまどう見えるかを答える。"""

    kind = ""
    gap = (0.0, 0.0)
    length = None

    def __init__(self, now: float):
        self.started = now

    @classmethod
    def begin(cls, me, now: float):
        """始められるなら実体を返す。素材が無い・合う所作が無いなら None。"""
        return cls(now)

    def finished(self, now: float) -> bool:
        return self.length is not None and now >= self.started + self.length

    def advance(self, me, now: float) -> bool:
        """1ティック進める。続けられなければ False（器が片づける）。"""
        return True

    def picture(self, base: str):
        """出す絵の名前。None なら脳の絵のまま。"""
        return None

    def tag(self, me):
        """絵のコマ（歩きの足など）。無ければ None。"""
        return None

    def mirror(self) -> bool:
        return False

    def shift(self, now: float):
        """行の帯のずらし (上, 下, dx)。無ければ None。"""
        return None

    def breathes(self) -> bool:
        """このあいだも呼吸するか。歩くときは止める。"""
        return True

    def lift(self) -> int:
        """足元から浮かせるドット。"""
        return 0

    def end(self, me) -> None:
        """終わったときの片づけ。歩いた先を覚えるなど。"""


class Stroll(Motion):
    """散歩。`walk` の絵で横に歩き、画面の端か決めた距離で止まる。"""

    kind = "stroll"
    gap = (300.0, 720.0)

    def __init__(self, now: float):
        super().__init__(now)
        self.direction = random.choice((-1, 1))
        self.left = random.randint(STROLL_PX_MIN, STROLL_PX_MAX)
        self.step_at, self.step = now, 0

    @classmethod
    def begin(cls, me, now: float):
        return cls(now) if "walk" in me.sheet.frames else None

    def advance(self, me, now: float) -> bool:
        if self.left <= 0:
            return False
        if now - self.step_at >= STROLL_STEP_SECONDS:
            self.step_at, self.step = now, self.step + 1
        if not me.dot.walk(self.direction * STROLL_SPEED):
            return False                      # 画面の端。引き返さず、ここで止まる
        self.left -= STROLL_SPEED
        return True

    def picture(self, base: str):
        return "walk"

    def tag(self, me):
        tags = me.sheet.tags("walk")
        return tags[self.step % len(tags)] if tags else None

    def mirror(self) -> bool:
        return (self.direction < 0) == WALK_FACES_RIGHT

    def shift(self, now: float):
        # 脚のコマが無い絵でも、脚の帯を一歩おきに左右へずらせば足が動いて見える
        if not STROLL_STEP_DX:
            return None
        return (sheet.SEAM, 1.0, STROLL_STEP_DX if self.step % 2 else -STROLL_STEP_DX)

    def breathes(self) -> bool:
        return False

    def lift(self) -> int:
        return STROLL_BOB if self.step % 2 else 0

    def end(self, me) -> None:
        me.remember_place(me.dot.x, me.dot.y)


class Fidget(Motion):
    """所作。`fidget_*` の絵を数秒だけ出して、元の姿に戻る。"""

    kind = "fidget"
    gap = (120.0, 300.0)
    length = FIDGET_SECONDS

    def __init__(self, now: float, name: str):
        super().__init__(now)
        self.name = name

    @classmethod
    def begin(cls, me, now: float):
        # 持っている絵から、脳が「いまは合わない」と言ったものを除く
        fits = [n for n in me.sheet.fidgets() if n not in me.fidgets_off]
        return cls(now, random.choice(fits)) if fits else None

    def picture(self, base: str):
        return self.name


class Sway(Motion):
    """揺れ。脚より上を右・戻す・左・戻す。絵は替えない。"""

    kind = "sway"
    gap = (40.0, 120.0)
    length = SWAY_SECONDS

    def __init__(self, now: float):
        super().__init__(now)
        self.side = random.choice((-1, 1))

    def shift(self, now: float):
        # 一往復を正弦で。丸めると 0, +1, 0, -1 の4段になる
        phase = (now - self.started) / self.length
        return (0.0, sheet.SEAM, self.side * round(math.sin(2 * math.pi * phase)))


class Tilt(Motion):
    """首かしげ。首より上を片側へ1ドット。絵は替えない。"""

    kind = "tilt"
    gap = (40.0, 120.0)
    length = TILT_SECONDS

    def __init__(self, now: float):
        super().__init__(now)
        self.side = random.choice((-1, 1))

    def shift(self, now: float):
        return (0.0, sheet.NECK, self.side)


# 暇なときに試す順。先に書いたものから、間が来ていれば始める。
# 揺れと首かしげは別々に間を持つので、合わせて 20〜60 秒に一度ほど小さく動く。
MOTIONS = (Stroll, Fidget, Sway, Tilt)


class Idle:
    """暇なときの動きの取り回し。いま動いているものと、それぞれの次の時刻。

    器はこれに `now` と「動いてよいか」を渡すだけ。どれを始めるか・いつ終えるかは
    動きの側が持っている。
    """

    def __init__(self, now: float, motions=MOTIONS):
        self.motions = motions
        self.current = None
        self.next = {m.kind: now + random.uniform(*m.gap) for m in motions}

    def tick(self, me, now: float, idle: bool) -> None:
        if self.current is None:
            if not idle:
                return
            for motion in self.motions:
                if now >= self.next[motion.kind]:
                    started = motion.begin(me, now)
                    if started is not None:
                        self.current = started
                        return
            return
        if not idle or self.current.finished(now) or not self.current.advance(me, now):
            self.stop(me, now)

    def stop(self, me, now: float) -> None:
        """途中でもやめる。脳が姿を替えたとき・触られたときも、ここを通る。"""
        if self.current is None:
            return
        motion = self.current
        self.current = None
        motion.end(me)
        self.next[motion.kind] = now + random.uniform(*motion.gap)
