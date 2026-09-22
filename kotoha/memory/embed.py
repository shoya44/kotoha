"""記憶と発言を、意味の近さで比べられる形（ベクトル）にする。

Ollamaが止まっていても会話は続けられるように、失敗はすべて EmbedError に
まとめて呼び出し側へ返す。ここでの失敗が対話や記憶を巻き込むことはない。
serve/voice.py と同じ約束で書いてある。

ベクトルは長さを1にそろえて持つ。こうしておくと、近さを求めるのが
掛けて足すだけになり、比べるたびに割り算をしなくて済む。
"""

import array
import math
import operator
import time

import httpx

from .. import config
from . import db

_client = None


def _http():
    """接続は最初に要るときだけ作る。読み込んだだけでは何も用意しない。

    voice.py と同じ理由で、一度作ったら開いたまま使い回す。相手は同じPCの
    中なのでプロキシ設定も見ない。1件あたり297msが56msになる（実測）。
    """
    global _client
    if _client is None:
        _client = httpx.Client(trust_env=False)
    return _client


class EmbedError(Exception):
    pass


# Ollamaが止まっているのに毎回つなぎに行くと、会話のたびに待ち時間が増える。
# 一度失敗したらしばらく諦める。巡回が60秒ごとに試し続けるので、戻れば再開する。
_blocked_until = 0.0


def available() -> bool:
    return time.monotonic() >= _blocked_until


def _post(texts, timeout):
    payload = {
        "model": config.EMBED_MODEL,
        "input": texts,
        # 指定しないと数分で眠ってしまい、次の1回に1.7秒かかる。
        "keep_alive": config.EMBED_KEEP_ALIVE,
    }
    url = config.EMBED_BASE_URL + "/api/embed"
    client = _http()
    try:
        try:
            response = client.post(url, json=payload, timeout=timeout)
        except httpx.RemoteProtocolError:
            # 開いたままの接続は、Ollamaが再起動すると黙って切れている。
            response = client.post(url, json=payload, timeout=timeout)
    except httpx.ConnectError:
        raise EmbedError("Ollamaに接続できません。") from None
    except httpx.TimeoutException:
        raise EmbedError("Ollamaの応答がありません。") from None
    except httpx.HTTPError as e:
        raise EmbedError(f"Ollamaとの通信に失敗しました: {e}") from None
    if response.status_code != 200:
        raise EmbedError(f"Ollamaが応答しませんでした ({response.status_code})。")
    try:
        vectors = response.json()["embeddings"]
    except (ValueError, KeyError, TypeError):
        raise EmbedError("Ollamaの応答を読み取れませんでした。") from None
    if len(vectors) != len(texts):
        raise EmbedError("Ollamaの応答の件数が合いません。")
    return vectors


def _unit(values):
    size = math.sqrt(sum(v * v for v in values))
    if not size:
        raise EmbedError("中身のないベクトルが返りました。")
    return array.array("f", [v / size for v in values])


def embed(texts, timeout=None):
    """文をベクトルにする。空欄や長すぎる文はここで整える。"""
    global _blocked_until
    if not texts:
        return []
    clipped = [(t or " ")[: config.EMBED_MAX_CHARS] for t in texts]
    try:
        raw = _post(clipped, timeout or config.EMBED_TIMEOUT_SECONDS)
    except EmbedError:
        _blocked_until = time.monotonic() + config.EMBED_RETRY_SECONDS
        raise
    _blocked_until = 0.0
    return [_unit(v) for v in raw]


def similarity(a, b) -> float:
    return sum(map(operator.mul, a, b))


def unpack(blob) -> array.array:
    """しまってあるバイト列を、比べられる形に戻す。"""
    out = array.array("f")
    out.frombytes(blob)
    return out


# ===== memory_vectors の出し入れ =====
# バイト列はこのPCの並び順でそのまま書く。別の機械へ持ち出すものではないし、
# 食い違えば作り直せばいいだけなので、移植性のために遅くする理由がない。


def missing(conn, limit):
    """まだベクトルの無い記憶を古い順に返す。モデルを替えたものもここに出る。"""
    return conn.execute(
        "SELECT n.id, n.text FROM memory_nodes n "
        "LEFT JOIN memory_vectors v ON v.node_id = n.id AND v.model = ? "
        "WHERE v.node_id IS NULL ORDER BY n.id LIMIT ?",
        (config.EMBED_MODEL, limit),
    ).fetchall()


def store(conn, pairs):
    conn.executemany(
        "INSERT OR REPLACE INTO memory_vectors(node_id, model, vector) VALUES (?,?,?)",
        [(node_id, config.EMBED_MODEL, vector.tobytes()) for node_id, vector in pairs],
    )
    conn.commit()


def drop(conn, node_id):
    """本文が変わったら捨てる。次の巡回で作り直される。"""
    conn.execute("DELETE FROM memory_vectors WHERE node_id = ?", (node_id,))


def load_all(conn):
    """記憶のベクトルをまとめて読む。71件で284KBなので、全部載せてよい。"""
    return conn.execute(
        "SELECT node_id, vector FROM memory_vectors WHERE model = ?",
        (config.EMBED_MODEL,),
    ).fetchall()


# ===== diary_vectors の出し入れ =====
# 日記も同じ道で座標にする。記憶と違って本文は変わらないので、捨てる口は要らない。
# 話さなかった日（定型文）は座標にしない。何にでも近くなって、枠を潰す。


def missing_diary(conn, limit, silent: str):
    return conn.execute(
        "SELECT d.id, d.day, d.text FROM diary d "
        "LEFT JOIN diary_vectors v ON v.diary_id = d.id AND v.model = ? "
        "WHERE v.diary_id IS NULL AND d.text != ? ORDER BY d.id LIMIT ?",
        (config.EMBED_MODEL, silent, limit),
    ).fetchall()


def store_diary(conn, pairs):
    conn.executemany(
        "INSERT OR REPLACE INTO diary_vectors(diary_id, model, vector) VALUES (?,?,?)",
        [(diary_id, config.EMBED_MODEL, vector.tobytes()) for diary_id, vector in pairs],
    )
    conn.commit()


def load_diary(conn):
    return conn.execute(
        "SELECT v.diary_id AS node_id, v.vector, d.day, d.text FROM diary_vectors v "
        "JOIN diary d ON d.id = v.diary_id WHERE v.model = ?",
        (config.EMBED_MODEL,),
    ).fetchall()


def nearest(vectors, query, limit, floor=0.0, exclude=()):
    """近い順に [(近さ, id), ...] を返す。vectors は load_all の結果。"""
    scored = []
    for r in vectors:
        node_id = r["node_id"]
        if node_id in exclude:
            continue
        score = similarity(query, unpack(r["vector"]))
        if score >= floor:
            scored.append((score, node_id))
    scored.sort(reverse=True)
    return scored[:limit]


def link_similar(conn):
    """新しく座標がついた記憶を、意味の近い記憶と結ぶ。

    タグが当たったときに「1本たどった先」を足す処理は前からあるが、
    memory_edges を作る側が無く、ずっと空振りしていた。ここで埋める。

    一度見た記憶は二度見ない。あとから来た記憶が古い記憶へ結ぶので、
    たどる側が双方向を見ている以上、片側だけ張れば足りる。
    """
    if not (config.EMBED_ENABLED and config.EMBED_LINK_LIMIT):
        return 0
    last = int(db.get_state(conn, db.LAST_LINKED_NODE_ID, "0") or 0)
    fresh = [r for r in load_all(conn) if r["node_id"] > last]
    if not fresh:
        return 0
    vectors = load_all(conn)
    made = 0
    for r in sorted(fresh, key=lambda x: x["node_id"]):
        node_id = r["node_id"]
        found = nearest(vectors, unpack(r["vector"]), config.EMBED_LINK_LIMIT,
                        config.EMBED_LINK_FLOOR, exclude={node_id})
        for _, other in found:
            made += conn.execute(
                "INSERT OR IGNORE INTO memory_edges(from_id, to_id, relation) "
                "VALUES (?,?,'related_to')",
                (node_id, other),
            ).rowcount
        db.set_state(conn, db.LAST_LINKED_NODE_ID, node_id)
    conn.commit()
    return made
