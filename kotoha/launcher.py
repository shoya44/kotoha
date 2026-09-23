"""Web起動、Tailscale Serve配信、ブラウザー表示。"""

import importlib.util
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from . import autostart, config, single


def local_url(host, port):
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    authority = f"[{host}]" if ":" in host else host
    return f"http://{authority}:{port}", host


def is_kotoha(url):
    """履歴本文を取得せず、アプリ識別と現在のトークンを確認する。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/openapi.json", timeout=1) as response:
            schema = json.load(response)
        if schema.get("info", {}).get("title") != "kotoha":
            return False
        if "/api/chat" not in schema.get("paths", {}):
            return False
        request = urllib.request.Request(
            url + "/api/history?limit=0",
            headers={"X-Kotoha-Token": config.WEB_TOKEN},
        )
        with opener.open(request, timeout=1) as response:
            return json.load(response) == []
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start_tailscale(quiet=False):
    folder = config.TAILSCALE_DIR
    cli = str(folder / "tailscale.exe")
    gui = folder / "tailscale-ipn.exe"
    if gui.is_file():
        try:
            subprocess.Popen(
                [str(gui)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            if not quiet:
                print("Tailscaleの画面を開けませんでした。kotoha.bat menu から確認してください。")
    try:
        result = subprocess.run(
            [cli, "status", "--json"], capture_output=True, text=True,
            encoding="utf-8", timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        state = json.loads(result.stdout) if result.returncode == 0 else {}
        if state.get("BackendState") == "Running":
            if not quiet:
                print("Tailscale: 接続済み")
            return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    if not quiet:
        print("Tailscale: 接続できません。インストール・ログイン状態を確認してください。")


def aivis_is_up():
    """音声エンジンが応じるか。起動済みなら二重に立ち上げない。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(config.VOICE_BASE_URL + "/version", timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start_aivis(quiet=False):
    """音声エンジンを起こす。GUIは使わず run.exe だけを画面なしで動かす。

    立ち上がりにはモデル読み込みぶんの時間がかかるが、待たない。
    間に合わなければ読み上げが静かなだけで、会話はそのまま続けられる。
    """
    if not config.AIVIS_AUTO_START:
        return
    if aivis_is_up():
        if not quiet:
            print("AivisSpeech: 起動済み")
        return

    exe = config.AIVIS_DIR / "AivisSpeech-Engine" / "run.exe"
    if not exe.is_file():
        if not quiet:
            print("AivisSpeech: 実行ファイルが見つかりません。kotoha.bat settings で配置先を確認してください。")
        return

    parsed = urllib.parse.urlsplit(config.VOICE_BASE_URL)
    command = [str(exe)]
    if parsed.hostname:
        command += ["--host", parsed.hostname]
    if parsed.port:
        command += ["--port", str(parsed.port)]
    try:
        subprocess.Popen(
            command,
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        if not quiet:
            print("AivisSpeech: 起動できませんでした。実行権限を確認してください。")
        return
    if not quiet:
        print("AivisSpeech: 起動しました（声が出せるまで少しかかります）")


def ollama_is_up():
    """ローカルLLMが応じるか。起動済みなら二重に立ち上げない。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(config.EMBED_BASE_URL + "/api/tags", timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def start_ollama(quiet=False):
    """Ollamaを起こす。画面のいる常駐アプリではなく、serve だけを動かす。

    記憶の想起に使う。間に合わなくても、想起がタグ検索だけに戻るだけで
    会話は続けられるので、立ち上がりは待たない。
    """
    if not config.OLLAMA_AUTO_START or not config.EMBED_ENABLED:
        return
    if ollama_is_up():
        if not quiet:
            print("Ollama: 起動済み")
        return

    exe = config.OLLAMA_DIR / "ollama.exe"
    if not exe.is_file():
        if not quiet:
            print("Ollama: 実行ファイルが見つかりません。kotoha.bat settings で配置先を確認してください。")
        return

    # 待ち受け先は環境変数で渡す。接続先の設定を変えても追いかけられる。
    environment = dict(os.environ)
    parsed = urllib.parse.urlsplit(config.EMBED_BASE_URL)
    if parsed.netloc:
        environment["OLLAMA_HOST"] = parsed.netloc
    try:
        subprocess.Popen(
            [str(exe), "serve"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        if not quiet:
            print("Ollama: 起動できませんでした。実行権限を確認してください。")
        return
    if not quiet:
        print("Ollama: 起動しました（記憶を思い出せるまで少しかかります）")


class ServeError(Exception):
    pass


def tailscale_command(*args):
    try:
        result = subprocess.run(
            [str(config.TAILSCALE_DIR / "tailscale.exe"), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=config.STARTUP_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise ServeError("Tailscaleが見つかりません。kotoha.bat settings で配置先を確認してください。") from None
    except subprocess.TimeoutExpired:
        raise ServeError("Tailscaleがタイムアウトしました。ログイン状態とHTTPS/Serveの有効化を確認してください。") from None
    except OSError:
        raise ServeError("Tailscaleを実行できません。インストールと実行権限を確認してください。") from None
    if result.returncode:
        if "access is denied" in result.stderr.lower() or "アクセスが拒否" in result.stderr:
            raise ServeError("Tailscaleへのアクセスが拒否されました。利用ユーザーの実行権限を確認してください。")
        raise ServeError("Tailscale配信に失敗しました。接続状態と管理画面のHTTPS/Serve設定を確認してください。")
    return result.stdout


def tailscale_json(*args):
    try:
        value = json.loads(tailscale_command(*args))
    except ValueError:
        raise ServeError("Tailscaleの状態を読み取れませんでした。") from None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ServeError("Tailscaleの状態形式を確認できませんでした。")
    return value


def check_serve_config(settings, authority, target):
    """同じポートの別配信やFunnelを変更せず、同一設定だけ再利用する。"""
    port = authority.rsplit(":", 1)[1]
    for foreground in (settings.get("Foreground") or {}).values():
        if port in (foreground.get("TCP") or {}):
            raise ServeError("同じポートで別のTailscale配信が動作中です。HTTPSポートを変更してください。")
    if (settings.get("AllowFunnel") or {}).get(authority):
        raise ServeError("同じURLでFunnelが有効です。別のHTTPSポートを指定してください。")
    web = settings.get("Web") or {}
    if any(key.endswith(":" + port) and key != authority for key in web):
        raise ServeError("同じHTTPSポートに別の配信設定があります。ポートを変更してください。")
    handlers = (web.get(authority) or {}).get("Handlers") or {}
    tcp = (settings.get("TCP") or {}).get(port)
    if handlers or tcp:
        proxy = (handlers.get("/") or {}).get("Proxy", "").rstrip("/")
        if handlers.keys() == {"/"} and proxy == target and tcp == {"HTTPS": True}:
            return True
        raise ServeError("同じHTTPSポートは別の配信で使用中です。kotoha.bat settings でHTTPSポートを変更してください。")
    return False


def start_serve(target):
    # TailscaleがRunningになるまで少し待つ
    deadline = time.monotonic() + config.STARTUP_TIMEOUT_SECONDS
    state = {}

    while time.monotonic() < deadline:
        state = tailscale_json("status", "--json")
        backend_state = state.get("BackendState")

        if backend_state == "Running":
            break

        time.sleep(0.5)
    else:
        raise ServeError(
            f"Tailscaleが未接続です。現在の状態: "
            f"{state.get('BackendState', 'Unknown')}"
        )

    dns = (state.get("Self", {}).get("DNSName") or "").rstrip(".")
    if not dns or not re.fullmatch(r"[a-zA-Z0-9.-]+", dns):
        raise ServeError("TailscaleのDNS名を取得できません。MagicDNS設定を確認してください。")
    port = config.TAILSCALE_HTTPS_PORT
    authority = f"{dns}:{port}"
    settings = tailscale_json("serve", "status", "--json")
    if not check_serve_config(settings, authority, target):
        tailscale_command("serve", "--bg", "--yes", f"--https={port}", target)
        settings = tailscale_json("serve", "status", "--json")
        if not check_serve_config(settings, authority, target):
            raise ServeError("Tailscaleの配信開始を確認できませんでした。")
    return f"https://{dns}" + (f":{port}" if port != 443 else "")


def publish_and_open(url, stopped):
    target = start_serve(url) if config.TAILSCALE_SERVE_ENABLED else url
    if config.TAILSCALE_SERVE_ENABLED:
        deadline = time.monotonic() + config.STARTUP_TIMEOUT_SECONDS
        while not stopped.is_set():
            if is_kotoha(target):
                break
            if time.monotonic() >= deadline:
                raise ServeError("配信設定は完了しましたがHTTPS接続を確認できません。DNS・証明書・Tailscale接続を確認してください。")
            stopped.wait(0.5)
    if not stopped.is_set():
        open_browser(target)


def open_browser(url):
    print(f"チャット: {url}")
    if not config.BROWSER_AUTO_OPEN:
        return
    try:
        if webbrowser.open(url):
            return
    except webbrowser.Error:
        pass
    print("ブラウザーで上のURLを開いてください。")


def open_when_ready(url, stopped, timeout=None):
    if timeout is None:
        timeout = config.STARTUP_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout
    while not stopped.is_set() and time.monotonic() < deadline:
        if is_kotoha(url):
            if not stopped.is_set():
                try:
                    publish_and_open(url, stopped)
                except ServeError as error:
                    print(f"配信失敗: {error}")
                    print("設定を確認し、kotoha.batを実行し直してください。")
            return
        stopped.wait(0.5)
    if not stopped.is_set():
        print("起動完了を確認できませんでした。サーバーのエラー表示を確認してください。")


def main():
    os.chdir(config.BASE_DIR)
    missing = [name for name in ("httpx", "fastapi", "uvicorn")
               if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit("依存関係が不足しています。kotoha.bat setup を実行してください: " + ", ".join(missing))
    config.require_keys()
    if not config.WEB_TOKEN:
        raise SystemExit(".env の KOTOHA_WEB_TOKEN を設定してください。")
    if not 1 <= config.WEB_PORT <= 65535:
        raise SystemExit("KOTOHA_WEB_PORT は1〜65535で設定してください。")
    # 入口の場所が変わっていたら、ログイン時の常駐と見張り番をここで直す。
    autostart.repair()

    url, connect_host = local_url(config.WEB_HOST, config.WEB_PORT)
    if config.TAILSCALE_SERVE_ENABLED:
        if config.WEB_HOST not in ("127.0.0.1", "localhost", "0.0.0.0"):
            raise SystemExit("Serve配信ではKOTOHA_WEB_HOSTを127.0.0.1に設定してください。")
        url, connect_host = local_url("127.0.0.1", config.WEB_PORT)
    if not single.claim(single.BODY):
        # 別の本体が印を持っている。二重には上げない。起動処理中で、まだ
        # 答えないだけかもしれないので、少し待ってから画面だけ開く。
        deadline = time.monotonic() + config.STARTUP_TIMEOUT_SECONDS
        while not is_kotoha(url):
            if time.monotonic() >= deadline:
                raise SystemExit("ことはは別のプロセスで起動中です。二重には起動しません。")
            time.sleep(0.5)
        listening = True
    else:
        # 印の無い相手（古い本体や別アプリ）がポートを使っていないか。
        try:
            with socket.create_connection((connect_host, config.WEB_PORT), timeout=1):
                listening = True
        except OSError:
            listening = False
    if listening:
        if not is_kotoha(url):
            raise SystemExit("指定ポートは使用中です。別アプリ、または異なる設定のことはを確認してください。")
        if config.TAILSCALE_AUTO_START:
            start_tailscale(quiet=True)
        start_aivis(quiet=True)
        start_ollama(quiet=True)
        try:
            publish_and_open(url, threading.Event())
        except ServeError as error:
            raise SystemExit(f"配信失敗: {error}") from None
        return

    from .memory import db
    conn = db.connect()
    try:
        db.init(conn)
    finally:
        conn.close()

    if config.TAILSCALE_AUTO_START:
        start_tailscale(quiet=True)
    start_aivis()
    start_ollama()
    print("ことは 起動中…")
    print("終了: Ctrl+C")
    import uvicorn
    from .serve.web import app

    stopped = threading.Event()
    watcher = threading.Thread(target=open_when_ready, args=(url, stopped), daemon=True)
    watcher.start()
    try:
        uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT,
                    log_level="info" if config.DEBUG else "warning", access_log=config.DEBUG)
    finally:
        stopped.set()
        watcher.join(timeout=3)


if __name__ == "__main__":
    main()
