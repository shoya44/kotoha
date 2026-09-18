"""画面に立つ枠なしの窓。ctypes で直に触る。

**絵をそのままの形で出す**ために、レイヤードウィンドウに `UpdateLayeredWindow`
で転送する。四角い窓に絵を描く普通のやり方だと、髪の外側の透明なところが
背景色で塗られてしまう。

タスクバーにもAlt+Tabにも出さない（`WS_EX_TOOLWINDOW`）。押しても前面を
奪わない（`WS_EX_NOACTIVATE`）。**透明なところのクリックは後ろへ通す**
（`WM_NCHITTEST` で、その座標の不透明さを見る）。

tray.py と同じ書き方に揃えてある。読む人が新しい作法を覚えなくていいように。
"""

import ctypes
import ctypes.wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x00080000, 0x00000080, 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
ULW_ALPHA = 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0x00, 0x01
HTTRANSPARENT, HTCLIENT = -1, 1
WM_DESTROY, WM_NCHITTEST, WM_TIMER = 0x0002, 0x0084, 0x0113
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_RBUTTONUP, WM_CLOSE = 0x0205, 0x0010
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
SWP_NOSIZE, SWP_NOACTIVATE, SWP_NOZORDER = 0x0001, 0x0010, 0x0004
HWND_TOPMOST = -1
SPI_GETWORKAREA = 0x0030
# 押したまま動かした距離がこれを超えたら、押したのではなく運んだと見なす。
DRAG_SLOP = 4


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
                ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


def _signature(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)


_signature(user32.DefWindowProcW, LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_signature(user32.CreateWindowExW, w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
_signature(user32.UpdateLayeredWindow, w.BOOL, w.HWND, w.HDC, ctypes.POINTER(w.POINT),
           ctypes.POINTER(ctypes.c_long * 2), w.HDC, ctypes.POINTER(w.POINT),
           w.DWORD, ctypes.POINTER(BLENDFUNCTION), w.DWORD)
_signature(user32.SetWindowPos, w.BOOL, w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
           ctypes.c_int, ctypes.c_int, w.UINT)
_signature(user32.GetDC, w.HDC, w.HWND)
_signature(user32.ReleaseDC, ctypes.c_int, w.HWND, w.HDC)
_signature(user32.ShowWindow, w.BOOL, w.HWND, ctypes.c_int)
_signature(user32.SetCapture, w.HWND, w.HWND)
_signature(user32.ReleaseCapture, w.BOOL)
_signature(user32.GetCursorPos, w.BOOL, ctypes.POINTER(w.POINT))
_signature(user32.CreatePopupMenu, w.HMENU)
_signature(user32.AppendMenuW, w.BOOL, w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
_signature(user32.TrackPopupMenu, ctypes.c_int, w.HMENU, w.UINT, ctypes.c_int,
           ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID)
_signature(user32.DestroyMenu, w.BOOL, w.HMENU)
_signature(user32.SetForegroundWindow, w.BOOL, w.HWND)
_signature(user32.SystemParametersInfoW, w.BOOL, w.UINT, w.UINT, w.LPVOID, w.UINT)
_signature(user32.SetTimer, ctypes.c_size_t, w.HWND, ctypes.c_size_t, w.UINT, w.LPVOID)
_signature(user32.GetMessageW, w.BOOL, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
_signature(user32.TranslateMessage, w.BOOL, ctypes.POINTER(w.MSG))
_signature(user32.DispatchMessageW, LRESULT, ctypes.POINTER(w.MSG))
_signature(user32.RegisterClassW, w.WORD, ctypes.POINTER(WNDCLASS))
_signature(user32.LoadCursorW, w.HANDLE, w.HINSTANCE, w.LPCWSTR)
_signature(user32.InvalidateRect, w.BOOL, w.HWND, w.LPVOID, w.BOOL)
_signature(user32.IsWindowVisible, w.BOOL, w.HWND)
_signature(user32.GetKeyState, ctypes.c_short, ctypes.c_int)
_signature(user32.PostQuitMessage, None, ctypes.c_int)
_signature(user32.DestroyWindow, w.BOOL, w.HWND)
_signature(gdi32.CreateCompatibleDC, w.HDC, w.HDC)
_signature(gdi32.CreateDIBSection, w.HBITMAP, w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT,
           ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD)
_signature(gdi32.SelectObject, w.HGDIOBJ, w.HDC, w.HGDIOBJ)
_signature(gdi32.DeleteObject, w.BOOL, w.HGDIOBJ)
_signature(gdi32.DeleteDC, w.BOOL, w.HDC)


def work_area():
    """タスクバーを除いた画面の広さ。ドットはこの中に立つ。"""
    rect = w.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


class Dot:
    """ことはの姿が立つ窓。絵の差し替えと、押された・運ばれたの知らせだけを持つ。

    どの絵を出すかは決めない。**決めるのは脳**で、ここは渡されたものを描く。
    """

    CLASS_NAME = "KotohaMascot"

    def __init__(self, width: int, height: int, on_click=None, on_menu=None,
                 on_moved=None, on_tick=None):
        self.width, self.height = width, height
        self.on_click = on_click or (lambda: None)
        self.on_menu = on_menu or (lambda x, y: None)
        self.on_moved = on_moved or (lambda x, y: None)
        self.on_tick = on_tick or (lambda: None)
        self.x = self.y = 0
        self.frame = None
        self._dragging = False
        self._moved = False
        self._grab = (0, 0)
        self._proc = WNDPROC(self._handle)     # 参照を持ち続けないと、回収されて落ちる
        self._register()
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            self.CLASS_NAME, "ことは", WS_POPUP, 0, 0, width, height,
            None, None, kernel32.GetModuleHandleW(None), None)
        self._make_canvas()

    def _register(self) -> None:
        klass = WNDCLASS()
        klass.lpfnWndProc = self._proc
        klass.hInstance = kernel32.GetModuleHandleW(None)
        klass.lpszClassName = self.CLASS_NAME
        klass.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32512))   # IDC_ARROW
        user32.RegisterClassW(ctypes.byref(klass))

    def _make_canvas(self) -> None:
        """転送用の下地。**上から下へ並ぶ向き**にしておく（高さを負で渡す）。"""
        screen = user32.GetDC(None)
        self.dc = gdi32.CreateCompatibleDC(screen)
        user32.ReleaseDC(None, screen)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self.width
        info.bmiHeader.biHeight = -self.height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(self.dc, ctypes.byref(info), 0,
                                             ctypes.byref(self.bits), None, 0)
        self.old_bitmap = gdi32.SelectObject(self.dc, self.bitmap)

    # --- 見た目 ---

    def draw(self, frame, lift: int = 0, opacity: int = 255) -> None:
        """1枚を転送する。lift は上に浮かせるドット数（呼吸）。"""
        if frame is None:
            return
        self.frame = frame
        ctypes.memmove(self.bits, bytes(frame.bgra), len(frame.bgra))
        position = w.POINT(self.x, self.y - lift)
        size = (ctypes.c_long * 2)(self.width, self.height)
        source = w.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, opacity, AC_SRC_ALPHA)
        screen = user32.GetDC(None)
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(position),
                                   ctypes.byref(size), self.dc, ctypes.byref(source),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        user32.ReleaseDC(None, screen)

    def place(self, x: int, y: int) -> None:
        self.x, self.y = int(x), int(y)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, self.x, self.y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)

    def visible(self, shown: bool) -> None:
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE if shown else SW_HIDE)

    def center(self):
        return self.x + self.width // 2, self.y + self.height // 2

    # --- 押された・運ばれた ---

    def _hit(self, screen_x: int, screen_y: int) -> bool:
        """その座標に絵があるか。無ければクリックは後ろのアプリへ通す。"""
        if self.frame is None:
            return False
        return self.frame.opaque_at(screen_x - self.x, screen_y - self.y)

    def _handle(self, hwnd, message, wparam, lparam):
        if message == WM_NCHITTEST:
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short(lparam >> 16).value
            return HTCLIENT if self._hit(x, y) else HTTRANSPARENT
        if message == WM_LBUTTONDOWN:
            point = w.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            self._dragging, self._moved = True, False
            self._grab = (point.x - self.x, point.y - self.y)
            user32.SetCapture(hwnd)
            return 0
        if message == WM_MOUSEMOVE and self._dragging:
            point = w.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            x, y = point.x - self._grab[0], point.y - self._grab[1]
            if abs(x - self.x) + abs(y - self.y) > DRAG_SLOP:
                self._moved = True
            self.place(x, y)
            return 0
        if message == WM_LBUTTONUP and self._dragging:
            self._dragging = False
            user32.ReleaseCapture()
            if self._moved:
                self.on_moved(self.x, self.y)
            else:
                self.on_click()
            return 0
        if message == WM_TIMER:
            self.on_tick()
            return 0
        if message == WM_RBUTTONUP:
            point = w.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            self.on_menu(point.x, point.y)
            return 0
        if message in (WM_DESTROY, WM_CLOSE):
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def menu(self, items, x: int, y: int):
        """右クリックの品書き。選ばれた番号を返す。選ばれなければ0。

        items は (番号, 文字, 押せるか) の並び。番号0は区切り線。
        """
        handle = user32.CreatePopupMenu()
        for number, label, enabled in items:
            if not number:
                user32.AppendMenuW(handle, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING | (0 if enabled else MF_GRAYED)
            user32.AppendMenuW(handle, flags, number, label)
        # これが無いと、ほかを押したときに品書きが消えない。
        user32.SetForegroundWindow(self.hwnd)
        chosen = user32.TrackPopupMenu(handle, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                       x, y, 0, self.hwnd, None)
        user32.DestroyMenu(handle)
        return chosen

    def close(self) -> None:
        gdi32.SelectObject(self.dc, self.old_bitmap)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.dc)
        user32.DestroyWindow(self.hwnd)
