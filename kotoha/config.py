"""設定の読み込み・検査。初期値は .env.example、編集先は .env。"""

import math
import os
from pathlib import Path

from .settings import read_env

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = BASE_DIR / "prompts"

_values = read_env(BASE_DIR / ".env.example")
_values.update(read_env(BASE_DIR / ".env"))
# 既存の環境変数による上書きは互換性のため維持する。
_values.update({key: os.environ[key] for key in _values if key in os.environ})


def _text(key, allow_empty=False):
    value = _values[key]
    if not allow_empty and not value:
        raise SystemExit(f"設定エラー: {key} は空欄にできません。settings.batで修正してください。")
    return value


def _number(key, kind=int, minimum=1, maximum=None):
    try:
        value = kind(_text(key))
    except ValueError:
        raise SystemExit(f"設定エラー: {key} は数値で指定してください。") from None
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        upper = f"〜{maximum}" if maximum is not None else "以上"
        raise SystemExit(f"設定エラー: {key} は{minimum}{upper}の範囲で指定してください。")
    return value


def _boolean(key):
    value = _text(key).lower()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise SystemExit(f"設定エラー: {key} はtrueまたはfalseで指定してください。")


GEMINI_API_KEY = _text("GEMINI_API_KEY", allow_empty=True)
GEMINI_MODEL = _text("GEMINI_MODEL")
GEMINI_BASE_URL = _text("GEMINI_BASE_URL").rstrip("/")
_db_path = Path(_text("KOTOHA_DB_PATH"))
DB_PATH = _db_path if _db_path.is_absolute() else BASE_DIR / _db_path
RECENT_TURNS = _number("KOTOHA_RECENT_TURNS")
RECENT_CHARS = _number("KOTOHA_RECENT_CHARS")
MAX_OUTPUT_TOKENS = _number("KOTOHA_MAX_OUTPUT_TOKENS")
TIMEOUT_SECONDS = _number("KOTOHA_TIMEOUT_SECONDS", float, minimum=0.1)
TEMPERATURE = _number("KOTOHA_TEMPERATURE", float, minimum=0, maximum=2)
LLM_ATTEMPTS = _number("KOTOHA_LLM_ATTEMPTS")
FAST_ENABLED = _boolean("KOTOHA_FAST_ENABLED")
FAST_MAX_INPUT_CHARS = _number("KOTOHA_FAST_MAX_CHARS")
DEBUG = _boolean("KOTOHA_DEBUG")
WEB_TOKEN = _text("KOTOHA_WEB_TOKEN", allow_empty=True)
WEB_HOST = _text("KOTOHA_WEB_HOST")
WEB_PORT = _number("KOTOHA_WEB_PORT", maximum=65535)
WEB_HISTORY_LIMIT = _number("KOTOHA_WEB_HISTORY_LIMIT")
BACKUP_KEEP = _number("KOTOHA_BACKUP_KEEP")
BACKUP_INTERVAL_SECONDS = _number("KOTOHA_BACKUP_INTERVAL_SECONDS", float, minimum=0.1)
BROWSER_AUTO_OPEN = _boolean("KOTOHA_BROWSER_AUTO_OPEN")
TAILSCALE_AUTO_START = _boolean("KOTOHA_TAILSCALE_AUTO_START")
TAILSCALE_SERVE_ENABLED = _boolean("KOTOHA_TAILSCALE_SERVE_ENABLED")
TAILSCALE_HTTPS_PORT = _number("KOTOHA_TAILSCALE_HTTPS_PORT", maximum=65535)
_tail_path = _text("KOTOHA_TAILSCALE_DIR", allow_empty=True)
TAILSCALE_DIR = (
    Path(_tail_path) if _tail_path
    else Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale"
)
if not TAILSCALE_DIR.is_absolute():
    TAILSCALE_DIR = BASE_DIR / TAILSCALE_DIR
STARTUP_TIMEOUT_SECONDS = _number("KOTOHA_STARTUP_TIMEOUT_SECONDS", float, minimum=0.1)
CONSOLIDATE_TURNS = _number("KOTOHA_CONSOLIDATE_TURNS")
BACKGROUND_INTERVAL_SECONDS = _number("KOTOHA_BACKGROUND_INTERVAL_SECONDS", float, minimum=0.1)
IDLE_SECONDS = _number("KOTOHA_IDLE_SECONDS", float, minimum=0.1)
BATCH_TURNS = _number("KOTOHA_BATCH_TURNS")
BATCH_CHARS = _number("KOTOHA_BATCH_CHARS")
CONSOLIDATION_MAX_TOKENS = _number("KOTOHA_CONSOLIDATION_MAX_TOKENS")
EPISODE_DAYS = _number("KOTOHA_EPISODE_DAYS")
SEMANTIC_DAYS = _number("KOTOHA_SEMANTIC_DAYS")
MAINTENANCE_SECONDS = _number("KOTOHA_MAINTENANCE_SECONDS", float, minimum=0.1)
TAG_RESET_DAYS = _number("KOTOHA_TAG_RESET_DAYS")
TAG_CANDIDATE_LIMIT = _number("KOTOHA_TAG_CANDIDATE_LIMIT")
HOP_LIMIT = _number("KOTOHA_HOP_LIMIT")
RETRIEVE_RECENT_LIMIT = _number("KOTOHA_RETRIEVE_RECENT_LIMIT")
PINNED_LIMIT = _number("KOTOHA_PINNED_LIMIT")
RELATED_LIMIT = _number("KOTOHA_RELATED_LIMIT")


def require_keys():
    if not GEMINI_API_KEY or GEMINI_API_KEY.startswith(("AIza...", "<")):
        raise SystemExit(".env の GEMINI_API_KEY が未設定です。")


if __name__ == "__main__":
    print("設定形式の確認: OK")
    if not GEMINI_API_KEY:
        print("未設定: GEMINI_API_KEY（会話・整理で必要）")
    if not WEB_TOKEN:
        print("未設定: KOTOHA_WEB_TOKEN（Web起動で必要）")
    overridden = [key for key in read_env(BASE_DIR / ".env.example") if key in os.environ]
    if overridden:
        print("環境変数が優先される項目: " + ", ".join(overridden))
    print("保存した設定は次回起動時に反映されます。")
