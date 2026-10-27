"""試験対策の全工程をつなぐ。各段階の結果を work に保存し、途中停止後は続きから再開する。"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import box as B
from . import classify as C
from . import cues as Q
from . import materials as M
from . import questions as G
from . import render as R
from . import scoring as S
from . import text as T
from . import topics as P
from .analyze import cached_read
from .discover import Discovery, Material, box_materials, discover_dropbox, discover_local, kind_of, sha256_file

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "exam.yaml"


def load_config(path: Path | None = None) -> dict:
    cfg = yaml.safe_load((path or DEFAULT_CONFIG).read_text(encoding="utf-8")) or {}
    cfg.setdefault("subjects", [])
    cfg.setdefault("video", {})
    if (cfg.get("ai") or {}).get("enabled"):
        raise SystemExit("外部 AI は承認が必要です（この版は外部 AI を呼びません）。configs/exam.yaml の ai.enabled を false にしてください")
    return cfg


class DropboxUnavailable(RuntimeError):
    """Dropbox の認証・接続に失敗した（理由は伏せ字済み）。"""


@dataclass
class Options:
    input_dir: Path | None = None
    dropbox_root: str | None = None
    box_urls: list[str] = field(default_factory=list)
    work: Path = ROOT / "work" / "exam"
    out: Path = ROOT / "output" / "exam"
    allow_download: bool = False
    use_browser: bool = True
    use_http: bool = True
    recheck_box_hours: float = 24.0
    docx: bool = True
    overrides: Path | None = None
    config: Path | None = None
    log: object = print


def _json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "名称なし"


# ---------------- 段階 1・2: 発見と Box ----------------
def discover_all(o: Options) -> Discovery:
    d = Discovery()
    if o.input_dir:
        discover_local(o.input_dir, d)
    if o.dropbox_root is not None:
        from ..sources.base import SourceError
        from ..sources.dropbox_source import DropboxSource, redact
        try:
            discover_dropbox(DropboxSource(o.dropbox_root), o.work / "dropbox_cache", d)
        except SourceError as e:
            raise DropboxUnavailable(f"Dropbox に接続できません: {redact(str(e))}") from None
        except Exception as e:  # noqa: BLE001  # dropbox ライブラリの認証・通信エラー
            raise DropboxUnavailable(f"Dropbox に接続できません（{type(e).__name__}）: {redact(str(e))[:300]}") from None
    from .links import classify_url
    for u in o.box_urls:
        r = classify_url(u)
        r.sources.append("direct:指定された URL")
        d.links.append(r)
    for m in box_materials(d):
        d.materials.append(m)
    return d


def check_boxes(d: Discovery, o: Options) -> dict[str, dict]:
    cache_path = o.work / "box_checks.json"
    cache = _load(cache_path, {})
    out = {}
    for m in [m for m in d.materials if m.kind == "box_link"]:
        url = m.source[4:]
        prev = cache.get(url)
        fresh = prev and time.time() - prev["checked_at"] < o.recheck_box_hours * 3600
        if fresh and not (o.allow_download and prev["state"] == "C" and prev.get("can_download")):
            chk = prev
        else:
            o.log(f"Box リンクを確認: {url}")
            chk = B.check_link(url, work_dir=o.work, allow_download=o.allow_download, use_browser=o.use_browser,
                               use_http=o.use_http).as_dict()
            cache[url] = chk
            _json(cache_path, cache)
        out[m.id] = chk
        if chk.get("name"):
            m.notes.append(f"Box 上の名前: {chk['name']}")
        if chk["state"] == "A" and chk.get("local_path"):
            p = Path(chk["local_path"])
            m.local_path, m.size, m.sha256 = str(p), p.stat().st_size, sha256_file(p)
            m.kind = kind_of(p.name) or "video"
    return out


def write_box_outputs(d: Discovery, checks: dict[str, dict], cov: dict[str, dict], out: Path) -> None:
    base = out / "Boxリンク管理"
    _json(base / "URL一覧.json", [r.as_dict() for r in d.links])
    _json(base / "抽出ログ.json", d.link_logs)
    _json(base / "アクセス検査結果.json", checks)
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "動画取得可否一覧.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["URL", "見つけた場所", "名前", "種類", "サイズ(バイト)", "再生時間", "状態", "状態の説明", "理由",
                    "ダウンロード許可", "解析状況"])
        for m in [m for m in d.materials if m.kind == "box_link" or m.source.startswith("box:")]:
            c = checks.get(m.id, {})
            w.writerow([m.source[4:], " / ".join([*([m.folder + "/" + m.name] if m.folder else [m.name]), *m.also_at]),
                        c.get("name") or "不明", c.get("extension") or "不明", c.get("size") or "不明",
                        c.get("duration_text") or "不明", c.get("state"), c.get("state_label"), c.get("reason"),
                        {True: "可", False: "不可"}.get(c.get("can_download"), "不明"),
                        cov.get(m.id, {}).get("detail", "")])


# ---------------- 段階 3〜: 読み取り・判定・教材 ----------------
def _sidecars(d: Discovery) -> dict[str, Path]:
    """動画（または Box リンク）と同じ名前の字幕・文字起こしを対応づける。"""
    subs = {Path(m.name).stem: m for m in d.materials if m.kind == "transcript"}
    out = {}
    for m in d.materials:
        if m.kind in ("video", "audio", "box_link"):
            s = subs.get(Path(m.name).stem)
            if s:
                out[m.id] = Path(s.local_path)
                s.notes.append(f"{m.name} の字幕として使用")
    return out


def read_all(d: Discovery, o: Options, cfg: dict) -> dict[str, dict]:
    from .. import media
    from .analyze import parse_subtitles
    vcfg = cfg.get("video", {})
    ocr = media.OCR("auto")
    transcriber = None
    if vcfg.get("transcription") == "faster_whisper":
        try:
            transcriber = media.FasterWhisperTranscriber(vcfg.get("whisper_model", "small"), "ja", "cpu", "int8")
        except media.MediaError as e:
            o.log(f"文字起こしを使えません: {e}")
    side = _sidecars(d)
    used_as_sidecar = {str(p) for p in side.values()}
    readings = {}
    for m in d.materials:
        if m.kind == "transcript" and m.local_path in used_as_sidecar:
            continue
        if m.kind == "box_link":
            r = {"material": m.id, "units": [], "coverage": {"status": "未取得", "detail": "動画本体は未取得"}}
            if m.id in side:
                sp = parse_subtitles(side[m.id].read_text(encoding="utf-8", errors="replace"))
                r["units"] = [{"kind": "speech", **s} for s in sp]
                r["coverage"] = {"status": "一部", "detail": f"映像: 未取得 / 音声: 字幕ファイル {len(sp)} 件"}
            readings[m.id] = r
            continue
        o.log(f"読み取り: {m.name}")
        try:
            readings[m.id] = cached_read(m, o.work, ocr=ocr, transcriber=transcriber, sidecar=side.get(m.id),
                                         video_interval=float(vcfg.get("interval", 2.0)), log=o.log)
        except Exception as e:  # noqa: BLE001
            readings[m.id] = {"material": m.id, "units": [], "coverage": {"status": "失敗", "detail": str(e)[:200]}}
    return readings


def _why_unanalyzed(m: Material, cov: dict, chk: dict | None) -> str | None:
    if m.kind == "box_link":
        return (chk or {}).get("state_label", "未確認") + ("" if cov.get("status") != "一部" else "（字幕のみ）")
    if cov.get("status") in ("完了", None):
        return None
    return cov.get("detail") or cov.get("status")


def slide_alignment(mats: list[dict], readings: dict, topics: list[dict]) -> list[dict]:
    """動画の画面（OCR）と配布資料のページを照合する。"""
    page_topic = {}
    for t in topics:
        for s in t["sources"]:
            if s["kind"] in ("page", "text"):
                page_topic[(s["material"], s["page"])] = (t["id"], " ".join(t["texts"]))
    out = []
    for m in mats:
        for u in readings.get(m["id"], {}).get("units", []):
            if u["kind"] != "scene" or len(u.get("text", "").strip()) < 10:
                continue
            best = max(((T.similarity(u["text"], txt), k, tid) for k, (tid, txt) in page_topic.items()),
                       default=(0, None, None))
            if best[0] >= 0.4:
                out.append({"material": m["id"], "start": u["start"], "end": u["end"], "slide_material": best[1][0],
                            "page": best[1][1], "topic": best[2], "similarity": round(best[0], 2)})
    return out


def analyze_subject(subject: str, mats: list[dict], readings: dict, cls: dict, cfg: dict, unanalyzed: list[dict],
                    overrides: dict) -> dict:
    topics = P.build_topics(subject, mats, readings, cls)
    patterns = Q.compile_patterns(cfg)
    cues = []
    for m in mats:
        cues += Q.find_cues(readings.get(m["id"], {}).get("units", []), m["id"], patterns, cls[m["id"]]["lecture"])
    align = slide_alignment(mats, readings, topics)

    def slide_for_time(mid, t):
        mine = [a for a in align if a["material"] == mid]
        hit = next((a for a in mine if a["start"] <= t < a["end"]), None)  # その時刻を含む場面を優先
        hit = hit or next((a for a in mine if a["start"] - 1 <= t <= a["end"] + 1), None)
        return hit["topic"] if hit else None

    Q.resolve_targets(cues, topics, slide_for_time)
    for c in cues:  # スライドに結びつかない発言（動画だけの授業など）は、発言から論点を作る
        if c.target_topic is None and c.type not in ("scope", "correction"):
            t = P.topic_from_cue(subject, c, len(topics))
            topics.append(t)
            c.target_topic, c.target_note = t["id"], "発言の文脈から論点を作成（スライドと未照合）"
    info = P.attach_document_evidence(topics, mats, readings, cls)
    all_units = [(m["id"], cls[m["id"]]["lecture"], u) for m in mats
                 for u in readings.get(m["id"], {}).get("units", [])]
    P.attach_repetition(topics, all_units)
    qs_past = [q for m in mats if m["role"] == "past_exam" for u in readings.get(m["id"], {}).get("units", [])
               for q in P.split_questions(u.get("text", ""))]
    if qs_past:
        info["calc_share"] = sum(1 for q in qs_past if re.search(r"求めよ|計算|=|＝", q)) / len(qs_past)
    j = {}
    for t in topics:
        j[t["id"]] = S.apply_overrides(t, S.score_topic(t, cues, info, cfg, unanalyzed), overrides)
    order = {"S": 0, "A": 1}
    selected = sorted([t for t in topics if j[t["id"]]["grade"] in order],
                      key=lambda t: (order[j[t["id"]]["grade"]], -j[t["id"]]["score"]))
    questions = {t["id"]: G.questions_for(t, [o for o in selected if o["id"] != t["id"]]) for t in selected}
    mocks = G.mock_exams(selected, j, info)
    return {"subject": subject, "topics": topics, "judgments": j, "cues": [c.as_dict() for c in cues],
            "info": info, "selected": [t["id"] for t in selected], "questions": questions, "mocks": mocks,
            "alignment": align, "unanalyzed": unanalyzed}


def ledger_of(res: dict, names: dict) -> dict:
    """出題根拠台帳（JSON）。各論点の判定・支持/反対証拠・未確認事項・出典。"""
    items = []
    for t in res["topics"]:
        j = res["judgments"][t["id"]]
        items.append({
            "id": t["id"], "subject": res["subject"], "lectures": t["lectures"], "title": t["title"],
            "content": t["summary"], "grade": j["grade"], "grade_sub": j["sub"], "reason": j["reason"],
            "confidence": j["confidence"], "score_internal": j["score"], "factors": j["factors"],
            "explicit_or_inferred": "明示" if any(e.get("basis", "").startswith("明示") for e in j["support"]) else "推測",
            "support": j["support"], "against": j["against"], "unknowns": j["unknowns"],
            "past_exam_years": j["past_exam_years"],
            "assignment_relation": [e["name"] for e in t["evidence"] if e["type"] == "assignment"],
            "formulas": t["formulas"], "question_types": sorted({q["type"] for q in res["questions"].get(t["id"], [])}),
            "sources": [M.fmt_source(s, names) for s in t["sources"]],
            **({"overridden": True, "auto_grade": j["auto_grade"]} if j.get("overridden") else {}),
        })
    return {"subject": res["subject"], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "点数は根拠の内部点数で、出題確率ではない", "scope_lectures": res["info"].get("scope_lectures"),
            "past_exam_years": res["info"].get("past_exam_years"), "unanalyzed": res["unanalyzed"], "topics": items}


def record_history(work: Path, subject: str, ledger: dict) -> list[dict]:
    """前回の判定と比べ、変わった論点を履歴に残す（A→S、範囲から除外など）。"""
    prev_path = work / "ledger" / f"{_safe(subject)}.json"
    prev = {t["title"]: t for t in _load(prev_path, {"topics": []})["topics"]}
    changes = []
    for t in ledger["topics"]:
        p = prev.get(t["title"])
        if p is None and prev:
            changes.append({"topic": t["title"], "from": None, "to": t["grade"], "reason": "新しい論点"})
        elif p and p["grade"] != t["grade"]:
            changes.append({"topic": t["title"], "from": p["grade"], "to": t["grade"], "reason": t["reason"]})
    now = {t["title"] for t in ledger["topics"]}
    changes += [{"topic": k, "from": v["grade"], "to": None, "reason": "論点が見つからなくなった"}
                for k, v in prev.items() if k not in now]
    if changes:
        with open(work / "history.jsonl", "a", encoding="utf-8") as f:
            for c in changes:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "subject": subject, **c},
                                   ensure_ascii=False) + "\n")
    _json(prev_path, ledger)
    return changes


def render_subject(res: dict, ledger: dict, names: dict, out: Path, work: Path, docx: bool, log=print) -> dict:
    base = out / "科目一覧" / _safe(res["subject"]) / "試験対策"
    by_id = {t["id"]: t for t in res["topics"]}
    selected = [by_id[i] for i in res["selected"]]
    ctx = {"subject": res["subject"], "topics": res["topics"], "selected": selected, "j": res["judgments"],
           "questions": res["questions"], "names": names, "info": res["info"], "by_id": by_id,
           "unanalyzed": res["unanalyzed"]}
    fp = hashlib.sha256(json.dumps([ledger["topics"], res["questions"], res["mocks"]], ensure_ascii=False,
                                   sort_keys=True, default=str).encode()).hexdigest()
    fp_path = work / "render_fp" / f"{_safe(res['subject'])}.txt"
    if fp_path.exists() and fp_path.read_text() == fp and (base / "最重要事項.pdf").exists():
        log(f"  {res['subject']}: 内容に変更なし（教材は作り直さない）")
        return {"skipped": True, "checks": _load(base / "品質検査.json", [])}
    all_q = [q for t in selected for q in res["questions"][t["id"]]]
    docs = {
        "最重要事項": (M.doc_summary(ctx), [t["title"] for t in selected[:3]]),
        "徹底対策資料": (M.doc_deep(ctx), [t["title"] for t in selected[:3]]),
        "公式一覧": (M.doc_formulas(ctx), []),
        "予想問題": (M.doc_questions(f"{res['subject']}　予想問題", all_q), ["問1"] if all_q else []),
        "解答用紙": (M.doc_answer_sheet(f"{res['subject']}　予想問題　解答用紙", all_q), []),
        "解答解説": (M.doc_solutions(f"{res['subject']}　予想問題　解答・解説", all_q, ctx), []),
        "判定一覧": (M.doc_judgments(ctx), []),
    }
    for ex in res["mocks"]:
        d = base / f"模擬テスト第{ex['no']}回"
        meta = (f"試験時間 {ex['time_minutes']} 分・100 点満点（{ex['provisional']}）。"
                "解答は別紙の解答用紙に記入すること。")
        docs[f"模擬テスト第{ex['no']}回/問題"] = (M.doc_questions(ex["title"], ex["questions"], meta, points=True), [])
        docs[f"模擬テスト第{ex['no']}回/解答用紙"] = (M.doc_answer_sheet(ex["title"] + "　解答用紙", ex["questions"]), [])
        sol = M.doc_solutions(ex["title"] + "　解答・解説", ex["questions"], ctx)
        sol.insert(1, {"t": "small", "text": "出題の設計: " + ex["design"]})
        docs[f"模擬テスト第{ex['no']}回/解答解説"] = (sol, [])
        d.mkdir(parents=True, exist_ok=True)
    checks = []
    for name, (blocks, expect) in docs.items():
        pdf = base / f"{name}.pdf"
        R.to_pdf(blocks, pdf, title=name, header=res["subject"])
        chk = R.check_pdf(pdf, expect, render_dir=work / "renders" / _safe(res["subject"]))
        if name.endswith("/問題") and any(f"［重要度" in str(b.get("text", "")) for b in blocks):
            chk["problems"].append("問題用紙に重要度が表示されている")
            chk["ok"] = False
        checks.append(chk)
        if docx:
            dx = R.to_docx(blocks, base / f"{name}.docx", title=name)
            checks.append(R.check_docx(dx, expect))
    _json(base / "出題根拠.json", ledger)
    _json(base / "模擬テスト.json", res["mocks"])
    _json(base / "品質検査.json", checks)
    fp_path.parent.mkdir(parents=True, exist_ok=True)
    fp_path.write_text(fp)
    return {"skipped": False, "checks": checks}


def run(o: Options) -> dict:
    cfg = load_config(o.config)
    overrides = yaml.safe_load(o.overrides.read_text(encoding="utf-8")) if o.overrides and o.overrides.exists() else {}
    o.work.mkdir(parents=True, exist_ok=True)
    report: dict = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "stages": {}}
    d = discover_all(o)
    report["stages"]["discover"] = {"materials": len(d.materials), "links": len(d.links),
                                    "duplicates": d.duplicates, "skipped": d.skipped}
    checks = check_boxes(d, o)
    report["stages"]["box"] = {k: {x: v.get(x) for x in ("url", "state", "state_label", "reason", "name")}
                               for k, v in checks.items()}
    readings = read_all(d, o, cfg)
    cov = {k: v.get("coverage", {}) for k, v in readings.items()}
    write_box_outputs(d, checks, cov, o.out)
    mats = [m.as_dict() for m in d.materials if m.id in readings]
    names = {m["id"]: m["name"] for m in mats}
    cls = C.classify(mats, readings, cfg["subjects"])
    for m in mats:  # Box の名前（04.mp4 など）も講義回の手がかりにする
        chk = checks.get(m["id"])
        if chk and cls[m["id"]]["lecture"] is None and chk.get("name"):
            lec, ev = C.lecture_of(chk["name"], "", "")
            if lec:
                cls[m["id"]].update(lecture=lec, lecture_evidence=f"Box 上の名前「{chk['name']}」の{ev}（推測）")
    _json(o.out / "全体処理履歴" / "分類結果.json",
          [{"name": m["name"], "source": m["source"], "role": m["role"], **cls[m["id"]],
            "coverage": cov.get(m["id"])} for m in mats])
    subjects: dict[str, list[dict]] = {}
    for m in mats:
        subjects.setdefault(cls[m["id"]]["subject"], []).append(m)
    report["subjects"] = {}
    for subj, ms in subjects.items():
        un = []
        for m in ms:
            why = _why_unanalyzed(Material(**m), cov.get(m["id"], {}), checks.get(m["id"]))
            if why:
                un.append({"name": m["name"], "source": m["source"], "lecture": cls[m["id"]]["lecture"], "why": why})
        if subj == C.UNKNOWN:
            _json(o.out / "未分類・要確認" / "一覧.json",
                  [{"name": m["name"], "source": m["source"], "evidence": cls[m["id"]]["evidence"]} for m in ms])
            report["subjects"][subj] = {"materials": len(ms), "note": "科目を判定できない資料。configs/exam.yaml の subjects に科目を登録するか、科目名のフォルダに置く"}
            continue
        o.log(f"科目「{subj}」を解析（資料 {len(ms)} 件）")
        res = analyze_subject(subj, ms, readings, cls, cfg, un, overrides)
        led = ledger_of(res, names)
        changes = record_history(o.work, subj, led)
        rr = render_subject(res, led, names, o.out, o.work, o.docx, o.log)
        _json(o.out / "科目一覧" / _safe(subj) / "解析データ" / "スライド照合.json", res["alignment"])
        _json(o.out / "科目一覧" / _safe(subj) / "解析データ" / "重要発言.json", res["cues"])
        grades = {g: sum(1 for x in res["judgments"].values() if x["grade"] == g) for g in "SABC"}
        report["subjects"][subj] = {
            "materials": len(ms), "topics": len(res["topics"]), "grades": grades,
            "mock_exams": len(res["mocks"]), "unanalyzed": un, "history_changes": changes,
            "rendered": not rr["skipped"], "quality_problems": [c for c in rr["checks"] if not c["ok"]],
            "calc_verified": all(q.get("verified", True) for qs in res["questions"].values() for q in qs),
        }
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _json(o.out / "全体処理履歴" / "実行報告.json", report)
    return report
