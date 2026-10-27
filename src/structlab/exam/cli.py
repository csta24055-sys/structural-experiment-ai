"""structlab-exam: 試験出題予測・集中対策システムのコマンド。

  structlab-exam run --input <フォルダ> [--dropbox-root /授業] [--box-url URL ...]
  structlab-exam box-check <URL または .url ファイル> ... [--allow-download]
  structlab-exam env      使える外部ツール（ffmpeg・tesseract・Playwright など）を表示
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import box as B
from . import links as L


def cmd_env(_a) -> int:
    from .. import media
    info = {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
            "tesseract": media.OCR("auto").label, "libreoffice": shutil.which("soffice") or shutil.which("libreoffice"),
            "browser": B.browser_available()}
    try:
        import faster_whisper  # noqa: F401
        info["faster_whisper"] = True
    except ImportError:
        info["faster_whisper"] = False
    import os
    info["secrets"] = {k: ("あり" if os.environ.get(k) else "なし") for k in
                       ("DROPBOX_APP_KEY", "DROPBOX_APP_SECRET", "DROPBOX_REFRESH_TOKEN", "BOX_ACCESS_TOKEN")}
    print(json.dumps(info, ensure_ascii=False, indent=1))
    return 0


def cmd_box_check(a) -> int:
    urls = []
    for x in a.targets:
        p = Path(x)
        if p.exists():
            recs, _ = L.extract_links(p.read_bytes(), p.name, f"local:{p}")
            urls += [r.url for r in recs]
        else:
            urls.append(x)
    results = []
    for u in L.dedupe([L.classify_url(u) for u in urls]):
        c = B.check_link(u.url, work_dir=Path(a.work), allow_download=a.allow_download,
                         use_browser=not a.no_browser).as_dict()
        results.append(c)
        if a.quiet:
            print(f"[{c['state']}] {c['state_label']}（{c['reason']}）")
        else:
            print(f"[{c['state']}] {c['state_label']}: {u.url}\n    名前={c['name']} 再生時間={c['duration_text']} "
                  f"ダウンロード許可={c['can_download']} 理由={c['reason']}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def _summary(rep: dict) -> dict:
    """公開されるログ（公開リポジトリの Actions）向け: 件数だけ。科目名・ファイル名・URL は出さない。"""
    subs = [v for k, v in rep["subjects"].items() if k != "要確認"]
    return {"subjects": len(subs),
            "grades": {g: sum(v.get("grades", {}).get(g, 0) for v in subs) for g in "SABC"},
            "mock_exams": sum(v.get("mock_exams", 0) for v in subs),
            "unanalyzed": sum(len(v.get("unanalyzed", [])) for v in subs),
            "unclassified": rep["subjects"].get("要確認", {}).get("materials", 0),
            "box_states": {st: sum(1 for b in rep["stages"]["box"].values() if b["state"] == st) for st in "ABCDEF"},
            "quality_problems": sum(len(v.get("quality_problems", [])) for v in subs)}


def cmd_run(a) -> int:
    from .pipeline import Options, run
    o = Options(input_dir=Path(a.input) if a.input else None, dropbox_root=a.dropbox_root, box_urls=a.box_url or [],
                work=Path(a.work), out=Path(a.out), allow_download=a.allow_download, use_browser=not a.no_browser,
                docx=not a.no_docx, overrides=Path(a.overrides) if a.overrides else None,
                config=Path(a.config) if a.config else None)
    if a.quiet:
        o.log = lambda *x: None
    if not (o.input_dir or o.dropbox_root is not None or o.box_urls):
        print("入力がありません: --input / --dropbox-root / --box-url のいずれかを指定してください", file=sys.stderr)
        return 2
    from .pipeline import DropboxUnavailable
    dst = None
    if a.dropbox_out:
        from ..sources.base import SourceError
        from ..sources.dropbox_source import DropboxOutput, redact
        from . import sync
        try:
            dst = DropboxOutput(a.dropbox_out, a.dropbox_root or "")
            print("前回の処理状態を復元:", "あり" if sync.restore_state(dst, o.work) else "なし（初回）")
        except Exception as e:  # noqa: BLE001
            print(f"Dropbox の出力フォルダを使えません: {redact(str(e))[:300]}", file=sys.stderr)
            return 3
    try:
        rep = run(o)
    except DropboxUnavailable as e:
        print(str(e), file=sys.stderr)
        return 3
    finally:
        if dst is not None:  # 途中で止まっても、そこまでの状態を保存して次回続きから
            print("処理状態を保存:", sync.save_state(dst, o.work), "件")
    if dst is not None:
        print("教材を Dropbox に保存:", sync.upload_outputs(dst, o.out), "件")
    if a.quiet:
        print(json.dumps(_summary(rep), ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"subjects": rep["subjects"], "box": rep["stages"]["box"]}, ensure_ascii=False, indent=1,
                         default=str)[:6000])
    bad = [s for s, v in rep["subjects"].items() if v.get("quality_problems")]
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="structlab-exam", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env", help="使える外部ツールを表示")
    e.set_defaults(fn=cmd_env)
    b = sub.add_parser("box-check", help="Box 共有リンクの検査（取得可否の判定）")
    b.add_argument("targets", nargs="+", help="URL または .url / テキストファイル")
    b.add_argument("--allow-download", action="store_true", help="許可されている場合だけ公式の手段で取得する")
    b.add_argument("--no-browser", action="store_true")
    b.add_argument("--work", default="work/exam")
    b.add_argument("--json", help="結果を JSON で保存")
    b.add_argument("--quiet", action="store_true", help="URL・ファイル名をログに出さない")
    b.set_defaults(fn=cmd_box_check)
    r = sub.add_parser("run", help="解析して教材を作る（差分更新・途中再開）")
    r.add_argument("--input", help="ローカルの資料フォルダ（読み取りのみ）")
    r.add_argument("--dropbox-root", help="Dropbox の資料フォルダ（例: /授業）")
    r.add_argument("--box-url", action="append", help="Box 共有 URL を直接指定（複数可）")
    r.add_argument("--work", default="work/exam")
    r.add_argument("--out", default="output/exam")
    r.add_argument("--allow-download", action="store_true")
    r.add_argument("--no-browser", action="store_true")
    r.add_argument("--no-docx", action="store_true")
    r.add_argument("--overrides", help="重要度の手動修正（YAML: 論点ID または題名 → {grade, reason}）")
    r.add_argument("--config", help="設定ファイル（既定: configs/exam.yaml）")
    r.add_argument("--dropbox-out", help="教材と処理状態を保存する Dropbox のフォルダ（資料フォルダと重ならないこと）")
    r.add_argument("--quiet", action="store_true", help="ログに科目名・ファイル名・URL を出さない（公開リポジトリの Actions 用）")
    r.set_defaults(fn=cmd_run)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
