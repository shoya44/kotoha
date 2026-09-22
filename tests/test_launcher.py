"""起動補助の検証。実DB・API・ブラウザー・Tailscaleには接続しない。"""

import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class LauncherFixture:
    """偽の config を差し込んで launcher だけを読み込む。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kotoha launcher ")
        self.addCleanup(self.tmp.cleanup)
        # 実際の.envを読まず、依存関係も起動しない。
        self.config = types.ModuleType("kotoha.config")
        self.config.BASE_DIR = ROOT
        self.config.WEB_HOST = "127.0.0.1"
        self.config.WEB_PORT = 8000
        self.config.WEB_TOKEN = "test-only"
        self.config.TAILSCALE_DIR = Path("C:/Program Files/Tailscale")
        self.config.TAILSCALE_AUTO_START = True
        self.config.TAILSCALE_SERVE_ENABLED = False
        self.config.TAILSCALE_HTTPS_PORT = 443
        self.config.DEBUG = False
        self.config.BROWSER_AUTO_OPEN = True
        # 本物は30秒。そのままだと、繋がらない場面のテストが本当に30秒待つ。
        # 何度か様子を見に行くだけの余裕があればよいので、短くしておく。
        self.config.STARTUP_TIMEOUT_SECONDS = 2
        self.config.VOICE_BASE_URL = "http://127.0.0.1:10101"
        self.config.AIVIS_AUTO_START = True
        self.config.AIVIS_DIR = Path(self.tmp.name) / "AivisSpeech"
        self.config.EMBED_ENABLED = True
        self.config.EMBED_BASE_URL = "http://127.0.0.1:11434"
        self.config.OLLAMA_AUTO_START = True
        self.config.OLLAMA_DIR = Path(self.tmp.name) / "Ollama"
        self.config.require_keys = Mock()
        # 常駐の登録は本物のレジストリとタスクを触る。テストでは何もしない。
        self.autostart = types.ModuleType("kotoha.autostart")
        self.autostart.repair = Mock()
        package = types.ModuleType("kotoha")
        package.config = self.config
        package.autostart = self.autostart
        spec = importlib.util.spec_from_file_location("kotoha.launcher", ROOT / "kotoha/launcher.py")
        self.launcher = importlib.util.module_from_spec(spec)
        self.modules = patch.dict("sys.modules", {
            "kotoha": package, "kotoha.config": self.config,
            "kotoha.autostart": self.autostart})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec.loader.exec_module(self.launcher)


class LauncherTests(LauncherFixture, unittest.TestCase):
    def test_wildcard_and_ipv6_browser_urls(self):
        self.assertEqual(self.launcher.local_url("0.0.0.0", 8000), ("http://127.0.0.1:8000", "127.0.0.1"))
        self.assertEqual(self.launcher.local_url("::", 9000), ("http://[::1]:9000", "::1"))

    def test_other_application_is_rejected_before_sending_token(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"info":{"title":"another app"}}')
        with patch.object(self.launcher.urllib.request, "build_opener", return_value=opener):
            self.assertFalse(self.launcher.is_kotoha("http://127.0.0.1:8000"))
        self.assertEqual(opener.open.call_count, 1)

    def test_readiness_checks_auth_without_reading_conversations(self):
        opener = Mock()
        opener.open.side_effect = [
            io.BytesIO(b'{"info":{"title":"kotoha"},"paths":{"/api/chat":{}}}'),
            io.BytesIO(b'[]'),
        ]
        with patch.object(self.launcher.urllib.request, "build_opener", return_value=opener):
            self.assertTrue(self.launcher.is_kotoha("http://127.0.0.1:8000"))
        request = opener.open.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/history?limit=0"))
        self.assertEqual(request.get_header("X-kotoha-token"), "test-only")

    def test_unavailable_server_is_not_ready(self):
        opener = Mock()
        opener.open.side_effect = OSError("not listening")
        with patch.object(self.launcher.urllib.request, "build_opener", return_value=opener):
            self.assertFalse(self.launcher.is_kotoha("http://127.0.0.1:8000"))

    def test_browser_waits_for_readiness(self):
        with patch.object(self.launcher, "is_kotoha", side_effect=[False, True]), \
             patch.object(self.launcher, "open_browser") as browser:
            self.launcher.open_when_ready("http://local", threading.Event())
        browser.assert_called_once_with("http://local")

    def test_timeout_and_shutdown_do_not_open_browser(self):
        with patch.object(self.launcher, "open_browser") as browser, \
             patch.object(self.launcher, "is_kotoha") as ready, \
             patch("builtins.print"):
            self.launcher.open_when_ready("http://local", threading.Event(), timeout=0)
            stopped = threading.Event()
            stopped.set()
            self.launcher.open_when_ready("http://local", stopped)
        browser.assert_not_called()
        ready.assert_not_called()

    def test_missing_dependency_stops_before_startup(self):
        with patch.object(self.launcher.importlib.util, "find_spec", return_value=None), \
             patch.object(self.launcher, "start_tailscale") as tailscale:
            with self.assertRaisesRegex(SystemExit, "kotoha.bat setup"):
                self.launcher.main()
        tailscale.assert_not_called()

    def test_conflicting_port_is_not_reused(self):
        connection = Mock()
        connection.__enter__ = Mock()
        connection.__exit__ = Mock()
        with patch.object(self.launcher.importlib.util, "find_spec", return_value=True), \
             patch.object(self.launcher.socket, "create_connection", return_value=connection), \
             patch.object(self.launcher, "is_kotoha", return_value=False), \
             patch.object(self.launcher, "open_browser") as browser:
            with self.assertRaisesRegex(SystemExit, "使用中"):
                self.launcher.main()
        browser.assert_not_called()

    def test_existing_server_opens_without_initializing_db(self):
        connection = Mock()
        connection.__enter__ = Mock()
        connection.__exit__ = Mock()
        with patch.object(self.launcher.importlib.util, "find_spec", return_value=True), \
             patch.object(self.launcher.socket, "create_connection", return_value=connection), \
             patch.object(self.launcher, "is_kotoha", return_value=True), \
             patch.object(self.launcher, "start_tailscale"), \
             patch.object(self.launcher, "start_aivis"), patch.object(self.launcher, "start_ollama"), \
             patch.object(self.launcher, "open_browser") as browser, \
             patch("builtins.print"):
            self.launcher.main()
        browser.assert_called_once_with("http://127.0.0.1:8000")

    def test_tailscale_missing_does_not_abort_local_startup(self):
        with patch.object(Path, "is_file", return_value=False), \
             patch.object(self.launcher.subprocess, "run", side_effect=FileNotFoundError), \
             patch.object(self.launcher.subprocess, "Popen") as popen, \
             patch("builtins.print"):
            self.launcher.start_tailscale()
        popen.assert_not_called()

    def test_new_server_initializes_db_and_cleans_up_on_failure(self):
        db = Mock()
        app = object()
        uvicorn = Mock()
        uvicorn.run.side_effect = SystemExit(1)
        watcher = Mock()
        stopped = threading.Event()
        with patch.dict("sys.modules", {
            "kotoha.memory": types.SimpleNamespace(db=db),
            "kotoha.serve": types.ModuleType("kotoha.serve"),
            "kotoha.serve.web": types.SimpleNamespace(app=app),
            "uvicorn": uvicorn,
        }), patch.object(self.launcher.importlib.util, "find_spec", return_value=True), \
             patch.object(self.launcher.socket, "create_connection", side_effect=OSError), \
             patch.object(self.launcher, "start_tailscale"), \
             patch.object(self.launcher, "start_aivis"), patch.object(self.launcher, "start_ollama"), \
             patch.object(self.launcher.threading, "Thread", return_value=watcher), \
             patch.object(self.launcher.threading, "Event", return_value=stopped), \
             patch("builtins.print"):
            with self.assertRaises(SystemExit):
                self.launcher.main()
        db.init.assert_called_once_with(db.connect.return_value)
        db.connect.return_value.close.assert_called_once()
        uvicorn.run.assert_called_once_with(app, host="127.0.0.1", port=8000,
                                            log_level="warning", access_log=False)
        watcher.start.assert_called_once()
        self.assertTrue(stopped.is_set())
        watcher.join.assert_called_once()

    def serve_state(self, proxy="http://127.0.0.1:8000"):
        return {"TCP": {"443": {"HTTPS": True}}, "Web": {
            "device.example.ts.net:443": {"Handlers": {"/": {"Proxy": proxy}}}
        }}

    def test_serve_starts_then_verifies_configuration(self):
        state = {"BackendState": "Running", "Self": {"DNSName": "device.example.ts.net."}}
        with patch.object(self.launcher, "tailscale_json", side_effect=[state, {}, self.serve_state()]), \
             patch.object(self.launcher, "tailscale_command") as command:
            url = self.launcher.start_serve("http://127.0.0.1:8000")
        command.assert_called_once_with("serve", "--bg", "--yes", "--https=443", "http://127.0.0.1:8000")
        self.assertEqual(url, "https://device.example.ts.net")

    def test_existing_identical_serve_is_reused(self):
        state = {"BackendState": "Running", "Self": {"DNSName": "device.example.ts.net."}}
        with patch.object(self.launcher, "tailscale_json", side_effect=[state, self.serve_state()]), \
             patch.object(self.launcher, "tailscale_command") as command:
            self.launcher.start_serve("http://127.0.0.1:8000")
        command.assert_not_called()

    def test_conflicting_serve_and_funnel_are_not_overwritten(self):
        funnel = self.serve_state()
        funnel["AllowFunnel"] = {"device.example.ts.net:443": True}
        foreground = {"Foreground": {"session": self.serve_state()}}
        for existing in (self.serve_state("http://127.0.0.1:9000"), funnel, foreground):
            with self.subTest(existing=existing), \
                 patch.object(self.launcher, "tailscale_json", side_effect=[
                     {"BackendState": "Running", "Self": {"DNSName": "device.example.ts.net"}}, existing]), \
                 patch.object(self.launcher, "tailscale_command") as command:
                with self.assertRaises(self.launcher.ServeError):
                    self.launcher.start_serve("http://127.0.0.1:8000")
                command.assert_not_called()

    def test_disconnected_tailscale_does_not_start_serve(self):
        # 待ちきってから諦めるところを見る。待つ時間そのものは確かめない。
        self.config.STARTUP_TIMEOUT_SECONDS = 0.01
        with patch.object(self.launcher, "tailscale_json", return_value={"BackendState": "NeedsLogin"}), \
             patch.object(self.launcher, "tailscale_command") as command:
            with self.assertRaisesRegex(self.launcher.ServeError, "未接続"):
                self.launcher.start_serve("http://127.0.0.1:8000")
        command.assert_not_called()

    def test_only_verified_tailscale_url_is_opened(self):
        self.config.TAILSCALE_SERVE_ENABLED = True
        url = "https://device.example.ts.net"
        with patch.object(self.launcher, "start_serve", return_value=url), \
             patch.object(self.launcher, "is_kotoha", return_value=True) as ready, \
             patch.object(self.launcher, "open_browser") as browser:
            self.launcher.publish_and_open("http://127.0.0.1:8000", threading.Event())
        ready.assert_called_once_with(url)
        browser.assert_called_once_with(url)

    def test_serve_failure_never_opens_local_url(self):
        self.config.TAILSCALE_SERVE_ENABLED = True
        with patch.object(self.launcher, "is_kotoha", return_value=True), \
             patch.object(self.launcher, "start_serve", side_effect=self.launcher.ServeError("failed")), \
             patch.object(self.launcher, "open_browser") as browser, patch("builtins.print"):
            self.launcher.open_when_ready("http://127.0.0.1:8000", threading.Event())
        browser.assert_not_called()

    def test_nondefault_https_port_is_in_url(self):
        self.config.TAILSCALE_HTTPS_PORT = 8443
        state = {"BackendState": "Running", "Self": {"DNSName": "device.example.ts.net"}}
        settings = {"TCP": {"8443": {"HTTPS": True}}, "Web": {
            "device.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}}}
        with patch.object(self.launcher, "tailscale_json", side_effect=[state, settings]):
            self.assertEqual(self.launcher.start_serve("http://127.0.0.1:8000"), "https://device.example.ts.net:8443")

    def test_permission_error_is_concise_and_does_not_dump_cli_output(self):
        result = types.SimpleNamespace(returncode=1, stderr="Access is denied. private details", stdout="")
        with patch.object(self.launcher.subprocess, "run", return_value=result):
            with self.assertRaises(self.launcher.ServeError) as error:
                self.launcher.tailscale_command("status", "--json")
        self.assertIn("アクセスが拒否", str(error.exception))
        self.assertNotIn("private details", str(error.exception))

    def test_batch_failure_from_directory_with_spaces(self):
        """入口は1枚。空白を含む場所に置かれても、道を見失わない。"""
        with tempfile.TemporaryDirectory(prefix="kotoha launcher ") as tmp:
            target = Path(tmp) / "kotoha.bat"
            target.write_bytes((ROOT / "kotoha.bat").read_bytes())
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "call", str(target)],
                cwd=ROOT, input="\n", capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('Run "kotoha.bat setup" first', result.stdout)


class EngineStartTests(LauncherFixture):
    """起こす相手が違うだけで、確かめることは同じ4つ。

    aivis と ollama は別のコードなので、どちらにも走らせる（本数は減らない）。
    同じ確認を二度書いていたのをやめる、という話。子は次を与える:
      up_name     生きているかを見る関数の名前
      start()     起こす
      make_engine()  それらしい実行ファイルを置く
      disable()   自動起動を止める
    """

    def up(self, **kwargs):
        return patch.object(self.launcher, self.up_name, **kwargs)

    def test_does_not_start_twice(self):
        self.make_engine()
        with self.up(return_value=True),              patch.object(self.launcher.subprocess, "Popen") as popen,              patch("builtins.print"):
            self.start()
        popen.assert_not_called()

    def test_missing_engine_is_only_reported(self):
        """置いていないなら、黙って諦めずに一言だけ言う。"""
        with self.up(return_value=False),              patch.object(self.launcher.subprocess, "Popen") as popen,              patch("builtins.print") as printed:
            self.start()
        popen.assert_not_called()
        self.assertTrue(printed.called)

    def test_disabled_does_nothing(self):
        """止めてあるなら、生きているかどうかも見に行かない。"""
        self.make_engine()
        self.disable()
        with self.up() as up, patch.object(self.launcher.subprocess, "Popen") as popen:
            self.start()
        popen.assert_not_called()
        up.assert_not_called()

    def test_failure_to_launch_does_not_raise(self):
        """起こせなくても、会話はそのまま続く。"""
        self.make_engine()
        with self.up(return_value=False),              patch.object(self.launcher.subprocess, "Popen", side_effect=OSError),              patch("builtins.print"):
            self.start()


class AivisStartTests(EngineStartTests, unittest.TestCase):
    """音声エンジンは画面を出さずに起こす。失敗しても会話は続ける。"""

    up_name = "aivis_is_up"

    def start(self):
        self.launcher.start_aivis()

    def disable(self):
        self.config.AIVIS_AUTO_START = False

    def make_engine(self):
        engine = self.config.AIVIS_DIR / "AivisSpeech-Engine"
        engine.mkdir(parents=True, exist_ok=True)
        exe = engine / "run.exe"
        exe.write_bytes(b"")
        return exe

    def test_starts_the_engine_without_a_window(self):
        exe = self.make_engine()
        with self.up(return_value=False),              patch.object(self.launcher.subprocess, "Popen") as popen,              patch("builtins.print"):
            self.start()
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(exe))
        self.assertIn("--host", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("--port", command)
        self.assertIn("10101", command)
        self.assertEqual(popen.call_args.kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))


class OllamaStartTests(EngineStartTests, unittest.TestCase):
    """記憶の想起に使うローカルLLM。止まっていても会話は続ける。"""

    up_name = "ollama_is_up"

    def start(self):
        self.launcher.start_ollama()

    def disable(self):
        self.config.OLLAMA_AUTO_START = False

    def make_engine(self):
        self.config.OLLAMA_DIR.mkdir(parents=True, exist_ok=True)
        exe = self.config.OLLAMA_DIR / "ollama.exe"
        exe.write_bytes(b"")
        return exe

    def test_starts_the_server_without_a_window(self):
        exe = self.make_engine()
        with self.up(return_value=False),              patch.object(self.launcher.subprocess, "Popen") as popen,              patch("builtins.print"):
            self.start()
        self.assertEqual(popen.call_args.args[0], [str(exe), "serve"])
        self.assertEqual(popen.call_args.kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_listening_address_follows_the_setting(self):
        self.make_engine()
        self.config.EMBED_BASE_URL = "http://127.0.0.1:9999"
        with self.up(return_value=False),              patch.object(self.launcher.subprocess, "Popen") as popen,              patch("builtins.print"):
            self.start()
        self.assertEqual(popen.call_args.kwargs["env"]["OLLAMA_HOST"], "127.0.0.1:9999")

    def test_not_started_when_recall_is_switched_off(self):
        """想起を使わない設定なら、立ち上げる理由がない。"""
        self.make_engine()
        self.config.EMBED_ENABLED = False
        with self.up() as up, patch.object(self.launcher.subprocess, "Popen") as popen:
            self.start()
        popen.assert_not_called()
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()
