"""タスクトレイのことは。組み立てと、絵を進める時計。

姿をどう出すかはここ、**何を出すかは脳**（`talk/figure.py` と `serve/hub.py`）。
受け取った名前を描き、押されたら送り返す。それだけにしてある。

呼吸とまばたき、それと**暇なときの動き**だけは、ここで作る。1ドット上下させ、
まばたきの差分があるものは時々差し替える。止まっている絵は、止まって見える。

暇なときの動き（脳は関わらない。まばたきと同じ「器の癖」）:
- 散歩: `walk` の絵で、机の端を少し歩いて止まる。コマ（f1, f2）を足で交互に出し、
  左へ行くときは絵を返す。歩いた先は置き場所として覚える
- 所作: `fidget_*` の絵を数秒だけ出して、元の姿に戻る
- 跳ね: 話したとき・機嫌がいいとき、足元から少し跳ねる
脳から姿が届いたら、動きは途中でもやめてそれに従う。
"""

import ctypes
import ctypes.wintypes as w
import json
import random
import time
import webbrowser

from .. import config
from . import client, sheet, window
from .bubble import Bubble
from .client import Brain
from .window import Dot, kernel32, user32

PLACE_PATH = config.BASE_DIR / "data" / "mascot.json"
LOG_PATH = config.BASE_DIR / "data" / "mascot.log"

TICK_MS = 40
# 呼吸。この周期で、1ドットぶん縮んでは戻る（足元は動かない）。
BREATH_SECONDS = 3.4
# まばたきの間。ばらつかせないと、機械に見える。
BLINK_MIN, BLINK_MAX, BLINK_MS = 4.0, 10.0, 0.13
# ふきだしが残る時間。頼まれごとは読み終わるまで置いておきたい。
BUBBLE_SECONDS = 12.0
# 繋がっていないときの濃さ。沈んだ色で座って待つ。
DIM = 120
# 画面の端から空けるぶん。壁に貼り付いているように見えると窮屈。
MARGIN_RIGHT, MARGIN_BOTTOM = 28, 10

# 暇なときの動き。間をばらつかせる。**動きすぎると邪魔**なので、散歩は数分に一度。
STROLL_MIN, STROLL_MAX = 150.0, 420.0
STROLL_PX_MIN, STROLL_PX_MAX = 60, 220       # 一度に歩く距離
STROLL_SPEED = 1                             # 1ティックに進むドット（40msなので 25px/s）
STROLL_STEP_SECONDS = 0.26                   # 足のコマを替える間
# 歩きの絵が向いている側。左へ行くときは返す。
WALK_FACES_RIGHT = True
FIDGET_MIN, FIDGET_MAX, FIDGET_SECONDS = 30.0, 90.0, 3.2
# 跳ね。足元から HOP_HEIGHT ドット浮いて戻る。
HOP_SECONDS, HOP_HEIGHT = 0.36, 6
# 脳の姿が「暇」のときだけ動く。話している・寝ている・すねているときは動かない。
IDLE_ACTS = ("idle", "happy")

# 同じ姿を二重に出さない。トレイと同じ作法（tray.already_running）。
MUTEX_NAME = "kotoha-mascot-single-instance"
_mutex = None

ID_OPEN, ID_TALK, ID_CALL, ID_HERE, ID_QUIT = 1, 2, 3, 4, 5
WM_KEYDOWN, VK_RETURN, VK_ESCAPE, VK_SHIFT = 0x0100, 0x0D, 0x1B, 0x10


def log(message: str) -> None:
    """画面はあるが、出せる場所ではない。起きたことはここに残す。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def chat_url(extra: str = "") -> str:
    return f"{client.base_url()}/{extra}"


class Mascot:
    """居場所と、いまの姿と、ふきだし。"""

    def __init__(self):
        self.sheet = sheet.Sheet().load()
        self.brain = Brain()
        self.picture = self.sheet.any_name()
        self.embodied = False
        self.online = False
        self.blinking = False
        self.busy = False                     # 返事を待っているあいだ
        self.next_blink = time.time() + random.uniform(BLINK_MIN, BLINK_MAX)
        self.bubble_until = 0.0
        self.last_drawn = None
        # 暇なときの動き
        self.act = "idle"
        self.motion = None                    # None | ("stroll", 向き, 残り) | ("fidget", 名前, 終わり)
        self.step_at = 0.0
        self.step = 0
        self.hops = []                        # 跳ねの始まり（time）の並び
        now = time.time()
        self.next_stroll = now + random.uniform(STROLL_MIN, STROLL_MAX)
        self.next_fidget = now + random.uniform(FIDGET_MIN, FIDGET_MAX)

        width, height = self.sheet.size
        self.dot = Dot(width, height, on_click=self.tapped, on_menu=self.opened_menu,
                       on_moved=self.remember_place, on_tick=self.tick)
        self.bubble = Bubble(on_click=self.open_chat, on_send=self.send)
        self.dot.place(*self.first_place(width, height))
        self.dot.visible(True)
        self.draw(force=True)

    # --- 居場所 ---

    def first_place(self, width: int, height: int):
        """前に置いた場所。無ければ主画面の右下。**画面の外には出さない。**

        収める画面は、**置いた場所のある画面**で見る。主画面で見ると、
        サブディスプレイに置いたぶんが起動のたびに主画面へ戻される。
        """
        left, top, right, bottom = window.work_area()
        x, y = right - width - MARGIN_RIGHT, bottom - height - MARGIN_BOTTOM
        try:
            saved = json.loads(PLACE_PATH.read_text(encoding="utf-8"))
            x, y = int(saved["x"]), int(saved["y"])
            left, top, right, bottom = window.work_area_at(x + width // 2, y + height // 2)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        x = max(left, min(x, right - width))
        y = max(top, min(y, bottom - height))
        return x, y

    def anchor(self):
        """ふきだしを出す位置。**頭の少し上**を指す。"""
        x, _ = self.dot.center()
        return x, self.dot.y + 10

    def remember_place(self, x: int, y: int) -> None:
        """運ばれた先を覚える。**DBには入れない**（表示の都合は記憶と混ぜない）。"""
        try:
            PLACE_PATH.parent.mkdir(parents=True, exist_ok=True)
            PLACE_PATH.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
        except OSError as error:
            log(f"置き場所を覚えられなかった: {error!r}")

    # --- 描く ---

    def draw(self, force: bool = False) -> None:
        now = time.time()
        # 息を吐いているあいだだけ、1ドットぶん縮む。浮かせると跳ねて見える。
        breathing_out = (now % BREATH_SECONDS) < BREATH_SECONDS / 2
        opacity = 255 if self.online else DIM
        name, tag, mirror = self.picture, None, False
        if self.motion and self.motion[0] == "stroll":
            name = "walk"
            tags = self.sheet.tags(name)
            tag = tags[self.step % len(tags)] if tags else None
            mirror = (self.motion[1] < 0) == WALK_FACES_RIGHT
            breathing_out = False
        elif self.motion and self.motion[0] == "fidget":
            name = self.motion[1]
        hop = self._hop(now)
        state = (name, tag, mirror, self.blinking, breathing_out, opacity, hop)
        if state == self.last_drawn and not force:
            return
        self.last_drawn = state
        frame = self.sheet.frame(name, self.blinking, tag)
        if frame is None:
            frame = self.sheet.frame(self.sheet.any_name())
        if frame is not None and mirror:
            frame = frame.mirrored()
        if frame is not None and breathing_out:
            frame = frame.squashed()
        self.dot.draw(frame, opacity=opacity)
        self.dot.lift(hop)

    # --- 暇なときの動き ---

    def _hop(self, now: float) -> int:
        """いま何ドット浮いているか。放物線で上がって戻る。"""
        self.hops = [t for t in self.hops if now - t < HOP_SECONDS]
        if not self.hops or now < self.hops[0]:
            return 0
        phase = (now - self.hops[0]) / HOP_SECONDS
        return round(HOP_HEIGHT * 4 * phase * (1 - phase))

    def hop(self, times: int = 1) -> None:
        now = time.time()
        self.hops = [now + i * HOP_SECONDS for i in range(times)]

    def _idle(self) -> bool:
        """動いてよいか。実体があって、繋がっていて、脳の姿が暇で、ふきだしも出ていない。"""
        return (self.embodied and self.online and not self.busy and self.act in IDLE_ACTS
                and not self.bubble_until and not self.bubble.asking and not self.dot.dragging())

    def _move(self, now: float) -> None:
        if self.motion is None:
            if not self._idle():
                return
            if now >= self.next_stroll and "walk" in self.sheet.frames:
                direction = random.choice((-1, 1))
                self.motion = ("stroll", direction, random.randint(STROLL_PX_MIN, STROLL_PX_MAX))
                self.step_at, self.step = now, 0
            elif now >= self.next_fidget and self.sheet.fidgets():
                self.motion = ("fidget", random.choice(self.sheet.fidgets()), now + FIDGET_SECONDS)
            return
        kind = self.motion[0]
        if kind == "stroll":
            _, direction, left = self.motion
            if not self._idle() or left <= 0:
                self._stop_motion()
                return
            if now - self.step_at >= STROLL_STEP_SECONDS:
                self.step_at, self.step = now, self.step + 1
            if not self.dot.walk(direction * STROLL_SPEED):
                self._stop_motion()           # 画面の端。引き返さず、ここで止まる
                return
            self.motion = ("stroll", direction, left - STROLL_SPEED)
        elif kind == "fidget":
            if now >= self.motion[2] or not self._idle():
                self._stop_motion()

    def _stop_motion(self) -> None:
        now = time.time()
        if self.motion and self.motion[0] == "stroll":
            self.remember_place(self.dot.x, self.dot.y)
            self.next_stroll = now + random.uniform(STROLL_MIN, STROLL_MAX)
        else:
            self.next_fidget = now + random.uniform(FIDGET_MIN, FIDGET_MAX)
        self.motion = None

    def say(self, text: str, asking: bool = False) -> None:
        # 打つときのふきだしは、姿と同じ幅に揃える。言葉のほうは読める幅に任せる。
        self.bubble.say(text, self.anchor(), asking=asking,
                        width=self.dot.width if asking and not text else 0)
        self.bubble_until = 0 if asking else time.time() + BUBBLE_SECONDS

    # --- 時計 ---

    def tick(self) -> None:
        self.drain()
        now = time.time()
        if self.sheet.can_blink(self.picture):
            if not self.blinking and now >= self.next_blink:
                self.blinking = True
                self.next_blink = now + BLINK_MS
            elif self.blinking and now >= self.next_blink:
                self.blinking = False
                self.next_blink = now + random.uniform(BLINK_MIN, BLINK_MAX)
        elif self.blinking:
            self.blinking = False
        if self.bubble_until and now >= self.bubble_until and not self.bubble.asking:
            self.bubble.hide()
            self.bubble_until = 0
        self._move(now)
        self.draw()

    def drain(self) -> None:
        """脳から届いたものを片づける。**画面を触るのはこの糸だけ。**"""
        while True:
            try:
                event = self.brain.events.get_nowait()
            except Exception:                  # noqa: BLE001 - 空になっただけ
                return
            kind = event.get("type")
            if kind == "here":
                self.embodied = True
                # 立て札は用済み。**消してから姿を出す**（両方は出さない）。
                self.bubble.hide()
                self.bubble_until = 0
                self.dot.visible(True)
            elif kind == "away":
                self.embodied = False
                self.wave_goodbye()
            elif kind == "act":
                self.picture = event.get("picture") or self.picture
                act = event.get("act") or "idle"
                if act != self.act and act == "happy":
                    self.hop(2)
                self.act = act
                if self.motion:
                    # 脳が姿を替えた。動きは途中でもやめて従う。
                    self._stop_motion()
            elif kind == "say":
                self.hop()
                self.say(event.get("text") or "")
            elif kind == "online":
                self.online = True
            elif kind == "offline":
                self.online = False
            elif kind == "reply":
                self.busy = False
                self.say(event.get("text") or "うまく言えなかった")
            self.draw(force=True)

    def wave_goodbye(self) -> None:
        """出ていくところ。**手を振ってから、立て札を残す。**

        歩いて出て行く絵は素材に無い。振る絵はあるので、そちらを使う。
        """
        self.bubble.hide()
        self.bubble_until = 0
        if "wave" in self.sheet.frames:
            self.picture = "wave"
            self.draw(force=True)
            time.sleep(0.45)
        self.dot.visible(False)
        x, _ = self.dot.center()
        # ひとことだけ。押せば戻ることは、押してみれば分かる。
        self.bubble.say("外出中", (x, self.dot.y + self.dot.height),
                        width=self.dot.width, center=True)
        self.bubble_until = 0

    # --- 押されたとき ---

    def tapped(self) -> None:
        if self.bubble.asking:
            self.bubble.hide()
            return
        self.say("", asking=True)
        self.bubble.focus_input()

    def open_chat(self, extra: str = "") -> None:
        """立て札なら呼び戻す。姿が出ているなら、会話画面を開く。"""
        if not self.embodied:
            self.brain.come_here()
            return
        webbrowser.open(chat_url(extra))

    def send(self, text: str) -> None:
        if not text or self.busy:
            return
        self.busy = True
        self.bubble.clear_input()
        self.say("…")
        client.in_thread(self.brain.chat, self.answered, text)

    def answered(self, reply, error) -> None:
        """網の糸から呼ばれる。**ここでは画面を触らず、順番待ちへ積むだけ。**"""
        if error:
            log(f"話しかけたが返らなかった: {error!r}")
        self.brain.events.put({"type": "reply", "text": reply or "うまく言えなかった"})

    def opened_menu(self, x: int, y: int) -> None:
        items = [
            (ID_TALK, "話しかける", True),
            (ID_OPEN, "ことはを開く", True),
            (ID_CALL, "通話する（会話画面が開きます）", True),
            (0, "", True),
            (ID_HERE, "こっちに呼ぶ", not self.embodied),
            (0, "", True),
            (ID_QUIT, "しまう", True),
        ]
        chosen = self.dot.menu(items, x, y)
        if chosen == ID_TALK:
            self.tapped()
        elif chosen == ID_OPEN:
            webbrowser.open(chat_url())
        elif chosen == ID_CALL:
            webbrowser.open(chat_url("?call=1"))
        elif chosen == ID_HERE:
            self.brain.come_here()
        elif chosen == ID_QUIT:
            user32.PostQuitMessage(0)

    # --- 動かす ---

    def run(self) -> None:
        self.brain.start()
        user32.SetTimer(self.dot.hwnd, 1, TICK_MS, None)
        message = w.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            # Enterは入力欄からは上がってこない。ここで拾う。
            # 日本語の変換中は VK_PROCESSKEY が来るので、確定と取り違えない。
            if message.message == WM_KEYDOWN and message.hWnd == self.bubble.edit:
                if message.wParam == VK_RETURN and user32.GetKeyState(VK_SHIFT) >= 0:
                    # Shiftを押しながらなら改行。押していなければ送る。
                    self.send(self.bubble.typed())
                    continue
                if message.wParam == VK_ESCAPE:
                    self.bubble.hide()
                    continue
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        self.brain.stop()


def already_running() -> bool:
    """二重に出さない。掴んだ印はプロセスが終わるまで持ったままにする。

    同時に立ち上がると印の取り合いになるので、窓の有無も見る。どちらかが
    見つかれば、もう1つは黙って引き下がる。
    """
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        return True
    return bool(user32.FindWindowW(Dot.CLASS_NAME, None))


def main() -> None:
    if not config.MASCOT_ENABLED:
        return
    if already_running():
        log("すでに姿が出ているので、何もしない")
        return
    try:
        Mascot().run()
    except Exception as error:                 # noqa: BLE001 - 落ちても本体は無事
        log(f"落ちた: {error!r}")
        raise


if __name__ == "__main__":
    main()
