"""PCの様子の検証。実際のWindows APIには触れない。"""

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("presence")

from kotoha.memory import db  # noqa: E402
from kotoha.talk import presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class PresenceTests(DbCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "PRESENCE_ENABLED", config.PRESENCE_ENABLED)
        config.PRESENCE_ENABLED = True
        # 実機のAPIは叩かない。Windows以外でも同じ結果になるようにする。
        self.addCleanup(setattr, presence, "enabled", presence.enabled)
        self.addCleanup(setattr, presence, "uptime_hours", presence.uptime_hours)
        presence.enabled = lambda: config.PRESENCE_ENABLED
        presence.uptime_hours = lambda: 6.4

    def front(self, app):
        return patch.object(presence, "foreground_app", return_value=app)

    def sample_many(self, app, times):
        with self.front(app):
            for _ in range(times):
                presence.sample(self.conn)
        self.conn.commit()

    def tally(self):
        return json.loads(db.get_state(self.conn, db.FRONT_TALLY) or "{}")

    def test_samples_are_counted(self):
        self.sample_many("VS Code", 3)
        self.assertEqual(self.tally(), {"VS Code": 3})

    def test_several_apps_are_counted_separately(self):
        self.sample_many("VS Code", 4)
        self.sample_many("Chrome", 2)
        self.assertEqual(self.tally(), {"VS Code": 4, "Chrome": 2})

    def test_a_short_stay_is_not_reported(self):
        """一瞬前面に来ただけのアプリを「触っている」とは言わない。"""
        self.sample_many("Chrome", presence.MIN_SAMPLES - 1)
        self.assertIsNone(presence.busy_with(self.conn))

    def test_the_longest_app_is_reported(self):
        self.sample_many("VS Code", presence.MIN_SAMPLES + 3)
        self.sample_many("Chrome", presence.MIN_SAMPLES)
        self.assertEqual(presence.busy_with(self.conn)[0], "VS Code")

    def test_last_hour_only(self):
        """時間が変われば数え直す。半日前の作業を今の様子として語らない。"""
        self.sample_many("VS Code", presence.MIN_SAMPLES + 2)
        db.set_state(self.conn, db.FRONT_TALLY_HOUR, "2020-01-01 03")
        self.conn.commit()
        self.assertIsNone(presence.busy_with(self.conn))

    def test_a_new_hour_starts_from_zero(self):
        self.sample_many("VS Code", 3)
        db.set_state(self.conn, db.FRONT_TALLY_HOUR, "2020-01-01 03")
        self.conn.commit()
        self.sample_many("Chrome", 1)
        self.assertEqual(self.tally(), {"Chrome": 1})

    def test_unreadable_tally_does_not_raise(self):
        db.set_state(self.conn, db.FRONT_TALLY, "こわれている")
        db.set_state(self.conn, db.FRONT_TALLY_HOUR,
                     datetime.now().strftime("%Y-%m-%d %H"))
        self.conn.commit()
        self.sample_many("Chrome", 1)
        self.assertEqual(self.tally(), {"Chrome": 1})

    def test_an_unknown_window_is_skipped(self):
        with self.front(None):
            presence.sample(self.conn)
        self.assertEqual(self.tally(), {})

    def test_the_line_mentions_uptime_and_app(self):
        self.sample_many("VS Code", presence.MIN_SAMPLES)
        line = presence.describe(self.conn)
        self.assertIn("6時間", line)
        self.assertIn("VS Code", line)

    def test_switched_off_says_nothing(self):
        self.sample_many("VS Code", presence.MIN_SAMPLES)
        config.PRESENCE_ENABLED = False
        self.assertEqual(presence.describe(self.conn), "")

    def test_switched_off_stops_counting(self):
        config.PRESENCE_ENABLED = False
        self.sample_many("VS Code", 3)
        self.assertEqual(self.tally(), {})

    def test_a_fresh_machine_only_mentions_the_app(self):
        presence.uptime_hours = lambda: 0.2
        self.sample_many("VS Code", presence.MIN_SAMPLES)
        self.assertNotIn("つけっぱなし", presence.describe(self.conn))


class SnapshotTests(DbCase):
    """画面に出す「いまの様子」。実機のAPIは叩かない。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "PRESENCE_ENABLED", config.PRESENCE_ENABLED)
        config.PRESENCE_ENABLED = True
        for name, value in (("enabled", lambda: config.PRESENCE_ENABLED),
                            ("uptime_hours", lambda: 6.4),
                            ("disks", lambda: [("C", 98.4, 72)]),
                            ("memory", lambda: (61, 12.5)),
                            ("cpu", lambda: 23),
                            ("gpu", lambda: None)):
            self.addCleanup(setattr, presence, name, getattr(presence, name))
            setattr(presence, name, value)

    def rows(self):
        with patch.object(presence, "foreground_app", return_value="VS Code"):
            return dict(presence.snapshot(self.conn))

    def sample_many(self, app, times):
        with patch.object(presence, "foreground_app", return_value=app):
            for _ in range(times):
                presence.sample(self.conn)
        self.conn.commit()

    def test_it_shows_what_the_conversation_already_uses(self):
        self.sample_many("VS Code", presence.MIN_SAMPLES)
        rows = self.rows()
        self.assertEqual(rows["起動してから"], "6時間")
        self.assertEqual(rows["いま前面"], "VS Code")
        self.assertIn("VS Code", rows["この1時間"])
        self.assertIn("98GB", rows["Cドライブ"])
        self.assertIn("61%", rows["メモリ"])
        self.assertEqual(rows["CPU"], "23%")

    def test_what_cannot_be_read_is_left_out(self):
        presence.memory = lambda: None
        presence.cpu = lambda: None
        rows = self.rows()
        self.assertNotIn("メモリ", rows)
        self.assertNotIn("CPU", rows)
        self.assertNotIn("GPU", rows)
        self.assertIn("Cドライブ", rows)

    def test_a_fresh_machine_is_counted_in_minutes(self):
        presence.uptime_hours = lambda: 0.5
        self.assertEqual(self.rows()["起動してから"], "30分")

    def test_it_never_reads_a_window_title(self):
        """覗き見の範囲を広げていないことを、見出しの並びで確かめる。"""
        self.sample_many("VS Code", presence.MIN_SAMPLES)
        for label in self.rows():
            self.assertNotIn("タイトル", label)
            self.assertNotIn("題名", label)


if __name__ == "__main__":
    unittest.main()


class CommitTests(DbCase):
    """数えたぶんはその場で閉じる。閉じずに Gemini を待つと、ほかの書き手が全部止まる。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, "PRESENCE_ENABLED", config.PRESENCE_ENABLED)
        config.PRESENCE_ENABLED = True
        self.addCleanup(setattr, presence, "enabled", presence.enabled)
        presence.enabled = lambda: True

    def test_the_sample_is_visible_from_another_connection_right_away(self):
        with patch.object(presence, "foreground_app", return_value="VS Code"):
            presence.sample(self.conn)
        other = db.connect()
        self.addCleanup(other.close)
        self.assertIn("VS Code", db.get_state(other, db.FRONT_TALLY) or "")
