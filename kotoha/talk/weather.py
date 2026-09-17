"""今日の空模様。Open-Meteo から取る。

鍵もアカウントも要らず、応答は500バイトほど。依存を増やさずに済む。
傘と服装はここで決め、言い回しはことはに任せる。型にはめると人格が死ぬ。
"""

import httpx

from .. import config

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8.0

# WMOの天気コード。Open-Meteo が返すのは数字だけなので、日本語にする。
WORDS = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々くもり", 3: "くもり",
    45: "霧", 48: "霧",
    51: "霧雨", 53: "霧雨", 55: "強い霧雨",
    56: "凍える霧雨", 57: "凍える霧雨",
    61: "小雨", 63: "雨", 65: "強い雨",
    66: "凍える雨", 67: "凍える雨",
    71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
    80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "強いにわか雪",
    95: "雷雨", 96: "雹まじりの雷雨", 99: "雹まじりの雷雨",
}
# 降水確率が低くても、これらの日は傘を持たせる。
WET = set(range(51, 68)) | set(range(71, 87)) | {95, 96, 99}

_client = None


def _http():
    """最初に使うときだけ作る。取り込み時に作ると設定の検査を邪魔する。"""
    global _client
    if _client is None:
        _client = httpx.Client(trust_env=False)
    return _client


def umbrella(code: int, chance: int) -> bool:
    return chance >= 50 or code in WET


def clothes(high: float) -> str:
    if high >= 25:
        return "半袖"
    if high >= 18:
        return "長袖"
    return "上着がいる"


def today():
    """今日の空模様。取れなければ None を返す。

    天気が取れないくらいで朝の挨拶をやめる理由はないので、
    呼ぶ側が None を受け取っても続けられるようにしてある。
    """
    try:
        response = _http().get(ENDPOINT, timeout=TIMEOUT, params={
            "latitude": config.LATITUDE,
            "longitude": config.LONGITUDE,
            "timezone": "Asia/Tokyo",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "forecast_days": 1,
        })
        response.raise_for_status()
        daily = response.json()["daily"]
        code = int(daily["weather_code"][0])
        high = float(daily["temperature_2m_max"][0])
        low = float(daily["temperature_2m_min"][0])
        chance = int(daily["precipitation_probability_max"][0] or 0)
    except Exception:
        return None
    return {
        "word": WORDS.get(code, "はっきりしない空"),
        "high": high,
        "low": low,
        "chance": chance,
        "umbrella": umbrella(code, chance),
        "clothes": clothes(high),
    }


def block(sky) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。"""
    if not sky:
        return ""
    return (
        "今日の空模様:\n"
        f"  {sky['word']} / 最高{sky['high']:.0f}℃ 最低{sky['low']:.0f}℃ "
        f"/ 降水確率{sky['chance']}%\n"
        f"  傘: {'いる' if sky['umbrella'] else 'いらない'}\n"
        f"  服装: {sky['clothes']}"
    )
