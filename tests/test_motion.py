"""暇なときの動き（mascot/motion.py）の検証。窓は持たず、偽の器で回す。

**動きを足したときの直し忘れを、ここで捕まえる。** `MOTIONS` に並べたものは、
名前・間・長さが揃っていて、始められて、終われて、見え方を答えられなければならない。
"""

import types
import unittest

from kotoha.mascot import motion, sheet


class FakeDot:
    def __init__(self, walkable=True):
        self.x, self.y = 100, 100
        self.walkable = walkable
        self.walked = 0

    def walk(self, dx):
        if not self.walkable:
            return False
        self.x += dx
        self.walked += dx
        return True


class FakeSheet:
    def __init__(self, names=("talk", "walk", "fidget_yawn", "fidget_sigh")):
        self.frames = dict.fromkeys(names)

    def fidgets(self):
        return [n for n in self.frames if n.startswith("fidget_")]

    def tags(self, name):
        return []


def fake_me(**kw):
    me = types.SimpleNamespace(sheet=FakeSheet(), dot=FakeDot(), fidgets_off=set(), places=[])
    me.remember_place = lambda x, y: me.places.append((x, y))
    for key, value in kw.items():
        setattr(me, key, value)
    return me


class EveryMotionTests(unittest.TestCase):
    """並べた動きは、どれも同じ約束を守る。1つでも欠けると器が止まる。"""

    def test_kinds_are_unique_and_named(self):
        kinds = [m.kind for m in motion.MOTIONS]
        self.assertTrue(all(kinds))
        self.assertEqual(len(kinds), len(set(kinds)))

    def test_gaps_are_ranges_in_seconds(self):
        for m in motion.MOTIONS:
            with self.subTest(motion=m.kind):
                low, high = m.gap
                self.assertGreater(low, 0)
                self.assertLessEqual(low, high)

    def test_lengths_are_positive_or_self_ending(self):
        for m in motion.MOTIONS:
            with self.subTest(motion=m.kind):
                if m.length is None:
                    # 自分で終わりを決める動きは advance を持ち替えていること
                    self.assertIsNot(m.advance, motion.Motion.advance)
                else:
                    self.assertGreater(m.length, 0)

    def test_each_motion_answers_how_it_looks(self):
        me = fake_me()
        for m in motion.MOTIONS:
            with self.subTest(motion=m.kind):
                started = m.begin(me, 1000.0)
                self.assertIsNotNone(started)
                self.assertIsInstance(started.picture("talk"), (str, type(None)))
                shift = started.shift(1000.5)
                if shift is not None:
                    top, bottom, dx = shift
                    self.assertLessEqual(0.0, top)
                    self.assertLess(top, bottom)
                    self.assertLessEqual(bottom, 1.0)
                    self.assertIn(dx, (-1, 0, 1))
                self.assertIsInstance(started.mirror(), bool)
                self.assertIsInstance(started.breathes(), bool)
                self.assertIsInstance(started.lift(), int)
                started.end(me)

    def test_each_motion_ends(self):
        """長さのある動きは長さで、散歩は距離か端で終わる。"""
        me = fake_me()
        for m in motion.MOTIONS:
            with self.subTest(motion=m.kind):
                started = m.begin(me, 0.0)
                self.assertFalse(started.finished(0.0))
                if m.length is not None:
                    self.assertTrue(started.finished(m.length))
                else:
                    now = 0.0
                    while started.advance(me, now):
                        now += 0.04
                        self.assertLess(now, 60.0, "終わらない")


class BeginTests(unittest.TestCase):
    def test_stroll_needs_the_walk_picture(self):
        me = fake_me(sheet=FakeSheet(("talk",)))
        self.assertIsNone(motion.Stroll.begin(me, 0.0))

    def test_fidget_skips_what_the_brain_ruled_out(self):
        me = fake_me(fidgets_off={"fidget_yawn"})
        for _ in range(10):
            self.assertEqual(motion.Fidget.begin(me, 0.0).name, "fidget_sigh")
        me.fidgets_off = {"fidget_yawn", "fidget_sigh"}
        self.assertIsNone(motion.Fidget.begin(me, 0.0))


class StrollTests(unittest.TestCase):
    def test_stops_at_the_edge_and_remembers_the_place(self):
        me = fake_me(dot=FakeDot(walkable=False))
        walk = motion.Stroll(0.0)
        self.assertFalse(walk.advance(me, 0.04))
        walk.end(me)
        self.assertEqual(me.places, [(100, 100)])

    def test_faces_the_way_it_walks(self):
        walk = motion.Stroll(0.0)
        walk.direction = 1
        self.assertEqual(walk.mirror(), not motion.WALK_FACES_RIGHT)
        walk.direction = -1
        self.assertEqual(walk.mirror(), motion.WALK_FACES_RIGHT)

    def test_feet_alternate_every_step(self):
        me = fake_me()
        walk = motion.Stroll(0.0)
        first = walk.shift(0.0)
        walk.advance(me, motion.STROLL_STEP_SECONDS + 0.01)
        second = walk.shift(0.0)
        self.assertEqual(first[:2], (sheet.SEAM, 1.0))
        self.assertEqual(first[2], -second[2])
        self.assertFalse(walk.breathes())


class SwayTests(unittest.TestCase):
    def test_goes_out_and_back_in_four_steps(self):
        sway = motion.Sway(0.0)
        sway.side = 1
        quarter = sway.length / 4
        seen = [sway.shift(quarter * i)[2] for i in range(4)]
        self.assertEqual(seen, [0, 1, 0, -1])


class IdleTests(unittest.TestCase):
    """器が持つのは、始める・進める・片づける、だけ。"""

    class Blink(motion.Motion):
        kind = "blink-test"
        gap = (10.0, 10.0)
        length = 1.0

    def setUp(self):
        self.me = fake_me()
        self.idle = motion.Idle(0.0, motions=(self.Blink,))

    def test_waits_for_the_gap_then_begins(self):
        self.idle.tick(self.me, 5.0, idle=True)
        self.assertIsNone(self.idle.current)
        self.idle.tick(self.me, 10.0, idle=True)
        self.assertIsInstance(self.idle.current, self.Blink)

    def test_does_not_begin_when_not_idle(self):
        self.idle.tick(self.me, 10.0, idle=False)
        self.assertIsNone(self.idle.current)

    def test_ends_by_length_and_sets_the_next_time(self):
        self.idle.tick(self.me, 10.0, idle=True)
        self.idle.tick(self.me, 10.5, idle=True)
        self.assertIsNotNone(self.idle.current)
        self.idle.tick(self.me, 11.0, idle=True)
        self.assertIsNone(self.idle.current)
        self.assertEqual(self.idle.next["blink-test"], 21.0)

    def test_stops_when_no_longer_idle(self):
        self.idle.tick(self.me, 10.0, idle=True)
        self.idle.tick(self.me, 10.2, idle=False)
        self.assertIsNone(self.idle.current)

    def test_stop_is_safe_when_nothing_moves(self):
        self.idle.stop(self.me, 0.0)
        self.assertIsNone(self.idle.current)

    def test_earlier_in_the_list_wins(self):
        class Other(self.Blink):
            kind = "other-test"

        idle = motion.Idle(0.0, motions=(Other, self.Blink))
        idle.tick(self.me, 10.0, idle=True)
        self.assertIsInstance(idle.current, Other)


if __name__ == "__main__":
    unittest.main()
