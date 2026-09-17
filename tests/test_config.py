"""設定と設定利用箇所の検証。実際の.env・DB・外部APIは使わない。"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from kotoha.settings import ensure_settings, read_env

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kotoha config ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        shutil.copytree(ROOT / "kotoha", self.base / "kotoha", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / ".env.example", self.base / ".env.example")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("KOTOHA_", "GEMINI_"))}
        self.env["PYTHONIOENCODING"] = "utf-8"

    def run_code(self, code):
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=self.base,
                              env=self.env, capture_output=True, text=True,
                              encoding="utf-8", timeout=15)

    def write_env(self, text):
        (self.base / ".env").write_text(text, encoding="utf-8-sig")

    def test_defaults_and_relative_path_are_independent_of_cwd(self):
        result = self.run_code("from kotoha import config as c; "
                               "assert c.WEB_PORT == 8000; assert c.BACKUP_KEEP == 7; "
                               "assert c.DB_PATH == c.BASE_DIR / 'data/kotoha.sqlite3'")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_file_overrides_and_environment_precedence(self):
        self.write_env('KOTOHA_WEB_PORT=9100\nKOTOHA_BACKUP_KEEP=2\nKOTOHA_BROWSER_AUTO_OPEN=false\n')
        self.env["KOTOHA_WEB_PORT"] = "9200"
        result = self.run_code("from kotoha import config as c; assert c.WEB_PORT == 9200; "
                               "assert c.BACKUP_KEEP == 2; assert not c.BROWSER_AUTO_OPEN")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_values_fail_without_printing_values(self):
        for key, value in (("KOTOHA_BACKUP_KEEP", "0"), ("KOTOHA_WEB_PORT", "65536"),
                           ("KOTOHA_TEMPERATURE", "nan"), ("KOTOHA_FAST_ENABLED", "bad-private-value"),
                           ("KOTOHA_BATCH_TURNS", "bad-private-value")):
            with self.subTest(key=key):
                self.write_env(f"{key}={value}\n")
                result = self.run_code("import kotoha.config")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertNotIn("bad-private-value", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_editor_adds_missing_settings_without_changing_existing_bytes(self):
        original = b'# existing\r\nGEMINI_API_KEY=test-placeholder\r\nKOTOHA_WEB_PORT=9100\r\n'
        target = self.base / ".env"
        target.write_bytes(original)
        ensure_settings(self.base)
        once = target.read_bytes()
        self.assertTrue(once.startswith(original))
        self.assertEqual(read_env(target)["KOTOHA_WEB_PORT"], "9100")
        self.assertEqual(read_env(target)["GEMINI_API_KEY"], "test-placeholder")
        self.assertEqual(set(read_env(target)), set(read_env(self.base / ".env.example")))
        ensure_settings(self.base)
        self.assertEqual(target.read_bytes(), once)

    def test_first_edit_creates_file_and_invalid_file_can_be_prepared(self):
        target = ensure_settings(self.base)
        self.assertEqual(target.read_bytes(), (self.base / ".env.example").read_bytes())
        target.write_text("broken-line\nKOTOHA_WEB_PORT=bad\n", encoding="utf-8")
        ensure_settings(self.base)
        self.assertTrue(target.read_text(encoding="utf-8").startswith("broken-line\n"))

    def test_bom_quotes_spaces_and_equals_are_preserved_in_values(self):
        self.write_env('GEMINI_API_KEY="test=placeholder"\nKOTOHA_DB_PATH="data/my chat.sqlite3"\n')
        values = read_env(self.base / ".env")
        self.assertEqual(values["GEMINI_API_KEY"], "test=placeholder")
        self.assertEqual(values["KOTOHA_DB_PATH"], "data/my chat.sqlite3")

    def test_configurable_batch_size_and_new_memory_expiry(self):
        self.write_env("KOTOHA_BATCH_TURNS=1\nKOTOHA_EPISODE_DAYS=2\n")
        code = '''
import sys, types
sys.modules['httpx'] = types.ModuleType('httpx')
from kotoha import db, consolidate
conn = db.connect()
db.init(conn)
for turn in (1, 2):
    db.insert_message(conn, turn, 'user', 'hello')
conn.execute("UPDATE messages SET created_at='2026-09-01T00:00:00Z'")
messages = consolidate.fetch_unprocessed(conn)
assert len(messages) == 1
consolidate._validate_and_save(conn, {'new_nodes': [
    {'layer': 'episode', 'text': 'event', 'source_message_ids': [messages[0]['id']]}
]}, messages)
assert conn.execute('SELECT expires_at FROM memory_nodes').fetchone()[0] == '2026-09-03T00:00:00Z'
conn.close()
'''
        result = self.run_code(code)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_configurable_backup_retention_preserves_latest_files(self):
        self.write_env("KOTOHA_BACKUP_KEEP=2\n")
        code = '''
import sys, types
sys.modules['httpx'] = types.ModuleType('httpx')
from kotoha import config, db, cli
conn = db.connect()
db.init(conn)
conn.close()
folder = config.DB_PATH.parent / 'backups'
folder.mkdir()
for year in (2000, 2001, 2002):
    (folder / f'kotoha_{year}0101_000000.sqlite3').write_bytes(b'test')
cli._backup()
files = sorted(folder.glob('*.sqlite3'))
assert len(files) == 2
assert files[0].name == 'kotoha_20020101_000000.sqlite3'
'''
        result = self.run_code(code)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
