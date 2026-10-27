"""tools/restore_dump.py: 全文ダンプからのファイル復元。"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("restore_dump", Path(__file__).parents[1] / "tools" / "restore_dump.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)
SEP = "=" * 100


def block(path, body):
    return f"{SEP}\n# ファイル: {path}\n{SEP}\n{body}\n"


def test_restore_paths_contents_and_notes(tmp_path):
    body_with_sep_like = f'x = "{SEP}"\n# ファイル: これは本文\nprint(x)'  # 3 行組でなければ区切りではない
    dump = "# 見出し\n\n" + block("a/b.py", body_with_sep_like) + block("c.md  ※注記つき", "hello") + block("a/b.py", "dup")
    (tmp_path / "d.txt").write_text(dump, encoding="utf-8")
    out = tmp_path / "out"
    assert rd.main([str(tmp_path / "d.txt"), str(out), "--manifest", str(tmp_path / "m.json")]) == 0
    assert (out / "a/b.py").read_text(encoding="utf-8") == body_with_sep_like + "\n"
    assert (out / "c.md").read_text(encoding="utf-8") == "hello\n"
    import json
    m = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert m["duplicates"] == [{"path": "a/b.py", "same": False}]
    assert next(r for r in m["restored"] if r["path"] == "c.md")["note"] == "注記つき"


def test_never_overwrites_and_rejects_unsafe(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("orig", encoding="utf-8")
    (tmp_path / "d.txt").write_text(block("keep.txt", "new") + block("../evil.txt", "x"), encoding="utf-8")
    rd.main([str(tmp_path / "d.txt"), str(tmp_path / "out"), "--manifest", str(tmp_path / "m.json")])
    assert (tmp_path / "out" / "keep.txt").read_text(encoding="utf-8") == "orig"
    assert not (tmp_path / "evil.txt").exists()
