"""段6: 思い出しかけて出なかった記憶を預かり、会話が途切れてから「そういえば」と言う。"""

import unittest
from datetime import datetime

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("afterthought")

from kotoha import notify  # noqa: E402
from kotoha.memory import db, embed, retrieve  # noqa: E402
from kotoha.serve import jobs  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, last_used_at, "
    "expires_at, pinned, source_key, strength, strength_at) VALUES (?,?,?,?,?,?,?,0,?,?,?)"
)


def blob(text):
    raw = text.encode()
    return raw + b" " * (-len(raw) % 4)


class FakeEmbedder:
    def __call__(self, texts, timeout=None):
        return b"query"


class Scored:
    def __init__(self, table):
        self.table = table

    def __call__(self, a, b):
        return self.table.get(bytes(b), 0.0)


class AfterthoughtCase(DbCase):
    def setUp(self):
        super().setUp()
        for name, value in (("EMBED_ENABLED", True), ("EMBED_RESERVE", 2), ("EMBED_FLOOR", 0.62),
                            ("AFTERTHOUGHT_ENABLED", True), ("AFTERTHOUGHT_MARGIN", 0.08),
                            ("AFTERTHOUGHT_AFTER_MINUTES", 15),
                            ("REACH_OUT_FROM_HOUR", 0), ("REACH_OUT_TO_HOUR", 24)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        embed._blocked_until = 0.0
        self.addCleanup(setattr, embed, "_blocked_until", 0.0)

    def add(self, text):
        now = db.now_utc()
        cur = self.conn.execute(NODE_SQL, ("semantic", "fact", text, now[:10], now, now,
                                           "2099-01-01T00:00:00Z", text, 1.0, now))
        self.conn.execute("INSERT INTO memory_vectors(node_id, model, vector) VALUES (?,?,?)",
                          (cur.lastrowid, config.EMBED_MODEL, blob(text)))
        self.conn.commit()
        return cur.lastrowid

    def use(self, scores):
        table = {blob(text): score for text, score in scores.items()}
        self.addCleanup(setattr, embed, "embed", embed.embed)
        self.addCleanup(setattr, embed, "similarity", embed.similarity)
        embed.embed, embed.similarity = FakeEmbedder(), Scored(table)

    def pending(self):
        return db.get_state(self.conn, db.AFTERTHOUGHT_ID) or ""


class KeepingTests(AfterthoughtCase):
    def test_the_nearest_miss_is_kept_and_not_recalled_now(self):
        close_call = self.add("豆から珈琲を淹れる")
        far = self.add("洗濯物をためがち")
        self.use({"豆から珈琲を淹れる": 0.58, "洗濯物をためがち": 0.40})
        _, related = retrieve.retrieve(self.conn, "コーヒー飲みたい")
        self.assertEqual(related, [])                     # その場では出ない
        self.assertEqual(self.pending(), str(close_call))
        self.assertNotEqual(self.pending(), str(far))

    def test_a_real_hit_is_not_an_afterthought(self):
        hit = self.add("豆から珈琲を淹れる")
        self.use({"豆から珈琲を淹れる": 0.80})
        _, related = retrieve.retrieve(self.conn, "コーヒー飲みたい")
        self.assertEqual([r["id"] for r in related], [hit])
        self.assertEqual(self.pending(), "")

    def test_below_the_margin_is_just_forgotten(self):
        self.add("豆から珈琲を淹れる")
        self.use({"豆から珈琲を淹れる": 0.50})
        retrieve.retrieve(self.conn, "コーヒー飲みたい")
        self.assertEqual(self.pending(), "")

    def test_the_last_one_said_today_is_not_kept_again(self):
        node = self.add("豆から珈琲を淹れる")
        db.set_state(self.conn, db.LAST_AFTERTHOUGHT_ID, node)
        self.conn.commit()
        self.use({"豆から珈琲を淹れる": 0.58})
        retrieve.retrieve(self.conn, "コーヒー飲みたい")
        self.assertEqual(self.pending(), "")

    def test_switched_off_keeps_nothing(self):
        config.AFTERTHOUGHT_ENABLED = False
        self.add("豆から珈琲を淹れる")
        self.use({"豆から珈琲を淹れる": 0.58})
        retrieve.retrieve(self.conn, "コーヒー飲みたい")
        self.assertEqual(self.pending(), "")

    def test_taking_it_empties_the_pocket(self):
        node = self.add("豆から珈琲を淹れる")
        self.use({"豆から珈琲を淹れる": 0.58})
        retrieve.retrieve(self.conn, "コーヒー飲みたい")
        row = retrieve.take_afterthought(self.conn)
        self.assertEqual(row["id"], node)
        self.assertIsNone(retrieve.take_afterthought(self.conn))


class SayingTests(AfterthoughtCase):
    def setUp(self):
        super().setUp()
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"), (chat, "speak")):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
        notify.log = lambda text: None
        notify.ready = lambda: True
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        self.closings = []
        chat.speak = lambda conn, closing, extra="", keep=True, chain=None: (
            self.closings.append((closing, keep)) or "そういえば、豆から淹れるって言ってたよね。")

    def pocket(self, text="豆から珈琲を淹れる"):
        node = self.add(text)
        db.set_state(self.conn, db.AFTERTHOUGHT_ID, node)
        self.conn.commit()
        return node

    def talked(self, minutes_ago):
        with Clock(datetime.now()) as tick:
            tick.advance(minutes=-minutes_ago)
            db.set_state(self.conn, db.LAST_CONVERSATION_AT, db.now_utc())
        self.conn.commit()

    def test_it_speaks_once_the_talk_has_gone_quiet(self):
        node = self.pocket()
        self.talked(20)
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(self.pushed, ["そういえば、豆から淹れるって言ってたよね。"])
        closing, keep = self.closings[0]
        self.assertIn("豆から珈琲を淹れる", closing)
        self.assertIn(f"[id:{node}]", closing)
        self.assertFalse(keep)                                   # 長期記憶には溜めない
        self.assertEqual(self.pending(), "")
        self.assertEqual(db.get_state(self.conn, db.LAST_AFTERTHOUGHT_ID), str(node))

    def test_not_while_still_talking(self):
        self.pocket()
        self.talked(2)
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(self.pushed, [])
        self.assertNotEqual(self.pending(), "")                  # 預かったまま

    def test_once_a_day(self):
        self.pocket()
        self.talked(20)
        jobs.maybe_afterthought(self.conn)
        self.pocket("もう一つの記憶")
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_nothing_in_the_pocket_means_silence(self):
        self.talked(20)
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(self.pushed, [])

    def test_outside_the_hours_it_waits(self):
        config.REACH_OUT_FROM_HOUR, config.REACH_OUT_TO_HOUR = 9, 9
        self.pocket()
        self.talked(20)
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(self.pushed, [])
        self.assertNotEqual(self.pending(), "")

    def test_a_memory_that_faded_meanwhile_is_dropped_quietly(self):
        node = self.pocket()
        self.conn.execute("UPDATE memory_nodes SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (node,))
        self.conn.commit()
        self.talked(20)
        jobs.maybe_afterthought(self.conn)
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.pending(), "")
        self.assertIsNone(db.get_state(self.conn, db.LAST_AFTERTHOUGHT_ON))   # 今日の1回は使っていない


if __name__ == "__main__":
    unittest.main()
