"""実体の居場所と、器への言づて。

ことはは1人で、姿を出す場所は1つだけ。**どの器に出すかは脳が決める。**
器（タスクトレイのドット・会話画面）は繋がってくるだけで、自分では決めない。

ここが持つのは2つ。**いま繋がっている器**と、**実体の居場所**。どちらも
揮発するもので、脳が落ちれば消える。器のほうから繋ぎ直してくるので、
それで足りる。DBに書き写すのは、ことは自身に居場所を言わせるためと、
上げ直したときの手がかりのため。**書き写しに失敗しても、実体はここにある。**

送るのは一方向。**器からの返事は待たない。** 届かない器は、繋がりが切れた
ときに自然と消える。
"""

import asyncio
import json
import threading

from datetime import datetime

from .. import notify
from ..memory import db
from ..talk import chat, figure, presence

# タスクトレイのドットの名前。会話画面は自分で名乗る（web-…）。
DESKTOP = "desktop"
# 無言の間に流す合図の間隔。途中の何かに切られないため（実装上の注意②）。
PING_SECONDS = 15.0

_lock = threading.Lock()
_vessels = {}        # 名前 -> Vessel。繋がっている器。
_body = None         # 実体のある器の名前。誰も居なければ None。
_since = ""
_reason = ""
_look = None         # 最後に押し出した姿。移った先にも、同じものを渡す。
_calling = None      # 通話している器。**実体のある器でしか始まらない。**


# 器ひとつが溜められるイベントの数。これを超えたら古いものから捨てる。
QUEUE_LIMIT = 100


class Vessel:
    """繋がっている器ひとつ。送り先の行列と、その行列が住んでいる輪。

    行列は uvicorn の輪（イベントループ）の中にある。**巡回や会話は別の糸で
    動いている**ので、そこから直に触らず、輪に頼んで入れてもらう。
    """

    __slots__ = ("name", "queue", "loop")

    def __init__(self, name, queue, loop):
        self.name = name
        self.queue = queue
        self.loop = loop

    @property
    def kind(self) -> str:
        return DESKTOP if self.name == DESKTOP else "web"

    def send(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        try:
            self.loop.call_soon_threadsafe(self._offer, payload)
        except RuntimeError:
            pass          # 輪が閉じたあと。次の繋ぎ直しで戻ってくる。

    def _offer(self, payload: str) -> None:
        """溜まりきっていたら、いちばん古いものを捨てて入れる。

        **半分死んだ器が繋がったままだと、行列は伸びつづける。** 姿も機嫌も
        最新のものが正しいので、古いものを抱えている意味はない。
        """
        while self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.queue.put_nowait(payload)


def reset() -> None:
    """全部忘れる。テストと、脳の上げ直しのため。"""
    global _body, _since, _reason, _look, _calling
    with _lock:
        _vessels.clear()
        _body, _since, _reason, _look, _calling = None, "", "", None, None


def wake() -> None:
    """脳が起きた。**まだどの器も繋がっていない。**

    前に動いていたときの写しが `app_state` に残っている。そのままだと、
    誰も居ないのに「デスクトップに居る」と言うことになる。
    """
    reset()
    _remember(None)


def join(name: str, queue, loop) -> Vessel:
    """器が繋がってきた。**誰も居なければ、その器に実体化する。**

    すでに実体がどこかにあるなら、繋がってきただけでは移らない。開いた画面は
    「外出中」から始まる。移るのは呼ばれたときだけ（claim）。
    """
    vessel = Vessel(name, queue, loop)
    with _lock:
        old = _vessels.get(name)
        _vessels[name] = vessel
        if old is not None and old is not vessel:
            # 同じ名前で繋ぎ直してきた。古いほうはもう読まれない。
            old.send({"type": "away", "reason": "つなぎ直し"})
        if _body is None:
            _move(name, "最初の器")
        else:
            vessel.send(_place_event(name))
            if _look and name == _body:
                vessel.send(dict(_look))
    return vessel


def leave(vessel) -> None:
    """繋がりが切れた。**居場所は動かさない。**

    切れたのは「見ていない」というだけで、置いてきたわけではない。iPhoneを
    ポケットにしまっても、ことははそこに居たままにする。戻ってくれば、また
    その画面に出る。見ていないあいだの言葉はスマホの通知で届く。
    """
    with _lock:
        if _vessels.get(vessel.name) is not vessel:
            return                     # すでに繋ぎ直されている。何もしない。
        _drop(vessel.name)


def forget(name: str) -> None:
    """器のほうから「見えなくなった」と言ってきた（bye）。

    ⚠️ **切断を待たない。** iPhoneのPWAは背景に回っても繋がりが残ることが
    あり、待っていると「まだ居る」と思い込んでPushが鳴らなくなる。

    切断と同じ扱いで、**居場所は動かさない**。見ていないだけ。
    """
    with _lock:
        if name in _vessels:
            _drop(name)


def claim(name: str, reason: str = "呼ばれた") -> bool:
    """実体をこの器へ。繋がっていない器は持てない。"""
    with _lock:
        if name not in _vessels:
            return False
        if _body != name:
            _move(name, reason)
        return True


def body():
    with _lock:
        return _body


def body_kind():
    """実体のある器の種類。"desktop" / "web" / None。"""
    with _lock:
        vessel = _vessels.get(_body)
        return vessel.kind if vessel else None


def anyone() -> bool:
    with _lock:
        return bool(_vessels)


def watching() -> bool:
    """実体のある器が、いま繋がっているか。

    **居場所と、見ているかどうかは別のこと。** iPhoneに居たままポケットに
    しまわれていれば、居場所はiPhoneのまま、見てはいない。そのときの言葉は
    通知で届ける。
    """
    with _lock:
        return _body in _vessels


def say(text: str) -> bool:
    """実体のある器に言づてる。**届ける先が無ければ False。**

    鳴らすかどうかを決めるのは announce の仕事で、ここは届けるだけ。
    """
    return _to_body({"type": "say", "text": text})


def show(picture: str, act: str) -> bool:
    """絵を切り替えさせる。**覚えておいて、移った先にも同じものを渡す。**"""
    global _look
    with _lock:
        _look = {"type": "act", "picture": picture, "act": act}
    return _to_body(dict(_look))


def refresh(conn=None, said_ago: float = None) -> bool:
    """いまの姿を測って押し出す。

    材料はDBの中（機嫌）と、すでに数えてある前面アプリだけ。**新しく覗くものは
    無い。** 呼ぶのは巡回と、会話のあとと、器が繋がってきたとき。
    """
    if conn is None:
        with db.session() as fresh:
            return refresh(fresh, said_ago)
    now = datetime.now()
    found = presence.streak(conn)
    picture, act = figure.look(
        now.hour, now,
        mood=chat.current_mood(conn, now.hour),
        said_ago=said_ago,
        streak_hours=found[1] if found else None,
    )
    return show(picture, act)


def start_call(name: str):
    """通話を始める。**実体ごとその器へ移る。**

    通話は姿のあるところでしか始まらない。だから通話の持ち主を別に持つ必要が
    なく、**実体が移れば通話は終わる**（`_move` を参照）。
    """
    global _calling
    with _lock:
        # **先に見ておく。** 実体を移すと、そこで前の通話は終わってしまう。
        previous = _calling
    if not claim(name, "通話"):
        return False, False
    with _lock:
        _calling = name
        return True, bool(previous and previous != name)


def calling():
    with _lock:
        return _calling


def end_call() -> bool:
    """通話を終える。置いてきた器からでも切れる。"""
    global _calling
    with _lock:
        released = _calling is not None
        _calling = None
        return released


def snapshot() -> dict:
    """いまの様子。管理画面とテストから見るため。"""
    with _lock:
        return {
            "body": _body,
            "kind": _vessels[_body].kind if _body in _vessels else None,
            "since": _since,
            "reason": _reason,
            "calling": _calling,
            "vessels": sorted(_vessels),
        }


# --- ここから下は _lock を持ったまま呼ぶ ---

def _to_body(event: dict) -> bool:
    with _lock:
        vessel = _vessels.get(_body)
        if vessel is None:
            return False
        vessel.send(event)
        return True


def _place_event(name: str) -> dict:
    if name == _body:
        return {"type": "here"}
    kind = _vessels[_body].kind if _body in _vessels else None
    return {"type": "away", "where": kind}


def _move(name: str, reason: str) -> None:
    """実体を移す。移ったことは、繋がっている全員に伝える。

    **通話は実体についてくる。** 別の器へ移ったら、そこで通話は終わる。
    置いていかれた器は、away を受けた時点で自分で切る。
    """
    global _body, _since, _reason, _calling
    if _calling and _calling != name:
        _calling = None
    _body, _since, _reason = name, db.now_utc(), reason
    for vessel in _vessels.values():
        vessel.send(_place_event(vessel.name))
    if _look and name in _vessels:
        # 移った先は、まだ何も知らない。覚えてある姿をそのまま渡す。
        _vessels[name].send(dict(_look))
    _remember(name)


def _drop(name: str) -> None:
    """器が1つ消えた。**居場所はそのまま。**

    移すのは、呼ばれたときだけ（話しかける・立て札を押す・通知から開く）。
    勝手に行き来すると落ち着かないし、iPhoneを見ていた続きが消える。

    通話だけは終わる。マイクの開いた画面がもう無い。
    """
    global _calling
    if _calling == name:
        _calling = None
    _vessels.pop(name, None)


def _remember(name) -> None:
    """居場所をDBへ書き写す。**失敗しても実体はここにある。**

    移ったときにしか呼ばない。表示のために毎分書くことはしない。
    """
    try:
        with db.session() as conn:
            db.set_state(conn, db.BODY_WHERE, name or "")
            conn.commit()
    except Exception as error:               # noqa: BLE001 - 書けなくても続ける
        notify.log(f"居場所を書き写せなかった: {error!r}")
