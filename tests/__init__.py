"""ことはのテスト。`python -m unittest discover -s tests -t .` で全部走る。

ここで**外へ出る道を塞ぐ**。テストは本物のGeminiを叩かず、本物の通知も
送らない約束になっている。個々のテストの気遣いだけに任せていたところ、
巡回をまるごと一周させるテストが本当に通知を送っていた（2026-09-18に発覚。
宛先の指定が間違っていて誰にも届いていなかったため、丸一日気づけなかった）。

塞ぎ方は「黙って無視」ではなく「その場で落とす」。うっかり外を叩いた
テストは失敗して、書いた人に分かる。
"""

import httpx

from kotoha import config

# 通知は既定で止めておく。鳴らす道そのものを試すテストだけが、自分で開ける。
config.PUSH_ENABLED = False


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
