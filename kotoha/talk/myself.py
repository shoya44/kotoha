"""自分の様子。止まっていたこと、中身が変わったこと、設定が変わったこと。

人は目が覚めれば、どれだけ寝ていたか見当がつく。ことはも同じで、分かるのは
**止まっていた長さ**、**起き直したら中身が違うこと**、**設定を変えられたこと**
の3つだけ。何が変わったかは分からない（自分の仕組みは見えない）。分かるぶんを
状況の行に1行渡し、触れるかどうかは人格に任せる。通知にはしない。起動の
たびに鳴らすと、トレイの上げ直しで毎回APIを1回食う。

- 生きている印は巡回が毎分書く。起きたときにその差が「止まっていた長さ」。
  トレイは巻き込まない（脳を取り込んだトレイが脳になった事故の形）。
- 中身の印は kotoha/ 配下の py の中身のハッシュ。版番号も git も要らない。
- 設定は、ことは自身のふるまいに関わるオン／オフだけ。数値の上限は渡さない。
- **人格の書き換えは拾わない。** 人は自分の性格が書き換わったことを知覚しない。
"""

import hashlib
import json
from pathlib import Path

from .. import config
from ..memory import db

PACKAGE_DIR = Path(__file__).resolve().parent.parent

# これより短い止まりは言わない。トレイが上げ直す数十秒や、起こし直しの数分は、
# 人でいえばうたた寝で、起きたことにすら気づかない。
NAP_MINUTES = 30
# 渡した様子を持っておく長さ。話題と同じで、翌日にはもう「さっき」ではない。
NOTE_KEEP_HOURS = 24

# 変わったら伝える設定。env のキーと、ことはに渡す呼び名。
WATCHED = {
    "KOTOHA_VOICE_ENABLED": "声",
    "KOTOHA_PUSH_ENABLED": "スマホへの通知",
    "KOTOHA_MASCOT_ENABLED": "デスクトップの姿",
    "KOTOHA_BRIEFING_ENABLED": "朝のひとこと",
    "KOTOHA_LOOKOUT_ENABLED": "見守り",
    "KOTOHA_REACH_OUT_ENABLED": "暇なときの声かけ",
    "KOTOHA_AFTERTHOUGHT_ENABLED": "後から思い出すこと",
    "KOTOHA_DIARY_ENABLED": "日記",
    "KOTOHA_EMBED_ENABLED": "意味で思い出すこと",
}


def fingerprint(root: Path = None) -> str:
    """いまの中身の印。py の中身が1文字でも違えば変わる。"""
    root = root or PACKAGE_DIR
    digest = hashlib.sha1()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def settings_now() -> dict:
    """見張っている設定の、いまの値。config から読む（.env は読み直し済みの前提）。"""
    return {key: bool(getattr(config, key[len("KOTOHA_"):], False)) for key in WATCHED}


def _settings_diff(before: dict, after: dict) -> list:
    words = []
    for key, name in WATCHED.items():
        if key in before and before[key] != after[key]:
            words.append(f"{name}が{'入れられた' if after[key] else '止められた'}")
    return words


def _slept_phrase(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}分"
    hours = minutes / 60
    if hours < 48:
        return f"{int(hours)}時間"
    return f"{int(hours // 24)}日"


def heartbeat(conn) -> None:
    """生きている印。巡回が毎分書く。止まれば、ここが止まった時刻になる。"""
    db.set_state(conn, db.LAST_ALIVE_AT, db.now_utc())
    conn.commit()


def wake(conn) -> str:
    """起きた。止まっていた長さ・中身・設定を、前に残した印と見比べる。

    **脳として動き出すときに、一度だけ**（serve/web.py の lifespan）。初めての
    起動は比べる相手が無いので、印だけ残して何も言わない。
    """
    parts = []
    slept = db.seconds_since(db.get_state(conn, db.LAST_ALIVE_AT))
    if slept != float("inf") and slept >= NAP_MINUTES * 60:
        parts.append(f"{_slept_phrase(slept)}ほど止まっていて、起き直した")
    code = fingerprint()
    before = db.get_state(conn, db.SELF_CODE)
    if before and before != code:
        parts.append("起き直したら中身が少し変わっていた（何が変わったかは分からない）")
    parts.extend(_settings_diff(_stored_settings(conn), settings_now()))
    db.set_state(conn, db.SELF_CODE, code)
    db.set_state(conn, db.SELF_SETTINGS, json.dumps(settings_now()))
    db.set_state(conn, db.LAST_ALIVE_AT, db.now_utc())
    if parts:
        _note(conn, "。".join(parts))
    conn.commit()
    return "。".join(parts)


def settings_changed(conn) -> str:
    """設定シートから変えられた。止めずに効く道なので、ここで気づく。"""
    now = settings_now()
    words = _settings_diff(_stored_settings(conn), now)
    db.set_state(conn, db.SELF_SETTINGS, json.dumps(now))
    if words:
        _note(conn, "設定が変わった: " + "、".join(words))
    conn.commit()
    return "、".join(words)


def _stored_settings(conn) -> dict:
    try:
        stored = json.loads(db.get_state(conn, db.SELF_SETTINGS) or "{}")
    except ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


def _note(conn, text: str) -> None:
    db.set_state(conn, db.SELF_NOTE, text)
    db.set_state(conn, db.SELF_NOTE_AT, db.now_utc())


def note(conn):
    """渡す様子と、その時刻。古くなっていれば (\"\", \"\")。"""
    at = db.get_state(conn, db.SELF_NOTE_AT)
    if db.seconds_since(at) > NOTE_KEEP_HOURS * 3600:
        return "", ""
    return db.get_state(conn, db.SELF_NOTE) or "", at
