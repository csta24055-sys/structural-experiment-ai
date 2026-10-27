"""資料をまたぐ整理（読み取りの最後に行う）。結果は reading/ の下に保存する（Dropbox には書き込まない）。

1. materials.md / materials.json: 全ファイルの一覧。内容が完全に同一のファイルは代表の結果を使い、別資料として記録する。
   似ている資料（同じ種類で内容の一部が共通）は、共通部分と違う部分（時刻の範囲）を記録する
2. experiment_teacher.md / .json: 先生の実験経過動画を、役割の順（1→2）に有効区間だけつないだ時間軸。
   録画の切れ目は「記録されていない時間」として明示し、補完しない。末尾の除外（冒頭画面に戻った部分）の時刻と根拠を記録
3. experiment_student.md / .json: 生徒の実験動画の区間ごとの観察（表面・裏面、ひび割れ、操作）と面の切り替わり
4. experiment_correspondence.md / .json: 先生と生徒の動画の対応（撮影時刻ではなく、試験体の変化・操作から判断。AI許可時のみ）

  python -m structlab.reading.corpus <読み取り結果のフォルダ>... --out <出力先> [-c 設定] [--allow-external-ai]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..ai.base import AIRequest
from ..ai.base import AwaitingAnswer
from ..models import fmt_time
from .verify import norm, similarity

CORR_SYSTEM = """あなたは鉄筋コンクリート梁の加力実験について、同じ実験を別々に撮影した2つの記録（先生の動画・生徒の動画）を
照らし合わせる担当者です。撮影の開始時刻や動画の経過時間が同じだからといって対応させてはいけません。
試験体の状態（ひび割れの出現・進展・破壊）、加力の段階、計測器の表示、人の操作など、記録された事実が一致するものだけを対応させ、
根拠を具体的に書いてください。先生の動画は試験体の表面だけ、生徒の動画は表面と裏面を行き来して撮影しています。
裏面だけを映している区間は、表面の変化と直接は比べられないので、前後の表面の区間や加力の段階から判断し、確実性を下げてください。
判断できない区間は無理に対応させず unmatched に入れてください。"""

CORR_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pairs", "unmatched_student", "notes"],
    "properties": {
        "pairs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["student_from", "student_to", "teacher_from", "teacher_to", "evidence", "confidence"],
            "properties": {"student_from": {"type": "string"}, "student_to": {"type": "string"},
                           "teacher_from": {"type": "string"}, "teacher_to": {"type": "string"},
                           "evidence": {"type": "string"},
                           "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}},
        "unmatched_student": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
}


def _speech_text(rec: dict) -> str:
    return "".join(s["text"] for s in rec.get("speech", []))


def _segment_coverage(a: dict, b: dict) -> tuple[float | None, list[list[float]]]:
    """a の発話のうち b にも（ほぼ同じ文で）ある割合と、b に無い a の時刻範囲。"""
    sa, sb = a.get("speech", []), b.get("speech", [])
    if not sa or not sb:
        return None, []
    pool = [norm(x["text"]) for x in sb]
    missing, hit = [], 0
    for s in sa:
        t = norm(s["text"])
        if len(t) < 6:
            continue
        ok = any(t in p or p in t or similarity(t, p) >= 0.5 for p in pool if p)
        hit += ok
        if not ok:
            if missing and s["start"] - missing[-1][1] < 15:
                missing[-1][1] = round(s["end"], 1)
            else:
                missing.append([round(s["start"], 1), round(s["end"], 1)])
    n = sum(1 for s in sa if len(norm(s["text"])) >= 6)
    return (hit / n if n else None), missing


def compare_materials(items: list, records: dict[str, dict]) -> dict:
    mats = [i for i in items if i.role == "material"]
    dup = [{"path": i.path, "same_content_as": i.canonical, "note": i.note} for i in items if i.role == "duplicate"]
    similar = []
    for x in range(len(mats)):
        for y in range(x + 1, len(mats)):
            a, b = mats[x], mats[y]
            ra, rb = records.get(a.path), records.get(b.path)
            if not ra or not rb or ra["kind"] != rb["kind"]:
                continue
            if ra["kind"] == "pdf":
                sim = similarity("".join(p["text_layer"] for p in ra["pages"]), "".join(p["text_layer"] for p in rb["pages"]))
                if sim >= 0.3:
                    similar.append({"a": a.path, "b": b.path, "text_similarity": round(sim, 3),
                                    "pages": [ra["n_pages"], rb["n_pages"]]})
                continue
            if (ra.get("role") or {}).get("kind") == "experiment" or (rb.get("role") or {}).get("kind") == "experiment":
                continue  # 実験経過の動画は冒頭が似ていても別の記録（先生・生徒の対応は別に整理する）
            ta, tb = _speech_text(ra), _speech_text(rb)
            sim = similarity(ta, tb) if ta and tb else 0.0
            if sim < 0.15:
                continue
            cov_ab, only_a = _segment_coverage(ra, rb)
            cov_ba, only_b = _segment_coverage(rb, ra)
            similar.append({
                "a": a.path, "b": b.path, "speech_similarity": round(sim, 3),
                "durations_min": [round(ra["duration"] / 60, 1), round(rb["duration"] / 60, 1)],
                "a_covered_by_b": None if cov_ab is None else round(cov_ab, 3),
                "b_covered_by_a": None if cov_ba is None else round(cov_ba, 3),
                "only_in_a": only_a[:40], "only_in_b": only_b[:40],
                "note": "同じ講義の別の録画の可能性（発話の共通部分が多い）。違う部分は only_in_* の時刻範囲" if sim >= 0.4
                else "一部の内容が共通"})
    return {"materials": [{"path": i.path, "kind": i.kind, "size": i.size, "duplicates": i.duplicates} for i in mats],
            "duplicates": dup, "similar": similar,
            "others": [{"path": i.path, "role": i.role, "note": i.note} for i in items
                       if i.role not in ("material", "duplicate")]}


def _window_row(sc: dict) -> dict:
    ai = sc.get("ai") or {}
    return {"start": sc["start"], "end": sc["end"], "frame": sc.get("frame"),
            "face": ai.get("face"), "face_evidence": ai.get("face_evidence"), "view": ai.get("view"),
            "specimen_state": ai.get("specimen_state"), "cracks": ai.get("cracks", []), "loading": ai.get("loading"),
            "readings": ai.get("readings", []), "actions": ai.get("actions", []), "uncertain": ai.get("uncertain", []),
            "ai_model": sc.get("ai_model"), "read": bool(ai),
            "speech": " ".join(s["text"] for s in sc.get("speech", []))}


def teacher_timeline(records: dict[str, dict]) -> dict:
    vids = [r for r in records.values() if (r.get("role") or {}).get("sequence") == "teacher"]
    vids.sort(key=lambda r: (r["role"].get("order", 0), r["path"]))
    offset, parts, rows = 0.0, [], []
    for r in vids:
        valid = float(r.get("valid_end") or r["duration"])
        parts.append({"path": r["path"], "order": r["role"].get("order"), "duration": r["duration"],
                      "valid_end": valid, "timeline_from": round(offset, 2), "timeline_to": round(offset + valid, 2),
                      "trim": r.get("trim"), "audio": {k: r.get("audio", {}).get(k) for k in ("track_seconds",)}})
        for sc in r["scenes"]:
            if sc["start"] >= valid:
                continue
            row = _window_row(sc)
            row.update({"source": r["path"], "source_start": sc["start"], "source_end": min(sc["end"], valid),
                        "t_from": round(offset + sc["start"], 2), "t_to": round(offset + min(sc["end"], valid), 2)})
            rows.append(row)
        offset += valid
    gaps = [{"after": a["path"], "before": b["path"], "at": a["timeline_to"],
             "note": "録画の切れ目。この間の実験は記録されていない（時間の長さも不明。補完しない）"}
            for a, b in zip(parts, parts[1:])]
    return {"parts": parts, "gaps": gaps, "windows": rows, "total_seconds": round(offset, 2)}


def student_observations(records: dict[str, dict]) -> dict:
    out = []
    for r in records.values():
        if (r.get("role") or {}).get("sequence") != "student":
            continue
        rows = [_window_row(sc) for sc in r["scenes"]]
        changes, prev = [], None
        for w in rows:
            if w["face"] in ("front", "back", "both") and w["face"] != prev:
                changes.append({"at": w["start"], "to": w["face"], "evidence": w["face_evidence"]})
                prev = w["face"]
        out.append({"path": r["path"], "duration": r["duration"], "windows": rows, "face_changes": changes,
                    "trim": r.get("trim"), "note": "生徒の動画は番号による連結・末尾の除外を行っていない"})
    return {"videos": out}


def correspondence(teacher: dict, student: dict, provider, log=print, batch: bool = False) -> dict:
    if provider is None:
        return {"status": "not_run", "reason": "AI の使用が許可されていないため未実施（撮影時刻による対応付けは行わない）"}
    if not teacher["windows"] or not student["videos"]:
        return {"status": "not_run", "reason": "先生または生徒の実験動画の読み取り結果が無い"}
    if not any(w["read"] for w in teacher["windows"]):
        return {"status": "not_run", "reason": "先生の動画の画面の読み取り（AI）が無い"}

    def line(w, t0, t1, src):
        cr = "; ".join(f"{c['location']} {c['direction']} {c['change']}" for c in w["cracks"])[:300]
        rd = "; ".join(f"{x['what']}={x['value']}" for x in w["readings"])[:200]
        return (f"[{fmt_time(t0)}–{fmt_time(t1)}{src}] 面={w['face']} 状態={str(w['specimen_state'])[:200]} "
                f"ひび割れ={cr} 加力={str(w['loading'])[:120]} 表示={rd} 操作={'; '.join(w['actions'])[:150]}")
    tl = "\n".join(line(w, w["t_from"], w["t_to"], f" 元:{Path(w['source']).parent.name} {fmt_time(w['source_start'])}")
                   for w in teacher["windows"] if w["read"])
    gap = "\n".join(f"- 先生の時間軸 {fmt_time(g['at'])} に録画の切れ目（間は記録なし）" for g in teacher["gaps"])
    results = []
    for v in student["videos"]:
        sl = "\n".join(line(w, w["start"], w["end"], "") for w in v["windows"] if w["read"])
        text = (f"# 先生の実験経過（1→2 を有効区間だけつないだ時間軸。時刻は先生の時間軸）\n{gap}\n{tl}\n\n"
                f"# 生徒の実験動画 {v['path']}（時刻は生徒の動画の経過時間）\n{sl}\n\n"
                "生徒の動画の区間ごとに、先生の時間軸のどこに当たるかを、根拠とともに答えてください。時刻は H:MM:SS で。")
        req = AIRequest(task="experiment_correspondence", source_label=v["path"], text=text, system=CORR_SYSTEM,
                        schema=CORR_SCHEMA, max_tokens=32000)
        try:
            if batch and hasattr(provider, "analyze_batch"):  # 同じ依頼を Batch API で（50% 引き）
                resp = provider.analyze_batch([req], log=log)[0]
                if isinstance(resp, Exception):
                    raise resp
            else:
                resp = provider.analyze(req)
            results.append({"student": v["path"], "model": resp.model, "input_tokens": resp.input_tokens,
                            "output_tokens": resp.output_tokens, **resp.data})
        except AwaitingAnswer as e:  # 回答待ち（候補 G5）: 失敗ではない
            log(f"  先生と生徒の対応付け: 回答待ち（{e}）")
            results.append({"student": v["path"], "awaiting": str(e)})
        except Exception as e:  # noqa: BLE001
            log(f"  先生と生徒の対応付けに失敗: {type(e).__name__}: {str(e)[:200]}")
            results.append({"student": v["path"], "error": f"{type(e).__name__}: {e}"})
    if any("awaiting" in r for r in results):
        return {"status": "awaiting", "results": results,
                "method": "試験体の状態・加力段階・計測器の表示・操作の一致から判断（撮影時刻は使わない）"}
    return {"status": "done", "results": results,
            "method": "試験体の状態・加力段階・計測器の表示・操作の一致から判断（撮影時刻は使わない）"}


def _md_windows(rows: list[dict], timeline: bool) -> list[str]:
    out = []
    for w in rows:
        head = (f"### {fmt_time(w['t_from'])}–{fmt_time(w['t_to'])}（{Path(w['source']).parent.name} の {fmt_time(w['source_start'])}–"
                f"{fmt_time(w['source_end'])}）" if timeline else f"### {fmt_time(w['start'])}–{fmt_time(w['end'])}")
        out.append(head)
        if not w["read"]:
            out.append("- 画面の内容は未読み取り（AI 未実行）")
        else:
            out.append(f"- 面: {w['face']}（{w['face_evidence']}） 視点: {w['view']}")
            out.append(f"- 試験体: {w['specimen_state']}")
            for c in w["cracks"]:
                out.append(f"- ひび割れ: {c['location']} / {c['direction']} / {c['change']}")
            if w["loading"]:
                out.append(f"- 加力: {w['loading']}")
            for x in w["readings"]:
                out.append(f"- 表示: {x['what']} = {x['value']}" + ("（概算）" if x["approx"] else ""))
            for a in w["actions"]:
                out.append(f"- 操作: {a}")
            for u in w["uncertain"]:
                out.append(f"- 不確実: {u}")
        if w["speech"]:
            out.append(f"- 音声: {w['speech'][:400]}")
        out.append("")
    return out


def analyze_corpus(cfg, out: Path, items: list, records: dict[str, dict], provider=None, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    comp = compare_materials(items, records)
    teacher = teacher_timeline(records)
    student = student_observations(records)
    corr = correspondence(teacher, student, provider, log,
                          batch=bool((cfg.data.get("reading", {}) if cfg is not None else {}).get("batch")))
    (out / "materials.json").write_text(json.dumps(comp, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "experiment_teacher.json").write_text(json.dumps(teacher, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "experiment_student.json").write_text(json.dumps(student, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "experiment_correspondence.json").write_text(json.dumps(corr, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 資料の一覧と重複・差分", "", "## 解析した資料"]
    md += [f"- {m['path']}（{m['kind']}、{m['size'] / 1e6:,.1f} MB）" + (f" ／ 同一内容: {'、'.join(m['duplicates'])}" if m["duplicates"] else "")
           for m in comp["materials"]]
    md += ["", "## 内容が完全に同一のファイル（代表の結果を使用。別資料として記録）"]
    md += [f"- {d['path']} = {d['same_content_as']}" for d in comp["duplicates"]] or ["- なし"]
    md += ["", "## 似ている資料（内容の一部が共通）"]
    for s in comp["similar"]:
        md.append(f"- {s['a']} と {s['b']}: " + ", ".join(f"{k}={v}" for k, v in s.items()
                                                         if k not in ("a", "b", "only_in_a", "only_in_b")))
        if s.get("only_in_a"):
            md.append("  - 前者だけにある発話の時刻: " + "、".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in s["only_in_a"][:15]))
        if s.get("only_in_b"):
            md.append("  - 後者だけにある発話の時刻: " + "、".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in s["only_in_b"][:15]))
    if not comp["similar"]:
        md.append("- なし")
    md += ["", "## 資料以外のファイル"] + [f"- {o['path']}（{o['role']}）{o['note']}" for o in comp["others"]]
    (out / "materials.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    tm = ["# 先生の実験経過（1→2、有効区間だけをつないだ時間軸）", ""]
    for p in teacher["parts"]:
        tr = p.get("trim") or {}
        tm.append(f"- {p['path']}: 時間軸 {fmt_time(p['timeline_from'])}–{fmt_time(p['timeline_to'])}、"
                  f"有効区間 0–{fmt_time(p['valid_end'])}（録画 {fmt_time(p['duration'])}）")
        tm.append(f"  - 末尾の除外: {tr.get('status')} {('除外開始 ' + fmt_time(tr['trim_at'])) if tr.get('trim_at') else ''}"
                  f" 根拠: {(tr.get('evidence') or {}).get('reason', '')}")
    for g in teacher["gaps"]:
        tm.append(f"- {fmt_time(g['at'])}: {g['note']}")
    tm += [""] + _md_windows(teacher["windows"], True)
    (out / "experiment_teacher.md").write_text("\n".join(tm) + "\n", encoding="utf-8")
    sm = ["# 生徒の実験動画（表面・裏面の観察）", ""]
    for v in student["videos"]:
        sm.append(f"## {v['path']}（{fmt_time(v['duration'])}）")
        sm.append("面の切り替わり: " + ("、".join(f"{fmt_time(c['at'])}→{c['to']}" for c in v["face_changes"]) or "（AI未実行のため未判定）"))
        sm += [""] + _md_windows(v["windows"], False)
    (out / "experiment_student.md").write_text("\n".join(sm) + "\n", encoding="utf-8")
    cm = ["# 先生と生徒の実験動画の対応", "", f"方法: {corr.get('method', '-')}", ""]
    if corr["status"] != "done":
        cm.append(f"未実施: {corr['reason']}")
    for r in corr.get("results", []):
        cm.append(f"## {r['student']}" + (f"（{r.get('model')}）" if r.get("model") else ""))
        if r.get("error"):
            cm.append(f"失敗: {r['error']}")
            continue
        cm += [f"- 生徒 {p['student_from']}–{p['student_to']} ↔ 先生 {p['teacher_from']}–{p['teacher_to']}"
               f"（確実性 {p['confidence']}）: {p['evidence']}" for p in r.get("pairs", [])]
        if r.get("unmatched_student"):
            cm.append("- 対応を判断できない区間: " + "、".join(r["unmatched_student"]))
        if r.get("notes"):
            cm.append(f"- 注記: {r['notes']}")
    (out / "experiment_correspondence.md").write_text("\n".join(cm) + "\n", encoding="utf-8")
    return {"materials": comp, "teacher": teacher, "student": student, "correspondence": corr}


def main(argv: list[str] | None = None) -> int:
    from ..config import load_config
    from .inventory import InventoryItem
    ap = argparse.ArgumentParser(prog="structlab.reading.corpus")
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("-c", "--config", default="configs/dropbox.yaml")
    ap.add_argument("--allow-external-ai", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    records: dict[str, dict] = {}
    items = None
    for d in a.dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            r = json.loads(rp.read_text(encoding="utf-8"))
            if r.get("path") and not r.get("error"):
                records.setdefault(r["path"], r)
        for ip in sorted(Path(d).rglob("inventory.json")):
            items = items or [InventoryItem(**x) for x in json.loads(ip.read_text(encoding="utf-8"))]
    provider = None
    if a.allow_external_ai and cfg.data["ai"]["provider"] != "anthropic":
        # 設定のプロバイダに従う（候補 G5 の受け渡し口などで、意図せず有料の API を呼ばない）
        from ..ai import make_provider
        provider, why = make_provider(cfg, allow_external_flag=True)
        if provider is None:
            print(f"AI を使えません: {why}")
    elif a.allow_external_ai:
        from ..ai.anthropic_provider import AnthropicProvider
        ai = cfg.data["ai"]
        provider = AnthropicProvider(model=ai.get("escalation_model") or ai["model"], effort=ai.get("escalation_effort", "high"),
                                     max_tokens=32000, use_fallbacks=ai["use_fallbacks"], max_image_edge=ai["max_image_edge"])
    res = analyze_corpus(cfg, Path(a.out), items or [], records, provider=provider, log=print)
    print((Path(a.out) / "materials.md").read_text(encoding="utf-8"))
    print(f"先生の時間軸: {res['teacher']['total_seconds'] / 60:.1f} 分（区間 {len(res['teacher']['windows'])}）、"
          f"生徒の動画 {len(res['student']['videos'])} 本、対応付け: {res['correspondence']['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
