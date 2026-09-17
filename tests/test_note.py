"""ことは自身のことを書き込む note コマンドの検証。APIは呼ばない。"""

import unittest
from unittest.mock import patch

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("note")

from kotoha import cli  # noqa: E402
from kotoha.memory import db  # noqa: E402
from kotoha.talk import chat, presence  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class NoteTests(DbCase):
    def setUp(self):
        super().setUp()
    def note(self, text):
        with patch("builtins.print"):
            cli._note(self.conn, [text])

    def rows(self):
        return self.conn.execute(
            "SELECT text, layer, kind, pinned, expires_at FROM memory_nodes"
        ).fetchall()

    def test_it_is_written_as_a_pinned_fact(self):
        """自分が何者かは、想起で引き当てるものではなく常に持っているもの。"""
        self.note("ことはは意味の近い記憶を思い出せるようになった。")
        row = self.rows()[0]
        self.assertEqual((row["layer"], row["kind"], row["pinned"]), ("semantic", "fact", 1))

    def test_it_never_expires(self):
        self.note("ことはは読み上げが速くなった。")
        self.assertIsNone(self.rows()[0]["expires_at"])

    def test_it_is_tagged_so_tags_can_find_it_too(self):
        self.note("ことはは読み上げが速くなった。")
        tags = {r[0] for r in self.conn.execute("SELECT tag FROM memory_tags")}
        self.assertEqual(tags, {"ことは", "更新"})

    def test_two_different_notes_in_the_same_second_both_survive(self):
        """鍵を時刻で作ると、同じ秒に書いた片方が黙って消える。"""
        self.note("ことはは意味で思い出せるようになった。")
        self.note("ことはは読み上げが速くなった。")
        self.assertEqual(len(self.rows()), 2)

    def test_the_same_note_twice_is_stored_once(self):
        self.note("ことはは読み上げが速くなった。")
        self.note("ことはは読み上げが速くなった。")
        self.assertEqual(len(self.rows()), 1)

    def test_an_empty_note_is_refused(self):
        self.note("   ")
        self.assertEqual(self.rows(), [])

    def test_an_overlong_note_is_refused(self):
        self.note("あ" * 201)
        self.assertEqual(self.rows(), [])


class SelfStateTests(DbCase):
    """自分がどれだけ覚えているかを、プロンプトに持たせる。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, presence, "describe", presence.describe)
        presence.describe = lambda conn: ""

    def build(self, fast=False):
        return chat.build_prompt(self.conn, "ただいま", [], [], [], fast=fast)

    def test_the_prompt_says_how_much_is_remembered(self):
        with patch("builtins.print"):
            cli._note(self.conn, ["ことはは読み上げが速くなった。"])
        self.assertIn("覚えていること: 1件", self.build())

    def test_expired_memories_are_not_counted(self):
        self.conn.execute(
            "INSERT INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) "
            "VALUES ('episode','event','むかしの話','2020-01-01','2020-01-01T00:00:00Z',"
            "'2020-01-01T00:00:00Z','2020-01-02T00:00:00Z',0,'old')"
        )
        self.conn.commit()
        self.assertIn("覚えていること: 0件", self.build())

    def test_the_machine_line_appears_when_there_is_something_to_say(self):
        presence.describe = lambda conn: "PCは6時間つけっぱなし"
        self.assertIn("相手の様子: PCは6時間つけっぱなし", self.build())

    def test_nothing_is_added_when_the_machine_says_nothing(self):
        self.assertNotIn("相手の様子", self.build())

    def test_the_short_path_stays_short(self):
        """Fastは保護記憶だけを積む道なので、自分語りの行も足さない。"""
        presence.describe = lambda conn: "PCは6時間つけっぱなし"
        prompt = self.build(fast=True)
        self.assertNotIn("覚えていること", prompt)
        self.assertNotIn("相手の様子", prompt)


if __name__ == "__main__":
    unittest.main()
