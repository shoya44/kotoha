import json
from datetime import datetime, timedelta, timezone

from .. import config
from ..talk import llm
from . import db, embed, retrieve

MAX_NEW_NODES = 8
# 同じバッチで続けて失敗したら諦める回数。
#
# 失敗しても処理位置は進まないので、巡回のたびに同じ会話を投げ直す。
# 読めない返事が返り続けると、60秒ごとに1回ずつAPIの枠を食い、
# 1日500回の枠を半日で使い切れてしまう。記憶1回ぶんより、枠のほうが高い。
GIVE_UP_AFTER = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return _utc_now()


def fetch_unprocessed(conn):
    last = int(db.get_state(conn, db.LAST_PROCESSED_MESSAGE_ID, "0") or 0)
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


def _safe_date(value, base: str) -> str:
    """出来事の日付。会話の日から離れすぎていたら、会話の日に寄せる。

    モデルは年を取り違えることがある。実際に2023年と書かれた記憶があった。
    形だけ見て通すと、その記憶は想起でも忘却でも別の時代に置かれてしまう。
    """
    if not isinstance(value, str) or len(value) != 10:
        return base[:10]
    try:
        said = datetime.strptime(value, "%Y-%m-%d")
        spoken = datetime.strptime(base[:10], "%Y-%m-%d")
    except ValueError:
        return base[:10]
    # 会話の前後1年まで。過去を振り返る話もあるので、幅は持たせる。
    if abs((said - spoken).days) > 366:
        return base[:10]
    return value


def _normalize_tag(tag: str) -> str:
    return " ".join(str(tag).strip().split())[:24]


def _tagged_ids(conn, text):
    hits = retrieve.match_tags(text, retrieve.load_tag_dict(conn))
    if not hits:
        return []
    ph = ",".join("?" * len(hits))
    return [
        r["node_id"]
        for r in conn.execute(
            f"SELECT DISTINCT node_id FROM memory_tags WHERE tag IN ({ph}) LIMIT 10", hits
        )
    ]


def _nearby_ids(conn, text, limit=5):
    """意味の近い記憶。新しく作られる記憶は、会話の終わり際に重なりやすい。"""
    if not (config.EMBED_ENABLED and embed.available()):
        return []
    vectors = embed.load_all(conn)
    if not vectors:
        return []
    try:
        query = embed.embed([text[-config.EMBED_MAX_CHARS :]])[0]
    except embed.EmbedError:
        return []
    return [node_id for _, node_id in embed.nearest(vectors, query, limit)]


def _existing_block(conn, messages) -> str:
    """既存の記憶を見せて、同じものを二度作らせない。

    タグの一致だけで選んでいたので、言い回しの違う重複を見落としていた。
    意味の近いものを先に混ぜる。件数は変えないのでプロンプトは太らない。
    """
    text = "\n".join(m["text"] for m in messages)
    ids = list(dict.fromkeys(_nearby_ids(conn, text) + _tagged_ids(conn, text)))[:10]
    if not ids:
        return ""
    ph = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, layer, kind, text FROM memory_nodes WHERE id IN ({ph})", ids
    ).fetchall()
    order = {node_id: i for i, node_id in enumerate(ids)}
    rows = sorted(rows, key=lambda r: order[r["id"]])
    lines = "\n".join(f"- [id:{r['id']}][{r['layer']}/{r['kind']}] {r['text']}" for r in rows)
    return f"既存の記憶（参考。同じ意味は重複登録しない）:\n{lines}"


def _merge_targets(conn, specs):
    """既存と言い方が違うだけの候補を見つける。{候補の位置: 既存のid}

    同じ出来事が何件にも分かれると、想起の6枠が同じ話で埋まる。実測では
    記憶76件のうち29件が何かと重なっていた。

    ただし近いだけで別の事実ということもある（「Rocket Now」と「出前館」は
    0.85で近いが、別の店）。取り違えると情報が消えて戻らないので、しきい値は
    言い直しだけを拾う高さに置く。
    """
    if not (config.EMBED_ENABLED and config.EMBED_MERGE_FLOOR) or not embed.available():
        return {}
    texts = {}
    for i, spec in enumerate(specs):
        if isinstance(spec, dict):
            text = (spec.get("text") or "").strip()
            if text:
                texts[i] = text
    if not texts:
        return {}
    vectors = embed.load_all(conn)
    if not vectors:
        return {}
    try:
        made = embed.embed(list(texts.values()))
    except embed.EmbedError:
        return {}
    found = {}
    for idx, vector in zip(texts, made):
        hit = embed.nearest(vectors, vector, 1, config.EMBED_MERGE_FLOOR)
        if hit:
            found[idx] = hit[0][1]
    return found


def _build_prompt(conn, messages, pending_nodes) -> str:
    system = (config.PROMPTS_DIR / "consolidation_system.txt").read_text(encoding="utf-8").strip()
    
    parts = [system]
    
    if messages:
        lines = []
        for m in messages:
            who = "ユーザー" if m["role"] == "user" else "ことは"
            lines.append(f"[id:{m['id']}] {who}: {_clip(m['text'])}")
        parts.append("未処理の会話:\n" + "\n".join(lines))
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
    merge = _merge_targets(conn, specs)
    merged_ids = []

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
        if idx in merge:
            # 作らずに既存へ寄せる。どの会話で確かめたかは残す。
            merged_ids.append(merge[idx])
            for mid in src:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_sources(node_id, message_id) VALUES (?,?)",
                    (merge[idx], mid),
                )
            continue
        tags = []
        for t in spec.get("tags", [])[:3]:
            nt = _normalize_tag(t)
            if nt and nt not in tags:
                tags.append(nt)
        base = msg_date[src[0]]
        occurred = _safe_date(spec.get("occurred_at"), base)
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

    # 再確認 (reconfirm)。重複として作らなかったぶんも、ここで確かめ直す。
    reconfirm = data.get("reconfirm_ids", [])
    if not isinstance(reconfirm, list):
        reconfirm = []
    now = db.now_utc()
    for nid in dict.fromkeys(list(reconfirm) + merged_ids):
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
            # 本文が変わったのでベクトルも古い。捨てておけば次の巡回で作り直される。
            embed.drop(conn, nid)

    return created, len(merged_ids)


def _as_json(raw: str):
    """返事からJSONを取り出す。最初の { から最後の } まで。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise llm.LLMError("整理結果をJSONとして解析できなかった。")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        raise llm.LLMError("整理結果をJSONとして解析できなかった。") from None


def _count_failure(conn, messages) -> None:
    """失敗を数え、続くようならそのバッチを置いていく。

    処理位置を進めるので、その会話からは記憶が作られない。惜しいが、
    同じ会話を毎分投げ直して枠を空にするほうが困る。
    """
    fails = int(db.get_state(conn, db.CONSOLIDATE_FAILS, "0") or 0) + 1
    db.set_state(conn, db.CONSOLIDATE_FAILS, fails)
    if fails >= GIVE_UP_AFTER and messages:
        db.set_state(conn, db.LAST_PROCESSED_MESSAGE_ID, messages[-1]["id"])
        db.set_state(conn, db.CONSOLIDATE_FAILS, 0)
    conn.commit()


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
    try:
        raw = llm.chat(prompt, max_tokens=config.CONSOLIDATION_MAX_TOKENS)
        data = _as_json(raw)
    except llm.LLMError:
        _count_failure(conn, messages)
        raise

    db.set_state(conn, db.CONSOLIDATE_FAILS, 0)

    created, merged = _validate_and_save(conn, data, messages)
    
    if messages:
        db.set_state(conn, db.LAST_PROCESSED_MESSAGE_ID, messages[-1]["id"])
    db.set_state(conn, db.LAST_CONSOLIDATION_AT, db.now_utc())
    db.set_pending_ids(conn, []) # 処理完了としてクリア
    conn.commit()
    linked = embed.link_similar(conn)
    summary = f"整理完了: 新規 {created} 件 / 再固定化 {len(pending_nodes)} 件"
    if merged:
        summary += f" / 既存へ寄せた {merged} 件"
    if linked:
        summary += f" / 連結 {linked} 本"
    return summary
