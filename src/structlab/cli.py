"""コマンドライン: structlab <command>

  init         設定ファイル config.yaml を作成
  check        実行環境・接続・設定を確認（元資料は読むだけ）
  scan         ファイル一覧の取得と新規・更新・削除の検出だけを行う
  run          スキャン → 解析 → 結果出力（途中停止しても次回続きから）
  status       処理状況を表示
  report       解析結果（Markdown/JSON）を再生成
  retry        失敗した部分を再試行対象に戻す
  reanalyze    指定ファイルを強制的に再解析
  errors       未解決のエラー一覧
  estimate     AI解析（有料）を始める前の呼び出し回数・費用の概算（APIは呼ばない）
  search       指示・場面の理解をキーワード検索
  show-directive 指示1件の根拠・前後の文脈・関連・照合結果を表示
  dropbox-auth Dropbox のリフレッシュトークン取得（PKCE・読み取り専用。値は表示しない）
  connect-check Dropbox・Claude API の接続確認（読み取りのみ・課金なし）
  read         資料の読み取り基盤（棚卸し・場面分割・OCR・文字起こし・AIの書き起こし・照合）
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .config import ConfigError, load_config
from .db import DB

log = logging.getLogger("structlab")


def _setup_logging(cfg, verbose: bool) -> Path:
    logdir = cfg.work_dir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    log.handlers.clear()
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(sh)
    log.addHandler(fh)
    return logfile


class RunLock:
    """同じ作業ディレクトリで2つの処理が同時に走らないようにする。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w")

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("別の structlab が実行中です（work/state/.lock）。終了を待ってください。")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# ---------------- Dropbox への状態・結果の保存 ----------------
class Syncer:
    def __init__(self, cfg, db: DB | None = None):
        self.cfg = cfg
        self.db = db
        self.out = None
        self.last = time.time()
        p = cfg.data["output"].get("dropbox_path")
        if p:
            from .sources.dropbox_source import DropboxOutput
            self.out = DropboxOutput(p, cfg.data["source"]["path"])

    def pull_state(self) -> bool:
        if not self.out or not self.cfg.data["output"]["sync_state"] or self.cfg.db_path.exists():
            return False
        ok = self.out.download_if_exists("state/state.db", self.cfg.db_path)
        if ok:
            log.info("Dropbox から前回の処理状況（state.db）を取得しました。続きから再開します。")
        return ok

    def maybe_push(self) -> None:
        if self.out and time.time() - self.last >= self.cfg.data["output"]["sync_interval_minutes"] * 60:
            self.push(results=False)

    def push(self, results: bool = True) -> None:
        if not self.out or self.db is None:
            return
        self.last = time.time()
        tmp = self.cfg.work_dir / "state" / "state_upload.db"
        self.db.backup_to(tmp)
        self.out.upload(tmp, "state/state.db")
        tmp.unlink(missing_ok=True)
        if results:
            n = 0
            for p in sorted(self.cfg.results_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(self.cfg.results_dir).as_posix()
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                if self.db.get_kv(f"synced:{rel}") == h:
                    continue
                self.out.upload(p, f"results/{rel}")
                self.db.set_kv(f"synced:{rel}", h)
                n += 1
            log.info("Dropbox の出力フォルダへ結果を保存しました（更新 %d ファイル）", n)


# ---------------- コマンド ----------------
def cmd_init(args) -> int:
    dst = Path(args.config)
    if dst.exists():
        print(f"{dst} は既にあります")
        return 0
    src = Path(__file__).resolve().parents[2] / "config.example.yaml"
    shutil.copy(src, dst)
    print(f"{dst} を作成しました。source.path などを確認してください。")
    return 0


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    ok = True

    def line(flag: bool, msg: str, required: bool = True) -> None:
        nonlocal ok
        mark = "OK " if flag else ("NG " if required else "-- ")
        if not flag and required:
            ok = False
        print(f"[{mark}] {msg}")

    print(f"設定: {args.config}  作業ディレクトリ: {cfg.work_dir}")
    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}")
    for mod in ("yaml", "pymupdf", "openpyxl", "PIL", "numpy", "dropbox"):
        try:
            __import__(mod)
            line(True, f"ライブラリ {mod}")
        except ImportError:
            line(False, f"ライブラリ {mod} がありません（pip install -r requirements.txt）")
    line(shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None, "ffmpeg/ffprobe（動画処理）")
    from .media import OCR
    ocr = OCR(cfg.data["ocr"]["backend"], cfg.data["ocr"]["languages"])
    line(ocr.available, f"ローカルOCR: {ocr.label}", required=False)
    tb = cfg.data["transcription"]["backend"]
    if tb == "faster_whisper":
        try:
            __import__("faster_whisper")
            line(True, "文字起こし: faster-whisper")
        except ImportError:
            line(False, "文字起こし: faster-whisper がありません（pip install faster-whisper）")
    else:
        line(tb == "mock", f"文字起こし: {tb}" + ("（テスト用）" if tb == "mock" else "（動画の音声は文字起こしされません）"),
             required=False)
    ai = cfg.data["ai"]
    if ai["provider"] == "anthropic":
        allowed = bool(ai["allow_external"]) or args.allow_external_ai
        line(allowed, "外部AI送信の許可（ai.allow_external または --allow-external-ai）"
             + ("" if allowed else " … 許可なし: ローカル抽出のみ行います"), required=False)
        line(bool(os.environ.get("ANTHROPIC_API_KEY")), "環境変数 ANTHROPIC_API_KEY（値は表示しません）",
             required=allowed)
    else:
        line(True, f"AI: {ai['provider']}（外部送信なし）")
    try:
        free = shutil.disk_usage(cfg.work_dir if cfg.work_dir.exists() else cfg.base_dir).free / 1e6
        line(free > cfg.data["limits"]["min_free_disk_mb"], f"作業ディスク空き {free:.0f} MB")
    except OSError as e:
        line(False, f"ディスク確認失敗: {e}")
    if cfg.data["source"]["type"] == "dropbox":
        from .sources.dropbox_source import dropbox_credentials
        has = dropbox_credentials()["usable"]
        line(bool(has), "Dropbox 認証情報（環境変数またはトークンファイル。値は表示しません）")
        if has:
            try:
                from .sources.dropbox_source import DropboxSource
                src = DropboxSource(cfg.data["source"]["path"])
                n = 0
                for _ in src.iter_files():
                    n += 1
                    if n >= 5:
                        break
                line(True, f"Dropbox 接続・フォルダ '{cfg.data['source']['path']}' の読み取り（先頭{n}件を確認）")
            except Exception as e:  # noqa: BLE001
                from .sources.dropbox_source import redact
                line(False, redact(f"Dropbox 接続失敗: {e}"))
    else:
        line(Path(cfg.data["source"]["path"]).is_dir(), f"ローカルフォルダ {cfg.data['source']['path']}")
    print("\n結果: " + ("実行可能です" if ok else "NG の項目を解消してください（docs/TROUBLESHOOTING.md）"))
    return 0 if ok else 1


def _open_db(cfg) -> DB:
    return DB(cfg.db_path)


def cmd_scan(args) -> int:
    cfg = load_config(args.config)
    _setup_logging(cfg, args.verbose)
    from .scanner import scan
    from .sources import make_source
    with RunLock(cfg.work_dir / "state" / ".lock"):
        syncer = Syncer(cfg)
        syncer.pull_state()
        db = _open_db(cfg)
        res = scan(db, make_source(cfg), cfg)
        from .report import write_reports
        write_reports(db, cfg)
        syncer.db = db
        syncer.push()
    print(json.dumps(res.summary(), ensure_ascii=False))
    for k in ("new", "changed", "deleted", "waiting"):
        for p in getattr(res, k)[:50]:
            print(f"  {k}: {p}")
    return 0


def run_once(cfg, args) -> dict:
    from .ai import make_provider
    from .media import OCR, make_transcriber
    from .pipeline import Pipeline
    from .report import write_reports
    from .scanner import scan
    from .sources import make_source

    syncer = Syncer(cfg)
    syncer.pull_state()
    db = _open_db(cfg)
    syncer.db = db
    recovered = db.recover_interrupted()
    if recovered:
        log.info("前回中断した %d 件を再開対象に戻しました", recovered)
    run_id = db.start_run()
    status = "ok"
    stats: dict = {}
    try:
        source = make_source(cfg)
        res = scan(db, source, cfg)
        log.info("スキャン: %s", res.summary())
        provider, note = make_provider(cfg, allow_external_flag=args.allow_external_ai)
        log.info("AI: %s", note if provider is None else f"{provider.name} / {provider.model}")
        transcriber = make_transcriber(cfg)
        ocr = OCR(cfg.data["ocr"]["backend"], cfg.data["ocr"]["languages"])
        pipe = Pipeline(cfg, db, source, provider=provider, provider_note=note, transcriber=transcriber, ocr=ocr,
                        run_id=run_id, on_progress=syncer.maybe_push)
        pipe.install_signal_handlers()
        stats = pipe.run()
        stats["scan"] = res.summary()
        if stats.get("stop_reason"):
            status = "stopped"
    except Exception as e:  # noqa: BLE001
        status = "error"
        stats["error"] = str(e)
        db.log_error(run_id=run_id, stage="run", message=str(e))
        log.exception("実行が異常終了しました: %s", e)
    finally:
        db.end_run(run_id, status, stats)
        try:
            write_reports(db, cfg)
            syncer.push()
        except Exception as e:  # noqa: BLE001
            log.error("結果の出力・保存に失敗: %s", e)
        db.close()
    stats["status"] = status
    return stats


def cmd_run(args) -> int:
    overrides: dict = {}
    if args.max_runtime is not None:
        overrides.setdefault("limits", {})["max_runtime_minutes"] = args.max_runtime
    cfg = load_config(args.config, overrides)
    logfile = _setup_logging(cfg, args.verbose)
    log.info("ログ: %s", logfile)
    with RunLock(cfg.work_dir / "state" / ".lock"):
        while True:
            stats = run_once(cfg, args)
            print(json.dumps(stats, ensure_ascii=False, default=str))
            if not args.watch or stats.get("stop_reason") or stats["status"] == "error":
                break
            log.info("%d 分後に再スキャンします（Ctrl+C で終了）", args.watch)
            try:
                time.sleep(args.watch * 60)
            except KeyboardInterrupt:
                break
    print(f"\n結果: {cfg.results_dir / 'knowledge.md'}\n状況: {cfg.results_dir / 'status.md'}")
    return 0 if stats["status"] != "error" else 1


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    if not cfg.db_path.exists():
        Syncer(cfg).pull_state()
    if not cfg.db_path.exists():
        print("まだ一度も実行されていません（structlab run）")
        return 0
    from .report import collect_status, status_markdown
    db = _open_db(cfg)
    st = collect_status(db, cfg)
    print(json.dumps(st, ensure_ascii=False, indent=1, default=str) if args.json else status_markdown(st))
    return 0


def cmd_report(args) -> int:
    cfg = load_config(args.config)
    from .report import write_reports
    db = _open_db(cfg)
    paths = write_reports(db, cfg)
    for k, p in paths.items():
        print(f"{k}: {p}")
    return 0


def cmd_retry(args) -> int:
    cfg = load_config(args.config)
    db = _open_db(cfg)
    where, params = "", []
    if args.file:
        where, params = " AND file_id IN (SELECT id FROM files WHERE path LIKE ?)", [f"%{args.file}%"]
    with db.tx():
        n1 = db.x(f"UPDATE units SET status='pending', attempts=0 WHERE status='failed'{where}", params).rowcount
        n2 = db.x(f"UPDATE units SET ai_status='pending', ai_attempts=0 WHERE ai_status IN ('failed','failed_final',"
                  f"'budget_exceeded'){where}", params).rowcount
        fw = " AND path LIKE ?" if args.file else ""
        n3 = db.x(f"UPDATE files SET status='ready', attempts=0 WHERE status IN ('failed','partial'){fw}", params).rowcount
        db.x(f"UPDATE files SET status='ready' WHERE id IN (SELECT file_id FROM units WHERE status='pending'{where})"
             " AND status IN ('done','partial')", params)
        db.x("UPDATE errors SET resolved=1 WHERE resolved=0")
    print(f"再試行対象に戻しました: 単位 {n1} / AI解析 {n2} / ファイル {n3}。`structlab run` で実行してください。")
    return 0


def cmd_reanalyze(args) -> int:
    cfg = load_config(args.config)
    db = _open_db(cfg)
    rows = db.q("SELECT id, path FROM files WHERE path LIKE ? AND status!='deleted'", (f"%{args.path}%",))
    with db.tx():
        for r in rows:
            db.x("UPDATE units SET status='superseded' WHERE file_id=?", (r["id"],))
            db.x("DELETE FROM items WHERE file_id=?", (r["id"],))
            db.x("DELETE FROM timeline WHERE file_id=?", (r["id"],))
            db.x("DELETE FROM units WHERE file_id=?", (r["id"],))
            db.x("UPDATE files SET status='ready', processed_hash=NULL, attempts=0 WHERE id=?", (r["id"],))
    print(f"{len(rows)} ファイルを再解析対象にしました")
    return 0


def cmd_errors(args) -> int:
    cfg = load_config(args.config)
    db = _open_db(cfg)
    for e in db.q("SELECT e.*, f.path FROM errors e LEFT JOIN files f ON f.id=e.file_id WHERE resolved=0"
                  " ORDER BY e.id DESC LIMIT ?", (args.limit,)):
        print(f"#{e['id']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['created_at']))} [{e['stage']}] "
              f"{e['path'] or ''}\n    {e['message']}")
    return 0


def cmd_estimate(args) -> int:
    """AI解析（有料）を始める前の概算。APIは呼ばない。"""
    overrides = {"semantic": {"ai": True}} if args.with_semantic else None
    cfg = load_config(args.config, overrides)
    if not cfg.db_path.exists():
        Syncer(cfg).pull_state()
    if not cfg.db_path.exists():
        print("まだ一度も実行されていません。先に AI なしで `structlab run` を実行してください。")
        return 0
    from .semantic.estimate import estimate, estimate_text
    db = _open_db(cfg)
    e = estimate(db, cfg)
    print(json.dumps(e, ensure_ascii=False, indent=1) if args.json else estimate_text(e))
    return 0


def cmd_search(args) -> int:
    cfg = load_config(args.config)
    from .semantic.report import search
    db = _open_db(cfg)
    hits = search(db, args.keyword, args.limit)
    if not hits:
        print("見つかりませんでした")
    for h in hits:
        if h["type"] == "directive":
            print(f"[指示 #{h['id']}] {h['class']} {h['text']}\n    {h['source']} {h['where']} ／ {h['verification']} ／ {h['effective']}")
        else:
            print(f"[場面] {h['source']} {h['where']}（解釈 {h['pass']} 版）: {h['text']}")
    return 0


def cmd_show_directive(args) -> int:
    cfg = load_config(args.config)
    from .semantic.report import directive_record
    db = _open_db(cfg)
    d = db.q1("SELECT * FROM directives WHERE id=?", (args.id,))
    if d is None:
        print("その番号の指示はありません")
        return 1
    print(json.dumps(directive_record(db, d), ensure_ascii=False, indent=1))
    return 0


def cmd_checks(args) -> int:
    cfg = load_config(args.config)
    from .checks.base import STAGE_LABEL, STATUS_LABEL
    from .checks.report import summary, write_checks_report
    db = _open_db(cfg)
    sm = summary(db)
    for stg, c in sm.items():
        print(f"{STAGE_LABEL.get(stg, stg)}: " + "、".join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in sorted(c.items())))
    print(f"詳細: {write_checks_report(db, cfg)}")
    return 0


def cmd_reverify(args) -> int:
    """独立した再検証。AIは --allow-external-ai のときだけ（有料）。"""
    cfg = load_config(args.config)
    _setup_logging(cfg, args.verbose)
    from .ai import make_provider
    from .checks.report import write_checks_report
    from .checks.reverify import reverify
    from .pipeline import Budget
    db = _open_db(cfg)
    provider, note = make_provider(cfg, allow_external_flag=args.allow_external_ai)
    run_id = db.start_run()
    counts = reverify(db, cfg, provider=provider, budget=Budget(cfg, db, run_id), run_id=run_id, limit=args.limit)
    db.end_run(run_id, "ok", {"reverify": counts})
    print(json.dumps({"reverify": counts, "ai": note if provider is None else provider.name}, ensure_ascii=False))
    print(f"詳細: {write_checks_report(db, cfg)}")
    return 0


def cmd_check_draft(args) -> int:
    """提出物の下書き（Excel・文章）を元資料・計算・指示と照合する（提出物は作らない）。"""
    cfg = load_config(args.config)
    from .checks.deliverable import check_draft
    from .checks.report import write_checks_report
    if args.db:  # 読み取り基盤・G5 の意味理解の DB（corpus/semantic_run/semantic.db）と照合する
        from .db import DB
        db = DB(Path(args.db))
    else:
        db = _open_db(cfg)
    counts = check_draft(db, cfg, Path(args.path), fmt=args.format, pages=args.pages)
    print(json.dumps(counts, ensure_ascii=False))
    print(f"詳細: {write_checks_report(db, cfg)}")
    return 0


def cmd_style(args) -> int:
    """文章スタイル（最終工程のみ）。profile: PDFの読み込み、apply: 確定した文章の表現調整。"""
    cfg = load_config(args.config)
    from .style.apply import apply_to_markdown, ensure_profile
    from .style.profile import load_profile
    if args.action == "profile":
        prof = ensure_profile(cfg, _open_db(cfg))
        if prof is None:
            print("文章スタイルのPDFがありません（style.pdf_path または style.dropbox_path を設定）。他の処理には影響しません。")
            return 0
        print(json.dumps(prof["stats"], ensure_ascii=False))
        print(f"保存先: {cfg.work_dir / 'style' / 'profile.json'}（リポジトリには保存しない）")
        return 0
    if args.action == "status":
        prof = load_profile(cfg.work_dir / "style" / "profile.json")
        print(json.dumps({"enabled": bool(cfg.data["style"].get("enabled")),
                          "profile": prof["stats"] if prof else None}, ensure_ascii=False))
        return 0
    if not args.input:
        print("文章ファイルを指定してください: structlab style apply 入力.md --out 出力.md")
        return 2
    from .ai import make_provider
    db = _open_db(cfg)
    provider, note = make_provider(cfg, allow_external_flag=args.allow_external_ai)
    src = Path(args.input)
    md = src.read_text(encoding="utf-8")
    out_text, runs = apply_to_markdown(db, cfg, md, provider=provider)
    out = Path(args.out or src.with_name(src.stem + ".styled.md"))
    out.write_text(out_text, encoding="utf-8")
    out.with_name(out.stem + ".original.md").write_text(md, encoding="utf-8")  # 適用前を必ず残す
    print(json.dumps({"ai": note if provider is None else provider.name, "sections": runs}, ensure_ascii=False, default=str))
    print(f"出力: {out}（適用前: {out.with_name(out.stem + '.original.md')}）")
    return 0


def cmd_feedback(args) -> int:
    """教授などのフィードバックを、内容を推測せずにそのまま記録する。"""
    cfg = load_config(args.config)
    db = _open_db(cfg)
    if args.action == "add":
        tt, tid = (args.target.split(":", 1) + [None])[:2] if args.target else (None, None)
        cur = db.x("INSERT INTO feedback(received_at,author,text,target_type,target_id,suspected_stage,status,note) "
                   "VALUES(?,?,?,?,?,?, 'open', ?)", (time.time(), args.author, args.text, tt, tid, args.stage, args.note))
        print(f"フィードバック #{cur.lastrowid} を記録しました。原因の追跡: structlab trace {args.target or '<対象>'}")
        return 0
    if args.action == "set":
        db.x("UPDATE feedback SET suspected_stage=COALESCE(?, suspected_stage), status=COALESCE(?, status), "
             "note=COALESCE(?, note) WHERE id=?", (args.stage, args.status, args.note, args.id))
        return 0
    from .trace import STAGES
    for r in db.q("SELECT * FROM feedback ORDER BY id"):
        print(f"#{r['id']} [{r['status']}] {r['author'] or ''}: {r['text']}（対象: {r['target_type'] or '-'}:{r['target_id'] or '-'}"
              f"／原因の工程: {STAGES.get(r['suspected_stage'] or 'unknown', r['suspected_stage'])}）")
    return 0


def cmd_trace(args) -> int:
    cfg = load_config(args.config)
    from .trace import trace_directive, trace_style
    db = _open_db(cfg)
    kind, _, tid = args.target.partition(":")
    try:
        res = trace_directive(db, int(tid)) if kind == "directive" else trace_style(db, int(tid)) if kind == "style_run" else None
    except (KeyError, ValueError):
        res = None
    if res is None:
        print("対象が見つかりません（例: structlab trace directive:37 / style_run:3）")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


def cmd_dropbox_auth(args) -> int:
    """PKCE でリフレッシュトークンを取得し、権限600のファイルに保存する（値は表示しない）。
    スコープは読み取り専用（--write を付けたときだけ、解析結果の保存用に files.content.write を追加）。"""
    from dropbox import DropboxOAuth2FlowNoRedirect
    from .sources.dropbox_source import CRED_DIR, READ_SCOPES, dropbox_credentials
    cdir = CRED_DIR.expanduser()
    state = cdir / "pkce.json"
    if args.step in ("preflight", "exchange", "store"):
        from . import token_store
        return token_store.main([args.step])
    if args.step == "start":
        app_key = dropbox_credentials()["app_key"]
        if not app_key:
            print("環境変数 DROPBOX_APP_KEY を設定してから実行してください（値はチャットに貼らないでください）")
            return 1
        scopes = READ_SCOPES + (["files.content.write"] if args.write else [])
        flow = DropboxOAuth2FlowNoRedirect(app_key, use_pkce=True, token_access_type="offline", scope=scopes)
        url = flow.start()
        cdir.mkdir(parents=True, exist_ok=True)
        fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"code_verifier": flow.code_verifier, "code_challenge": flow.code_challenge,
                       "app_key": app_key, "scopes": scopes}, f)
        print("1) 次のURLをブラウザで開き、Dropbox で「許可」を押してください（権限: " + ", ".join(scopes) + "）:\n" + url)
        print("2) 表示されたコードで: structlab dropbox-auth finish <コード>（コードは一度きり・数分で失効）")
        return 0
    if not state.exists() or not args.code:
        print("先に `structlab dropbox-auth start` を実行してください")
        return 1
    saved = json.loads(state.read_text())
    flow = DropboxOAuth2FlowNoRedirect(saved["app_key"], use_pkce=True, token_access_type="offline",
                                       scope=saved.get("scopes"))
    flow.code_verifier, flow.code_challenge = saved["code_verifier"], saved["code_challenge"]
    try:
        res = flow.finish(args.code.strip())
    except Exception as e:  # noqa: BLE001
        print(f"トークンの取得に失敗しました（{type(e).__name__}）。コードの期限切れの場合は start からやり直してください")
        return 1
    finally:
        state.unlink(missing_ok=True)
    for name, val in (("dropbox_refresh_token", res.refresh_token), ("dropbox_app_key", saved["app_key"])):
        fd = os.open(cdir / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(val)
        os.chmod(cdir / name, 0o600)
    print(f"リフレッシュトークンを {cdir / 'dropbox_refresh_token'} に保存しました（権限600。値は表示しません）。")
    print("この実行環境では環境変数なしで使えます。GitHub Actions で使うには docs/SETUP_DROPBOX.md の手順で Secrets に登録してください。")
    print("続けて接続を確認します（読み取りのみ）:")
    try:
        cfg = load_config(args.config)
    except ConfigError:
        cfg = load_config(Path(__file__).resolve().parents[2] / "configs" / "dropbox.yaml")
    from .connect import run_connect_check
    return 0 if run_connect_check(cfg, download=False, claude=False)["ok"] else 1


def cmd_read(args) -> int:
    """資料の読み取り（棚卸し・ローカル読み取り・AIの書き起こし（許可時）・照合）。元資料は読むだけ。"""
    cfg = load_config(args.config)
    if args.list_targets:  # GitHub Actions で資料ごとに並列実行するための一覧（JSON）
        from .reading.runner import list_targets
        from .sources import make_source
        print(json.dumps(list_targets(cfg, make_source(cfg), with_hash=True), ensure_ascii=False))
        return 0
    _setup_logging(cfg, args.verbose)
    from .reading.runner import run_reading
    provider = None
    if args.allow_external_ai:
        from .ai import make_provider
        provider, why = make_provider(cfg, allow_external_flag=True)
        if provider is None:
            print(f"AI を使えません: {why}")
            return 1
        from .ai import make_escalation_provider
        escalation = make_escalation_provider(cfg, allow_external_flag=True)
    else:
        escalation = None
    counter = None
    if args.count_tokens:  # 入力トークンの実測（count_tokens。推論しないので課金なし。内容は API に送られる）
        from .ai.anthropic_provider import AnthropicProvider
        ai = cfg.data["ai"]
        counter = AnthropicProvider(model=ai["model"], effort=ai["effort"], max_tokens=ai["max_output_tokens"],
                                    use_fallbacks=False, max_image_edge=ai["max_image_edge"])
    if args.max_ai_calls is not None:
        cfg.data.setdefault("reading", {})["max_ai_calls"] = args.max_ai_calls
    summary = run_reading(cfg, only=args.only, allow_ai=args.allow_external_ai, provider=provider,
                          escalation=escalation, counter=counter,
                          out_dir=Path(args.out) if args.out else None, log=lambda m: print(m, flush=True))
    out = Path(args.out) if args.out else cfg.work_dir / "reading"
    print((out / "summary.md").read_text(encoding="utf-8"))
    return 0 if not any(f.get("error") for f in summary["files"]) else 1


def cmd_requirements(args) -> int:
    """要求事項の台帳の照合（解析の完了時・Excel 作成時・レポート作成時）。"""
    from . import requirements as R
    cfg = load_config(args.config)
    reading = [Path(x) for x in (args.reading or [])]
    sem = Path(args.semantic) if args.semantic else None
    out = Path(args.out)
    if args.action == "checklist":
        items = R.checklist(args.stage, sem, reading)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"checklist_{args.stage}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{args.stage} の照合項目 {len(items)} 件 → {out / f'checklist_{args.stage}.json'}（evidence を埋めて verify に渡す）")
        return 0
    if args.stage == "analysis":
        rows = R.verify_analysis(cfg, reading, Path(args.corpus) if args.corpus else None, sem)
    else:
        items = R.checklist(args.stage, sem, reading)
        ev = json.loads(Path(args.evidence).read_text(encoding="utf-8")) if args.evidence else {}
        rows = R.verify_stage(args.stage, items, ev)
    counts = R.write(rows, out, args.stage)
    print(f"要求事項の照合（{args.stage}）: " + "、".join(f"{k} {v}" for k, v in counts.items() if v))
    print((out / f"ledger_{args.stage}.md").read_text(encoding="utf-8")[:6000])
    return 0


def cmd_cost(args) -> int:
    """費用の発生箇所（工程・依頼の種類ごと）と最適化ごとの効果（API は呼ばない）。"""
    from . import costmodel as CM
    res = CM.report([Path(x) for x in args.reading], Path(args.semantic_db) if args.semantic_db else None, Path(args.out))
    print(json.dumps({k: v for k, v in res.items() if k != "assumptions"}, ensure_ascii=False, indent=1))
    return 0


def cmd_pilot(args) -> int:
    """費用を下げる候補の品質の比較（小さな見本で、今の方式×2 と候補を比べる）。"""
    from . import pilot as PL
    if args.action == "plan":
        for c in PL.CANDIDATES:
            print(f"{c['id']}: {c['what']}\n  設定: {c['config']} / 見本: {c['sample']} / 見込み: {c['saving']}")
        rows = PL.plan_costs(Path(args.semantic_db) if args.semantic_db else None)
        if rows:
            print("\n見本の比較の追加費用と、本番 1 回あたりの削減（mid、円）:")
            for r in rows:
                print(f"  {r['arm']}（{r['stage']} から先を比較、見本 {r['sample']}）: 比較 約 {r['pilot_extra_yen']:,} / "
                      f"削減 約 {r['saving_per_run_yen']:,} / 本番 1 回で差し引き {r['net_after_one_run_yen']:+,}")
        return 0
    if args.action == "branch":  # 有料: 見本の資料で、指定の段階から先を今の方式×2・候補×1 で実行して比べる
        if not args.allow_external_ai:
            print("有料の比較です。利用者の承認後に --allow-external-ai を付けて実行してください")
            return 1
        from .ai import make_provider
        cfg = load_config(args.config)
        provider, why = make_provider(cfg, allow_external_flag=True)
        if provider is None:
            print(f"AI を使えません: {why}")
            return 1
        rec = None
        for d in args.records or []:
            for rp in Path(d).rglob("reading.json"):
                r = json.loads(rp.read_text(encoding="utf-8"))
                if args.sample in r.get("path", ""):
                    rec = r
        if rec is None:
            print(f"見本の資料 {args.sample} の読み取り結果がありません")
            return 1
        res = PL.branch_compare(cfg, Path(args.semantic_db), rec, args.arm, Path(args.out), provider)
        print(("採用" if res["adopt"] else "不採用（今の方式のまま）") + f": {json.dumps(res, ensure_ascii=False)[:600]}")
        return 0
    res = PL.compare(args.kind, Path(args.baseline), Path(args.repeat), Path(args.candidate), args.tolerance)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pilot_decision.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(("採用" if res["adopt"] else "不採用（今の方式のまま）") + f": 候補の包含率 {res['containment_candidate']} / "
          f"同じ方式どうし {res['containment_repeat']}、重要項目の欠落 候補 {res['critical_missing_candidate']} / "
          f"同じ方式どうし {res['critical_missing_repeat']}")
    return 0


def cmd_queue(args) -> int:
    """候補 G5: Claude Code で解析する依頼の受け渡し（status / next / show / answer）。"""
    from .ai import queue_provider as Q
    d = Path(args.dir)
    if args.action == "status":
        req = list((d / "requests").glob("*.json"))
        print(f"依頼 {len(req)} 件、回答済み {len(req) - len(Q.pending(d))} 件、未回答 {len(Q.pending(d))} 件")
    elif args.action == "next":
        for p in Q.pending(d)[:args.n]:
            r = json.loads(p.read_text(encoding="utf-8"))
            print(f"{r['id']}\t{r['task']}\t{r['source_label']}\t画像 {len(r['images'])} 枚")
    elif args.action == "show":
        r = json.loads((d / "requests" / f"{args.id}.json").read_text(encoding="utf-8"))
        print(f"# 依頼 {r['id']}（{r['task']}・{r['source_label']}）\n## 指示（system）\n{r['system']}\n"
              f"## 画像（この順に Read で開く。縮小しない）")
        for i, im in enumerate(r["images"], 1):
            print(f"{i}. {im['label']}: {im['path']}")
        print(f"## 本文\n{r['text']}\n## 出力の形（JSON スキーマ。これに合う JSON だけを書く）\n"
              f"{json.dumps(r['schema'], ensure_ascii=False)}")
    elif args.action == "answer":
        errs = Q.answer(d, args.id, Path(args.file), args.model)
        print("保存しました" if not errs else "出力の形に合いません（保存していない）:\n" + "\n".join(errs))
        return 1 if errs else 0
    return 0


def cmd_connect_check(args) -> int:
    cfg = load_config(args.config)
    from .connect import run_connect_check
    res = run_connect_check(cfg, download=not args.no_download, claude=not args.no_claude,
                            max_sample_mb=args.max_sample_mb,
                            summary_path=args.summary or os.environ.get("GITHUB_STEP_SUMMARY"))
    return 0 if res["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="structlab", description="建築構造実験資料の自動解析")
    ap.add_argument("-c", "--config", default=os.environ.get("STRUCTLAB_CONFIG", "config.yaml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("check")
    p.add_argument("--allow-external-ai", action="store_true")
    sub.add_parser("scan")
    p = sub.add_parser("run")
    p.add_argument("--allow-external-ai", action="store_true",
                   help="外部AIサービスへの資料送信を今回の実行に限り許可する")
    p.add_argument("--max-runtime", type=int, default=None, help="最大実行時間（分）")
    p.add_argument("--watch", type=int, default=0, help="N分ごとに再スキャンして新規ファイルを処理し続ける")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    sub.add_parser("report")
    p = sub.add_parser("retry")
    p.add_argument("--file", help="パスの一部で対象を絞る")
    p = sub.add_parser("reanalyze")
    p.add_argument("path", help="パスの一部")
    p = sub.add_parser("errors")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("estimate", help="AI解析の呼び出し回数・費用の概算（APIは呼ばない）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--with-semantic", action="store_true", help="semantic.ai が無効な設定でも、有効にした場合を見積もる")
    p = sub.add_parser("search", help="指示・場面の理解をキーワード検索（出典の時刻付き）")
    p.add_argument("keyword")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("show-directive", help="指示1件の全項目（根拠・前後・関連・照合）を表示")
    p.add_argument("id", type=int)
    sub.add_parser("checks", help="各工程のチェック結果の集計")
    p = sub.add_parser("reverify", help="独立した再検証（AIは --allow-external-ai のときだけ）")
    p.add_argument("--allow-external-ai", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("check-draft", help="提出物の下書きを元資料・計算・指示と照合")
    p.add_argument("path")
    p.add_argument("--format", default=None, help="提出形式（PDF / Word など）")
    p.add_argument("--pages", type=int, default=None)
    p.add_argument("--db", help="照合に使う DB（例: work/g5/corpus/semantic_run/semantic.db）。省略時は設定の作業フォルダ")
    p = sub.add_parser("style", help="文章スタイル（最終工程のみ）: profile / status / apply")
    p.add_argument("action", choices=["profile", "status", "apply"])
    p.add_argument("input", nargs="?")
    p.add_argument("--out")
    p.add_argument("--allow-external-ai", action="store_true")
    p = sub.add_parser("feedback", help="教授などのフィードバックの記録: add / list / set")
    p.add_argument("action", choices=["add", "list", "set"])
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--id", type=int)
    p.add_argument("--target", help="例: directive:37 / style_run:3")
    p.add_argument("--author")
    p.add_argument("--stage", choices=["extraction", "understanding", "directives", "calculation", "writing", "unknown"])
    p.add_argument("--status", choices=["open", "investigating", "resolved"])
    p.add_argument("--note")
    p = sub.add_parser("trace", help="結果がどの工程を経て作られたかを表示（例: directive:37）")
    p.add_argument("target")
    p = sub.add_parser("dropbox-auth", help="Dropbox のリフレッシュトークン取得（PKCE。値は表示しない）")
    p.add_argument("step", choices=["start", "finish", "preflight", "exchange", "store"],
                   help="start/finish: PKCE（手元）。preflight/exchange/store: GitHub Actions 用"
                        "（DROPBOX_AUTH_CODE を交換し DROPBOX_REFRESH_TOKEN に登録）")
    p.add_argument("code", nargs="?")
    p.add_argument("--write", action="store_true", help="解析結果の保存用に files.content.write も許可する")
    p = sub.add_parser("read", help="資料の読み取り基盤（棚卸し・場面分割・OCR・文字起こし・AIの書き起こし・照合）")
    p.add_argument("--only", nargs="*", help="パスの一部で対象を絞る")
    p.add_argument("--allow-external-ai", action="store_true", help="AIによる画像の書き起こし（有料。事前に見積もりを確認）")
    p.add_argument("--max-ai-calls", type=int, default=None)
    p.add_argument("--out", help="出力先（既定: work/reading）")
    p.add_argument("--list-targets", action="store_true", help="読み取る資料（重複を除く）の一覧を JSON で出す")
    p.add_argument("--count-tokens", action="store_true",
                   help="AI に送る依頼の入力トークンを API の count_tokens で数える（推論しないので課金なし）")
    p = sub.add_parser("requirements", help="要求事項の台帳（利用者の指示＋資料の要求事項＋未確認事項）の照合")
    p.add_argument("action", choices=["verify", "checklist"])
    p.add_argument("--stage", choices=["analysis", "excel", "report"], default="analysis")
    p.add_argument("--reading", nargs="*", help="読み取り結果のフォルダ（reading.json を含む）")
    p.add_argument("--corpus", help="資料横断の結果（materials.json など）")
    p.add_argument("--semantic", help="意味理解の結果（semantic/directives.json を含むフォルダ）")
    p.add_argument("--evidence", help="作成段階の確認記録（{項目ID: {status, where, note}}）")
    p.add_argument("--out", required=True)
    p = sub.add_parser("cost", help="費用の発生箇所と最適化ごとの効果（API は呼ばない）")
    p.add_argument("--reading", nargs="+", required=True)
    p.add_argument("--semantic-db")
    p.add_argument("--out", default="work/costs")
    p = sub.add_parser("pilot", help="費用を下げる候補が今の方式と同じ品質かを比べる（採用の判断）")
    p.add_argument("action", choices=["plan", "compare", "branch"])
    p.add_argument("--arm", default="C1v+C4", help="branch: 比べる候補（C1v / C4 / C1v+C4 / C1r）")
    p.add_argument("--sample", default="事前説明1", help="branch: 見本の資料（パスの一部）")
    p.add_argument("--records", nargs="*", help="branch: 読み取り結果のフォルダ")
    p.add_argument("--allow-external-ai", action="store_true", help="branch: 有料の比較を実行する（承認後だけ）")
    p.add_argument("--kind", choices=["reading", "semantic"], default="semantic")
    p.add_argument("--baseline")
    p.add_argument("--repeat")
    p.add_argument("--candidate")
    p.add_argument("--tolerance", type=float, default=0.0)
    p.add_argument("--out", default="work/pilot")
    p.add_argument("--semantic-db", help="plan: 見本の比較にかかる費用を計算する意味理解の DB（semantic.db）")
    p = sub.add_parser("queue", help="（候補 G5）Claude Code で解析する依頼の受け渡し")
    p.add_argument("action", choices=["status", "next", "show", "answer"])
    p.add_argument("id", nargs="?")
    p.add_argument("file", nargs="?", help="answer: 答えの JSON ファイル")
    p.add_argument("--dir", default="work/queue")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--model", default="claude-code:claude-opus-5-5", help="answer: 答えたモデル（記録用）")
    p = sub.add_parser("connect-check", help="Dropbox・Claude API の接続確認（読み取りのみ・課金なし）")
    p.add_argument("--no-download", action="store_true", help="動画・PDF・Excel の見本の取得を省略")
    p.add_argument("--no-claude", action="store_true")
    p.add_argument("--max-sample-mb", type=int, default=1000)
    p.add_argument("--summary", help="結果の Markdown を追記するファイル（既定: GITHUB_STEP_SUMMARY）")
    args = ap.parse_args(argv)
    try:
        return {"init": cmd_init, "check": cmd_check, "scan": cmd_scan, "run": cmd_run, "status": cmd_status,
                "report": cmd_report, "retry": cmd_retry, "reanalyze": cmd_reanalyze, "errors": cmd_errors,
                "style": cmd_style, "feedback": cmd_feedback, "trace": cmd_trace,
                "checks": cmd_checks, "reverify": cmd_reverify, "check-draft": cmd_check_draft,
                "estimate": cmd_estimate, "search": cmd_search, "show-directive": cmd_show_directive,
                "dropbox-auth": cmd_dropbox_auth, "connect-check": cmd_connect_check, "read": cmd_read,
                "requirements": cmd_requirements, "pilot": cmd_pilot,
                "cost": cmd_cost, "queue": cmd_queue}[args.cmd](args)
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
