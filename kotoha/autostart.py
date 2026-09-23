"""ログイン時の常駐と、見張り番の登録。**置き場所が変わっても迷子にしない。**

トレイを上げる道は2つある。ログイン時に走るレジストリの Run と、rescue が
入れる5分ごとのタスク（見張り番）。どちらも「pythonw.exe に入口のパスを渡す」
という形で、**外に控えを持つ。** だから入口を動かすと、古いパスが残ったまま
黙って何も起きなくなる。そこを、動いたときに直す。

Run は自分のキーなので黙って書き直せる。タスクのほうは管理者で作られている
ことがあり、直せないときは、どう直すかだけ伝える。
"""

import subprocess
import sys

from . import config

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "ことは"
TASK = "Kotoha Watchdog"
TRAY = config.BASE_DIR / "scripts" / "tray.pyw"


def command() -> str:
    """pythonw から直に呼ぶ。batを挟むと、そこでコンソールが一瞬出る。"""
    runner = config.BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    return f'"{runner}" "{TRAY}"'


def _key(write=False):
    import winreg

    access = winreg.KEY_READ | (winreg.KEY_WRITE if write else 0)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, access)


def current():
    """いま登録されている起動文字列。無ければ None。"""
    import winreg

    with _key() as key:
        try:
            return winreg.QueryValueEx(key, NAME)[0]
        except FileNotFoundError:
            return None


def enable() -> str:
    import winreg

    with _key(write=True) as key:
        winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
    return command()


def disable() -> bool:
    import winreg

    with _key(write=True) as key:
        try:
            winreg.DeleteValue(key, NAME)
        except FileNotFoundError:
            return False
    return True


def refresh() -> bool:
    """登録があって中身が古ければ、黙って書き直す。無ければ何もしない。

    **登録していない人に登録しない。** ここでするのは直しであって、勧誘ではない。
    """
    if sys.platform != "win32":
        return False
    try:
        if current() in (None, command()):
            return False
        enable()
    except OSError:
        return False
    return True


def _task_xml():
    try:
        done = subprocess.run(
            ["schtasks", "/query", "/tn", TASK, "/xml", "ONE"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def watchdog_needs_fixing() -> bool:
    """見張り番が、もう無いファイルを指していないか。"""
    if sys.platform != "win32":
        return False
    xml = _task_xml()
    if not xml:
        return False
    # rescue.bat が書く綴りは打った人しだい（c:\ と C:\）。大文字小文字では違わない。
    return str(TRAY).lower() not in xml.lower() and "tray.pyw" in xml.lower()


def fix_watchdog() -> bool:
    """見張り番の指し先を今の入口に直す。権限が足りなければ False。"""
    try:
        done = subprocess.run(
            ["schtasks", "/change", "/tn", TASK, "/tr", command()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def repair(say=print) -> None:
    """起動のたびに1度だけ通る。直せたものは黙って直し、残りだけ言う。"""
    refresh()
    if watchdog_needs_fixing() and not fix_watchdog():
        say(f'[見張り番] 「{TASK}」が古い場所を指しています。'
            'kotoha.bat rescue を管理者権限で実行し直してください。')
