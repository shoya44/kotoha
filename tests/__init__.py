"""ことはのテスト。`python -m unittest discover -s tests -t .` で全部走る。

ここで**外へ出る道を塞ぐ**。テストは本物のGeminiを叩かず、本物の通知も
送らない約束になっている。個々のテストの気遣いだけに任せていたところ、
巡回をまるごと一周させるテストが本当に通知を送っていた（2026-09-18に発覚。
宛先の指定が間違っていて誰にも届いていなかったため、丸一日気づけなかった）。

塞ぎ方は「黙って無視」ではなく「その場で落とす」。うっかり外を叩いた
テストは失敗して、書いた人に分かる。
"""

import pathlib
import tempfile

import httpx

from kotoha import config, notify

# 通知は既定で止めておく。鳴らす道そのものを試すテストだけが、自分で開ける。
config.PUSH_ENABLED = False

# **書き置きも外へ出さない。** notify.log は持ち主が事故を追うための1本で、
# そこにテストの「失敗したふり」が混ざると、本物の記録が読めなくなる
# （実際に、テストの出した「控えを置けなかった」が本番のログに並んだ）。
_LOGS = tempfile.TemporaryDirectory(prefix="kotoha logs ")
notify.LOG_PATH = pathlib.Path(_LOGS.name) / "notify.log"

# **本物の ~/.claude も読まない。** 読むだけとはいえ、そこに何があるかで
# プロンプトの行が増えたり減ったりすると、テストが持ち主の作業状況で揺れる。
from kotoha.talk import coding  # noqa: E402

coding.SESSIONS_DIR = pathlib.Path(_LOGS.name) / "claude-projects"


class WentOutside(RuntimeError):
    """テストから外へ出ようとした。差し替え忘れ。"""


_send = httpx.Client.send


def _guard(self, request, **kwargs):
    # FastAPIのTestClientもhttpxで出来ているが、あれは同じプロセスの中で
    # 折り返すだけで外へは出ない。塞ぐのは、本当に線をつなぐものだけ。
    if not isinstance(getattr(self, "_transport", None), httpx.HTTPTransport):
        return _send(self, request, **kwargs)
    raise WentOutside(
        f"テストから外へつなごうとしました（{request.url.host}）。差し替えてください。"
    )


httpx.Client.send = _guard
