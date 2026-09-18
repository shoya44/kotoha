"""ふきだしと、一行の入力。

⚠️ **ここはレイヤードウィンドウにしない。** GDIで文字を描くと不透明さが0のまま
残るので、透過する窓に書いた文字は見えなくなる。ふきだしは**不透明な普通の窓**に
して、角だけリージョンで丸める。ドット本体だけが透過する。

立て札（外出中）も同じ窓で出す。絵を用意しなくてよく、**押せる場所**にもなる。
"""

import ctypes
import ctypes.wintypes as w

from .window import (WNDCLASS, WNDPROC, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP,
                     SW_HIDE, SW_SHOWNOACTIVATE, HWND_TOPMOST, SWP_NOACTIVATE,
                     WM_CLOSE, WM_DESTROY, WM_LBUTTONUP, gdi32, kernel32, user32, _signature,
                     LRESULT)

WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
ES_AUTOHSCROLL = 0x0080
WM_PAINT, WM_SETFONT, WM_GETTEXT, WM_SETTEXT = 0x000F, 0x0030, 0x000D, 0x000C
WM_GETTEXTLENGTH = 0x000E
DT_WORDBREAK, DT_CALCRECT, DT_NOPREFIX = 0x0010, 0x0400, 0x0800
TRANSPARENT = 1
DEFAULT_CHARSET = 1

# 見た目。淡い紙に濃い字。どんな壁紙の上でも読める濃さにしてある。
PAPER = 0x00FAFCFC          # COLORREF は 0x00BBGGRR
INK = 0x002B2123
FADED = 0x00807878
MAX_WIDTH = 280
PADDING = 11
INPUT_HEIGHT = 26
GAP = 8


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", w.HDC), ("fErase", w.BOOL), ("rcPaint", w.RECT),
                ("fRestore", w.BOOL), ("fIncUpdate", w.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


_signature(user32.BeginPaint, w.HDC, w.HWND, ctypes.POINTER(PAINTSTRUCT))
_signature(user32.EndPaint, w.BOOL, w.HWND, ctypes.POINTER(PAINTSTRUCT))
_signature(user32.FillRect, ctypes.c_int, w.HDC, ctypes.POINTER(w.RECT), w.HBRUSH)
_signature(user32.DrawTextW, ctypes.c_int, w.HDC, w.LPCWSTR, ctypes.c_int,
           ctypes.POINTER(w.RECT), w.UINT)
_signature(user32.SetWindowRgn, ctypes.c_int, w.HWND, w.HANDLE, w.BOOL)
_signature(user32.SendMessageW, LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
_signature(user32.SetFocus, w.HWND, w.HWND)
_signature(gdi32.CreateSolidBrush, w.HBRUSH, w.DWORD)
_signature(gdi32.CreateRoundRectRgn, w.HANDLE, ctypes.c_int, ctypes.c_int,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
# 高さ・幅・傾き・向き・太さ・斜体・下線・打ち消し・文字集合・精度3つ・字送り・書体名。
_signature(gdi32.CreateFontW, w.HANDLE,
           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
           w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
           w.LPCWSTR)
_signature(gdi32.SetTextColor, w.DWORD, w.HDC, w.DWORD)
_signature(gdi32.SetBkMode, ctypes.c_int, w.HDC, ctypes.c_int)
_signature(gdi32.SelectObject, w.HGDIOBJ, w.HDC, w.HGDIOBJ)


class Bubble:
    """ことはの言葉と、こちらの一行。押されたら知らせる。"""

    CLASS_NAME = "KotohaBubble"

    def __init__(self, on_click=None, on_send=None):
        self.on_click = on_click or (lambda: None)
        self.on_send = on_send or (lambda text: None)
        self.text = ""
        self.width = self.height = 0
        self.asking = False
        self._proc = WNDPROC(self._handle)
        self._brush = gdi32.CreateSolidBrush(PAPER)
        self._font = gdi32.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, DEFAULT_CHARSET,
                                       0, 0, 0, 0, "Yu Gothic UI")
        self._register()
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST, self.CLASS_NAME, "ことは",
            WS_POPUP, 0, 0, 10, 10, None, None, kernel32.GetModuleHandleW(None), None)
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | ES_AUTOHSCROLL, 0, 0, 10, INPUT_HEIGHT,
            self.hwnd, None, kernel32.GetModuleHandleW(None), None)
        user32.SendMessageW(self.edit, WM_SETFONT, self._font, 1)

    def _register(self) -> None:
        klass = WNDCLASS()
        klass.lpfnWndProc = self._proc
        klass.hInstance = kernel32.GetModuleHandleW(None)
        klass.lpszClassName = self.CLASS_NAME
        klass.hbrBackground = self._brush
        klass.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32512))
        user32.RegisterClassW(ctypes.byref(klass))

    # --- 出す・消す ---

    def say(self, text: str, anchor, asking: bool = False) -> None:
        """言葉を出す。anchor は (中心X, 上端Y)。その上に置く。"""
        self.text = text
        self.asking = asking
        inner = self._measure(text)
        self.width = inner[0] + PADDING * 2
        self.height = inner[1] + PADDING * 2 + (INPUT_HEIGHT + GAP if asking else 0)
        x = max(8, anchor[0] - self.width // 2)
        y = max(8, anchor[1] - self.height - 6)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.width, self.height,
                            SWP_NOACTIVATE)
        region = gdi32.CreateRoundRectRgn(0, 0, self.width + 1, self.height + 1, 14, 14)
        user32.SetWindowRgn(self.hwnd, region, True)
        if asking:
            user32.SetWindowPos(self.edit, None, PADDING, self.height - PADDING - INPUT_HEIGHT,
                                self.width - PADDING * 2, INPUT_HEIGHT, SWP_NOACTIVATE)
            user32.ShowWindow(self.edit, SW_SHOWNOACTIVATE)
        else:
            user32.ShowWindow(self.edit, SW_HIDE)
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.InvalidateRect(self.hwnd, None, True)

    def focus_input(self) -> None:
        """打てるようにする。**ここだけは前面を取る**（そうしないと字が入らない）。"""
        user32.SetForegroundWindow(self.hwnd)
        user32.SetFocus(self.edit)

    def typed(self) -> str:
        length = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.SendMessageW(self.edit, WM_GETTEXT, length + 1,
                            ctypes.cast(buffer, ctypes.c_void_p).value)
        return buffer.value.strip()

    def clear_input(self) -> None:
        user32.SendMessageW(self.edit, WM_SETTEXT, 0,
                            ctypes.cast(ctypes.create_unicode_buffer(""),
                                        ctypes.c_void_p).value)

    def hide(self) -> None:
        self.asking = False
        user32.ShowWindow(self.edit, SW_HIDE)
        user32.ShowWindow(self.hwnd, SW_HIDE)

    def shown(self) -> bool:
        return bool(user32.IsWindowVisible(self.hwnd))

    # --- 描く ---

    def _measure(self, text: str):
        dc = user32.GetDC(self.hwnd)
        old = gdi32.SelectObject(dc, self._font)
        rect = w.RECT(0, 0, MAX_WIDTH, 0)
        user32.DrawTextW(dc, text, -1, ctypes.byref(rect),
                         DT_WORDBREAK | DT_CALCRECT | DT_NOPREFIX)
        gdi32.SelectObject(dc, old)
        user32.ReleaseDC(self.hwnd, dc)
        return max(rect.right, 90), max(rect.bottom, 18)

    def _handle(self, hwnd, message, wparam, lparam):
        if message == WM_PAINT:
            paint = PAINTSTRUCT()
            dc = user32.BeginPaint(hwnd, ctypes.byref(paint))
            rect = w.RECT(0, 0, self.width, self.height)
            user32.FillRect(dc, ctypes.byref(rect), self._brush)
            old = gdi32.SelectObject(dc, self._font)
            gdi32.SetBkMode(dc, TRANSPARENT)
            gdi32.SetTextColor(dc, INK if self.text else FADED)
            area = w.RECT(PADDING, PADDING, self.width - PADDING,
                          self.height - PADDING - (INPUT_HEIGHT + GAP if self.asking else 0))
            user32.DrawTextW(dc, self.text, -1, ctypes.byref(area),
                             DT_WORDBREAK | DT_NOPREFIX)
            gdi32.SelectObject(dc, old)
            user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        if message == WM_LBUTTONUP:
            self.on_click()
            return 0
        if message in (WM_DESTROY, WM_CLOSE):
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)
