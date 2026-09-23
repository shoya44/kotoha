"""ことはの姿の検証。DBも外も触らないので、時刻と機嫌を渡すだけで確かめられる。"""

import unittest
from datetime import datetime

from kotoha import config
from kotoha.talk import chat, figure


class GroupTests(unittest.TestCase):
    def test_every_hour_has_a_group(self):
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertIn(figure.group(hour), figure.GROUPS)
                self.assertTrue(figure.situation(hour))

    def test_boundaries(self):
        """static/app.js の getAvatarGroup() と区切りを揃える。"""
        groups = {hour: figure.group(hour) for hour in range(24)}
        self.assertEqual(groups[6], groups[10])
        self.assertNotEqual(groups[5], groups[6])
        self.assertEqual(groups[21], groups[1])   # 夜更かしは日付をまたぐ
        self.assertNotEqual(groups[1], groups[2])
        self.assertEqual(groups[2], groups[5])

    def test_chat_reads_the_same_table(self):
        """言葉と絵で二重に持たない。chat 側に区切りが戻ったら落とす。"""
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertEqual(chat.situation(hour), figure.situation(hour))


class SpriteTests(unittest.TestCase):
    def test_sprite_is_a_listed_name(self):
        for hour in range(24):
            now = datetime(2026, 9, 19, hour)
            with self.subTest(hour=hour):
                self.assertIn(figure.sprite(now), figure.GROUPS[figure.group(hour)][1])

    def test_same_day_and_group_gives_the_same_picture(self):
        """見るたびに姿が入れ替わると落ち着かない。"""
        first = figure.sprite(datetime(2026, 9, 19, 21, 5))
        later = figure.sprite(datetime(2026, 9, 19, 23, 50))
        self.assertEqual(first, later)

    def test_the_picture_can_differ_by_day(self):
        days = {figure.sprite(datetime(2026, 9, day, 12)) for day in range(1, 8)}
        self.assertGreater(len(days), 1)


class SituationTests(unittest.TestCase):
    """様子も日替わり。毎日14時に必ずおやつでは、生活ではなく時刻表に見える。"""

    def test_situation_is_a_listed_one(self):
        for hour in range(24):
            now = datetime(2026, 9, 19, hour)
            with self.subTest(hour=hour):
                self.assertIn(figure.situation(hour, now), figure.GROUPS[figure.group(hour)][0])

    def test_same_day_and_group_gives_the_same_situation(self):
        """同じ日のあいだに様子が入れ替わると、さっき言ったことと食い違う。"""
        first = figure.situation(14, datetime(2026, 9, 19, 14, 5))
        later = figure.situation(16, datetime(2026, 9, 19, 16, 50))
        self.assertEqual(first, later)

    def test_the_situation_can_differ_by_day(self):
        days = {figure.situation(14, datetime(2026, 9, day, 14)) for day in range(1, 8)}
        self.assertGreater(len(days), 1)

    def test_every_group_but_sleep_has_variety(self):
        for name, (situations, _pictures) in figure.GROUPS.items():
            with self.subTest(group=name):
                if name == "sleep":
                    self.assertEqual(len(situations), 1)
                else:
                    self.assertGreater(len(situations), 1)

    def test_pictures_line_up_with_situations(self):
        """様子と絵は同じ式で選ぶ。数がずれると、言っていることと違う絵が出る日ができる。"""
        for name, (situations, pictures) in figure.GROUPS.items():
            with self.subTest(group=name):
                self.assertEqual(len(situations), len(pictures))

    def test_the_picture_matches_what_she_says(self):
        """同じ日・同じ時刻なら、i 番目の様子のときは i 番目の絵。"""
        for day in range(1, 8):
            for hour in range(24):
                now = datetime(2026, 9, day, hour)
                situations, pictures = figure.GROUPS[figure.group(hour)]
                with self.subTest(day=day, hour=hour):
                    said = situations.index(figure.situation(hour, now))
                    self.assertEqual(figure.sprite(now), pictures[said])


class FaceTests(unittest.TestCase):
    def test_the_face_replaces_talk(self):
        self.assertEqual(figure.look(12, face="笑う", said_ago=0), ("laugh", "talk"))

    def test_no_face_is_plain_talk(self):
        self.assertEqual(figure.look(12, face="", said_ago=0), ("talk", "talk"))
        self.assertEqual(figure.look(12, face="知らない顔", said_ago=0), ("talk", "talk"))

    def test_the_face_only_counts_while_talking(self):
        """顔は一瞬のもの。話し終わったら、機嫌と時間帯の姿に戻る。"""
        picture, act = figure.look(12, datetime(2026, 9, 19, 12), face="笑う")
        self.assertEqual(act, "idle")
        self.assertNotEqual(picture, "laugh")

    def test_the_face_labels_match_the_prompt_rules(self):
        """FACES と fixed_rules.txt のラベル一覧がずれると、顔が出ない。"""
        rules = (config.PROMPTS_DIR / "fixed_rules.txt").read_text(encoding="utf-8")
        line = next(l for l in rules.splitlines() if l.startswith("- [FACE:"))
        for label in figure.FACES:
            with self.subTest(label=label):
                self.assertIn(label, line)


class FidgetFitTests(unittest.TestCase):
    def test_off_by_hour(self):
        self.assertIn("fidget_stretch", figure.fidgets_off(datetime(2026, 9, 19, 22), "ふつう"))
        self.assertNotIn("fidget_stretch", figure.fidgets_off(datetime(2026, 9, 19, 8), "ふつう"))

    def test_off_by_season(self):
        self.assertIn("fidget_cold", figure.fidgets_off(datetime(2026, 8, 1, 12), "ふつう"))
        self.assertNotIn("fidget_cold", figure.fidgets_off(datetime(2026, 1, 10, 12), "ふつう"))
        self.assertIn("fidget_fan", figure.fidgets_off(datetime(2026, 1, 10, 12), "ふつう"))

    def test_off_by_mood(self):
        self.assertIn("fidget_spin", figure.fidgets_off(datetime(2026, 9, 19, 12), "疲れ気味"))
        self.assertNotIn("fidget_spin", figure.fidgets_off(datetime(2026, 9, 19, 12), "機嫌がいい"))

    def test_unlisted_fidgets_are_never_off(self):
        for hour in range(24):
            off = figure.fidgets_off(datetime(2026, 9, 19, hour), "疲れ気味")
            with self.subTest(hour=hour):
                self.assertTrue(set(off) <= set(figure.FIDGET_FIT))

    def test_the_moods_are_real_labels(self):
        for name, fit in figure.FIDGET_FIT.items():
            for mood in fit.get("moods", ()):
                with self.subTest(fidget=name):
                    self.assertIn(mood, chat.MOODS)
            for when in fit.get("when", ()):
                with self.subTest(fidget=name):
                    self.assertIn(when, figure.GROUPS)


class ActTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, config, "LOOKOUT_SIT_HOURS", config.LOOKOUT_SIT_HOURS)
        config.LOOKOUT_SIT_HOURS = 3

    def test_default_is_idle(self):
        self.assertEqual(figure.act(12), "idle")

    def test_talk_wins_over_everything(self):
        self.assertEqual(figure.act(3, mood="眠い", said_ago=1, streak_hours=9), "talk")

    def test_sleep_by_hour_or_by_mood(self):
        self.assertEqual(figure.act(3), "sleep")
        self.assertEqual(figure.act(12, mood="眠い"), "sleep")

    def test_worry_needs_a_long_streak(self):
        self.assertEqual(figure.act(12, streak_hours=2.9), "idle")
        self.assertEqual(figure.act(12, streak_hours=3.0), "worry")

    def test_talk_ends(self):
        self.assertEqual(figure.act(12, said_ago=figure.TALK_SECONDS), "idle")

    def test_sulk_comes_from_the_mood(self):
        self.assertEqual(figure.act(12, mood="すねている"), "sulk")

    def test_every_act_is_listed(self):
        """器は ACTS ぶんの絵しか持たない。知らない名前を返すと絵が出ない。"""
        seen = {
            figure.act(12),
            figure.act(12, said_ago=0),
            figure.act(3),
            figure.act(12, streak_hours=99),
            figure.act(12, mood="すねている"),
            figure.act(12, mood="機嫌がいい"),
        }
        self.assertEqual(seen, set(figure.ACTS))

    def test_act_sprites_are_known_acts(self):
        for name in figure.ACT_SPRITES:
            with self.subTest(act=name):
                self.assertIn(name, figure.ACTS)


class LookTests(unittest.TestCase):
    def test_act_with_a_picture_wins(self):
        now = datetime(2026, 9, 19, 12)
        self.assertEqual(figure.look(12, now, said_ago=1), ("talk", "talk"))
        self.assertEqual(figure.look(12, now, mood="すねている"), ("sulk", "sulk"))

    def test_idle_falls_back_to_the_time_of_day(self):
        now = datetime(2026, 9, 19, 12)
        self.assertEqual(figure.look(12, now), (figure.sprite(now), "idle"))


class PriorTests(unittest.TestCase):
    """申告が無いときの穴埋めと、時間帯に合わない機嫌の落とし方。"""

    def test_every_hour_has_a_prior(self):
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertIn(figure.prior(hour), chat.MOODS)

    def test_only_the_small_hours_are_sleepy(self):
        self.assertEqual(figure.prior(3), figure.SLEEPY_MOOD)
        for hour in (7, 12, 15, 19, 23):
            with self.subTest(hour=hour):
                self.assertEqual(figure.prior(hour), "ふつう")

    def test_sleepiness_is_kept_only_at_night(self):
        for hour in (2, 5, 22, 1):
            with self.subTest(hour=hour):
                self.assertTrue(figure.keeps(hour, figure.SLEEPY_MOOD))
        for hour in (6, 10, 12, 16, 20):
            with self.subTest(hour=hour):
                self.assertFalse(figure.keeps(hour, figure.SLEEPY_MOOD))

    def test_other_moods_are_kept_all_day(self):
        for hour in range(24):
            for mood in chat.MOODS:
                if mood == figure.SLEEPY_MOOD:
                    continue
                with self.subTest(hour=hour, mood=mood):
                    self.assertTrue(figure.keeps(hour, mood))

    def test_the_prior_never_changes_the_picture(self):
        """**PRIORは絵に反映させない。** 言葉のぶんだけを埋める。"""
        for hour in range(24):
            now = datetime(2026, 9, 19, hour)
            with self.subTest(hour=hour):
                self.assertEqual(figure.look(hour, now, mood=figure.prior(hour)),
                                 figure.look(hour, now))


class MoodLabelTests(unittest.TestCase):
    def test_labels_exist(self):
        """chat.MOODS のラベルを書き換えると、その機嫌の絵が出なくなる。"""
        self.assertIn(figure.SLEEPY_MOOD, chat.MOODS)
        self.assertIn(figure.SULKY_MOOD, chat.MOODS)

    def test_prior_labels_exist(self):
        """PRIOR のラベルがずれると、プロンプトの「今の機嫌」が作れない。"""
        for name, label in figure.MOOD_PRIOR.items():
            with self.subTest(group=name):
                self.assertIn(label, chat.MOODS)

    def test_every_group_has_a_prior(self):
        self.assertEqual(set(figure.MOOD_PRIOR), set(figure.GROUPS))


if __name__ == "__main__":
    unittest.main()
