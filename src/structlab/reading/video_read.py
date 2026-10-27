"""動画のローカル読み取り（無料）: 画面を「場面（スライド・操作画面・実験映像の区間）」に分け、場面ごとの代表画面・OCR・発話を対応付ける。

1. 画面の大きさ（縦向き・横向き）が変わる区切りを見つけ、区切りごとに資料の領域を検出する
   （スマホの画面録画では時計・アドレスバー・黒い余白・プレーヤーの操作部を除く）
2. 1秒ごとの画面の変化から場面を区切る（スライドの切り替え／操作／カメラ映像は一定間隔）。
   画面の上端だけが一時的に変わる場面（通知のバナー）は前の場面に含め、代表画面には使わない
3. 場面ごとに、場面内で最も典型的な（一時的な表示の無い）画面を高解像度で切り出してOCR
4. 同じ画面に戻った場面は「再表示」として同じ内容に結び付ける（AIの読み取りを重複させない）
5. 音声: 音声トラックの長さと無音区間を調べ、音声がある部分だけを文字起こしする（無音・音声の無い部分は書き起こさない）
6. 実験経過の動画（役割 experiment）は一定間隔の区間に分け、末尾で録画の冒頭画面に戻った部分（trim_end）は除外する
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import numpy as np

from .. import media

SLIDE_CUT = 0.015     # スライド動画: これ以上の変化で場面を区切る（同じ体裁で文字だけ変わるスライドも拾う）
OPS_CUT = 0.12        # 操作の画面録画: 画面の切り替えだけで区切り、小さな変化は「操作」として記録
SMALL_CHANGE = 0.004  # これ未満は圧縮のノイズとみなす
OPS_RATE = 3.0        # 小さな変化が毎分これ以上あれば操作の画面録画とみなす
MIN_SCENE = 3.0       # これより短い場面（読み込み・切り替えの途中）は次の場面に含める
TOP_BAND = 0.16       # 通知のバナーが出る上端の帯（画面の高さに対する割合）
SILENCE_DB = -50      # これより小さい音は無音とみなす
SILENCE_MIN = 2.0     # これ以上続く無音を無音区間とする
#: 無音・雑音から作られやすい文字起こし（音声認識の典型的な誤出力）
HALLUCINATION = re.compile(r"ご視聴ありがとうございました|チャンネル登録|字幕|おやすみなさい|ブーブー")


def dhash(img: np.ndarray, size: int = 16) -> int:
    from PIL import Image
    small = np.asarray(Image.fromarray(img).resize((size + 1, size), Image.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------- 画面の大きさの区切り ----------------
def frame_size_at(path: Path, t: float) -> tuple[int, int] | None:
    """t 秒の画面の大きさ（回転の指定を反映した、実際に表示される大きさ）。"""
    try:
        err = media._run(["ffmpeg", "-nostdin", "-v", "info", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-an", "-vf", "showinfo", "-f", "null", "-"], timeout=120).stderr.decode(errors="replace")
        m = re.search(r" s:(\d+)x(\d+) ", err)
        return (int(m.group(1)), int(m.group(2))) if m else None
    except media.MediaError:
        return None


def size_segments(path: Path, info: media.ProbeInfo, step: float = 20.0) -> list[dict]:
    """画面の大きさ（向き）が一定の区間 [{start, end, w, h}]。録画の途中で回転した場合に複数になる。"""
    times = list(np.arange(0.5, max(info.duration, 1.0), step)) or [0.5]
    sizes = [frame_size_at(path, float(t)) or (info.width, info.height) for t in times]
    segs = [{"start": 0.0, "w": sizes[0][0], "h": sizes[0][1]}]
    for (t0, s0), (t1, s1) in zip(zip(times, sizes), zip(times[1:], sizes[1:])):
        if s1 != s0:
            lo, hi = float(t0), float(t1)  # 二分探索で切り替わりを約0.5秒まで絞る
            while hi - lo > 0.5:
                mid = (lo + hi) / 2
                if (frame_size_at(path, mid) or s0) == s0:
                    lo = mid
                else:
                    hi = mid
            segs.append({"start": round(hi, 2), "w": s1[0], "h": s1[1]})
    for a, b in zip(segs, segs[1:]):
        a["end"] = b["start"]
    segs[-1]["end"] = round(info.duration, 3)
    return [s for s in segs if s["end"] - s["start"] > 0.2]


# ---------------- 音声 ----------------
def _stream_seconds(s: dict) -> float:
    """ストリームの長さ。MKV では duration が無く tags.DURATION（"00:20:27.626000000"）にある。"""
    if s.get("duration") not in (None, "N/A"):
        return float(s["duration"])
    tag = (s.get("tags") or {}).get("DURATION")
    if tag:
        h, m, sec = tag.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return 0.0


def split_by_orientation(path: Path, segs: list[dict], log=print, step: float = 2.0) -> list[dict]:
    """画面録画の途中で端末を回し、画面の内容が横倒しになっている部分（録画の縦横は同じまま）を分ける。
    ブラウザ・端末の明るい帯が右（または左）にある時間を「回っている」とみなし、rotate を付けた区間にする。"""
    out: list[dict] = []
    for seg in segs:
        w = 240
        h = max(2, int(round(seg["h"] * w / seg["w"] / 2)) * 2)
        try:
            sm = media.sample_gray_frames(path, seg["start"], seg["end"] - seg["start"], step, w=w, h=h)
        except media.MediaError:
            sm = []
        runs = media.orientation_runs([t for t, _ in sm], [f for _, f in sm])
        if len(runs) <= 1:
            out.append({**seg, "rotate": runs[0][2] if runs else 0})
            continue
        for k, (a, b, rot) in enumerate(runs):
            s0 = seg["start"] if k == 0 else round((runs[k - 1][1] + a) / 2, 3)
            s1 = seg["end"] if k == len(runs) - 1 else round((b + runs[k + 1][0]) / 2, 3)
            out.append({**seg, "start": s0, "end": s1, "rotate": rot})
        log("  画面の向き: " + "、".join(f"{a / 60:.1f}–{b / 60:.1f} 分 {'そのまま' if r == 0 else f'{r} 度回して補正'}"
                                       for a, b, r in runs))
    return out


def audio_profile(path: Path, info: media.ProbeInfo, until: float) -> dict:
    """音声トラックの長さと無音区間。音声が無い部分は文字起こししない（書き起こしを作らない）ために使う。"""
    import json as _json
    try:
        out = media._run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=duration:stream_tags=DURATION", "-of", "json", str(path)], timeout=120).stdout
        streams = _json.loads(out).get("streams", [])
        track = max((_stream_seconds(s) for s in streams), default=0.0)
    except (media.MediaError, ValueError):
        track = 0.0
    if not info.has_audio:
        track = 0.0
    silences: list[list[float]] = []
    if track > 0:
        try:
            err = media._run(["ffmpeg", "-nostdin", "-v", "info", "-t", f"{min(until, track):.3f}", "-i", str(path),
                              "-vn", "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
                             timeout=3600).stderr.decode(errors="replace")
            start = None
            for ln in err.splitlines():
                m = re.search(r"silence_start: ([\d.]+)", ln)
                if m:
                    start = float(m.group(1))
                m = re.search(r"silence_end: ([\d.]+)", ln)
                if m and start is not None:
                    silences.append([round(start, 2), round(float(m.group(1)), 2)])
                    start = None
            if start is not None:
                silences.append([round(start, 2), round(min(until, track), 2)])
        except media.MediaError:
            pass
    no_audio = [[round(track, 2), round(until, 2)]] if track < until - 1 else []
    return {"track_seconds": round(track, 2), "silences": silences, "no_audio_ranges": no_audio}


def voiced_spans(audio: dict, until: float, min_len: float = 1.0) -> list[tuple[float, float]]:
    end = min(until, audio["track_seconds"])
    spans, t = [], 0.0
    for a, b in sorted(audio["silences"]):
        if a > t + min_len:
            spans.append((t, min(a, end)))
        t = max(t, b)
    if end > t + min_len:
        spans.append((t, end))
    return [(a, b) for a, b in spans if b - a >= min_len]


def in_ranges(t: float, ranges: list[list[float]]) -> bool:
    return any(a <= t <= b for a, b in ranges)


def transcribe_spans(path: Path, out_dir: Path, transcriber, spans: list[tuple[float, float]], chunk: float,
                     audio: dict) -> list[dict]:
    speech: list[dict] = []
    for a, b in spans:
        t = a
        while t < b - 0.5:
            length = min(chunk, b - t)
            wav = media.extract_audio(path, t, length, out_dir / "audio_chunk.wav")
            try:
                speech += transcriber.transcribe(wav, t)
            finally:
                wav.unlink(missing_ok=True)
            t += length
    kept = []
    for s in speech:
        mid = (s["start"] + s["end"]) / 2
        silent = in_ranges(mid, audio["silences"]) or mid > audio["track_seconds"]
        weak = (s.get("no_speech_prob") or 0) > 0.5 or (s.get("avg_logprob") or 0) < -1.0
        if silent or (weak and HALLUCINATION.search(s["text"])):
            continue  # 無音・雑音から作られた書き起こしは捨てる
        kept.append(s)
    return kept


# ---------------- 場面 ----------------
def segment_scenes(times: list[float], frames: list[np.ndarray], start: float, end: float,
                   force_mode: str | None = None, window: float = 20.0) -> tuple[list[dict], str]:
    """変化量で場面に分ける。戻り値: 場面 [{start, end, events}], モード（slides / operations / camera / experiment）。"""
    scores = media.scene_scores(list(zip(times, frames)))
    moving = float(np.median(scores[1:])) if len(scores) > 2 else 0.0
    small = sum(1 for x in scores[1:] if SMALL_CHANGE <= x < SLIDE_CUT)
    duration = end - start
    if force_mode == "experiment":
        mode, cut = "experiment", 1.01   # 実験映像: 変化に関係なく一定間隔で区切る
    elif moving > 0.08:
        mode, cut = "camera", max(0.35, moving * 3)
    elif small >= 5 and duration > 0 and small / (duration / 60) > OPS_RATE:
        mode, cut = "operations", OPS_CUT
    else:
        mode, cut = "slides", SLIDE_CUT
    cuts = [times[i] for i in range(1, len(times)) if scores[i] >= cut]
    bounds = [start] + cuts + [end]
    changed = True
    while changed and len(bounds) > 2:
        changed = False
        for i in range(len(bounds) - 1):
            if bounds[i + 1] - bounds[i] < MIN_SCENE:
                del bounds[i + 1 if i + 1 < len(bounds) - 1 else i]
                changed = True
                break
    scenes = [{"start": a, "end": b, "events": []} for a, b in zip(bounds, bounds[1:]) if b > a]
    if mode in ("camera", "experiment"):  # カメラ映像は一定間隔で区切る（内容の把握・時刻の対応付け用）
        out = []
        for sc in scenes:
            t = sc["start"]
            while t < sc["end"] - 0.5:
                out.append({"start": t, "end": min(t + window, sc["end"]), "events": []})
                t += window
        scenes = out
    for i in range(1, len(times)):
        if SMALL_CHANGE <= scores[i] and (scores[i] < cut or times[i] not in bounds):
            for sc in scenes:
                if sc["start"] < times[i] < sc["end"]:
                    sc["events"].append({"time": round(times[i], 1), "change": round(scores[i], 4)})
                    break
    return scenes, mode


def merge_notifications(scenes: list[dict], times: list[float], frames: list[np.ndarray]) -> list[dict]:
    """上端の帯だけが変わった場面（通知のバナーの表示・消去）を前の場面に含める。"""
    def rep(sc):
        idx = [k for k, t in enumerate(times) if sc["start"] <= t < sc["end"]]
        return frames[idx[len(idx) // 2]] if idx else None

    out: list[dict] = []
    for sc in scenes:
        if out:
            a, b = rep(out[-1]), rep(sc)
            if a is not None and b is not None and a.shape == b.shape:
                band = int(a.shape[0] * TOP_BAND)
                diff = np.abs(a.astype(np.int16) - b.astype(np.int16)) >= 24
                if diff[band:].mean() < 0.004 and diff[:band].mean() >= 0.004:
                    out[-1]["end"] = sc["end"]
                    out[-1].setdefault("notifications", []).append(round(sc["start"], 1))
                    out[-1]["events"] += sc["events"]
                    continue
        out.append(sc)
    return out


def typical_time(sc: dict, times: list[float], frames: list[np.ndarray]) -> float:
    """場面内で最も典型的な画面の時刻（場面の中央値の画面に最も近いもの。通知などの一時的な表示を避ける）。"""
    idx = [k for k, t in enumerate(times) if sc["start"] <= t < sc["end"]]
    if not idx:
        return sc["start"] + (sc["end"] - sc["start"]) * 0.8
    stack = np.stack([frames[k] for k in idx]).astype(np.int16)
    med = np.median(stack, axis=0)
    # 後半を少し優先（スライドの書き込み・アニメーションが出そろった状態）
    cost = [float(np.abs(stack[j] - med).mean()) - 0.002 * j for j in range(len(idx))]
    return float(times[idx[int(np.argmin(cost))]])


#: 画面の保存サイズ（資料の部分の長辺）。AI に送れる最大は 2576
#: native: 切り出した資料は元の解像度（長辺が FRAME_MIN_EDGE 未満なら拡大）、画面全体（カメラ映像）は長辺 1568
#: detail: Excel・レポートの作り方の動画。資料の部分を元の解像度のまま可逆（WebP lossless）で保存し、
#:         AI に送るときに全体を 2 倍、部分拡大を 3 倍にする（ai_read.detail_images）
FRAME_MIN_EDGE = 1000
FRAME_FULL_EDGE = 1568
FRAME_MAX_EDGE = 2576


def frame_size(region: media.Region, mode: str = "native", min_edge: int = FRAME_MIN_EDGE) -> dict:
    """extract_frame に渡す幅（資料の部分の長辺が規則どおりになる幅）。"""
    w, h = region.w, region.h
    long_edge = max(w, h)
    if mode == "detail":  # 元の解像度のまま可逆で保存する（拡大は AI に送るときに元の画素から作る）
        edge = long_edge
    elif region.is_full:
        edge = min(FRAME_FULL_EDGE, long_edge)
    else:
        edge = min(FRAME_MAX_EDGE, max(min_edge, long_edge))
    width = edge if w >= h else int(edge * w / h)
    width -= width % 2
    return {"max_width": max(width, 2), "min_width": width if width > w else 0}


DENSE_INTERVAL = 0.2
DENSE_BRIEF = 3        # 連続して変化している間がこの回数以下なら「短時間の表示」とみなし、すべて残す


def dense_changes(path: Path, start: float, end: float, w: int, h: int, crop: str | None,
                  interval: float = DENSE_INTERVAL) -> list[dict]:
    """interval 秒ごとに画面を見て、意味のある変化（マウスの移動などの小さな変化を除く）のあった時刻を返す。
    短時間の表示（変化が DENSE_BRIEF 回以内で終わる）は全部、長く続く変化（スクロール・入力中）は始め・終わりと1秒ごと。"""
    out: list[dict] = []
    prev = None
    run: list[tuple[float, np.ndarray]] = []

    def flush():
        if not run:
            return
        if len(run) <= DENSE_BRIEF:
            keep = run
        else:
            keep = [run[0]] + [x for k, x in enumerate(run[1:-1], 1) if k % max(1, int(1 / interval)) == 0] + [run[-1]]
        for t, f in keep:
            out.append({"time": round(t, 2), "change": None, "dense": True, "hash": dhash(f)})
        run.clear()
    for t, f in media.iter_gray_frames(path, start, max(0.0, end - start), interval, w, h, crop=crop):
        if prev is not None:
            frac = float(np.mean(np.abs(f.astype(np.int16) - prev.astype(np.int16)) >= 24))
            if frac >= SMALL_CHANGE:
                run.append((t, f))
            else:
                flush()
        prev = f
    flush()
    return out


def merge_dense(events: list[dict], dense: list[dict], max_bits: int = 2) -> list[dict]:
    """0.5 秒ごとの変化と 0.2 秒ごとの走査の変化をまとめる（同じ時刻・同じ画面は1つに）。"""
    out = list(events)
    for d in dense:
        if any(abs(d["time"] - e["time"]) <= 0.3 for e in out):
            continue
        out.append({k: v for k, v in d.items() if k != "hash"})
    return sorted(out, key=lambda e: e["time"])


def distinct_events(events: list[dict], times: list[float], frames: list[np.ndarray], max_bits: int = 2) -> list[dict]:
    """場面内の画面の変化をすべて残す（短時間だけ表示された操作・設定を落とさない）。
    直前に残した画面とほぼ同じ（差分ハッシュの差が max_bits 以下）ものだけを除く。"""
    out, last = [], None
    for e in sorted(events, key=lambda e: e["time"]):
        k = min(range(len(times)), key=lambda i: abs(times[i] - e["time"])) if times else None
        h = dhash(frames[k]) if k is not None else None
        if last is not None and h is not None and hamming(last, h) <= max_bits:
            continue
        out.append(e)
        last = h
    return out


def sinfo_of(seg: dict, info: media.ProbeInfo) -> media.ProbeInfo:
    return replace(info, width=seg["w"], height=seg["h"])


def save_frame(path: Path, t: float, out: Path, region: media.Region, info: media.ProbeInfo,
               mode: str = "native") -> Path:
    """画面を保存する。切り出す範囲の外に普段と違う表示がある画面（安全確認に通らない画面）は切り出さずに全体で保存する。"""
    if not region.is_full and not media.crop_is_safe(path, t, region):
        W, H = (info.height, info.width) if region.rotate else (info.width, info.height)
        region = media.Region(0, 0, W, H, W, H, region.rotate)
        out = out.with_name(out.stem + "_full" + out.suffix)
    if mode == "detail":  # 元の映像から取り出した画素をそのまま（可逆）保存する
        png = media.extract_frame(path, t, out.with_suffix(".png"), region=region, **frame_size(region, mode))
        return media.save_lossless(png)
    return media.extract_frame(path, t, out, region=region, **frame_size(region, mode))


# ---------------- 末尾の除外（実験経過動画） ----------------
def detect_return_to_start(times: list[float], frames: list[np.ndarray], audio: dict, duration: float) -> dict:
    """実験映像を再生し終えて録画の冒頭画面に戻った時刻を、次の3つがそろったときだけ決める（推測では除外しない）:
    (1) 末尾が無音（無音が最後まで続く、または音声トラックが終わっている）
    (2) その無音の始まりの前後で画面が大きく切り替わる
    (3) 切り替わった後の画面が、録画の冒頭（再生開始直後）の画面と一致し、切り替わる前より冒頭に近い
    戻り値: {"trim_at": 秒 or None, "status": trimmed/not_found/needs_confirmation, "evidence": {...}}"""
    track = audio["track_seconds"]
    end = min(duration, track) if track else duration
    silent_from = next((a for a, b in audio["silences"] if b >= end - 1.0), None)
    if track and track < duration - 1 and silent_from is None:
        silent_from = track
    if len(times) < 20:
        return {"trim_at": None, "status": "not_found", "evidence": {"reason": "画面のサンプルが少ない"}}
    arr = [f.astype(np.int16) for f in frames]
    # 冒頭の基準: 再生開始直後の操作パネルなどを避け、5〜15秒の画面の平均
    ref_idx = [k for k, t in enumerate(times) if 5.0 <= t <= 15.0] or list(range(min(10, len(times))))
    ref = np.mean([arr[k] for k in ref_idx], axis=0)
    dist = [float(np.abs(a - ref).mean()) for a in arr]
    scores = media.scene_scores(list(zip(times, frames)))
    ev: dict = {"silent_from": silent_from, "audio_track_seconds": track}
    if silent_from is None:
        ev["reason"] = "末尾に無音が無い（冒頭画面に戻った根拠が無い）"
        return {"trim_at": None, "status": "not_found", "evidence": ev}
    win = [k for k, t in enumerate(times) if silent_from - 20 <= t <= silent_from + 8 and k > 0]
    if not win:
        ev["reason"] = "無音の始まり付近の画面が無い"
        return {"trim_at": None, "status": "needs_confirmation", "evidence": ev}
    k = max(win, key=lambda j: scores[j])
    before = [dist[j] for j in range(max(0, k - 20), k)]
    after = [dist[j] for j in range(k, len(times))]
    d_before, d_after = float(np.median(before)), float(np.median(after))
    ev.update({"cut_at": round(times[k], 2), "cut_change": round(scores[k], 4),
               "diff_to_opening_before": round(d_before, 2), "diff_to_opening_after": round(d_after, 2),
               "diff_to_opening_at_start": round(float(np.median([dist[j] for j in ref_idx])), 2)})
    ok = scores[k] >= 0.03 and d_after <= 0.6 * d_before and d_after <= 12.0
    if ok:
        at = round(min(times[k], silent_from), 2)
        ev["reason"] = (f"{times[k]:.1f} 秒で画面が大きく切り替わり（変化 {scores[k]:.1%}）、以降の画面は録画の冒頭と一致"
                        f"（冒頭との差 {d_before:.1f} → {d_after:.1f}）。音声も {silent_from:.1f} 秒から最後まで無音")
        return {"trim_at": at, "status": "trimmed", "evidence": ev}
    ev["reason"] = "末尾は無音だが、画面が冒頭に戻ったことを確認できない（推測で除外しない）"
    return {"trim_at": None, "status": "needs_confirmation", "evidence": ev}


def ocr_image(ocr: media.OCR, path: Path) -> str:
    """スライド・画面向けのOCR: グレースケール化して、疎な文字（psm 11）と段落（psm 6）の結果を合わせる。"""
    if not ocr.available:
        return ""
    from PIL import Image
    g = path.with_name(path.stem + "_g.png")
    im = Image.open(path).convert("L")
    if im.width < 1600:  # 画面は元の解像度で保存しているので、OCR（小さい文字に弱い）のためだけに拡大する
        k = 1600 / im.width
        im = im.resize((1600, int(im.height * k)), Image.LANCZOS)
    im.save(g)
    texts = []
    for psm in ("11", "6"):
        try:
            texts.append(media._run(["tesseract", str(g), "stdout", "-l", ocr.languages, "--psm", psm],
                                    timeout=300).stdout.decode(errors="replace").strip())
        except media.MediaError:
            continue
    g.unlink(missing_ok=True)
    seen, lines = set(), []
    for t in texts:
        for ln in t.splitlines():
            k = "".join(ln.split())
            if k and k not in seen:
                seen.add(k)
                lines.append(ln.strip())
    return "\n".join(lines)


def read_video(path: Path, out_dir: Path, *, ocr: media.OCR, transcriber: media.Transcriber | None,
               interval: float = 1.0, max_events_per_scene: int = 4, frame_width: int = FRAME_MIN_EDGE,
               detail: bool = False,
               asr_chunk: float = 600.0, role: dict | None = None, meta_path: Path | None = None, log=print) -> dict:
    """role: {"kind": "experiment", "window": 15, "trim_end": True} など（資料の役割。configs の reading.roles）
    meta_path: 元のファイル（path が入れ直した MKV のとき）。録画の長さは元のファイルで測る"""
    role = role or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = Path(meta_path or path)
    info = media.probe(meta)
    rec: dict = {"kind": "video", "duration": round(info.duration, 2), "width": info.width, "height": info.height,
                 "has_audio": info.has_audio, "role": role, "scenes": [], "speech": [], "segments": []}
    valid_end = info.duration
    sampled_all: list[tuple[float, np.ndarray, int]] = []
    if info.has_video:
        segs = split_by_orientation(path, size_segments(path, info), log)
        rec["orientation_changes"] = [s["start"] for s in segs[1:]]
        for si, seg in enumerate(segs):
            sinfo = replace(info, width=seg["w"], height=seg["h"], duration=info.duration)
            region = media.detect_content_region(path, sinfo, seg["start"], seg["end"] - seg["start"],
                                                 rotate=seg.get("rotate", 0))
            seg["region"] = region.as_dict()
            seg["region_cropped"] = not region.is_full
            crop = region.vf()
            w = 240
            h = max(2, int(round(region.h * w / region.w / 2)) * 2)
            log(f"  画面の変化を解析（{seg['start'] / 60:.1f}–{seg['end'] / 60:.1f} 分、"
                f"{seg['w']}x{seg['h']}、資料領域 {region.w}x{region.h}）")
            sampled = media.sample_gray_frames(path, seg["start"], seg["end"] - seg["start"], interval, w=w, h=h,
                                               crop=crop)
            sampled_all += [(t, f, si) for t, f in sampled]
            seg["_region"] = region
        rec["segments"] = [{k: v for k, v in s.items() if k != "_region"} for s in segs]
        rec["region"] = segs[0]["region"]
        rec["region_cropped"] = any(s["region_cropped"] for s in segs)
    else:
        segs = []
    audio = audio_profile(path, info, info.duration)  # 入れ直した MKV には扱える音声だけが入っている
    rec["audio"] = audio
    if role.get("trim_end") and sampled_all:
        seg0 = [(t, f) for t, f, si in sampled_all if si == sampled_all[0][2]]
        same_shape = [(t, f) for t, f, si in sampled_all if f.shape == seg0[0][1].shape]
        trim = detect_return_to_start([t for t, _ in same_shape], [f for _, f in same_shape], audio, info.duration)
        rec["trim"] = trim
        if trim["status"] == "trimmed":
            valid_end = trim["trim_at"]
            log(f"  末尾を除外: {valid_end:.1f} 秒以降（{trim['evidence']['reason']}）")
        else:
            log(f"  末尾の除外: {trim['status']}（{trim['evidence'].get('reason')}）")
    rec["valid_end"] = round(valid_end, 2)
    hashes: list[int] = []
    scenes_all: list[dict] = []
    for si, seg in enumerate(segs):
        s_end = min(seg["end"], valid_end)
        if s_end - seg["start"] < 0.5:
            continue
        pts = [(t, f) for t, f, k in sampled_all if k == si and t < s_end]
        times, frames = [t for t, _ in pts], [f for _, f in pts]
        scenes, mode = segment_scenes(times, frames, seg["start"], s_end,
                                      force_mode="experiment" if role.get("kind") == "experiment" else None,
                                      window=float(role.get("window", 20.0)))
        if mode in ("slides", "operations"):
            scenes = merge_notifications(scenes, times, frames)
        seg["mode"] = mode
        dense: list[dict] = []
        if detail:  # 短時間だけ表示された操作・設定を落とさないよう、0.2 秒ごとに画面を見直す
            sh = frames[0].shape if frames else (2, 2)
            dense = dense_changes(path, seg["start"], s_end, sh[1], sh[0], seg["_region"].vf())
            seg["dense_scan"] = {"interval": DENSE_INTERVAL, "distinct": len(dense)}
            rec["segments"][si]["dense_scan"] = seg["dense_scan"]
            log(f"  0.2 秒ごとの走査: 画面の変化 {len(dense)} か所")
        region = seg["_region"]
        for sc in scenes:
            i = len(scenes_all)
            t = typical_time(sc, times, frames)
            idx = min(range(len(times)), key=lambda k: abs(times[k] - t)) if times else 0
            hsh = dhash(frames[idx]) if frames else 0
            same = None if mode == "experiment" else next(
                (j for j, h0 in enumerate(hashes) if h0 is not None and hamming(h0, hsh) <= 6
                 and scenes_all[j]["segment"] == si), None)
            hashes.append(hsh)
            sc.update({"index": i, "segment": si, "mode": mode, "frame_time": round(t, 2), "dhash": f"{hsh:064x}",
                       "same_as": same})
            if same is not None:
                sc["frame"] = scenes_all[same]["frame"]
                sc["ocr"] = scenes_all[same]["ocr"]
            else:
                jpg = save_frame(path, t, out_dir / f"scene_{i:04d}.jpg", region, sinfo_of(seg, info),
                                 "detail" if detail else "native")
                sc["frame"] = str(jpg)
                sc["ocr"] = ocr_image(ocr, jpg) if mode != "experiment" else ""
            if mode == "experiment":  # 区間の始めと終わりの画面も保存（変化の確認用）
                extra = [round(sc["start"] + 0.5, 2), round(max(sc["start"], sc["end"] - 0.5), 2)]
                evs = [{"time": x, "change": None} for x in extra if abs(x - t) > 1.0]
            elif detail:  # Excel・レポートの作り方: 変化した画面をすべて（0.2 秒ごとの走査で見つけた短時間の表示を含む）
                evs = merge_dense(distinct_events(sc["events"], times, frames),
                                  [d for d in dense if sc["start"] <= d["time"] < sc["end"] and abs(d["time"] - t) > 0.3])
            else:
                evs = sorted(sc["events"], key=lambda e: -e["change"])[:max_events_per_scene]
            for e in sorted(evs, key=lambda e: e["time"]):
                jpg = save_frame(path, min(e["time"] + (0.5 if e["change"] and not detail else 0.0), s_end - 0.1),
                                 out_dir / f"scene_{i:04d}_ev_{int(e['time'] * 10):07d}.jpg", region,
                                 sinfo_of(seg, info), "detail" if detail else "native")
                e["frame"] = str(jpg)
            sc["events_with_frames"] = [e for e in evs if e.get("frame")]
            sc["start"], sc["end"] = round(sc["start"], 2), round(sc["end"], 2)
            scenes_all.append(sc)
    for s in rec["segments"]:
        s["mode"] = next((seg.get("mode") for seg in segs if seg["start"] == s["start"]), None)
    rec["scenes"] = scenes_all
    rec["mode"] = rec["segments"][0]["mode"] if rec["segments"] else None
    rec["n_unique_scenes"] = sum(1 for s in scenes_all if s["same_as"] is None)
    if info.has_audio and transcriber is not None and transcriber.available:
        spans = voiced_spans(audio, valid_end)
        log(f"  音声の文字起こし（{transcriber.name}、音声のある {sum(b - a for a, b in spans) / 60:.1f} 分）")
        rec["speech"] = transcribe_spans(path, out_dir, transcriber, spans, asr_chunk, audio)
        rec["transcriber"] = getattr(transcriber, "model_name", transcriber.name)
    for sc in rec["scenes"]:
        sc["speech"] = [s for s in rec["speech"] if sc["start"] <= (s["start"] + s["end"]) / 2 < sc["end"]]
    return rec
