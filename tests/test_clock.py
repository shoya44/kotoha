"""時計は1本。止めて、進めると、DBの期限も機嫌の減衰も同じ時刻を見る。

これから入れる「強さが日ごとに薄れる」「機嫌が時間で戻る」は、ここが
無いと確かめられない。先に道だけ通しておく。
"""

import unittest
from datetime import datetime

from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("clock")

from kotoha import clock  # noqa: E402
from kotoha.memory import db, retrieve, strength  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FrozenClockTests(DbCase):
    def add(self, key, days):
        with_expiry = db.shifted(days)
        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, last_used_at, "
            "expires_at, pinned, source_key) VALUES ('semantic','fact',?,?,?,?,?,0,?)",
            (key, db.now_utc()[:10], db.now_utc(), db.now_utc(), with_expiry, key))
        self.conn.commit()

    def test_python_and_db_see_the_same_now(self):
        with Clock(datetime(2026, 9, 22, 12, 0)):
            self.assertEqual(db.now_utc(), clock.utc())
            self.assertEqual(clock.now().hour, 12)

    def test_a_memory_expires_when_days_pass(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.add("三十日", 30)
            self.assertEqual(len(retrieve.pinned_only(self.conn)), 0)     # pinned ではない
            self.assertEqual(chat._memory_count(self.conn), 1)
            tick.advance(days=31)
            self.assertEqual(chat._memory_count(self.conn), 0)         # もう生きていない
            self.assertEqual(db.run_maintenance(self.conn), 1)          # 忘却も同じ時計

    def test_using_a_memory_extends_from_the_frozen_now(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            self.add("使う", 5)
            tick.advance(days=4)
            db.update_usage(self.conn, [1], turn_id=1)
            row = self.conn.execute(
                "SELECT layer, strength, expires_at FROM memory_nodes WHERE id = 1").fetchone()
            # 止めた「今」から、いまの強さが床に落ちるまで。進めた分だけ間が空き、大きく足される。
            self.assertEqual(row["expires_at"], strength.fades_at(row["strength"], row["layer"]))
            self.assertGreater(row["expires_at"], db.shifted(180))

    def test_mood_fades_with_the_clock(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            db.set_state(self.conn, db.MOOD, "すねている")
            db.set_state(self.conn, db.MOOD_AT, db.now_utc())
            self.assertEqual(chat.current_mood(self.conn, 12), "すねている")
            tick.advance(hours=7)
            self.assertNotEqual(chat.current_mood(self.conn, 19), "すねている")

    def test_the_clock_is_released_afterwards(self):
        with Clock(datetime(2020, 1, 1, 0, 0)):
            self.assertEqual(db.now_utc()[:4], "2019" if clock.utc()[:4] == "2019" else "2020")
        self.assertIsNone(clock._frozen)
        self.assertNotEqual(db.now_utc()[:4], "2020")

    def test_local_and_utc_agree(self):
        """9/22 の 2:00（ローカル）は、UTCでは前日になることがある。両方が同じ瞬間を指す。"""
        with Clock(datetime(2026, 9, 22, 2, 0)):
            self.assertEqual(clock.now().hour, 2)
            self.assertEqual(clock.utc_now().astimezone().hour, 2)


if __name__ == "__main__":
    unittest.main()
