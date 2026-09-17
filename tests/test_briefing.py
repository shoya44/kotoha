"""朝のひとことと、空模様の読み。外へは一度も出ない。"""

import unittest
from datetime import datetime

import httpx

from kotoha import config
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("briefing")

from kotoha import notify  # noqa: E402
from kotoha.memory import db  # noqa: E402
from kotoha.serve import announce as announce_mod, jobs, web  # noqa: E402
from kotoha.talk import chat, weather  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


DAILY = {
    "daily": {
        "time": ["2026-09-18"],
        "weather_code": [3],
        "temperature_2m_max": [21.4],
        "temperature_2m_min": [14.2],
        "precipitation_probability_max": [20],
    }
}


class SkyTests(unittest.TestCase):
    """傘と服装は、言い回しではなく数字で決める。"""

    def test_a_high_chance_means_an_umbrella(self):
        self.assertTrue(weather.umbrella(3, 50))
        self.assertFalse(weather.umbrella(3, 49))

    def test_rain_means_an_umbrella_even_when_unlikely(self):
        """降水確率が低くても、雨と言われた日は持たせる。"""
        self.assertTrue(weather.umbrella(63, 10))
        self.assertTrue(weather.umbrella(75, 0))    # 雪
        self.assertTrue(weather.umbrella(95, 0))    # 雷雨

    def test_what_to_wear(self):
        self.assertEqual(weather.clothes(25), "半袖")
        self.assertEqual(weather.clothes(24.9), "長袖")
        self.assertEqual(weather.clothes(18), "長袖")
        self.assertEqual(weather.clothes(17.9), "上着がいる")

    def use(self, payload=DAILY, status=200, error=None):
        def get(url, **kwargs):
            if error:
                raise error
            # raise_for_status は request を見る。付けずに作ると落ちる。
            return httpx.Response(status, json=payload,
                                  request=httpx.Request("GET", url))

        client = weather._http()
        self.addCleanup(setattr, client, "get", client.get)
        client.get = get

    def test_it_reads_the_forecast(self):
        self.use()
        sky = weather.today()
        self.assertEqual(sky["word"], "くもり")
        self.assertEqual(sky["clothes"], "長袖")
        self.assertFalse(sky["umbrella"])

    def test_a_silent_service_is_not_an_error(self):
        """天気が取れないくらいで朝の挨拶をやめる理由はない。"""
        self.use(error=httpx.ConnectError("圏外"))
        self.assertIsNone(weather.today())
        self.assertEqual(weather.block(None), "")

    def test_a_broken_answer_is_not_an_error(self):
        self.use(payload={"daily": {}})
        self.assertIsNone(weather.today())

    def test_the_block_carries_the_judgement(self):
        self.use()
        text = weather.block(weather.today())
        self.assertIn("傘: いらない", text)
        self.assertIn("服装: 長袖", text)


class BriefingTests(DbCase):
    """朝いちばんに一度だけ。"""

    def setUp(self):
        super().setUp()
        for name, value in (("BRIEFING_ENABLED", True), ("BRIEFING_HOUR", 8),
                            ("BRIEFING_GRACE_HOURS", 3)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"),
                            (chat, "speak"), (jobs, "weather")):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
        notify.ready = lambda: True
        notify.log = lambda text: None
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        self.given = []
        chat.speak = lambda conn, closing, extra="", keep=True: (
            self.given.append(extra) or "おはよ。傘いるよ。")
        jobs.weather = FakeSky()

    def at(self, hour):
        """時計を動かす代わりに、その時刻で呼んだことにする。"""
        moment = datetime.now().replace(hour=hour, minute=30)
        self.addCleanup(setattr, jobs, "datetime", jobs.datetime)
        jobs.datetime = Clock(moment)

    def test_it_speaks_in_the_morning(self):
        self.at(8)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.pushed, ["おはよ。傘いるよ。"])

    def test_it_keeps_quiet_before_the_hour(self):
        self.at(7)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.pushed, [])

    def test_it_says_it_once_a_day(self):
        self.at(8)
        jobs.maybe_briefing(self.conn)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_a_late_start_still_gets_a_greeting(self):
        """8時にPCが寝ていても、昼前に起きたなら言う。"""
        self.at(10)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(len(self.pushed), 1)

    def test_it_does_not_say_good_morning_in_the_evening(self):
        self.at(19)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.pushed, [])

    def test_a_missed_morning_is_not_owed_tomorrow(self):
        """夕方に見送った日は、その日の分として畳む。"""
        self.at(19)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(db.get_state(self.conn, "last_briefing_on"),
                         datetime.now().strftime("%Y-%m-%d"))

    def test_switched_off_says_nothing(self):
        config.BRIEFING_ENABLED = False
        self.at(8)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.pushed, [])

    def test_the_sky_is_handed_over(self):
        self.at(8)
        jobs.maybe_briefing(self.conn)
        self.assertIn("傘: いる", self.given[0])

    def test_a_sky_it_cannot_see_is_not_fatal(self):
        jobs.weather = FakeSky(broken=True)
        self.at(8)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(len(self.pushed), 1)       # 挨拶は出る
        self.assertEqual(self.given[0], "")         # 空模様だけ抜ける

    def speaks_for_real(self):
        """文だけ替えて、残すところは本物に通す。"""
        def speak(conn, closing, extra="", keep=True):
            chat.remember(conn, "おはよ", keep=keep)
            return "おはよ"

        chat.speak = speak

    def kept(self):
        row = self.conn.execute(
            "SELECT extractable FROM messages WHERE role = 'assistant'").fetchone()
        return row["extractable"]

    def test_the_weather_is_not_kept_forever(self):
        """今日の天気を毎朝1件ずつ溜めても、あとから邪魔になるだけ。"""
        self.speaks_for_real()
        self.at(8)
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.kept(), 0)   # 画面には残るが、記憶には昇格しない

    def test_what_it_says_out_of_the_blue_is_still_remembered(self):
        """天気と違い、ふだんの声かけは会話の流れの一部なので覚える。"""
        self.speaks_for_real()
        announce_mod.announce(self.conn, "何か言う")
        self.assertEqual(self.kept(), 1)

    def test_a_failure_does_not_repeat_all_morning(self):
        """文を作れなくても、毎分やり直さない。APIを空回りさせない。"""
        def explode(conn, closing, extra="", keep=True):
            raise RuntimeError("だめ")

        chat.speak = explode
        self.at(8)
        jobs.maybe_briefing(self.conn)
        chat.speak = lambda conn, closing, extra="", keep=True: "おはよ"
        jobs.maybe_briefing(self.conn)
        self.assertEqual(self.pushed, [])


class AnnounceTests(DbCase):
    """通知はすべてここを通る。言わずに鳴らさない。"""

    def setUp(self):
        super().setUp()
        for owner, name in ((notify, "ready"), (notify, "push"), (notify, "log"),
                            (chat, "speak")):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
        notify.ready = lambda: True
        notify.log = lambda text: None
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True

    def said(self):
        return [r["text"] for r in
                self.conn.execute("SELECT text FROM messages WHERE role = 'assistant'")]

    def test_what_it_says_is_what_it_sends(self):
        chat.speak = lambda conn, closing, extra="", keep=True: "ねえ、聞いてる？"
        announce_mod.announce(self.conn, "何か言う")
        self.assertEqual(self.pushed, ["ねえ、聞いてる？"])

    def test_what_it_sends_is_left_in_the_conversation(self):
        """通知だけ来て、開いても何も無いのは不親切。"""
        self.addCleanup(setattr, chat, "llm", chat.llm)
        chat.llm = Mouth("うん、ここにいるよ")
        announce_mod.announce(self.conn, "何か言う")
        self.assertEqual(self.said(), ["うん、ここにいるよ"])

    def test_it_falls_back_to_plain_words(self):
        """文を作れなくても、伝えたいことは伝える。"""
        def explode(conn, closing, extra="", keep=True):
            raise RuntimeError("だめ")

        chat.speak = explode
        announce_mod.announce(self.conn, "知らせる", plain="音声エンジンが止まったみたい")
        self.assertEqual(self.pushed, ["音声エンジンが止まったみたい"])
        self.assertEqual(self.said(), ["音声エンジンが止まったみたい"])

    def test_without_plain_words_it_stays_silent(self):
        def explode(conn, closing, extra="", keep=True):
            raise RuntimeError("だめ")

        chat.speak = explode
        announce_mod.announce(self.conn, "声をかける")
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.said(), [])

    def test_it_does_nothing_when_push_is_not_set_up(self):
        notify.ready = lambda: False
        chat.speak = lambda conn, closing, extra="", keep=True: "おーい"
        announce_mod.announce(self.conn, "何か言う")
        self.assertEqual(self.pushed, [])
        self.assertEqual(self.said(), [])


class Clock:
    """web が見る datetime の代役。now() だけ差し替える。"""

    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment


class FakeSky:
    def __init__(self, broken=False):
        self.broken = broken

    def today(self):
        return None if self.broken else {"umbrella": True}

    def block(self, sky):
        return "" if sky is None else "今日の空模様:\n  傘: いる"


class Mouth:
    """llm の代役。"""

    def __init__(self, text):
        self.text = text

    def chat(self, prompt):
        return self.text


if __name__ == "__main__":
    unittest.main()
