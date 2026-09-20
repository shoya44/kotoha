"""画面から直せるものの検証。本番の .env とプロンプトには触れない。"""

import tempfile
import unittest
from pathlib import Path

from kotoha import config

_TMP = tempfile.TemporaryDirectory(prefix="kotoha admin ")
_BASE = Path(_TMP.name)
config.DB_PATH = _BASE / "test.sqlite3"

from kotoha.serve import admin  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class AdminTestCase(unittest.TestCase):
    """config の指す先を一時ディレクトリへ向けてから動かす。"""

    def setUp(self):
        self.prompts = _BASE / "prompts"
        self.prompts.mkdir(exist_ok=True)
        for item in self.prompts.glob("*"):
            item.unlink()
        # 自分ぶんの置き場（data/prompts の代わり）。実物を覗かないように、
        # ここも必ず一時の空っぽへ向ける。
        self.personal = _BASE / "personal_prompts"
        self.personal.mkdir(exist_ok=True)
        for item in self.personal.glob("*"):
            item.unlink()
        self.env = _BASE / ".env"
        self.env.write_text("KOTOHA_TEMPERATURE=0.7\n", encoding="utf-8")
        (_BASE / ".env.example").write_text(
            "KOTOHA_TEMPERATURE=0.7\nKOTOHA_RECENT_TURNS=5\nKOTOHA_VOICE_STYLE_ID=1878365376\n",
            encoding="utf-8",
        )

        for name, value in (("BASE_DIR", _BASE), ("PROMPTS_DIR", self.prompts),
                            ("PERSONAL_PROMPTS_DIR", self.personal)):
            original = getattr(config, name)
            setattr(config, name, value)
            self.addCleanup(setattr, config, name, original)
        # 読み直すと実物の .env を見てしまうので、ここでは止めておく
        original_reload = admin.reload_config
        admin.reload_config = lambda: None
        self.addCleanup(setattr, admin, "reload_config", original_reload)


class PromptTests(AdminTestCase):
    def write(self, name, text):
        (self.prompts / f"{name}.txt").write_text(text, encoding="utf-8")

    def test_reads_what_is_there(self):
        self.write("persona", "名前：ことは。")
        self.assertEqual(admin.read_prompt("persona"), "名前：ことは。")

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(admin.read_prompt("persona"), "")

    def test_unknown_name_is_refused(self):
        with self.assertRaises(admin.AdminError):
            admin.read_prompt("../../.env")

    def test_saves_and_keeps_one_backup(self):
        self.write("persona", "はじめの文")
        admin.write_prompt("persona", "あとの文")
        self.assertEqual(admin.read_prompt("persona").strip(), "あとの文")
        self.assertEqual(admin.prompt_backup("persona").read_text(encoding="utf-8"), "はじめの文")

    def test_revert_brings_the_previous_text_back(self):
        self.write("persona", "はじめの文")
        admin.write_prompt("persona", "あとの文")
        self.assertEqual(admin.revert_prompt("persona").strip(), "はじめの文")
        self.assertEqual(admin.read_prompt("persona").strip(), "はじめの文")

    def test_revert_twice_returns_to_the_newer_text(self):
        """戻しすぎたときに、もう一度押せば戻れる。"""
        self.write("persona", "はじめの文")
        admin.write_prompt("persona", "あとの文")
        admin.revert_prompt("persona")
        self.assertEqual(admin.revert_prompt("persona").strip(), "あとの文")

    def test_reading_back_what_was_saved_is_stable(self):
        """画面で開いて保存し直しても、空行が増えていかないこと。"""
        admin.write_prompt("persona", "名前：ことは。")
        once = admin.read_prompt("persona")
        admin.write_prompt("persona", once)
        self.assertEqual(admin.read_prompt("persona"), once)

    def test_revert_without_backup_is_refused(self):
        self.write("persona", "はじめの文")
        with self.assertRaises(admin.AdminError):
            admin.revert_prompt("persona")

    def test_empty_save_is_refused(self):
        self.write("persona", "はじめの文")
        with self.assertRaises(admin.AdminError):
            admin.write_prompt("persona", "   \n  ")
        self.assertEqual(admin.read_prompt("persona"), "はじめの文")  # 元のまま

    def test_too_long_save_is_refused(self):
        with self.assertRaises(admin.AdminError):
            admin.write_prompt("persona", "あ" * (admin.MAX_PROMPT_CHARS + 1))


class PersonalPromptTests(AdminTestCase):
    """data/prompts がひな形より優先される。画面は効いているほうと揃う。

    会話側（talk/chat.py の _read）が見る順と、画面が直す先がずれていると、
    保存は成功するのに発言は変わらない、という静かな失敗になる。
    """

    def place(self, folder, name, text):
        (folder / f"{name}.txt").write_text(text, encoding="utf-8")

    def test_reads_the_one_in_effect(self):
        self.place(self.prompts, "persona", "ひながた")
        self.place(self.personal, "persona", "じぶん")
        self.assertEqual(admin.read_prompt("persona"), "じぶん")

    def test_saving_goes_to_the_one_in_effect(self):
        self.place(self.prompts, "persona", "ひながた")
        self.place(self.personal, "persona", "じぶん")
        admin.write_prompt("persona", "なおした")
        saved = (self.personal / "persona.txt").read_text(encoding="utf-8")
        self.assertEqual(saved.strip(), "なおした")
        # ひながたは触らない。配るときの1枚のまま残る。
        self.assertEqual((self.prompts / "persona.txt").read_text(encoding="utf-8"), "ひながた")

    def test_backup_lives_beside_the_one_in_effect(self):
        self.place(self.personal, "persona", "じぶん")
        admin.write_prompt("persona", "なおした")
        backup = (self.personal / "persona.bak").read_text(encoding="utf-8")
        self.assertEqual(backup, "じぶん")

    def test_revert_swaps_the_one_in_effect(self):
        self.place(self.personal, "persona", "じぶん")
        admin.write_prompt("persona", "なおした")
        self.assertEqual(admin.revert_prompt("persona").strip(), "じぶん")

    def test_without_personal_the_template_is_edited(self):
        """置いていなければ今までどおり。ひながたの側が直る。"""
        self.place(self.prompts, "persona", "ひながた")
        admin.write_prompt("persona", "なおした")
        saved = (self.prompts / "persona.txt").read_text(encoding="utf-8")
        self.assertEqual(saved.strip(), "なおした")


class SettingsTests(AdminTestCase):
    def values(self):
        return {item["key"]: item["value"] for item in admin.read_settings()}

    def test_reads_env_over_example(self):
        self.env.write_text("KOTOHA_TEMPERATURE=0.3\n", encoding="utf-8")
        got = self.values()
        self.assertEqual(got["KOTOHA_TEMPERATURE"], "0.3")   # .env 側
        self.assertEqual(got["KOTOHA_RECENT_TURNS"], "5")     # .env になければ既定値

    def test_replaces_the_existing_line(self):
        admin.write_settings({"KOTOHA_TEMPERATURE": "0.4"})
        body = self.env.read_text(encoding="utf-8")
        self.assertIn("KOTOHA_TEMPERATURE=0.4", body)
        self.assertEqual(body.count("KOTOHA_TEMPERATURE"), 1)  # 二重に書かない

    def test_a_key_written_twice_is_changed_in_both_places(self):
        """読むほうは後ろの行を採る。前だけ直すと、画面で直したのに効かない。"""
        self.env.write_text("KOTOHA_TEMPERATURE=0.3\nKOTOHA_TEMPERATURE=0.7\n",
                            encoding="utf-8")
        admin.write_settings({"KOTOHA_TEMPERATURE": "0.4"})
        body = self.env.read_text(encoding="utf-8")
        self.assertEqual(body.count("KOTOHA_TEMPERATURE=0.4"), 2)
        self.assertEqual(self.values()["KOTOHA_TEMPERATURE"], "0.4")

    def test_appends_a_key_that_was_not_written_yet(self):
        admin.write_settings({"KOTOHA_RECENT_TURNS": "8"})
        self.assertIn("KOTOHA_RECENT_TURNS=8", self.env.read_text(encoding="utf-8"))
        self.assertEqual(self.values()["KOTOHA_RECENT_TURNS"], "8")

    def test_keeps_unrelated_lines(self):
        self.env.write_text(
            "# だいじなメモ\nGEMINI_API_KEY=secret\nKOTOHA_TEMPERATURE=0.7\n", encoding="utf-8"
        )
        admin.write_settings({"KOTOHA_TEMPERATURE": "0.9"})
        body = self.env.read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY=secret", body)
        self.assertIn("# だいじなメモ", body)

    def test_out_of_range_is_refused(self):
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"KOTOHA_TEMPERATURE": "5"})
        self.assertEqual(self.values()["KOTOHA_TEMPERATURE"], "0.7")  # 書き換わっていない

    def test_not_a_number_is_refused(self):
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"KOTOHA_TEMPERATURE": "あつめ"})

    def test_integer_field_refuses_a_fraction(self):
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"KOTOHA_RECENT_TURNS": "5.5"})

    def test_key_outside_the_list_is_refused(self):
        """画面に出していない設定を、リクエストだけで書き換えられないこと。"""
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"GEMINI_API_KEY": "abc"})
        self.assertNotIn("abc", self.env.read_text(encoding="utf-8"))

    def test_nothing_is_written_when_one_value_is_bad(self):
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"KOTOHA_TEMPERATURE": "0.4", "KOTOHA_RECENT_TURNS": "-1"})
        self.assertEqual(self.values()["KOTOHA_TEMPERATURE"], "0.7")


class ToggleSettingTests(AdminTestCase):
    """オン・オフで持つ設定。外出先から声かけを止められるように。"""

    def test_it_is_written_as_true_or_false(self):
        admin.write_settings({"KOTOHA_LOOKOUT_ENABLED": "false"})
        self.assertIn("KOTOHA_LOOKOUT_ENABLED=false", self.env.read_text(encoding="utf-8"))
        admin.write_settings({"KOTOHA_LOOKOUT_ENABLED": "true"})
        self.assertIn("KOTOHA_LOOKOUT_ENABLED=true", self.env.read_text(encoding="utf-8"))

    def test_upper_case_is_accepted(self):
        admin.write_settings({"KOTOHA_BRIEFING_ENABLED": "TRUE"})
        self.assertIn("KOTOHA_BRIEFING_ENABLED=true", self.env.read_text(encoding="utf-8"))

    def test_anything_else_is_refused(self):
        for bad in ("1", "はい", "", "onn"):
            with self.subTest(bad=bad):
                with self.assertRaises(admin.AdminError):
                    admin.write_settings({"KOTOHA_REACH_OUT_ENABLED": bad})

    def test_a_number_is_still_a_number(self):
        """切り替えを足しても、数値の検査は変わらない。"""
        with self.assertRaises(admin.AdminError):
            admin.write_settings({"KOTOHA_TEMPERATURE": "true"})

    def test_the_screen_gets_the_type(self):
        """画面は type を見て、入力欄か切り替えかを決める。"""
        kinds = {item["key"]: item["type"] for item in admin.read_settings()}
        self.assertEqual(kinds["KOTOHA_LOOKOUT_ENABLED"], "bool")
        self.assertEqual(kinds["KOTOHA_TEMPERATURE"], "number")


if __name__ == "__main__":
    unittest.main()
