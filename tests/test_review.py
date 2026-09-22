"""段4: 夜の整理。ここ1週間の記憶を読み返して、寄せる・直す・閉じる。LLMは呼ばない。"""

import unittest
from datetime import datetime, timedelta

from kotoha import config
from tests.support import Clock, DbCase, use_temp_db

_TMP = use_temp_db("review")

from kotoha import notify  # noqa: E402
from kotoha.memory import db, diary, review  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class Pen:
    def __init__(self, text):
        self.text = text
        self.prompts = []

    def chat(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.text


class ReviewCase(DbCase):
    def setUp(self):
        super().setUp()
        for name, value in (("DIARY_ENABLED", True), ("REVIEW_ENABLED", True), ("DIARY_HOUR", 0)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, review, "llm", review.llm)
        self.addCleanup(setattr, notify, "log", notify.log)
        self.logged = []
        notify.log = lambda text: self.logged.append(text)

    def node(self, text, kind="fact", layer="semantic", days_ago=1, pinned=0, tags=(), sources=()):
        when = (datetime.now() - timedelta(days=days_ago)).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, last_used_at, "
            "expires_at, pinned, source_key, strength, strength_at) VALUES (?,?,?,?,?,?,?,?,?,1.0,?)",
            (layer, kind, text, when[:10], when, when, "2099-01-01T00:00:00Z", pinned, text, when))
        node_id = cur.lastrowid
        for tag in tags:
            self.conn.execute("INSERT INTO memory_tags(node_id, tag) VALUES (?,?)", (node_id, tag))
        for mid in sources:
            self.conn.execute("INSERT OR IGNORE INTO messages(id, turn_id, role, text, created_at) "
                              "VALUES (?,?,?,?,?)", (mid, mid, "user", "x", when))
            self.conn.execute("INSERT INTO memory_sources(node_id, message_id) VALUES (?,?)", (node_id, mid))
        self.conn.commit()
        return node_id

    def diary(self, days_ago, text):
        day = (datetime.now() - timedelta(days=days_ago)).strftime(diary.DAY)
        self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)", (day, text, db.now_utc()))
        self.conn.commit()

    def rows(self):
        return {r["id"]: dict(r) for r in self.conn.execute("SELECT * FROM memory_nodes")}


class WhenTests(ReviewCase):
    def test_after_yesterdays_diary_and_once_a_day(self):
        self.assertFalse(review.due(self.conn))          # 昨日の日記がまだ無い
        self.diary(1, "昨日のこと。")
        self.assertTrue(review.due(self.conn))
        db.mark_today(self.conn, db.LAST_REVIEW_ON, datetime.now().strftime("%Y-%m-%d"))
        self.assertFalse(review.due(self.conn))

    def test_off_means_off(self):
        self.diary(1, "昨日のこと。")
        config.REVIEW_ENABLED = False
        self.assertFalse(review.due(self.conn))

    def test_waits_for_the_hour(self):
        self.diary(1, "昨日のこと。")
        config.DIARY_HOUR = 23
        with Clock(datetime.now().replace(hour=1, minute=0)):
            self.assertFalse(review.due(self.conn))


class MaterialTests(ReviewCase):
    def test_last_week_unpinned_and_every_open_topic(self):
        fresh = self.node("今週のこと")
        old = self.node("先月のこと", days_ago=20)
        pinned = self.node("基本情報", days_ago=1, pinned=1)
        topic = self.node("引っ越し、どうなった？", kind="open_topic", days_ago=40)
        ids = [r["id"] for r in review.material(self.conn)]
        self.assertIn(fresh, ids)
        self.assertIn(topic, ids)
        self.assertNotIn(old, ids)
        self.assertNotIn(pinned, ids)

    def test_the_prompt_carries_ids_and_the_diary(self):
        self.node("今週のこと")
        self.node("もう一つ")
        self.diary(1, "昨日は引っ越しの手伝い。")
        pen = Pen('{"merge": [], "fix": [], "close": []}')
        review.llm = pen
        review.run(self.conn)
        self.assertIn("[id:1]", pen.prompts[0])
        self.assertIn("昨日は引っ越しの手伝い。", pen.prompts[0])


class ApplyTests(ReviewCase):
    def test_merge_keeps_one_and_moves_sources_tags_and_edges(self):
        keep = self.node("コーヒーは豆から淹れる", tags=("コーヒー",), sources=(1,))
        dup = self.node("珈琲は豆から", tags=("珈琲",), sources=(2,))
        other = self.node("ミルは手挽き")
        self.conn.execute("INSERT INTO memory_edges(from_id, to_id, relation) VALUES (?,?,'related_to')", (dup, other))
        self.conn.commit()
        done = review.apply(self.conn, {"merge": [{"keep": keep, "drop": [dup]}]}, {keep, dup, other})
        self.assertEqual(done["merged"], 1)
        self.assertNotIn(dup, self.rows())
        tags = {r["tag"] for r in self.conn.execute("SELECT tag FROM memory_tags WHERE node_id = ?", (keep,))}
        self.assertEqual(tags, {"コーヒー", "珈琲"})
        sources = {r["message_id"] for r in self.conn.execute("SELECT message_id FROM memory_sources WHERE node_id = ?", (keep,))}
        self.assertEqual(sources, {1, 2})
        edges = self.conn.execute("SELECT from_id, to_id FROM memory_edges").fetchall()
        self.assertEqual([tuple(e) for e in edges], [(keep, other)])
        self.assertGreater(self.rows()[keep]["strength"], 1.0)      # 何度も出てきた＝確かめ直した

    def test_merge_never_touches_pinned_or_unknown(self):
        keep = self.node("a")
        pinned = self.node("b", pinned=1)
        done = review.apply(self.conn, {"merge": [{"keep": keep, "drop": [pinned, 999]}]}, {keep, pinned})
        self.assertEqual(done["merged"], 0)
        self.assertIn(pinned, self.rows())

    def test_fix_rewrites_and_drops_the_vector(self):
        node = self.node("引っ越しは来週", tags=("引っ越し",))
        self.conn.execute("UPDATE memory_tags SET use_count = 3 WHERE node_id = ?", (node,))
        self.conn.execute("INSERT INTO memory_vectors(node_id, model, vector) VALUES (?,?,?)",
                          (node, config.EMBED_MODEL, b"\x00\x00\x80\x3f"))
        self.conn.commit()
        done = review.apply(self.conn, {"fix": [{"id": node, "text": "引っ越しは土曜に終わった"}]}, {node})
        self.assertEqual(done["fixed"], 1)
        self.assertEqual(self.rows()[node]["text"], "引っ越しは土曜に終わった")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT use_count FROM memory_tags WHERE node_id = ?",
                                           (node,)).fetchone()[0], 0)

    def test_fix_with_no_text_does_nothing(self):
        node = self.node("そのまま")
        done = review.apply(self.conn, {"fix": [{"id": node, "text": "  "}]}, {node})
        self.assertEqual(done["fixed"], 0)
        self.assertEqual(self.rows()[node]["text"], "そのまま")

    def test_close_turns_a_topic_into_a_fact(self):
        topic = self.node("引っ越し、どうなった？", kind="open_topic")
        done = review.apply(self.conn, {"close": [{"id": topic, "text": "引っ越しは土曜に無事終わった"}]}, {topic})
        self.assertEqual(done["closed"], 1)
        self.assertEqual(self.rows()[topic]["kind"], "fact")
        self.assertEqual(self.rows()[topic]["text"], "引っ越しは土曜に無事終わった")

    def test_close_only_works_on_open_topics(self):
        fact = self.node("事実")
        done = review.apply(self.conn, {"close": [{"id": fact}]}, {fact})
        self.assertEqual(done["closed"], 0)
        self.assertEqual(self.rows()[fact]["kind"], "fact")

    def test_limits_and_garbage_are_tolerated(self):
        ids = [self.node(f"n{i}") for i in range(8)]
        data = {"merge": [{"keep": ids[0], "drop": [ids[i]]} for i in range(1, 8)],
                "fix": "not a list", "close": [None, 3, {"id": "x"}]}
        done = review.apply(self.conn, data, set(ids))
        self.assertEqual(done["merged"], review.LIMIT_EACH)
        self.assertEqual(done["fixed"], 0)
        self.assertEqual(done["closed"], 0)


class RunTests(ReviewCase):
    def test_nothing_to_compare_means_no_api_call(self):
        self.node("ひとつだけ")
        self.diary(1, "昨日のこと。")
        pen = Pen("{}")
        review.llm = pen
        review.run(self.conn)
        self.assertEqual(pen.prompts, [])
        self.assertIsNotNone(db.get_state(self.conn, db.LAST_REVIEW_ON))

    def test_a_full_round_is_logged(self):
        a = self.node("コーヒーは豆から淹れる")
        b = self.node("珈琲は豆から")
        self.diary(1, "昨日のこと。")
        review.llm = Pen(f'{{"merge": [{{"keep": {a}, "drop": [{b}]}}], "fix": [], "close": []}}')
        done = review.run(self.conn)
        self.assertEqual(done, {"merged": 1, "fixed": 0, "closed": 0})
        self.assertTrue(any("夜の整理" in line for line in self.logged))

    def test_a_bad_answer_is_an_error_but_not_retried_today(self):
        self.node("a")
        self.node("b")
        self.diary(1, "昨日のこと。")
        review.llm = Pen("うーん")
        with self.assertRaises(ValueError):
            review.run(self.conn)
        self.assertFalse(review.due(self.conn))


if __name__ == "__main__":
    unittest.main()
