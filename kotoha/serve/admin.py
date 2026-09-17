"""画面から直せるもの。ことばの元の文と、よく触る設定。

外出先から直すことを想定しているので、壊したときに戻せることを優先する。
プロンプトは保存のたびに直前を1世代だけ控え、設定は範囲を検査してから書く。
"""

import importlib
import re

from .. import config
from ..settings import read_env

# 画面に出す3つ。ここにない名前は受け付けない。
PROMPTS = {
    "persona": "人格・話し方",
    "fixed_rules": "会話の決まりごと",
    "consolidation_system": "記憶整理の指示",
}
MAX_PROMPT_CHARS = 8000

# 画面に出す設定。範囲は config.py の検査と合わせてある。
SETTINGS = [
    {"key": "KOTOHA_TEMPERATURE", "label": "返答のふり幅", "type": "number",
     "min": 0, "max": 2, "step": 0.1,
     "note": "小さいほど落ち着いた返事になる"},
    {"key": "KOTOHA_MAX_OUTPUT_TOKENS", "label": "返答の長さの上限", "type": "int",
     "min": 1, "max": 8192,
     "note": "大きくすると長く話せるが、声になるまで待つ"},
    {"key": "KOTOHA_RECENT_TURNS", "label": "覚えている往復の数", "type": "int",
     "min": 1, "max": 50,
     "note": "多いほど話が続くが、返答は遅くなる"},
    {"key": "KOTOHA_RECENT_CHARS", "label": "覚えている会話の文字数", "type": "int",
     "min": 1, "max": 20000,
     "note": "同上。既定は3000"},
    {"key": "KOTOHA_IDLE_SECONDS", "label": "記憶整理までの間（秒）", "type": "number",
     "min": 0.1, "max": 86400,
     "note": "会話が途切れてこの時間がたつと整理する"},
    {"key": "KOTOHA_VOICE_STYLE_ID", "label": "声の種類（スタイルID）", "type": "int",
     "min": 0, "max": 2147483647,
     "note": "音声エンジンの /speakers で調べた番号"},
]
_BY_KEY = {item["key"]: item for item in SETTINGS}


class AdminError(Exception):
    pass


# --- ことばの元の文 ---

def _prompt_path(name):
    if name not in PROMPTS:
        raise AdminError("その名前の文はありません。")
    return config.PROMPTS_DIR / f"{name}.txt"


def prompt_backup(name):
    return _prompt_path(name).with_suffix(".bak")


def read_prompt(name) -> str:
    path = _prompt_path(name)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_prompt(name, text: str) -> None:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise AdminError("空では保存できません。")
    if len(text) > MAX_PROMPT_CHARS:
        raise AdminError(f"長すぎます。{MAX_PROMPT_CHARS}文字までにしてください。")
    path = _prompt_path(name)
    if path.exists():
        prompt_backup(name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def revert_prompt(name) -> str:
    backup = prompt_backup(name)
    if not backup.exists():
        raise AdminError("戻せる内容がありません。")
    text = backup.read_text(encoding="utf-8")
    path = _prompt_path(name)
    # 今の内容と入れ替える。戻したあともう一度押せば元に戻せる。
    backup.write_text(path.read_text(encoding="utf-8") if path.exists() else "", encoding="utf-8")
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


# --- よく触る設定 ---

def read_settings() -> list:
    values = read_env(config.BASE_DIR / ".env.example")
    values.update(read_env(config.BASE_DIR / ".env"))
    return [{**item, "value": values.get(item["key"], "")} for item in SETTINGS]


def _clean(key, raw) -> str:
    item = _BY_KEY.get(key)
    if item is None:
        raise AdminError(f"{key} は画面から変えられません。")
    try:
        number = float(str(raw).strip())
    except ValueError:
        raise AdminError(f"{item['label']}: 数値で入れてください。") from None
    if item["type"] == "int":
        if number != int(number):
            raise AdminError(f"{item['label']}: 整数で入れてください。")
        number = int(number)
    if not item["min"] <= number <= item["max"]:
        raise AdminError(f"{item['label']}: {item['min']}〜{item['max']} の範囲にしてください。")
    return str(number)


def write_settings(values: dict) -> None:
    """.env を書き換えてから設定を読み直す。プロセスは止めない。"""
    cleaned = {key: _clean(key, raw) for key, raw in values.items()}
    if not cleaned:
        return
    path = config.BASE_DIR / ".env"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []

    remaining = dict(cleaned)
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"
    if remaining:
        lines.append("")
        lines.append("# 画面から変更した設定")
        lines.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    reload_config()


def reload_config() -> None:
    """config を読み直す。各モジュールは config.XXX 参照なので、これで新しい値になる。"""
    importlib.reload(config)
