"""姿の切り替えの溶かし（mascot/fade.py と sheet.Frame.blended）の検証。"""

import unittest

from kotoha.mascot import fade, png, sheet


def solid(r, g, b, a, width=2, height=2):
    image = png.Image(width, height)
    for i in range(width * height):
        image.px[i * 4:(i + 1) * 4] = bytes((r, g, b, a))
    return image


class BlendedTests(unittest.TestCase):
    def test_zero_weight_is_the_first_and_one_is_the_second(self):
        a, b = sheet.Frame(solid(200, 100, 50, 255)), sheet.Frame(solid(0, 0, 0, 0))
        self.assertEqual(bytes(a.blended(b, 0.0).bgra), bytes(a.bgra))
        self.assertEqual(bytes(a.blended(b, 1.0).bgra), bytes(b.bgra))

    def test_halfway_is_the_middle_of_colour_and_alpha(self):
        a, b = sheet.Frame(solid(200, 100, 50, 255)), sheet.Frame(solid(0, 0, 0, 0))
        mid = a.blended(b, 0.5)
        self.assertIn(tuple(mid.bgra[:4]), [(25, 50, 100, 127), (25, 50, 100, 128)])
        self.assertIn(mid.alpha[0], (127, 128))

    def test_never_exceeds_either_side(self):
        # 縮めた値の和が 255 を超えると、大きな整数の足し算で隣の桁へ漏れる。
        a, b = sheet.Frame(solid(255, 255, 255, 255)), sheet.Frame(solid(255, 255, 255, 255))
        for weight in (0.1, 0.2, 0.33, 0.5, 0.8, 0.99):
            with self.subTest(weight=weight):
                self.assertEqual(bytes(a.blended(b, weight).bgra), bytes(a.bgra))

    def test_does_not_touch_the_originals(self):
        a, b = sheet.Frame(solid(200, 100, 50, 255)), sheet.Frame(solid(10, 20, 30, 255))
        before = bytes(a.bgra), bytes(b.bgra)
        a.blended(b, 0.5)
        self.assertEqual((bytes(a.bgra), bytes(b.bgra)), before)

    def test_different_sizes_refuse(self):
        a, b = sheet.Frame(solid(0, 0, 0, 255)), sheet.Frame(solid(0, 0, 0, 255, width=3))
        with self.assertRaises(ValueError):
            a.blended(b, 0.5)


class CrossfadeTests(unittest.TestCase):
    def setUp(self):
        self.a = sheet.Frame(solid(200, 100, 50, 255))
        self.b = sheet.Frame(solid(0, 0, 0, 255))
        self.c = sheet.Frame(solid(0, 0, 200, 255))
        self.fader = fade.Crossfade(seconds=0.2, steps=5)

    def test_first_picture_shows_at_once(self):
        self.assertIs(self.fader.frame(("talk", False), self.a, 0.0), self.a)
        self.assertFalse(self.fader.fading(0.0))

    def test_same_key_passes_the_frame_through(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        blink = sheet.Frame(solid(1, 2, 3, 255))
        self.assertIs(self.fader.frame(("talk", False), blink, 0.04), blink)   # まばたき・呼吸は溶かさない

    def test_a_new_key_fades_from_the_old_picture_to_the_new(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        shown = [self.fader.frame(("happy", False), self.b, 1.0 + i * 0.04) for i in range(6)]
        reds = [f.bgra[2] for f in shown]
        self.assertEqual(reds[0], 160)                    # 1/5 だけ次の絵
        self.assertTrue(all(x > y for x, y in zip(reds, reds[1:5])))
        self.assertIs(shown[5], self.b)                   # 0.2 秒で次の絵そのもの
        self.assertFalse(self.fader.fading(1.2))

    def test_step_counts_down_and_is_zero_when_done(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        self.fader.frame(("happy", False), self.b, 1.0)
        self.assertEqual(self.fader.step(1.0), 5)
        self.assertEqual(self.fader.step(1.12), 2)
        self.assertEqual(self.fader.step(1.2), 0)

    def test_a_change_mid_fade_starts_from_what_is_seen(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        seen = self.fader.frame(("happy", False), self.b, 1.0)
        first = self.fader.frame(("worry", False), self.c, 1.04)
        # 前の姿（a）へ戻らず、見えていた混ざりから c へ向かう
        self.assertLess(first.bgra[2], seen.bgra[2])
        self.assertGreater(first.bgra[0], 0)

    def test_instant_pictures_do_not_fade(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        self.assertIs(self.fader.frame(("surprised", False), self.b, 1.0), self.b)
        # 驚きから戻るときは溶かす
        self.assertIsNot(self.fader.frame(("talk", False), self.a, 2.0), self.a)

    def test_cut_forgets_what_was_seen(self):
        self.fader.frame(("talk", False), self.a, 0.0)
        self.fader.cut()
        self.assertIs(self.fader.frame(("wave", False), self.b, 1.0), self.b)

    def test_size_change_does_not_fade(self):
        wide = sheet.Frame(solid(0, 0, 0, 255, width=3))
        self.fader.frame(("talk", False), self.a, 0.0)
        self.assertIs(self.fader.frame(("talk", True), wide, 1.0), wide)

    def test_mirroring_counts_as_a_change(self):
        self.fader.frame(("walk", False), self.a, 0.0)
        self.assertIsNot(self.fader.frame(("walk", True), self.b, 1.0), self.b)

    def test_none_passes_through(self):
        self.assertIsNone(self.fader.frame(("x", False), None, 0.0))
