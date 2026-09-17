import httpx

from .. import config


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def chat(prompt: str, max_tokens: int | None = None) -> str:
    payload = {
        "model": config.GEMINI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens or config.MAX_OUTPUT_TOKENS,
        "temperature": config.TEMPERATURE,
    }
    headers = {"Authorization": f"Bearer {config.GEMINI_API_KEY}"}
    last_error = None

    for _ in range(config.LLM_ATTEMPTS):
        try:
            resp = httpx.post(
                f"{config.GEMINI_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=config.TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as e:
            last_error = LLMError(f"通信失敗: {e}", retryable=True)
            continue

        if resp.status_code in (400, 401):
            raise LLMError(f"認証またはモデルエラー ({resp.status_code})。キーやモデル名を確認して。")
        if resp.status_code == 429:
            # 間を置かずに投げ直しても、分あたりの枠は空いていないので必ず失敗する。
            # 失敗したぶんも枠を食うので、ここでは諦めて呼び出し側に返す。
            raise LLMError("いま混み合っているみたい。少し待ってからもう一度送って。")
        if resp.status_code >= 500:
            last_error = LLMError(f"サーバーエラー ({resp.status_code})。", retryable=True)
            continue
        if resp.status_code != 200:
            raise LLMError(f"要求失敗 ({resp.status_code}): {resp.text[:200]}")

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
