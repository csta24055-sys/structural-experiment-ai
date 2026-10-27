"""試験対策: 合成データでの全工程（発見 → Box → 読み取り → 判定 → 教材 → 差分更新・再開）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import exam_synth
from structlab.exam import box as B
from structlab.exam import cli
from structlab.exam.pipeline import Options, run

SECRET = "BOXSECRET-0123456789"
FAKE_HTML = ('<script>{"typedID":"f_7","type":"file","id":7,"description":"","extension":"mp4","name":"04.mp4",'
             '"itemSize":1000,"canDownload":false,"can_download":false,"can_preview":true}</script>')


@pytest.fixture
def fake_box(monkeypatch):
    calls = []

    def browser(url, **k):
        calls.append(url)
        return {"ok": True, "html": FAKE_HTML, "text": "04.mp4\n0:00\n/\n1:30:00", "title": "04.mp4 | Powered by Box",
                "final_url": url}

    monkeypatch.setattr(B, "browser_probe", browser)
    monkeypatch.setattr(B, "http_probe", lambda url, **k: {"ok": True, "status": 200})
    # 大学の共有リンクは個人の Box トークンでは参照できない場合がある（API では見つからない）
    monkeypatch.setattr(B, "api_probe", lambda url, token, **k: {"ok": False, "error_kind": "not_found", "status": 404})
    monkeypatch.setenv("BOX_ACCESS_TOKEN", SECRET)
    return calls


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("exam")
    exam_synth.build(root / "input", video=bool(shutil.which("ffmpeg")))
    return root


def opts(root: Path, **kw) -> Options:
    return Options(input_dir=root / "input", work=root / "work", out=root / "out", log=lambda *a: None, **kw)


def pdf_text(p: Path) -> str:
    import pymupdf
    with pymupdf.open(str(p)) as d:
        return "".join(pg.get_text() for pg in d).replace("\n", "")


def all_text(root: Path) -> str:
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".csv", ".jsonl", ".txt"):
            out.append(p.read_text(encoding="utf-8", errors="replace"))
        elif p.is_file() and p.suffix == ".pdf":
            out.append(pdf_text(p))
    return "\n".join(out)


def test_full_run(synth, fake_box):
    rep = run(opts(synth))
    env = rep["subjects"]["環境工学Ⅳ"]
    base = synth / "out" / "科目一覧" / "環境工学Ⅳ" / "試験対策"
    # 教材一式（22-3）
    for name in ["最重要事項", "徹底対策資料", "公式一覧", "予想問題", "解答用紙", "解答解説"]:
        assert (base / f"{name}.pdf").exists() and (base / f"{name}.docx").exists()
    for n in (1, 2, 3):
        for part in ("問題", "解答用紙", "解答解説"):
            assert (base / f"模擬テスト第{n}回" / f"{part}.pdf").exists()
    assert env["mock_exams"] == 3 and env["quality_problems"] == [] and env["calc_verified"]
    led = json.loads((base / "出題根拠.json").read_text(encoding="utf-8"))
    g = {t["title"]: (t["grade"], t["grade_sub"]) for t in led["topics"]}
    assert g["鏡像音源法"][0] == "S"
    assert g["残響時間"][0] == "A"
    assert g["音の回折"] == ("C", "除外")
    assert g["吸音率"][0] == "B"
    assert led["scope_lectures"] == [1, 2, 3, 4, 5] and led["past_exam_years"] == [2023, 2024]
    rev = next(t for t in led["topics"] if t["title"] == "残響時間")
    assert rev["past_exam_years"] == [2023, 2024] and "%" not in json.dumps(rev, ensure_ascii=False)
    # 問題用紙に重要度を出さない／B・C の詳しい解説を混ぜない
    for n in (1, 2, 3):
        t = pdf_text(base / f"模擬テスト第{n}回" / "問題.pdf")
        assert "重要度" not in t and "S（" not in t and "仮設定" in t
    deep = pdf_text(base / "徹底対策資料.pdf")
    assert "鏡像音源法" in deep and "音の回折" not in deep and "吸音率" not in deep
    assert "音の回折" in pdf_text(base / "判定一覧.pdf")  # B・C は判定一覧に題名と理由だけ
    # 計算問題は sympy で検算済み・例題の値であることを明示
    sol = pdf_text(base / "解答解説.pdf")
    assert "両辺が一致" in sol and "システムが設定した例題の値" in sol


def test_box_and_unanalyzed_are_visible(synth, fake_box):
    run(opts(synth))
    box_csv = (synth / "out" / "Boxリンク管理" / "動画取得可否一覧.csv").read_text(encoding="utf-8-sig")
    assert "synthetictest0001" in box_csv and ",B," in box_csv and "不可" in box_csv
    led = json.loads((synth / "out" / "科目一覧" / "環境工学Ⅳ" / "試験対策" / "出題根拠.json").read_text("utf-8"))
    un = {u["name"]: u for u in led["unanalyzed"]}
    assert "第04回" in un and "再生のみ可" in un["第04回"]["why"] and un["第04回"]["lecture"] == 4
    assert "未解析" in pdf_text(synth / "out" / "科目一覧" / "環境工学Ⅳ" / "試験対策" / "最重要事項.pdf")


def test_duplicates_and_unclassified(synth, fake_box):
    rep = run(opts(synth))
    dups = rep["stages"]["discover"]["duplicates"]
    assert any("コピー" in d["duplicate"] for d in dups)
    un = json.loads((synth / "out" / "未分類・要確認" / "一覧.json").read_text("utf-8"))
    assert [u["name"] for u in un] == ["メモ.pdf"]
    assert "要確認" in rep["subjects"]
    # 別科目の内容が混ざらない
    rc = json.loads((synth / "out" / "科目一覧" / "鉄筋コンクリート構造Ⅱ" / "試験対策" / "出題根拠.json").read_text("utf-8"))
    assert not any("残響" in t["title"] for t in rc["topics"])


def test_secrets_never_reach_outputs(synth, fake_box):
    run(opts(synth))
    assert SECRET not in all_text(synth / "out") and SECRET not in all_text(synth / "work")


def test_rerun_reuses_readings_and_skips_unchanged(synth, fake_box, monkeypatch):
    run(opts(synth))
    from structlab.exam import analyze
    monkeypatch.setattr(analyze, "read_material", lambda *a, **k: pytest.fail("読み取り済みの資料を読み直した"))
    rep = run(opts(synth))
    assert rep["subjects"]["環境工学Ⅳ"]["rendered"] is False
    assert fake_box == [] or len(fake_box) <= 1  # Box の確認結果は一定時間再利用する


def test_new_material_updates_grade_and_history(tmp_path, fake_box):
    root = tmp_path
    exam_synth.build(root / "input", video=False)
    run(opts(root))
    # 後日の授業（第5回）で吸音率の出題が明言された
    (root / "input" / "環境工学Ⅳ" / "第05回_授業.vtt").write_text(exam_synth.vtt([
        (10, 20, "吸音率は試験に出します。必ず計算できるようにしてください。")]), encoding="utf-8")
    rep = run(opts(root))
    led = json.loads((root / "out" / "科目一覧" / "環境工学Ⅳ" / "試験対策" / "出題根拠.json").read_text("utf-8"))
    assert next(t for t in led["topics"] if t["title"] == "吸音率")["grade"] == "S"
    ch = rep["subjects"]["環境工学Ⅳ"]["history_changes"]
    assert {"topic": "吸音率", "from": "B", "to": "S"}.items() <= next(c for c in ch if c["topic"] == "吸音率").items()
    hist = (root / "work" / "history.jsonl").read_text("utf-8")
    assert "吸音率" in hist
    # 原本は変更しない
    assert (root / "input" / "環境工学Ⅳ" / "第03回_講義資料.pdf").exists()


def test_resume_after_interruption(tmp_path, fake_box, monkeypatch):
    exam_synth.build(tmp_path / "input", video=False)
    from structlab.exam import analyze
    real = analyze.read_material
    n = {"calls": 0}

    def crash_after_two(*a, **k):
        n["calls"] += 1
        if n["calls"] > 2:
            raise KeyboardInterrupt("途中停止")
        return real(*a, **k)

    monkeypatch.setattr(analyze, "read_material", crash_after_two)
    with pytest.raises(KeyboardInterrupt):
        run(opts(tmp_path))
    done = len(list((tmp_path / "work" / "readings").glob("*.json")))
    assert done == 2
    monkeypatch.setattr(analyze, "read_material", real)
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(analyze, "read_material", counting)
    rep = run(opts(tmp_path))
    assert rep["subjects"]["環境工学Ⅳ"]["mock_exams"] == 3
    total = len(list((tmp_path / "work" / "readings").glob("*.json")))
    assert calls["n"] == total - 2  # 読み取り済みの 2 件は読み直さない


def test_cli_requires_input_and_box_check(tmp_path, fake_box, capsys):
    assert cli.main(["run", "--work", str(tmp_path / "w"), "--out", str(tmp_path / "o")]) == 2
    u = tmp_path / "第04回.url"
    u.write_text("[InternetShortcut]\nURL=https://nihon-u.app.box.com/s/synthetictest0001\n", encoding="utf-8")
    assert cli.main(["box-check", str(u), "--work", str(tmp_path / "w"), "--json", str(tmp_path / "r.json")]) == 0
    r = json.loads((tmp_path / "r.json").read_text("utf-8"))
    assert r[0]["state"] == "B" and r[0]["name"] == "04.mp4"
    assert "[B] 再生のみ可" in capsys.readouterr().out


def test_dropbox_failure_is_reported(tmp_path, monkeypatch, capsys):
    for k in ("DROPBOX_APP_KEY", "DROPBOX_REFRESH_TOKEN", "DROPBOX_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DROPBOX_REFRESH_TOKEN_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("DROPBOX_APP_KEY_FILE", str(tmp_path / "none"))
    rc = cli.main(["run", "--dropbox-root", "/授業", "--work", str(tmp_path / "w"), "--out", str(tmp_path / "o")])
    assert rc == 3 and "Dropbox に接続できません" in capsys.readouterr().err


class FakeDropboxOut:
    """DropboxOutput の代わり（ローカルのフォルダに保存）。"""

    def __init__(self, root: Path):
        self.root = root

    def upload(self, local: Path, rel: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(local.read_bytes())

    def download_if_exists(self, rel: str, local: Path) -> bool:
        p = self.root / rel
        if not p.exists():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(p.read_bytes())
        return True


def test_dropbox_state_roundtrip_resumes_on_fresh_runner(tmp_path, fake_box, monkeypatch):
    """GitHub Actions では毎回まっさらな環境になる。Dropbox に置いた状態から続きを再開する。"""
    from structlab.exam import analyze, sync
    exam_synth.build(tmp_path / "input", video=False)
    dst = FakeDropboxOut(tmp_path / "dropbox")
    run(opts(tmp_path))
    n = sync.save_state(dst, tmp_path / "work")
    assert n > 0 and sync.upload_outputs(dst, tmp_path / "out") > 0
    assert (tmp_path / "dropbox" / "試験対策システム" / "科目一覧" / "環境工学Ⅳ" / "試験対策" / "最重要事項.pdf").exists()
    shutil.rmtree(tmp_path / "work")  # 新しい実行環境
    assert sync.restore_state(dst, tmp_path / "work")
    monkeypatch.setattr(analyze, "read_material", lambda *a, **k: pytest.fail("復元した読み取り結果を使っていない"))
    rep = run(opts(tmp_path))
    assert rep["subjects"]["環境工学Ⅳ"]["mock_exams"] == 3


def test_quiet_summary_hides_names_and_urls(synth, fake_box, capsys):
    rc = cli.main(["run", "--input", str(synth / "input"), "--work", str(synth / "work"), "--out",
                   str(synth / "out"), "--quiet", "--no-docx"])
    out = capsys.readouterr().out
    assert rc == 0 and '"subjects": 2' in out
    for secret_ish in ("環境工学", "鉄筋", "第03回", "box.com", "メモ", SECRET):
        assert secret_ish not in out
