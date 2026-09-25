"""トレイの姿の、生きて見えるための小さな癖（まばたきの間・マウスの方を向く）。"""

import random
import unittest
from types import SimpleNamespace

from kotoha.mascot import sheet
from kotoha.mascot.__main__ import GAZE_FAR, GAZE_REST, Mascot, blink_gap


class BlinkGapTests(unittest.TestCase):
    def test_gaps_vary_and_sometimes_come_in_pairs(self):
        random.seed(1)
        gaps = [blink_gap() for _ in range(2000)]
        self.assertTrue(all(0.18 <= g <= 11.0 for g in gaps))
        self.assertTrue(any(g < 0.5 for g in gaps))    # 2回続ける
        self.assertTrue(any(g > 6.0 for g in gaps))    # じっと見る


class FakeDot:
    def __init__(self, cursor):
        self.spot = cursor

    def cursor(self):
        return self.spot

    def center(self):
        return (1000, 500)

    def dragging(self):
        return False


class GazeTests(unittest.TestCase):
    def mascot(self, cursor=(1300, 400)):
        return SimpleNamespace(embodied=True, online=True, act="idle", dot=FakeDot(cursor),
                               cursor_at=None, cursor_moved=0.0)

    def gaze(self, me, now):
        return Mascot._gaze(me, now)

    def test_a_still_mouse_is_not_watched(self):
        me = self.mascot()
        self.assertIsNone(self.gaze(me, 100.0))

    def test_she_turns_toward_a_moving_mouse(self):
        me = self.mascot()
        self.gaze(me, 100.0)
        me.dot.spot = (1300, 420)
        self.assertEqual(self.gaze(me, 101.0), (0.0, sheet.NECK, 1))
        me.dot.spot = (700, 420)
        self.assertEqual(self.gaze(me, 101.5), (0.0, sheet.NECK, -1))

    def test_she_looks_back_after_a_while(self):
        me = self.mascot()
        self.gaze(me, 100.0)
        me.dot.spot = (1300, 420)
        self.gaze(me, 101.0)
        self.assertIsNone(self.gaze(me, 101.0 + GAZE_REST + 0.1))

    def test_far_away_or_asleep_is_ignored(self):
        me = self.mascot()
        self.gaze(me, 100.0)
        me.dot.spot = (1000 + GAZE_FAR + 10, 420)
        self.assertIsNone(self.gaze(me, 101.0))
        me.act = "sleep"
        me.dot.spot = (1300, 400)
        self.assertIsNone(self.gaze(me, 101.5))


if __name__ == "__main__":
    unittest.main()
