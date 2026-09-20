"""この機械の様子。ことはが「そこに居る」ための材料。

普段見るのは2つだけにしてある。**PCを起動してからの時間**と、**前面にあるアプリの
名前**。ウィンドウの題名も、中身も、履歴も読まない。相手が何をしているかを
推し量るには足り、のぞき見にはならない範囲に絞ってある。

機械の中身（空き容量・メモリ・CPU・GPU）は、**聞かれたときだけ**調べる。
毎回プロンプトに載せると150字ほど食うが、ほとんどの会話では要らない。

アプリは1分ごとに数えて、1時間ぶんの多数決で「いま何をしているか」とする。
会話している瞬間はブラウザが前面に来てしまうので、その一瞬では判断しない。
"""

import ctypes
import json
import shutil
import subprocess
import sys
from datetime import datetime

from .. import config
from ..memory import db

# 数えた結果の置き場は db にまとめてある（1時間ごとに捨てて数え直す）。
# 1時間ぶん数えても、これ未満のアプリは「触っている」と言わない。
MIN_SAMPLES = 5

# 実行ファイル名から、口に出せる名前へ。載っていないものはそのまま使う。
FRIENDLY = {
    "code": "VS Code",
    "devenv": "Visual Studio",
    "chrome": "Chrome",
    "brave": "Brave",
    "msedge": "Edge",
    "firefox": "Firefox",
    "explorer": "エクスプローラー",
    "windowsterminal": "ターミナル",
    "powershell": "PowerShell",
    "cmd": "コマンドプロンプト",
    "slack": "Slack",
    "discord": "Discord",
    "ms-teams": "Teams",
    "outlook": "Outlook",
    "excel": "Excel",
    "winword": "Word",
    "powerpnt": "PowerPoint",
    "notepad": "メモ帳",
    "steam": "Steam",
    "spotify": "Spotify",
    "obs64": "OBS",
    "vlc": "VLC",
    "photoshop": "Photoshop",
    "illustrator": "Illustrator",
    "clipstudiopaint": "クリスタ",
    "blender": "Blender",
    "claude": "Claude",
    "cursor": "Cursor",
}


# この言葉が出たときだけ、機械の中身を調べて渡す。外れたら足せばよい。
MACHINE_WORDS = (
    "容量", "空き", "ディスク", "ドライブ", "ストレージ", "メモリ", "cpu", "ＣＰＵ",
    "gpu", "ＧＰＵ", "vram", "温度", "ファン", "パソコン", "pc", "ＰＣ",
    "音声エンジン", "aivis", "ollama", "エンジン", "再起動", "入れ直",
    "起こし", "起動", "止め", "落とし", "重い", "遅い",
)


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def enabled() -> bool:
    return config.PRESENCE_ENABLED and sys.platform == "win32"


def asked_about_machine(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in MACHINE_WORDS)


def uptime_hours():
    """PCを起動してからの時間。眠らずに使い続けているかが分かる。"""
    if sys.platform != "win32":
        return None
    try:
        return ctypes.windll.kernel32.GetTickCount64() / 3600000.0
    except (AttributeError, OSError):
        return None


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def idle_seconds():
    """最後に何か触ってからの秒数。分からなければ None。

    **押した内容は読まない。** 「最後に何かした時刻」だけを見る。前面アプリの
    名前しか見ないのと同じ線引きで、相手が席に居るかを推し量るには足りる。

    ことはが口を開くときにだけ呼ぶ（announce）。常時見張る理由がない。
    """
    if sys.platform != "win32":
        return None
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks = ctypes.windll.kernel32.GetTickCount64()
    except (AttributeError, OSError):
        return None
    # 最後の入力は32bitで返る。49.7日で一周するので、その幅で引く。
    return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0


def foreground_app():
    """前面にあるアプリの名前。題名は読まない。"""
    if sys.platform != "win32":
        return None
    try:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        window = user32.GetForegroundWindow()
        if not window:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if not pid.value:
            return None
        # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION。名前を聞くだけの権限。
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(len(buffer))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            stem = buffer.value.rsplit("\\", 1)[-1].rsplit(".", 1)[0].lower()
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, ValueError):
        return None
    return FRIENDLY.get(stem, stem) if stem else None


def memory():
    """使用率と空き。単位はGB。"""
    if sys.platform != "win32":
        return None
    block = MEMORYSTATUSEX()
    block.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(block)):
        return None
    return block.dwMemoryLoad, block.ullAvailPhys / 2 ** 30


def disks():
    """つながっているドライブの空き。読めないものは飛ばす。"""
    found = []
    for letter in "CDEFG":
        try:
            usage = shutil.disk_usage(f"{letter}:\\")
        except OSError:
            continue
        if usage.total < 10 * 2 ** 30:      # 復旧領域などは出さない
            continue
        found.append((letter, usage.free / 2 ** 30, usage.used * 100 // usage.total))
    return found


def _cpu_times():
    if sys.platform != "win32":
        return None
    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    whole = lambda f: (f.high << 32) | f.low
    return whole(idle), whole(kernel) + whole(user)


_cpu_base = None


def cpu_tick() -> None:
    """巡回のたびに基準を取り直す。次に聞かれたとき、この間の平均を出せる。"""
    global _cpu_base
    _cpu_base = _cpu_times()


def cpu():
    """前回の巡回からの平均使用率。基準が無ければ短く測る。"""
    now = _cpu_times()
    if now is None:
        return None
    base = _cpu_base
    if base is None:
        import time as _time

        _time.sleep(0.1)
        base, now = now, _cpu_times()
        if now is None:
            return None
    idle, total = now[0] - base[0], now[1] - base[1]
    if total <= 0:
        return None
    return 100 - idle * 100 // total


def gpu():
    """NVIDIAのGPUだけ。無ければ黙って諦める。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode or not result.stdout.strip():
        return None
    try:
        load, used, total, temp = (int(x) for x in result.stdout.splitlines()[0].split(","))
    except ValueError:
        return None
    return load, used / 1024, total / 1024, temp


def details() -> str:
    """機械の中身。聞かれたときだけ渡す。"""
    if not enabled():
        return ""
    parts = []
    for letter, free, used in disks():
        parts.append(f"{letter}ドライブ 空き{free:.0f}GB（{used}%使用）")
    found = memory()
    if found:
        parts.append(f"メモリ{found[0]}%使用（空き{found[1]:.1f}GB）")
    load = cpu()
    if load is not None:
        parts.append(f"CPU{load}%")
    card = gpu()
    if card:
        parts.append(f"GPU{card[0]}%・VRAM{card[1]:.1f}/{card[2]:.0f}GB・{card[3]}℃")
    return "、".join(parts)


def _hours_text(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)}分"
    return f"{int(hours)}時間"


def snapshot(conn):
    """画面に出す「いまの様子」。(見出し, 値) を並べて返す。

    新しく覗くものは足していない。会話で使っている値をそのまま並べるだけで、
    details() と同じく、見に来られたときにだけ調べる。窓の題名は読まない。
    """
    rows = []
    hours = uptime_hours()
    if hours is not None:
        rows.append(("起動してから", _hours_text(hours)))
    app = foreground_app()
    if app:
        rows.append(("いま前面", app))
    busy = busy_with(conn)
    if busy:
        rows.append(("この1時間", f"{busy[0]}（{busy[1]}分ほど）"))
    for letter, free, used in disks():
        rows.append((f"{letter}ドライブ", f"空き{free:.0f}GB（{used}%使用）"))
    found = memory()
    if found:
        rows.append(("メモリ", f"{found[0]}%使用（空き{found[1]:.1f}GB）"))
    load = cpu()
    if load is not None:
        rows.append(("CPU", f"{load}%"))
    card = gpu()
    if card:
        rows.append(("GPU", f"{card[0]}%・VRAM{card[1]:.1f}/{card[2]:.0f}GB・{card[3]}℃"))
    return rows


def sample(conn) -> None:
    """巡回から1分ごとに呼ぶ。いま前面のアプリを1つ数える。"""
    cpu_tick()
    if not enabled():
        return
    app = foreground_app()
    if not app:
        return
    hour = datetime.now().strftime("%Y-%m-%d %H")
    tally = {}
    if db.get_state(conn, db.FRONT_TALLY_HOUR) == hour:
        try:
            tally = json.loads(db.get_state(conn, db.FRONT_TALLY) or "{}")
        except ValueError:
            tally = {}
    if not isinstance(tally, dict):
        tally = {}
    tally[app] = int(tally.get(app, 0)) + 1
    db.set_state(conn, db.FRONT_TALLY_HOUR, hour)
    db.set_state(conn, db.FRONT_TALLY, json.dumps(tally, ensure_ascii=False))
    _mark_streak(conn, app)


def _mark_streak(conn, app: str) -> None:
    """同じアプリが続いている間、始まった時刻を覚えておく。"""
    if db.get_state(conn, db.FRONT_STREAK_APP) != app:
        db.set_state(conn, db.FRONT_STREAK_APP, app)
        db.set_state(conn, db.FRONT_STREAK_FROM, db.now_utc())


def streak(conn):
    """いま何を、何時間続けて触っているか。分からなければ None。"""
    app = db.get_state(conn, db.FRONT_STREAK_APP)
    began = db.get_state(conn, db.FRONT_STREAK_FROM)
    if not app or not began:
        return None
    return app, db.seconds_since(began) / 3600.0


def reset_streak(conn) -> None:
    """一度声をかけたら、そこから数え直す。何度も言わない。"""
    db.set_state(conn, db.FRONT_STREAK_FROM, db.now_utc())


def busy_with(conn):
    """この1時間で、いちばん長く触っていたアプリと、そのおおよその分数。"""
    if not enabled():
        return None
    if db.get_state(conn, db.FRONT_TALLY_HOUR) != datetime.now().strftime("%Y-%m-%d %H"):
        return None
    try:
        tally = json.loads(db.get_state(conn, db.FRONT_TALLY) or "{}")
    except ValueError:
        return None
    if not isinstance(tally, dict) or not tally:
        return None
    app, count = max(tally.items(), key=lambda kv: kv[1])
    if count < MIN_SAMPLES:
        return None
    return app, count


def describe(conn) -> str:
    """プロンプトに入れる1行。分からなければ空を返す。"""
    if not enabled():
        return ""
    parts = []
    hours = uptime_hours()
    if hours is not None and hours >= 1:
        parts.append(f"PCは{int(hours)}時間つけっぱなし")
    busy = busy_with(conn)
    if busy:
        app, minutes = busy
        parts.append(f"この1時間は{app}を触っている（{minutes}分ほど）")
    return "、".join(parts)
