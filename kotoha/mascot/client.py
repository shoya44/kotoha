"""脳とのやりとり。受け取るのは言づて、送るのは会話と呼び出しだけ。

**こちらから様子を見に行かない。** 開いたままの道（SSE）に流れてくるものを
受けるだけで、絵をどうするかも、姿を出すかどうかも脳が決める。

繋がらないときは、黙って待って繋ぎ直す。本体を上げ直すのはトレイの仕事で、
ここからは何もしない。
"""

import json
import queue
import threading
import time

import httpx

from .. import config

VESSEL = "desktop"
# 切れたときに繋ぎ直すまでの間。すぐ繋ぎ直すと、落ちている相手を叩き続ける。
RETRY_WAIT = 5.0
# 会話の返事を待つ上限。生成に数秒かかる。
CHAT_TIMEOUT = 120.0


def base_url() -> str:
    """脳の居場所。**同じPCの中**なので、外には出ない。"""
    return f"http://127.0.0.1:{config.WEB_PORT}"


class Brain:
    """脳への糸。受け取ったものは順番待ちに積み、画面の側が拾う。"""

    def __init__(self):
        self.events = queue.Queue()
        self.stopping = threading.Event()
        self._client = httpx.Client(timeout=None)
        self._thread = None

    # --- 受ける ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def _listen(self) -> None:
        connected = False
        while not self.stopping.is_set():
            try:
                url = (f"{base_url()}/api/presence/stream"
                       f"?vessel={VESSEL}&token={config.WEB_TOKEN}")
                with self._client.stream("GET", url) as response:
                    response.raise_for_status()
                    connected = True
                    self.events.put({"type": "online"})
                    for line in response.iter_lines():
                        if self.stopping.is_set():
                            return
                        if line.startswith("data:"):
                            try:
                                self.events.put(json.loads(line[5:]))
                            except ValueError:
                                pass
            except Exception:                  # noqa: BLE001 - 繋がらないだけ
                pass
            if connected:
                connected = False
                self.events.put({"type": "offline"})
            self.stopping.wait(RETRY_WAIT)

    def stop(self) -> None:
        self.stopping.set()
        self.goodbye()
        self._client.close()

    # --- 送る ---

    def _post(self, path: str, payload: dict, timeout: float = 5.0):
        return self._client.post(f"{base_url()}{path}", json=payload, timeout=timeout,
                                 headers={"X-Kotoha-Token": config.WEB_TOKEN})

    def chat(self, text: str) -> str:
        """話しかける。**送信が呼び出しを兼ねる**ので、実体はこちらへ来る。"""
        response = self._post("/api/chat", {"text": text, "vessel": VESSEL}, CHAT_TIMEOUT)
        response.raise_for_status()
        return (response.json().get("reply") or "").strip()

    def come_here(self) -> bool:
        try:
            return self._post("/api/presence/here", {"vessel": VESSEL}).status_code == 200
        except Exception:                      # noqa: BLE001
            return False

    def goodbye(self) -> None:
        try:
            self._post("/api/presence/bye", {"vessel": VESSEL}, 2.0)
        except Exception:                      # noqa: BLE001
            pass                               # 切れれば脳のほうで気づく


def in_thread(work, done, *args):
    """待つ仕事を別の糸へ逃がす。**画面を止めない。**

    返事は done(結果, 失敗) で受け取る。呼ばれるのは網の糸の上なので、
    画面を触らずに順番待ちへ積むこと。
    """
    def run():
        try:
            done(work(*args), None)
        except Exception as error:             # noqa: BLE001
            done(None, error)

    threading.Thread(target=run, daemon=True).start()
    return time.time()
