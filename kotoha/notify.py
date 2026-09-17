"""OneSignal 経由で、iPhoneに通知を送る。

Web Push を自前で作ると VAPID の署名と暗号化が要り、`cryptography` あたりを
抱えることになる。依存3つで済んでいる構成に対して重いので、送るところは
OneSignal に任せ、こちらはREST APIを1回叩くだけにしてある。

送れなくても会話は続ける。失敗は記録するだけで、呼び出し側へは投げない。
読み上げや想起と同じ約束。
"""

import time

import httpx

from . import config

ENDPOINT = "https://api.onesignal.com/notifications"
LOG_PATH = config.BASE_DIR / "data" / "notify.log"
TIMEOUT = 10.0

# voice.py と同じ理由で、接続は開いたまま使い回す。相手は外なのでプロキシは見る。
_client = None


def _http():
    global _client
    if _client is None:
        _client = httpx.Client()
    return _client


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n")
    except OSError:
        pass


def ready() -> bool:
    return bool(config.PUSH_ENABLED and config.ONESIGNAL_APP_ID and config.ONESIGNAL_API_KEY)


def push(title: str, body: str, quiet_body: str = "ことはから") -> bool:
    """通知を送る。送れたかどうかを返す。

    本文をそのまま載せるかは設定で選べる。載せると OneSignal を通るので、
    会話の中身が外のサーバーに渡る。伏せる場合は、開いてもらって読む形になる。
    """
    if not ready():
        return False
    shown = body if config.PUSH_SHOW_TEXT else quiet_body
    payload = {
        "app_id": config.ONESIGNAL_APP_ID,
        "included_segments": ["Subscribed Users"],
        "headings": {"en": title},
        # 言語別に入れる決まりで、en は必ず要る。日本語をそのまま入れてよい。
        "contents": {"en": shown},
        "url": config.PUSH_OPEN_URL or None,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    try:
        response = _http().post(
            ENDPOINT, json=payload, timeout=TIMEOUT,
            headers={"Authorization": f"Key {config.ONESIGNAL_API_KEY}"},
        )
    except httpx.HTTPError as error:
        log(f"送れなかった: {error!r}")
        return False
    if response.status_code >= 300:
        # 鍵は出さない。本文だけ短く残す。
        log(f"断られた ({response.status_code}): {response.text[:200]}")
        return False
    log(f"送った: {title} / {shown[:60]}")
    return True
