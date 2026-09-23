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


class LlmTests(unittest.TestCase):
    def use(self, *responses):
        """呼ばれた順に返す。呼ばれた回数を数えたいので記録もする。"""
        self.calls = []
        queue = list(responses)

        def fake_post(url, **kwargs):
            self.calls.append(url)
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


class HurriedTests(unittest.TestCase):
    """裏の仕事は短い上限で1回だけ。会話の糸には影響しない。"""

    def setUp(self):
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
