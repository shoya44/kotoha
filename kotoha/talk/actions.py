"""ことはに頼める操作。ここに書いたものしかできない。

返答に付いた `[DO: 名前]` を見て動かす。名前はこの表にあるものだけを受ける。
ことはが好きなコマンドを組み立てられる作りにはしていない。頼まれごとと、
実際に動かせることを、ここで突き合わせる。

何をしたかは必ず記録する。画面を持たない場面でも動くので、黙って起きたり
止まったりすると、あとから追えなくなる。
"""

import ctypes
import ctypes.wintypes as w
import os
import subprocess
import sys
import threading
import time

from .. import config

LOG_PATH = config.BASE_DIR / "data" / "actions.log"
# 応答を返しきってから落ちる。web.py の再起動と同じ間の取り方。
RESTART_DELAY = 0.4

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD),
                ("th32ParentProcessID", w.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", w.DWORD), ("szExeFile", ctypes.c_char * 260)]


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def _image_path(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _pids_of(path):
    """その実行ファイルから動いているプロセス。名前ではなく道のりで照合する。

    `run.exe` のようなありふれた名前を名前だけで狙うと、無関係なものまで
    巻き込む。フルパスが一致したものだけを止める。
    """
    if sys.platform != "win32":
        return []
    target = str(path).lower()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []
    found = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            actual = _image_path(entry.th32ProcessID)
            if actual and actual.lower() == target:
                found.append(entry.th32ProcessID)
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _stop(path, label):
    pids = _pids_of(path)
    if not pids:
        return f"{label}: もともと動いていない"
    stopped = 0
    for pid in pids:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(handle, 0):
                stopped += 1
        finally:
            kernel32.CloseHandle(handle)
    return f"{label}: {stopped}件を止めた"


def _aivis_exe():
    return config.AIVIS_DIR / "AivisSpeech-Engine" / "run.exe"


def _ollama_exe():
    return config.OLLAMA_DIR / "ollama.exe"


def _start_aivis():
    from ..launcher import start_aivis

    start_aivis(quiet=True)
    return "音声エンジン: 起こした"


def _start_ollama():
    from ..launcher import start_ollama

    start_ollama(quiet=True)
    return "Ollama: 起こした"


def _restart_self():
    from ..serve.web import RESTART_EXIT_CODE

    # 返答を届けきってから落ちる。start.bat かトレイ常駐が上げ直す。
    threading.Timer(RESTART_DELAY, lambda: os._exit(RESTART_EXIT_CODE)).start()
    return "ことは: 入れ直す"


ACTIONS = {
    "音声エンジンを起こす": _start_aivis,
    "音声エンジンを止める": lambda: _stop(_aivis_exe(), "音声エンジン"),
    "Ollamaを起こす": _start_ollama,
    "Ollamaを止める": lambda: _stop(_ollama_exe(), "Ollama"),
    "自分を入れ直す": _restart_self,
}


def names():
    return list(ACTIONS)


def offer() -> str:
    """プロンプトに載せる、頼まれたらできることの一覧。"""
    if not config.ACTIONS_ENABLED:
        return ""
    return (
        "頼まれたらできること: " + " / ".join(names()) + "\n"
        "（やるときは返答の最後に [DO: 名前] を付ける。名前は上のとおりに書く。"
        "頼まれていないのに付けない）"
    )


def run(name: str) -> str:
    """名前の通りの操作をする。表にないものは何もしない。"""
    if not config.ACTIONS_ENABLED:
        log(f"止められているので何もしない: {name}")
        return ""
    action = ACTIONS.get(name)
    if action is None:
        log(f"知らない操作なので何もしない: {name}")
        return ""
    try:
        result = action()
    except Exception as error:      # 操作の失敗で会話を止めない
        log(f"{name}: 失敗した（{error!r}）")
        return ""
    log(f"{name} -> {result}")
    return result
