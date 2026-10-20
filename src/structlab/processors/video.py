"""動画・音声: 時間区間ごとに音声の文字起こしと映像のシーン解析を行い、時刻で対応付ける。"""
from __future__ import annotations

from pathlib import Path

from .. import media
from ..models import UnitSpec, fmt_time
from .base import Ctx, Extraction, Processor


def align(speech: list[dict], keyframes: list[dict], seg_end: float) -> list[dict]:
    """発話と画面（キーフレーム）を時刻で対応付ける。

    各発話には「発話開始時点で表示されていた画面」（開始時刻以前で最も新しいキーフレーム）を割り当てる。
    どの発話にも対応しないキーフレームは、発話なしの行として残す。"""
    kfs = sorted(keyframes, key=lambda k: k["time"])
    rows: list[dict] = []
    used: set[int] = set()
    for s in sorted(speech, key=lambda x: x["start"]):
        idx = None
        for i, k in enumerate(kfs):
            if k["time"] <= s["start"] + 0.5:
                idx = i
            else:
                break
        if idx is None and kfs:
            idx = 0
        k = kfs[idx] if idx is not None else None
        if idx is not None:
            used.add(idx)
        rows.append({"t_start": s["start"], "t_end": s["end"], "speech": s["text"],
                     "frame_path": k["path"] if k else None, "frame_time": k["time"] if k else None,
                     "screen_text": k.get("ocr", "") if k else ""})
    for i, k in enumerate(kfs):
        if i not in used:
            nxt = kfs[i + 1]["time"] if i + 1 < len(kfs) else seg_end
            rows.append({"t_start": k["time"], "t_end": nxt, "speech": "", "frame_path": k["path"],
                         "frame_time": k["time"], "screen_text": k.get("ocr", "")})
    rows.sort(key=lambda r: (r["t_start"], r["speech"] == ""))
    return rows


def pick_keyframes(times: list[float], scores: list[float], threshold: float, max_n: int) -> list[int]:
    idx = [i for i, s in enumerate(scores) if i == 0 or s >= threshold]
    if len(idx) > max_n:
        rest = sorted(idx[1:], key=lambda i: scores[i], reverse=True)[:max_n - 1]
        idx = sorted([idx[0], *rest])
    return idx


def focus_spec(s: float, e: float, reason: str, duration: float) -> UnitSpec:
    """重要場面の追加解析単位（最長30秒、動画の終端を超えない）。"""
    s = max(0.0, min(s, duration - 1.0))
    e = min(e, s + 30.0, duration - 0.2)
    return UnitSpec("video_focus", {"start": round(s, 3), "end": round(max(e, s + 0.5), 3), "reason": reason,
                                    "has_video": True, "duration": round(duration, 3)}, 0)


def _merge_windows(wins: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for s, e, r in sorted(wins):
        if out and s <= out[-1][1]:
            ps, pe, pr = out[-1]
            out[-1] = (ps, max(pe, e), pr if r in pr else f"{pr} / {r}")
        else:
            out.append((s, e, r))
    return out


class VideoProcessor(Processor):
    kind = "video"

    def plan(self, path: Path, ctx: Ctx) -> list[UnitSpec]:
        info = media.probe(path)
        if not info.has_video and not info.has_audio:
            raise ValueError("映像・音声ストリームがありません")
        seg = float(ctx.cfg.data["video"]["segment_seconds"])
        units, t, i = [], 0.0, 0
        while t < info.duration - 0.05:
            end = min(t + seg, info.duration)
            units.append(UnitSpec("video_segment", {"start": round(t, 3), "end": round(end, 3),
                                                    "duration": round(info.duration, 3),
                                                    "has_video": info.has_video, "has_audio": info.has_audio}, i))
            t, i = end, i + 1
        return units

    def label(self, unit: UnitSpec) -> str:
        lo = unit.locator
        tag = "重要場面 " if unit.unit_type == "video_focus" else ""
        return f"{tag}{fmt_time(lo['start'])}–{fmt_time(lo['end'])}"

    def extract(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        if unit.unit_type == "video_focus":
            return self._focus(path, unit, ctx)
        vc = ctx.cfg.data["video"]
        lo = unit.locator
        start, end = float(lo["start"]), float(lo["end"])
        length = end - start
        ex = Extraction(t_start=start, t_end=end)
        art = ctx.artifacts_dir
        art.mkdir(parents=True, exist_ok=True)

        speech: list[dict] = []
        if lo.get("has_audio"):
            if ctx.transcriber.available:
                wav = media.extract_audio(path, start, length, art / "audio.wav")
                try:
                    speech = ctx.transcriber.transcribe(wav, start)
                finally:
                    wav.unlink(missing_ok=True)
            else:
                ex.needs_ai.append("音声は文字起こし未実施（transcription.backend=none）")

        keyframes: list[dict] = []
        if lo.get("has_video"):
            frames = media.sample_gray_frames(path, start, length, float(vc["sample_interval_seconds"]))
            scores = media.scene_scores(frames)
            times = [t for t, _ in frames]
            for i in pick_keyframes(times, scores, float(vc["scene_change_threshold"]),
                                    int(vc["max_keyframes_per_segment"])):
                t = times[i]
                jpg = media.extract_frame(path, t, art / f"frame_{int(t * 1000):010d}.jpg")
                kf = {"time": t, "path": str(jpg), "score": round(scores[i], 4)}
                if ctx.ocr.available:
                    kf["ocr"] = ctx.ocr.image_to_text(jpg)
                keyframes.append(kf)
            ex.data["n_sampled_frames"] = len(frames)
            if keyframes:
                ex.needs_ai.append("画面の内容（Excel操作・数式・グラフ）の解釈にはAI解析が必要")

        rows = align(speech, keyframes, end)
        kws = list(vc["important_keywords"])
        windows: list[tuple[float, float, str]] = []
        for r in rows:
            hits = [k for k in kws if k in (r["speech"] or "")]
            if hits:
                r["important"] = 1
                r["reason"] = "発話キーワード: " + "・".join(hits)
                windows.append((max(0.0, r["t_start"] - 5), r["t_end"] + 10, r["reason"]))
        ex.timeline = rows
        if vc.get("focus_analysis") and lo.get("has_video"):
            dur = float(lo.get("duration") or end)
            for s, e, reason in _merge_windows(windows):
                ex.followups.append(focus_spec(s, e, reason, dur))
        ex.data.update({"n_speech": len(speech), "n_keyframes": len(keyframes),
                        "keyframes": [{k: v for k, v in kf.items() if k != "ocr"} for kf in keyframes],
                        "transcriber": ctx.transcriber.name, "ocr": ctx.ocr.label})
        ex.segments = [{"text": s["text"], "origin": "speech", "t_start": s["start"], "t_end": s["end"]} for s in speech]
        ex.segments += [{"text": k.get("ocr", ""), "origin": "screen", "t_start": k["time"], "t_end": k["time"]}
                        for k in keyframes if k.get("ocr")]
        ex.text = "\n".join(s["text"] for s in speech) + "\n" + "\n".join(k.get("ocr", "") for k in keyframes)
        ex.images = [(Path(k["path"]), f"{fmt_time(k['time'])} の画面") for k in keyframes]
        lines = [f"### 区間 {fmt_time(start)}–{fmt_time(end)}", "### 文字起こし（時刻付き）"]
        lines += [f"[{fmt_time(s['start'])}–{fmt_time(s['end'])}] {s['text']}" for s in speech] or ["（なし）"]
        lines.append("### 音声と画面の対応")
        for r in rows:
            lines.append(f"[{fmt_time(r['t_start'])}] 画面={fmt_time(r['frame_time']) if r['frame_time'] is not None else '-'}"
                         f" 発話={r['speech'] or '-'}" + (f" ★{r['reason']}" if r.get("important") else ""))
        if any(k.get("ocr") for k in keyframes):
            lines.append("### 画面OCR")
            lines += [f"[{fmt_time(k['time'])}] {k['ocr'][:800]}" for k in keyframes if k.get("ocr")]
        ex.ai_text = "\n".join(lines)
        return ex

    def _focus(self, path: Path, unit: UnitSpec, ctx: Ctx) -> Extraction:
        lo = unit.locator
        start, end = float(lo["start"]), float(lo["end"])
        n = max(1, int(ctx.cfg.data["ai"]["max_images_per_call"]))
        step = max((end - start) / n, 1.0)
        ex = Extraction(t_start=start, t_end=end, data={"reason": lo.get("reason", "")})
        t = start
        frames = []
        while t < end and len(frames) < n:
            jpg = media.extract_frame(path, t, ctx.artifacts_dir / f"focus_{int(t * 1000):010d}.jpg", max_width=1920)
            frames.append({"time": t, "path": str(jpg),
                           "ocr": ctx.ocr.image_to_text(jpg) if ctx.ocr.available else ""})
            t += step
        speech = ctx.timeline_lookup(start, end) if ctx.timeline_lookup else []
        ex.images = [(Path(f["path"]), f"{fmt_time(f['time'])} の画面（高解像度）") for f in frames]
        ex.text = "\n".join(r.get("speech") or "" for r in speech) + "\n" + "\n".join(f["ocr"] for f in frames)
        ex.segments = [{"text": f["ocr"], "origin": "screen", "t_start": f["time"], "t_end": f["time"]}
                       for f in frames if f["ocr"]]
        ex.data["frames"] = [{"time": f["time"], "path": f["path"]} for f in frames]
        lines = [f"### 重要場面 {fmt_time(start)}–{fmt_time(end)}（理由: {lo.get('reason', '')}）", "### 発話"]
        lines += [f"[{fmt_time(r['t_start'])}] {r['speech']}" for r in speech if r.get("speech")] or ["（なし）"]
        ex.ai_text = "\n".join(lines)
        ex.needs_ai.append("重要場面の詳細（セル番地・メニュー操作）の読み取りにはAI解析が必要")
        return ex
