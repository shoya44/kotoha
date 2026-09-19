"""ことはのほうから何か言う。言ったことが、そのまま通知になる。

自発的な発言はすべてここを通す。通知だけ送る道はない。開いても何も無い
通知ほど不親切なものはないし、あとから会話を辿れなくなる。

立て続けには鳴らさない。巡回のあいだは預かっておいて、最後に1通へまとめる。
向こうに居るのは1人で、2通に分けて届く理由がない。
"""

import contextlib
import json

from .. import config, notify
from ..memory import db
from ..talk import chat, presence
from . import hub

# 巡回のあいだ立てる印。ここが True の間、announce は鳴らさずに預かる。
# 触るのは裏の巡回だけで、会話（/api/chat）はここを通らない。
_collecting = False

# 預かったことを呼び出し側へ伝える印。空文字（言えなかった）とは区別する。
HELD = "あとでまとめて言う"
# 長く溜め込んでも困る。一度に言える量には限りがある。
HELD_LIMIT = 8
# 言えない状態が続いたときに諦める回数。**言えるまで毎分試すと、そのたびに
# APIの枠を1回ずつ食う。** 記憶整理と同じ考え方（consolidate.GIVE_UP_AFTER）。
GIVE_UP_AFTER = 3


@contextlib.contextmanager
def collecting():
    """このあいだは鳴らさずに預かる。巡回が一巡するあいだ立てておく。

    巡回の途中で二度三度と鳴らすと、同じ人から立て続けに届く。最後に
    flush_held() でまとめて言う。
    """
    global _collecting
    _collecting = True
    try:
        yield
    finally:
        _collecting = False


def _held(conn):
    try:
        items = json.loads(db.get_state(conn, db.HELD_ANNOUNCEMENTS) or "[]")
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _too_soon(conn) -> bool:
    """前に鳴らしてから、まだ間が空いていない。"""
    if config.NOTIFY_GAP_MINUTES <= 0:
        return False
    return not db.overdue(conn, db.LAST_NOTIFY_AT, config.NOTIFY_GAP_MINUTES * 60)


def _with_ids(name, ids) -> str:
    """開く先に番号だけを足す。用件は載せない。通知の道はOneSignalを通る。"""
    joiner = "&" if "?" in config.PUSH_OPEN_URL else "?"
    return f"{config.PUSH_OPEN_URL}{joiner}{name}=" + ",".join(str(i) for i in ids)


def _snooze_ready(ids) -> bool:
    return bool(ids and config.SNOOZE_MINUTES > 0 and config.PUSH_OPEN_URL)


def _snooze_buttons(ids):
    """通知そのものに付ける「あとで」。

    ⚠️ Safari は通知のボタンに対応していない。iPhoneのPWAには出ないので、
    こちらは Chrome で見たときのための道。iPhone では開いた画面で押す。
    """
    if not _snooze_ready(ids):
        return None
    return [{"id": "snooze", "text": f"{config.SNOOZE_MINUTES}分後にもう一度",
             "url": _with_ids("snooze", ids)}]


def _say(conn, items) -> str:
    """預かったぶんも含めて、ひと続きの言葉にして鳴らす。

    2つを2通に分けると、同じ人から立て続けに届く。1通にまとめるのは
    体裁の問題ではなく、向こうに居るのが1人だからで、言い方はことはに任せる。
    """
    closings = [i["closing"] for i in items if i.get("closing")]
    if len(closings) > 1:
        reasons = "\n".join(f"- {c}" for c in closings)
        closing = ("いくつか言うことがある。次のことを、ひと続きの短い言葉にまとめて言う。"
                   "箇条書きにはしない。\n" + reasons)
    else:
        closing = closings[0] if closings else ""
    extra = "\n".join(i["extra"] for i in items if i.get("extra"))
    keep = any(i.get("keep", True) for i in items)
    text = ""
    try:
        text = chat.speak(conn, closing, extra, keep)
    except Exception as error:
        notify.log(f"言えなかった: {error!r}")
    if not text:
        plains = [i["plain"] for i in items if i.get("plain")]
        if plains:
            text = "。".join(plains)
            chat.remember(conn, text, keep=keep)   # 定型でも、言った以上は残す
    if text:
        db.set_state(conn, db.LAST_NOTIFY_AT, db.now_utc())
        conn.commit()
        _deliver(text, [i for item in items for i in item.get("remind_ids") or ()])
    return text


def reaches_the_person() -> bool:
    """いま、ことはの姿が相手の目に入るところにあるか。

    実体が会話画面にあるなら、**その画面は見られている**（背景に回れば器の
    ほうから bye が来て、実体は解ける）。タスクトレイのドットは画面に居っぱなし
    なので、そこだけは席に居るかどうかを確かめる。

    ⚠️ **測るのはこの瞬間だけ。** 巡回では測らない。ことはが口を開くのは
    1日に数回で、常時見張る理由がない。
    """
    if not hub.watching():
        # 居場所はあるが、その画面はもう見られていない（閉じた・裏に回った）。
        return False
    kind = hub.body_kind()
    if kind is None:
        return False
    if kind != hub.DESKTOP:
        return True
    idle = presence.idle_seconds()
    if idle is None:
        return False          # 分からないなら、居ないほうに倒す（鳴らす）
    return idle < config.BODY_AWAY_MINUTES * 60


def _deliver(text: str, ids) -> None:
    """言葉を届ける。**姿が見えているならふきだし、でなければスマホ。**

    どちらか一方しか通らない。目の前に居るのに鳴らすのは重複で、
    誰も見ていないのにふきだしを出すのは、戻ったとき溜まって残るだけ。
    """
    seen = reaches_the_person()
    if seen:
        hub.say(text)
        hub.refresh(said_ago=0)          # 言った直後は、こちらを向かせる
    if not seen or config.PUSH_WHEN_EMBODIED:
        # 開く先にも番号を載せる。ボタンの出ない iPhone では、開いた画面に出す。
        url = _with_ids("remind", ids) if _snooze_ready(ids) else ""
        notify.push("ことは", text, buttons=_snooze_buttons(ids), url=url)


def can_speak() -> bool:
    """ことはが口を開ける状態か。

    **届ける先は、スマホの通知だけではなくなった。** 姿が出ていれば、そこへ
    言えばよい。通知を切っていても、画面に居るあいだは話しかけてくる。
    """
    return notify.ready() or hub.anyone()


def announce(conn, closing: str, plain: str = "", extra: str = "",
             keep: bool = True, remind_ids=()) -> str:
    """ことはのほうから何か言う。言ったことが、そのまま通知になる。

    通知はすべてここを通す。言わずに鳴らすことはしない。開いても何も
    無い通知ほど不親切なものはないし、あとから会話を辿れなくなる。

    前の通知から間が空いていなければ、ここでは鳴らさずに預かる。巡回が
    間をおいてから、溜まったぶんをまとめて1通にして言う。預かったときは
    HELD を返すので、呼び出し側から見れば「言えた」と同じ扱いでよい。

    plain は、文を作れなかったときに代わりに言わせる一言。見張りのように
    「黙るくらいなら定型でも伝えたい」用がある。声かけのように、言えない
    なら黙っていればよいものは空のままでよい。
    """
    if not can_speak():
        return ""
    if _collecting or _too_soon(conn):
        items = _held(conn)
        if len(items) >= HELD_LIMIT:
            notify.log(f"預かりきれないので古いぶんを捨てた: {items[0].get('plain') or ''}"[:120])
            items = items[1:]
        items.append({"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                      "remind_ids": list(remind_ids)})
        _put_held(conn, items)
        conn.commit()
        return HELD
    return _say(conn, [{"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                        "remind_ids": list(remind_ids)}])


def _put_held(conn, items) -> None:
    db.set_state(conn, db.HELD_ANNOUNCEMENTS, json.dumps(items, ensure_ascii=False))


def flush_held(conn) -> str:
    """預かったぶんを、間が空いてからまとめて言う。

    **言えなかったら預かりは消さない。** 頼まれごとは「言った」ことにして
    あるので、ここで落とすと二度と出てこない。ただし言えるまで毎分
    試すと、そのたびにAPIの枠を食う。数えて、続くようなら諦める。
    """
    if not can_speak():
        return ""
    items = _held(conn)
    if not items or _too_soon(conn):
        return ""
    spoken = _say(conn, items)
    if spoken:
        _put_held(conn, [])
        db.set_state(conn, db.HELD_FAILS, 0)
        conn.commit()
        return spoken
    fails = int(db.get_state(conn, db.HELD_FAILS, "0") or 0) + 1
    if fails >= GIVE_UP_AFTER:
        notify.log(f"まとめて言えないので諦めた: {len(items)}件")
        _put_held(conn, [])
        fails = 0
    db.set_state(conn, db.HELD_FAILS, fails)
    conn.commit()
    return ""
