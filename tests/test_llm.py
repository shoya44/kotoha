"""Gemini とのやりとりの検証。実際のAPIには接続しない。"""

import tempfile
import unittest
from pathlib import Path

import httpx

from kotoha import config

_TMP = tempfile.TemporaryDirectory(prefix="kotoha llm ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.talk import llm  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def reply(text="おかえり。"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def spare(test, model=""):
    """控えのモデルをこの試験のあいだだけ決める。空なら控え無し。"""
    test.addCleanup(setattr, config, "GEMINI_FALLBACK_MODEL", config.GEMINI_FALLBACK_MODEL)
    config.GEMINI_FALLBACK_MODEL = model


class LlmTests(unittest.TestCase):
    def setUp(self):
        spare(self)

    def use(self, *responses):
        """呼ばれた順に返す。呼ばれた回数と、どのモデルに頼んだかを記録する。"""
        self.calls = []
        self.models = []
        queue = list(responses)

        def fake_post(url, **kwargs):
            self.calls.append(url)
            self.models.append(kwargs["json"]["model"])
            item = queue.pop(0) if queue else responses[-1]
            if isinstance(item, Exception):
                raise item
            return item

        original = llm.httpx.post
        llm.httpx.post = fake_post
        self.addCleanup(setattr, llm.httpx, "post", original)

    def test_returns_the_reply(self):
        self.use(reply("おかえり。"))
        self.assertEqual(llm.chat("やっほー"), "おかえり。")
        self.assertEqual(len(self.calls), 1)

    def test_rate_limit_is_reported_without_retrying(self):
        """429 で投げ直しても枠は空いていない。失敗ぶんも枠を食うので即あきらめる。"""
        self.use(httpx.Response(429, json={}), reply())
        with self.assertRaises(llm.LLMError) as caught:
            llm.chat("やっほー")
        self.assertEqual(len(self.calls), 1)  # 投げ直していない
        self.assertFalse(caught.exception.retryable)
        self.assertIn("混み合って", str(caught.exception))

    def test_server_error_is_retried(self):
        self.use(httpx.Response(503, text="busy"), reply("ただいま。"))
        self.assertEqual(llm.chat("やっほー"), "ただいま。")
        self.assertEqual(len(self.calls), 2)

    def test_connection_error_is_retried(self):
        self.use(httpx.ConnectError("boom"), reply("ただいま。"))
        self.assertEqual(llm.chat("やっほー"), "ただいま。")
        self.assertEqual(len(self.calls), 2)

    def test_empty_reply_is_retried(self):
        self.use(reply("   "), reply("ただいま。"))
        self.assertEqual(llm.chat("やっほー"), "ただいま。")
        self.assertEqual(len(self.calls), 2)

    def test_auth_error_is_not_retried(self):
        self.use(httpx.Response(401, json={}), reply())
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(len(self.calls), 1)

    def test_gives_up_after_the_configured_attempts(self):
        self.use(httpx.Response(503, text="busy"))
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(len(self.calls), config.LLM_ATTEMPTS)


class FallbackTests(unittest.TestCase):
    """投げ直しは控えのモデルへ。混んでいるモデルに投げ直しても、また断られる。"""

    def setUp(self):
        spare(self, "spare-model")
        self.addCleanup(setattr, config, "GEMINI_MODEL", config.GEMINI_MODEL)
        config.GEMINI_MODEL = "main-model"

    use = LlmTests.use

    def test_busy_goes_to_the_spare_without_counting(self):
        """すぐ断られた 503 は数えない。控えにも、決めた回数だけ試す。"""
        self.use(httpx.Response(503, text="busy"))
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(self.models,
                         ["main-model"] + ["spare-model"] * config.LLM_ATTEMPTS)

    def test_spare_answers_after_busy(self):
        self.use(httpx.Response(503, text="busy"), reply("ただいま。"))
        self.assertEqual(llm.chat("やっほー"), "ただいま。")
        self.assertEqual(self.models, ["main-model", "spare-model"])

    def test_timeout_counts_and_moves_to_the_spare(self):
        """待ちきれなかったぶんは数える。もう一度同じだけ待たせないため。"""
        self.use(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(self.models, ["main-model", "spare-model"][:config.LLM_ATTEMPTS])

    def test_hurried_still_escapes_a_busy_model(self):
        """1回しか試さない裏の仕事も、すぐ断られたなら控えに逃げられる。"""
        self.use(httpx.Response(503, text="busy"), reply("整理した。"))
        with llm.hurried():
            self.assertEqual(llm.chat("整理して"), "整理した。")
        self.assertEqual(self.models, ["main-model", "spare-model"])

    def test_hurried_does_not_wait_twice(self):
        self.use(httpx.ReadTimeout("slow"), reply("整理した。"))
        with llm.hurried(), self.assertRaises(llm.LLMError):
            llm.chat("整理して")
        self.assertEqual(self.models, ["main-model"])

    def test_only_the_spare_is_told_to_think_less(self):
        """控えの flash は長く考えて、上限の小さい日記では答えまで届かない。"""
        self.addCleanup(setattr, config, "GEMINI_FALLBACK_REASONING",
                        config.GEMINI_FALLBACK_REASONING)
        config.GEMINI_FALLBACK_REASONING = "minimal"
        sent = []
        self.use(httpx.Response(503, text="busy"), reply("ただいま。"))
        post = llm.httpx.post
        llm.httpx.post = lambda url, **kwargs: (sent.append(kwargs["json"]), post(url, **kwargs))[1]
        llm.chat("やっほー")
        self.assertEqual([body.get("reasoning_effort") for body in sent], [None, "minimal"])

    def test_spare_first_skips_the_main_model(self):
        self.use(reply("ただいま。"))
        llm.chat("やっほー", spare_first=True)
        self.assertEqual(self.models, ["spare-model"])

    def test_rate_limit_is_not_sent_to_the_spare(self):
        self.use(httpx.Response(429, json={}), reply())
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(self.models, ["main-model"])


class StreamFallbackTests(unittest.TestCase):
    """流す道は、1文字も出す前に 5xx で断られたときだけ控えで流し直す。"""

    def setUp(self):
        spare(self, "spare-model")
        self.addCleanup(setattr, config, "GEMINI_MODEL", config.GEMINI_MODEL)
        config.GEMINI_MODEL = "main-model"
        self.models = []

    def use(self, *statuses):
        queue = list(statuses)
        test = self

        class Fake:
            def __init__(self, method, url, **kwargs):
                test.models.append(kwargs["json"]["model"])
                status = queue.pop(0)
                body = ('data: {"choices":[{"delta":{"content":"ただいま。"}}]}\n'
                        "data: [DONE]\n") if status == 200 else "busy"
                self.response = httpx.Response(status, text=body)

            def __enter__(self):
                return self.response

            def __exit__(self, *exc):
                return False

        self.addCleanup(setattr, llm.httpx, "stream", llm.httpx.stream)
        llm.httpx.stream = Fake

    def test_busy_stream_moves_to_the_spare(self):
        self.use(503, 200)
        self.assertEqual("".join(llm.stream("やっほー")), "ただいま。")
        self.assertEqual(self.models, ["main-model", "spare-model"])

    def test_busy_spare_gives_up(self):
        self.use(503, 503)
        with self.assertRaises(llm.LLMError):
            list(llm.stream("やっほー"))
        self.assertEqual(self.models, ["main-model", "spare-model"])

    def test_without_a_spare_it_does_not_retry(self):
        spare(self)
        self.use(503, 200)
        with self.assertRaises(llm.LLMError):
            list(llm.stream("やっほー"))
        self.assertEqual(self.models, ["main-model"])


class HurriedTests(unittest.TestCase):
    """裏の仕事は短い上限で1回だけ。会話の糸には影響しない。"""

    def setUp(self):
        spare(self)
        self.addCleanup(setattr, config, "BACKGROUND_TIMEOUT_SECONDS", config.BACKGROUND_TIMEOUT_SECONDS)
        config.BACKGROUND_TIMEOUT_SECONDS = 7.0
        self.timeouts = []

        def fake_post(url, **kwargs):
            self.timeouts.append(kwargs.get("timeout"))
            return httpx.Response(503, text="busy")

        self.addCleanup(setattr, llm.httpx, "post", llm.httpx.post)
        llm.httpx.post = fake_post

    def test_hurried_tries_once_with_the_short_limit(self):
        with llm.hurried():
            with self.assertRaises(llm.LLMError):
                llm.chat("整理して")
        self.assertEqual(self.timeouts, [7.0])

    def test_the_hurry_ends_with_the_block(self):
        with llm.hurried():
            pass
        with self.assertRaises(llm.LLMError):
            llm.chat("やっほー")
        self.assertEqual(len(self.timeouts), config.LLM_ATTEMPTS)
        self.assertEqual(self.timeouts[0], config.TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
