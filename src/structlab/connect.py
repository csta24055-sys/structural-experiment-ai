"""接続確認（`structlab connect-check`）。読み取りのみで、Dropbox には何も書き込まない。

1. 認証情報の有無（値は表示しない）
2. Dropbox: トークンの更新、解析対象フォルダの存在、ファイル一覧（種類別の件数・フォルダ別の件数・例）
3. 既存の解析プログラムへの接続: 一覧を一時DBで scan し（アップロード完了待ちの判定を含む）、
   動画・PDF・Excel を種類ごとに1件（最小のもの）だけ取得して、ハッシュ照合と解析単位への分割（plan）まで行い、削除する
4. Claude API: 無料のモデル一覧の取得だけでキーの有効性を確認する（有料の解析は呼ばない）

GitHub Actions では Secrets の登録状況（SECRET_SET_<名前>=true/false）も表示する。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable

from .models import RemoteFile, kind_for
from .sources.dropbox_source import READ_SCOPES, DropboxSource, dropbox_credentials, make_client, redact

SAMPLE_KINDS = ("video", "pdf", "excel")
KIND_LABEL = {"video": "動画", "audio": "音声", "pdf": "PDF", "excel": "Excel", "excel_legacy": "Excel(旧.xls)",
              "image": "画像", "zip": "ZIP", "text": "テキスト", "docx": "Word", "pptx": "PowerPoint", "other": "その他"}
ANTHROPIC_SECRET_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_KEY", "CLAUDE_KEY")


class _ListedSource:
    """一覧を一度だけ取得して scan に渡す（Dropbox への問い合わせを二重にしない）。"""

    def __init__(self, files: list[RemoteFile]):
        self.files = files

    def iter_files(self):
        return iter(self.files)


def _err(e: Exception) -> str:
    return redact(f"{type(e).__name__}: {e}")[:400]


def _dropbox_error_hint(e: Exception) -> str:
    s = str(e)
    if "invalid_grant" in s or "invalid_access_token" in s or "expired_access_token" in s:
        return ("リフレッシュトークンが無効です（取り消し済み・別アプリのもの・コピーの欠け）。"
                "App key / App secret と同じアプリで取り直してください")
    if "missing_scope" in s:
        return ("権限（スコープ）が足りません。App Console の Permissions で files.metadata.read と "
                "files.content.read にチェック → Submit のあと、もう一度許可（トークンの取り直し）が必要です")
    if "invalid_client" in s or "unauthorized_client" in s:
        return "App key または App secret が違います（Secrets の値を確認）"
    return ""


def run_connect_check(cfg, *, download: bool = True, claude: bool = True, max_sample_mb: int = 1000,
                      out: Callable[[str], None] = print, client=None, ai_client=None,
                      summary_path: str | None = None) -> dict:
    lines: list[str] = []
    res: dict = {"dropbox": {}, "samples": {}, "claude": {}, "ok": True}

    def say(msg: str = "") -> None:
        msg = redact(msg)
        lines.append(msg)
        out(msg)

    def fail(msg: str) -> None:
        res["ok"] = False
        say("[NG ] " + msg)

    # ---- 1. 認証情報 ----
    say("## 認証情報（値は表示しません）")
    gh = {k[len("SECRET_SET_"):]: v.lower() == "true" for k, v in os.environ.items() if k.startswith("SECRET_SET_")}
    if gh:
        say("GitHub Secrets の登録: " + "、".join(f"{k}={'あり' if v else 'なし'}" for k, v in sorted(gh.items())))
    cred = dropbox_credentials()
    src = cred["sources"]
    for name, key in (("DROPBOX_APP_KEY", "app_key"), ("DROPBOX_REFRESH_TOKEN", "refresh_token"),
                      ("DROPBOX_APP_SECRET", "app_secret"), ("DROPBOX_ACCESS_TOKEN", "access_token")):
        say(f"- {name}: " + ({"env": "あり（環境変数）", "file": "あり（権限600のファイル）"}.get(src[key]) or "なし"))
    say(f"- ANTHROPIC_API_KEY: {'あり' if os.environ.get('ANTHROPIC_API_KEY') else 'なし'}")
    res["credentials"] = {k: bool(v) for k, v in src.items()} | {"anthropic": bool(os.environ.get("ANTHROPIC_API_KEY"))}

    # ---- 2. Dropbox ----
    root = cfg.data["source"]["path"]
    say("")
    say(f"## Dropbox（読み取りのみ。対象: {root}）")
    files: list[RemoteFile] | None = None
    if client is None and not cred["usable"]:
        fail("Dropbox の認証情報がありません（DROPBOX_APP_KEY と DROPBOX_REFRESH_TOKEN）。docs/SETUP_DROPBOX.md 参照")
    else:
        try:
            import dropbox
            dbx = client or make_client()
            if client is None and cred["refresh_token"] and cred["app_key"]:
                dbx.check_and_refresh_access_token()
                say("[OK ] リフレッシュトークンでアクセストークンを取得（自動更新できます）"
                    + ("" if cred["app_secret"] else "。App secret なし＝PKCE で取得したトークンとして更新"))
            source = DropboxSource(root, client=dbx)
            if source.root:
                try:
                    md = dbx.files_get_metadata(source.root)
                    if not isinstance(md, dropbox.files.FolderMetadata):
                        raise ValueError("フォルダではありません")
                except dropbox.exceptions.ApiError as e:
                    res["dropbox"]["folder_found"] = False
                    fail(f"フォルダ '{root}' が見つかりません")
                    _not_found_hint(dbx, root, say)
                    raise _Stop() from e
            res["dropbox"]["folder_found"] = True
            files = list(source.iter_files())
            say(f"[OK ] フォルダ '{root}' のファイル一覧を取得（サブフォルダを含めて {len(files)} 件）")
            _summarize_listing(files, cfg, say, res)
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001
            fail("Dropbox への接続・一覧取得に失敗: " + _err(e))
            hint = _dropbox_error_hint(e)
            if hint:
                say("      → " + hint)
            res["dropbox"]["error"] = _err(e)

    # ---- 3. 解析プログラムへの接続（scan と見本の取得） ----
    if files is not None:
        say("")
        say("## 解析プログラムへの接続（作業用の一時フォルダだけを使い、終了時に削除）")
        tmp = Path(tempfile.mkdtemp(prefix="structlab_check_"))
        try:
            _scan_dry(files, cfg, tmp, say, res)
            if download:
                _sample_downloads(files, cfg, source, tmp, max_sample_mb, say, fail, res)
            else:
                say("-- 見本の取得は省略（--no-download）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- 4. Claude API ----
    if claude:
        say("")
        say("## Claude API（無料のモデル一覧の取得だけ。解析・課金される呼び出しはしません）")
        _claude_check(cfg, ai_client, say, fail, res)

    say("")
    say("結果: " + ("接続確認はすべて成功" if res["ok"] else "NG の項目があります（上の説明を参照）"))
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("# 接続確認（structlab connect-check）\n\n" + "\n".join(
                    ln if ln.startswith(("#", "- ", "|")) or not ln else ln + "  " for ln in lines) + "\n")
        except OSError:
            pass
    return res


class _Stop(Exception):
    pass


def _not_found_hint(dbx, root: str, say) -> None:
    import dropbox
    try:
        top = dbx.files_list_folder("", recursive=False, limit=2000)
        names = [e.name for e in top.entries if isinstance(e, dropbox.files.FolderMetadata)]
    except Exception as e:  # noqa: BLE001
        say("      最上位の一覧も取得できません: " + _err(e))
        return
    want = PurePosixPath(root).name
    similar = [n for n in names if n.lower() == want.lower() or any(w in n for w in ("鋼材", "実験", "レポート"))]
    say(f"      見えている最上位のフォルダ: {len(names)} 件" + (f"（似た名前: {', '.join(similar[:10])}）" if similar else ""))
    if not names:
        say("      → 何も見えない場合、アプリの Access type が「App folder」の可能性があります。"
            "「Full Dropbox」で作成したアプリを使ってください（docs/SETUP_DROPBOX.md）")
    else:
        say("      → フォルダ名（全角・半角、空白）を確認し、configs/dropbox.yaml の source.path と合わせてください")


def _summarize_listing(files: list[RemoteFile], cfg, say, res) -> None:
    from .scanner import is_ignored
    pats = list(cfg.data["stability"]["ignore_patterns"])
    by_kind: dict[str, list[RemoteFile]] = defaultdict(list)
    folders: Counter = Counter()
    ignored = 0
    for f in files:
        if is_ignored(f.path, pats):
            ignored += 1
            continue
        by_kind[kind_for(f.path)[0]].append(f)
        parts = PurePosixPath(f.path).parts
        folders[parts[0] if len(parts) > 1 else "（直下）"] += 1
    total_mb = sum(f.size for f in files) / 1e6
    say(f"合計 {total_mb:,.1f} MB" + (f"、一時ファイル等で対象外 {ignored} 件" if ignored else ""))
    say("")
    say("| 種類 | 件数 | 合計MB | 例（最大3件） |")
    say("|---|---:|---:|---|")
    for k, lst in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        ex = "<br>".join(f.path for f in sorted(lst, key=lambda f: f.path)[:3])
        say(f"| {KIND_LABEL.get(k, k)} | {len(lst)} | {sum(f.size for f in lst) / 1e6:,.1f} | {ex} |")
    if folders:
        say("")
        say("フォルダ別: " + "、".join(f"{n} {c}件" for n, c in folders.most_common(20))
            + ("（ほか省略）" if len(folders) > 20 else ""))
    unsupported = len(by_kind.get("other", [])) + len(by_kind.get("excel_legacy", []))
    if unsupported:
        say(f"-- 解析できない形式 {unsupported} 件（一覧には記録されます。.xls は .xlsx に保存し直すと解析できます）")
    res["dropbox"].update(files=len(files), total_mb=round(total_mb, 1), ignored=ignored,
                          kinds={k: len(v) for k, v in by_kind.items()}, folders=dict(folders))


def _scan_dry(files, cfg, tmp: Path, say, res) -> None:
    from .db import DB
    from .scanner import scan
    try:
        db = DB(tmp / "state.db")
        s = scan(db, _ListedSource(files), cfg).summary()
        db.close()
        say(f"[OK ] 解析の対象判定（scan）: 解析可能 {s['ready']} 件、アップロード完了待ち {s['waiting_upload']} 件、"
            f"対象外 {s['ignored']} 件（一時DBで実行。実際の処理状況は変えていません）")
        res["scan"] = s
    except Exception as e:  # noqa: BLE001
        say("[NG ] scan の実行に失敗: " + _err(e))
        res["ok"] = False


def _sample_downloads(files, cfg, source, tmp: Path, max_mb: int, say, fail, res) -> None:
    from .processors import Ctx, get_processor
    for kind in SAMPLE_KINDS:
        cands = sorted((f for f in files if kind_for(f.path)[0] == kind), key=lambda f: f.size)
        label = KIND_LABEL[kind]
        if not cands:
            say(f"-- {label}: フォルダに無いため取得の確認はしていません")
            res["samples"][kind] = "none"
            continue
        rf = cands[0]
        if rf.size > max_mb * 1e6:
            say(f"-- {label}: 最小のファイルでも {rf.size / 1e6:,.0f} MB（上限 {max_mb} MB）のため取得の確認を省略")
            res["samples"][kind] = "too_large"
            continue
        dest = tmp / "dl" / f"{kind}{PurePosixPath(rf.path).suffix.lower()}"
        try:
            got = source.fetch(rf, dest, int(cfg.data["limits"]["min_free_disk_mb"]))
            units = get_processor(kind).plan(got, Ctx(cfg=cfg, artifacts_dir=tmp / "art", ocr=None, transcriber=None,
                                                      ai_enabled=False))
            unit = {"video": "区間", "pdf": "ページ", "excel": "単位"}[kind]
            extra = ""
            if kind == "video" and units:
                extra = f"、長さ {units[0].locator['duration'] / 60:.1f} 分"
            say(f"[OK ] {label}: {rf.path}（{rf.size / 1e6:,.1f} MB）を取得 → 内容ハッシュ一致 → "
                f"解析単位に分割できた（{len(units)} {unit}{extra}）")
            res["samples"][kind] = {"path": rf.path, "units": len(units)}
        except Exception as e:  # noqa: BLE001
            fail(f"{label}: {rf.path} の取得・読み込みに失敗: " + _err(e))
            res["samples"][kind] = "error"
        finally:
            dest.unlink(missing_ok=True)


def _claude_check(cfg, ai_client, say, fail, res) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if ai_client is None and not key:
        gh = [n for n in ANTHROPIC_SECRET_NAMES if os.environ.get(f"SECRET_SET_{n}", "").lower() == "true"]
        say("-- ANTHROPIC_API_KEY がありません" + (f"（Secrets {', '.join(gh)} は登録済み）" if gh else
                                                 "。GitHub Secrets に ANTHROPIC_API_KEY の名前で登録してください"))
        res["claude"]["status"] = "missing"
        return
    try:
        import anthropic
        client = ai_client or anthropic.Anthropic(api_key=key, max_retries=2, timeout=60)
        ids = [m.id for m in client.models.list(limit=50).data]
        say(f"[OK ] APIキーは有効（利用できるモデル {len(ids)} 件を取得。課金なし）")
        model = cfg.data["ai"].get("model")
        if model:
            try:
                client.models.retrieve(model)
                say(f"[OK ] 設定のモデル {model} を利用可能")
                res["claude"]["model_available"] = True
            except Exception as e:  # noqa: BLE001
                fail(f"設定のモデル {model} を利用できません: " + _err(e))
                res["claude"]["model_available"] = False
        say("-- 残高・支払い設定は無料の確認では分かりません（有料解析の前に Console の Billing で確認）")
        res["claude"]["status"] = "ok"
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        hint = {"AuthenticationError": "キーが無効です（貼り付けの欠け・削除済みのキー）",
                "PermissionDeniedError": "キーに権限がありません（組織・ワークスペースの設定）",
                "APIConnectionError": "Anthropic へ接続できません（ネットワーク）"}.get(name, "")
        fail("Claude API の確認に失敗: " + _err(e) + (f" → {hint}" if hint else ""))
        res["claude"]["status"] = "error"


__all__ = ["run_connect_check", "READ_SCOPES"]
