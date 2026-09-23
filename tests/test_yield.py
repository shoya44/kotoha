"""巡回は、話しかけた人が待っていれば手を止める。Gemini にも外にも出ない。"""

import unittest

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("yield")

from kotoha.serve import hub, jobs  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class YieldTests(DbCase):
    """順番待ちを持ったまま Gemini を待つ段の前で、待っている人が居れば次の巡回へ譲る。"""

    def setUp(self):
        super().setUp()
        for name, value in (("PRESENCE_ENABLED", False), ("BRIEFING_ENABLED", False),
                            ("LOOKOUT_ENABLED", False), ("REACH_OUT_ENABLED", False),
                            ("BACKUP_INTERVAL_SECONDS", 10 ** 9),
                            ("MAINTENANCE_SECONDS", 10 ** 9)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.steps = []
        for name in ("maybe_diary", "maybe_reminders"):
            self.addCleanup(setattr, jobs, name, getattr(jobs, name))
            setattr(jobs, name, lambda conn, *a, _n=name: self.steps.append(_n))
        self.addCleanup(setattr, jobs.hub, "refresh", jobs.hub.refresh)
        jobs.hub.refresh = lambda conn=None, said_ago=None: self.steps.append("refresh")
        hub.reset()
        self.addCleanup(hub.reset)

    def test_the_round_runs_through_when_nobody_waits(self):
        jobs.run_periodic_jobs(self.conn)
        self.assertEqual(self.steps, ["maybe_diary", "maybe_reminders", "refresh"])

    def test_the_round_stops_short_while_someone_waits(self):
        hub.begin_turn()
        try:
            jobs.run_periodic_jobs(self.conn)
        finally:
            hub.end_turn()
        self.assertEqual(self.steps, [])
        self.assertFalse(hub.talking())

    def test_talking_counts_the_people_in_line(self):
        self.assertFalse(hub.talking())
        hub.begin_turn()
        hub.begin_turn()
        hub.end_turn()
        self.assertTrue(hub.talking())
        hub.end_turn()
        self.assertFalse(hub.talking())


if __name__ == "__main__":
    unittest.main()
