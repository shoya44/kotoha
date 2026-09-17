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


class LauncherTests(unittest.TestCase):
    def setUp(self):
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
        self.config.STARTUP_TIMEOUT_SECONDS = 30
        self.config.require_keys = Mock()
        package = types.ModuleType("kotoha")
        package.config = self.config
        spec = importlib.util.spec_from_file_location("kotoha.launcher", ROOT / "kotoha/launcher.py")
        self.launcher = importlib.util.module_from_spec(spec)
        self.modules = patch.dict("sys.modules", {"kotoha": package, "kotoha.config": self.config})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec.loader.exec_module(self.launcher)

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
            with self.assertRaisesRegex(SystemExit, "setup.bat"):
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
        with tempfile.TemporaryDirectory(prefix="kotoha launcher ") as tmp:
            folder = Path(tmp)
            for name in ("kotoha.bat", "start.bat"):
                target = folder / name
                target.write_bytes((ROOT / name).read_bytes())
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "call", str(target)],
                    cwd=ROOT, input="\n", capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("Run setup.bat first", result.stdout)


if __name__ == "__main__":
    unittest.main()
