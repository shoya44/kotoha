"""AivisSpeech（VOICEVOX互換エンジン）で返答を声にする。

エンジンが止まっていても会話は続けられるように、失敗はすべて VoiceError に
まとめて呼び出し側へ返す。ここでの失敗が対話や記憶を巻き込むことはない。
"""

import httpx

from . import config

# 1回の合成に渡す上限。これより長い返答は切って、待たされ続けるのを避ける。
MAX_CHARS = 300


class VoiceError(Exception):
    pass


def clip(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_CHARS]


def _request(method: str, path: str, **kwargs):
    try:
        response = httpx.request(
            method,
            f"{config.VOICE_BASE_URL}{path}",
            timeout=config.VOICE_TIMEOUT_SECONDS,
            **kwargs,
        )
    except httpx.ConnectError:
        raise VoiceError("音声エンジンに接続できません。AivisSpeechを起動してください。") from None
    except httpx.TimeoutException:
        raise VoiceError("音声エンジンの応答がありません。") from None
    except httpx.HTTPError as e:
        raise VoiceError(f"音声エンジンとの通信に失敗しました: {e}") from None
    if response.status_code != 200:
        raise VoiceError(f"音声エンジンが応答しませんでした ({response.status_code})。")
    return response


def speak(text: str) -> bytes:
    """テキストを wav にして返す。"""
    text = clip(text)
    if not text:
        raise VoiceError("読み上げる文がありません。")
    params = {"text": text, "speaker": config.VOICE_STYLE_ID}
    query = _request("POST", "/audio_query", params=params).json()
    audio = _request(
        "POST", "/synthesis", params={"speaker": config.VOICE_STYLE_ID}, json=query
    )
    return audio.content
