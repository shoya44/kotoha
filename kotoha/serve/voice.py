"""AivisSpeech（VOICEVOX互換エンジン）で返答を声にする。

エンジンが止まっていても会話は続けられるように、失敗はすべて VoiceError に
まとめて呼び出し側へ返す。ここでの失敗が対話や記憶を巻き込むことはない。
"""

import httpx

from .. import config

# 1回の合成に渡す上限。これより長い返答は切って、待たされ続けるのを避ける。
MAX_CHARS = 300

# 機嫌ごとの声の表情。(話速, 音高, 抑揚)。**ラベルは chat.MOODS と同じ文字**で
# なければ効かない（tests/test_voice.py が見張っている）。
#
# 音高は半音ではない。エンジンの画面でも ±0.15 までしか振らないもので、
# 大きく動かすと声が割れる。**ここは全部、控えめに置いてある。**
# 何を渡せばよいかを決めるのは脳で、器は出来たwavを受け取るだけ。
MOOD_VOICE = {
    "ふつう": (1.00, 0.00, 1.00),
    "機嫌がいい": (1.05, 0.03, 1.10),
    "眠い": (0.93, -0.05, 0.90),
    "疲れ気味": (0.96, -0.02, 0.92),
    "すねている": (0.98, -0.03, 0.85),
}

# 同じエンジンへ何度もつなぐので、接続は開いたまま使い回す。1文ごとにつなぎ直すと
# 1文あたり0.44秒ほど余計にかかる（実測 903ms → 462ms）。通話では1文ごとに効く。
# 相手は同じPCの中なので、プロキシ設定は見ない。見にいく時間のぶんだけ遅くなる。
_client = None


def _http() -> httpx.Client:
    """つなぎを1本、使い回す。**取り込んだときには作らない。**

    設定を読む前に作ると、設定の検査を邪魔する（embed・weather・quake と
    同じ形）。「取り込んだだけでは、スレッドも接続も始めない」という
    決まりに揃えてある（docs/01 コンセプト）。
    """
    global _client
    if _client is None:
        _client = httpx.Client(trust_env=False)
    return _client


class VoiceError(Exception):
    pass


def clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_CHARS]


def _send(method: str, url: str, **kwargs):
    return _http().request(method, url, timeout=config.VOICE_TIMEOUT_SECONDS, **kwargs)


def _request(method: str, path: str, **kwargs):
    url = f"{config.VOICE_BASE_URL}{path}"
    try:
        try:
            response = _send(method, url, **kwargs)
        except httpx.RemoteProtocolError:
            # 開いたままの接続は、エンジンが再起動すると黙って切れている。
            # つなぎ直せば通るので、ここだけ一度やり直す。
            response = _send(method, url, **kwargs)
    except httpx.ConnectError:
        raise VoiceError("音声エンジンに接続できません。AivisSpeechを起動してください。") from None
    except httpx.TimeoutException:
        raise VoiceError("音声エンジンの応答がありません。") from None
    except httpx.HTTPError as e:
        raise VoiceError(f"音声エンジンとの通信に失敗しました: {e}") from None
    if response.status_code != 200:
        raise VoiceError(f"音声エンジンが応答しませんでした ({response.status_code})。")
    return response


def color(query: dict, mood: str) -> dict:
    """機嫌ぶんの表情を audio_query に乗せる。**知らない機嫌は素のまま。**

    話速と抑揚は掛け算、音高は足し算（エンジンがそういう単位で持っている）。
    機嫌を渡さなければ何も触らないので、素の声はここを通っても変わらない。
    """
    found = MOOD_VOICE.get(mood)
    if not found:
        return query
    speed, pitch, intonation = found
    query["speedScale"] = query.get("speedScale", 1.0) * speed
    query["pitchScale"] = query.get("pitchScale", 0.0) + pitch
    query["intonationScale"] = query.get("intonationScale", 1.0) * intonation
    return query


def speak(text: str, mood: str = "") -> bytes:
    """テキストを wav にして返す。機嫌を渡すと、そのぶん声の表情が変わる。"""
    text = clip(text)
    if not text:
        raise VoiceError("読み上げる文がありません。")
    params = {"text": text, "speaker": config.VOICE_STYLE_ID}
    query = color(_request("POST", "/audio_query", params=params).json(), mood)
    audio = _request(
        "POST", "/synthesis", params={"speaker": config.VOICE_STYLE_ID}, json=query
    )
    return audio.content
