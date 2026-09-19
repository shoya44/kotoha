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

# 昨日との最高気温の差。これ未満は毎日のことなので言わない。
SWING_FLOOR = 3.0
# この確率に届いた時間を「降り出す」とみなす。
RAIN_FLOOR = 50
# 起きている時間の中で、どれだけ下がったら気圧に触れるか（hPa）。
# **目安の値。** 体感に合わないときはここを動かす。
FALL_FLOOR = 4.0
# 空模様を見る時間帯。寝ているあいだの雨は言っても仕方がない。
AWAKE = range(6, 23)

_client = None


def _http():
    """最初に使うときだけ作る。取り込み時に作ると設定の検査を邪魔する。"""
    global _client
    if _client is None:
        _client = httpx.Client(trust_env=False)
    return _client


def umbrella(code: int, chance: int) -> bool:
    return chance >= 50 or code in WET


def rain_from(hours):
    """降り出す時刻。起きている時間で、最初に確率が届くところ。"""
    for hour, chance in hours:
        if hour in AWAKE and chance is not None and chance >= RAIN_FLOOR:
            return hour
    return None


def biggest_fall(values) -> float:
    """その日のうちで、いちばん大きな下がり幅。

    最高値と最低値の差ではない。**下がった幅**が知りたいので、
    高いところから、そのあとの低いところまでの落差を見る。
    """
    top = None
    fall = 0.0
    for value in values:
        if value is None:
            continue
        if top is None or value > top:
            top = value
        fall = max(fall, top - value)
    return fall


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
        # **1回の呼び出しで全部取る。** 昨日の気温も、時間ごとの雨と気圧も、
        # 同じ応答に入っている。材料が増えても外への往復は増やさない。
        response = _http().get(ENDPOINT, timeout=TIMEOUT, params={
            "latitude": config.LATITUDE,
            "longitude": config.LONGITUDE,
            "timezone": "Asia/Tokyo",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "hourly": "precipitation_probability,surface_pressure",
            "past_days": 1,
            "forecast_days": 1,
        })
        response.raise_for_status()
        answer = response.json()
        daily = answer["daily"]
        # past_days=1 なので [昨日, 今日] の2日ぶん返る。
        code = int(daily["weather_code"][-1])
        high = float(daily["temperature_2m_max"][-1])
        low = float(daily["temperature_2m_min"][-1])
        chance = int(daily["precipitation_probability_max"][-1] or 0)
        yesterday = float(daily["temperature_2m_max"][0])
        stamp = daily["time"][-1]
        hourly = answer.get("hourly", {})
        times = hourly.get("time", [])
        rains = [(int(t[11:13]), c) for t, c in
                 zip(times, hourly.get("precipitation_probability", []))
                 if t[:10] == stamp]
        pressures = [p for t, p in zip(times, hourly.get("surface_pressure", []))
                     if t[:10] == stamp and int(t[11:13]) in AWAKE]
    except Exception:
        return None
    return {
        "word": WORDS.get(code, "はっきりしない空"),
        "high": high,
        "low": low,
        "chance": chance,
        "umbrella": umbrella(code, chance),
        "clothes": clothes(high),
        "swing": high - yesterday,
        "rain_from": rain_from(rains),
        "fall": biggest_fall(pressures),
    }


def block(sky) -> str:
    """ことはに渡す形。判断はこちらで済ませ、言い方は向こうに任せる。

    **足すのは、今日だけ違うときだけ。** 毎朝出るものが増えると、朝の一言が
    ただ長くなる。差が小さい日や降らない日は、その行ごと出さない。
    """
    if not sky:
        return ""
    lines = [
        "今日の空模様:",
        f"  {sky['word']} / 最高{sky['high']:.0f}℃ 最低{sky['low']:.0f}℃ "
        f"/ 降水確率{sky['chance']}%",
        f"  傘: {'いる' if sky['umbrella'] else 'いらない'}",
        f"  服装: {sky['clothes']}",
    ]
    swing = sky.get("swing") or 0.0
    if abs(swing) >= SWING_FLOOR:
        lines.append(f"  昨日より{abs(swing):.0f}℃{'高い' if swing > 0 else '低い'}")
    if sky.get("rain_from") is not None:
        lines.append(f"  {sky['rain_from']}時ごろから降り出す")
    if (sky.get("fall") or 0.0) >= FALL_FLOOR:
        lines.append(f"  気圧が{sky['fall']:.0f}hPa下がる。体調に響くかもしれないと気にかける")
    return "\n".join(lines)
