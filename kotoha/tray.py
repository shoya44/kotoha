"""タスクトレイに住みつき、ことはと周りのツールの面倒を見る。

画面を持たないので pythonw.exe から起動する。そうするとコンソールが1枚も
出ない。本体（uvicorn）は子プロセスにして見張り、落ちたら上げ直す。画面から
の再起動（終了コード42）も、これまで kotoha.bat が見ていたのをここで引き取る。

トレイまわりは ctypes で直に触っている。pystray と Pillow を入れれば短く
書けるが、依存3つで動いている構成に10MB超を足す理由がない。
"""

import ctypes
import ctypes.wintypes as w
import os
import pathlib
import subprocess
import sys
import threading
import time
import webbrowser

from . import autostart, config
from .config import RESTART_EXIT_CODE

_STATIC = config.BASE_DIR / "kotoha" / "serve" / "static"
ICON_PATH = _STATIC / "kotoha.ico"
# 止まっているときは沈んだ色にする。かざさなくても分かるように。
ICON_OFF_PATH = _STATIC / "kotoha_off.ico"
LOG_PATH = config.BASE_DIR / "data" / "tray.log"
# 同じものを二重に常駐させない。名前は書き換えないこと。
MUTEX_NAME = "kotoha-tray-single-instance"
# 落ちたときに上げ直すまでの間。すぐ上げ直すと、壊れていたとき暴れ続ける。
RESPAWN_WAIT = 5.0
# 前の常駐が終わりかけのとき、印が消えるのを待つ長さ（秒）。終わるときは本体と姿を
# 止めて待つので数秒かかる。終えた直後に上げ直すと、黙って引き下がっていた。
PREVIOUS_WAIT = 10.0
# 自分で上げていないことはを見守るときの、様子見の間隔。
WATCH_WAIT = 5.0

WM_DESTROY, WM_COMMAND, WM_TRAY = 0x0002, 0x0111, 0x0400 + 1
WM_LBUTTONUP, WM_RBUTTONUP, WM_LBUTTONDBLCLK = 0x0202, 0x0205, 0x0203
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
MF_STRING, MF_SEPARATOR, MF_DEFAULT, MF_GRAYED = 0x0000, 0x0800, 0x1000, 0x0001
MF_POPUP = 0x0010
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040

ID_OPEN, ID_RESTART, ID_QUIT = 1, 3, 4
# 頼まれごとはここから番号を振る。actions の一覧と並びを合わせる。
ID_ACTION_BASE = 100
# 様子の書き換え間隔。メニューを開いた瞬間に調べると、止まっているとき待たされる。
STATUS_WAIT = 10.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# スリープしないよう頼む合図。ES_DISPLAY_REQUIRED は付けない（画面は消してよい）。
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
kernel32.SetThreadExecutionState.argtypes = [w.DWORD]
kernel32.SetThreadExecutionState.restype = w.DWORD

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]


def _signature(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)


_signature(user32.DefWindowProcW, LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_signature(user32.CreateWindowExW, w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
_signature(user32.LoadImageW, w.HANDLE, w.HINSTANCE, w.LPCWSTR, w.UINT,
           ctypes.c_int, ctypes.c_int, w.UINT)
_signature(user32.CreatePopupMenu, w.HMENU)
_signature(user32.AppendMenuW, w.BOOL, w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
_signature(user32.TrackPopupMenu, ctypes.c_int, w.HMENU, w.UINT, ctypes.c_int,
           ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID)
_signature(user32.DestroyMenu, w.BOOL, w.HMENU)
_signature(user32.DestroyWindow, w.BOOL, w.HWND)
_signature(user32.SetForegroundWindow, w.BOOL, w.HWND)
_signature(user32.PostMessageW, w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_signature(user32.FindWindowW, w.HWND, w.LPCWSTR, w.LPCWSTR)
_signature(user32.RegisterWindowMessageW, w.UINT, w.LPCWSTR)
_signature(user32.GetMessageW, w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
_signature(kernel32.GetModuleHandleW, w.HMODULE, w.LPCWSTR)
_signature(kernel32.CreateMutexW, w.HANDLE, w.LPVOID, w.BOOL, w.LPCWSTR)
_signature(kernel32.CloseHandle, w.BOOL, w.HANDLE)
_signature(shell32.Shell_NotifyIconW, w.BOOL, w.DWORD, ctypes.POINTER(NOTIFYICONDATA))


def log(message: str) -> None:
    """画面が無いので、起きたことはここに残す。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


class Supervisor:
    """ことは本体を動かし続ける。落ちたら上げ直し、42なら作り直す。"""

    def __init__(self, on_change=None):
        self.process = None
        self.stopping = threading.Event()
        self.on_change = on_change or (lambda: None)
        self._thread = None

    def mine(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def serving(self) -> bool:
        """誰かがことはを動かしているか。kotoha.bat から上がっていることもある。"""
        from .launcher import is_kotoha, local_url

        url, _ = local_url(config.WEB_HOST, config.WEB_PORT)
        return is_kotoha(url)

    def alive(self) -> bool:
        return self.mine() or self.serving()

    def spawn(self):
        # 起動時にブラウザーを開かない。開くのはトレイの役目になった。
        environment = dict(os.environ, KOTOHA_BROWSER_AUTO_OPEN="false")
        return subprocess.Popen(
            [sys.executable, "-m", "kotoha.launcher"],
            cwd=str(config.BASE_DIR),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _loop(self):
        while not self.stopping.is_set():
            if self.serving():
                # すでに動いている。kotoha.bat から上げた本体を横取りしない。
                # ここで子を作ると、ポートが塞がっていて即終了し、上げ直し続ける。
                self.stopping.wait(WATCH_WAIT)
                continue
            try:
                self.process = self.spawn()
            except OSError as error:
                log(f"本体を起動できない: {error}")
                self.stopping.wait(RESPAWN_WAIT)
                continue
            self.on_change()
            code = self.process.wait()
            self.process = None
            self.on_change()
            if self.stopping.is_set():
                return
            if code == RESTART_EXIT_CODE:
                log("再起動の合図を受けた")
                continue
            log(f"本体が終了した（コード {code}）。{RESPAWN_WAIT:.0f}秒後に上げ直す")
            self.stopping.wait(RESPAWN_WAIT)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def restart(self):
        """いま動いているものを止める。上げ直すのは見張りの仕事。"""
        if self.mine():
            self.process.terminate()

    def stop(self):
        self.stopping.set()
        if self.mine():
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Figure:
    """ことはの姿（タスクトレイのドット）を動かし続ける。

    本体と同じように子プロセスにして見張る。**落ちてもトレイと本体は無事**で、
    ここだけ上げ直せばよい。姿を出さない設定なら、何もしない。
    """

    def __init__(self):
        self.process = None
        self.stopping = threading.Event()
        self._thread = None

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def spawn(self):
        runner = pathlib.Path(sys.executable).with_name("pythonw.exe")
        return subprocess.Popen(
            [str(runner if runner.exists() else sys.executable),
             str(config.BASE_DIR / "scripts" / "mascot.pyw")],
            cwd=str(config.BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _loop(self):
        while not self.stopping.is_set():
            try:
                self.process = self.spawn()
            except OSError as error:
                log(f"姿を出せない: {error}")
                self.stopping.wait(RESPAWN_WAIT)
                continue
            code = self.process.wait()
            self.process = None
            if self.stopping.is_set():
                return
            # しまわれた（自分で終わった）ときは、上げ直さない。
            if code == 0:
                log("姿をしまった")
                return
            log(f"姿が落ちた（コード {code}）。{RESPAWN_WAIT:.0f}秒後に出し直す")
            self.stopping.wait(RESPAWN_WAIT)

    def start(self):
        if not config.MASCOT_ENABLED:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.stopping.set()
        if self.alive():
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


class Status:
    """ことはと周りの様子。メニューを開くたびに調べると、止まっているとき待つ。

    向こうが応じないときは1秒ずつ待たされる。裏で先に調べておき、
    メニューには覚えた結果を出す。
    """

    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.lines = ["調べています…"]
        self.stopping = threading.Event()
        self.on_change = lambda: None

    def probe(self):
        from .launcher import aivis_is_up, ollama_is_up

        def mark(ok):
            return "動いている" if ok else "止まっている"

        alive = self.supervisor.alive()
        note = "" if self.supervisor.mine() or not alive else "（別に上がっているもの）"
        self.lines = [
            f"ことは: {mark(alive)}{note}",
            f"音声エンジン: {mark(aivis_is_up())}",
            f"Ollama: {mark(ollama_is_up())}",
        ]
        self.on_change()

    def _loop(self):
        while not self.stopping.is_set():
            try:
                self.probe()
            except Exception as error:      # 様子見で常駐を落とさない
                log(f"様子を調べられない: {error}")
            self.stopping.wait(STATUS_WAIT)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.stopping.set()


class Tray:
    def __init__(self, supervisor, status=None):
        self.supervisor = supervisor
        self.status = status or Status(supervisor)
        self.hwnd = None
        self.icon = None
        # WNDPROC は参照を持っておかないと回収され、落ちる。
        self._proc = WNDPROC(self._on_message)
        self._added = False
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

    # ---- 窓とアイコン ----

    def create(self):
        instance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASS()
        cls.lpfnWndProc = self._proc
        cls.hInstance = instance
        cls.lpszClassName = "KotohaTray"
        if not user32.RegisterClassW(ctypes.byref(cls)):
            raise OSError(f"窓の種別を登録できない: {ctypes.get_last_error()}")
        self.hwnd = user32.CreateWindowExW(0, "KotohaTray", "ことは", 0, 0, 0, 0, 0,
                                           None, None, instance, None)
        if not self.hwnd:
            raise OSError(f"窓を作れない: {ctypes.get_last_error()}")
        self.icons = {
            state: user32.LoadImageW(None, str(path), IMAGE_ICON, 0, 0,
                                     LR_LOADFROMFILE | LR_DEFAULTSIZE)
            for state, path in ((True, ICON_PATH), (False, ICON_OFF_PATH))
        }
        self.icon = self.icons[True]
        self._notify(NIM_ADD)
        self._added = True

    def _data(self, flags):
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self.icon
        return data

    def _notify(self, action, flags=NIF_MESSAGE | NIF_ICON | NIF_TIP, **text):
        data = self._data(flags)
        data.szTip = text.get("tip", "ことは")
        if flags & NIF_INFO:
            data.szInfoTitle = text.get("title", "ことは")
            data.szInfo = text.get("info", "")
        shell32.Shell_NotifyIconW(action, ctypes.byref(data))

    def refresh_tip(self):
        """アイコンと、かざしたときに出る文字。ここには必ず出せる。

        風船（通知）は Windows 11 だと集中モードや通知設定で黙って消される。
        受け付けられても表示されないので、様子はこことメニューに出す。
        """
        self.icon = self.icons.get(bool(self.supervisor.alive()), self.icon)
        self._notify(NIM_MODIFY, NIF_ICON | NIF_TIP,
                     tip="\n".join(self.status.lines)[:127])

    # ---- 操作 ----

    def open_chat(self):
        from .launcher import local_url

        url, _ = local_url(config.WEB_HOST, config.WEB_PORT)
        webbrowser.open(url)

    def menu(self):
        handle = user32.CreatePopupMenu()
        user32.AppendMenuW(handle, MF_STRING | MF_DEFAULT, ID_OPEN, "ことはを開く")
        user32.AppendMenuW(handle, MF_SEPARATOR, 0, None)
        # 押せない行として並べる。押させるより、開いた時点で見えるほうが早い。
        for line in self.status.lines:
            user32.AppendMenuW(handle, MF_STRING | MF_GRAYED, 0, line)
        helpers = self.helper_menu()
        if helpers:
            user32.AppendMenuW(handle, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(handle, MF_POPUP, helpers, "手を貸す")
        user32.AppendMenuW(handle, MF_SEPARATOR, 0, None)
        # 自分で上げた本体でなければ、入れ直せない。押せないことを見せておく。
        restart = MF_STRING if self.supervisor.mine() else MF_STRING | MF_GRAYED
        user32.AppendMenuW(handle, restart, ID_RESTART, "入れ直す（再起動）")
        user32.AppendMenuW(handle, MF_STRING, ID_QUIT, "終わる")

        point = w.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # これを挟まないと、メニューの外を押しても閉じない。
        user32.SetForegroundWindow(self.hwnd)
        choice = user32.TrackPopupMenu(handle, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                       point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(handle)
        if choice:
            self.command(choice)

    def helper_menu(self):
        """頼まれたらできることを、そのまま押せるようにする。

        ことはに言えばやってくれるが、手で押したいときもある。一覧は
        actions と同じものを使うので、片方だけ増えることがない。
        """
        from .talk import actions

        names = [name for name in actions.names() if name != "自分を入れ直す"]
        if not (config.ACTIONS_ENABLED and names):
            return None
        handle = user32.CreatePopupMenu()
        for offset, name in enumerate(names):
            user32.AppendMenuW(handle, MF_STRING, ID_ACTION_BASE + offset, name)
        self._helpers = names
        return handle

    def command(self, choice):
        if choice >= ID_ACTION_BASE:
            from .talk import actions

            index = choice - ID_ACTION_BASE
            names = getattr(self, "_helpers", [])
            if index < len(names):
                log(f"メニューから: {names[index]} -> {actions.run(names[index])}")
            return
        if choice == ID_OPEN:
            self.open_chat()
        elif choice == ID_RESTART:
            self.supervisor.restart()
        elif choice == ID_QUIT:
            user32.DestroyWindow(self.hwnd)

    # ---- 窓からの知らせ ----

    def _on_message(self, hwnd, message, wparam, lparam):
        try:
            return self._handle(hwnd, message, wparam, lparam)
        except Exception as error:
            # ここで投げると ctypes に握りつぶされ、画面が無いので誰も気づけない。
            log(f"窓の処理で失敗: {error!r}")
            return 0

    def _handle(self, hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            if lparam in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                self.open_chat()
            elif lparam == WM_RBUTTONUP:
                self.menu()
            return 0
        if message == WM_COMMAND:
            self.command(wparam & 0xFFFF)
            return 0
        if message == self._taskbar_created and self._added:
            # エクスプローラーが再起動すると、アイコンごと消える。置き直す。
            self._notify(NIM_ADD)
            return 0
        if message == WM_DESTROY:
            self.remove()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def remove(self):
        if self._added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))
            self._added = False

    def loop(self):
        message = w.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))


def already_running(wait: float = PREVIOUS_WAIT) -> bool:
    """二重常駐を防ぐ。掴んだ印はプロセスが終わるまで持ったままにする。

    同時に立ち上がると印の取り合いになるので、窓の有無も見る。どちらかが
    見つかれば、もう1つは引き下がる。

    **ただし少し待ってから決める。** 前の常駐は「終えた」と書いてから本当に
    消えるまで数秒かかる。そのあいだに上げると印が残っていて、何も出ないまま
    終わっていた（2026-09-23、終えた3秒後のダブルクリック）。
    """
    deadline = time.monotonic() + wait
    while _taken():
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.5)
    return False


def _taken() -> bool:
    """いま印か窓があるか。無ければ印を掴んだまま False を返す。"""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183 or user32.FindWindowW("KotohaTray", None):  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(_mutex)
        _mutex = None
        return True
    return False


_mutex = None


def keep_awake(on: bool) -> None:
    """ことはが常駐しているあいだ、PCを寝かせない。

    **出先から繋がらなくなる一番の理由がスリープ。** 寝てしまうと Tailscale が
    応じず、外から起こす手立てが無い（Wake-on-LANの合図は同じLANからしか
    届かない）。頼むだけで、**電源設定そのものは書き換えない。** ことはを
    終えれば元に戻る。画面は消えてよいので、そちらは頼まない。

    合図は呼んだ糸に効く。**消えない糸から呼ぶこと**（ここでは主糸）。
    """
    if not config.KEEP_AWAKE:
        return
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS
    if not kernel32.SetThreadExecutionState(flags):
        log("スリープ抑止を頼めなかった")


def main():
    if sys.platform != "win32":
        raise SystemExit("トレイ常駐はWindows専用です。")
    os.chdir(config.BASE_DIR)
    if already_running():
        log("すでに常駐しているので、何もしない")
        return
    log("常駐をはじめる")
    # 上がれたいま、次に上がる道が古くなっていないか見ておく。
    autostart.repair(say=log)

    supervisor = Supervisor()
    figure = Figure()
    status = Status(supervisor)
    tray = Tray(supervisor, status)
    tray.create()
    supervisor.on_change = tray.refresh_tip
    status.on_change = tray.refresh_tip
    supervisor.start()
    figure.start()
    status.start()
    tray.refresh_tip()
    keep_awake(True)
    try:
        tray.loop()
    finally:
        keep_awake(False)
        status.stop()
        figure.stop()
        supervisor.stop()
        tray.remove()
        log("常駐を終えた")


if __name__ == "__main__":
    main()
