#!/usr/bin/env python3
"""システムの状態を、Git 履歴なしでも読める独立した保存データにまとめる（最適化前後の保存用）。

  python3 scripts/snapshot.py --ref <commit> --name <名前> [--docs <手書き資料のフォルダ>] [--test]
  python3 scripts/snapshot.py --compare <前の名前> <後の名前>   （差分の機械的な比較を snapshots/DIFF-<前>-<後>.md に）

手書き資料（コードから読み取れない前提・判断理由・進捗）は docs/handoff/ に置き、変わった部分だけを直す（毎回書き直さない）。
--docs を省略すると docs/handoff/ を使う。それ以外（モジュール・設定・ワークフロー・要求事項・テスト・環境・差分）は機械的に作る。

出力（リポジトリの snapshots/ の下）:
  snapshots/<名前>/                 … 読むための資料（GitHub でそのまま読める）
      MANIFEST.json                … 元のコミット・ブランチ・作成日時・全ファイルの sha256
      MODULES.md                   … 全モジュールの役割・関数・クラス（ソースから自動生成）
      CONFIG.md / WORKFLOWS.md     … 設定ファイル・GitHub Actions の全文と要点
      REQUIREMENTS.md              … 要求事項の台帳（requirements/process.yaml）の一覧
      TESTS.md                     … テストの一覧と、保存データ単体で実行した結果
      ENVIRONMENT.md               … Python・ライブラリ・OS コマンドの版、必要な環境変数の「名前」
      （--docs で渡した手書き資料: HANDOFF.md・DESIGN.md・DECISIONS.md・PROGRESS.md など）
  snapshots/<名前>.tar.gz           … 上の資料 + ソース一式（source/）をまとめたアーカイブ

秘密情報（API キー・トークン）は含めない。実データ（work/、config.yaml、.env）は Git に無いので含まれない。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = (re.compile(r"(^|/)work/"), re.compile(r"(^|/)config\.yaml$"), re.compile(r"(^|/)\.env($|\.(?!example$))"),
             re.compile(r"style_profile.*\.json$|profile\.json$"), re.compile(r"\.pdf$"))


def git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def export_tree(ref: str, dest: Path) -> None:
    data = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        t.extractall(dest, filter="data")
    # 以前の保存データは入れ子にしない（各保存データは単体で完結させる）
    shutil.rmtree(dest / "snapshots", ignore_errors=True)


def manifest(ref: str, name: str, src: Path) -> dict:
    files = []
    for p in sorted(src.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src).as_posix()
            if any(f.search(rel) for f in FORBIDDEN):
                raise SystemExit(f"保存してはいけないファイルが含まれています: {rel}")
            files.append({"path": rel, "bytes": p.stat().st_size,
                          "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    commit = git("rev-parse", ref).strip()
    return {"name": name, "commit": commit, "commit_subject": git("log", "-1", "--format=%s", commit).strip(),
            "commit_date": git("log", "-1", "--format=%cI", commit).strip(),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": "Git で管理しているファイル一式（ソース・設定・ワークフロー・テスト・文書・要求事項の台帳）。"
                     "実データ・解析結果・秘密情報は含まない",
            "files": files}


def _sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return f"{fn.name}({ast.unparse(fn.args)})"
    except Exception:
        return fn.name + "(...)"


def _first(doc: str | None) -> str:
    return (doc or "").strip().splitlines()[0] if (doc or "").strip() else ""


def modules_md(src: Path) -> str:
    out = ["# モジュール一覧（ソースから自動生成）", "",
           "各モジュールの先頭の説明（docstring）と、トップレベルのクラス・関数（引数と説明の1行目）。", ""]
    for p in sorted((src / "src").rglob("*.py")):
        rel = p.relative_to(src).as_posix()
        text = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            out += [f"## `{rel}`", f"構文エラー: {e}", ""]
            continue
        out += [f"## `{rel}`（{len(text.splitlines())} 行）", ""]
        doc = ast.get_docstring(tree)
        if doc:
            out += ["```text", doc.strip(), "```", ""]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(f"- `{_sig(node)}` — {_first(ast.get_docstring(node))}")
            elif isinstance(node, ast.ClassDef):
                out.append(f"- class `{node.name}` — {_first(ast.get_docstring(node))}")
                for m in node.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("__"):
                        out.append(f"  - `{_sig(m)}` — {_first(ast.get_docstring(m))}")
            elif isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
                v = ast.unparse(node.value)
                out.append(f"- 定数 `{', '.join(t.id for t in node.targets)}` = `{v[:160]}{'…' if len(v) > 160 else ''}`")
        out.append("")
    return "\n".join(out)


def verbatim_md(title: str, src: Path, globs: list[str], lang: str) -> str:
    out = [f"# {title}", ""]
    for g in globs:
        for p in sorted(src.glob(g)):
            out += [f"## `{p.relative_to(src).as_posix()}`", "", f"```{lang}", p.read_text(encoding="utf-8").rstrip(), "```", ""]
    return "\n".join(out)


def requirements_md(src: Path) -> str:
    import yaml
    f = src / "requirements" / "process.yaml"
    if not f.exists():
        return "# 要求事項\n\n台帳がありません。\n"
    d = yaml.safe_load(f.read_text(encoding="utf-8"))
    out = ["# 要求事項の台帳（requirements/process.yaml の全項目）", "",
           "出典（利用者の指示）→ 項目。訂正された項目は superseded_by、矛盾は conflicts に双方を残している。"
           "工程: reading（読み取り）/ semantic（理解・統合）/ analysis（解析の完了時）/ excel / report。", "",
           "## 出典", "", "| ID | 日時 | 要旨 |", "|---|---|---|"]
    srcs = d.get("sources") or {}
    for sid, s in (srcs.items() if isinstance(srcs, dict) else enumerate(srcs)):
        out.append(f"| {sid} | {s.get('ts', '')} | {str(s.get('title', '')).replace('|', '／')} |")
    out += ["", f"## 項目（{len(d.get('requirements') or [])} 件）", "",
            "| ID | 出典 | 工程 | 種類 | 内容 | 確認方法 | 備考 |", "|---|---|---|---|---|---|---|"]
    cell = lambda v: (", ".join(map(str, v)) if isinstance(v, list) else str(v or "")).replace("|", "／").replace("\n", " ")
    for it in d.get("requirements") or []:
        note = "; ".join(f"{k}: {cell(it[k])}" for k in ("status", "superseded_by", "supersedes", "supersedes_partly",
                                                          "conflicts", "note") if it.get(k))
        out.append(f"| {it.get('id')} | {cell(it.get('src'))} | {cell(it.get('stage'))} | {cell(it.get('kind'))} | "
                   f"{cell(it.get('text'))} | {cell(it.get('verify'))} | {cell(note)} |")
    return "\n".join(out) + "\n"


def tests_md(src: Path, result: str | None) -> str:
    out = ["# テスト", "", "実行: `python3 -m pytest -q`（ダミーデータを自動生成。`-m \"not slow\"` で短縮）。", ""]
    if result is not None:
        out += ["## 保存データ単体での実行結果", "", "保存データのソース（source/）だけを別の一時フォルダに展開し、"
                "`PYTHONPATH=src python3 -m pytest -q -m \"not slow\"` を実行した結果の末尾:", "", "```text",
                result.strip()[-3000:], "```", ""]
    out += ["## テストの一覧", ""]
    for p in sorted((src / "tests").glob("test_*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        out += [f"### `{p.relative_to(src).as_posix()}`", ""]
        doc = ast.get_docstring(tree)
        if doc:
            out += [_first(doc), ""]
        for n in tree.body:
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test"):
                out.append(f"- `{n.name}` — {_first(ast.get_docstring(n))}")
        out.append("")
    return "\n".join(out)


def environment_md(src: Path) -> str:
    def ver(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.splitlines()[0]
        except Exception:
            return "（この環境には無い）"
    pk = []
    for mod in ("yaml", "dropbox", "fitz", "openpyxl", "PIL", "numpy", "anthropic", "faster_whisper", "pytest", "reportlab"):
        try:
            m = __import__(mod)
            pk.append(f"| {mod} | {getattr(m, '__version__', getattr(m, 'VersionBind', '?'))} |")
        except Exception:
            pk.append(f"| {mod} | （この環境には無い） |")
    envs = set()
    pat = re.compile(r"""(?:environ(?:\.get)?\(|environ\[|getenv\()\s*["']([A-Z][A-Z0-9_]+)["']""")
    for p in (src / "src").rglob("*.py"):
        envs |= set(pat.findall(p.read_text(encoding="utf-8")))
    for p in (src / ".github" / "workflows").glob("*.yml"):
        envs |= set(re.findall(r"secrets\.([A-Z][A-Z0-9_]+)", p.read_text(encoding="utf-8")))
    return "\n".join([
        "# 実行環境", "",
        f"保存データを作った環境: Python {platform.python_version()}（{platform.system()} {platform.machine()}）。"
        "GitHub Actions は ubuntu-latest・Python 3.12（.github/workflows/*.yml の setup-python を参照）。", "",
        "## Python ライブラリ（requirements.txt / requirements-optional.txt）", "", "| モジュール | 版 |", "|---|---|", *pk, "",
        "## OS のコマンド", "", f"- ffmpeg: {ver(['ffmpeg', '-version'])}", f"- ffprobe: {ver(['ffprobe', '-version'])}",
        f"- tesseract: {ver(['tesseract', '--version'])}", "",
        "## 必要な環境変数・GitHub Secrets（名前だけ。値は保存していない）", "",
        *[f"- `{e}`" for e in sorted(envs)], "",
        "値の登録方法は docs/SETUP_DROPBOX.md（手順A）・docs/SETUP_AI.md を参照。値をチャット・ログ・コードに出さない。", "",
        "## 環境構築", "", "```bash", "sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-jpn   # OS のコマンド",
        "python3 -m pip install -r requirements.txt -r requirements-optional.txt", "python3 -m pip install -e .",
        "python3 -m pytest -q -m \"not slow\"", "```", ""])


def run_tests(src: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "source"
        shutil.copytree(src, work)
        env = dict(os.environ, PYTHONPATH=str(work / "src"))
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-m", "not slow", "-p", "no:cacheprovider"],
                           cwd=work, env=env, capture_output=True, text=True, timeout=3600)
        return r.stdout[-6000:] + r.stderr[-2000:]


def compare(root: Path, before: str, after: str) -> str:
    """2 つの保存データの差分（ファイルの追加・削除・変更、行数、要求事項の追加）を機械的に書く（AI の文章生成を使わない）。"""
    import difflib
    import yaml
    mb = json.loads((root / before / "MANIFEST.json").read_text(encoding="utf-8"))
    ma = json.loads((root / after / "MANIFEST.json").read_text(encoding="utf-8"))
    fb = {f["path"]: f["sha256"] for f in mb["files"]}
    fa = {f["path"]: f["sha256"] for f in ma["files"]}
    added = sorted(set(fa) - set(fb))
    removed = sorted(set(fb) - set(fa))
    changed = sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p])
    with tempfile.TemporaryDirectory() as td:
        rows = []
        tb, ta = Path(td) / "b", Path(td) / "a"
        for tgz, dest, name in ((root / f"{before}.tar.gz", tb, before), (root / f"{after}.tar.gz", ta, after)):
            with tarfile.open(tgz) as t:
                t.extractall(dest, filter="data")
        for p in changed + added + removed:
            old = (tb / before / "source" / p)
            new = (ta / after / "source" / p)
            try:
                lo = old.read_text(encoding="utf-8").splitlines() if old.exists() else []
                ln = new.read_text(encoding="utf-8").splitlines() if new.exists() else []
            except UnicodeDecodeError:
                rows.append(f"| `{p}` | （バイナリ） | |")
                continue
            d = list(difflib.unified_diff(lo, ln, lineterm="", n=0))
            plus = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
            minus = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
            rows.append(f"| `{p}` | +{plus} / −{minus} | {'追加' if p in added else '削除' if p in removed else '変更'} |")
        def req_ids(base):
            f = base / "source" / "requirements" / "process.yaml"
            d = yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}
            return {r["id"]: r for r in d.get("requirements", []) or []}, set((d.get("sources") or {}).keys())
        rb, sb = req_ids(tb / before)
        ra, sa = req_ids(ta / after)
    new_req = sorted(set(ra) - set(rb))
    lines = [f"# 保存データの機械的な比較: {before} → {after}", "",
             f"- {before}: コミット {mb['commit'][:7]}（{mb['commit_date']}）、{len(fb)} ファイル",
             f"- {after}: コミット {ma['commit'][:7]}（{ma['commit_date']}）、{len(fa)} ファイル",
             f"- 追加 {len(added)}・削除 {len(removed)}・変更 {len(changed)} ファイル", "",
             "## ファイルごとの変更（行数）", "", "| ファイル | 行 | 種類 |", "|---|---|---|", *rows, "",
             f"## 要求事項の台帳: 追加された出典 {sorted(sa - sb) or 'なし'}、追加された項目 {len(new_req)} 件", ""]
    lines += [f"- {i}: {ra[i].get('text', '')}" for i in new_req]
    out = root / f"DIFF-{before}-{after}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref")
    ap.add_argument("--name")
    ap.add_argument("--docs", default=str(ROOT / "docs" / "handoff"), help="手書き資料（*.md）のフォルダ。保存データにそのまま入れる")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="2 つの保存データの機械的な比較")
    ap.add_argument("--test", action="store_true", help="保存データ単体でテストを実行して結果を記録する")
    ap.add_argument("--out", default=str(ROOT / "snapshots"))
    a = ap.parse_args()
    out_root = Path(a.out)
    if a.compare:
        print(compare(out_root, *a.compare))
        return
    if not (a.ref and a.name):
        ap.error("--ref と --name が必要です")
    folder = out_root / a.name
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source"
        export_tree(a.ref, src)
        m = manifest(a.ref, a.name, src)
        folder.mkdir(parents=True, exist_ok=True)
        if a.docs:
            for p in sorted(Path(a.docs).glob("*.md")):
                shutil.copy2(p, folder / p.name)
        (folder / "MANIFEST.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        (folder / "MODULES.md").write_text(modules_md(src), encoding="utf-8")
        (folder / "CONFIG.md").write_text(verbatim_md("設定ファイル（全文）", src, ["configs/*.yaml", "config.example.yaml",
                                                                           "ops/requests/*.txt"], "yaml"), encoding="utf-8")
        (folder / "WORKFLOWS.md").write_text(verbatim_md("GitHub Actions のワークフロー（全文）", src,
                                                         [".github/workflows/*.yml"], "yaml"), encoding="utf-8")
        (folder / "REQUIREMENTS.md").write_text(requirements_md(src), encoding="utf-8")
        (folder / "TESTS.md").write_text(tests_md(src, run_tests(src) if a.test else None), encoding="utf-8")
        (folder / "ENVIRONMENT.md").write_text(environment_md(src), encoding="utf-8")
        bundle = Path(td) / a.name
        shutil.copytree(folder, bundle)
        shutil.copytree(src, bundle / "source")
        archive = out_root / f"{a.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            t.add(bundle, arcname=a.name)
    # 開けることと、必要なファイルが揃っていることを確かめる
    with tarfile.open(archive) as t:
        names = set(t.getnames())
    need = [f"{a.name}/{x}" for x in ("MANIFEST.json", "MODULES.md", "REQUIREMENTS.md", "TESTS.md", "ENVIRONMENT.md",
                                      "source/src/structlab/cli.py", "source/requirements/process.yaml")]
    missing = [x for x in need if x not in names]
    want = {f"{a.name}/source/{f['path']}" for f in m["files"]}
    print(json.dumps({"folder": str(folder), "archive": str(archive), "bytes": archive.stat().st_size,
                      "entries": len(names), "source_files": len(m["files"]), "missing_required": missing,
                      "missing_source": sorted(want - names)[:20], "commit": m["commit"]}, ensure_ascii=False, indent=1))
    if missing or (want - names):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
