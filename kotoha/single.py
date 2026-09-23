"""同じものを二つ動かさないための印。

本体（uvicorn）は、ポートを叩いて「もう居るか」を見ていた。だがその問いは
ネットワーク越しで、機械が重いと1秒で答えが返らず「居ない」と誤る。誤ると
もう1本上がろうとして、ポートで弾かれて落ちる。それを見張り番が上げ直し、
また誤る。印なら答えは即座で、間違えない。

Windows の名前付きミューテックスを使う。掴んだ印はプロセスが終わるまで持った
ままにする。Windows 以外では印が無いので、掴めたことにする（テスト用）。
"""

import ctypes
import sys

BODY = "kotoha-body-single-instance"

ERROR_ALREADY_EXISTS = 183
_held = {}


def claim(name: str) -> bool:
    """印を掴む。誰かが持っていれば False。掴んだ印は返さない。"""
    if sys.platform != "win32":
        return True
    if name in _held:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # 印が作れない環境では、止めるより動かす
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _held[name] = handle
    return True


def taken(name: str) -> bool:
    """誰かが印を持っているか、覗くだけ。自分では持たない。"""
    if sys.platform != "win32":
        return False
    if name in _held:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    kernel32.CloseHandle(handle)
    return exists
