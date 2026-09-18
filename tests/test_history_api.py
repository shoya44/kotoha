"""開いたままの画面が、ことはのほうからの発言に気づけること。

画面は起動時にしか履歴を読んでいなかったので、頼まれごとが鳴っても
そのままでは何も出なかった。`after` で続きだけを取りに行く。
"""

import unittest

from kotoha import config
from tests.support import use_temp_db

_TMP = use_temp_db("history")

from kotoha.memory import db  # noqa: E402
from kotoha.serve import web  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def say(self, role, text):
        db.insert_message(self.conn, db.next_turn_id(self.conn), role, text)
        self.conn.commit()

    def history(self, **params):
        response = self.client.get("/api/history", params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_the_whole_history_comes_back_oldest_first(self):
        self.say("user", "ひとつめ")
        self.say("assistant", "ふたつめ")
        rows = self.history()
        self.assertEqual([r["text"] for r in rows], ["ひとつめ", "ふたつめ"])

    def test_every_row_carries_its_number(self):
        """番号が無いと、画面はどこまで並べたかを覚えられない。"""
        self.say("user", "ひとつめ")
        self.assertIn("id", self.history()[0])

    def test_after_returns_only_what_is_newer(self):
        self.say("user", "ひとつめ")
        first = self.history()[0]["id"]
        self.say("assistant", "あとから言ったこと")
        rows = self.history(after=first)
        self.assertEqual([r["text"] for r in rows], ["あとから言ったこと"])

    def test_nothing_new_is_an_empty_answer(self):
        self.say("user", "ひとつめ")
        last = self.history()[-1]["id"]
        self.assertEqual(self.history(after=last), [])

    def test_the_newer_ones_come_oldest_first(self):
        """並べる順が逆だと、会話が裏返しになる。"""
        self.say("user", "ひとつめ")
        first = self.history()[0]["id"]
        for text in ("ふたつめ", "みっつめ", "よっつめ"):
            self.say("assistant", text)
        rows = self.history(after=first)
        self.assertEqual([r["text"] for r in rows], ["ふたつめ", "みっつめ", "よっつめ"])


class ChatCursorTests(unittest.TestCase):
    """送った直後の往復を、次の見に行きで二度拾わないこと。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()

        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

        from kotoha.talk import chat

        self.addCleanup(setattr, chat, "run_turn", chat.run_turn)

        def answer(conn, text):
            db.insert_message(conn, db.next_turn_id(conn), "user", text)
            db.insert_message(conn, db.next_turn_id(conn), "assistant", "へんじ")
            conn.commit()
            return chat.Turn(reply="へんじ", mode="fast", kept=())

        chat.run_turn = answer

    def test_the_answer_says_how_far_the_screen_can_skip(self):
        sent = self.client.post("/api/chat", json={"text": "こんにちは"},
                                headers=self.headers).json()
        self.assertIn("last_id", sent)

        rest = self.client.get("/api/history", params={"after": sent["last_id"]},
                               headers=self.headers)
        self.assertEqual(rest.json(), [], "自分が送ったばかりの往復を拾うと二重に並ぶ")
