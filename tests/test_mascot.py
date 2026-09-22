"""器（タスクトレイのドット）の、画面を持たない部分の検証。

窓まわりは自動では確かめられない。ここで見るのは、**渡す形が正しいこと**だけ。
不透明さを先に掛け忘れると髪の先に黒い縁が出るし、当たり判定を間違えると
透明なところのクリックを奪う。どちらも目で気づきにくい。
"""

import unittest

from kotoha.mascot import png, sheet


def solid(r, g, b, a, width=2, height=1):
    image = png.Image(width, height)
    for i in range(width * height):
        image.px[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    return image


class FrameTests(unittest.TestCase):
    def test_colour_is_multiplied_by_the_alpha(self):
        """掛け忘れると、縁が黒ずむ。UpdateLayeredWindow はその形を求める。"""
        frame = sheet.Frame(solid(200, 100, 50, 128))
        blue, green, red, alpha = frame.bgra[0:4]
        self.assertEqual(alpha, 128)
        self.assertEqual(red, 200 * 128 // 255)
        self.assertEqual(green, 100 * 128 // 255)
        self.assertEqual(blue, 50 * 128 // 255)

    def test_opaque_pixels_keep_their_colour(self):
        frame = sheet.Frame(solid(200, 100, 50, 255))
        self.assertEqual(tuple(frame.bgra[0:4]), (50, 100, 200, 255))

    def test_transparent_pixels_stay_black(self):
        frame = sheet.Frame(solid(200, 100, 50, 0))
        self.assertEqual(tuple(frame.bgra[0:4]), (0, 0, 0, 0))

    def test_hit_testing(self):
        image = png.Image(2, 1)
        image.px[3] = 255            # 左だけ中身がある
        frame = sheet.Frame(image)
        self.assertTrue(frame.opaque_at(0, 0))
        self.assertFalse(frame.opaque_at(1, 0))
        self.assertFalse(frame.opaque_at(-1, 0))   # 窓の外は通す
        self.assertFalse(frame.opaque_at(0, 5))


class SheetTests(unittest.TestCase):
    # 素材は27枚で、読むのに0.5秒かかる。どのテストも読むだけなので、1回で足りる。
    loaded = None

    def setUp(self):
        if not (sheet.SPRITE_DIR / "sprites.json").exists():
            self.skipTest("素材がまだ作られていない（python -m tools.build_sprites）")
        if SheetTests.loaded is None:
            SheetTests.loaded = sheet.Sheet().load()
        self.sheet = SheetTests.loaded

    def test_every_frame_is_the_size_it_says(self):
        width, height = self.sheet.size
        for name in self.sheet.frames:
            with self.subTest(sprite=name):
                frame = self.sheet.frame(name)
                self.assertEqual((frame.width, frame.height), (width, height))

    def test_blink_frames_match_their_picture(self):
        for name in self.sheet.blinks:
            with self.subTest(sprite=name):
                self.assertIsNot(self.sheet.frame(name, True), self.sheet.frame(name, False))

    def test_asking_for_a_blink_that_does_not_exist(self):
        """まばたきの差分が無い絵は、開いたままでいるだけ。

        いまは全部の絵に差分がある。**無い絵を足したときに落ちないこと**を
        見たいので、無い状態をここで作る。
        """
        plain = next((n for n in self.sheet.frames if n not in self.sheet.blinks), None)
        if plain is None:
            plain = next(iter(self.sheet.frames))
            self.sheet.blinks.pop(plain)
        self.assertIs(self.sheet.frame(plain, True), self.sheet.frame(plain, False))

    def test_an_unknown_name_gives_nothing(self):
        """知らない名前で描き替えない。器は前の絵のままでいる。"""
        self.assertIsNone(self.sheet.frame("そんな絵は無い"))

    def test_there_is_a_first_picture(self):
        self.assertIn(self.sheet.any_name(), self.sheet.frames)


if __name__ == "__main__":
    unittest.main()
