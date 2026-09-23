"""固定化のバッチ切り出しの検証。一時DBだけを使い、LLMは呼ばない。"""

import array
import unittest

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("consolidate")

from kotoha.memory import consolidate, db, embed  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class FetchUnprocessedTests(DbCase):
    def setUp(self):
        super().setUp()
    def add_turn(self, turn_id, user_text="質問", assistant_text="返事"):
        db.insert_message(self.conn, turn_id, "user", user_text)
        db.insert_message(self.conn, turn_id, "assistant", assistant_text)
        self.conn.commit()

    def test_oversized_single_message_is_still_picked_up(self):
        """上限超えの1通で固定化が恒久停止する退行を防ぐ。"""
        self.add_turn(1, user_text="あ" * (config.BATCH_CHARS + 1000))
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertTrue(picked, "上限超えの1通でバッチが空になり、処理が進まなくなる")
        self.assertEqual(picked[0]["id"], 1)

    def test_progress_continues_after_an_oversized_message(self):
        """巨大メッセージを処理済みにすれば、次のバッチが前進する。"""
        self.add_turn(1, user_text="あ" * (config.BATCH_CHARS + 1000))
        self.add_turn(2, user_text="短い質問")
        first = consolidate.fetch_unprocessed(self.conn)
        db.set_state(self.conn, "last_processed_message_id", first[-1]["id"])
        self.conn.commit()
        second = consolidate.fetch_unprocessed(self.conn)
        self.assertTrue(second)
        self.assertGreater(second[0]["id"], first[-1]["id"])

    def test_char_limit_still_stops_the_batch(self):
        half = "い" * (config.BATCH_CHARS // 2 + 100)
        self.add_turn(1, user_text=half, assistant_text=half)
        self.add_turn(2)
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertEqual(len(picked), 1)  # 先頭1通のみ。2通目で上限に達する

    def test_turn_limit_still_stops_the_batch(self):
        for turn_id in range(1, config.BATCH_TURNS + 3):
            self.add_turn(turn_id)
        picked = consolidate.fetch_unprocessed(self.conn)
        self.assertEqual(len({r["turn_id"] for r in picked}), config.BATCH_TURNS)

    def test_nothing_to_do_returns_empty(self):
        self.assertEqual(consolidate.fetch_unprocessed(self.conn), [])


class ClipTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(consolidate._clip("短い本文"), "短い本文")

    def test_oversized_text_is_clipped(self):
        clipped = consolidate._clip("う" * (config.BATCH_CHARS + 5000))
        self.assertLess(len(clipped), config.BATCH_CHARS + 100)
        self.assertTrue(clipped.endswith("（以下省略）"))


class SafeDateTests(unittest.TestCase):
    """出来事の日付。モデルは年を取り違えることがある。"""

    BASE = "2026-09-17T00:00:00Z"

    def test_a_sane_date_is_kept(self):
        self.assertEqual(consolidate._safe_date("2026-09-17", self.BASE), "2026-09-17")

    def test_a_date_months_back_is_kept(self):
        """過去を振り返る話もあるので、少し前は通す。"""
        self.assertEqual(consolidate._safe_date("2026-03-01", self.BASE), "2026-03-01")

    def test_a_wrong_year_falls_back_to_the_conversation(self):
        """実データに2023年と書かれた記憶があった。別の時代に置かれてしまう。"""
        self.assertEqual(consolidate._safe_date("2023-09-17", self.BASE), "2026-09-17")

    def test_an_impossible_date_falls_back(self):
        self.assertEqual(consolidate._safe_date("2026-13-45", self.BASE), "2026-09-17")

    def test_a_non_date_falls_back(self):
        for value in ("きのう", "", None, 20260917):
            self.assertEqual(consolidate._safe_date(value, self.BASE), "2026-09-17")


class MergeTests(DbCase):
    """言い直しただけの記憶を作らせない。同じ話で想起の6枠が埋まるのを防ぐ。"""

    def setUp(self):
        super().setUp()
        for name, value in (("EMBED_ENABLED", True), ("EMBED_MERGE_FLOOR", 0.90)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        self.addCleanup(setattr, embed, "embed", embed.embed)
        self.addCleanup(setattr, embed, "available", embed.available)
        self.addCleanup(setattr, embed, "link_similar", embed.link_similar)
        embed.link_similar = lambda conn: 0
        db.insert_message(self.conn, 1, "user", "通院の話")
        self.conn.commit()
        self.messages = self.conn.execute(
            "SELECT id, text, created_at FROM messages"
        ).fetchall()

    def existing(self, text, *values):
        cur = self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) "
            "VALUES ('episode','event',?,?,?,?,NULL,0,?)",
            (text, "2026-09-01", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", text),
        )
        total = sum(v * v for v in values) ** 0.5
        embed.store(self.conn, [(cur.lastrowid, array.array("f", [v / total for v in values]))])
        return cur.lastrowid

    def use(self, *values):
        """これから作る候補の座標を決める。"""
        total = sum(v * v for v in values) ** 0.5
        vector = array.array("f", [v / total for v in values])
        embed.embed = lambda texts, timeout=None: [vector] * len(texts)

    def save(self, text):
        return consolidate._validate_and_save(self.conn, {"new_nodes": [{
            "layer": "episode", "text": text,
            "source_message_ids": [self.messages[0]["id"]],
        }]}, self.messages)

    def texts(self):
        return [r[0] for r in self.conn.execute("SELECT text FROM memory_nodes")]

    def test_a_restatement_is_not_stored_twice(self):
        self.existing("9月17日の午後に通院する予定だと話した。", 1.0, 0.0)
        self.use(1.0, 0.02)
        created, merged = self.save("9月17日の午後に病院へ行く予定だと話した。")
        self.assertEqual((created, merged), (0, 1))
        self.assertEqual(len(self.texts()), 1)

    def test_the_existing_memory_is_reconfirmed_instead(self):
        node = self.existing("通院する予定だと話した。", 1.0, 0.0)
        before = self.conn.execute(
            "SELECT confirmed_at FROM memory_nodes WHERE id = ?", (node,)
        ).fetchone()[0]
        self.use(1.0, 0.02)
        self.save("病院へ行く予定だと話した。")
        after = self.conn.execute(
            "SELECT confirmed_at FROM memory_nodes WHERE id = ?", (node,)
        ).fetchone()[0]
        self.assertGreater(after, before)

    def test_the_source_is_attached_to_the_existing_memory(self):
        """どの会話で確かめたかを失わない。"""
        node = self.existing("通院する予定だと話した。", 1.0, 0.0)
        self.use(1.0, 0.02)
        self.save("病院へ行く予定だと話した。")
        linked = self.conn.execute(
            "SELECT COUNT(*) FROM memory_sources WHERE node_id = ?", (node,)
        ).fetchone()[0]
        self.assertEqual(linked, 1)

    def test_a_different_fact_is_still_stored(self):
        """近いだけで別の事実のことがある。取り違えると情報が消えて戻らない。"""
        self.existing("Rocket Nowの出前にはまっている。", 1.0, 0.0)
        self.use(1.0, 0.5)   # 0.89 ほど。しきい値のすぐ下
        created, merged = self.save("出前館もたまに利用している。")
        self.assertEqual((created, merged), (1, 0))
        self.assertEqual(len(self.texts()), 2)

    def test_engine_down_stores_everything_as_before(self):
        self.existing("通院する予定だと話した。", 1.0, 0.0)
        embed.available = lambda: False
        created, merged = self.save("病院へ行く予定だと話した。")
        self.assertEqual((created, merged), (1, 0))

    def test_switched_off_stores_everything_as_before(self):
        self.existing("通院する予定だと話した。", 1.0, 0.0)
        config.EMBED_MERGE_FLOOR = 0
        self.use(1.0, 0.0)
        created, merged = self.save("病院へ行く予定だと話した。")
        self.assertEqual((created, merged), (1, 0))


class GiveUpTests(DbCase):
    """読めない返事が続いたら、そのバッチは置いていく。枠を食い続けない。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, consolidate.llm, "chat", consolidate.llm.chat)
        for turn in range(1, 4):
            db.insert_message(self.conn, turn, "user", f"{turn}回目の話")
            db.insert_message(self.conn, turn, "assistant", "ふーん")
        self.conn.commit()

    def answer(self, text):
        consolidate.llm.chat = lambda prompt, max_tokens=None: text

    def position(self):
        return int(db.get_state(self.conn, db.LAST_PROCESSED_MESSAGE_ID, "0") or 0)

    def fails(self):
        return int(db.get_state(self.conn, db.CONSOLIDATE_FAILS, "0") or 0)

    def test_a_failure_is_counted_and_the_batch_stays(self):
        self.answer("JSONじゃない返事")
        with self.assertRaises(consolidate.llm.LLMError):
            consolidate.run(self.conn)
        self.assertEqual(self.fails(), 1)
        self.assertEqual(self.position(), 0)      # まだ置いていかない

    def test_it_gives_up_after_a_few_tries(self):
        self.answer("JSONじゃない返事")
        for _ in range(consolidate.GIVE_UP_AFTER):
            with self.assertRaises(consolidate.llm.LLMError):
                consolidate.run(self.conn)
        self.assertGreater(self.position(), 0)    # 置いていった
        self.assertEqual(self.fails(), 0)         # 数え直す

    def test_a_good_answer_clears_the_count(self):
        self.answer("JSONじゃない返事")
        with self.assertRaises(consolidate.llm.LLMError):
            consolidate.run(self.conn)
        self.answer('{"new_nodes": [], "edges": [], "reconfirm_ids": [], "updates": []}')
        consolidate.run(self.conn)
        self.assertEqual(self.fails(), 0)

    def test_nothing_to_process_is_not_a_failure(self):
        """整理するものが無いときは、失敗の数を触らない。"""
        db.set_state(self.conn, db.LAST_PROCESSED_MESSAGE_ID, 999)
        self.conn.commit()
        consolidate.run(self.conn)
        self.assertEqual(self.fails(), 0)


if __name__ == "__main__":
    unittest.main()


class HonestMemoryTests(DbCase):
    """作り話を記憶にしない。名前をタグにしない。

    ことはの発言から「ことはが朝ごはんを作った」を出来事にすると、作り話が
    記憶になり、次の返事の根拠になる循環ができる（実際に起きた: スープパスタ）。
    名前は全往復で命中するので、タグにすると同じ記憶が想起の枠に居座る。
    """

    def setUp(self):
        super().setUp()
        from pathlib import Path
        folder = Path(_TMP.name) / "prompts"
        folder.mkdir(exist_ok=True)
        (folder / "persona.txt").write_text(
            "## ことは\n- 名前はことは。\n- ユーザーを「たろう」と呼ぶ。\n", encoding="utf-8")
        self.addCleanup(setattr, config, "PERSONAL_PROMPTS_DIR", config.PERSONAL_PROMPTS_DIR)
        config.PERSONAL_PROMPTS_DIR = folder
        db.insert_message(self.conn, 1, "user", "朝ごはん食べたよ")
        self.conn.commit()
        self.messages = self.conn.execute("SELECT id, text, created_at FROM messages").fetchall()

    def tags(self):
        return sorted(r[0] for r in self.conn.execute("SELECT tag FROM memory_tags"))

    def test_names_are_dropped_from_tags(self):
        consolidate._validate_and_save(self.conn, {"new_nodes": [{
            "layer": "episode", "text": "たろうは朝ごはんを食べた。",
            "tags": ["たろう", "ことは", "朝ごはん"],
            "source_message_ids": [self.messages[0]["id"]],
        }]}, self.messages)
        self.assertEqual(self.tags(), ["朝ごはん"])

    def test_the_rules_are_written_down(self):
        """ルール文は Gemini にしか読めない。消えたら気づけないので、ここで見張る。"""
        from kotoha.talk import chat
        rules = chat._read("consolidation_system.txt")
        self.assertIn("人名", rules)
        self.assertIn("ことはが主語の出来事", rules)
