"""話しながら喋る道の検証。Geminiにはつながず、届く順番だけを見る。

通話でいちばん長い待ちは、生成が終わるまで最初の1文字も決まらないこと。
ここで確かめるのは「言い終わった文から先に渡す」ことと、**タグの途中では
切らない**こと。[MOOD: …] が声になって出ると目も当てられない。
"""

import json
import unittest

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("stream")

from kotoha.memory import db  # noqa: E402
from kotoha.serve import hub, web  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class ReadyToSayTests(unittest.TestCase):
    def test_only_finished_sentences_go_out(self):
        self.assertEqual(chat.ready_to_say("こんにちは。げんき", 0), ("こんにちは。", 6))

    def test_it_picks_up_where_it_left_off(self):
        """一度渡した文は二度渡さない。二度言うほうが、遅いより困る。"""
        said = chat.ready_to_say("こんにちは。げんき？", 0)[1]
        ready, _ = chat.ready_to_say("こんにちは。げんき？ そっちは？", said)
        self.assertEqual(ready.strip(), "そっちは？")

    def test_an_unfinished_tag_is_held_back(self):
        ready, _ = chat.ready_to_say("ただいま。[MOOD: うれ", 0)
        self.assertEqual(ready, "ただいま。")

    def test_a_finished_tag_never_becomes_a_voice(self):
        ready, _ = chat.ready_to_say("ただいま。[MOOD: うれしい] おかえり。", 0)
        self.assertNotIn("MOOD", ready)
        self.assertIn("ただいま。", ready)

    def test_nothing_to_say_yet(self):
        self.assertEqual(chat.ready_to_say("まだ途中", 0), ("", 0))


class StreamTurnTests(DbCase):
    def speaks(self, *pieces):
        original = chat.llm.stream
        chat.llm.stream = lambda prompt, max_tokens=None: iter(pieces)
        self.addCleanup(setattr, chat.llm, "stream", original)

    def run_stream(self, text="ただいま"):
        return list(chat.stream_turn(self.conn, text))

    def test_sentences_arrive_before_the_end(self):
        self.speaks("ただいま。", "おかえり", "。またね。")
        parts = self.run_stream()
        self.assertEqual([p["say"] for p in parts if "say" in p],
                         ["ただいま。", "おかえり。またね。"])
        self.assertIn("done", parts[-1])

    def test_the_whole_thing_is_stored_without_tags(self):
        self.speaks("ただいま。", "[MOOD: 機嫌がいい]")
        done = self.run_stream()[-1]["done"]
        self.assertEqual(done.reply, "ただいま。")
        self.assertEqual(db.get_state(self.conn, "mood"), "機嫌がいい")
        stored = self.conn.execute(
            "SELECT text FROM messages WHERE role = 'assistant'").fetchone()["text"]
        self.assertEqual(stored, "ただいま。")

    def test_what_was_not_said_yet_is_handed_over_at_the_end(self):
        """最後の文が言い終わらないまま終わることがある。取りこぼさない。"""
        self.speaks("ただいま。", "ごはんは")
        parts = self.run_stream()
        self.assertEqual([p["say"] for p in parts if "say" in p], ["ただいま。"])
        self.assertEqual(parts[-1]["rest"], "ごはんは")

    def test_a_dead_stream_falls_back_to_the_ordinary_road(self):
        """1文字も来ていないなら、投げ直しのあるふつうの道へ落ちる。"""
        def broken(prompt, max_tokens=None):
            raise chat.llm.LLMError("通信失敗", retryable=True)
            yield  # pragma: no cover
        original_stream, original_chat = chat.llm.stream, chat.llm.chat
        chat.llm.stream = broken
        chat.llm.chat = lambda prompt, max_tokens=None: "おかえり。"
        self.addCleanup(setattr, chat.llm, "stream", original_stream)
        self.addCleanup(setattr, chat.llm, "chat", original_chat)
        self.assertEqual(self.run_stream()[-1]["done"].reply, "おかえり。")

    def test_what_was_already_said_is_kept_when_it_breaks_midway(self):
        """言いかけたぶんは捨てない。取り消せないものを無かったことにしない。"""
        def half(prompt, max_tokens=None):
            yield "ただいま。"
            raise chat.llm.LLMError("通信失敗", retryable=True)
        original = chat.llm.stream
        chat.llm.stream = half
        self.addCleanup(setattr, chat.llm, "stream", original)
        self.assertEqual(self.run_stream()[-1]["done"].reply, "ただいま。")


class StreamApiTests(DbCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        hub.reset()
        self.addCleanup(hub.reset)
        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}
        original = chat.llm.stream
        chat.llm.stream = lambda prompt, max_tokens=None: iter(["ただいま。", "おかえり。"])
        self.addCleanup(setattr, chat.llm, "stream", original)

    def lines(self):
        response = self.client.post("/api/chat/stream",
                                    json={"text": "ただいま", "vessel": "web"},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return [json.loads(line) for line in response.text.splitlines() if line.strip()]

    def test_it_comes_back_one_line_at_a_time(self):
        rows = self.lines()
        self.assertEqual([r["say"] for r in rows if "say" in r], ["ただいま。", "おかえり。"])
        done = rows[-1]["done"]
        self.assertEqual(done["reply"], "ただいま。おかえり。")
        self.assertEqual(done["mode"], "slow")
        self.assertTrue(done["last_id"])

    def test_the_password_is_still_asked_for(self):
        self.assertEqual(
            self.client.post("/api/chat/stream", json={"text": "ただいま"}).status_code, 401)
