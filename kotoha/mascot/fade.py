"""姿の切り替えを、前の絵から次の絵へ短く溶かす（クロスフェード）。

絵は止まったコマの束で、姿が替わる瞬間はどうしてもパッと変わる。間のポーズの
絵は作らず（姿の組み合わせぶん要る上に、生成でも安定しない）、**前の絵と次の絵を
短く混ぜる**だけで、目には「動いた」ように映る。

器（`__main__.py` の `draw`）との約束は1つ: 毎ティック、**出そうとしている絵と
その鍵**を `frame()` に通し、返ってきた絵を出す。鍵が替わった瞬間から SECONDS の
あいだ、前に出していた絵から新しい絵へ溶かして返す。鍵が同じなら（まばたき・
呼吸・帯のずらし）そのまま返す。溶かす途中に鍵がまた替わったら、**いま見えている
混ざった絵**から次へ溶かす（前の姿へ戻ってから、にはしない）。

混ぜるのは `sheet.Frame.blended`。ここは時間と鍵しか見ない。
"""

# 溶かす長さ（秒）。長いと、腕や小物の違う姿のあいだで二重写しに見える。
SECONDS = 0.2
# 何段で溶かすか。40ms の時計なら 0.2 秒に 5 段。段ごとに混ぜた絵を作る。
STEPS = 5
# 溶かさずにパッと替える絵。驚いた顔は、驚きらしく一瞬で。
INSTANT = frozenset({"surprised"})


class Crossfade:
    """鍵が替わったら、前の絵から次の絵へ SECONDS だけ溶かす。"""

    def __init__(self, seconds: float = SECONDS, steps: int = STEPS, instant=INSTANT):
        self.seconds = seconds
        self.steps = max(1, steps)
        self.instant = frozenset(instant)
        self._key = None          # いま出している姿の鍵
        self._shown = None        # 最後に返した絵（次の溶かし始めになる）
        self._from = None         # 溶かし始めの絵
        self._since = None        # 溶かし始めた時（time）。None なら溶かしていない

    def step(self, now: float) -> int:
        """いま何段目か。0 なら溶かしていない（= 次の絵そのもの）。"""
        if self._since is None:
            return 0
        # 0.12 / 0.2 * 5 が 2.9999… になるので、段に切る前に丸める（float の癖）。
        passed = round((now - self._since) / self.seconds * self.steps, 6)
        if passed >= self.steps:
            return 0
        return self.steps - int(passed)

    def fading(self, now: float) -> bool:
        return self.step(now) > 0

    def cut(self) -> None:
        """溶かしを打ち切り、見ていた絵も忘れる。**次に姿が替わってもパッと出る**
        （出ていく前の手振りは、待つあいだ時計が止まるので溶かせない）。"""
        self._since = None
        self._from = None
        self._shown = None

    def frame(self, key, frame, now: float):
        """出そうとしている絵を通す。溶かしの途中なら混ざった絵を返す。

        key は「姿」を表すもの（絵の名前と向き）。まばたきや呼吸の差は鍵に入れない。
        frame が None（知らない絵）なら、そのまま None を返して器に任せる。
        """
        if frame is None:
            return None
        if key != self._key:
            name = key[0] if isinstance(key, tuple) else key
            if (self._shown is not None and self._key is not None
                    and name not in self.instant
                    and self._shown.width == frame.width and self._shown.height == frame.height):
                self._from, self._since = self._shown, now
            else:
                self._since, self._from = None, None  # 最初の1枚・大きさ違い・驚き顔はそのまま
            self._key = key
        step = self.step(now)
        if step == 0:
            self._since, self._from = None, None      # 溶かし終わり
            shown = frame
        else:
            # step は残りの段数。5 段なら次の絵の重みが 1/5, 2/5, ... 5/5 と増えていく。
            weight = 1.0 - (step - 1) / self.steps
            shown = self._from.blended(frame, weight)
        self._shown = shown
        return shown
