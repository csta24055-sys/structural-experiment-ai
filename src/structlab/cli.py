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
  dropbox-auth Dropbox のリフレッシュトークン取得（PKCE）
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
        has = (os.environ.get("DROPBOX_APP_KEY") and os.environ.get("DROPBOX_REFRESH_TOKEN")) or \
            os.environ.get("DROPBOX_ACCESS_TOKEN")
        line(bool(has), "Dropbox 認証情報（環境変数。値は表示しません）")
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
                line(False, f"Dropbox 接続失敗: {e}")
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


def cmd_dropbox_auth(args) -> int:
    from dropbox import DropboxOAuth2FlowNoRedirect
    app_key = os.environ.get("DROPBOX_APP_KEY")
    if not app_key:
        print("環境変数 DROPBOX_APP_KEY を設定してから実行してください")
        return 1
    state = Path(os.path.expanduser("~/.config/structlab/pkce.json"))
    flow = DropboxOAuth2FlowNoRedirect(app_key, use_pkce=True, token_access_type="offline")
    if args.step == "start":
        url = flow.start()
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"code_verifier": flow.code_verifier, "code_challenge": flow.code_challenge}))
        os.chmod(state, 0o600)
        print("1) 次のURLをブラウザで開き、Dropbox で「許可」を押してください:\n" + url)
        print("2) 表示されたコードで: structlab dropbox-auth finish <コード>")
        return 0
    if not state.exists() or not args.code:
        print("先に `structlab dropbox-auth start` を実行してください")
        return 1
    saved = json.loads(state.read_text())
    flow.code_verifier, flow.code_challenge = saved["code_verifier"], saved["code_challenge"]
    res = flow.finish(args.code.strip())
    state.unlink(missing_ok=True)
    out = Path(os.path.expanduser("~/.config/structlab/dropbox_refresh_token"))
    out.write_text(res.refresh_token)
    os.chmod(out, 0o600)
    print(f"リフレッシュトークンを {out} に保存しました（権限600）。")
    print("この値を実行環境の環境変数 DROPBOX_REFRESH_TOKEN に設定してください。GitHub やチャットには貼らないでください。")
    print("表示する場合: cat " + str(out))
    return 0


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
    p = sub.add_parser("dropbox-auth")
    p.add_argument("step", choices=["start", "finish"])
    p.add_argument("code", nargs="?")
    args = ap.parse_args(argv)
    try:
        return {"init": cmd_init, "check": cmd_check, "scan": cmd_scan, "run": cmd_run, "status": cmd_status,
                "report": cmd_report, "retry": cmd_retry, "reanalyze": cmd_reanalyze, "errors": cmd_errors,
                "dropbox-auth": cmd_dropbox_auth}[args.cmd](args)
    except ConfigError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
