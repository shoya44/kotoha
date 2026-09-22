from .. import config, notify
from . import db, embed, strength

_ALIVE = db.alive_sql()          # `?` が1つ。引数には db.now_utc() を渡す
_COLS = "id, layer, kind, text, occurred_at, confirmed_at, pinned, strength, strength_at"

# 続きのある話を、毎回これだけは渡す。最近のエピソードと同じ枠に入れていると、
# 出来事が続いた週は押し出されて、続きを聞く機会が来ない。
OPEN_TOPIC_LIMIT = 2


def load_tag_dict(conn):
    rows = conn.execute("SELECT DISTINCT tag FROM memory_tags").fetchall()
    return [r["tag"] for r in rows]


def match_tags(text: str, tags):
    return [t for t in tags if t and t in text]


def pinned_only(conn):
    return conn.execute(
        f"SELECT {_COLS} FROM memory_nodes WHERE pinned = 1 AND {_ALIVE} "
        f"ORDER BY confirmed_at DESC LIMIT ?",
        (db.now_utc(), config.PINNED_LIMIT),
    ).fetchall()


def _query_text(user_text: str, recent_text: str) -> str:
    """意味を照らし合わせる文。発言だけでは短すぎて、意味が定まらない。

    発言の長さは中央値14字しかない。「しんどい」だけだと的外れな記憶を
    引くが、直前のやりとりを足すと正しい方に寄る（実測で確認済み）。
    """
    lines = [line for line in recent_text.strip().split("\n") if line]
    tail = lines[-config.EMBED_CONTEXT_LINES:] if config.EMBED_CONTEXT_LINES else []
    return "\n".join([*tail, user_text])


def _by_meaning(conn, query_text: str, known):
    """意味の近い記憶を返す。タグと違い、言葉が一致しなくてもたどり着ける。

    Ollamaが答えなければ静かに空を返す。読み上げと同じで、無くても会話は続く。
    """
    if not (config.EMBED_ENABLED and config.EMBED_RESERVE) or not embed.available():
        return []
    rows = embed.load_all(conn)
    if not rows:
        return []
    try:
        query = embed.embed([query_text])[0]
    except embed.EmbedError:
        return []

    # 薄れた記憶は、同じ近さでも出にくい。強い手がかり（高い近さ）なら出る。
    now = db.now_utc()
    strengths = strength.of_rows(conn.execute(
        f"SELECT id, layer, confirmed_at, strength, strength_at FROM memory_nodes WHERE {_ALIVE}",
        (now,)).fetchall())
    scored = []
    for r in rows:
        if r["node_id"] in known or r["node_id"] not in strengths:
            continue
        score = embed.similarity(query, embed.unpack(r["vector"]))
        score *= strength.weight(strengths[r["node_id"]])
        # 近いものが無い回もある。無理に引くと、関係ない記憶で枠を潰す。
        if score >= config.EMBED_FLOOR:
            scored.append((score, r["node_id"]))
    if not scored:
        return []
    scored.sort(reverse=True)
    ids = [node_id for _, node_id in scored[: config.EMBED_RESERVE]]
    ph = ",".join("?" * len(ids))
    found = conn.execute(
        f"SELECT {_COLS} FROM memory_nodes WHERE id IN ({ph}) AND {_ALIVE}", (*ids, db.now_utc())
    ).fetchall()
    order = {node_id: i for i, node_id in enumerate(ids)}
    return sorted(found, key=lambda r: order[r["id"]])


def retrieve(conn, user_text: str, recent_text: str = ""):
    haystack = user_text + "\n" + recent_text
    hits = match_tags(haystack, load_tag_dict(conn))
    # どのタグで当たったか。**1〜2字のタグ（例「雨」）は部分一致で誤って当たる。**
    # 当たり率は実ログでしか測れないので、調べるときだけ残す。
    notify.trace("retrieve", f"タグ命中 {hits}")

    cand = []
    seen = set()

    def add(rows):
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            cand.append(r)

    if hits:
        ph = ",".join("?" * len(hits))
        tagged = conn.execute(
            f"SELECT {_COLS} FROM memory_tags t JOIN memory_nodes n ON n.id = t.node_id "
            f"WHERE t.tag IN ({ph}) AND {_ALIVE} "
            # 同じ強さなら、よく想起されたタグ・最近使った記憶を先に。use_count は
            # 0〜3で頭打ちになり未使用なら日数で戻るので、古い記憶が居座り続けない。
            f"ORDER BY t.use_count DESC, n.last_used_at DESC, n.confirmed_at DESC LIMIT ?",
            (*hits, db.now_utc(), config.TAG_CANDIDATE_LIMIT),
        ).fetchall()
        # **強い記憶が先。** 使うほど強く、放っておくほど薄れる。並べ替えは安定なので、
        # 同じ強さのあいだでは上の順が残る。
        now_strength = strength.of_rows(tagged)
        add(sorted(tagged, key=lambda r: -now_strength[r["id"]]))

    if cand:
        ids = [c["id"] for c in cand]
        ph = ",".join("?" * len(ids))
        nids = [
            r["nid"]
            for r in conn.execute(
                f"SELECT to_id AS nid FROM memory_edges WHERE from_id IN ({ph})", ids
            ).fetchall()
            + conn.execute(
                f"SELECT from_id AS nid FROM memory_edges WHERE to_id IN ({ph})", ids
            ).fetchall()
        ]
        if nids:
            ph2 = ",".join("?" * len(nids))
            add(
                conn.execute(
                    f"SELECT {_COLS} FROM memory_nodes WHERE id IN ({ph2}) AND {_ALIVE} LIMIT ?",
                    (*nids, db.now_utc(), config.HOP_LIMIT),
                ).fetchall()
            )

    add(
        conn.execute(
            f"SELECT {_COLS} FROM memory_nodes WHERE {_ALIVE} AND kind = 'open_topic' "
            f"ORDER BY confirmed_at DESC LIMIT ?",
            (db.now_utc(), OPEN_TOPIC_LIMIT),
        ).fetchall()
    )

    add(
        conn.execute(
            f"SELECT {_COLS} FROM memory_nodes WHERE {_ALIVE} "
            f"AND (layer = 'episode' OR kind = 'open_topic') "
            f"ORDER BY confirmed_at DESC LIMIT ?",
            (db.now_utc(), config.RETRIEVE_RECENT_LIMIT),
        ).fetchall()
    )

    pinned = pinned_only(conn)
    known = {c["id"] for c in cand} | {p["id"] for p in pinned}
    by_meaning = _by_meaning(conn, _query_text(user_text, recent_text), known)
    # 予約したぶんは必ず渡す。後ろに足すだけだと、タグが当たった回は枠が
    # 埋まりきって出番が来ない。取り違えても割を食うのは予約枠だけで済む。
    keep = config.RELATED_LIMIT - len(by_meaning)
    related = [c for c in cand if not c["pinned"]][:keep] + by_meaning
    return pinned, related
