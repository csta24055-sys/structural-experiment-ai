"""要求事項の台帳（利用者の指示）と照合の仕組み。"""
from __future__ import annotations

import json
from pathlib import Path

from structlab import requirements as R


def test_process_registry_is_complete_and_keeps_conflicts():
    d = R.load_process()
    ids = [r["id"] for r in d["requirements"]]
    assert len(ids) == len(set(ids))
    used = {s for r in d["requirements"] for s in r["src"]}
    assert set(d["sources"]) == used                     # どの指示（メッセージ）からも項目を抽出している
    by = {r["id"]: r for r in d["requirements"]}
    for r in d["requirements"]:
        assert r["text"] and r["stage"] and r["kind"]
        if r.get("status") == "superseded":
            assert r["superseded_by"] in by                # 置き換えられた指示も削除せず、置き換え先と結ぶ
        for c in r.get("conflicts", []):
            assert c["with"] in by and c["resolution"]     # 矛盾は双方を残す
    # 重要な指示が台帳にあること（例示）
    text = "\n".join(r["text"] for r in d["requirements"])
    for key in ("冒頭画面", "表面と裏面", "Dropbox には書き込まない", "エクセルの作り方", "未確認事項", "最上位モデル"):
        assert key in text


def test_stage_recheck_never_marks_success_without_evidence(tmp_path):
    items = R.checklist("excel", None)
    ids = {i["id"] for i in items}
    assert {"P061", "P065", "P066"} <= ids and all(i["evidence"]["status"] == "未検証" for i in items)
    ev = {"P061": {"status": "成功", "where": "Sheet1!B3:B40", "note": "再計算一致"},
          "P065": {"status": "成功", "where": "", "note": "場所の記録なし"}}
    rows = {r["id"]: r["status"] for r in R.verify_stage("excel", items, ev)}
    assert rows["P061"] == "成功" and rows["P065"] == "要確認" and rows["P066"] == "未検証"


def test_teacher_directives_enter_the_ledger_with_sources(tmp_path):
    sem = tmp_path / "semantic"
    sem.mkdir()
    (sem / "directives.json").write_text(json.dumps([
        {"id": 1, "content": "グラフの軸ラベルを必ず書くこと", "class": "mandatory", "class_label": "C. 必須事項",
         "source": "エクセルの作り方1/a.MP4", "time_label": "00:10:00–00:10:10", "page": None, "certainty": "high",
         "verification": "verified", "verification_label": "照合済み", "origin": "explicit", "speaker_label": "発言者未確認",
         "effective": "effective", "condition": None, "unconfirmed": None, "relations": [],
         "target_task": "グラフ作成", "deliverable": "Excel", "screen": "", "operation": ""},
        {"id": 2, "content": "考察は2ページ以内", "class": "mandatory", "class_label": "C. 必須事項",
         "source": "レポートの作り方1/b.MP4", "time_label": "00:01:00–00:01:05", "page": None, "certainty": "low",
         "verification": "unverified", "verification_label": "未照合", "origin": "explicit", "speaker_label": "発言者未確認",
         "effective": "unresolved_conflict", "condition": None, "unconfirmed": "聞き取りにくい", "relations": [],
         "target_task": "", "deliverable": "", "screen": "", "operation": ""}], ensure_ascii=False), encoding="utf-8")
    t = R.teacher_items(tmp_path)
    assert t[0]["stage"] == ["analysis", "excel"] and "report" in t[1]["stage"]
    rep = {i["id"] for i in R.checklist("report", tmp_path)}
    assert "T2" in rep and "T1" not in rep
