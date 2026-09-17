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
