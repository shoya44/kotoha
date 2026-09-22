"""段3: 日記も座標にして、発言に近い日を思い出す。Ollama には行かない。"""

import unittest
from datetime import datetime, timedelta

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("diary_recall")

from kotoha.memory import db, diary, embed, retrieve  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def blob(text: str) -> bytes:
    """比べるためだけのベクトル。本文そのものを入れ、長さだけ4の倍数に揃える。"""
    raw = text.encode("utf-8")
    return raw + b" " * (-len(raw) % 4)


class FakeEmbedder:
    def __init__(self, error=None):
        self.error = error
        self.queries = []

    def __call__(self, texts, timeout=None):
        self.queries.append(texts[0])
        if self.error:
            raise self.error
        return b"query"


class Scored:
    def __init__(self, table):
        self.table = table

    def __call__(self, a, b):
        return self.table.get(bytes(b), 0.0)


def day_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime(diary.DAY)


class DiaryRecallCase(DbCase):
    def setUp(self):
        super().setUp()
        for name, value in (("EMBED_ENABLED", True), ("EMBED_RESERVE", 2),
                            ("EMBED_FLOOR", 0.62), ("DIARY_RECALL_LIMIT", 2)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        embed._blocked_until = 0.0
        self.addCleanup(setattr, embed, "_blocked_until", 0.0)

    def entry(self, day, text, vector=True):
        cur = self.conn.execute("INSERT INTO diary(day, text, created_at) VALUES (?,?,?)",
                                (day, text, db.now_utc()))
        if vector:
            self.conn.execute(
                "INSERT INTO diary_vectors(diary_id, model, vector) VALUES (?,?,?)",
                (cur.lastrowid, config.EMBED_MODEL, blob(text)))
        self.conn.commit()
        return cur.lastrowid

    def recent_days(self):
        """会話に毎回添えるここ数日ぶん。想起はこれより前の日から引く。"""
        for back in (1, 2, 3):
            self.entry(day_ago(back), f"{back}日前のこと。")

    def use(self, scores, error=None):
        table = {blob(text): score for text, score in scores.items()}
        fake = FakeEmbedder(error=error)
        self.addCleanup(setattr, embed, "embed", embed.embed)
        self.addCleanup(setattr, embed, "similarity", embed.similarity)
        embed.embed, embed.similarity = fake, Scored(table)
        return fake


class StorageTests(DiaryRecallCase):
    def test_a_new_entry_is_listed_as_missing_and_a_silent_day_is_not(self):
        self.entry(day_ago(10), "引っ越しの手伝いで一日つぶれた。", vector=False)
        self.entry(day_ago(9), diary.SILENT, vector=False)
        rows = embed.missing_diary(self.conn, 10, diary.SILENT)
        self.assertEqual([r["text"] for r in rows], ["引っ越しの手伝いで一日つぶれた。"])

    def test_stored_entries_are_no_longer_missing(self):
        self.entry(day_ago(10), "引っ越しの手伝いで一日つぶれた。")
        self.assertEqual(embed.missing_diary(self.conn, 10, diary.SILENT), [])
        self.assertEqual(len(embed.load_diary(self.conn)), 1)

    def test_deleting_an_entry_drops_its_vector(self):
        entry = self.entry(day_ago(10), "引っ越しの手伝いで一日つぶれた。")
        self.conn.execute("DELETE FROM diary WHERE id = ?", (entry,))
        self.conn.commit()
        self.assertEqual(embed.load_diary(self.conn), [])


class VectorJobTests(DiaryRecallCase):
    def setUp(self):
        super().setUp()
        from kotoha.serve import jobs
        self.jobs = jobs

    def test_the_job_makes_diary_vectors_too(self):
        self.entry(day_ago(10), "引っ越しの手伝いで一日つぶれた。", vector=False)
        self.entry(day_ago(9), diary.SILENT, vector=False)
        made = []
        self.addCleanup(setattr, embed, "embed", embed.embed)
        embed.embed = lambda texts, timeout=None: made.extend(texts) or [
            embed.unpack(bytes([0, 0, 128, 63])) for _ in texts]
        self.jobs.run_vector_jobs()
        self.assertEqual(made, ["引っ越しの手伝いで一日つぶれた。"])
        with db.session() as conn:
            self.assertEqual(len(embed.load_diary(conn)), 1)


class RecallTests(DiaryRecallCase):
    def setUp(self):
        super().setUp()
        self.recent_days()

    def test_a_near_diary_comes_back_with_the_day(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        self.entry("2026-08-31", "ずっと寝ていた。")
        self.use({"引っ越しの手伝いで一日つぶれた。": 0.80, "ずっと寝ていた。": 0.30})
        _, _, diaries = retrieve.retrieve_all(self.conn, "先月の引っ越しどうだった？")
        self.assertEqual(diaries, [{"day": "2026-08-30", "text": "引っ越しの手伝いで一日つぶれた。"}])

    def test_the_query_is_embedded_once_for_memories_and_diaries(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        fake = self.use({"引っ越しの手伝いで一日つぶれた。": 0.80})
        retrieve.retrieve_all(self.conn, "先月の引っ越しどうだった？")
        self.assertEqual(len(fake.queries), 1)

    def test_days_already_in_the_prompt_are_not_recalled_again(self):
        """昨日のことは毎回添えている。想起で同じ日を二度出さない。"""
        self.entry(day_ago(20), "二十日前のこと。")
        self.use({"1日前のこと。": 0.95, "二十日前のこと。": 0.90})
        _, _, diaries = retrieve.retrieve_all(self.conn, "何してた？")
        self.assertEqual([d["day"] for d in diaries], [day_ago(20)])

    def test_far_diaries_are_left_out(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        self.use({"引っ越しの手伝いで一日つぶれた。": 0.40})
        _, _, diaries = retrieve.retrieve_all(self.conn, "眠れない")
        self.assertEqual(diaries, [])

    def test_the_limit_holds(self):
        for i in range(5):
            self.entry(f"2026-08-{10 + i:02d}", f"出来事{i}")
        self.use({f"出来事{i}": 0.9 - i * 0.01 for i in range(5)})
        _, _, diaries = retrieve.retrieve_all(self.conn, "八月は？")
        self.assertEqual([d["text"] for d in diaries], ["出来事0", "出来事1"])

    def test_zero_switches_it_off_without_asking_the_engine(self):
        config.DIARY_RECALL_LIMIT = 0
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        fake = self.use({"引っ越しの手伝いで一日つぶれた。": 0.80})
        _, _, diaries = retrieve.retrieve_all(self.conn, "先月の引っ越しどうだった？")
        self.assertEqual(diaries, [])
        self.assertEqual(fake.queries, [])          # 比べる記憶も無いので、座標も作らない

    def test_engine_down_means_no_diary_and_no_error(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        self.use({}, error=embed.EmbedError("止まっている"))
        _, _, diaries = retrieve.retrieve_all(self.conn, "先月の引っ越しどうだった？")
        self.assertEqual(diaries, [])

    def test_the_two_tuple_road_still_works(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        self.use({"引っ越しの手伝いで一日つぶれた。": 0.80})
        pinned, related = retrieve.retrieve(self.conn, "先月の引っ越しどうだった？")
        self.assertEqual((pinned, related), ([], []))


class PromptTests(DiaryRecallCase):
    def setUp(self):
        super().setUp()
        self.recent_days()

    def test_the_prompt_carries_what_was_recalled(self):
        diaries = [{"day": "2026-08-30", "text": "引っ越しの手伝いで一日つぶれた。"}]
        prompt = chat.build_prompt(self.conn, "先月の引っ越しどうだった？", [], [], [], diaries=diaries)
        self.assertIn("思い当たる日記", prompt)
        self.assertIn("8/30: 引っ越しの手伝いで一日つぶれた。", prompt)

    def test_nothing_recalled_means_no_block(self):
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [])
        self.assertNotIn("思い当たる日記", prompt)

    def test_the_fast_path_never_carries_it(self):
        diaries = [{"day": "2026-08-30", "text": "引っ越しの手伝いで一日つぶれた。"}]
        prompt = chat.build_prompt(self.conn, "やあ", [], [], [], fast=True, diaries=diaries)
        self.assertNotIn("思い当たる日記", prompt)

    def test_a_turn_reaches_the_model_with_the_diary(self):
        self.entry("2026-08-30", "引っ越しの手伝いで一日つぶれた。")
        self.use({"引っ越しの手伝いで一日つぶれた。": 0.80})
        seen = []
        self.addCleanup(setattr, chat.llm, "chat", chat.llm.chat)
        chat.llm.chat = lambda prompt, max_tokens=None: seen.append(prompt) or "大変だったねー"
        chat.run_turn(self.conn, "先月の引っ越しどうだった？")
        self.assertIn("8/30: 引っ越しの手伝いで一日つぶれた。", seen[-1])


if __name__ == "__main__":
    unittest.main()
