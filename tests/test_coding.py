"""Claude Code の様子の検証。記録は一時フォルダーに作り、本物の ~/.claude は見ない。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("coding")

from kotoha.memory import db  # noqa: E402
from kotoha.talk import coding  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


NOW = 1_800_000_000.0


def line(kind, stop_reason=None, cwd="C:\\work\\kotoha"):
    record = {"type": kind, "cwd": cwd}
    if kind in ("assistant", "user"):
        record["message"] = {"role": kind, "stop_reason": stop_reason}
    return json.dumps(record)


class RecordsMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory(prefix="kotoha coding ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(setattr, config, "CODING_ENABLED", config.CODING_ENABLED)
        self.addCleanup(setattr, config, "CODING_MIN_MINUTES", config.CODING_MIN_MINUTES)
        config.CODING_ENABLED = True
        config.CODING_MIN_MINUTES = 5

    def session(self, name, lines, age_minutes, project="C--work-kotoha"):
        folder = self.root / project
        folder.mkdir(exist_ok=True)
        path = folder / f"{name}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stamp = NOW - age_minutes * 60
        os.utime(path, (stamp, stamp))
        return path


class StateTests(RecordsMixin, unittest.TestCase):
    def test_end_turn_means_waiting(self):
        self.session("a", [line("user"), line("assistant", "end_turn"), line("system")], 3)
        found = coding.sessions(NOW, self.root)
        self.assertEqual([(s.place, s.state) for s in found], [("kotoha", coding.WAITING)])

    def test_tool_use_means_working(self):
        self.session("a", [line("assistant", "tool_use"), line("user")], 1)
        self.assertEqual(coding.sessions(NOW, self.root)[0].state, coding.WORKING)

    def test_quiet_for_too_long_means_stalled(self):
        self.session("a", [line("assistant", "tool_use")], coding.STALL_MINUTES + 1)
        self.assertEqual(coding.sessions(NOW, self.root)[0].state, coding.STALLED)

    def test_old_sessions_are_ignored(self):
        self.session("a", [line("assistant", "end_turn")], coding.RECENT_MINUTES + 1)
        self.assertEqual(coding.sessions(NOW, self.root), [])

    def test_newest_first_and_limited(self):
        for i, age in enumerate((30, 5, 60)):
            self.session(f"s{i}", [line("assistant", "end_turn")], age)
        found = coding.sessions(NOW, self.root)
        self.assertEqual([s.id for s in found], ["s1", "s0"])

    def test_broken_lines_are_skipped(self):
        self.session("a", ["{not json", line("assistant", "end_turn")], 1)
        self.assertEqual(coding.sessions(NOW, self.root)[0].state, coding.WAITING)

    def test_only_the_tail_is_read(self):
        """記録は数MBになる。頭から読むと巡回のたびに引っかかる。"""
        filler = [line("user")] * 5000
        self.session("a", filler + [line("assistant", "end_turn")], 1)
        self.assertEqual(coding.sessions(NOW, self.root)[0].state, coding.WAITING)


class DescribeTests(RecordsMixin, unittest.TestCase):
    def test_says_where_and_what(self):
        self.session("a", [line("assistant", "tool_use")], 2)
        self.assertIn("kotoha で Claude Code が作業中", coding.describe(NOW, self.root))

    def test_waiting_is_said_as_finished(self):
        self.session("a", [line("assistant", "end_turn")], 12)
        text = coding.describe(NOW, self.root)
        self.assertIn("作業を終えて", text)
        self.assertIn("12分前", text)

    def test_switched_off_says_nothing(self):
        config.CODING_ENABLED = False
        self.session("a", [line("assistant", "end_turn")], 1)
        self.assertEqual(coding.describe(NOW, self.root), "")


class FinishedTests(RecordsMixin, DbCase):
    def test_working_then_waiting_after_a_while(self):
        self.session("a", [line("assistant", "tool_use")], 1)
        self.assertEqual(coding.finished(self.conn, NOW, self.root), [])
        # 動き始めた時刻を、5分より前に置き直す。
        began = (datetime.now(timezone.utc) - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_state(self.conn, db.CODING_PREFIX + "a", f"{coding.WORKING}|{began}")
        self.session("a", [line("assistant", "end_turn")], 0.5)
        done = coding.finished(self.conn, NOW, self.root)
        self.assertEqual([s.place for s in done], ["kotoha"])
        # 二度は言わない。
        self.assertEqual(coding.finished(self.conn, NOW, self.root), [])

    def test_short_exchanges_are_not_announced(self):
        """会話のように1分おきに返事が来る使い方で、毎回鳴らさない。"""
        self.session("a", [line("assistant", "tool_use")], 1)
        coding.finished(self.conn, NOW, self.root)
        self.session("a", [line("assistant", "end_turn")], 0.5)
        self.assertEqual(coding.finished(self.conn, NOW, self.root), [])

    def test_first_sight_of_a_waiting_session_is_quiet(self):
        """起動直後に、前から返事待ちだったものを「終わった」と言わない。"""
        self.session("a", [line("assistant", "end_turn")], 1)
        self.assertEqual(coding.finished(self.conn, NOW, self.root), [])

    def test_forgotten_sessions_are_cleaned_up(self):
        self.session("a", [line("assistant", "tool_use")], 1)
        coding.finished(self.conn, NOW, self.root)
        self.assertIsNotNone(db.get_state(self.conn, db.CODING_PREFIX + "a"))
        (self.root / "C--work-kotoha" / "a.jsonl").unlink()
        coding.finished(self.conn, NOW, self.root)
        self.assertIsNone(db.get_state(self.conn, db.CODING_PREFIX + "a"))
