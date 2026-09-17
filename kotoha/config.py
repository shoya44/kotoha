"""設定の読み込み・検査。初期値は .env.example、編集先は .env。"""

import math
import os
import shutil
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
VOICE_ENABLED = _boolean("KOTOHA_VOICE_ENABLED")
VOICE_BASE_URL = _text("KOTOHA_VOICE_BASE_URL").rstrip("/")
VOICE_STYLE_ID = _number("KOTOHA_VOICE_STYLE_ID", minimum=0)
VOICE_TIMEOUT_SECONDS = _number("KOTOHA_VOICE_TIMEOUT_SECONDS", float, minimum=0.1)
AIVIS_AUTO_START = _boolean("KOTOHA_AIVIS_AUTO_START")
_aivis_path = _text("KOTOHA_AIVIS_DIR", allow_empty=True)
AIVIS_DIR = (
    Path(_aivis_path) if _aivis_path
    else Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "AivisSpeech"
)
if not AIVIS_DIR.is_absolute():
    AIVIS_DIR = BASE_DIR / AIVIS_DIR
EMBED_ENABLED = _boolean("KOTOHA_EMBED_ENABLED")
EMBED_BASE_URL = _text("KOTOHA_EMBED_BASE_URL").rstrip("/")
EMBED_MODEL = _text("KOTOHA_EMBED_MODEL")
EMBED_KEEP_ALIVE = _text("KOTOHA_EMBED_KEEP_ALIVE")
EMBED_TIMEOUT_SECONDS = _number("KOTOHA_EMBED_TIMEOUT_SECONDS", float, minimum=0.1)
EMBED_BUILD_TIMEOUT_SECONDS = _number("KOTOHA_EMBED_BUILD_TIMEOUT_SECONDS", float, minimum=0.1)
EMBED_BATCH = _number("KOTOHA_EMBED_BATCH")
EMBED_MAX_CHARS = _number("KOTOHA_EMBED_MAX_CHARS")
EMBED_FLOOR = _number("KOTOHA_EMBED_FLOOR", float, minimum=0, maximum=1)
EMBED_RESERVE = _number("KOTOHA_EMBED_RESERVE", minimum=0)
EMBED_CONTEXT_LINES = _number("KOTOHA_EMBED_CONTEXT_LINES", minimum=0)
EMBED_RETRY_SECONDS = _number("KOTOHA_EMBED_RETRY_SECONDS", float, minimum=0)
EMBED_MERGE_FLOOR = _number("KOTOHA_EMBED_MERGE_FLOOR", float, minimum=0, maximum=1)
EMBED_LINK_FLOOR = _number("KOTOHA_EMBED_LINK_FLOOR", float, minimum=0, maximum=1)
EMBED_LINK_LIMIT = _number("KOTOHA_EMBED_LINK_LIMIT", minimum=0)
OLLAMA_AUTO_START = _boolean("KOTOHA_OLLAMA_AUTO_START")
_ollama_path = _text("KOTOHA_OLLAMA_DIR", allow_empty=True)
if _ollama_path:
    OLLAMA_DIR = Path(_ollama_path)
else:
    # 置き場所は人によって違う。PATHにいればそこを使い、いなければ既定の場所を見る。
    _found = shutil.which("ollama")
    OLLAMA_DIR = (
        Path(_found).parent if _found
        else Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
    )
if not OLLAMA_DIR.is_absolute():
    OLLAMA_DIR = BASE_DIR / OLLAMA_DIR
PRESENCE_ENABLED = _boolean("KOTOHA_PRESENCE_ENABLED")
ACTIONS_ENABLED = _boolean("KOTOHA_ACTIONS_ENABLED")
PUSH_ENABLED = _boolean("KOTOHA_PUSH_ENABLED")
PUSH_SHOW_TEXT = _boolean("KOTOHA_PUSH_SHOW_TEXT")
PUSH_OPEN_URL = _text("KOTOHA_PUSH_OPEN_URL", allow_empty=True)
ONESIGNAL_APP_ID = _text("KOTOHA_ONESIGNAL_APP_ID", allow_empty=True)
ONESIGNAL_API_KEY = _text("KOTOHA_ONESIGNAL_API_KEY", allow_empty=True)
REACH_OUT_ENABLED = _boolean("KOTOHA_REACH_OUT_ENABLED")
REACH_OUT_AFTER_HOURS = _number("KOTOHA_REACH_OUT_AFTER_HOURS", float, minimum=0.1)
REACH_OUT_INTERVAL_HOURS = _number("KOTOHA_REACH_OUT_INTERVAL_HOURS", float, minimum=0.1)
REACH_OUT_FROM_HOUR = _number("KOTOHA_REACH_OUT_FROM_HOUR", minimum=0, maximum=23)
REACH_OUT_TO_HOUR = _number("KOTOHA_REACH_OUT_TO_HOUR", minimum=0, maximum=23)
DISK_WARN_GB = _number("KOTOHA_DISK_WARN_GB", minimum=0)
BRIEFING_ENABLED = _boolean("KOTOHA_BRIEFING_ENABLED")
LOOKOUT_ENABLED = _boolean("KOTOHA_LOOKOUT_ENABLED")
LOOKOUT_SIT_HOURS = _number("KOTOHA_LOOKOUT_SIT_HOURS", float, minimum=0.5)
LOOKOUT_LATE_HOUR = _number("KOTOHA_LOOKOUT_LATE_HOUR", minimum=0, maximum=23)
LOOKOUT_MORNING_HOUR = _number("KOTOHA_LOOKOUT_MORNING_HOUR", minimum=0, maximum=23)
BRIEFING_HOUR = _number("KOTOHA_BRIEFING_HOUR", minimum=0, maximum=23)
BRIEFING_GRACE_HOURS = _number("KOTOHA_BRIEFING_GRACE_HOURS", minimum=1, maximum=24)
LATITUDE = _number("KOTOHA_LATITUDE", float, minimum=-90, maximum=90)
LONGITUDE = _number("KOTOHA_LONGITUDE", float, minimum=-180, maximum=180)
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
