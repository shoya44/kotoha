"""タグ想起の並び順の検証。一時DBだけを使い、LLMは呼ばない。"""

import unittest
from pathlib import Path

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("retrieve")

from kotoha.memory import db, embed, retrieve, strength  # noqa: E402

NODE_SQL = (
    "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
    "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,?,?)"
)


def tearDownModule():
    _TMP.cleanup()


def use_persona(case, text: str) -> None:
    """人格をこの文にする。本物の data/prompts を読ませない（名前が違えば落ちる）。"""
    folder = Path(_TMP.name) / "prompts"
    folder.mkdir(exist_ok=True)
    (folder / "persona.txt").write_text(text, encoding="utf-8")
    case.addCleanup(setattr, config, "PERSONAL_PROMPTS_DIR", config.PERSONAL_PROMPTS_DIR)
    config.PERSONAL_PROMPTS_DIR = folder


class TagOrderTests(DbCase):
    def setUp(self):
        super().setUp()
    def add(self, text, tag, use_count, confirmed_at, last_used_at):
        cur = self.conn.execute(
            NODE_SQL,
            ("semantic", "fact", text, "2026-01-01", confirmed_at,
             last_used_at, "2099-01-01T00:00:00Z", 0, text),
        )
        self.conn.execute(
            "INSERT INTO memory_tags(node_id, tag, use_count, last_used_at) VALUES (?,?,?,?)",
            (cur.lastrowid, tag, use_count, last_used_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def strengthen(self, node_id, value):
        self.conn.execute("UPDATE memory_nodes SET strength = ?, strength_at = ? WHERE id = ?",
                          (value, db.now_utc(), node_id))
        self.conn.commit()

    def test_the_stronger_memory_comes_first(self):
        """使うほど強く、放っておくほど薄れる。並びは強さで決まる。"""
        weak = self.add("たまにしか使わない", "仕事", 3,
                        "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        strong = self.add("よく思い出す", "仕事", 0,
                          "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.strengthen(weak, 0.5)
        self.strengthen(strong, 4.0)
        _, related = retrieve.retrieve(self.conn, "仕事の話をしよう")
        self.assertEqual([r["id"] for r in related][:2], [strong, weak])

    def test_frequently_used_memory_comes_first_at_equal_strength(self):
        """同じ強さなら、use_count が読まれる（書かれるだけの退行を防ぐ）。"""
        rare = self.add("たまにしか使わない", "仕事", 0,
                        "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        often = self.add("よく思い出す", "仕事", 3,
                         "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        for node in (rare, often):
            self.strengthen(node, 1.0)
        _, related = retrieve.retrieve(self.conn, "仕事の話をしよう")
        self.assertEqual([r["id"] for r in related][:2], [often, rare])

    def test_recently_used_memory_wins_at_equal_counts(self):
        older = self.add("前に使った", "趣味", 1,
                         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        newer = self.add("最近使った", "趣味", 1,
                         "2026-01-01T00:00:00Z", "2026-09-01T00:00:00Z")
        for node in (older, newer):
            self.strengthen(node, 1.0)
        _, related = retrieve.retrieve(self.conn, "趣味の話")
        self.assertEqual([r["id"] for r in related][:2], [newer, older])

    def test_tag_dictionary_lists_distinct_tags(self):
        self.add("あ", "仕事", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.add("い", "仕事", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.assertEqual(retrieve.load_tag_dict(self.conn), ["仕事"])

    def test_names_are_not_tags(self):
        """名前はどの発言にも出る。タグにすると毎回当たり、同じ記憶が居座る。"""
        use_persona(self, "## ことは\n- 名前はことは。\n- ユーザーを「たろう」と呼ぶ。以下の「相手」はユーザーのこと。\n")
        self.assertEqual(retrieve.persona_names(), {"ことは", "たろう"})
        self.add("たろうと話すと機嫌がいい", "たろう", 3, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.add("ことはは甘いものが好き", "ことは", 3, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.add("通院は木曜", "通院", 0, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        self.assertEqual(retrieve.load_tag_dict(self.conn), ["通院"])
        pinned, related = retrieve.retrieve(self.conn, "たろうだよ、ことは元気？")
        self.assertEqual(related, [])

    def test_a_persona_without_names_skips_nothing(self):
        use_persona(self, "## ことは\n- ものぐさ。\n")
        self.assertEqual(retrieve.persona_names(), set())

    def test_no_tag_hit_falls_back_to_recent(self):
        self.add("関係ない話", "料理", 0, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z")
        pinned, related = retrieve.retrieve(self.conn, "まったく別の話題")
        self.assertEqual(pinned, [])
        self.assertEqual(related, [])  # episode でも open_topic でもないので拾わない

    def test_open_topics_are_not_crowded_out_by_episodes(self):
        """続きのある話は、出来事が続いた週でも押し出されない。"""
        self.addCleanup(setattr, config, "RETRIEVE_RECENT_LIMIT", config.RETRIEVE_RECENT_LIMIT)
        config.RETRIEVE_RECENT_LIMIT = 2
        self.conn.execute(
            NODE_SQL, ("semantic", "open_topic", "引っ越し先を探している", "2026-08-01",
                       "2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z",
                       "2099-01-01T00:00:00Z", 0, "topic"))
        for day in range(2, 6):
            stamp = f"2026-09-0{day}T00:00:00Z"
            self.conn.execute(
                NODE_SQL, ("episode", "event", f"出来事{day}", stamp[:10], stamp, stamp,
                           "2099-01-01T00:00:00Z", 0, f"event{day}"))
        self.conn.commit()
        _pinned, related = retrieve.retrieve(self.conn, "まったく別の話題")
        kinds = [r["kind"] for r in related]
        self.assertIn("open_topic", kinds)
        self.assertEqual(kinds.count("event"), 2)


def blob(text):
    """比べるためだけのベクトル。本文そのものを入れ、長さだけ4の倍数に揃える。"""
    raw = text.encode()
    return raw + b" " * (-len(raw) % 4)


class FakeEmbedder:
    """Ollama の代わり。呼ばれた回数と、渡された文を覚えておく。"""

    def __init__(self, scores=None, error=None):
        # scores: 記憶の本文 -> 近さ。書いていない記憶は遠いものとして扱う。
        self.scores = scores or {}
        self.error = error
        self.queries = []

    def __call__(self, texts, timeout=None):
        self.queries.append(texts[0])
        if self.error:
            raise self.error
        return b"query"


class Scored:
    """embed.similarity の代わり。本文ごとに決めた値を返す。"""

    def __init__(self, table):
        self.table = table

    def __call__(self, a, b):
        return self.table.get(bytes(b), 0.0)


class MeaningTests(DbCase):
    """意味で思い出すぶんの検証。実際のOllamaには接続しない。"""

    def setUp(self):
        super().setUp()
        for name, value in (("EMBED_ENABLED", True), ("EMBED_RESERVE", 2),
                            ("EMBED_FLOOR", 0.62), ("EMBED_CONTEXT_LINES", 2)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        embed._blocked_until = 0.0
        self.addCleanup(setattr, embed, "_blocked_until", 0.0)

    def add(self, text, days_ahead="2099-01-01T00:00:00Z",
            when="2026-09-01T00:00:00Z"):
        cur = self.conn.execute(
            NODE_SQL,
            ("episode", "event", text, "2026-01-01", when, when, days_ahead, 0, text),
        )
        node_id = cur.lastrowid
        # ベクトルの中身は本文そのものにしておく。近さを本文で決められる。
        self.conn.execute(
            "INSERT INTO memory_vectors(node_id, model, vector) VALUES (?,?,?)",
            (node_id, config.EMBED_MODEL, blob(text)),
        )
        self.conn.commit()
        return node_id

    def fresh(self, node_id):
        """いま思い出したばかりの強さにする。古い記憶でも薄れていない、という設定。"""
        self.conn.execute("UPDATE memory_nodes SET strength = ?, strength_at = ? WHERE id = ?",
                          (strength.FRESH, db.now_utc(), node_id))
        self.conn.commit()

    def use(self, scores, error=None):
        """本文ごとの近さを決めて、engine と similarity を差し替える。"""
        table = {blob(text): score for text, score in scores.items()}
        fake = FakeEmbedder(error=error)
        self.addCleanup(setattr, embed, "embed", embed.embed)
        self.addCleanup(setattr, embed, "similarity", embed.similarity)
        embed.embed, embed.similarity = fake, Scored(table)
        return fake

    def texts(self, related):
        return [r["text"] for r in related]

    def fill_recent(self, count=12):
        """直近枠を埋める。これより古い記憶は、意味で引く以外に出てこない。"""
        for i in range(count):
            self.add(f"最近の出来事{i}", when=f"2026-09-{10 + i:02d}T00:00:00Z")

    def test_meaningful_memory_is_recalled_without_a_matching_word(self):
        """タグが当たらなくても、意味が近ければ出てくる。"""
        # 直近枠からあふれる古さにしておく。出てきたら意味でたどり着いた証拠。
        node = self.add("ユーザーは豆から珈琲を淹れている。",
                        when="2026-01-05T00:00:00Z")
        self.fresh(node)
        self.fill_recent()
        self.use({"ユーザーは豆から珈琲を淹れている。": 0.80})
        _, related = retrieve.retrieve(self.conn, "珈琲")
        self.assertIn("ユーザーは豆から珈琲を淹れている。", self.texts(related))

    def test_a_faded_memory_needs_a_stronger_cue(self):
        """思い出せそうで出ない。薄れた記憶は、床ぎりぎりの近さでは出ず、強い手がかりなら出る。"""
        node = self.add("ユーザーは豆から珈琲を淹れている。", when="2026-01-05T00:00:00Z")
        self.conn.execute("UPDATE memory_nodes SET strength = 0.15, strength_at = ? WHERE id = ?",
                          (db.now_utc(), node))
        self.conn.commit()
        self.fill_recent()
        self.use({"ユーザーは豆から珈琲を淹れている。": 0.70})
        _, related = retrieve.retrieve(self.conn, "珈琲")
        self.assertNotIn("ユーザーは豆から珈琲を淹れている。", self.texts(related))
        self.use({"ユーザーは豆から珈琲を淹れている。": 0.95})
        _, related = retrieve.retrieve(self.conn, "珈琲")
        self.assertIn("ユーザーは豆から珈琲を淹れている。", self.texts(related))

    def test_reserved_slots_do_not_crowd_out_the_rest(self):
        self.add("意味が近い記憶", when="2026-01-05T00:00:00Z")
        self.fill_recent()
        self.use({"意味が近い記憶": 0.90})
        _, related = retrieve.retrieve(self.conn, "眠れない")
        self.assertEqual(len(related), config.RELATED_LIMIT)
        self.assertEqual(self.texts(related).count("意味が近い記憶"), 1)

    def test_far_memories_are_left_out(self):
        """近いものが無い回に無理に引くと、関係ない記憶で枠を潰す。"""
        self.add("遠い記憶", when="2026-01-05T00:00:00Z")
        self.fill_recent()
        self.use({"遠い記憶": 0.30})
        _, related = retrieve.retrieve(self.conn, "眠れない")
        self.assertNotIn("遠い記憶", self.texts(related))

    def test_expired_memory_is_not_recalled(self):
        self.add("忘れたはずの記憶", days_ahead="2000-01-01T00:00:00Z",
                 when="2026-01-05T00:00:00Z")
        self.fill_recent()
        self.use({"忘れたはずの記憶": 0.99})
        _, related = retrieve.retrieve(self.conn, "眠れない")
        self.assertNotIn("忘れたはずの記憶", self.texts(related))

    def test_switched_off_matches_the_old_behaviour(self):
        """RESERVE=0 は、この機能を入れる前と1件も違わないこと。"""
        self.fill_recent()
        fake = self.use({"最近の出来事0": 0.99})
        config.EMBED_RESERVE = 0
        _, related = retrieve.retrieve(self.conn, "眠れない")
        self.assertEqual(fake.queries, [])
        self.assertEqual(len(related), config.RELATED_LIMIT)

    def test_engine_down_matches_the_old_behaviour(self):
        self.fill_recent()
        config.EMBED_RESERVE = 0
        _, expected = retrieve.retrieve(self.conn, "眠れない")
        config.EMBED_RESERVE = 2
        self.use({}, error=embed.EmbedError("止まっている"))
        _, related = retrieve.retrieve(self.conn, "眠れない")
        self.assertEqual(self.texts(related), self.texts(expected))

    def test_a_dead_engine_is_not_asked_every_turn(self):
        """止まっているのに毎回つなぎに行くと、会話のたびに待たされる。"""
        self.add("記憶")
        fake = self.use({}, error=embed.EmbedError("止まっている"))
        self.addCleanup(setattr, embed, "available", embed.available)
        embed.available = lambda: False
        retrieve.retrieve(self.conn, "眠れない")
        self.assertEqual(fake.queries, [])

    def test_context_is_added_to_the_query(self):
        """発言だけでは短すぎる。直前のやりとりを足して意味を定める。"""
        query = retrieve._query_text("しんどい", "おはよう" + chr(10) + "よく眠れた？")
        self.assertEqual(query, "おはよう" + chr(10) + "よく眠れた？" + chr(10) + "しんどい")

    def test_context_length_is_capped(self):
        config.EMBED_CONTEXT_LINES = 1
        recent = chr(10).join(["ずっと前", "ひとつ前"])
        self.assertEqual(retrieve._query_text("いま", recent),
                         "ひとつ前" + chr(10) + "いま")


if __name__ == "__main__":
    unittest.main()
