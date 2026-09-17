"""読み上げの検証。実際の音声エンジンには接続しない。"""

import tempfile
import unittest
from pathlib import Path

import httpx

from kotoha import config

# db が参照する前に保存先を一時DBへ向ける。
_TMP = tempfile.TemporaryDirectory(prefix="kotoha voice ")
config.DB_PATH = Path(_TMP.name) / "test.sqlite3"

from kotoha.serve import voice  # noqa: E402

WAV = b"RIFF\x00\x00\x00\x00WAVEfmt "


def tearDownModule():
    _TMP.cleanup()


class FakeEngine:
    """AivisSpeech の代わり。呼ばれ方を記録する。"""

    def __init__(self, query_status=200, synthesis_status=200, error=None):
        self.query_status = query_status
        self.synthesis_status = synthesis_status
        self.error = error
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((url, kwargs.get("params"), kwargs.get("json")))
        if self.error:
            raise self.error
        if url.endswith("/audio_query"):
            return httpx.Response(self.query_status, json={"speedScale": 1.0})
        return httpx.Response(self.synthesis_status, content=WAV)


class StaleOnce(FakeEngine):
    """最初の1回だけ、切れた接続のふりをする。"""

    def __call__(self, method, url, **kwargs):
        if not self.calls:
            # 記録は親に任せる。ここで足すと同じ呼び出しを二重に数えてしまう。
            self.calls.append((url, kwargs.get("params"), kwargs.get("json")))
            raise httpx.RemoteProtocolError("Server disconnected")
        return super().__call__(method, url, **kwargs)


class SpeakTests(unittest.TestCase):
    def use(self, engine):
        # 接続は使い回す1つに集約したので、差し替え先もそこになる。
        original = voice._client.request
        voice._client.request = engine
        self.addCleanup(setattr, voice._client, "request", original)
        return engine

    def test_returns_audio(self):
        self.use(FakeEngine())
        self.assertEqual(voice.speak("おかえり"), WAV)

    def test_sends_text_and_style_to_the_engine(self):
        engine = self.use(FakeEngine())
        voice.speak("ただいま")
        query_url, query_params, _ = engine.calls[0]
        synth_url, synth_params, synth_body = engine.calls[1]
        self.assertTrue(query_url.endswith("/audio_query"))
        self.assertEqual(query_params["text"], "ただいま")
        self.assertEqual(query_params["speaker"], config.VOICE_STYLE_ID)
        self.assertTrue(synth_url.endswith("/synthesis"))
        self.assertEqual(synth_params["speaker"], config.VOICE_STYLE_ID)
        self.assertEqual(synth_body, {"speedScale": 1.0})  # クエリをそのまま渡す

    def test_empty_text_is_refused_before_calling_the_engine(self):
        engine = self.use(FakeEngine())
        with self.assertRaises(voice.VoiceError):
            voice.speak("   ")
        self.assertEqual(engine.calls, [])

    def test_long_text_is_clipped(self):
        engine = self.use(FakeEngine())
        voice.speak("あ" * (voice.MAX_CHARS + 500))
        self.assertEqual(len(engine.calls[0][1]["text"]), voice.MAX_CHARS)

    def test_newlines_are_folded(self):
        self.assertEqual(voice.clip("一行目\n二行目"), "一行目 二行目")

    def test_engine_not_running_is_reported_clearly(self):
        self.use(FakeEngine(error=httpx.ConnectError("refused")))
        with self.assertRaises(voice.VoiceError) as caught:
            voice.speak("おーい")
        self.assertIn("AivisSpeech", str(caught.exception))

    def test_timeout_is_reported(self):
        self.use(FakeEngine(error=httpx.ReadTimeout("slow")))
        with self.assertRaises(voice.VoiceError):
            voice.speak("おーい")

    def test_engine_error_status_is_reported(self):
        self.use(FakeEngine(synthesis_status=500))
        with self.assertRaises(voice.VoiceError):
            voice.speak("おーい")

    def test_stale_connection_is_retried_once(self):
        """エンジンが再起動すると、開いたままの接続は黙って切れている。"""
        engine = self.use(StaleOnce())
        self.assertEqual(voice.speak("おかえり"), WAV)
        # 1回目の失敗ぶんを足して、audio_query が2回、synthesis が1回。
        self.assertEqual(len(engine.calls), 3)

    def test_engine_really_down_is_not_retried_forever(self):
        engine = self.use(FakeEngine(error=httpx.RemoteProtocolError("closed")))
        with self.assertRaises(voice.VoiceError):
            voice.speak("おーい")
        self.assertEqual(len(engine.calls), 2)  # やり直しは一度きり


class EndpointTests(unittest.TestCase):
    """読み上げが落ちても会話は落ちないこと。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        from kotoha.memory import db
        from kotoha.serve import web

        if config.DB_PATH.exists():
            config.DB_PATH.unlink()
        conn = db.connect()
        db.init(conn)
        conn.close()
        self.token = config.WEB_TOKEN
        config.WEB_TOKEN = "testtoken"
        self.addCleanup(setattr, config, "WEB_TOKEN", self.token)
        self.enabled = config.VOICE_ENABLED
        self.addCleanup(setattr, config, "VOICE_ENABLED", self.enabled)
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def post(self, text, headers=None):
        return self.client.post(
            "/api/speak",
            json={"text": text},
            headers=self.headers if headers is None else headers,
        )

    def use(self, engine):
        original = voice._client.request
        voice._client.request = engine
        self.addCleanup(setattr, voice._client, "request", original)

    def test_returns_wav(self):
        config.VOICE_ENABLED = True
        self.use(FakeEngine())
        response = self.post("おかえり")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.content, WAV)

    def test_requires_a_token(self):
        self.assertEqual(self.post("おかえり", headers={}).status_code, 401)

    def test_empty_text_is_rejected(self):
        config.VOICE_ENABLED = True
        self.assertEqual(self.post("   ").status_code, 400)

    def test_disabled_returns_service_unavailable(self):
        config.VOICE_ENABLED = False
        self.assertEqual(self.post("おかえり").status_code, 503)

    def test_engine_down_returns_service_unavailable(self):
        config.VOICE_ENABLED = True
        self.use(FakeEngine(error=httpx.ConnectError("refused")))
        self.assertEqual(self.post("おかえり").status_code, 503)


if __name__ == "__main__":
    unittest.main()
