"""記憶を意味の座標に変える処理の検証。実際のOllamaには接続しない。"""

import array
import tempfile
import unittest
from pathlib import Path

import httpx

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha embed ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db, embed  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def vector(*values):
    """長さは揃えない。揃えるのは embed 側の仕事なので、そこを試すため。"""
    return list(values) + [0.0] * (8 - len(values))


class FakeOllama:
    """Ollama の代わり。呼ばれ方を記録する。"""

    def __init__(self, status=200, body=None, error=None):
        self.status = status
        self.body = body
        self.error = error
        self.calls = []

    def __call__(self, url, **kwargs):
        payload = kwargs.get("json") or {}
        self.calls.append(payload)
        if self.error:
            raise self.error
        if self.body is not None:
            return httpx.Response(self.status, json=self.body)
        count = len(payload.get("input", []))
        return httpx.Response(self.status, json={"embeddings": [vector(3.0, 4.0)] * count})


class StaleOnce(FakeOllama):
    """最初の1回だけ、切れた接続のふりをする。"""

    def __call__(self, url, **kwargs):
        if not self.calls:
            self.calls.append(kwargs.get("json") or {})
            raise httpx.RemoteProtocolError("Server disconnected")
        return super().__call__(url, **kwargs)


class EngineMixin:
    def use(self, engine):
        client = embed._http()
        original = client.post
        client.post = engine
        self.addCleanup(setattr, client, "post", original)
        return engine


class EmbedTests(EngineMixin, unittest.TestCase):
    def test_vectors_come_back_with_length_one(self):
        """長さを揃えておくので、近さは掛けて足すだけで出せる。"""
        self.use(FakeOllama())
        got = embed.embed(["おかえり"])[0]
        self.assertAlmostEqual(embed.similarity(got, got), 1.0, places=5)
        self.assertAlmostEqual(got[0], 0.6, places=5)  # 3/5

    def test_long_text_is_clipped(self):
        engine = self.use(FakeOllama())
        embed.embed(["あ" * (config.EMBED_MAX_CHARS + 500)])
        self.assertEqual(len(engine.calls[0]["input"][0]), config.EMBED_MAX_CHARS)

    def test_empty_list_does_not_call_the_engine(self):
        engine = self.use(FakeOllama())
        self.assertEqual(embed.embed([]), [])
        self.assertEqual(engine.calls, [])

    def test_model_stays_loaded(self):
        """指定しないと数分で眠り、次の1回が遅くなる。"""
        engine = self.use(FakeOllama())
        embed.embed(["やあ"])
        self.assertEqual(engine.calls[0]["keep_alive"], config.EMBED_KEEP_ALIVE)

    def test_engine_not_running_is_reported_clearly(self):
        self.use(FakeOllama(error=httpx.ConnectError("refused")))
        with self.assertRaises(embed.EmbedError) as caught:
            embed.embed(["やあ"])
        self.assertIn("Ollama", str(caught.exception))

    def test_timeout_is_reported(self):
        self.use(FakeOllama(error=httpx.ReadTimeout("slow")))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["やあ"])

    def test_error_status_is_reported(self):
        self.use(FakeOllama(status=500, body={}))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["やあ"])

    def test_unreadable_answer_is_reported(self):
        self.use(FakeOllama(body={"nope": 1}))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["やあ"])

    def test_wrong_count_is_refused(self):
        """件数がずれたまま進むと、別の記憶に取り違えて結び付く。"""
        self.use(FakeOllama(body={"embeddings": [vector(1.0)]}))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["ひとつめ", "ふたつめ"])

    def test_empty_vector_is_refused(self):
        self.use(FakeOllama(body={"embeddings": [vector()]}))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["やあ"])

    def test_stale_connection_is_retried_once(self):
        engine = self.use(StaleOnce())
        self.assertEqual(len(embed.embed(["やあ"])), 1)
        self.assertEqual(len(engine.calls), 2)

    def test_engine_really_down_is_not_retried_forever(self):
        engine = self.use(FakeOllama(error=httpx.RemoteProtocolError("closed")))
        with self.assertRaises(embed.EmbedError):
            embed.embed(["やあ"])
        self.assertEqual(len(engine.calls), 2)


class MemoryFixture(EngineMixin):
    """記憶を1件置ける一時DB。継承したままだと親のテストが二重に走る。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def add_memory(self, text="しょうやは眠れないと話した。", key=None):
        cur = self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) "
            "VALUES ('episode','event',?,?,?,?,NULL,0,?)",
            (text, "2026-09-17", "2026-09-17T00:00:00Z", "2026-09-17T00:00:00Z",
             key or f"test:{text}"),
        )
        self.conn.commit()
        return cur.lastrowid


class StorageTests(MemoryFixture, unittest.TestCase):
    def test_new_memory_is_listed_as_missing(self):
        node_id = self.add_memory()
        rows = embed.missing(self.conn, 10)
        self.assertEqual([r["id"] for r in rows], [node_id])

    def test_stored_memory_is_no_longer_missing(self):
        node_id = self.add_memory()
        embed.store(self.conn, [(node_id, array.array("f", [1.0, 0.0]))])
        self.assertEqual(embed.missing(self.conn, 10), [])

    def test_limit_is_respected(self):
        for i in range(5):
            self.add_memory(text=f"記憶{i}")
        self.assertEqual(len(embed.missing(self.conn, 2)), 2)

    def test_round_trip_keeps_the_numbers(self):
        node_id = self.add_memory()
        original = array.array("f", [0.6, 0.8])
        embed.store(self.conn, [(node_id, original)])
        rows = embed.load_all(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(embed.unpack(rows[0]["vector"]).tolist(), original.tolist())

    def test_changing_the_model_makes_everything_missing_again(self):
        node_id = self.add_memory()
        embed.store(self.conn, [(node_id, array.array("f", [1.0, 0.0]))])
        original = config.EMBED_MODEL
        config.EMBED_MODEL = "別のモデル"
        self.addCleanup(setattr, config, "EMBED_MODEL", original)
        self.assertEqual([r["id"] for r in embed.missing(self.conn, 10)], [node_id])
        self.assertEqual(embed.load_all(self.conn), [])

    def test_drop_makes_it_missing_again(self):
        node_id = self.add_memory()
        embed.store(self.conn, [(node_id, array.array("f", [1.0, 0.0]))])
        embed.drop(self.conn, node_id)
        self.conn.commit()
        self.assertEqual([r["id"] for r in embed.missing(self.conn, 10)], [node_id])

    def test_forgetting_a_memory_also_drops_its_vector(self):
        """記憶が消えたのにベクトルが残ると、居ない記憶を思い出してしまう。"""
        node_id = self.add_memory()
        embed.store(self.conn, [(node_id, array.array("f", [1.0, 0.0]))])
        self.conn.execute("DELETE FROM memory_nodes WHERE id = ?", (node_id,))
        self.conn.commit()
        self.assertEqual(embed.load_all(self.conn), [])


class PeriodicJobTests(MemoryFixture, unittest.TestCase):
    """裏で貯める処理。会話を止めないこと、止まっていても平気なことを見る。"""

    def setUp(self):
        super().setUp()
        from kotoha.serve import web

        self.web = web
        self.enabled = config.EMBED_ENABLED
        config.EMBED_ENABLED = True
        self.addCleanup(setattr, config, "EMBED_ENABLED", self.enabled)

    def vectors(self):
        conn = db.connect()
        try:
            return embed.load_all(conn)
        finally:
            conn.close()

    def test_missing_vectors_are_filled_in(self):
        self.add_memory(text="しょうやは眠れないと話した。", key="a")
        self.add_memory(text="しょうやはポキ丼を食べた。", key="b")
        self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.assertEqual(len(self.vectors()), 2)

    def test_already_made_ones_are_left_alone(self):
        self.add_memory(key="a")
        engine = self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.web.run_vector_jobs()
        self.assertEqual(len(engine.calls), 1)  # 2度目は呼ばない

    def test_nothing_to_do_does_not_call_the_engine(self):
        engine = self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.assertEqual(engine.calls, [])

    def test_a_sleeping_model_is_woken_up(self):
        """放置するとモデルはGPUから降りる。次の会話1回だけ想起が効かなくなる。"""
        engine = self.use(FakeOllama())
        embed._blocked_until = float("inf")   # 眠っているとみなされている状態
        self.web.run_vector_jobs()
        self.assertEqual(len(engine.calls), 1)
        self.assertTrue(embed.available())

    def test_a_woken_model_is_not_poked_again(self):
        engine = self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.web.run_vector_jobs()
        self.assertEqual(engine.calls, [])

    def test_engine_down_is_survived_quietly(self):
        """Ollamaが止まっていても、次の巡回でやり直せばよい。"""
        self.add_memory(key="a")
        self.use(FakeOllama(error=httpx.ConnectError("refused")))
        self.web.run_vector_jobs()   # 例外が出ないこと
        self.assertEqual(self.vectors(), [])

    def test_switched_off_does_not_call_the_engine(self):
        self.add_memory(key="a")
        config.EMBED_ENABLED = False
        engine = self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.assertEqual(engine.calls, [])

    def test_one_round_is_capped(self):
        """一度に抱え込まない。既存の記憶も巡回を重ねて埋まればよい。"""
        for i in range(config.EMBED_BATCH + 3):
            self.add_memory(text=f"記憶{i}", key=f"k{i}")
        engine = self.use(FakeOllama())
        self.web.run_vector_jobs()
        self.assertEqual(len(engine.calls[0]["input"]), config.EMBED_BATCH)


class LinkTests(MemoryFixture, unittest.TestCase):
    """意味の近い記憶どうしを結ぶ。想起の「1本たどる」がずっと空振りしていた。"""

    def setUp(self):
        super().setUp()
        for name, value in (("EMBED_ENABLED", True), ("EMBED_LINK_FLOOR", 0.80),
                            ("EMBED_LINK_LIMIT", 3)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)

    def put(self, text, *values):
        """記憶と、その座標を置く。座標は長さ1にそろえておく。"""
        node_id = self.add_memory(text=text, key=text)
        total = sum(v * v for v in values) ** 0.5
        embed.store(self.conn, [(node_id, array.array("f", [v / total for v in values]))])
        return node_id

    def edges(self):
        return {(r[0], r[1]) for r in
                self.conn.execute("SELECT from_id, to_id FROM memory_edges")}

    def test_near_memories_get_connected(self):
        a = self.put("朝会がつらい", 1.0, 0.0)
        b = self.put("朝会の反応が微妙だった", 0.99, 0.14)
        embed.link_similar(self.conn)
        self.assertIn((b, a), self.edges())

    def test_far_memories_are_left_alone(self):
        self.put("朝会がつらい", 1.0, 0.0)
        self.put("ポキ丼を食べた", 0.0, 1.0)
        embed.link_similar(self.conn)
        self.assertEqual(self.edges(), set())

    def test_a_memory_is_not_linked_to_itself(self):
        node = self.put("ひとりだけ", 1.0, 0.0)
        embed.link_similar(self.conn)
        self.assertNotIn((node, node), self.edges())

    def test_only_the_closest_few_are_kept(self):
        config.EMBED_LINK_LIMIT = 2
        for i in range(5):
            self.put(f"似た記憶{i}", 1.0, i * 0.01)
        embed.link_similar(self.conn)
        counts = {}
        for a, _ in self.edges():
            counts[a] = counts.get(a, 0) + 1
        self.assertTrue(all(n <= 2 for n in counts.values()), counts)

    def test_already_seen_memories_are_not_redone(self):
        self.put("朝会がつらい", 1.0, 0.0)
        self.put("朝会の反応が微妙だった", 0.99, 0.14)
        first = embed.link_similar(self.conn)
        again = embed.link_similar(self.conn)
        self.assertTrue(first)
        self.assertEqual(again, 0)

    def test_switched_off_makes_no_edges(self):
        config.EMBED_LINK_LIMIT = 0
        self.put("朝会がつらい", 1.0, 0.0)
        self.put("朝会の反応が微妙だった", 0.99, 0.14)
        self.assertEqual(embed.link_similar(self.conn), 0)
        self.assertEqual(self.edges(), set())


if __name__ == "__main__":
    unittest.main()
