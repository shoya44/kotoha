"""記憶の強さ。使うと強く、放っておくと薄れ、床を切ると消える。列を足した日の逆算も。"""

import unittest
from datetime import datetime, timedelta, timezone

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("strength")

from kotoha.memory import db, strength  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, last_used_at, "
    "expires_at, pinned, source_key, strength, strength_at) VALUES (?,?,?,?,?,?,?,0,?,?,?)"
)


class CurveTests(unittest.TestCase):
    """数の形。DBは要らない。"""

    def test_a_fresh_memory_lives_exactly_its_lifetime(self):
        """強さ1が床に落ちるまでが、設定の寿命（30日 / 180日）。設定の意味は変わらない。"""
        at = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(strength.touch_new("episode", at)[:10], "2026-10-22")
        self.assertEqual(strength.touch_new("semantic", at)[:10], "2027-03-21")

    def test_it_halves_every_half_life(self):
        at = "2026-09-22T00:00:00Z"
        later = datetime(2026, 9, 22, tzinfo=timezone.utc)
        later += timedelta(days=strength.half_life("episode"))
        self.assertAlmostEqual(strength.current(1.0, at, "episode", later), 0.5, places=6)

    def test_no_record_means_no_decay(self):
        self.assertEqual(strength.current(2.0, None, "episode"), 2.0)

    def test_the_gain_grows_with_the_gap(self):
        """間が空いてから思い出すほど、よく定着する（間隔反復）。"""
        self.assertLess(strength.gain(0), strength.gain(1))
        self.assertLess(strength.gain(1), strength.gain(30))
        self.assertAlmostEqual(strength.gain(0), config.STRENGTH_GAIN)

    def test_the_weight_is_full_for_a_fresh_memory_and_lighter_when_faded(self):
        self.assertEqual(strength.weight(1.0), 1.0)
        self.assertEqual(strength.weight(4.0), 1.0)
        self.assertLess(strength.weight(0.1), 0.8)

    def test_below_the_floor_fades_now(self):
        at = datetime(2026, 9, 22, tzinfo=timezone.utc)
        self.assertEqual(strength.fades_at(config.STRENGTH_FLOOR / 2, "semantic", at)[:10], "2026-09-22")


class ReinforceTests(DbCase):
    def add(self, layer="semantic", value=1.0, at=None, expires="2099-01-01T00:00:00Z", key="k"):
        now = db.now_utc()
        self.conn.execute(NODE_SQL, (layer, "event" if layer == "episode" else "fact", key,
                                     now[:10], now, now, expires, key, value, at or now))
        self.conn.commit()
        return self.conn.execute("SELECT MAX(id) FROM memory_nodes").fetchone()[0]

    def row(self, node_id):
        return self.conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()

    def test_reinforcing_adds_and_rewrites_the_fade_time(self):
        with Clock(datetime(2026, 9, 22, 12, 0)):
            node = self.add()
            after = strength.reinforce(self.conn, node)
            row = self.row(node)
            self.assertAlmostEqual(after, 1.0 + config.STRENGTH_GAIN)
            self.assertEqual(row["expires_at"], strength.fades_at(after, "semantic"))

    def test_a_faded_memory_recalled_after_a_long_gap_gets_a_big_boost(self):
        with Clock(datetime(2026, 9, 22, 12, 0)) as tick:
            node = self.add(layer="episode")
            tick.advance(days=20)
            before = strength.current(1.0, "2026-09-22T03:00:00Z", "episode")
            self.assertLess(before, 0.3)                         # 20日でだいぶ薄れている
            after = strength.reinforce(self.conn, node)
            self.assertGreater(after, before + config.STRENGTH_GAIN * 2)   # 間が空いたぶん大きく

    def test_reinforcing_an_unknown_id_is_harmless(self):
        self.assertEqual(strength.reinforce(self.conn, 999), 0.0)


class MigrationTests(DbCase):
    """版1のDBに列を足した日、既存の記憶には残り日数から逆算した強さが入る。"""

    def setUp(self):
        # DbCase は最新版まで上げてしまうので、ここでは版1のDBを自分で作る。
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        self.conn.executescript("BEGIN;" + db.SCHEMA + "COMMIT;")
        for sql in db.MIGRATIONS[0][1]:
            self.conn.execute(sql)
        self.conn.execute("PRAGMA user_version = 1")
        self.conn.commit()

    def old_node(self, key, expires):
        now = db.now_utc()
        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, last_used_at, "
            "expires_at, pinned, source_key) VALUES ('semantic','fact',?,?,?,?,?,0,?)",
            (key, now[:10], now, now, expires, key))
        self.conn.commit()

    def test_backfill_keeps_the_fade_time_and_derives_the_strength(self):
        with Clock(datetime(2026, 9, 22, 12, 0)):
            self.old_node("半分", db.shifted(90))       # 寿命の半分が残っている
            self.old_node("切れかけ", db.shifted(1))
            self.old_node("消えない", None)
            db.migrate(self.conn)
            rows = {r["text"]: r for r in self.conn.execute("SELECT * FROM memory_nodes")}
            self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.MIGRATIONS[-1][0])
            half, dying, endless = rows["半分"], rows["切れかけ"], rows["消えない"]
            self.assertLess(config.STRENGTH_FLOOR, half["strength"])
            self.assertLess(half["strength"], 1.0)
            self.assertLess(dying["strength"], half["strength"])
            self.assertEqual(endless["strength"], 1.0)
            # 期限は触らない。逆算した強さから導き直しても、同じ日に落ちる。
            self.assertEqual(half["expires_at"], db.shifted(90))
            self.assertEqual(strength.fades_at(half["strength"], "semantic")[:10], db.shifted(90)[:10])

    def test_migrating_twice_does_nothing_more(self):
        db.migrate(self.conn)
        db.migrate(self.conn)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], db.MIGRATIONS[-1][0])


if __name__ == "__main__":
    unittest.main()
