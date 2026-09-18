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


class Frame:
    """1枚ぶん。転送用の並びと、当たり判定用の不透明さ。"""

    __slots__ = ("width", "height", "bgra", "alpha")

    def __init__(self, image: png.Image):
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
        return self

    def frame(self, name: str, blinking: bool = False):
        """その絵。知らない名前なら None（器は前の絵のままでいる）。"""
        if blinking and name in self.blinks:
            return self.blinks[name]
        return self.frames.get(name)

    def can_blink(self, name: str) -> bool:
        return name in self.blinks

    def any_name(self) -> str:
        """最初に出す絵。指示が来るまでのあいだの一枚。"""
        return "talk" if "talk" in self.frames else next(iter(self.frames), "")
