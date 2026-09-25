"""ことはのほうから電話をかける。実際には鳴らさず、Gemini も呼ばない。

見るのは約束ごとだけ。
- 通知には「電話」としか出さない。第一声は出てから渡す
- 同時に2本は鳴らさない。通話中にも鳴らさない
- 出なければ不在着信。頼まれた電話だけ、1回だけ掛け直す
- 出ないまま文字で返事があれば、黙って畳む
"""

import json
import unittest
from datetime import datetime, timedelta

from kotoha import clock, config, notify
from tests.support import DbCase, use_temp_db

_TMP = use_temp_db("ring")

from kotoha.memory import db, remind  # noqa: E402
from kotoha.serve import announce, hub, jobs, web  # noqa: E402
from kotoha.talk import chat  # noqa: E402

OPENER = "ねえ、昨日の映画の続き、どうなった？"


def tearDownModule():
    _TMP.cleanup()


class FakeLoop:
    def call_soon_threadsafe(self, call, argument):
        call(argument)


class FakeQueue:
    def __init__(self):
        self.items = []

    def full(self):
        return False

    def get_nowait(self):
        return self.items.pop(0)

    def put_nowait(self, payload):
        self.items.append(json.loads(payload))


class RingCase(DbCase):
    def setUp(self):
        super().setUp()
        hub.reset()
        self.addCleanup(hub.reset)
        for name in ("ready", "push", "log", "trace"):
            self.addCleanup(setattr, notify, name, getattr(notify, name))
        self.addCleanup(setattr, chat, "speak", chat.speak)
        notify.log = lambda text: None
        notify.trace = lambda *a: None
        notify.ready = lambda: True
        self.pushed = []
        notify.push = lambda title, body, *a, **k: self.pushed.append(body) or True
        self.closings = []
        chat.speak = lambda conn, closing, extra="", keep=True, chain=None: (
            self.closings.append(closing) or OPENER)

    def ring(self, kind=announce.REACH, **options):
        return announce.ring(self.conn, "電話をかけた。", kind, **options)

    def age_the_ring(self, minutes):
        ring = announce.ringing(self.conn)
        then = clock.utc_now() - timedelta(minutes=minutes)
        ring["at"] = then.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_state(self.conn, db.RING, json.dumps(ring))
        self.conn.commit()

    def history(self):
        return [r["text"] for r in self.conn.execute(
            "SELECT text FROM messages ORDER BY id")]


class RingingTests(RingCase):
    def test_the_notice_says_only_that_she_is_calling(self):
        """中身を先に読ませたら、電話にした意味がない。"""
        self.assertEqual(self.ring(), OPENER)
        self.assertEqual(self.pushed, [announce.RING_TITLE])
        self.assertIsNotNone(announce.ringing(self.conn))

    def test_answering_hands_over_the_first_words(self):
        self.ring()
        ring_id = announce.ringing(self.conn)["id"]
        self.assertEqual(announce.answer(self.conn, ring_id), OPENER)
        self.assertIsNone(announce.ringing(self.conn))

    def test_a_ring_that_is_gone_cannot_be_answered(self):
        self.ring()
        self.assertIsNone(announce.answer(self.conn, 12345))

    def test_it_does_not_ring_twice_at_once(self):
        self.ring()
        self.assertEqual(self.ring(), "")
        self.assertEqual(len(self.pushed), 1)

    def test_it_does_not_ring_during_a_call(self):
        hub.join("web-1", FakeQueue(), FakeLoop())
        hub.start_call("web-1")
        self.assertEqual(self.ring(), "")

    def test_an_open_screen_gets_the_ring_instead_of_a_notice(self):
        queue = FakeQueue()
        hub.join("web-1", queue, FakeLoop())
        self.ring()
        self.assertEqual(self.pushed, [])
        self.assertIn("ring", [e["type"] for e in queue.items])


class MissedTests(RingCase):
    def test_it_keeps_ringing_for_a_while(self):
        self.ring()
        announce.check_ring(self.conn)
        self.assertIsNotNone(announce.ringing(self.conn))

    def test_an_unanswered_call_leaves_a_missed_call(self):
        self.ring()
        self.age_the_ring(config.RING_MINUTES + 1)
        announce.check_ring(self.conn)
        self.assertIsNone(announce.ringing(self.conn))
        self.assertEqual(self.history()[-1], announce.MISSED_TEXT)

    def test_her_own_call_is_not_repeated(self):
        self.ring()
        announce.miss(self.conn)
        self.assertEqual(remind.pending(self.conn), [])

    def test_an_asked_call_is_repeated_once(self):
        self.ring(announce.ASKED, remind_text="起こす")
        announce.miss(self.conn)
        rows = remind.pending(self.conn)
        self.assertEqual([(r["text"], r["chain"], r["phone"]) for r in rows], [("起こす", 1, 1)])

    def test_the_repeat_is_not_repeated_again(self):
        self.ring(announce.ASKED, remind_text="起こす", chain=1)
        announce.miss(self.conn)
        self.assertEqual(remind.pending(self.conn), [])

    def test_a_typed_reply_folds_it_quietly(self):
        """出なくても、文字で返事があれば用は済んでいる。"""
        self.ring(announce.ASKED, remind_text="起こす")
        self.age_the_ring(1)
        db.set_state(self.conn, db.LAST_CONVERSATION_AT, db.now_utc())
        announce.check_ring(self.conn)
        self.assertIsNone(announce.ringing(self.conn))
        self.assertNotIn(announce.MISSED_TEXT, self.history())
        self.assertEqual(remind.pending(self.conn), [])


class PickUpTests(RingCase):
    """ことはから掛けた電話は、どう繋がっても、ことはが先に話す。"""

    def test_calling_while_it_rings_answers_it(self):
        self.ring()
        self.assertEqual(announce.pick_up(self.conn), OPENER)
        self.assertIsNone(announce.ringing(self.conn))

    def test_a_plain_call_has_no_first_words(self):
        self.assertEqual(announce.pick_up(self.conn), "")

    def test_calling_back_brings_back_the_topic(self):
        self.ring()
        announce.miss(self.conn)
        text = announce.pick_up(self.conn)
        self.assertEqual(text, announce.CALLBACK_LEAD + OPENER)
        self.assertEqual(self.history()[-1], text)
        self.assertEqual(announce.pick_up(self.conn), "")   # 1回きり

    def test_calling_back_cancels_the_repeat(self):
        self.ring(announce.ASKED, remind_text="起こす")
        announce.miss(self.conn)
        announce.pick_up(self.conn)
        self.assertEqual(remind.pending(self.conn), [])

    def test_after_talking_the_missed_topic_is_old(self):
        self.ring()
        announce.miss(self.conn)
        later = clock.utc_now() + timedelta(seconds=5)
        db.set_state(self.conn, db.LAST_CONVERSATION_AT, later.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(announce.pick_up(self.conn), "")

    def test_a_late_call_back_is_a_plain_call(self):
        self.ring()
        announce.miss(self.conn)
        missed = json.loads(db.get_state(self.conn, db.MISSED_RING))
        then = clock.utc_now() - timedelta(hours=announce.CALLBACK_HOURS + 1)
        missed["at"] = then.strftime("%Y-%m-%dT%H:%M:%SZ")
        db.set_state(self.conn, db.MISSED_RING, json.dumps(missed))
        self.assertEqual(announce.pick_up(self.conn), "")


class TriggerTests(RingCase):
    def setUp(self):
        super().setUp()
        for name, value in (("REACH_OUT_ENABLED", True), ("REACH_OUT_AFTER_HOURS", 5),
                            ("REACH_OUT_INTERVAL_HOURS", 6), ("REACH_OUT_FROM_HOUR", 0),
                            ("REACH_OUT_TO_HOUR", 24), ("REACH_CALL_ENABLED", True),
                            ("REACH_CALL_SHARE", 1.0), ("NOTIFY_GAP_MINUTES", 0)):
            self.addCleanup(setattr, config, name, getattr(config, name))
            setattr(config, name, value)

    def quiet(self):
        db.set_state(self.conn, db.LAST_CONVERSATION_AT, "2000-01-01T00:00:00Z")
        db.set_state(self.conn, db.LAST_REACH_OUT_AT, "")
        self.conn.commit()

    def test_reaching_out_can_be_a_call_that_brings_a_topic(self):
        self.quiet()
        jobs.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, [announce.RING_TITLE])
        self.assertIn("話題を1つ持ってくる", self.closings[0])

    def test_only_one_call_a_day(self):
        self.quiet()
        jobs.maybe_reach_out(self.conn)
        announce.answer(self.conn, announce.ringing(self.conn)["id"])
        self.quiet()
        jobs.maybe_reach_out(self.conn)
        self.assertEqual(self.pushed, [announce.RING_TITLE, OPENER])

    def test_a_phone_reminder_rings_with_its_errand(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "起こす", phone=True)
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, [announce.RING_TITLE])
        self.assertIn("「起こす」", self.closings[0])
        self.assertEqual(announce.ringing(self.conn)["remind_text"], "起こす")
        self.assertEqual(remind.pending(self.conn), [])

    def test_a_plain_reminder_is_still_said(self):
        remind.add(self.conn, datetime.now() - timedelta(minutes=1), "歯医者")
        self.conn.commit()
        jobs.maybe_reminders(self.conn)
        self.assertEqual(self.pushed, [OPENER])
        self.assertIsNone(announce.ringing(self.conn))


class RingApiTests(RingCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        self.addCleanup(setattr, config, "WEB_TOKEN", config.WEB_TOKEN)
        config.WEB_TOKEN = "testtoken"
        self.client = TestClient(web.app)
        self.headers = {"X-Kotoha-Token": "testtoken"}

    def test_the_screen_learns_of_the_ring_without_the_words(self):
        self.ring()
        ring = self.client.get("/api/ring", headers=self.headers).json()["ring"]
        self.assertNotIn("text", ring)
        answer = self.client.post("/api/ring/answer", json={"id": ring["id"]},
                                  headers=self.headers)
        self.assertEqual(answer.json(), {"text": OPENER})

    def test_answering_too_late_is_gone(self):
        self.ring()
        ring_id = announce.ringing(self.conn)["id"]
        announce.miss(self.conn)
        answer = self.client.post("/api/ring/answer", json={"id": ring_id}, headers=self.headers)
        self.assertEqual(answer.status_code, 410)

    def test_picking_up_hands_over_the_first_words(self):
        self.ring()
        self.conn.commit()
        answer = self.client.post("/api/ring/pickup", json={}, headers=self.headers)
        self.assertEqual(answer.json(), {"text": OPENER})

    def test_later_counts_as_missed(self):
        self.ring(announce.ASKED, remind_text="起こす")
        ring_id = announce.ringing(self.conn)["id"]
        answer = self.client.post("/api/ring/decline", json={"id": ring_id}, headers=self.headers)
        self.assertEqual(answer.json(), {"missed": True})
        self.conn.rollback()
        self.assertEqual(len(remind.pending(self.conn)), 1)


if __name__ == "__main__":
    unittest.main()
