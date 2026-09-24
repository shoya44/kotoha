"""Gemini への往復。まとめて受け取る道と、届いたそばから受け取る道。

**流す道はやり直さない。** 一度口に出したものは取り消せないので、途中で
切れたらそこまでを言ったことにする。まとめて受け取る道だけが投げ直す。

**投げ直す前にひと息おく。** 混んで 503 を返したモデルに間を置かず投げ直すと、
また 503 が返りやすい。数秒あければ通ることが多い（2026-09-25）。
控えのモデル（flash）へ回す案は、無料枠が1日20回しかなく使い切ると 429 で
止まり、かえって返事が付かなくなったのでやめた（2026-09-25）。
"""

import contextlib
import json
import threading
import time

import httpx

from .. import config


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# 糸ごとの「急ぎ」の印。裏の仕事（巡回）が立てる。会話の糸には影響しない。
_local = threading.local()


@contextlib.contextmanager
def hurried():
    """この糸の中では、短い上限で1回だけ試す。

    巡回は会話と同じ順番待ちを持ったまま Gemini を待つ。会話と同じ上限
    （既定180秒）で2回試すと、その6分のあいだ会話が「考え中…」のまま止まる
    （2026-09-24）。裏の仕事は急がば回れで、駄目なら次の巡回に譲る。
    """
    before = getattr(_local, "hurry", False)
    _local.hurry = True
    try:
        yield
    finally:
        _local.hurry = before


# 投げ直す前に置く間（秒）。すぐ投げ直すと同じ 503 が返りやすい。
BREATH_SECONDS = 2.0


def _budget() -> tuple[float, int]:
    """いまの糸の待ち上限と試す回数。"""
    if getattr(_local, "hurry", False):
        return config.BACKGROUND_TIMEOUT_SECONDS, 1
    return config.TIMEOUT_SECONDS, config.LLM_ATTEMPTS


def _payload(prompt: str, max_tokens: int | None = None) -> dict:
    return {
        "model": config.GEMINI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens or config.MAX_OUTPUT_TOKENS,
        "temperature": config.TEMPERATURE,
    }


def _headers() -> dict:
    return {"Authorization": f"Bearer {config.GEMINI_API_KEY}"}


def _trouble(status: int, body: str):
    """返ってきた番号の読み方。**どちらの道でも同じにする。**

    諦めるものはここで投げ、もう一度試してよいものだけを返す。
    """
    if status in (400, 401):
        raise LLMError(f"認証またはモデルエラー ({status})。キーやモデル名を確認して。")
    if status == 429:
        # 間を置かずに投げ直しても、分あたりの枠は空いていないので必ず失敗する。
        # 失敗したぶんも枠を食うので、ここでは諦めて呼び出し側に返す。
        raise LLMError("いま混み合っているみたい。少し待ってからもう一度送って。")
    if status >= 500:
        return LLMError(f"サーバーエラー ({status})。", retryable=True)
    if status != 200:
        raise LLMError(f"要求失敗 ({status}): {body[:200]}")
    return None


def chat(prompt: str, max_tokens: int | None = None) -> str:
    payload = _payload(prompt, max_tokens)
    headers = _headers()
    last_error = None
    timeout, attempts = _budget()

    for _ in range(attempts):
        if last_error is not None:
            time.sleep(BREATH_SECONDS)
        try:
            resp = httpx.post(
                f"{config.GEMINI_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            last_error = LLMError(f"通信失敗: {e}", retryable=True)
            continue

        trouble = _trouble(resp.status_code, resp.text)
        if trouble is not None:
            last_error = trouble
            continue

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise LLMError("応答形式が不正。")
        if not content or not content.strip():
            last_error = LLMError("応答が空。", retryable=True)
            continue
        return content.strip()

    raise last_error or LLMError("不明なエラー。")


def stream(prompt: str, max_tokens: int | None = None):
    """届いたそばから渡す。**最初の一文が決まるまでの待ちを省くため。**

    通話でいちばん長い待ちは、生成が終わるまで1文字も決まらないこと。
    文の切れ目まで来たぶんを先に渡せば、そこから声を作り始められる。

    **やり直しはしない。** 途中まで喋ったものは取り消せない。1文字も
    受け取れていないうちの失敗だけを投げて、呼ぶ側がまとめて受け取る道へ
    落とせるようにする。
    """
    payload = _payload(prompt, max_tokens) | {"stream": True}
    try:
        with httpx.stream("POST", f"{config.GEMINI_BASE_URL}/chat/completions",
                          json=payload, headers=_headers(),
                          timeout=_budget()[0]) as resp:
            if resp.status_code != 200:
                resp.read()
                raise _trouble(resp.status_code, resp.text) or LLMError("応答が空。")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    return
                try:
                    chunk = json.loads(body)
                except ValueError:
                    continue      # 読めない断片は飛ばす。次が来る。
                try:
                    piece = chunk["choices"][0]["delta"].get("content")
                except (KeyError, IndexError):
                    continue
                if piece:
                    yield piece
    except httpx.HTTPError as error:
        raise LLMError(f"通信失敗: {error}", retryable=True) from None

