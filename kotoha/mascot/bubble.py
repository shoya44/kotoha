"""ふきだしと、一行の入力。

⚠️ **絵と同じやり方（UpdateLayeredWindow）は使わない。** GDIで文字を描くと
不透明さが0のまま残るので、その道で透過させると文字が消える。ここは普通に
描いたうえで、**窓全体をまとめて薄くする**（SetLayeredWindowAttributes）。
文字も背景も同じだけ透けるので、字は消えない。

見た目は会話画面に合わせてある（`static/style.css` の色をそのまま使う）。
角はリージョンで丸め、縁を1本引く。

立て札（外出中）も同じ窓で出す。絵を用意しなくてよく、**押せる場所**にもなる。
"""

import ctypes
import ctypes.wintypes as w

from .window import (WNDCLASS, WNDPROC, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
                     WS_POPUP, SW_HIDE, SW_SHOWNOACTIVATE, HWND_TOPMOST, SWP_NOACTIVATE,
                     WM_CLOSE, WM_DESTROY, WM_LBUTTONUP, gdi32, kernel32, user32, _signature,
                     LRESULT, work_area)

WS_CHILD, WS_VISIBLE, WS_BORDER = 0x40000000, 0x10000000, 0x00800000
ES_AUTOHSCROLL = 0x0080
WM_PAINT, WM_SETFONT, WM_GETTEXT, WM_SETTEXT = 0x000F, 0x0030, 0x000D, 0x000C
WM_GETTEXTLENGTH = 0x000E
WM_CTLCOLOREDIT = 0x0133
EM_SETCUEBANNER = 0x1501
DT_WORDBREAK, DT_CALCRECT, DT_NOPREFIX = 0x0010, 0x0400, 0x0800
DT_CENTER = 0x0001
TRANSPARENT = 1
DEFAULT_CHARSET = 1
LWA_ALPHA = 0x00000002

# 画面の端から空けるぶん。ぴったり寄せると窮屈に見える。
MARGIN = 16

# 会話画面と同じ色。style.css の --panel / --input-bg / --ink / --muted / --input-line。
# COLORREF は 0x00BBGGRR なので、CSSの並びとは逆になる。
PANEL = 0x00241F1E          # #1e1f24
INPUT_BG = 0x002C2726       # #26272c
INK = 0x00E2E7E9            # #e9e7e2
MUTED = 0x009D9898          # #98989d
EDGE = 0x00423B3A           # #3a3b42
# 窓ごと薄くする。壁紙が透けるが、字は読める濃さ。
OPACITY = 232
MAX_WIDTH = 280
PADDING = 13
INPUT_HEIGHT = 30
GAP = 9
ROUND = 16


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
_signature(gdi32.SetBkColor, w.DWORD, w.HDC, w.DWORD)
_signature(gdi32.SetBkMode, ctypes.c_int, w.HDC, ctypes.c_int)
_signature(gdi32.SelectObject, w.HGDIOBJ, w.HDC, w.HGDIOBJ)
_signature(gdi32.FrameRgn, w.BOOL, w.HDC, w.HANDLE, w.HBRUSH, ctypes.c_int, ctypes.c_int)
_signature(gdi32.FillRgn, w.BOOL, w.HDC, w.HANDLE, w.HBRUSH)
_signature(user32.SetLayeredWindowAttributes, w.BOOL, w.HWND, w.DWORD, w.BYTE, w.DWORD)


class Bubble:
    """ことはの言葉と、こちらの一行。押されたら知らせる。"""

    CLASS_NAME = "KotohaBubble"

    def __init__(self, on_click=None, on_send=None):
        self.on_click = on_click or (lambda: None)
        self.on_send = on_send or (lambda text: None)
        self.text = ""
        self.width = self.height = 0
        self.asking = False
        self.center = False
        self._proc = WNDPROC(self._handle)
        self._brush = gdi32.CreateSolidBrush(PANEL)
        self._input_brush = gdi32.CreateSolidBrush(INPUT_BG)
        self._edge_brush = gdi32.CreateSolidBrush(EDGE)
        self._region = None
        self._font = gdi32.CreateFontW(-14, 0, 0, 0, 400, 0, 0, 0, DEFAULT_CHARSET,
                                       0, 0, 0, 0, "Yu Gothic UI")
        self._register()
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST, self.CLASS_NAME, "ことは",
            WS_POPUP, 0, 0, 10, 10, None, None, kernel32.GetModuleHandleW(None), None)
        # 窓ごと薄くする。**字も背景も同じだけ透ける**ので、文字は消えない。
        user32.SetLayeredWindowAttributes(self.hwnd, 0, OPACITY, LWA_ALPHA)
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | ES_AUTOHSCROLL, 0, 0, 10, INPUT_HEIGHT,
            self.hwnd, None, kernel32.GetModuleHandleW(None), None)
        user32.SendMessageW(self.edit, WM_SETFONT, self._font, 1)
        # 打つ前の薄い案内。会話画面の入力欄と同じ文句にしてある。
        user32.SendMessageW(self.edit, EM_SETCUEBANNER, 1,
                            ctypes.cast(ctypes.create_unicode_buffer("なにか話す…"),
                                        ctypes.c_void_p).value)

    def _register(self) -> None:
        klass = WNDCLASS()
        klass.lpfnWndProc = self._proc
        klass.hInstance = kernel32.GetModuleHandleW(None)
        klass.lpszClassName = self.CLASS_NAME
        klass.hbrBackground = self._brush
        klass.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(32512))
        user32.RegisterClassW(ctypes.byref(klass))

    # --- 出す・消す ---

    def say(self, text: str, anchor, asking: bool = False, width: int = 0,
            center: bool = False) -> None:
        """言葉を出す。anchor は (中心X, 上端Y)。その上に置く。

        width を渡すと、その幅にする（姿と同じ幅に揃えるため）。渡さなければ
        言葉の長さで決める。

        ⚠️ **測るときと描くときで、同じ幅を使うこと。** 測ったより1ドットでも
        狭く描くと、そのぶん行が増えて、下が見切れる。
        """
        self.text = text
        self.asking = asking
        self.center = center
        limit = (width - PADDING * 2) if width else (MAX_WIDTH - PADDING * 2)
        inner = self._measure(text, limit) if text else (limit, 0)
        self.width = width or (inner[0] + PADDING * 2)
        # 言葉が無いなら、その場所は空けない（入力欄だけのふきだしになる）。
        self.height = (inner[1] + (GAP if text else 0) + PADDING * 2
                       + (INPUT_HEIGHT if asking else 0))
        left, top, right, bottom = work_area()
        x = min(max(left + MARGIN, anchor[0] - self.width // 2), right - self.width - MARGIN)
        y = min(max(top + MARGIN, anchor[1] - self.height - 8), bottom - self.height - MARGIN)
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self.width, self.height,
                            SWP_NOACTIVATE)
        self._region = gdi32.CreateRoundRectRgn(0, 0, self.width + 1, self.height + 1,
                                                ROUND, ROUND)
        user32.SetWindowRgn(self.hwnd, self._region, True)
        if asking:
            # 入力欄は、下地の角丸から少し内側に置く。
            inset = PADDING + 6
            user32.SetWindowPos(self.edit, None, inset,
                                self.height - PADDING - INPUT_HEIGHT + 7,
                                self.width - inset * 2, INPUT_HEIGHT - 14,
                                SWP_NOACTIVATE)
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

    def _measure(self, text: str, limit: int):
        """その幅に収めたときの、言葉の大きさ。描くときと同じ幅で測る。"""
        dc = user32.GetDC(self.hwnd)
        old = gdi32.SelectObject(dc, self._font)
        rect = w.RECT(0, 0, limit, 0)
        user32.DrawTextW(dc, text, -1, ctypes.byref(rect),
                         DT_WORDBREAK | DT_CALCRECT | DT_NOPREFIX)
        gdi32.SelectObject(dc, old)
        user32.ReleaseDC(self.hwnd, dc)
        return min(max(rect.right, 60), limit), max(rect.bottom, 18)

    def _handle(self, hwnd, message, wparam, lparam):
        if message == WM_PAINT:
            paint = PAINTSTRUCT()
            dc = user32.BeginPaint(hwnd, ctypes.byref(paint))
            rect = w.RECT(0, 0, self.width, self.height)
            user32.FillRect(dc, ctypes.byref(rect), self._brush)
            if self.asking:
                # 入力欄の下地。会話画面の入力と同じ、少し明るい角丸。
                field = gdi32.CreateRoundRectRgn(
                    PADDING, self.height - PADDING - INPUT_HEIGHT,
                    self.width - PADDING + 1, self.height - PADDING + 1, 12, 12)
                gdi32.FillRgn(dc, field, self._input_brush)
                gdi32.DeleteObject(field)
            if self._region:
                gdi32.FrameRgn(dc, self._region, self._edge_brush, 1, 1)
            old = gdi32.SelectObject(dc, self._font)
            gdi32.SetBkMode(dc, TRANSPARENT)
            gdi32.SetTextColor(dc, INK if self.text else MUTED)
            if self.text:
                # **測ったときと同じ幅で描く。** 狭めると行が増えて見切れる。
                area = w.RECT(PADDING, PADDING, self.width - PADDING,
                              self.height - PADDING
                              - (INPUT_HEIGHT + GAP if self.asking else 0))
                style = DT_WORDBREAK | DT_NOPREFIX | (DT_CENTER if self.center else 0)
                user32.DrawTextW(dc, self.text, -1, ctypes.byref(area), style)
            gdi32.SelectObject(dc, old)
            user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        if message == WM_CTLCOLOREDIT:
            # 入力欄も同じ色にする。何もしないと白いままで、ここだけ浮く。
            gdi32.SetTextColor(wparam, INK)
            gdi32.SetBkColor(wparam, INPUT_BG)
            return self._input_brush
        if message == WM_LBUTTONUP:
            self.on_click()
            return 0
        if message in (WM_DESTROY, WM_CLOSE):
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)
