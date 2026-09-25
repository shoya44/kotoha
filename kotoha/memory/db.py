import contextlib
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import clock, config, notify

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

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  due_at TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  done_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(done_at, due_at);

CREATE TABLE IF NOT EXISTS diary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL,
  first_at TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  retired_at TEXT
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

CREATE TABLE IF NOT EXISTS memory_vectors (
  node_id INTEGER PRIMARY KEY REFERENCES memory_nodes(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS diary_vectors (
  diary_id INTEGER PRIMARY KEY REFERENCES diary(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  vector BLOB NOT NULL
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
    return clock.utc()


def seconds_since(value) -> float:
    """記録からの経過秒。未記録・壊れた記録は「十分昔」として扱う。"""
    if not value:
        return float("inf")
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (clock.utc_now() - dt).total_seconds()


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    # 読む側と書く側が互いを待たない（WAL）。脳の中では会話・巡回・器の取次が
    # 別々の糸から繋ぎ、外からはCUIも同じファイルを開く。巻き戻し日誌のままだと
    # 読んでいるだけの相手にも書き手が待たされ、5秒を越えて「database is locked」
    # になった（2026-09-23）。設定はファイルに残るので、一度効けば毎回同じ。
    # DBの隣に -wal / -shm ができる。控えは backup API で取るので、そこも含めて写る。
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# 既にあるDBに、あとから足りない形を足すための一覧。
# (版, [SQL...]) を古い順に並べる。**一度入れたものは書き換えない。**
# 書き換えると、途中の版で止まっているDBだけが別の形になる。
#   例: (1, ["ALTER TABLE messages ADD COLUMN vessel TEXT"]),
MIGRATIONS: list = [
    # 頼まれごとの繰り返し（毎日/平日）と、追いかけの段数（本人に頼まれたものは 0）。
    (1, ["ALTER TABLE reminders ADD COLUMN repeat TEXT",
         "ALTER TABLE reminders ADD COLUMN chain INTEGER NOT NULL DEFAULT 0"]),
    # 記憶の強さ。期限は強さから導くようになった（memory/strength.py）。
    # 既存の記憶には、残り日数から逆算した強さを入れる（migrate の後段）。
    (2, ["ALTER TABLE memory_nodes ADD COLUMN strength REAL NOT NULL DEFAULT 1.0",
         "ALTER TABLE memory_nodes ADD COLUMN strength_at TEXT"]),
    # 電話で知らせる頼まれごと（モーニングコールなど）。1 なら着信にする。
    (3, ["ALTER TABLE reminders ADD COLUMN phone INTEGER NOT NULL DEFAULT 0"]),
]


def migrate(conn: sqlite3.Connection) -> None:
    """DBの版を見て、足りないぶんだけ順に当てる。

    `CREATE TABLE IF NOT EXISTS` は、既にある表には何もしない。だから
    列を1つ足したくなった日、新しいDBだけが新しい形になり、**手元の、
    いちばん大事な1つだけが古いまま静かに置いていかれる**。版を数えて
    おけば、その日に慌てずに済む。
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, statements in MIGRATIONS:
        if version >= target:
            continue
        for sql in statements:
            conn.execute(sql)
        if target == 2:
            from . import strength          # 循環を避けて、ここで読む
            strength.backfill(conn)
        conn.execute(f"PRAGMA user_version = {target}")
        version = target
    conn.commit()


def init(conn: sqlite3.Connection) -> None:
    """表を揃える。**1つのトランザクションで。**

    文ごとに確定させると、そのたびにディスクへ書き切る（fsync）。表と索引で
    十数文あり、実測で1回 51ms。まとめれば 5.6ms で、途中で落ちても半端な
    形が残らない。テストは毎回まっさらなDBから始めるので、ここが 400回以上
    走る。
    """
    conn.executescript("BEGIN;" + SCHEMA + "COMMIT;")
    migrate(conn)


def forget_states(conn, prefix: str, keep) -> None:
    """同じ接頭辞の書き置きのうち、もう要らないものを消す。

    セッションのように相手が入れ替わるものは、名前ごとに1行ずつ増える。
    見なくなったぶんを残すと、app_state がじわじわ太る。
    """
    keep = list(keep)
    ph = ",".join("?" * len(keep))
    sql = "DELETE FROM app_state WHERE key LIKE ?"
    if keep:
        sql += f" AND key NOT IN ({ph})"
    conn.execute(sql, (prefix + "%", *keep))


def count_mood_change(conn, turn_id: int) -> None:
    """機嫌が動いた。**書くのは動いたときだけ**なので、ほとんどの回は何もしない。

    分母（総ターン数）は messages から数えられるので、ここでは持たない。
    """
    if get_state(conn, MOOD_COUNTED_FROM) is None:
        set_state(conn, MOOD_COUNTED_FROM, turn_id)
    set_state(conn, MOOD_CHANGES, int(get_state(conn, MOOD_CHANGES, "0") or 0) + 1)


def mood_rate(conn):
    """機嫌が動いた回数と、そのあいだのターン数。数え始めていなければ None。"""
    began = get_state(conn, MOOD_COUNTED_FROM)
    if began is None:
        return None
    turns = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE role = 'assistant' AND turn_id >= ?",
        (int(began),),
    ).fetchone()["n"]
    return int(get_state(conn, MOOD_CHANGES, "0") or 0), turns


def start_or_resume_turn(conn, text: str) -> int:
    """発言を1件書いて番号を返す。**同じ発言の送り直しは、同じ往復に入れる。**

    生成に失敗した回は、発言だけが残って返事が無い。そこへ送り直すと新しい
    往復になり、画面に同じ発言が2行並ぶ。聞き返されたのではなく、無視された
    末に自分で言い直したように見える。UNIQUE(turn_id, role) がちょうど
    「ひとつの往復に返事はひとつ」を許すので、空いている枠へ入れる。

    入れ直すのは、**いちばん新しい往復が「発言だけ・同じ文」のとき**だけ。
    返事のある往復にも、ことはから始まった往復にも触れない。
    """
    last = conn.execute("SELECT MAX(turn_id) AS turn FROM messages").fetchone()["turn"]
    if last:
        rows = conn.execute(
            "SELECT role, text FROM messages WHERE turn_id = ?", (last,)
        ).fetchall()
        if [r["role"] for r in rows] == ["user"] and rows[0]["text"] == text:
            return last
    return start_turn(conn, "user", text)


def insert_message(conn, turn_id: int, role: str, text: str, extractable: int = 1) -> None:
    conn.execute(
        "INSERT INTO messages(turn_id, role, text, created_at, extractable) VALUES (?,?,?,?,?)",
        (turn_id, role, text, now_utc(), extractable),
    )


def start_turn(conn, role: str, text: str, extractable: int = 1) -> int:
    """新しい往復として1件書き、使った番号を返す。

    **番号を見るのと書くのを、1つの文で済ませる。** 2つに分けると、その
    あいだにもう一方が書き込んだとき同じ番号になり、UNIQUE(turn_id, role)
    で落ちる（2026-09-18に一度起きた）。SQLiteは書き込みを文の単位で
    直列化するので、この形なら同じ番号は出ない。
    """
    cursor = conn.execute(
        "INSERT INTO messages(turn_id, role, text, created_at, extractable) "
        "VALUES ((SELECT COALESCE(MAX(turn_id), 0) + 1 FROM messages), ?,?,?,?)",
        (role, text, now_utc(), extractable),
    )
    return conn.execute(
        "SELECT turn_id AS n FROM messages WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()["n"]


def fetch_recent(conn, turns: int, char_limit: int):
    rows = conn.execute(
        "SELECT turn_id, role, text, created_at, extractable FROM messages "
        "ORDER BY turn_id DESC, id DESC LIMIT ?",
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


def overdue(conn, key: str, seconds: float) -> bool:
    """前にやってから、その時間がたったか。一度もやっていなければ、たったものとして扱う。

    整理も、バックアップも、忘却も、声かけも、通知の間隔も、やることは同じ。
    「前にやったのを覚えていて、間を空ける」。人がそうしているのと変わらない。
    """
    return seconds_since(get_state(conn, key)) > seconds


def done_today(conn, key: str, day: str) -> bool:
    """その日のぶんを、もう済ませたか。day は呼ぶ側が決める。

    朝のひとことは暦の日付で数え、夜更かしは日付をまたぐので朝を境にする。
    どちらを使うかは、その場でしか決められない。
    """
    return get_state(conn, key) == day


def mark_today(conn, key: str, day: str) -> None:
    """済ませた印を付ける。**やる前に付ける。** 失敗しても毎分やり直させない。"""
    set_state(conn, key, day)
    conn.commit()


# --- app_state に置く覚えごと ---
# キーの名前はここにだけ書く。打ち間違えても誰も教えてくれないため。
# **値はディスクに残っている。** 名前を変えると、それまでの覚えごとが迷子になる。
LAST_CONVERSATION_AT = "last_conversation_at"
LAST_NOTIFY_AT = "last_notify_at"
LAST_BACKUP_AT = "last_backup_at"
LAST_FORGET_AT = "last_forget_at"
LAST_CONSOLIDATION_AT = "last_consolidation_at"
CONSOLIDATE_FAILS = "consolidate_fails"
LAST_PROCESSED_MESSAGE_ID = "last_processed_message_id"
LAST_REACH_OUT_AT = "last_reach_out_at"
LAST_BRIEFING_ON = "last_briefing_on"
LAST_DIARY_ON = "last_diary_on"
# 日記が書けなかった時刻と、その日に何度しくじったか（"YYYY-MM-DD:n"）。
# 1時間おきに数回まで試し直す。その日を捨てないため（serve/jobs.py の maybe_diary）。
DIARY_FAILED_AT = "diary_failed_at"
DIARY_FAILS = "diary_fails"
# 朝に取った今日の空模様（{"on": 日付, "text": 1行}）。その日のあいだ、状況の行に添える。
SKY_TODAY = "sky_today"
LAST_HABITS_AT = "last_habits_at"
# 相手への信頼。習慣と同じ振り返りで週に一度動く。叱るかどうかの根拠。
TRUST = "trust"
TRUST_WHY = "trust_why"
LAST_REVIEW_ON = "last_review_on"
# 思い出しかけて出なかった記憶。会話が途切れたあとに「そういえば」と言う（1日1回）。
AFTERTHOUGHT_ID = "afterthought_id"
AFTERTHOUGHT_AT = "afterthought_at"
LAST_AFTERTHOUGHT_ON = "last_afterthought_on"
LAST_AFTERTHOUGHT_ID = "last_afterthought_id"
LAST_LATE_NIGHT_ON = "last_late_night_on"
LAST_LINKED_NODE_ID = "last_linked_node_id"
PENDING_RECONSOLIDATION_IDS = "pending_reconsolidation_ids"
MOOD = "mood"
MOOD_AT = "mood_at"
# さっきまでの話題。返事の [TOPIC:] で動き、TOPIC_KEEP_HOURS で忘れる。
TOPIC = "topic"
TOPIC_AT = "topic_at"
# 直前の返事の顔。返事の [FACE:] で書き、話した直後（figure.TALK_SECONDS）だけ絵に効く。
FACE = "face"
FACE_AT = "face_at"
# 機嫌が動いた回数と、数え始めた地点。**測ってから決めるため。**
# プロンプトは「変わったときだけ」機嫌を出させるが、実際にどれくらいの頻度で
# 出るのかは残っていなかった（持っているのは今の機嫌だけ）。「今の機嫌: ふつう」が
# 壁紙になっていないかは、数えないと分からない。
MOOD_CHANGES = "mood_changes"
MOOD_COUNTED_FROM = "mood_counted_from"
# 実体（姿を出している器）の居場所。正は serve/hub.py が持つメモリで、
# ここにあるのはその写し。ことは自身に居場所を言わせるために使う。
BODY_WHERE = "body_where"
VESSEL_PREFIX = "vessel:"
VESSEL_NOTE_PREFIX = "vessel_note:"
QUIET_UNTIL = "quiet_until"
GROWTH = "growth"
HELD_ANNOUNCEMENTS = "held_announcements"
HELD_FAILS = "held_fails"
# いま鳴らしている着信（JSON）。出るか、出ないまま時間が来たら消える。
RING = "ring"
# 出てもらえなかった着信（JSON）。かけ直してもらったとき、ことはが先に話すのに使う。
MISSED_RING = "missed_ring"
LAST_REACH_CALL_ON = "last_reach_call_on"
FRONT_TALLY = "front_tally"
FRONT_TALLY_HOUR = "front_tally_hour"
FRONT_STREAK_APP = "front_streak_app"
FRONT_STREAK_FROM = "front_streak_from"
# 道具ごとの生死と、ドライブごとの空き。うしろに相手の名前が付く。
UP_PREFIX = "up:"
DISK_PREFIX = "disk:"
# Claude Code のセッションごとの様子。うしろにセッションの id が付く（talk/coding.py）。
CODING_PREFIX = "coding:"
# いまの機嫌になったきっかけ。機嫌と一緒に書き、機嫌が薄れれば読まれない。
MOOD_WHY = "mood_why"
# 自分の様子（talk/myself.py）。生きている印は巡回が毎分書き、起きたときに
# その差が「止まっていた長さ」になる。中身と設定の印は、起きたときに見比べる。
LAST_ALIVE_AT = "last_alive_at"
SELF_CODE = "self_code"
SELF_SETTINGS = "self_settings"
SELF_NOTE = "self_note"
SELF_NOTE_AT = "self_note_at"


@contextlib.contextmanager
def session():
    """つないで、終わったら必ず閉じる。

    同じ7行が23か所に書かれていた。閉じ忘れは静かに増えるので、
    入口をひとつにする。
    """
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


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

# 「今」は SQLite の 'now' ではなく、Python の時計（clock.py）を束縛して渡す。
# 二つの時計が別々に刻むと、時間を進めたテストで片方だけが動く。列に関数を
# 当てないので expires_at のインデックスもそのまま効く。
def alive_sql(column: str = "expires_at") -> str:
    """生きている記憶の条件。`?` が1つ増えるので、引数に now_utc() を足す。"""
    return f"({column} IS NULL OR {column} > ?)"


def shifted(days: float) -> str:
    """今から days 日後（負なら前）の、DBに書く形。"""
    return (clock.utc_now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")




def update_usage(conn, node_ids: list, turn_id: int) -> None:
    if not node_ids:
        return
    from . import strength          # 循環を避けて、ここで読む
    now = now_utc()
    for nid in node_ids:
        # 思い出した記憶は強くなり、消える時刻が遠のく。触れられない記憶だけが薄れる。
        strength.reinforce(conn, nid)
        conn.execute("UPDATE memory_nodes SET last_used_at = ? WHERE id = ?", (now, nid))
        conn.execute(
            "UPDATE memory_tags SET use_count = MIN(use_count + 1, 3), "
            "last_used_at = ?, last_used_turn_id = ? WHERE node_id = ?",
            (now, turn_id, nid),
        )


def run_maintenance(conn) -> int:
    cur = conn.execute(
        "DELETE FROM memory_nodes WHERE pinned = 0 "
        "AND expires_at IS NOT NULL AND expires_at < ?", (now_utc(),)
    )
    conn.execute(
        "UPDATE memory_tags SET use_count = 0 "
        "WHERE last_used_at IS NOT NULL AND last_used_at < ?",
        (shifted(-config.TAG_RESET_DAYS),),
    )
    set_state(conn, LAST_FORGET_AT, now_utc())
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
    val = get_state(conn, PENDING_RECONSOLIDATION_IDS, "")
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
    set_state(conn, PENDING_RECONSOLIDATION_IDS, val)


def backup(dest_path) -> None:
    src = connect()
    dst = sqlite3.connect(dest_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()


def backup_dir():
    return config.DB_PATH.parent / "backups"


def spare_dir():
    """控えのもう1本を置く先。設定していなければ None。

    **ことはの本体は記憶です。** DBの隣にしか控えが無いと、ディスクが1枚
    壊れた日にすべて消える。別ドライブや同期フォルダーを指しておくと、
    最新の1本だけがそこへも渡る。
    """
    return Path(config.BACKUP_DIR) if config.BACKUP_DIR else None


def _copy_to_spare(dest) -> None:
    """取れた控えを、もう1本の置き場へも渡す。**ここの失敗で控えは無効にしない。**

    外付けが外れている、同期フォルダーが落ちている。どれも起こるが、
    それでDBの隣の1本まで失う理由はない。黙っては済ませず、log に残す。
    """
    spare = spare_dir()
    if spare is None:
        return
    try:
        # 置き場がファイルだと mkdir が FileExistsError を返し、何が悪いのか
        # 読めない行が毎回並んだ。何を直せばよいかまで書く。
        if spare.exists() and not spare.is_dir():
            raise NotADirectoryError(f"{spare} はフォルダーではない（KOTOHA_BACKUP_DIR を見直す）")
        spare.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, spare / dest.name)
        for old in sorted(spare.glob("kotoha_*.sqlite3"))[: -config.BACKUP_KEEP]:
            old.unlink()
    except OSError as error:
        notify.log(f"控えのもう1本を置けなかった: {error!r}")


def run_backup(conn=None):
    """世代付きのバックアップを1本取り、古い世代を消す。

    DBがまだ無ければ何もせず None を返す。conn を渡すと実行時刻を記録し、
    次の自動バックアップまでの間隔を数えられるようにする。
    """
    if not config.DB_PATH.exists():
        return None
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"kotoha_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    backup(dest)
    for old in sorted(folder.glob("kotoha_*.sqlite3"))[: -config.BACKUP_KEEP]:
        old.unlink()
    _copy_to_spare(dest)
    if conn is not None:
        set_state(conn, LAST_BACKUP_AT, now_utc())
        conn.commit()
    return dest
