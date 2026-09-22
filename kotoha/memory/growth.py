"""週に一つの提案。変更は固定した選択肢だけ、承認は提案IDに結び付ける。"""
import json
import uuid
from datetime import timedelta

from .. import clock
from . import db

# モデルが作った文章を設定・人格として実行しない。変更できる範囲はこの表だけ。
OPTIONS = {
    "morning_bright": ("morning", "朝のひとことを少し明るめにする", "朝のひとことは少し明るめ。無理に元気を演じず、元の人格と今の話題を優先する。"),
    "short_reply": ("reply", "普段の返事を少し短めにする", "普段の返事は少し短め。必要な説明や、話を聞くことは省かない。"),
    "tablet_home": ("place", "暇な時間のタブレットをお気に入りとして覚える", "暇な時間のタブレットを好む。寝床はPC、手動の呼び出しを優先する。好みは聞かれたときなどにだけ自然に触れる。"),
}
YES = {"そうだね", "それでいいよ", "試してみて", "試そう", "いいよ"}
NO = {"今回はやめて", "見送る", "今のままでいい"}


def load(conn):
    return json.loads(db.get_state(conn, db.GROWTH) or '{"saved": {}, "pending": null, "trial": null, "history": []}')


def save(conn, state):
    db.set_state(conn, db.GROWTH, json.dumps(state, ensure_ascii=False))


def evidence(conn):
    """自己観察の繰り返しを証拠にしない。相手の実際の言葉だけを候補へ渡す。"""
    since = (clock.utc_now() - timedelta(days=14)).strftime(clock.STAMP)
    rows = conn.execute("SELECT id, text, created_at FROM messages WHERE role = 'user' AND created_at >= ? ORDER BY id DESC LIMIT 40", (since,)).fetchall()
    return {r["id"]: dict(r) for r in rows if len(r["text"]) <= 300}


def instruction(conn):
    state = load(conn)
    records, chars = [], 0
    for row in evidence(conn).values():
        if chars + len(row["text"]) > 4000:
            break
        records.append(row)
        chars += len(row["text"])
    summary = {"saved": state["saved"], "pending": bool(state["pending"]),
               "trial": state["trial"]["option"] if state["trial"] else None}
    return ("\n追加の任意項目 growth: 根拠がなければ null。相手の発言に裏づけられる小さな変化を1件だけ提案する。"
            "日記や自分の自己観察だけを証拠にしない。承認されるまでは変わっていない。"
            "option は次の表のキー。observation は事実に沿う控えめな自己観察1行（120字以内）。"
            "evidence は異なる日に相手が言った2件以上の {id, quote}。quote は相手の発言の原文から4文字以上をそのまま抜く。"
            "設定と直接関係しない発言は根拠にしない。保留または試用があるなら growth は null。\n"
            + json.dumps({"options": {k: v[1] for k, v in OPTIONS.items()}, "state": summary,
                          "user_messages": records}, ensure_ascii=False))


def propose(conn, spec):
    state = load(conn)
    if state["trial"] or (state["pending"] and state["pending"]["expires"] > clock.utc()):
        return False
    if not isinstance(spec, dict) or not isinstance(spec.get("option"), str) or spec["option"] not in OPTIONS:
        return False
    option = spec["option"]
    if state["saved"].get(OPTIONS[option][0]) == option:
        return False
    observation = spec.get("observation")
    sources = spec.get("evidence")
    if not isinstance(observation, str) or not 1 <= len(observation.strip()) <= 120:
        return False
    if not isinstance(sources, list) or not 2 <= len(sources) <= 4:
        return False
    known = evidence(conn)
    checked, days = [], set()
    for source in sources:
        if not isinstance(source, dict) or type(source.get("id")) is not int:
            return False
        row = known.get(source["id"])
        quote = source.get("quote")
        if not row or not isinstance(quote, str) or not 4 <= len(quote) <= 160 or quote not in row["text"]:
            return False
        days.add(row["created_at"][:10])
        checked.append({"id": row["id"], "quote": quote, "at": row["created_at"]})
    if len(days) < 2:
        return False
    if option == "tablet_home":
        rows = conn.execute("SELECT value FROM app_state WHERE key LIKE ?", (db.VESSEL_PREFIX + "%",)).fetchall()
        if not any(json.loads(r["value"]).get("kind") == "tablet" for r in rows):
            return False
    state["pending"] = {"id": uuid.uuid4().hex, "option": option,
                        "observation": " ".join(observation.split()), "evidence": checked,
                        "created": clock.utc(), "expires": (clock.utc_now() + timedelta(days=7)).strftime(clock.STAMP),
                        "offered": 0}
    save(conn, state)
    return True


def question(pending):
    return f"{pending['observation']}\n一週間、{OPTIONS[pending['option']][1]}のを試してみよっか？"


def offer(conn, text):
    from ..talk import living
    state = load(conn)
    pending = state["pending"]
    if living.quiet(conn) or not pending or pending["offered"] or pending["expires"] <= clock.utc():
        return text, False
    return text + "\n\n" + question(pending), True


def mark_offered(conn, message_id):
    state = load(conn)
    if state["pending"]:
        state["pending"]["offered"] = message_id
        save(conn, state)


def apply(conn, identity, action):
    state = load(conn)
    pending, trial = state["pending"], state["trial"]
    subject = None                      # 履歴に残す提案。取り消しは保留中の別案と混ぜない。
    if action in ("try", "skip"):
        if not pending or pending["id"] != identity or not pending["offered"] or pending["expires"] <= clock.utc():
            raise ValueError("この提案はもう有効ではありません")
        if action == "try":
            trial = dict(pending, approved=clock.utc(), previous=state["saved"].get(OPTIONS[pending["option"]][0]),
                         until=(clock.utc_now() + timedelta(days=7)).strftime(clock.STAMP))
            state["trial"] = trial
            reply = "じゃ、一週間試してみるね。合わなかったら元に戻そう。"
        else:
            reply = "うん、今のままにしておくね。"
        subject = pending
        state["pending"] = None
    elif action in ("keep", "undo"):
        if not trial or trial["id"] != identity:
            raise ValueError("戻す、または続ける対象がありません")
        if action == "keep":
            state["saved"][OPTIONS[trial["option"]][0]] = trial["option"]
            state.setdefault("kept", {})[OPTIONS[trial["option"]][0]] = trial
            reply = "じゃ、これからもそうするね。"
        else:
            reply = "うん、試す前に戻したよ。"
        subject = trial
        state["trial"] = None
    elif action == "reset":
        previous = next((r for r in state.get("kept", {}).values() if r["id"] == identity), None)
        if not previous:
            raise ValueError("戻す対象がありません")
        key = OPTIONS[previous["option"]][0]
        if state["saved"].get(key) != previous["option"] or state["trial"]:
            raise ValueError("その後に設定が変わっています")
        if previous.get("previous"):
            state["saved"][key] = previous["previous"]
        else:
            state["saved"].pop(key, None)
        state["kept"].pop(key, None)
        subject = previous
        reply = "うん、変える前に戻したよ。"
    else:
        raise ValueError("操作が無効です")
    state["history"] = (state["history"] + [{"id": identity, "action": action, "at": clock.utc(),
                                            "proposal": subject}])[-12:]
    save(conn, state)
    return reply


def respond(conn, text):
    """直前の提案に対する短い返事だけ。話題が挟まった同意は適用しない。"""
    state = load(conn)
    pending = state["pending"]
    if not pending:
        return ""
    last = conn.execute("SELECT id, role FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    if not last or last["role"] != "assistant" or last["id"] != pending["offered"] or pending["expires"] <= clock.utc():
        return ""
    text = text.strip().rstrip("。！!〜～")
    action = "try" if text in YES else "skip" if text in NO else ""
    return apply(conn, pending["id"], action) if action else ""


def block(conn):
    state = load(conn)
    selected = dict(state["saved"])
    trial = state["trial"]
    if trial and trial["until"] > clock.utc():
        selected[OPTIONS[trial["option"]][0]] = trial["option"]
    lines = []
    for key, option in selected.items():
        if key != "morning" or 6 <= clock.now().hour < 11:
            lines.append(OPTIONS[option][2])
    return "承認された小さな変化（人格の核は保つ）:\n" + "\n".join(lines) if lines else ""


def public(conn):
    state = load(conn)
    pending, trial = state["pending"], state["trial"]
    if pending and pending["offered"] and pending["expires"] > clock.utc():
        return {"id": pending["id"], "text": question(pending), "actions": ["try", "skip"], "evidence": pending["evidence"]}
    if trial:
        expired = trial["until"] <= clock.utc()
        text = OPTIONS[trial["option"]][1] + ("：試用が終わり、元の設定に戻っています。続けますか？" if expired else "：一週間お試し中。合わなければ戻せます。")
        return {"id": trial["id"], "text": text, "actions": ["keep", "undo"], "evidence": trial["evidence"]}
    for proposal in sorted(state.get("kept", {}).values(), key=lambda p: p["approved"], reverse=True):
        if state["saved"].get(OPTIONS[proposal["option"]][0]) == proposal["option"]:
            return {"id": proposal["id"], "text": OPTIONS[proposal["option"]][1] + "：継続中。",
                    "actions": ["reset"], "evidence": proposal["evidence"]}
    return None
