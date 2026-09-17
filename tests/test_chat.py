"""プロンプトに渡す時刻・経過・様子の検証。一時DBだけを使い、LLMは呼ばない。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha chat ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.memory import db  # noqa: E402
from kotoha.talk import chat  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def ago(**delta):
    moment = datetime.now(timezone.utc) - timedelta(**delta)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class ElapsedPhraseTests(unittest.TestCase):
    def test_missing_or_broken_record(self):
        self.assertEqual(chat.elapsed_phrase(None), "初めての会話")
        self.assertEqual(chat.elapsed_phrase(""), "初めての会話")
        self.assertEqual(chat.elapsed_phrase("2026-09-17"), "不明")

    def test_scales_from_seconds_to_years(self):
        cases = [
            (dict(seconds=5), "たった今"),
            (dict(minutes=3), "3分前"),
            (dict(hours=5), "5時間前"),
            (dict(hours=30), "昨日"),
            (dict(days=3), "3日前"),
            (dict(days=10), "1週間前"),
            (dict(days=60), "2か月前"),
            (dict(days=400), "1年以上前"),
        ]
        for delta, expected in cases:
            with self.subTest(**delta):
                self.assertEqual(chat.elapsed_phrase(ago(**delta)), expected)

    def test_utc_record_is_not_read_as_a_timezone_offset(self):
        """UTCの記録をそのまま渡すと時差ぶんの間隔に見える退行を防ぐ。"""
        self.assertEqual(chat.elapsed_phrase(ago(seconds=1)), "たった今")


class SituationTests(unittest.TestCase):
    def test_every_hour_has_a_situation(self):
        for hour in range(24):
            with self.subTest(hour=hour):
                self.assertTrue(chat.situation(hour))

    def test_boundaries_match_the_avatar_groups(self):
        """static/app.js の getAvatarGroup() と区切りを揃える。"""
        groups = {hour: chat.situation(hour) for hour in range(24)}
        self.assertEqual(groups[6], groups[10])
        self.assertNotEqual(groups[5], groups[6])
        self.assertEqual(groups[21], groups[1])  # 夜更かしは日付をまたぐ
        self.assertNotEqual(groups[1], groups[2])
        self.assertEqual(groups[2], groups[5])


class TimeBlockTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def time_lines(self):
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        return [
            line for line in prompt.split("\n")
            if line.startswith(("現在:", "前回の会話:", "今のことは:"))
        ]

    def test_prompt_carries_all_three_lines(self):
        self.assertEqual(len(self.time_lines()), 3)

    def test_weekday_is_japanese(self):
        current = self.time_lines()[0]
        self.assertIn(chat.WEEKDAYS[datetime.now().weekday()] + "曜日", current)

    def test_first_conversation_then_just_now(self):
        self.assertIn("初めての会話", self.time_lines()[1])
        db.set_state(self.conn, "last_conversation_at", db.now_utc())
        self.conn.commit()
        self.assertIn("たった今", self.time_lines()[1])


class TagStrippingTests(unittest.TestCase):
    """内部制御タグが本文に残るとユーザーに見えてしまう。"""

    def test_normal_tags_are_removed(self):
        text, ids = chat.parse_used_ids("おかえりー [USED: 1,2] [MOOD: 機嫌がいい]")
        text, mood = chat.parse_mood(text)
        self.assertEqual(text, "おかえりー")
        self.assertEqual(ids, [1, 2])
        self.assertEqual(mood, "機嫌がいい")

    def test_missing_closing_bracket_is_still_stripped(self):
        text, ids = chat.parse_used_ids("ねむい [USED: 3")
        self.assertEqual(text, "ねむい")
        self.assertEqual(ids, [3])
        text, mood = chat.parse_mood("ねむい [MOOD: 眠い")
        self.assertEqual(text, "ねむい")
        self.assertEqual(mood, "眠い")

    def test_fullwidth_brackets_are_still_stripped(self):
        text, mood = chat.parse_mood("はいはい ［MOOD：疲れ気味］")
        self.assertEqual(text, "はいはい")
        self.assertEqual(mood, "疲れ気味")

    def test_unknown_label_is_dropped_but_tag_removed(self):
        text, mood = chat.parse_mood("しらない [MOOD: ごきげん斜め]")
        self.assertEqual(text, "しらない")
        self.assertIsNone(mood)

    def test_text_without_tags_is_untouched(self):
        self.assertEqual(chat.parse_used_ids("タグなしの返事"), ("タグなしの返事", []))
        self.assertEqual(chat.parse_mood("タグなしの返事"), ("タグなしの返事", None))

    def test_multiline_reply_keeps_its_body(self):
        text, mood = chat.parse_mood("一行目\n二行目 [MOOD: ふつう]")
        self.assertEqual(text, "一行目\n二行目")
        self.assertEqual(mood, "ふつう")


class MoodTests(unittest.TestCase):
    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def remember(self, label, at=None):
        db.set_state(self.conn, "mood", label)
        db.set_state(self.conn, "mood_at", at or db.now_utc())
        self.conn.commit()

    def test_defaults_to_neutral(self):
        self.assertEqual(chat.current_mood(self.conn), chat.DEFAULT_MOOD)

    def test_remembers_a_recent_mood(self):
        self.remember("すねている")
        self.assertEqual(chat.current_mood(self.conn), "すねている")

    def test_old_mood_is_not_carried_over(self):
        self.remember("すねている", ago(hours=7))
        self.assertEqual(chat.current_mood(self.conn), chat.DEFAULT_MOOD)

    def test_unknown_stored_label_falls_back(self):
        self.remember("ごきげん斜め")
        self.assertEqual(chat.current_mood(self.conn), chat.DEFAULT_MOOD)

    def test_prompt_carries_the_mood(self):
        self.remember("眠い")
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        line = [l for l in prompt.split("\n") if l.startswith("今の機嫌:")]
        self.assertEqual(len(line), 1)
        self.assertIn("眠い", line[0])
        self.assertIn(chat.MOODS["眠い"], line[0])

    def test_labels_match_the_prompt_rules(self):
        """MOODS と fixed_rules.txt のラベル一覧がずれると機嫌が反映されない。"""
        rules = (config.PROMPTS_DIR / "fixed_rules.txt").read_text(encoding="utf-8")
        for label in chat.MOODS:
            with self.subTest(label=label):
                self.assertIn(label, rules)


class TurnWiringTests(unittest.TestCase):
    """1ターン通したときに、タグが隠れて機嫌が残ることを確かめる。"""

    def setUp(self):
        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        self.conn = db.connect()
        self.addCleanup(self.conn.close)
        db.init(self.conn)

    def reply_with(self, raw):
        original = chat.llm.chat
        chat.llm.chat = lambda prompt, max_tokens=None: raw
        self.addCleanup(setattr, chat.llm, "chat", original)

    def test_tags_are_hidden_and_mood_is_stored(self):
        self.reply_with("おかえりー [USED: ] [MOOD: 機嫌がいい]")
        reply, _ = chat.run_turn(self.conn, "ただいま")
        self.assertEqual(reply, "おかえりー")
        self.assertEqual(db.get_state(self.conn, "mood"), "機嫌がいい")
        stored = self.conn.execute(
            "SELECT text FROM messages WHERE role = 'assistant'"
        ).fetchone()["text"]
        self.assertEqual(stored, "おかえりー")  # 履歴にもタグを残さない

    def test_unknown_label_keeps_the_previous_mood(self):
        db.set_state(self.conn, "mood", "眠い")
        db.set_state(self.conn, "mood_at", db.now_utc())
        self.conn.commit()
        self.reply_with("ふーん [MOOD: ごきげん斜め]")
        reply, _ = chat.run_turn(self.conn, "ねえ")
        self.assertEqual(reply, "ふーん")
        self.assertEqual(db.get_state(self.conn, "mood"), "眠い")


if __name__ == "__main__":
    unittest.main()
