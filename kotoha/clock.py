"""時計は1本。**時間の経過を、テストで日単位に進めるため。**

記憶の強さが日ごとに薄れる、機嫌が1時間で半分戻る、日記が日付をまたいで
書かれる。どれも「時間がたった」を再現できないと確かめられない。SQLite の
'now' と Python の datetime.now() が別々に時を刻んでいると、片方だけ進めた
テストは嘘をつく。ここを通せば、両方が同じ時刻を見る。

本番では何も変わらない。`freeze` は tests/support.py からだけ使う。
"""

from datetime import datetime, timezone

STAMP = "%Y-%m-%dT%H:%M:%SZ"

_frozen = None


def now() -> datetime:
    """ローカルの今。datetime.now() の代わり。"""
    if _frozen is not None:
        return _frozen.astimezone()
    return datetime.now()


def utc_now() -> datetime:
    """UTCの今（tz付き）。凍結した時刻がローカルでも、ここでUTCに直す。"""
    if _frozen is not None:
        return _frozen.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def utc() -> str:
    """DBに書く形（UTC、秒まで）。db.now_utc() の実体。"""
    return utc_now().strftime(STAMP)


def freeze(moment) -> None:
    """時計を止める（テスト用）。None で戻す。naive なら UTC として扱う。"""
    global _frozen
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    _frozen = moment
