import sys

from . import config
from .memory import consolidate, db
from .talk import chat, llm


def _status(conn) -> None:
    print(f"DB: {config.DB_PATH}")
    print(f"モデル: {config.GEMINI_MODEL}")
    print(f"メッセージ数: {db.message_count(conn)}")
    print(f"前回会話: {db.get_state(conn, 'last_conversation_at') or '-'}")
    print(f"最終整理: {db.get_state(conn, 'last_consolidation_at') or '-'}")
    print(f"最終忘却: {db.get_state(conn, 'last_forget_at') or '-'}")


def _backup() -> None:
    dest = db.run_backup()
    if dest is None:
        print("DBが見つかりません。")
        return
    print(f"バックアップ完了: {dest}（最新{config.BACKUP_KEEP}件を保持）")


def _unprocessed_turns(conn) -> int:
    last = int(db.get_state(conn, "last_processed_message_id", "0") or 0)
    row = conn.execute(
        "SELECT COUNT(DISTINCT turn_id) AS n FROM messages WHERE id > ? AND extractable = 1",
        (last,),
    ).fetchone()
    return row["n"]


def _maybe_consolidate(conn, force: bool = False) -> None:
    if not force and _unprocessed_turns(conn) < config.CONSOLIDATE_TURNS:
        return
    try:
        print(consolidate.run(conn))
    except llm.LLMError as e:
        print(f"[整理失敗] {e}（次回再試行します）")


def _memory(conn, args) -> None:
    kind = args[0] if args else "semantics"
    if kind == "episodes":
        rows = conn.execute(
            "SELECT id, occurred_at, text, pinned FROM memory_nodes "
            "WHERE layer='episode' ORDER BY occurred_at DESC LIMIT 30"
        ).fetchall()
        for r in rows:
            pin = " [保護]" if r["pinned"] else ""
            print(f"[{r['id']}][{r['occurred_at']}] {r['text']}{pin}")
    elif kind == "semantics":
        rows = conn.execute(
            "SELECT id, kind, text, confirmed_at, pinned FROM memory_nodes "
            "WHERE layer='semantic' ORDER BY confirmed_at DESC LIMIT 30"
        ).fetchall()
        for r in rows:
            pin = " [保護]" if r["pinned"] else ""
            print(f"[{r['id']}][{r['kind']}][{r['confirmed_at'][:10]}] {r['text']}{pin}")
    elif kind == "pinned":
        rows = conn.execute(
            "SELECT id, layer, kind, text FROM memory_nodes WHERE pinned=1 ORDER BY id LIMIT 50"
        ).fetchall()
        for r in rows:
            print(f"[{r['id']}][{r['layer']}/{r['kind']}] {r['text']}")
    elif kind == "tags":
        rows = conn.execute(
            "SELECT node_id, tag, use_count FROM memory_tags ORDER BY tag LIMIT 100"
        ).fetchall()
        for r in rows:
            print(f"#{r['tag']} -> node {r['node_id']} (利用 {r['use_count']})")
    elif kind == "show" and len(args) > 1:
        nid = int(args[1])
        r = conn.execute("SELECT * FROM memory_nodes WHERE id=?", (nid,)).fetchone()
        if not r:
            print("見つかりません。")
            return
        print(f"id: {r['id']} / {r['layer']}/{r['kind']} / 保護={r['pinned']}")
        print(f"発生日: {r['occurred_at']} / 確認: {r['confirmed_at']} / 期限: {r['expires_at'] or '-'}")
        print(f"本文: {r['text']}")
        tags = conn.execute("SELECT tag FROM memory_tags WHERE node_id=?", (nid,)).fetchall()
        print("タグ: " + (", ".join(t["tag"] for t in tags) or "-"))
        edges = conn.execute(
            "SELECT relation, from_id, to_id FROM memory_edges WHERE from_id=? OR to_id=?", (nid, nid)
        ).fetchall()
        for e in edges:
            other = e["to_id"] if e["from_id"] == nid else e["from_id"]
            print(f"接続: {e['relation']} -> node {other}")
        srcs = conn.execute("SELECT message_id FROM memory_sources WHERE node_id=?", (nid,)).fetchall()
        print("出典: " + (", ".join(f"msg{s['message_id']}" for s in srcs) or "なし（要約のみ）"))
    else:
        print("使い方: memory [episodes|semantics|pinned|tags|show <id>]")


def _start() -> None:
    config.require_keys()
    conn = db.connect()
    db.init(conn)
    _maybe_consolidate(conn)

    recent = db.fetch_recent(conn, config.RECENT_TURNS, config.RECENT_CHARS)
    if recent:
        print("--- 直近の会話を復元 ---")
        for r in recent[-6:]:
            print(f"{'ユーザー' if r['role'] == 'user' else 'ことは'}: {r['text']}")
        print("------------------------")
    if db.unfinished_turn(conn):
        print("[未完了ターン] 最後の発言に返答がありません。もう一度送って。")
    print("コマンド: /status /memory /consolidate /protect <id> /forget <id> /exit")

    while True:
        try:
            text = input("あなた> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/exit":
            break
        if text == "/status":
            _status(conn)
            continue
        if text == "/consolidate":
            _maybe_consolidate(conn, force=True)
            continue
        if text.startswith("/memory"):
            _memory(conn, text.split()[1:])
            continue
        if text.startswith("/protect"):
            try:
                nid = int(text.split()[1])
                if db.set_pinned(conn, nid, True):
                    print(f"記憶 {nid} を保護しました。")
                else:
                    print("見つかりません。")
            except (IndexError, ValueError):
                print("使い方: /protect <id>")
            continue
        if text.startswith("/forget"):
            try:
                nid = int(text.split()[1])
                if db.forget_node(conn, nid):
                    print(f"記憶 {nid} を完全に削除しました。")
                else:
                    print("見つかりません。")
            except (IndexError, ValueError):
                print("使い方: /forget <id>")
            continue
        try:
            reply, mode = chat.run_turn(conn, text)
        except llm.LLMError as e:
            print(f"[エラー] {e}（返答は未保存 / 再試行: {'可' if e.retryable else '不可'}）")
            continue
        if config.DEBUG:
            print(f"-- mode: {mode} --")
        print(f"ことは> {reply}")
        _maybe_consolidate(conn)

    # 終了時にメンテナンス実行。バックアップは忘却より先に取る。
    if db.seconds_since(db.get_state(conn, "last_backup_at")) > config.BACKUP_INTERVAL_SECONDS:
        try:
            dest = db.run_backup(conn)
            if dest:
                print(f"[バックアップ] {dest.name}")
        except OSError as e:
            print(f"[バックアップ失敗] {e}")
    forgotten = db.run_maintenance(conn)
    if forgotten > 0:
        print(f"[忘却] {forgotten} 件の古い記憶を削除しました。")
    conn.close()


def main(argv) -> None:
    cmd = argv[1] if len(argv) > 1 else "start"
    if cmd == "init":
        conn = db.connect()
        db.init(conn)
        conn.close()
        print(f"DB初期化完了: {config.DB_PATH}")
    elif cmd == "status":
        conn = db.connect()
        db.init(conn)
        _status(conn)
        conn.close()
    elif cmd == "test-llm":
        config.require_keys()
        print(llm.chat("「私はことは。一声かけて」と1行だけで返して。"))
    elif cmd == "memory":
        conn = db.connect()
        db.init(conn)
        _memory(conn, argv[2:])
        conn.close()
    elif cmd == "consolidate":
        config.require_keys()
        conn = db.connect()
        db.init(conn)
        _maybe_consolidate(conn, force=True)
        conn.close()
    elif cmd == "start":
        _start()
    elif cmd == "web":
        config.require_keys()
        if not config.WEB_TOKEN:
            raise SystemExit(".env に KOTOHA_WEB_TOKEN を設定して。")
        conn = db.connect()
        db.init(conn)
        conn.close()
        import uvicorn
        from .serve.web import app
        print(f"http://{config.WEB_HOST}:{config.WEB_PORT} で起動")
        uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT,
                    log_level="info" if config.DEBUG else "warning", access_log=config.DEBUG)
    elif cmd == "tailscale":
        from .launcher import start_tailscale
        start_tailscale()
    elif cmd == "backup":
        _backup()
    else:
        print(f"未知のコマンド: {cmd}")
        sys.exit(1)
