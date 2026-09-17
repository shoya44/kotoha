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
from ..talk import chat

# 巡回のあいだ立てる印。ここが True の間、announce は鳴らさずに預かる。
# 触るのは裏の巡回だけで、会話（/api/chat）はここを通らない。
_collecting = False

# 預かったことを呼び出し側へ伝える印。空文字（言えなかった）とは区別する。
HELD = "あとでまとめて言う"
# 長く溜め込んでも困る。一度に言える量には限りがある。
HELD_LIMIT = 8


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
        ids = [i for item in items for i in item.get("remind_ids") or ()]
        # 開く先にも番号を載せる。ボタンの出ない iPhone では、開いた画面に出す。
        url = _with_ids("remind", ids) if _snooze_ready(ids) else ""
        notify.push("ことは", text, buttons=_snooze_buttons(ids), url=url)
    return text


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
    if not notify.ready():
        return ""
    if _collecting or _too_soon(conn):
        items = _held(conn)
        if len(items) >= HELD_LIMIT:
            notify.log(f"預かりきれないので古いぶんを捨てた: {items[0].get('plain') or ''}"[:120])
            items = items[1:]
        items.append({"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                      "remind_ids": list(remind_ids)})
        db.set_state(conn, db.HELD_ANNOUNCEMENTS, json.dumps(items, ensure_ascii=False))
        conn.commit()
        return HELD
    return _say(conn, [{"closing": closing, "plain": plain, "extra": extra, "keep": keep,
                        "remind_ids": list(remind_ids)}])


def flush_held(conn) -> str:
    """預かったぶんを、間が空いてからまとめて言う。

    先に置き場を空にする。ここで失敗しても、同じものを抱えたまま毎分
    やり直すことにはしない。
    """
    if not notify.ready():
        return ""
    items = _held(conn)
    if not items or _too_soon(conn):
        return ""
    db.set_state(conn, db.HELD_ANNOUNCEMENTS, "[]")
    conn.commit()
    return _say(conn, items)
