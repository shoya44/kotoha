"""素材を、画面へ出せる形にして抱えておく。

`UpdateLayeredWindow` に渡す絵は、**不透明さを先に掛けた並び（プリマルチプライ
済みBGRA）**でなければならない。掛け忘れると、髪の先に黒い縁が出る。読み込んだ
その場で掛けてしまえば、描くときは転送するだけになる。

全部まとめても1MB強なので、起動時に読み切って持つ。絵を出すたびに読むと、
そのたびに引っかかる。

当たり判定のために、不透明さだけの並びも持っておく。**透明なところのクリックは
後ろへ通す**ので、毎回そこを見る（window.py の `WM_NCHITTEST`）。
"""

import json

from .. import config
from . import png

SPRITE_DIR = config.BASE_DIR / "kotoha" / "serve" / "static" / "sprite"
MANIFEST = SPRITE_DIR / "sprites.json"
# 器はこの実寸を使う。会話画面と同じもの。
SIZE = "full"
BLINK_SUFFIX = "-blink"
# 呼吸で1行抜くときの、抜く高さ（上からの割合）。脚のあたり。揺れと足踏みの境でもある。
SEAM = 0.72
# 首のあたり（上からの割合）。首かしげは、ここより上だけをずらす。
NECK = 0.48


class Frame:
    """1枚ぶん。転送用の並びと、当たり判定用の不透明さ。

    呼吸・揺れ・首かしげ・足踏みは、**行の帯を1ドットずらした同じ絵**で作る。
    行単位のコピーだけなので速く、一度作ったら持っておく（`_variants`）。
    """

    __slots__ = ("width", "height", "bgra", "alpha", "_squashed", "_mirrored", "_variants")

    def __init__(self, image: png.Image):
        self._squashed = None
        self._mirrored = None
        self._variants = {}
        self.width = image.width
        self.height = image.height
        self.bgra = bytearray(len(image.px))
        self.alpha = bytearray(image.width * image.height)
        source = image.px
        for i in range(0, len(source), 4):
            a = source[i + 3]
            if a == 255:
                self.bgra[i] = source[i + 2]
                self.bgra[i + 1] = source[i + 1]
                self.bgra[i + 2] = source[i]
            elif a:
                self.bgra[i] = source[i + 2] * a // 255
                self.bgra[i + 1] = source[i + 1] * a // 255
                self.bgra[i + 2] = source[i] * a // 255
            self.bgra[i + 3] = a
            self.alpha[i >> 2] = a

    def _blank(self) -> "Frame":
        """同じ大きさの空の1枚。変形した絵の入れ物。"""
        other = Frame.__new__(Frame)
        other.width, other.height = self.width, self.height
        other.bgra = bytearray(self.width * self.height * 4)
        other.alpha = bytearray(self.width * self.height)
        other._squashed, other._mirrored, other._variants = None, None, {}
        return other

    def _copy_row(self, other: "Frame", y: int, source_y: int, dx: int = 0) -> None:
        """元の source_y 行を、other の y 行へ dx ドット横にずらして写す。はみ出たぶんは捨てる。"""
        width = self.width
        src = source_y * width
        dst = y * width
        if dx >= 0:
            count = width - dx
            other.bgra[(dst + dx) * 4:(dst + width) * 4] = self.bgra[src * 4:(src + count) * 4]
            other.alpha[dst + dx:dst + width] = self.alpha[src:src + count]
        else:
            count = width + dx
            other.bgra[dst * 4:(dst + count) * 4] = self.bgra[(src - dx) * 4:(src + width) * 4]
            other.alpha[dst:dst + count] = self.alpha[src - dx:src + width]

    def squashed(self) -> "Frame":
        """1ドットぶん縮めた同じ絵。**足元は動かさない。**

        窓ごと持ち上げると、影も足も一緒に浮いて「跳ねている」ように見える。
        息を吸うのは胸から上なので、下を留めたまま縦だけ詰める。1ドットあれば、
        動いていることは分かる。

        抜く1行は脚のあたり（SEAM）から。顔の中で抜くと、目や口が歪んで見える。
        """
        if self._squashed is not None:
            return self._squashed
        other = self._blank()
        seam = int(self.height * SEAM)
        for y in range(1, self.height):
            self._copy_row(other, y, y - 1 if y < seam else y)
        other._squashed = other
        self._squashed = other
        return other

    def shifted(self, top: float, bottom: float, dx: int) -> "Frame":
        """上から top〜bottom（高さの割合）の帯だけを、横に dx ドットずらした同じ絵。

        - 揺れ: 脚より上（0〜SEAM）を ±1。足元は動かない
        - 首かしげ: 首より上（0〜NECK）を ±1
        - 足踏み: 脚（SEAM〜1）を ±1。歩きのコマが無くても足が動いて見える

        帯の外の行はそのまま。ずらしてはみ出た1列は捨て、空いた1列は透明。
        """
        key = (top, bottom, dx)
        cached = self._variants.get(key)
        if cached is not None:
            return cached
        if dx == 0:
            self._variants[key] = self
            return self
        other = self._blank()
        first, last = int(self.height * top), int(self.height * bottom)
        for y in range(self.height):
            self._copy_row(other, y, y, dx if first <= y < last else 0)
        self._variants[key] = other
        return other

    def mirrored(self) -> "Frame":
        """左右を返した同じ絵。歩く向きに合わせるのに使う。一度作ったら持っておく。"""
        if self._mirrored is not None:
            return self._mirrored
        width, height = self.width, self.height
        other = self._blank()
        for y in range(height):
            row = self.bgra[y * width * 4:(y + 1) * width * 4]
            flipped = bytearray(width * 4)
            for x in range(width):
                flipped[x * 4:(x + 1) * 4] = row[(width - 1 - x) * 4:(width - x) * 4]
            other.bgra[y * width * 4:(y + 1) * width * 4] = flipped
            other.alpha[y * width:(y + 1) * width] = self.alpha[y * width:(y + 1) * width][::-1]
        other._mirrored = self
        self._mirrored = other
        return other

    def opaque_at(self, x: int, y: int) -> bool:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        return self.alpha[y * self.width + x] > 16


class Sheet:
    """絵の一式。名前で引く。

    まばたきの差分は、無ければ None。**無い絵はまばたきしないだけ**で、
    出ないということはない。
    """

    def __init__(self, folder=None):
        self.folder = folder or SPRITE_DIR
        self.frames = {}
        self.blinks = {}
        # 動きのコマ。{名前: {タグ: Frame}}。歩きの足など。
        self.motions = {}
        self.size = (0, 0)

    def load(self) -> "Sheet":
        manifest = json.loads((self.folder / "sprites.json").read_text(encoding="utf-8"))
        width, height = manifest["sizes"][SIZE]
        self.size = (width, height)
        for name, info in manifest["sprites"].items():
            self.frames[name] = Frame(png.load(self.folder / SIZE / f"{name}.png"))
            if info.get("blink"):
                self.blinks[name] = Frame(
                    png.load(self.folder / SIZE / f"{name}{BLINK_SUFFIX}.png"))
            for tag in info.get("frames", ()):
                self.motions.setdefault(name, {})[tag] = Frame(
                    png.load(self.folder / SIZE / f"{name}-{tag}.png"))
        return self

    def frame(self, name: str, blinking: bool = False, tag: str = None):
        """その絵。知らない名前なら None（器は前の絵のままでいる）。

        tag はコマ（歩きの足など）。無ければ本体のまま。
        """
        if tag and tag in self.motions.get(name, {}):
            return self.motions[name][tag]
        if blinking and name in self.blinks:
            return self.blinks[name]
        return self.frames.get(name)

    def tags(self, name: str):
        """その絵のコマの並び。無ければ空。"""
        return list(self.motions.get(name, {}))

    def fidgets(self):
        """手持ちぶさたの所作。名前が fidget_ で始まる絵。"""
        return [n for n in self.frames if n.startswith("fidget_")]

    def can_blink(self, name: str) -> bool:
        return name in self.blinks

    def any_name(self) -> str:
        """最初に出す絵。指示が来るまでのあいだの一枚。"""
        return "talk" if "talk" in self.frames else next(iter(self.frames), "")
