"""器の登録。接続状態は hub、名前と役割だけをここに保存する。"""

import json
import re

from . import db

PRESETS = {
    "screen": ("会話画面", "呼ばれたときに話す場所"),
    "pc": ("PC", "寝床・作業のそば"),
    "tablet": ("タブレット", "暇な時間の居場所"),
    "iphone": ("iPhone", "持ち歩く器"),
}


def valid_name(name):
    return isinstance(name, str) and re.fullmatch(r"(?:desktop|web-[A-Za-z0-9_-]{1,60})", name)


def get(conn, name):
    raw = db.get_state(conn, db.VESSEL_PREFIX + name)
    if raw:
        return json.loads(raw)
    kind = "pc" if name == "desktop" else "screen"
    return {"kind": kind, "label": PRESETS[kind][0], "role": PRESETS[kind][1], "frame": False}


def save(conn, name, spec):
    if not valid_name(name):
        raise ValueError("器の名前が無効です")
    kind = spec.get("kind")
    label = spec.get("label")
    frame = spec.get("frame", False)
    if kind not in PRESETS or not isinstance(label, str) or not 1 <= len(label.strip()) <= 32:
        raise ValueError("種類と32文字以内の名前を指定してください")
    if not isinstance(frame, bool) or (frame and kind != "tablet"):
        raise ValueError("額縁表示は固定タブレットで使います")
    if name == "desktop" and kind != "pc":
        raise ValueError("デスクトップの役割はPCです")
    profile = {"kind": kind, "label": " ".join(label.split()), "role": PRESETS[kind][1], "frame": frame}
    db.set_state(conn, db.VESSEL_PREFIX + name, json.dumps(profile, ensure_ascii=False))
    return profile
