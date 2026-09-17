import json
from datetime import datetime, timedelta, timezone

from . import config, db, llm, retrieve

MAX_NEW_NODES = 8


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return _utc_now()


def fetch_unprocessed(conn):
    last = int(db.get_state(conn, "last_processed_message_id", "0") or 0)
    rows = conn.execute(
        "SELECT id, turn_id, role, text, created_at FROM messages "
        "WHERE id > ? AND extractable = 1 ORDER BY id",
        (last,),
    ).fetchall()
    turns, chars, out = [], 0, []
    for r in rows:
        if r["turn_id"] not in turns:
            turns.append(r["turn_id"])
        # 先頭の1通は上限を超えていても必ず含める。ここで空を返すと
        # last_processed_message_id が永久に進まず、記憶が作られなくなる。
        if out and (len(turns) > config.BATCH_TURNS or chars + len(r["text"]) > config.BATCH_CHARS):
            break
        chars += len(r["text"])
        out.append(r)
    return out


def _clip(text: str) -> str:
    """上限超えの1通を切り詰める。落とすのではなく、入る分だけ記憶に残す。"""
    if len(text) <= config.BATCH_CHARS:
        return text
    return text[: config.BATCH_CHARS] + "…（以下省略）"


def _normalize_tag(tag: str) -> str:
    return " ".join(str(tag).strip().split())[:24]


def _existing_block(conn, messages) -> str:
    text = "\n".join(m["text"] for m in messages)
    hits = retrieve.match_tags(text, retrieve.load_tag_dict(conn))
    if not hits:
        return ""
    ph = ",".join("?" * len(hits))
    rows = conn.execute(
        f"SELECT n.id, n.layer, n.kind, n.text FROM memory_tags t "
        f"JOIN memory_nodes n ON n.id = t.node_id WHERE t.tag IN ({ph}) LIMIT 10",
        hits,
    ).fetchall()
    if not rows:
        return ""
    lines = "\n".join(f"- [id:{r['id']}][{r['layer']}/{r['kind']}] {r['text']}" for r in rows)
    return f"既存の記憶（参考。同じ意味は重複登録しない）:\n{lines}"


def _build_prompt(conn, messages, pending_nodes) -> str:
    system = (config.PROMPTS_DIR / "consolidation_system.txt").read_text(encoding="utf-8").strip()
    
    parts = [system]
    
    if messages:
        lines = []
        for m in messages:
            who = "ユーザー" if m["role"] == "user" else "ことは"
            lines.append(f"[id:{m['id']}] {who}: {_clip(m['text'])}")
        parts.append(f"未処理の会話:\n" + "\n".join(lines))
        existing = _existing_block(conn, messages)
        if existing:
            parts.append(existing)
            
    if pending_nodes:
        p_lines = []
        for r in pending_nodes:
            p_lines.append(f"- [id:{r['id']}][{r['layer']}/{r['kind']}] {r['text']}")
        parts.append("最近使われた記憶（再確認や訂正の対象）:\n" + "\n".join(p_lines))
        
    return "\n\n".join(parts)


def _validate_and_save(conn, data, messages) -> int:
    msg_ids = {m["id"] for m in messages} if messages else set()
    msg_date = {m["id"]: m["created_at"] for m in messages} if messages else {}
    first_id = messages[0]["id"] if messages else 0
    last_id = messages[-1]["id"] if messages else 0

    specs = data.get("new_nodes")
    if not isinstance(specs, list):
        specs = []
    specs = specs[:MAX_NEW_NODES]
    new_ids = [None] * len(specs)
    created = 0

    for idx, spec in enumerate(specs):
        if not isinstance(spec, dict):
            continue
        layer = spec.get("layer")
        kind = spec.get("kind")
        text = (spec.get("text") or "").strip()
        if layer == "episode":
            kind, limit, days = "event", 400, config.EPISODE_DAYS
        elif layer == "semantic":
            if kind not in ("fact", "preference", "open_topic", "procedure"):
                continue
            limit, days = 200, config.SEMANTIC_DAYS
        else:
            continue
        if not text or len(text) > limit:
            continue
        src = [i for i in spec.get("source_message_ids", []) if isinstance(i, int) and i in msg_ids]
        if not src:
            continue
        tags = []
        for t in spec.get("tags", [])[:3]:
            nt = _normalize_tag(t)
            if nt and nt not in tags:
                tags.append(nt)
        base = msg_date[src[0]]
        occurred = spec.get("occurred_at")
        if not isinstance(occurred, str) or len(occurred) != 10:
            occurred = base[:10]
        expires = (_parse_dt(base) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        cur = conn.execute(
            "INSERT OR IGNORE INTO memory_nodes(layer, kind, text, occurred_at, confirmed_at, "
            "last_used_at, expires_at, pinned, source_key) VALUES (?,?,?,?,?,?,?,0,?)",
            (layer, kind, text, occurred, base, base, expires, f"batch:{first_id}-{last_id}:{idx}"),
        )
        if cur.rowcount == 0:
            continue
        node_id = cur.lastrowid
        new_ids[idx] = node_id
        created += 1
        for t in tags:
            conn.execute("INSERT OR IGNORE INTO memory_tags(node_id, tag) VALUES (?,?)", (node_id, t))
        for mid in src:
            conn.execute("INSERT OR IGNORE INTO memory_sources(node_id, message_id) VALUES (?,?)", (node_id, mid))

    edges = data.get("edges")
    if isinstance(edges, list):
        for e in edges:
            if not isinstance(e, dict) or e.get("relation") != "derived_from":
                continue
            fi, ti = e.get("from"), e.get("to")
            if not isinstance(fi, int) or not isinstance(ti, int):
                continue
            if fi >= len(new_ids) or ti >= len(new_ids):
                continue
            fid, tid = new_ids[fi], new_ids[ti]
            if not fid or not tid:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO memory_edges(from_id, to_id, relation) VALUES (?,?, 'derived_from')",
                (fid, tid),
            )

    # 再確認 (reconfirm)
    reconfirm = data.get("reconfirm_ids", [])
    if isinstance(reconfirm, list):
        now = db.now_utc()
        for nid in reconfirm:
            if isinstance(nid, int):
                conn.execute(
                    f"UPDATE memory_nodes SET confirmed_at = ?, {db.EXTEND_EXPIRES} WHERE id = ?",
                    (now, *db.extend_args(), nid),
                )

    # 更新 (updates)
    updates = data.get("updates", [])
    if isinstance(updates, list):
        now = db.now_utc()
        for u in updates[:4]:
            if not isinstance(u, dict):
                continue
            nid = u.get("id")
            text = (u.get("text") or "").strip()[:200]
            if not isinstance(nid, int) or not text:
                continue
            src = [i for i in u.get("source_message_ids", []) if isinstance(i, int) and i in msg_ids]
            if not src:
                continue
            conn.execute(
                f"UPDATE memory_nodes SET text = ?, confirmed_at = ?, {db.EXTEND_EXPIRES} WHERE id = ?",
                (text, now, *db.extend_args(), nid),
            )
            for mid in src:
                conn.execute("INSERT OR IGNORE INTO memory_sources(node_id, message_id) VALUES (?,?)", (nid, mid))
            # 訂正時は近道リセット
            conn.execute("UPDATE memory_tags SET use_count = 0 WHERE node_id = ?", (nid,))

    return created


def run(conn) -> str:
    messages = fetch_unprocessed(conn)
    pending_ids = db.get_pending_ids(conn)
    
    pending_nodes = []
    if pending_ids:
        ph = ",".join("?" * len(pending_ids))
        pending_nodes = conn.execute(
            f"SELECT id, layer, kind, text FROM memory_nodes WHERE id IN ({ph})", pending_ids
        ).fetchall()

    if not messages and not pending_nodes:
        return "未処理の会話や再固定化の対象はありません。"

    prompt = _build_prompt(conn, messages, pending_nodes)
    raw = llm.chat(prompt, max_tokens=config.CONSOLIDATION_MAX_TOKENS)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise llm.LLMError("整理結果をJSONとして解析できなかった。")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        raise llm.LLMError("整理結果をJSONとして解析できなかった。")
        
    created = _validate_and_save(conn, data, messages)
    
    if messages:
        db.set_state(conn, "last_processed_message_id", messages[-1]["id"])
    db.set_state(conn, "last_consolidation_at", db.now_utc())
    db.set_pending_ids(conn, []) # 処理完了としてクリア
    conn.commit()
    return f"整理完了: 新規 {created} 件 / 再固定化 {len(pending_nodes)} 件"
