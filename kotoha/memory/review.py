"""夜の整理。ここ1週間の記憶を読み返して、寄せる・直す・閉じる。

人は寝ているあいだに、その日の出来事を古い記憶と結び直す。整理
（consolidate）は会話から記憶を作る道で、そのとき既にある近い記憶へ
寄せもするが、**できてしまった記憶どうし**を後から見比べる回は無かった。
ここがそれ。1日1回、日記を書いたあとに、記憶自身に向けて回す。

やることは3つだけ。
- 寄せる: 同じことを言っている記憶を1つに（残す方へ出どころとタグを移す）
- 直す:   日記と食い違う記憶を、日記に合わせて書き直す
- 閉じる: 続きのある話（open_topic）で、もう済んだものを事実に変える

ピン留めは触らない。作りはしない（作るのは整理の仕事）。API は1日1回。
"""

import json
from datetime import timedelta

from .. import clock, config, notify
from ..talk import llm
from . import consolidate, db, diary, embed, strength

DAYS = 7
LIMIT_EACH = 4


def due(conn) -> bool:
    """今日ぶんをまだ見ていなくて、昨日の日記が書けているか。"""
    if not (config.DIARY_ENABLED and config.REVIEW_ENABLED):
        return False
    now = clock.now()
    if now.hour < config.DIARY_HOUR:
        return False
    if db.done_today(conn, db.LAST_REVIEW_ON, now.strftime("%Y-%m-%d")):
        return False
    return diary.entry(conn, (now - timedelta(days=1)).date()) is not None


def material(conn):
    """ここ1週間に確かめた記憶（ピン留めは除く）と、続きのある話すべて。"""
    since = (clock.utc_now() - timedelta(days=DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return conn.execute(
        f"SELECT id, layer, kind, text, occurred_at, confirmed_at FROM memory_nodes "
        f"WHERE pinned = 0 AND {db.alive_sql()} AND (confirmed_at >= ? OR kind = 'open_topic') "
        f"ORDER BY id", (db.now_utc(), since),
    ).fetchall()


def _prompt(nodes, entries) -> str:
    from ..talk import chat
    head = chat._read("review_system.txt")
    lines = "\n".join(f"[id:{r['id']}][{r['occurred_at'][5:] if r['layer'] == 'episode' else r['confirmed_at'][5:10]}]"
                      f"[{r['kind']}] {r['text']}" for r in nodes)
    days = "\n".join(f"{r['day']}: {r['text']}" for r in reversed(entries)) or "（無い）"
    return f"{head}\n\nここ1週間の記憶:\n{lines}\n\nここ1週間の日記:\n{days}\n\nJSON:"


def _as_json(raw: str):
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("夜の整理をJSONとして読めなかった。")
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        raise ValueError("夜の整理をJSONとして読めなかった。") from None


def _ints(values, known):
    out = []
    for v in values if isinstance(values, list) else []:
        if isinstance(v, int) and v in known and v not in out:
            out.append(v)
    return out


def merge(conn, keep: int, drop) -> int:
    """drop を keep に寄せる。出どころ・タグ・繋がりは残す方へ。寄せた数を返す。"""
    moved = 0
    for gone in drop:
        if gone == keep:
            continue
        conn.execute("INSERT OR IGNORE INTO memory_sources(node_id, message_id) "
                     "SELECT ?, message_id FROM memory_sources WHERE node_id = ?", (keep, gone))
        conn.execute("INSERT OR IGNORE INTO memory_tags(node_id, tag) "
                     "SELECT ?, tag FROM memory_tags WHERE node_id = ?", (keep, gone))
        conn.execute("INSERT OR IGNORE INTO memory_edges(from_id, to_id, relation) "
                     "SELECT ?, to_id, relation FROM memory_edges WHERE from_id = ? AND to_id != ?",
                     (keep, gone, keep))
        conn.execute("INSERT OR IGNORE INTO memory_edges(from_id, to_id, relation) "
                     "SELECT from_id, ?, relation FROM memory_edges WHERE to_id = ? AND from_id != ?",
                     (keep, gone, keep))
        moved += conn.execute("DELETE FROM memory_nodes WHERE id = ? AND pinned = 0", (gone,)).rowcount
    if moved:
        strength.reinforce(conn, keep)          # 何度も出てきた＝確かめ直した
    return moved


def fix(conn, node_id: int, text: str) -> bool:
    """本文を日記に合わせて直す。整理の updates と同じ道。"""
    row = conn.execute("SELECT layer FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return False
    text = " ".join(text.split())[:consolidate.text_limit(row["layer"])]
    if not text:
        return False
    strength.reinforce(conn, node_id)
    changed = conn.execute("UPDATE memory_nodes SET text = ?, confirmed_at = ? WHERE id = ? AND pinned = 0",
                           (text, db.now_utc(), node_id)).rowcount
    if changed:
        conn.execute("UPDATE memory_tags SET use_count = 0 WHERE node_id = ?", (node_id,))
        embed.drop(conn, node_id)          # 本文が変わったので座標も古い
    return bool(changed)


def close(conn, node_id: int, text: str = "") -> bool:
    """続きのある話を閉じる。事実（fact）に変え、書き直しがあれば本文も。"""
    row = conn.execute("SELECT kind, text, layer FROM memory_nodes WHERE id = ? AND pinned = 0",
                       (node_id,)).fetchone()
    if not row or row["kind"] != "open_topic":
        return False
    text = " ".join((text or row["text"]).split())[:consolidate.text_limit(row["layer"])] or row["text"]
    conn.execute("UPDATE memory_nodes SET kind = 'fact', text = ?, confirmed_at = ? WHERE id = ?",
                 (text, db.now_utc(), node_id))
    if text != row["text"]:
        embed.drop(conn, node_id)
    return True


def apply(conn, data, known) -> dict:
    """読めたぶんだけ当てる。数は {寄せた, 直した, 閉じた}。"""
    done = {"merged": 0, "fixed": 0, "closed": 0}
    if not isinstance(data, dict):
        return done
    for spec in (data.get("merge") or [])[:LIMIT_EACH]:
        if not isinstance(spec, dict):
            continue
        keep, drop = spec.get("keep"), _ints(spec.get("drop"), known)
        if isinstance(keep, int) and keep in known and drop:
            done["merged"] += merge(conn, keep, drop)
            known -= set(drop)
    for spec in (data.get("fix") or [])[:LIMIT_EACH]:
        if isinstance(spec, dict) and isinstance(spec.get("id"), int) and spec["id"] in known:
            done["fixed"] += fix(conn, spec["id"], spec.get("text") or "")
    for spec in (data.get("close") or [])[:LIMIT_EACH]:
        if isinstance(spec, dict) and isinstance(spec.get("id"), int) and spec["id"] in known:
            done["closed"] += close(conn, spec["id"], spec.get("text") or "")
    return done


def run(conn) -> dict:
    """1日1回。印は先に付け、失敗しても毎分やり直さない。"""
    db.mark_today(conn, db.LAST_REVIEW_ON, clock.now().strftime("%Y-%m-%d"))
    nodes = material(conn)
    if len(nodes) < 2:
        return {"merged": 0, "fixed": 0, "closed": 0}
    entries = diary.recent(conn, DAYS)
    raw = llm.chat(_prompt(nodes, entries), max_tokens=config.CONSOLIDATION_MAX_TOKENS)
    done = apply(conn, _as_json(raw), {r["id"] for r in nodes})
    conn.commit()
    if any(done.values()):
        notify.log(f"夜の整理: 寄せた{done['merged']} 直した{done['fixed']} 閉じた{done['closed']}")
    return done
