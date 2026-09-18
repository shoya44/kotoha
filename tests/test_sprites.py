"""素材と、それを指す名前がずれていないかの検証。

**判定（figure.py）と素材（sprites.json）は別々に育つ。** 絵の名前を変えたり、
時間帯の割り当てを差し替えたりしたときに、片方だけ直したことに気づけるようにする。
気づけないと、その時間帯だけ絵が出ない。

素材そのものは `tools/build_sprites.py` が作る。ここでは中身の絵柄は見ない。
"""

import json
import unittest

from kotoha import config
from kotoha.mascot import png
from kotoha.talk import figure

SPRITE_DIR = config.BASE_DIR / "kotoha" / "serve" / "static" / "sprite"
MANIFEST = SPRITE_DIR / "sprites.json"


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        if not MANIFEST.exists():
            self.skipTest("素材がまだ作られていない（python -m tools.build_sprites）")
        self.manifest = load_manifest()

    def test_every_picture_has_a_sprite(self):
        """時間帯の立ち姿と、振る舞い専用の絵。**どちらも実物が要る。**"""
        names = set(self.manifest["sprites"])
        for group, (_, choices) in figure.GROUPS.items():
            for choice in choices:
                with self.subTest(group=group, sprite=choice):
                    self.assertIn(choice, names)
        for act, choice in figure.ACT_SPRITES.items():
            with self.subTest(act=act):
                self.assertIn(choice, names)

    def test_files_exist_in_every_size(self):
        for size in ("full",):
            for name, info in self.manifest["sprites"].items():
                with self.subTest(size=size, sprite=name):
                    self.assertTrue((SPRITE_DIR / size / f"{name}.png").exists())
                    if info["blink"]:
                        self.assertTrue((SPRITE_DIR / size / f"{name}-blink.png").exists())

    def test_sizes_are_listed(self):
        for size in ("full", "face"):
            self.assertIn(size, self.manifest["sizes"])
        self.assertTrue((SPRITE_DIR / "face.png").exists())

    def test_every_frame_has_the_same_shape(self):
        """大きさが揃っていないと、絵を切り替えるたびに位置が跳ねる。"""
        for size in ("full",):
            width, height = self.manifest["sizes"][size]
            for name in self.manifest["sprites"]:
                with self.subTest(size=size, sprite=name):
                    image = png.load(SPRITE_DIR / size / f"{name}.png")
                    self.assertEqual((image.width, image.height), (width, height))


class PngTests(unittest.TestCase):
    def test_write_then_read_gives_the_same_pixels(self):
        image = png.Image(3, 2, bytearray(range(24)))
        path = config.BASE_DIR / "data" / "test_png_roundtrip.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(path.unlink, True)
        png.save(path, image)
        back = png.load(path)
        self.assertEqual((back.width, back.height), (3, 2))
        self.assertEqual(bytes(back.px), bytes(image.px))

    def test_bounds_finds_the_content(self):
        image = png.Image(4, 4)
        image.px[(1 * 4 + 2) * 4 + 3] = 255
        self.assertEqual(png.bounds(image), (2, 1, 2, 1))

    def test_bounds_of_an_empty_image(self):
        self.assertIsNone(png.bounds(png.Image(2, 2)))

    def test_broken_file_is_refused(self):
        with self.assertRaises(png.PngError):
            png.load(config.BASE_DIR / "requirements.txt")


if __name__ == "__main__":
    unittest.main()
