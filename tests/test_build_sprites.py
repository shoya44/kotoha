"""まばたき差分の合わせ方の検証。絵は小さく作り、ファイルには触れない。"""

import unittest

from kotoha.mascot import png
from tools import build_sprites


def canvas(size=60):
    return png.Image(size, size)


def fill(image, x0, y0, x1, y1, rgba):
    for y in range(y0, y1):
        for x in range(x0, x1):
            image.px[(y * image.width + x) * 4:(y * image.width + x) * 4 + 4] = bytes(rgba)


def pixel(image, x, y):
    i = (y * image.width + x) * 4
    return tuple(image.px[i:i + 4])


BODY = (200, 180, 160, 255)
EYE = (40, 20, 20, 255)
LID = (90, 60, 60, 255)


class FitBlinkTests(unittest.TestCase):
    def setUp(self):
        # 本体: 体の四角と、目の四角。
        self.base = canvas()
        fill(self.base, 10, 5, 50, 55, BODY)
        fill(self.base, 22, 14, 27, 18, EYE)
        # 差分: 全体が (3, 1) ずれていて、目は閉じた線。体の下のほうにも違いがある。
        self.blink = canvas()
        fill(self.blink, 13, 6, 53, 56, BODY)
        fill(self.blink, 25, 17, 30, 19, LID)
        fill(self.blink, 20, 45, 40, 50, (0, 0, 255, 255))
        self.window = (12, 8, 48, 30)

    def test_finds_the_offset(self):
        _fitted, found = build_sprites._fit_blink(self.base, self.blink, self.window)
        self.assertEqual(found, (3, 1))

    def test_outside_the_window_is_the_base(self):
        """体も髪の先も動かない。窓の外は本体そのもの。"""
        fitted, _found = build_sprites._fit_blink(self.base, self.blink, self.window)
        self.assertEqual(pixel(fitted, 30, 47), BODY)       # 差分の青い違いは持ち込まない
        self.assertEqual(pixel(fitted, 10, 40), BODY)       # ずれた差分の縁も出ない
        self.assertEqual(pixel(fitted, 52, 40), (0, 0, 0, 0))

    def test_inside_the_window_is_the_aligned_blink(self):
        fitted, _found = build_sprites._fit_blink(self.base, self.blink, self.window)
        self.assertEqual(pixel(fitted, 24, 17), LID)        # 閉じた目が、本体の目の位置に
        self.assertEqual(pixel(fitted, 24, 14), BODY)       # 開いた目は消えている

    def test_guess_narrows_the_search(self):
        _fitted, found = build_sprites._fit_blink(self.base, self.blink, self.window,
                                                  guess=(3, 1), search=1)
        self.assertEqual(found, (3, 1))

    def test_eye_window_scales_with_the_output(self):
        window = build_sprites._eye_window((0, 0, 99, 199), (10, 20), 0.5)
        self.assertEqual(window, (10, 22, 50, 60))
