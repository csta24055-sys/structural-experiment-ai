"""費用を下げる候補の品質比較（採用の判断）。"""
from __future__ import annotations

from structlab import pilot as PL


def _dirs(*items):
    return PL.semantic_items([{"class": c, "content": t} for c, t in items])


def test_candidate_adopted_only_when_as_good_as_baseline_rerun():
    base = _dirs(("prohibited", "連続コピーはしない"), ("mandatory", "軸ラベルを書く"), ("request", "ショートカットを覚えて"))
    repeat = _dirs(("prohibited", "連続コピーはしない"), ("mandatory", "軸ラベルを書くこと"))          # 揺れ: 要望を1件落とす
    same = _dirs(("prohibited", "連続コピーはしないこと"), ("mandatory", "軸ラベルを書く"))
    worse = _dirs(("mandatory", "軸ラベルを書く"), ("request", "ショートカットを覚えて"))              # 禁止事項を落とした
    assert PL.decide("semantic", base, repeat, same)["adopt"]
    r = PL.decide("semantic", base, repeat, worse)
    assert not r["adopt"] and r["critical_missing_candidate"] > r["critical_missing_repeat"]


def test_reading_items_catch_numbers_cells_formulas():
    ai = {"formulas": [{"plain": "δ = P / K", "latex": ""}], "operations": [{"target": "B3", "value": "=A3*2"}],
          "settings_shown": [{"value": "近似曲線: 線形"}], "text_blocks": [{"text": "最大荷重 97.2 kN"}]}
    it = PL.reading_items(ai)
    assert "97.2" in it["number"] and {"B3", "A3"} <= it["cell"] and it["formula"] and it["setting"]
    base = {k: set(v) for k, v in it.items()}
    cand = {k: set(v) for k, v in it.items()}
    cand["number"].discard("97.2")
    assert not PL.decide("reading", base, base, cand)["adopt"]       # 数値を1つでも落とせば不採用


def test_branch_pilot_reruns_only_the_verify_stage(tmp_path):
    """本番を照合の直前まで進め、見本の資料だけ DB を複製して照合から先を方式ごとに実行する（Mock）。
    本番の DB は変わらず、本番を再開すると照合から続く（前の段階をやり直さない）。"""
    import json
    from pathlib import Path
    from structlab.ai.mock_provider import MockProvider
    from structlab.db import DB
    from structlab.pilot import branch_run, compare
    from structlab.reading.semantic_bridge import run_semantic
    from tests.test_reading import make_cfg

    class BatchMock(MockProvider):
        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            return [self.analyze(r) for r in reqs]
    speech = ["グラフには必ず軸ラベルを書いてください", "データは消さないでください", "提出はPDFにしてください",
              "単位は必ず書くこと", "図番号を付けてください"]
    recs = {f"v{n}.mp4": {"path": f"v{n}.mp4", "kind": "video", "duration": 400.0, "has_audio": True, "content_hash": f"h{n}",
                          "transcriber": "x", "scenes": [{"index": 0, "start": 0, "end": 400, "frame_time": 1, "frame": "",
                                                          "mode": "slides", "ai": {"title": "t", "text_blocks": []}}],
                          "speech": [{"start": 10.0 + 70 * k, "end": 14.0 + 70 * k, "text": s} for k, s in enumerate(speech)]}
            for n in range(2)}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"].update(ai=True, stop_before="verify")
    prov = BatchMock()
    run_semantic(cfg, tmp_path / "prod", recs, prov, log=lambda m: None)
    db = DB(tmp_path / "prod" / "semantic.db")
    assert not db.q("SELECT 1 FROM sem_steps WHERE step LIKE 'verify:%'")
    assert {r["step"] for r in db.q("SELECT step FROM sem_docs WHERE mode='ai'")} == {"verify"}
    db.close()
    before = Path(tmp_path / "prod" / "semantic.db").read_bytes()
    a = branch_run(cfg, tmp_path / "prod" / "semantic.db", recs["v0.mp4"], None, tmp_path / "rep", prov, log=lambda m: None)
    n0 = len(prov.calls)
    b = branch_run(cfg, tmp_path / "prod" / "semantic.db", recs["v0.mp4"], "C1v+C4", tmp_path / "cand", prov, log=lambda m: None)
    tasks = [c.task for c in prov.calls[n0:]]
    assert tasks and set(tasks) <= {"directive_verify", "directive_verify_group"}   # 照合だけを実行した
    assert Path(tmp_path / "prod" / "semantic.db").read_bytes() == before       # 本番の DB はそのまま
    res = compare("semantic", a, a, b)
    assert {"adopt", "containment_candidate", "critical_missing_candidate"} <= set(res)
    cfg.data["semantic"]["stop_before"] = None                                  # 本番を再開: 照合から続く
    n1 = len(prov.calls)
    run_semantic(cfg, tmp_path / "prod", recs, prov, log=lambda m: None)
    assert not any(c.task in ("scene_analysis", "directive_extraction") for c in prov.calls[n1:])


def test_branch_compare_reuses_baseline_as_production(tmp_path):
    """比較の今の方式の 1 回目は本番の結果に使う: 本番を再開しても見本の資料の照合は送り直さない。"""
    import json
    from structlab.ai.mock_provider import MockProvider
    from structlab.pilot import branch_compare
    from structlab.reading.semantic_bridge import run_semantic
    from tests.test_reading import make_cfg

    class BatchMock(MockProvider):
        def analyze_batch(self, reqs, log=print, known=None, on_created=None):
            return [self.analyze(r) for r in reqs]
    recs = {f"v{n}.mp4": {"path": f"v{n}.mp4", "kind": "video", "duration": 300.0, "has_audio": True, "content_hash": f"h{n}",
                          "transcriber": "x", "scenes": [{"index": 0, "start": 0, "end": 300, "frame_time": 1, "frame": "",
                                                          "mode": "slides", "ai": {"title": "t", "text_blocks": []}}],
                          "speech": [{"start": 20.0 + 90 * k, "end": 24.0 + 90 * k, "text": f"手順{k}は必ず守ってください"}
                                     for k in range(3)]} for n in range(2)}
    cfg = make_cfg(tmp_path, tmp_path)
    cfg.data["semantic"].update(ai=True, stop_before="verify")
    prov = BatchMock()
    run_semantic(cfg, tmp_path / "prod", recs, prov, log=lambda m: None)
    res = branch_compare(cfg, tmp_path / "prod" / "semantic.db", recs["v0.mp4"], "C4", tmp_path / "pilot", prov,
                         log=lambda m: None)
    assert json.loads((tmp_path / "pilot" / "pilot_decision.json").read_text(encoding="utf-8"))["arm"] == "C4"
    assert res["sample"] == "v0.mp4"
    cfg.data["semantic"]["stop_before"] = None
    n = len(prov.calls)
    run_semantic(cfg, tmp_path / "prod", recs, prov, log=lambda m: None)
    again = [c.source_label for c in prov.calls[n:] if c.task.startswith("directive_verify")]
    assert again and not any(lbl.startswith("v0.mp4") for lbl in again)   # 見本の資料の照合は送り直さない


def test_reinterpret_delta_candidate_rebuilds_the_same_analysis(tmp_path):
    """候補C5（再解釈で変わった項目だけ返す）: 保存される場面の解釈・変更点・指示は、全体を書き直させる今の方式と同じになる（Mock）。
    実際の AI での同等性は有料の比較（structlab pilot branch --arm C5）で確かめるまで既定では使わない。"""
    import json
    from structlab.ai.mock_provider import MockProvider
    from structlab.db import DB
    from structlab.reading.semantic_bridge import run_semantic
    from tests.test_reading import make_cfg
    speech = ["グラフには必ず軸ラベルを書いてください", "さっきの説明を訂正します。軸ラベルは英語で書いてください",
              "データは消さないでください", "提出はPDFにしてください"]
    rec = {"path": "v.mp4", "kind": "video", "duration": 520.0, "has_audio": True, "content_hash": "h", "transcriber": "x",
           "scenes": [{"index": 0, "start": 0, "end": 520, "frame_time": 1, "frame": "", "mode": "slides",
                       "ai": {"title": "t", "text_blocks": []}}],
           "speech": [{"start": 10.0 + 125 * k, "end": 14.0 + 125 * k, "text": s} for k, s in enumerate(speech)]}
    out = {}
    for mode in ("full", "delta"):
        cfg = make_cfg(tmp_path, tmp_path)
        cfg.data["semantic"].update(ai=True, reinterpret_output=mode)
        prov = MockProvider()
        run_semantic(cfg, tmp_path / mode, {"v.mp4": rec}, prov, batch=False, log=lambda m: None)
        db = DB(tmp_path / mode / "semantic.db")
        scenes = [(r["unit_id"], r["pass"], r["analysis_json"], r["changes_json"])
                  for r in db.q("SELECT * FROM sem_scenes ORDER BY unit_id, pass")]
        db.close()
        d = json.loads((tmp_path / mode / "semantic" / "directives.json").read_text(encoding="utf-8"))
        out[mode] = (scenes, sorted((x["content"], x["class"], x["verification"]) for x in d),
                     {c.task for c in prov.calls})
    assert out["full"][0] == out["delta"][0] and len(out["full"][0]) > 4
    assert out["full"][1] == out["delta"][1]
    assert "scene_reinterpret_delta" in out["delta"][2] and "scene_reinterpret" not in out["delta"][2]
