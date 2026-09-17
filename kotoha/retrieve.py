from . import config, db

_ALIVE = "(expires_at IS NULL OR expires_at > datetime('now'))"
_COLS = "id, layer, kind, text, occurred_at, confirmed_at, pinned"


def load_tag_dict(conn):
    rows = conn.execute("SELECT DISTINCT tag FROM memory_tags").fetchall()
    return [r["tag"] for r in tags] if False else [r["tag"] for r in rows]


def match_tags(text: str, tags):
    return [t for t in tags if t and t in text]


def pinned_only(conn):
    return conn.execute(
        f"SELECT {_COLS} FROM memory_nodes WHERE pinned = 1 AND {_ALIVE} "
        f"ORDER BY confirmed_at DESC LIMIT ?",
        (config.PINNED_LIMIT,),
    ).fetchall()


def retrieve(conn, user_text: str, recent_text: str = ""):
    haystack = user_text + "\n" + recent_text
    hits = match_tags(haystack, load_tag_dict(conn))

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
        add(
            conn.execute(
                f"SELECT {_COLS} FROM memory_tags t JOIN memory_nodes n ON n.id = t.node_id "
                f"WHERE t.tag IN ({ph}) AND {_ALIVE} ORDER BY n.confirmed_at DESC LIMIT ?",
                (*hits, config.TAG_CANDIDATE_LIMIT),
            ).fetchall()
        )

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
                    (*nids, config.HOP_LIMIT),
                ).fetchall()
            )

    add(
        conn.execute(
            f"SELECT {_COLS} FROM memory_nodes WHERE {_ALIVE} "
            f"AND (layer = 'episode' OR kind = 'open_topic') "
            f"ORDER BY confirmed_at DESC LIMIT ?",
            (config.RETRIEVE_RECENT_LIMIT,),
        ).fetchall()
    )

    pinned = pinned_only(conn)
    related = [c for c in cand if not c["pinned"]][:config.RELATED_LIMIT]
    return pinned, related
