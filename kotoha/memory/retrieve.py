import re

from .. import config, notify
from . import db, diary, embed, strength

_ALIVE = db.alive_sql()          # `?` が1つ。引数には db.now_utc() を渡す
_COLS = "id, layer, kind, text, occurred_at, confirmed_at, pinned, strength, strength_at"

# 続きのある話を、毎回これだけは渡す。最近のエピソードと同じ枠に入れていると、
# 出来事が続いた週は押し出されて、続きを聞く機会が来ない。
OPEN_TOPIC_LIMIT = 2

# 人格に書いてある名前の見つけ方。ひな形（prompts/persona.txt）の書き方に合わせる:
# 「名前はことは。」「ユーザーを「あなた」と呼ぶ。」
_NAME_PATTERNS = (re.compile(r"名前は(.+?)[。、\s]"), re.compile(r"「(.+?)」と呼"))


def persona_names() -> set:
    """人格に書いてある名前（ことは自身と、相手の呼び名）。

    **名前はタグにしない。** どの発言にも出てくるので、タグにすると毎回当たり、
    同じ記憶が想起の枠に居座る（実測: 名前2つが全往復で命中し、同じ6件が
    固定で渡っていた）。整理で付けさせず、すでに付いているぶんは辞書から外す。
    """
    from ..talk import chat            # 人格を同じ場所から読む（循環を避けて遅らせる）
    text = chat._read("persona.txt")
    found = set()
    for pattern in _NAME_PATTERNS:
        found.update(name.strip() for name in pattern.findall(text))
    return {name for name in found if name}


def load_tag_dict(conn):
    skip = persona_names()
    rows = conn.execute("SELECT DISTINCT tag FROM memory_tags").fetchall()
    return [r["tag"] for r in rows if r["tag"] not in skip]


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


def _query_vector(query_text: str, rows, pages):
    """照らし合わせる文の座標。1往復に1回だけ作り、記憶にも日記にも使う。

    比べる相手が1つも無ければ作らない（Ollama に行かない）。答えなければ
    None。読み上げと同じで、無くても会話は続く。意味の想起を切っていれば
    （EMBED_RESERVE=0）、日記の想起も一緒に切れる。
    """
    if not (config.EMBED_ENABLED and config.EMBED_RESERVE) or not embed.available():
        return None
    if not rows and not pages:
        return None
    try:
        return embed.embed([query_text])[0]
    except embed.EmbedError:
        return None


def _by_meaning(conn, query, known, rows):
    """意味の近い記憶を返す。タグと違い、言葉が一致しなくてもたどり着ける。"""
    if query is None or not rows:
        return []

    # 薄れた記憶は、同じ近さでも出にくい。強い手がかり（高い近さ）なら出る。
    now = db.now_utc()
    strengths = strength.of_rows(conn.execute(
        f"SELECT id, layer, confirmed_at, strength, strength_at FROM memory_nodes WHERE {_ALIVE}",
        (now,)).fetchall())
    scored, near = [], []
    for r in rows:
        if r["node_id"] in known or r["node_id"] not in strengths:
            continue
        score = embed.similarity(query, embed.unpack(r["vector"]))
        score *= strength.weight(strengths[r["node_id"]])
        # 近いものが無い回もある。無理に引くと、関係ない記憶で枠を潰す。
        if score >= config.EMBED_FLOOR:
            scored.append((score, r["node_id"]))
        elif score >= config.EMBED_FLOOR - config.AFTERTHOUGHT_MARGIN:
            near.append((score, r["node_id"]))
    _keep_afterthought(conn, near)
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


def _keep_afterthought(conn, near) -> None:
    """思い出しかけて出なかった記憶を1つ預かる。会話が途切れてから言う。

    床のすぐ下は「関係ありそうだが、いまは出てこない」。人が風呂で思い出す
    のはこれで、その場では黙っているのが正しい。いちばん惜しかった1件だけ。
    今日もう言ったものと同じなら預からない（同じことを二度は言わない）。
    """
    if not (near and config.AFTERTHOUGHT_ENABLED):
        return
    near.sort(reverse=True)
    node_id = near[0][1]
    if str(node_id) == db.get_state(conn, db.LAST_AFTERTHOUGHT_ID):
        return
    db.set_state(conn, db.AFTERTHOUGHT_ID, node_id)
    db.set_state(conn, db.AFTERTHOUGHT_AT, db.now_utc())


def take_afterthought(conn):
    """預かっている「そういえば」を取り出して空にする。無ければ None。"""
    node_id = db.get_state(conn, db.AFTERTHOUGHT_ID)
    if not node_id:
        return None
    db.set_state(conn, db.AFTERTHOUGHT_ID, "")
    row = conn.execute(
        f"SELECT {_COLS} FROM memory_nodes WHERE id = ? AND {_ALIVE}", (int(node_id), db.now_utc())
    ).fetchone()
    return row


def recall_diary(query, pages, skip_days=()):
    """発言に意味の近い日記。ここ数日ぶん（毎回添えている日）は除く。

    記憶と違って強さは持たない。日記は「その日がどんな日だったか」で、
    使うほど強まるものではない。近さの床は記憶と同じ。
    """
    if query is None or not pages:
        return []
    scored = []
    for r in pages:
        if r["day"] in skip_days:
            continue
        score = embed.similarity(query, embed.unpack(r["vector"]))
        if score >= config.EMBED_FLOOR:
            scored.append((score, r["day"], r["text"]))
    scored.sort(reverse=True)
    return [{"day": day, "text": text} for _, day, text in scored[: config.DIARY_RECALL_LIMIT]]


def retrieve(conn, user_text: str, recent_text: str = ""):
    pinned, related, _ = retrieve_all(conn, user_text, recent_text)
    return pinned, related


def retrieve_all(conn, user_text: str, recent_text: str = ""):
    """(保護記憶, 関連記憶, 思い当たる日記)。座標は1回だけ作る。"""
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
    rows = embed.load_all(conn) if config.EMBED_ENABLED else []
    pages = embed.load_diary(conn) if config.EMBED_ENABLED and config.DIARY_RECALL_LIMIT else []
    query = _query_vector(_query_text(user_text, recent_text), rows, pages)
    by_meaning = _by_meaning(conn, query, known, rows)
    # 予約したぶんは必ず渡す。後ろに足すだけだと、タグが当たった回は枠が
    # 埋まりきって出番が来ない。取り違えても割を食うのは予約枠だけで済む。
    keep = max(0, config.RELATED_LIMIT - len(by_meaning))
    related = [c for c in cand if not c["pinned"]][:keep] + by_meaning
    diaries = recall_diary(query, pages, {r["day"] for r in diary.shown(conn)})
    return pinned, related, diaries
