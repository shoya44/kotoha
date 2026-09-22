"""固定した器の移動、額縁と通知、静かな同席の境界。"""
from datetime import datetime
from unittest.mock import patch

from tests.support import Clock, use_temp_db

_TMP = use_temp_db("living")
from tests.test_hub import HubCase  # noqa: E402
from kotoha.memory import db, vessels  # noqa: E402
from kotoha.serve import hub, announce, jobs  # noqa: E402
from kotoha.talk import living  # noqa: E402
from kotoha import clock, config  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class LivingTests(HubCase):
    def setup_home(self):
        vessels.save(self.conn, "web-tab", {"kind": "tablet", "label": "居間", "frame": True})
        vessels.save(self.conn, "web-phone", {"kind": "iphone", "label": "iPhone"})
        self.conn.commit()
        self.connect("desktop")
        self.connect("web-tab")
        self.connect("web-phone")

    def test_moves_only_between_home_vessels_and_leaves_note(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            timer.advance(minutes=31)
            hub.activity("web-tab")
            self.assertTrue(hub.maybe_move(self.conn))
            self.assertEqual(hub.body(), "web-tab")
            self.assertIn("居間", db.get_state(self.conn, db.VESSEL_NOTE_PREFIX + "desktop"))
            self.assertFalse(hub.maybe_move(self.conn))
            timer.advance(hours=13)
            self.assertTrue(hub.maybe_move(self.conn))
            self.assertEqual(hub.body(), "desktop")
            self.assertEqual(db.get_state(self.conn, db.VESSEL_NOTE_PREFIX + "desktop"), "")

    def test_phone_never_moves_automatically(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            hub.claim("web-phone")
            timer.advance(hours=2)
            hub.activity("web-tab")
            self.assertFalse(hub.maybe_move(self.conn))
            self.assertEqual(hub.body(), "web-phone")

    def test_sleepy_mood_returns_to_pc_without_waiting_for_sleep_hour(self):
        with Clock(datetime(2026, 9, 22, 22)) as timer:
            self.setup_home()
            hub.claim("web-tab")
            timer.advance(minutes=31)
            db.set_state(self.conn, db.MOOD, "眠い")
            db.set_state(self.conn, db.MOOD_AT, clock.utc())
            self.conn.commit()
            self.assertTrue(hub.maybe_move(self.conn))
            self.assertEqual(hub.body(), "desktop")
            self.assertEqual(hub.snapshot()["reason"], "眠いので寝床へ")

    def test_stale_target_busy_call_quiet_and_recent_claim_prevent_move(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            timer.advance(minutes=31)
            self.assertFalse(hub.maybe_move(self.conn))
            hub.activity("web-tab")
            hub.begin_turn()
            self.assertFalse(hub.maybe_move(self.conn))
            hub.end_turn()
            hub.start_call("desktop")
            self.assertFalse(hub.maybe_move(self.conn))
            hub.end_call()
            self.assertFalse(hub.maybe_move(self.conn))
            timer.advance(minutes=31)
            hub.activity("web-tab")
            living.set_quiet(self.conn, True)
            self.assertFalse(hub.maybe_move(self.conn))

    def test_frame_display_is_not_human_attention_and_still_shows_message(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            hub.claim("web-tab")
            with patch.object(announce.notify, "push", return_value=True) as push, patch.object(config, "PUSH_WHEN_EMBODIED", False):
                self.assertFalse(announce.reaches_the_person())
                self.assertTrue(announce._deliver("頼まれた通知", []))
                push.assert_called_once()
                self.assertTrue(any(e.get("text") == "頼まれた通知" for e in self.events("web-tab")))
            hub.activity("web-tab", True)
            self.assertTrue(announce.reaches_the_person())
            timer.advance(minutes=3)
            hub.activity("web-tab", False)
            self.assertFalse(announce.reaches_the_person())

    def test_quiet_is_bounded_and_does_not_stop_reminders(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            living.accept(self.conn, "少し作業するね。")
            self.assertTrue(living.quiet(self.conn))
            with patch.object(jobs, "announce") as say:
                jobs.maybe_reach_out(self.conn)
                jobs.maybe_afterthought(self.conn)
                jobs.maybe_lookout(self.conn)
                say.assert_not_called()
            with patch.object(announce, "can_speak", return_value=True), patch.object(announce, "_say", return_value="届いた") as say:
                self.assertEqual(announce.announce(self.conn, "雑談", casual=True), "")
                with patch.object(announce, "_too_soon", return_value=False):
                    self.assertEqual(announce.announce(self.conn, "頼まれごと", chain=0), "届いた")
                say.assert_called_once()
            timer.advance(minutes=61)
            self.assertFalse(living.quiet(self.conn))

    def test_quiet_discards_held_casual_but_preserves_reminder(self):
        living.set_quiet(self.conn, True)
        announce._put_held(self.conn, [{"closing": "雑談", "casual": True}, {"closing": "予定", "chain": 0}])
        self.conn.commit()
        with patch.object(announce, "can_speak", return_value=True), patch.object(announce, "_say", return_value="予定") as say:
            announce.flush_held(self.conn)
            self.assertEqual(say.call_args.args[1], [{"closing": "予定", "chain": 0}])
        living.accept(self.conn, "終わった")
        self.assertFalse(living.quiet(self.conn))
        self.assertEqual(announce._held(self.conn), [])

    def test_recent_conversation_prevents_move(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            timer.advance(minutes=31)
            hub.activity("web-tab")
            db.set_state(self.conn, db.LAST_CONVERSATION_AT, clock.utc())
            self.conn.commit()
            self.assertFalse(hub.maybe_move(self.conn))

    def test_daytime_sleepy_report_does_not_send_her_to_bed(self):
        with Clock(datetime(2026, 9, 22, 14)) as timer:
            self.setup_home()
            hub.claim("web-tab")
            timer.advance(minutes=31)
            db.set_state(self.conn, db.MOOD, "眠い")
            db.set_state(self.conn, db.MOOD_AT, clock.utc())
            self.conn.commit()
            self.assertFalse(hub.maybe_move(self.conn))
            self.assertEqual(hub.body(), "web-tab")
