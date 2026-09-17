import sqlite3
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  extractable INTEGER NOT NULL DEFAULT 1 CHECK (extractable IN (0, 1)),
  UNIQUE (turn_id, role)
);

CREATE TABLE IF NOT EXISTS memory_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  layer TEXT NOT NULL CHECK (layer IN ('episode', 'semantic')),
  kind TEXT NOT NULL CHECK (
    kind IN ('event', 'fact', 'preference', 'open_topic', 'procedure')
  ),
  text TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL,
  expires_at TEXT,
  pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
  source_summary TEXT,
  source_key TEXT NOT NULL UNIQUE,
  CHECK (
    (layer = 'episode' AND kind = 'event')
    OR (layer = 'semantic' AND kind IN ('fact', 'preference', 'open_topic', 'procedure'))
  )
);

CREATE TABLE IF NOT EXISTS memory_edges (
  from_id INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
  to_id INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK (relation IN ('derived_from', 'related_to')),
  PRIMARY KEY (from_id, to_id, relation),
  CHECK (from_id <> to_id)
);

CREATE TABLE IF NOT EXISTS memory_tags (
  node_id INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count BETWEEN 0 AND 3),
  last_used_at TEXT,
  last_used_turn_id INTEGER,
  PRIMARY KEY (node_id, tag)
);

CREATE TABLE IF NOT EXISTS memory_sources (
  node_id INTEGER NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  PRIMARY KEY (node_id, message_id)
);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_created_at_id ON messages(created_at, id);
CREATE INDEX IF NOT EXISTS idx_memory_tags_tag_node_id ON memory_tags(tag, node_id);
CREATE INDEX IF NOT EXISTS idx_memory_nodes_expires_at ON memory_nodes(expires_at);
CREATE INDEX IF NOT EXISTS idx_memory_nodes_layer_confirmed_at ON memory_nodes(layer, confirmed_at);
CREATE INDEX IF NOT EXISTS idx_memory_edges_from_relation ON memory_edges(from_id, relation);
CREATE INDEX IF NOT EXISTS idx_memory_edges_to_relation ON memory_edges(to_id, relation);
CREATE INDEX IF NOT EXISTS idx_memory_sources_message_node ON memory_sources(message_id, node_id);

INSERT OR IGNORE INTO app_state (key, value) VALUES
  ('last_processed_message_id', '0');
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seconds_since(value) -> float:
    """記録からの経過秒。未記録・壊れた記録は「十分昔」として扱う。"""
    if not value:
        return float("inf")
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds()


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_message(conn, turn_id: int, role: str, text: str, extractable: int = 1) -> None:
    conn.execute(
        "INSERT INTO messages(turn_id, role, text, created_at, extractable) VALUES (?,?,?,?,?)",
        (turn_id, role, text, now_utc(), extractable),
    )


def next_turn_id(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(turn_id), 0) + 1 AS n FROM messages").fetchone()["n"]


def fetch_recent(conn, turns: int, char_limit: int):
    rows = conn.execute(
        "SELECT turn_id, role, text, created_at FROM messages ORDER BY turn_id DESC, id DESC LIMIT ?",
        (turns * 2,),
    ).fetchall()
    rows = list(reversed(rows))
    while len(rows) > 1 and sum(len(r["text"]) for r in rows) > char_limit:
        rows.pop(0)
    return rows


def message_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]


def unfinished_turn(conn) -> bool:
    row = conn.execute("SELECT role FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    return row is not None and row["role"] == "user"


def get_state(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO app_state(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# --- 記憶の利用実績・メンテナンス ---

# 比較は now_utc() と同じ形式で行う。datetime('now') はスペース区切りのため
# 文字列比較がずれ、同日中に切れた期限を取りこぼす。列に関数を当てないので
# expires_at のインデックスもそのまま効く。
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
NOW_SQL_OFFSET = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)"


# 想起・再確認された記憶は期限を延ばす。触れられない記憶だけが自然に薄れる。
EXTEND_EXPIRES = (
    "expires_at = CASE WHEN expires_at IS NULL THEN NULL ELSE MAX(expires_at, "
    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', "
    "CASE layer WHEN 'episode' THEN ? ELSE ? END)) END"
)


def extend_args() -> tuple:
    return (f"+{config.EPISODE_DAYS} days", f"+{config.SEMANTIC_DAYS} days")


def update_usage(conn, node_ids: list, turn_id: int) -> None:
    if not node_ids:
        return
    now = now_utc()
    for nid in node_ids:
        conn.execute(
            f"UPDATE memory_nodes SET last_used_at = ?, {EXTEND_EXPIRES} WHERE id = ?",
            (now, *extend_args(), nid),
        )
        conn.execute(
            "UPDATE memory_tags SET use_count = MIN(use_count + 1, 3), "
            "last_used_at = ?, last_used_turn_id = ? WHERE node_id = ?",
            (now, turn_id, nid),
        )


def run_maintenance(conn) -> int:
    cur = conn.execute(
        "DELETE FROM memory_nodes WHERE pinned = 0 "
        f"AND expires_at IS NOT NULL AND expires_at < {NOW_SQL}"
    )
    conn.execute(
        "UPDATE memory_tags SET use_count = 0 "
        f"WHERE last_used_at IS NOT NULL AND last_used_at < {NOW_SQL_OFFSET}",
        (f"-{config.TAG_RESET_DAYS} days",),
    )
    set_state(conn, "last_forget_at", now_utc())
    conn.commit()
    return cur.rowcount


def set_pinned(conn, node_id: int, pinned: bool) -> bool:
    cur = conn.execute("UPDATE memory_nodes SET pinned = ? WHERE id = ?", (1 if pinned else 0, node_id))
    conn.commit()
    return cur.rowcount > 0


def forget_node(conn, node_id: int) -> bool:
    cur = conn.execute("DELETE FROM memory_nodes WHERE id = ?", (node_id,))
    conn.commit()
    return cur.rowcount > 0


def get_pending_ids(conn) -> list:
    val = get_state(conn, "pending_reconsolidation_ids", "")
    if not val:
        return []
    ids = []
    for i in val.split(","):
        try:
            ids.append(int(i.strip()))
        except ValueError:
            pass
    return ids


def set_pending_ids(conn, ids: list) -> None:
    val = ",".join(str(i) for i in ids)
    set_state(conn, "pending_reconsolidation_ids", val)


def backup(dest_path) -> None:
    src = connect()
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
