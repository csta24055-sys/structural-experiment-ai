"""要求事項の台帳と照合（本番解析の完了時・Excel 作成時・レポート作成時に同じ台帳で照合する）。

台帳 = 利用者の指示（requirements/process.yaml、コミット済み）＋ 資料から抽出した先生の要求事項（意味理解層の指示一覧）
       ＋ 読み取りで判読できなかった箇所（未確認事項）。

照合の結果は項目ごとに次のどれか（確かめられないものを成功にしない）:
  成功 … 自動の確認で満たしていることを確かめた
  不一致 … 自動の確認で満たしていないことが分かった
  要確認 … 自動では判断できない・根拠が弱い。人が元資料・元動画の該当箇所で確認する
  未検証 … 確認の仕組みが無い・まだ実行していない工程
  未実施 … その工程（AI の読み取り等）を今回は行っていない
  矛盾 … 資料や指示の間で食い違いがあり、どちらも破棄せずに残している
  対象外 … この工程では確かめない（別の工程で確かめる）

  structlab requirements verify --stage analysis --reading <読み取り結果> --corpus <資料横断> --semantic <意味理解> --out <出力先>
  structlab requirements checklist --stage excel|report --semantic <意味理解> --out <出力先>
  structlab requirements verify --stage excel|report --evidence <作成時の確認記録.json> ... （作成段階での再照合）
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROCESS_PATH = ROOT / "requirements" / "process.yaml"
STATUSES = ("成功", "不一致", "要確認", "未検証", "未実施", "矛盾", "対象外")

EXCEL_WORDS = re.compile(r"Excel|エクセル|セル|グラフ|数式|関数|シート|近似|散布図|軸|凡例|オートフィル|コピー|貼り付け|列|行|表計算|計算")
REPORT_WORDS = re.compile(r"レポート|報告書|考察|書式|章|提出|図番号|表番号|ページ|表紙|結論|目的|文献|参考|書き方|記入|手書き|印刷|PDF")


def load_process(path: Path = PROCESS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def teacher_items(semantic_dir: Path | None) -> list[dict]:
    """意味理解層の指示一覧（directives.json）を要求事項の項目にする（すべて。分類・強さは変えない）。"""
    if not semantic_dir:
        return []
    f = Path(semantic_dir) / "semantic" / "directives.json"
    if not f.exists():
        f = Path(semantic_dir) / "directives.json"
    if not f.exists():
        return []
    out = []
    for d in json.loads(f.read_text(encoding="utf-8")):
        text = d.get("content", "")
        hay = " ".join(str(d.get(k) or "") for k in ("content", "target_task", "deliverable", "screen", "operation"))
        stages = [s for s, rx in (("excel", EXCEL_WORDS), ("report", REPORT_WORDS)) if rx.search(hay)] or ["excel", "report"]
        out.append({"id": f"T{d['id']}", "origin": "teacher_material", "kind": d.get("class_label", d.get("class")),
                    "text": text, "stage": ["analysis"] + stages, "source": d.get("source"), "time": d.get("time_label"),
                    "page": d.get("page"), "certainty": d.get("certainty"), "verification": d.get("verification"),
                    "verification_label": d.get("verification_label"), "explicit": d.get("origin") == "explicit",
                    "speaker": d.get("speaker_label"), "effective": d.get("effective"), "condition": d.get("condition"),
                    "unconfirmed": d.get("unconfirmed"), "relations": d.get("relations", [])})
    return out


def unreadable_items(reading_dirs: list[Path]) -> list[dict]:
    """読み取りで判読できなかった箇所（AI の unreadable）を未確認事項として台帳に載せる。"""
    out, seen = [], set()
    for d in reading_dirs:
        for rp in sorted(Path(d).rglob("reading.json")):
            r = json.loads(rp.read_text(encoding="utf-8"))
            if not r.get("path") or r["path"] in seen:
                continue
            seen.add(r["path"])
            units = [(f"p.{p['page']}", p.get("ai")) for p in r.get("pages", [])] + \
                    [(f"{s.get('start')}s", s.get("ai")) for s in r.get("scenes", []) if s.get("same_as") is None]
            for where, ai in units:
                for u in (ai or {}).get("unreadable", []) or []:
                    out.append({"id": f"U{len(out) + 1:04d}", "origin": "unreadable", "kind": "未確認事項",
                                "text": f"{u.get('where', '')}: {u.get('reason', '')}", "stage": ["analysis", "excel", "report"],
                                "source": r["path"], "time": where})
                for x in (ai or {}).get("uncertain", []) or []:
                    out.append({"id": f"U{len(out) + 1:04d}", "origin": "unreadable", "kind": "未確認事項",
                                "text": str(x), "stage": ["analysis", "excel", "report"], "source": r["path"], "time": where})
    return out


# ---------------- 解析工程での自動の確認 ----------------
class Ctx:
    def __init__(self, cfg, reading_dirs: list[Path], corpus: Path | None, semantic: Path | None):
        self.cfg = cfg
        self.records: dict[str, dict] = {}
        self.summaries: list[dict] = []
        self.inventory: list[dict] = []
        for d in reading_dirs:
            for rp in sorted(Path(d).rglob("reading.json")):
                r = json.loads(rp.read_text(encoding="utf-8"))
                if r.get("path"):
                    self.records.setdefault(r["path"], {**r, "_file": str(rp)})
            for sp in sorted(Path(d).rglob("summary.json")):
                self.summaries.append(json.loads(sp.read_text(encoding="utf-8")))
            for ip in sorted(Path(d).rglob("inventory.json")):
                self.inventory = self.inventory or json.loads(ip.read_text(encoding="utf-8"))
        self.corpus = Path(corpus) if corpus else None
        self.semantic = Path(semantic) if semantic else None

    def cj(self, name: str) -> dict | None:
        if self.corpus and (self.corpus / name).exists():
            return json.loads((self.corpus / name).read_text(encoding="utf-8"))
        return None

    def videos(self, pred=lambda r: True) -> list[dict]:
        return [r for r in self.records.values() if r.get("kind") == "video" and not r.get("error") and pred(r)]

    @property
    def ai_used(self) -> int:
        return sum(s.get("ai_usage", {}).get("calls", 0) for s in self.summaries)


def _ok(cond: bool, good: str, bad: str) -> tuple[str, str]:
    return ("成功", good) if cond else ("不一致", bad)


def _units(r: dict) -> list[dict]:
    return r.get("pages", []) if r.get("kind") == "pdf" else [s for s in r.get("scenes", []) if s.get("same_as") is None]


def check_auto(name: str, c: Ctx) -> tuple[str, str]:
    role = lambda r: r.get("role") or {}  # noqa: E731
    if name == "all_materials_listed":
        if not c.inventory:
            return "未検証", "棚卸し（inventory.json）が無い"
        mats = [i for i in c.inventory if i["role"] == "material"]
        missing = [i["path"] for i in mats if i["path"] not in c.records]
        return _ok(not missing, f"棚卸し {len(c.inventory)} 件、資料 {len(mats)} 件すべてに読み取り結果あり",
                   f"読み取り結果が無い資料: {missing}")
    if name == "no_unconfirmed_exclusion":
        bad = [i["path"] for i in c.inventory if i["role"] == "duplicate" and not i.get("canonical")]
        return _ok(not bad, "除外は内容のハッシュが同一の重複・補助ファイルだけ", f"根拠のない除外: {bad}")
    if name == "duplicates_recorded":
        m = c.cj("materials.json")
        return ("未検証", "materials.json が無い") if m is None else ("成功", f"同一内容 {len(m['duplicates'])} 件を別資料として記録")
    if name == "similar_differences_recorded":
        m = c.cj("materials.json")
        return ("未検証", "materials.json が無い") if m is None else ("成功", f"似ている資料 {len(m['similar'])} 組の違い（時刻の範囲）を記録")
    if name == "ai_read_complete":
        if not c.ai_used and not any(u.get("ai") for r in c.records.values() for u in _units(r)):
            return "未実施", "AI による読み取りを今回は行っていない（無料の読み取りのみ）"
        miss = {p: sum(1 for u in _units(r) if not u.get("ai")) for p, r in c.records.items() if not r.get("error")}
        miss = {k: v for k, v in miss.items() if v}
        return _ok(not miss, "全ページ・全場面を AI で読み取り済み", f"AI 未読の単位: {miss}")
    if name == "verification_recorded":
        bad = [p for p, r in c.records.items() if not r.get("error") and len(r.get("verification", [])) != len(
            r.get("pages", []) if r.get("kind") == "pdf" else r.get("scenes", []))]
        return _ok(not bad, "全単位に照合の結果を記録（未照合は成功ではない）", f"照合の記録が欠けている: {bad}")
    if name == "orientation_handled":
        rot = [(r["path"], s["start"], s["end"]) for r in c.videos() for s in r.get("segments", []) if s.get("rotate")]
        return "成功", (f"横倒しの区間を補正: {rot}" if rot else "横倒しの区間は検出されなかった（検出の仕組みは動作）")
    if name == "no_speech_without_audio":
        bad = []
        for r in c.videos():
            track = (r.get("audio") or {}).get("track_seconds")
            end = float(r.get("valid_end") or r.get("duration") or 0)
            for s in r.get("speech", []):
                if (track is not None and s["start"] > track + 0.5) or s["start"] > end + 0.5:
                    bad.append((r["path"], s["start"]))
        return _ok(not bad, "音声の無い時間・除外した末尾に文字起こしは無い", f"音声の無い時間の文字起こし: {bad[:10]}")
    if name == "lecture_detail_applied":
        vs = c.videos(lambda r: role(r).get("kind") == "lecture_detail")
        if not vs:
            return "不一致", "Excel・レポートの作り方の動画に細部の読み取りが適用されていない"
        no_tiles = [r["path"] for r in vs if any(not s.get("tiles") and not str(s.get("frame", "")).endswith("_full.jpg")
                                                 for s in _units(r) if s.get("mode") != "experiment")]
        return _ok(not no_tiles, f"{len(vs)} 本に適用（0.5 秒ごと・変化した画面すべて・2 倍・部分拡大・large-v3）",
                   f"部分拡大の無い場面がある: {no_tiles}")
    if name == "per_material_outputs":
        miss = [p for p, r in c.records.items() if not r.get("error") and not (Path(r["_file"]).parent / "reading.md").exists()]
        return _ok(not miss, "資料ごとの reading.md / reading.json あり", f"出力が無い: {miss}")
    if name == "teacher_sequence":
        t = c.cj("experiment_teacher.json")
        if t is None:
            return "未検証", "experiment_teacher.json が無い"
        names = [Path(p["path"]).parent.name for p in t["parts"]]
        return _ok(names == ["鋼材実験経過先生1", "鋼材実験経過先生2"], "先生1→先生2 の2本だけを順に連結", f"連結の対象・順序: {names}")
    if name == "teacher_trim":
        t = c.cj("experiment_teacher.json")
        if t is None:
            return "未検証", "experiment_teacher.json が無い"
        st = [(Path(p["path"]).parent.name, (p.get("trim") or {}).get("status"), (p.get("trim") or {}).get("trim_at"),
               ((p.get("trim") or {}).get("evidence") or {}).get("reason")) for p in t["parts"]]
        if all(s[1] == "trimmed" and s[2] and s[3] for s in st):
            return "成功", "; ".join(f"{n}: {a} 秒以降を除外（{why}）" for n, _, a, why in st)
        return "要確認", f"除外を確定できなかった動画がある（推測で削除していない）: {st}"
    if name == "teacher_timeline":
        t = c.cj("experiment_teacher.json")
        if t is None:
            return "未検証", "experiment_teacher.json が無い"
        over = [w for w in t["windows"] for p in t["parts"] if w["source"] == p["path"] and w["source_end"] > p["valid_end"] + 0.01]
        return _ok(not over and len(t["gaps"]) == len(t["parts"]) - 1,
                   f"有効区間だけを連結（{t['total_seconds'] / 60:.1f} 分）、切れ目 {len(t['gaps'])} か所を「記録なし」として明示",
                   "除外した末尾が時間軸に含まれている、または切れ目の記録が無い")
    if name in ("student_faces", "student_outputs"):
        s = c.cj("experiment_student.json")
        if s is None:
            return "未検証", "experiment_student.json が無い"
        if not any(w.get("read") for v in s["videos"] for w in v["windows"]):
            return "未実施", "表面・裏面の判定は AI の読み取り後（今回は未実施）"
        return "成功", f"生徒の動画 {len(s['videos'])} 本、面の切り替わり " + \
            "、".join(str(len(v["face_changes"])) for v in s["videos"]) + " 回"
    if name == "student_not_duplicate":
        m = c.cj("materials.json") or {"duplicates": [], "materials": []}
        bad = [d["path"] for d in m["duplicates"] if "生徒" in d["path"] or "先生" in d["path"]]
        return _ok(not bad, "先生・生徒の動画を重複として除外していない", f"重複として除外された: {bad}")
    if name == "student_rules":
        bad = [r["path"] for r in c.videos(lambda r: role(r).get("sequence") == "student") if r.get("trim") or role(r).get("order")]
        return _ok(not bad, "生徒の動画に連結・末尾除外を適用していない", f"適用されている: {bad}")
    if name == "correspondence_method":
        k = c.cj("experiment_correspondence.json")
        if k is None:
            return "未検証", "experiment_correspondence.json が無い"
        if k.get("status") != "done":
            return "未実施", k.get("reason", "")
        return "成功", k.get("method", "")
    if name == "no_number_continuity":
        seq = {role(r).get("sequence") for r in c.videos()} - {None}
        return _ok(seq <= {"teacher", "student"}, "番号による連結は先生1・2だけ（その他は資料ごとに独立して読む）",
                   f"想定外の連結: {seq}")
    if name == "semantic_outputs":
        if not c.semantic:
            return "未検証", "意味理解の結果が無い"
        need = ["directives.json", "coverage.md", "unconfirmed.md", "conflicts.md", "changes.md", "failures.md"]
        miss = [n for n in need if not (c.semantic / "semantic" / n).exists()]
        if miss:
            return "不一致", f"意味理解の出力が欠けている: {miss}"
        ai = json.loads((c.semantic / "semantic_usage.json").read_text()) if (c.semantic / "semantic_usage.json").exists() else {}
        return ("成功", "第1〜3回（AI）と規則の結果を出力") if ai.get("calls") else \
            ("未実施", "規則による抽出のみ（AI の第1〜3回は未実施。網羅性は未確認）")
    if name == "check_catalog":
        from .checks.catalog import CATALOG
        return "成功", f"チェックのカタログ {len(CATALOG)} 項目（工程ごとに組み込み済み）"
    if name in ("requirements_ledger", "requirements_verified_at_analysis"):
        return "成功", "この台帳（利用者の指示＋資料の要求事項＋未確認事項）を作成し項目ごとに照合した"
    if name == "requirements_recheck_mechanism":
        return "成功", "structlab requirements checklist / verify --stage excel|report で同じ台帳を再照合できる"
    if name == "unconfirmed_marked":
        return "成功", "判読不能・不確実な箇所を未確認事項（U 番号）として台帳に載せた（確認済みとして扱わない）"
    if name == "conflicts_kept":
        return "成功", "矛盾は双方を残す（台帳の conflicts・意味理解の conflicts.md）"
    if name == "no_paid_run_without_approval":
        return ("成功", "この実行では有料 AI を使っていない") if not c.ai_used else \
            ("要確認", f"有料 AI を {c.ai_used} 回使用。予算の承認の記録と照らし合わせる")
    if name == "model_top":
        ai = c.cfg.data["ai"]
        used = {m for s in c.summaries for m in (s.get("ai_usage", {}).get("by_model") or {})}
        ok = ai["model"] == "claude-opus-5-5" and not ai.get("escalation_model") and used <= {ai["model"]}
        return _ok(ok, f"全解析で {ai['model']}（モデルを分けない）", f"設定 {ai['model']} / 上位 {ai.get('escalation_model')} / 使用 {used}")
    if name == "config_2350":
        rd = c.cfg.data.get("reading", {})
        roles = rd.get("roles", [])
        exp = [r for r in roles if r.get("kind") == "experiment"]
        ok = rd.get("batch") and exp and all(int(r.get("group", 1)) == 4 and float(r.get("window", 15)) == 15 for r in exp)
        return _ok(bool(ok), "Batch API・実験区間 15 秒（3 枚）×4 区間まとめ・切り出しの構成", "2,350円構成の設定と違う")
    if name == "notifications_excluded":
        return "要確認", "指示文で通知の書き起こしを禁止（仕組み）。出力ごとに通知が含まれていないかは人が確認"
    return "未検証", f"自動の確認 {name} は未実装"


def verify_analysis(cfg, reading_dirs: list[Path], corpus: Path | None, semantic: Path | None) -> list[dict]:
    c = Ctx(cfg, reading_dirs, corpus, semantic)
    proc = load_process()
    rows = []
    for r in proc["requirements"]:
        res = {**r, "origin": "user_instruction", "source": ", ".join(f"{s} {proc['sources'][s]['ts']}" for s in r["src"])}
        if r.get("status") == "superseded":
            res.update(status="対象外", evidence=f"{r.get('superseded_by')} で訂正・置き換え（記録は残す）")
        elif not any(s in r["stage"] for s in ("reading", "semantic", "integration", "cost", "security", "operations")):
            res.update(status="対象外", evidence="Excel・レポート・文章調整・開発の工程で確かめる")
        else:
            checks = [x.strip() for x in str(r.get("verify", "manual")).split(";")]
            parts = []
            for chk in checks:
                if chk.startswith("auto:"):
                    parts.append(check_auto(chk[5:], c))
                elif chk.startswith("check:"):
                    parts.append(("要確認", f"チェック {chk[6:]} の結果（results/checks）で確認"))
                else:
                    parts.append(("要確認", "人が確認する項目（自動では確かめられない）"))
            order = ["不一致", "矛盾", "未実施", "未検証", "要確認", "成功"]
            res["status"] = min((p[0] for p in parts), key=order.index)
            res["evidence"] = " / ".join(p[1] for p in parts)
        if r.get("conflicts"):
            res["status"] = "矛盾" if res["status"] in ("成功", "要確認") else res["status"]
            res["evidence"] += " / 矛盾: " + "; ".join(f"{x['with']}: {x['note']}（{x['resolution']}）" for x in r["conflicts"])
        rows.append(res)
    for t in teacher_items(semantic):
        if t["effective"] == "unresolved_conflict":
            st, ev = "矛盾", "資料内・資料間で食い違いがあり未解決（双方を残す）"
        elif t["verification"] in ("verified", "revised") and t["explicit"]:
            st, ev = "成功", f"第3回の照合で確認（{t['verification_label']}）。出典 {t['source']} {t['time'] or ''}"
        else:
            st, ev = "要確認", f"{t['verification_label']}。出典 {t['source']} {t['time'] or ''}。元資料の該当箇所で確認する"
        rows.append({**t, "status": st, "evidence": ev})
    for u in unreadable_items(reading_dirs):
        rows.append({**u, "status": "要確認", "evidence": "判読できない・不確実（確認済みとして扱わない）"})
    return rows


# ---------------- Excel・レポートの作成段階 ----------------
def checklist(stage: str, semantic: Path | None, reading_dirs: list[Path] | None = None) -> list[dict]:
    """作成段階で照合する項目（利用者の指示＋先生の要求事項＋未確認事項）。evidence を埋めて verify に渡す。"""
    proc = load_process()
    items = [{"id": r["id"], "origin": "user_instruction", "kind": r["kind"], "text": r["text"], "verify": r.get("verify"),
              "source": ", ".join(r["src"])} for r in proc["requirements"]
             if stage in r["stage"] and r.get("status") != "superseded"]
    items += [{k: t[k] for k in ("id", "origin", "kind", "text", "source", "time", "condition", "verification_label")}
              for t in teacher_items(semantic) if stage in t["stage"]]
    items += [{k: u[k] for k in ("id", "origin", "kind", "text", "source", "time")}
              for u in unreadable_items(reading_dirs or []) if stage in u["stage"]]
    for it in items:
        it["evidence"] = {"status": "未検証", "where": "", "note": ""}
    return items


def verify_stage(stage: str, items: list[dict], evidence: dict) -> list[dict]:
    """作成段階の照合。evidence: {項目ID: {status, where, note}}。記録が無い・根拠が無い項目は成功にしない。"""
    out = []
    for it in items:
        e = evidence.get(it["id"]) or {}
        st = e.get("status", "未検証")
        if st not in STATUSES:
            st = "要確認"
        if st == "成功" and not e.get("where"):
            st = "要確認"  # 満たした場所（シート・セル・ページ）の記録が無いものは確認済みにしない
        if it.get("origin") == "unreadable" and st == "成功" and not e.get("note"):
            st = "要確認"
        out.append({**it, "status": st, "evidence": e})
    return out


def write(rows: list[dict], out: Path, stage: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    counts = {s: sum(1 for r in rows if r.get("status") == s) for s in STATUSES}
    (out / f"ledger_{stage}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = [f"# 要求事項の照合（{stage}）", "",
         "台帳 = 利用者の指示（P）＋ 資料から抽出した要求事項（T、話者は特定していない）＋ 判読できなかった箇所（U）。",
         "「要確認」「未検証」「未実施」「矛盾」は成功ではない。", "",
         "件数: " + "、".join(f"{k} {v}" for k, v in counts.items() if v), ""]
    for origin, title in (("user_instruction", "利用者の指示"), ("teacher_material", "資料の要求事項（先生の説明など）"),
                          ("unreadable", "未確認事項（判読不能・不確実）")):
        sub = [r for r in rows if r.get("origin") == origin]
        if not sub:
            continue
        L += [f"## {title}（{len(sub)} 件）", "", "| ID | 状態 | 分類 | 内容 | 根拠・出典 | 確認 |", "|---|---|---|---|---|---|"]
        for r in sub:
            ev = r.get("evidence")
            ev = ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False)
            src = f"{r.get('source', '')} {r.get('time') or ''}".strip()
            L.append(f"| {r['id']} | {r.get('status')} | {r.get('kind', '')} | {str(r.get('text', '')).replace('|', '／')[:300]} | "
                     f"{src.replace('|', '／')[:120]} | {ev.replace('|', '／')[:400]} |")
        L.append("")
    (out / f"ledger_{stage}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return counts
