from .. import config
from ..memory import retrieve
from . import presence

QUESTION_WORDS = ("?", "？", "いつ", "どこ", "誰", "なに", "何", "なぜ", "どうして", "どれ", "どう")
PAST_WORDS = ("昨日", "一昨日", "前回", "この前", "あの時", "以前", "覚えてる", "覚えてます", "話した", "言った")
OP_WORDS = ("覚えて", "訂正", "忘れて", "削除")


def decide(conn, text: str) -> str:
    if not config.FAST_ENABLED:
        return "slow"
    if len(text) > config.FAST_MAX_INPUT_CHARS:
        return "slow"
    for w in QUESTION_WORDS + PAST_WORDS + OP_WORDS:
        if w in text:
            return "slow"
    if retrieve.match_tags(text, retrieve.load_tag_dict(conn)):
        return "slow"
    # 機械のことは Slow でしか動かせない。**Fast は [DO:] を読まない**ので、
    # ここを落とすと「頼んだのにやってくれない、しかも黙って」になる。
    if presence.asked_about_machine(text):
        return "slow"
    return "fast"