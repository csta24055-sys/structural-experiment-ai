"""ファイル区切り付き全文ダンプから元の相対パスとファイル内容を復元する。

区切りは「= が 100 個の行 / `# ファイル: <path>` / = が 100 個の行」の 3 行組。
本文中に同じ形の 3 行組が現れない限り誤認しない（3 行がそろったときだけ区切りとみなす）。
既存ファイルは上書きしない（--force 指定時のみ）。復元結果は manifest（パス・行数・sha256）に記録する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SEP = "=" * 100
PREFIX = "# ファイル: "


def parse(text: str) -> list[tuple[str, str, str]]:
    lines = text.split("\n")
    marks: list[tuple[int, str, str]] = []
    i = 0
    while i + 2 < len(lines):
        if lines[i] == SEP and lines[i + 1].startswith(PREFIX) and lines[i + 2] == SEP:
            raw = lines[i + 1][len(PREFIX):].strip()
            # 「path  ※注記」形式の注記はパスに含めない（注記は manifest に残す）
            path, _, note = raw.partition("  ※")
            marks.append((i, path.strip(), note.strip()))
            i += 3
        else:
            i += 1
    files = []
    for k, (start, path, note) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = lines[start + 3:end]
        # ダンプ側が各ファイルの後ろに入れた区切り用の空行を 1 行だけ除く
        if body and body[-1] == "":
            body = body[:-1]
        content = "\n".join(body)
        if content and not content.endswith("\n"):
            content += "\n"
        files.append((path, content, note))
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--manifest")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    files = parse(Path(a.dump).read_text(encoding="utf-8"))
    out = Path(a.out)
    seen: dict[str, str] = {}
    records, skipped, dups = [], [], []
    for path, content, note in files:
        if path.startswith("/") or ".." in Path(path).parts:
            skipped.append({"path": path, "reason": "unsafe path"})
            continue
        h = hashlib.sha256(content.encode()).hexdigest()
        if path in seen:
            dups.append({"path": path, "same": seen[path] == h})
            continue
        seen[path] = h
        dest = out / path
        if dest.exists() and not a.force:
            skipped.append({"path": path, "reason": "exists"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        records.append({"path": path, "lines": content.count("\n"), "sha256": h, **({"note": note} if note else {})})
    manifest = {"source": Path(a.dump).name, "restored": records, "skipped": skipped, "duplicates": dups}
    if a.manifest:
        Path(a.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"restored={len(records)} skipped={len(skipped)} duplicates={len(dups)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
