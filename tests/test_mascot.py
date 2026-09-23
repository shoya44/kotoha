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




class CatchUpTests(unittest.TestCase):
    """知らない絵が来たら、素材を読み直す。起こし直さないと一日じゅう talk だった。"""

    def setUp(self):
        import types
        from unittest import mock
        from kotoha.mascot import __main__ as mascot
        if not (sheet.SPRITE_DIR / "sprites.json").exists():
            self.skipTest("素材がまだ作られていない（python -m tools.build_sprites）")
        self.mascot = mascot
        self.logged = []
        patcher = mock.patch.object(mascot, "log", self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        old = sheet.Sheet()
        old.frames = {"talk": object()}           # 起動したときの古い素材
        self.me = types.SimpleNamespace(sheet=old, reloaded_for=set(), last_drawn="古い")

    def catch_up(self, name):
        self.mascot.Mascot._catch_up(self.me, name)

    def test_an_unknown_picture_reloads_the_sheet(self):
        self.catch_up("dishes")
        self.assertIn("dishes", self.me.sheet.frames)
        self.assertIsNone(self.me.last_drawn)

    def test_a_known_picture_does_not_reload(self):
        before = self.me.sheet
        self.catch_up("talk")
        self.assertIs(self.me.sheet, before)

    def test_a_name_that_is_nowhere_is_tried_once(self):
        self.catch_up("まだ焼いていない絵")
        reloaded = self.me.sheet
        self.catch_up("まだ焼いていない絵")
        self.assertIs(self.me.sheet, reloaded)
        self.assertEqual(len(self.logged), 1)

    def test_a_broken_sheet_keeps_the_old_one(self):
        from unittest import mock
        before = self.me.sheet
        with mock.patch.object(sheet.Sheet, "load", side_effect=OSError("書きかけ")):
            self.catch_up("dishes")
        self.assertIs(self.me.sheet, before)
        self.assertNotIn("dishes", self.me.reloaded_for)


class StrollTests(unittest.TestCase):
    """散歩は一歩ずつ。滑らせると引きずられて見え、向きが読めない。"""

    def setUp(self):
        import types
        from kotoha.mascot import __main__ as mascot
        self.mascot = mascot
        self.dot = types.SimpleNamespace(x=100, dragging=lambda: False,
                                         room=lambda: (100, 1000), walk_to=self.walk_to)
        self.me = types.SimpleNamespace(
            dot=self.dot, motion=None, step_at=0.0, step=0, step_from=0,
            next_stroll=0.0, next_fidget=10 ** 9, sheet=types.SimpleNamespace(frames={"walk": 1}),
            _idle=lambda: True, _fidgets=lambda: [], remember_place=lambda x, y: None)
        self.me._stride_phase = lambda now: mascot.Mascot._stride_phase(self.me, now)

    def walk_to(self, x):
        self.dot.x = x
        return True

    def move(self, now):
        self.mascot.Mascot._move(self.me, now)

    def test_a_step_advances_by_one_stride_then_rests(self):
        import random
        random.seed(1)
        self.dot.room = lambda: (10, 1000)               # 左は 10 しか無い。右へ
        self.move(0.0)
        self.assertEqual(self.me.motion[0], "stroll")
        self.assertEqual(self.me.motion[1], 1)
        direction = self.me.motion[1]
        moving = self.mascot.STROLL_STEP_SECONDS * self.mascot.STROLL_STEP_MOVING
        self.move(moving / 2)
        half = abs(self.dot.x - 100)
        self.assertTrue(0 < half < self.mascot.STROLL_STRIDE)
        self.move(moving)
        self.assertEqual(self.dot.x, 100 + direction * self.mascot.STROLL_STRIDE)
        self.move(self.mascot.STROLL_STEP_SECONDS * 0.9)  # 一歩の後半は止まっている
        self.assertEqual(self.dot.x, 100 + direction * self.mascot.STROLL_STRIDE)
        self.move(self.mascot.STROLL_STEP_SECONDS + 0.001)  # 次の一歩へ
        self.assertEqual(self.me.step, 1)

    def test_no_stroll_towards_a_near_edge(self):
        self.dot.room = lambda: (10, 10)
        self.move(0.0)
        self.assertIsNone(self.me.motion)
        self.assertGreater(self.me.next_stroll, 0.0)

    def test_the_picture_faces_the_way_she_walks(self):
        faces_right = self.mascot.WALK_FACES_RIGHT
        self.assertEqual(self.mascot.walk_mirrored(1), not faces_right)
        self.assertEqual(self.mascot.walk_mirrored(-1), faces_right)


if __name__ == "__main__":
    unittest.main()
