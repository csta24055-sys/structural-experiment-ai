"""教員の発言・資料の記述から出題根拠（手がかり）を見つけ、対象（論点・式・ページ）を特定する。

手がかりの文だけで判断せず、前後の発言・同じ時刻の画面・対応するスライドを文脈として集める。
「この式は重要」の「この式」が特定できなければ target は None（要確認）のまま残す。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import text as T


@dataclass
class Cue:
    type: str
    label: str
    polarity: str          # + / - / 0
    strength: int
    text: str              # 手がかりの文（原文）
    material: str
    lecture: int | None = None
    page: int | None = None
    start: float | None = None
    end: float | None = None
    context: str = ""      # 前後の発言・画面の文字
    formulas: list[str] = field(default_factory=list)  # 文脈にある式（「この式」の候補）
    target_topic: str | None = None
    target_note: str = ""
    order: float = 0.0     # 時系列（講義回 * 1e5 + 秒）。後の指示が前の指示を上書きするかの判定に使う

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def compile_patterns(cfg: dict) -> list[tuple[str, dict, re.Pattern]]:
    out = []
    for name, spec in (cfg.get("cues") or {}).items():
        for p in spec.get("patterns", []):
            out.append((name, spec, re.compile(p)))
    return out


def find_cues(units: list[dict], material: str, patterns, lecture: int | None = None,
              window: float = 45.0) -> list[Cue]:
    """units（1 資料分）から手がかりを探す。同じ文に複数の型が当たれば強い方 1 つ（除外は必ず残す）。"""
    cues: list[Cue] = []
    speech = [u for u in units if u["kind"] == "speech"]
    scenes = [u for u in units if u["kind"] == "scene"]
    for u in units:
        if u["kind"] not in ("speech", "page", "text"):
            continue
        for sent in (T.sentences(u["text"]) if u["kind"] != "speech" else [T.norm(u["text"])]):
            hits = [(name, spec) for name, spec, rx in patterns if rx.search(sent)]
            if not hits:
                continue
            neg = [h for h in hits if str(h[1].get("polarity")) == "-"]
            # 「出ます」と「出ません」の両方に当たる場合は否定を優先する
            chosen = neg[:1] or sorted(hits, key=lambda h: -int(h[1].get("strength", 1)))[:1]
            for name, spec in chosen:
                c = Cue(type=name, label=spec.get("label", name), polarity=str(spec.get("polarity", "+")),
                        strength=int(spec.get("strength", 1)), text=sent, material=material, lecture=lecture,
                        page=u.get("page"), start=u.get("start"), end=u.get("end"))
                if u["kind"] == "speech":
                    t0 = u["start"]
                    ctx = [s["text"] for s in speech if t0 - window <= s["start"] <= t0 + window]
                    scr = [s["text"] for s in scenes if s["start"] - 1 <= t0 <= s["end"] + 1 and s["text"]]
                    c.context = " ".join(ctx + scr)
                    c.formulas = [f for s in scr for f in T.formulas(s)]
                    c.order = (lecture or 0) * 1e5 + t0
                else:
                    c.context = u["text"]
                    c.formulas = T.formulas(u["text"]) if "式" in sent else []
                    c.order = (lecture or 0) * 1e5 + (u.get("page") or 0)
                cues.append(c)
    return cues


def resolve_targets(cues: list[Cue], topics: list[dict], slide_for_time=None, min_sim: float = 0.25) -> None:
    """手がかりを論点に結びつける。

    1) 文中に論点の題名がそのまま出ていれば、その論点（画面より発言の明示を優先）
    2) 発言の時刻に表示されていたスライド（slide_for_time）があれば、そのページの論点
    3) 文と前後の文脈が最も似ている論点（しきい値未満なら特定できない）
    """
    def score(c, t):
        title = T.norm(t["title"]).replace(" ", "")
        if len(title) >= 2 and title in T.norm(c.text).replace(" ", ""):
            return 1.0 + len(title) / 100  # 論点の題名がその文に出ている（長い題名を優先）
        s_text = T.similarity(c.text, t["title"] + " " + t["summary"])
        return max(s_text, 0.6 * T.similarity(c.context, t["title"] + " " + t["summary"]))

    for c in cues:
        best, how = None, ""
        scored = sorted(((score(c, t), t["id"]) for t in topics), reverse=True)
        if scored and scored[0][0] >= 1.0:
            best, how = scored[0][1], "文中に論点名"
        if best is None and slide_for_time is not None and c.start is not None:
            tid = slide_for_time(c.material, c.start)
            if tid:
                best, how = tid, "発言時に表示されていたスライドの論点"
        if best is None and scored and scored[0][0] >= min_sim:
            best, how = scored[0][1], f"文脈の類似（{scored[0][0]:.2f}）"
        if c.formulas and "式" in c.text and best is None:
            how = "式は特定できたが論点に結びつかない"
        c.target_topic = best
        c.target_note = how or "対象を特定できない（要確認）"
