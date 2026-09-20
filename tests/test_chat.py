"""プロンプトに渡す時刻・経過・様子の検証。一時DBだけを使い、LLMは呼ばない。"""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("chat")

from kotoha.memory import db, remind  # noqa: E402
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


class TimeBlockTests(DbCase):
    def setUp(self):
        super().setUp()
    def time_lines(self):
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        return [
            line for line in prompt.split("\n")
            if line.startswith(("現在:", "前回の会話:", "今のことは:"))
        ]

    def test_prompt_carries_all_three_lines(self):
        self.assertEqual(len(self.time_lines()), 3)

    def test_the_prompt_says_what_kind_of_day_it_is(self):
        """今日が仕事か休みかを、ことはが毎回知っているようにする。

        人格の側に書き置きにすると、祝日にも「仕事いってらっしゃい」と
        言ってしまう。日ごとに変わるものは毎回渡す。
        """
        prompt = chat.build_prompt(self.conn, "やっほー", [], [], [])
        today = [line for line in prompt.split("\n") if line.startswith("今日:")]
        self.assertEqual(len(today), 1, prompt[:200])
        self.assertTrue(today[0].endswith(("仕事", "休み", "）", "出社")))

    def test_weekday_is_japanese(self):
        current = self.time_lines()[0]
        self.assertIn(chat.WEEKDAYS[datetime.now().weekday()] + "曜日", current)

    def test_first_conversation_then_just_now(self):
        self.assertIn("初めての会話", self.time_lines()[1])
        db.set_state(self.conn, "last_conversation_at", db.now_utc())
        self.conn.commit()
        self.assertIn("たった今", self.time_lines()[1])


class MemoryLineTests(unittest.TestCase):
    """記憶1件の書き方。ラベルが長いとプロンプトを無駄に食う。"""

    def row(self, **over):
        base = {
            "id": 12, "layer": "semantic", "kind": "fact",
            "occurred_at": "2026-03-01", "confirmed_at": "2026-09-17T00:00:00Z",
            "text": "ユーザーはフルリモートで働いている",
        }
        base.update(over)
        return base

    def test_layer_is_left_out(self):
        """層は種類から決まるので書かない。"""
        line = chat._mem_line(self.row())
        self.assertIn("[fact]", line)
        self.assertNotIn("semantic", line)

    def test_year_is_left_out_for_this_year(self):
        year = datetime.now().year
        line = chat._mem_line(self.row(confirmed_at=f"{year}-09-17T00:00:00Z"))
        self.assertIn("[09-17]", line)
        self.assertNotIn(str(year), line)

    def test_year_is_kept_for_other_years(self):
        line = chat._mem_line(self.row(confirmed_at="2020-09-17T00:00:00Z"))
        self.assertIn("[2020-09-17]", line)

    def test_id_stays_readable(self):
        """[USED:] で返してもらうので、idは形を変えない。"""
        self.assertIn("[id:12]", chat._mem_line(self.row()))

    def test_episode_uses_the_date_it_happened(self):
        year = datetime.now().year
        line = chat._mem_line(self.row(
            layer="episode", kind="event",
            occurred_at=f"{year}-03-01", confirmed_at=f"{year}-09-17T00:00:00Z",
        ))
        self.assertIn("[03-01]", line)
        self.assertIn("[event]", line)

    def test_label_is_shorter_than_before(self):
        """以前は - [id:12][2026-09-17][semantic/fact] で36字あった。"""
        line = chat._mem_line(self.row(confirmed_at=f"{datetime.now().year}-09-17T00:00:00Z"))
        label = line.split("] ", 2)[0] + "] "
        self.assertLess(len(line) - len(self.row()["text"]), 25)
        self.assertTrue(label.startswith("- [id:12]"))


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


class MoodTests(DbCase):
    def setUp(self):
        super().setUp()
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


class TagSweepTests(unittest.TestCase):
    """崩れたタグを画面に出さない。実際に [REMIND: ] が出たことがある。"""

    def test_an_empty_errand_tag_is_wiped(self):
        self.assertEqual(chat.strip_tags("スマホ見てるー。[REMIND: ]"), "スマホ見てるー。")

    def test_a_tag_with_nothing_at_all_is_wiped(self):
        self.assertEqual(chat.strip_tags("うん。[REMIND:]"), "うん。")

    def test_a_broken_tag_is_wiped(self):
        """閉じ括弧が無くても、全角でも残さない。"""
        self.assertEqual(chat.strip_tags("はい ［REMIND：こわれた"), "はい")
        self.assertEqual(chat.strip_tags("ねむい [MOOD: 眠い"), "ねむい")

    def test_every_tag_is_wiped(self):
        for name in chat.TAG_NAMES:
            with self.subTest(name=name):
                self.assertEqual(chat.strip_tags(f"ふむ。[{name}: なにか]"), "ふむ。")

    def test_plain_text_is_left_alone(self):
        self.assertEqual(chat.strip_tags("おはよー。今日は寒いね"), "おはよー。今日は寒いね")

    def test_brackets_that_are_not_tags_stay(self):
        """タグの名前でなければ、括弧はそのまま残す。"""
        self.assertEqual(chat.strip_tags("[明日] は雨だって"), "[明日] は雨だって")


class TurnWiringTests(DbCase):
    """1ターン通したときに、タグが隠れて機嫌が残ることを確かめる。"""

    def setUp(self):
        super().setUp()
    def reply_with(self, raw):
        original = chat.llm.chat
        chat.llm.chat = lambda prompt, max_tokens=None: raw
        self.addCleanup(setattr, chat.llm, "chat", original)

    def test_tags_are_hidden_and_mood_is_stored(self):
        self.reply_with("おかえりー [USED: ] [MOOD: 機嫌がいい]")
        reply = chat.run_turn(self.conn, "ただいま").reply
        self.assertEqual(reply, "おかえりー")
        self.assertEqual(db.get_state(self.conn, "mood"), "機嫌がいい")
        stored = self.conn.execute(
            "SELECT text FROM messages WHERE role = 'assistant'"
        ).fetchone()["text"]
        self.assertEqual(stored, "おかえりー")  # 履歴にもタグを残さない

    def test_no_mood_tag_keeps_the_mood(self):
        """機嫌は変わったときだけ書かせる。来なくても、前のまま続く。"""
        db.set_state(self.conn, db.MOOD, "眠い")
        db.set_state(self.conn, db.MOOD_AT, db.now_utc())
        self.conn.commit()
        self.reply_with("ふーん")
        chat.run_turn(self.conn, "ねえ")
        self.assertEqual(db.get_state(self.conn, db.MOOD), "眠い")

    def test_talking_keeps_the_mood_from_fading(self):
        """6時間の薄れは、黙っている時間に効かせたい。話していれば進まない。"""
        db.set_state(self.conn, db.MOOD, "眠い")
        db.set_state(self.conn, db.MOOD_AT, "2020-01-01T00:00:00Z")
        self.conn.commit()
        self.reply_with("ふーん")
        chat.run_turn(self.conn, "ねえ")
        self.assertEqual(chat.current_mood(self.conn), "眠い")

    def test_a_broken_tag_never_reaches_the_screen(self):
        """23:16に「スマホ見てるー。[REMIND: ]」が出た。同じ形を通しで確かめる。"""
        self.reply_with("スマホ見てるー。[REMIND: ] [MOOD: ふつう]")
        reply = chat.run_turn(self.conn, "おすー、今何してるの？").reply
        self.assertEqual(reply, "スマホ見てるー。")
        stored = self.conn.execute(
            "SELECT text FROM messages WHERE role = 'assistant'"
        ).fetchone()["text"]
        self.assertEqual(stored, "スマホ見てるー。")
        self.assertEqual(remind.pending(self.conn), [])   # 妙な予定も残さない

    def test_unknown_label_keeps_the_previous_mood(self):
        db.set_state(self.conn, "mood", "眠い")
        db.set_state(self.conn, "mood_at", db.now_utc())
        self.conn.commit()
        self.reply_with("ふーん [MOOD: ごきげん斜め]")
        reply = chat.run_turn(self.conn, "ねえ").reply
        self.assertEqual(reply, "ふーん")
        self.assertEqual(db.get_state(self.conn, "mood"), "眠い")




class ResendTests(DbCase):
    """送り直しは、同じ往復の空いている枠へ。同じ発言を2行並べない。"""

    def reply_with(self, raw):
        original = chat.llm.chat
        chat.llm.chat = lambda prompt, max_tokens=None: raw
        self.addCleanup(setattr, chat.llm, "chat", original)

    def fails(self):
        original = chat.llm.chat

        def broken(prompt, max_tokens=None):
            raise chat.llm.LLMError("通信失敗", retryable=True)
        chat.llm.chat = broken
        self.addCleanup(setattr, chat.llm, "chat", original)

    def rows(self):
        return self.conn.execute(
            "SELECT turn_id, role, text FROM messages ORDER BY id").fetchall()

    def test_the_same_words_sent_again_fill_the_empty_seat(self):
        self.fails()
        with self.assertRaises(chat.llm.LLMError):
            chat.run_turn(self.conn, "ただいま")
        self.reply_with("おかえり")
        chat.run_turn(self.conn, "ただいま")
        rows = self.rows()
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["turn_id"], rows[1]["turn_id"])

    def test_different_words_start_their_own_turn(self):
        """言い直したのが別の言葉なら、言いかけたぶんはそのまま残る。"""
        self.fails()
        with self.assertRaises(chat.llm.LLMError):
            chat.run_turn(self.conn, "ただいま")
        self.reply_with("おかえり")
        chat.run_turn(self.conn, "やっぱりおやすみ")
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertNotEqual(rows[0]["turn_id"], rows[1]["turn_id"])

    def test_saying_the_same_thing_twice_on_purpose_is_two_turns(self):
        """1回目に返事があるなら、それは送り直しではない。"""
        self.reply_with("うん")
        chat.run_turn(self.conn, "ねえ")
        chat.run_turn(self.conn, "ねえ")
        turns = {r["turn_id"] for r in self.rows()}
        self.assertEqual(len(turns), 2)
class PersonalPromptTests(unittest.TestCase):
    """呼び名や人となりは、git の外に置いた側が勝つ。"""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="kotoha prompts "))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.addCleanup(setattr, config, "PERSONAL_PROMPTS_DIR", config.PERSONAL_PROMPTS_DIR)
        config.PERSONAL_PROMPTS_DIR = self.folder

    def test_the_shipped_template_is_used_when_nothing_is_placed(self):
        self.assertIn("ことは", chat._read("persona.txt"))

    def test_what_is_placed_outside_git_wins(self):
        (self.folder / "persona.txt").write_text("名前：てすと。", encoding="utf-8")
        self.assertEqual(chat._read("persona.txt"), "名前：てすと。")

    def test_the_shipped_template_carries_no_name(self):
        """配るほうに呼び名を残さない。**公開されたまま気づけない。**"""
        shipped = (config.PROMPTS_DIR / "persona.txt").read_text(encoding="utf-8")
        self.assertIn("あなた", shipped)


if __name__ == "__main__":
    unittest.main()
