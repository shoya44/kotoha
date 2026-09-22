"""実際の発言・承認・期限・取消を持つ、小さな成長。外部LLMは呼ばない。"""
import json
from datetime import datetime
from unittest.mock import patch

from tests.support import DbCase, Clock, use_temp_db

_TMP = use_temp_db("growth")
from kotoha.memory import db, growth, habits  # noqa: E402
from kotoha.talk import chat, living  # noqa: E402
from kotoha.serve import web  # noqa: E402
from kotoha import config  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


class GrowthTests(DbCase):
    def setUp(self):
        super().setUp()
        self.timer = self.enterContext(Clock(datetime(2026, 9, 20, 9)))
        self.sources = []
        for text in ("普段は短い返事が好きだよ", "今日も短い返事で話してほしい"):
            turn = db.start_turn(self.conn, "user", text)
            row = self.conn.execute("SELECT id FROM messages WHERE turn_id = ?", (turn,)).fetchone()
            self.sources.append({"id": row["id"], "quote": text})
            db.insert_message(self.conn, turn, "assistant", "うん。")
            self.timer.advance(days=1)
        self.conn.commit()
        self.spec = {"option": "short_reply", "observation": "短く話すのも合ってる気がする。", "evidence": self.sources}

    def offered(self):
        self.assertTrue(growth.propose(self.conn, self.spec))
        turn = db.start_turn(self.conn, "user", "おはよう")
        done = chat._finish(self.conn, turn, "おはよ。", [], "slow")
        self.assertIn("試してみよっか", done.reply)
        return growth.load(self.conn)["pending"]["id"]

    def test_neither_candidate_nor_offer_applies_change(self):
        identity = self.offered()
        self.assertEqual(growth.block(self.conn), "")
        self.assertEqual(growth.public(self.conn)["id"], identity)

    def test_exact_followup_applies_without_another_llm_call(self):
        self.offered()
        with patch.object(chat.llm, "chat", side_effect=AssertionError("LLM must not run")):
            reply = chat.run_turn(self.conn, "そうだね。")
        self.assertEqual(reply.mode, "local")
        self.assertIn("少し短め", growth.block(self.conn))
        self.assertIsNone(growth.load(self.conn)["pending"])

    def test_unrelated_agreement_and_ambiguous_text_do_not_apply(self):
        self.offered()
        self.assertEqual(growth.respond(self.conn, "そうだね。でも今のままでもいいかな"), "")
        turn = db.start_turn(self.conn, "user", "天気の話をしよう")
        db.insert_message(self.conn, turn, "assistant", "晴れだね")
        self.assertEqual(growth.respond(self.conn, "そうだね"), "")
        self.assertEqual(growth.block(self.conn), "")

    def test_stale_id_double_click_and_unoffered_proposal_rejected(self):
        growth.propose(self.conn, self.spec)
        identity = growth.load(self.conn)["pending"]["id"]
        with self.assertRaises(ValueError):
            growth.apply(self.conn, identity, "try")
        growth.mark_offered(self.conn, 42)
        with self.assertRaises(ValueError):
            growth.apply(self.conn, "wrong-id", "try")
        growth.apply(self.conn, identity, "try")
        with self.assertRaises(ValueError):
            growth.apply(self.conn, identity, "try")

    def test_trial_expires_and_keep_can_be_undone(self):
        identity = self.offered()
        growth.apply(self.conn, identity, "try")
        self.timer.advance(days=8)
        self.assertEqual(growth.block(self.conn), "")
        self.assertIn("試用が終わり", growth.public(self.conn)["text"])
        growth.apply(self.conn, identity, "keep")
        self.assertIn("少し短め", growth.block(self.conn))
        growth.apply(self.conn, identity, "reset")
        self.assertEqual(growth.block(self.conn), "")

    def test_undo_during_trial_restores_prior_state(self):
        identity = self.offered()
        growth.apply(self.conn, identity, "try")
        growth.apply(self.conn, identity, "undo")
        self.assertEqual(growth.block(self.conn), "")

    def test_saved_change_remains_reversible_after_audit_history_rolls_over(self):
        identity = self.offered()
        growth.apply(self.conn, identity, "try")
        growth.apply(self.conn, identity, "keep")
        state = growth.load(self.conn)
        state["history"] = []
        growth.save(self.conn, state)
        self.assertEqual(growth.public(self.conn)["id"], identity)
        growth.apply(self.conn, identity, "reset")
        self.assertEqual(growth.block(self.conn), "")

    def test_bad_sources_unknown_option_and_same_day_are_rejected(self):
        self.assertFalse(growth.propose(self.conn, dict(self.spec, option=[])))
        self.assertFalse(growth.propose(self.conn, dict(self.spec, option="rewrite_persona")))
        self.assertFalse(growth.propose(self.conn, dict(self.spec, evidence=[self.sources[0]] * 2)))
        fake = [dict(source, quote="話していないこと") for source in self.sources]
        self.assertFalse(growth.propose(self.conn, dict(self.spec, evidence=fake)))
        fake = [dict(source, id=source["id"] + 1) for source in self.sources]
        self.assertFalse(growth.propose(self.conn, dict(self.spec, evidence=fake)))

    def test_optional_malformed_or_truncated_growth_does_not_break_habits(self):
        for raw in ('{"habits":[{"text":"朝に話す"}],"growth":{"option":[]}}',
                    '{"habits":[{"text":"朝に話す"}],"growth":{"option":'):
            with patch.object(habits.llm, "chat", return_value=raw):
                self.assertGreater(habits.reflect(self.conn), 0)
        self.assertIsNone(growth.load(self.conn)["pending"])

    def test_stream_delivers_offer_in_rest_and_accepts_locally(self):
        growth.propose(self.conn, self.spec)
        with patch.object(chat.llm, "stream", return_value=iter(["おはよ。"])), \
                patch.object(chat.retrieve, "retrieve_all", return_value=([], [], [])):
            parts = list(chat.stream_turn(self.conn, "おはよう"))
        self.assertIn("試してみよっか", parts[-1]["rest"])
        with patch.object(chat.llm, "stream", side_effect=AssertionError("LLM must not run")):
            accepted = list(chat.stream_turn(self.conn, "試してみて"))
        self.assertEqual(accepted[-1]["done"].mode, "local")

    def test_quiet_defers_proposal_and_expired_proposal_never_applies(self):
        growth.propose(self.conn, self.spec)
        living.set_quiet(self.conn, True)
        self.assertEqual(growth.offer(self.conn, "返事"), ("返事", False))
        self.timer.advance(days=8)
        self.assertIsNone(growth.public(self.conn))
        self.assertEqual(growth.block(self.conn), "")

    def test_api_requires_auth_and_preserves_record(self):
        from fastapi.testclient import TestClient
        identity = self.offered()
        with patch.object(config, "WEB_TOKEN", "testtoken"):
            client = TestClient(web.app)
            payload = {"id": identity, "action": "try"}
            self.assertEqual(client.post("/api/living/growth", json=payload).status_code, 401)
            headers = {"X-Kotoha-Token": "testtoken"}
            self.assertEqual(client.post("/api/living/growth", json=payload, headers=headers).status_code, 200)
            self.assertEqual(client.post("/api/living/growth", json=payload, headers=headers).status_code, 409)
        self.assertIn("approved", json.dumps(growth.load(self.conn)))

    def test_saved_change_can_be_reset_over_api_and_history_names_it(self):
        from fastapi.testclient import TestClient
        identity = self.offered()
        growth.apply(self.conn, identity, "try")
        growth.apply(self.conn, identity, "keep")
        self.assertIn("少し短め", growth.block(self.conn))
        # 別の候補が保留になっていても、取り消しの履歴はその候補と混ざらない。
        state = growth.load(self.conn)
        state["pending"] = {"id": "other", "option": "morning_bright", "observation": "x", "evidence": [],
                            "created": "2026-09-22T00:00:00Z", "expires": "2099-01-01T00:00:00Z", "offered": 0}
        growth.save(self.conn, state)
        self.conn.commit()
        with patch.object(config, "WEB_TOKEN", "testtoken"):
            client = TestClient(web.app)
            headers = {"X-Kotoha-Token": "testtoken"}
            answer = client.post("/api/living/growth", json={"id": identity, "action": "reset"}, headers=headers)
            self.assertEqual(answer.status_code, 200)
            self.assertIn("戻したよ", answer.json()["reply"])
            self.assertEqual(client.post("/api/living/growth", json={"id": identity, "action": "reset"},
                                         headers=headers).status_code, 409)
        self.assertEqual(growth.block(self.conn), "")
        state = growth.load(self.conn)
        self.assertEqual(state["saved"], {})
        self.assertEqual(state["history"][-1]["proposal"]["id"], identity)
        self.assertEqual(state["history"][-1]["action"], "reset")
        self.assertEqual(state["pending"]["id"], "other")
