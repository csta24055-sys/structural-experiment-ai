"""G5 の作業フォルダの保全（scripts/g5_backup.py）: 追加だけの保存、作業フォルダが消えた後の復元、上書きしない、壊れた束を使わない。"""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from structlab.ai.base import AIRequest
from structlab.ai.queue_provider import Queue, QueueProvider

ROOT = Path(__file__).parent.parent


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


B = _mod("g5_backup")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "integer"}}, "required": ["ok"], "additionalProperties": False}


def _reqs(n):
    return [AIRequest(task="t", source_label=f"s{i}", text=f"本文{i}", system="指示", schema=SCHEMA) for i in range(n)]


def _answer_all(work):
    Q = Queue(work / "queue")
    for rid in Q.pending():
        (work / "queue" / "inbox" / f"{rid}.json").write_text(json.dumps({"data": {"ok": 1}}), encoding="utf-8")
    return Q.ingest()


def test_lost_work_folder_is_restored_and_answers_are_reused(tmp_path):
    work, rem = tmp_path / "g5", B.DirRemote(tmp_path / "remote")
    QueueProvider(work / "queue").analyze_batch(_reqs(3), log=lambda m: None)
    _answer_all(work)
    (work / "pg5").mkdir()
    (work / "pg5" / "decision.json").write_text('{"pass": true}', encoding="utf-8")   # 有料の結果も保存する
    r1 = B.push(work, rem)
    assert r1["bundled"] == 4 and B.push(work, rem)["bundled"] == 0                     # 変化がなければ束を作らない
    QueueProvider(work / "queue").analyze_batch(_reqs(4), log=lambda m: None)
    _answer_all(work)
    assert B.push(work, rem)["bundled"] == 1                                            # 増えた分だけ
    assert len(rem.names()) == 2 and B.status(work)["unsaved"] == 0
    before = {p.relative_to(work).as_posix(): p.read_bytes() for p in work.rglob("*")
              if p.is_file() and ("answers" in p.parts or "pg5" in p.parts)}

    shutil.rmtree(work)                                                                 # 作業環境の回収
    res = B.restore(work, rem)
    assert res["restored"] == 5 and not res["conflicts"] and not res["broken"]
    after = {k: (work / k).read_bytes() for k in before}
    assert after == before
    # 依頼は作り直す（同じ依頼は同じ番号）→ 保存してあった答えがそのまま使われ、答え直しは 0 件
    out = QueueProvider(work / "queue").analyze_batch(_reqs(4), log=lambda m: None)
    assert [o.data for o in out] == [{"ok": 1}] * 4 and not Queue(work / "queue").pending()
    assert B.push(work, rem)["bundled"] == 0                                            # 戻したものを二重に保存しない


def test_never_overwrites_remote_or_local(tmp_path):
    work, rem = tmp_path / "g5", B.DirRemote(tmp_path / "remote")
    QueueProvider(work / "queue").analyze_batch(_reqs(1), log=lambda m: None)
    _answer_all(work)
    name = B.push(work, rem)["bundle"]
    with pytest.raises(FileExistsError):
        rem.put(name, b"x")                                                             # 保存先は追加だけ
    ans = next((work / "queue" / "answers").glob("*.json"))
    ans.write_text('{"local": 1}', encoding="utf-8")                                    # 手元に別の版がある
    res = B.restore(work, rem)
    assert res["conflicts"] == [ans.relative_to(work).as_posix()] and ans.read_text(encoding="utf-8") == '{"local": 1}'


def test_broken_or_tampered_bundles_are_not_used(tmp_path):
    work, rem = tmp_path / "g5", B.DirRemote(tmp_path / "remote")
    QueueProvider(work / "queue").analyze_batch(_reqs(2), log=lambda m: None)
    _answer_all(work)
    name = B.push(work, rem)["bundle"]
    p = rem.root / name
    p.write_bytes(p.read_bytes()[:-10] + b"0" * 10)                                     # 書き換え
    shutil.rmtree(work)
    res = B.restore(work, rem)
    assert res["broken"] and res["restored"] == 0 and not (work / "queue" / "answers").exists()


def test_test_only_mark_survives_restore(tmp_path):
    g5 = _mod("g5_driver")
    work, rem = tmp_path / "g5", B.DirRemote(tmp_path / "remote")
    (work).mkdir()
    (work / g5.TEST_MARK).write_text("x", encoding="utf-8")
    B.push(work, rem)
    shutil.rmtree(work)
    B.restore(work, rem)
    assert (work / g5.TEST_MARK).exists()                                               # 仮の答えのフォルダは復元後も本番に使えない


def test_dropbox_remote_adds_only_and_stays_outside_source(tmp_path):
    import dropbox

    class Fake:
        def __init__(self):
            self.calls = []

        def files_upload(self, data, path, mode, autorename, mute):
            self.calls.append((path, mode, autorename))
    f = Fake()
    r = B.DropboxRemote(client=f)
    r.put("g5-00001-0123456789ab.tar.xz", b"d")
    path, mode, auto = f.calls[0]
    assert path.startswith("/鋼材実験レポート用_解析結果/") and mode == dropbox.files.WriteMode.add and auto is False
    with pytest.raises(Exception):
        B.DropboxRemote(root="/鋼材実験レポート用/g5_backup", client=f)                  # 元資料のフォルダの中は拒否


def test_driver_step_backs_up_when_configured(tmp_path, monkeypatch):
    g5 = _mod("g5_driver")
    work = tmp_path / "g5"
    monkeypatch.delenv("G5_BACKUP", raising=False)
    assert g5.backup_now(work) == {"enabled": False}
    monkeypatch.setenv("G5_BACKUP", f"dir:{tmp_path / 'remote'}")
    QueueProvider(work / "queue").analyze_batch(_reqs(1), log=lambda m: None)
    _answer_all(work)
    r = g5.backup_now(work)
    assert r["enabled"] and r["bundled"] == 1
    monkeypatch.setenv("G5_BACKUP", "dropbox")
    r = g5.backup_now(work)                                                             # 認証情報が無い等の失敗は記録（値は出さない）
    assert r["enabled"] and ("error" in r or "bundled" in r)


def test_restore_into_a_different_folder_uses_the_answers(tmp_path):
    """作業フォルダの場所が変わっても（新しい環境）、戻した答えが使われる。M27 までの指紋（絶対パス入り）の答えも保存時に直す。"""
    from PIL import Image
    from structlab.ai.base import AIImage
    from structlab.ai.queue_provider import record_digest

    def reqs(base):
        img = base / "files" / "doc" / "p.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (30, 20), "white").save(img)
        return [AIRequest(task="t", source_label="s", text="本文", system="指示", schema=SCHEMA,
                          images=[AIImage(img, "画面", lossless=True)])]
    a, b, rem = tmp_path / "a" / "g5", tmp_path / "b" / "g5", B.DirRemote(tmp_path / "remote")
    QueueProvider(a / "queue").analyze_batch(reqs(a), log=lambda m: None)
    _answer_all(a)
    ans = next((a / "queue" / "answers").glob("*.json"))
    rec = json.loads((a / "queue" / "requests" / ans.name).read_text(encoding="utf-8"))
    old = json.loads(ans.read_text(encoding="utf-8"))
    old["record_sha256"] = record_digest(rec, legacy=True)            # M27 までの形の答え
    ans.write_text(json.dumps(old), encoding="utf-8")
    assert QueueProvider(a / "queue").analyze_batch(reqs(a), log=lambda m: None)[0].data == {"ok": 1}
    B.push(a, rem)
    res = B.restore(b, rem)
    assert res["restored"] == 1 and not res["conflicts"]
    out = QueueProvider(b / "queue").analyze_batch(reqs(b), log=lambda m: None)   # 別の場所で依頼を作り直す
    assert out[0].data == {"ok": 1} and not Queue(b / "queue").pending()
    assert B.restore(a, rem)["conflicts"] == []                                    # 元の場所に戻しても衝突にしない
