"""動画のローカル読み取り（無料）: 画面を「場面（スライド・操作画面）」に分け、場面ごとの代表画面・OCR・発話を対応付ける。

1. 資料の領域を検出（スマホの画面録画では時計・アドレスバー・黒い余白を除く）。領域だけで画面の変化を比べる
2. 1秒ごとの画面の変化から場面を区切る（大きな変化＝場面の切り替え、小さな変化＝場面内の操作・書き込み）
3. 場面ごとに、変化が落ち着いた代表画面を高解像度で切り出してOCR（拡大＋グレースケール＋疎な文字向けの設定）
4. 同じ画面に戻った場面は「再表示」として同じ内容に結び付ける（AIの読み取りを重複させない）
5. 音声は全体を文字起こしし、時刻で場面に割り当てる
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import media

SLIDE_CUT = 0.015     # スライド動画: これ以上の変化で場面を区切る（同じ体裁で文字だけ変わるスライドも拾う）
OPS_CUT = 0.12        # 操作の画面録画: 画面の切り替えだけで区切り、小さな変化は「操作」として記録
SMALL_CHANGE = 0.004  # これ未満は圧縮のノイズとみなす
OPS_RATE = 3.0        # 小さな変化が毎分これ以上あれば操作の画面録画とみなす
MIN_SCENE = 3.0       # これより短い場面（読み込み・切り替えの途中）は次の場面に含める


def dhash(img: np.ndarray, size: int = 16) -> int:
    from PIL import Image
    small = np.asarray(Image.fromarray(img).resize((size + 1, size), Image.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def segment_scenes(times: list[float], frames: list[np.ndarray], duration: float) -> tuple[list[dict], str]:
    """変化量で場面に分ける。戻り値: 場面 [{start, end, events}], モード（slides / operations / camera）。"""
    scores = media.scene_scores(list(zip(times, frames)))
    moving = float(np.median(scores[1:])) if len(scores) > 2 else 0.0
    small = sum(1 for x in scores[1:] if SMALL_CHANGE <= x < SLIDE_CUT)  # スライドの切り替えより小さい変化
    if moving > 0.08:
        mode, cut = "camera", max(0.35, moving * 3)
    elif small >= 5 and duration > 0 and small / (duration / 60) > OPS_RATE:
        mode, cut = "operations", OPS_CUT
    else:
        mode, cut = "slides", SLIDE_CUT
    cuts = [times[i] for i in range(1, len(times)) if scores[i] >= cut]
    bounds = [0.0] + cuts + [duration]
    # 短すぎる場面は次の場面に含める（最後の場面だけは前に含める）
    changed = True
    while changed and len(bounds) > 2:
        changed = False
        for i in range(len(bounds) - 1):
            if bounds[i + 1] - bounds[i] < MIN_SCENE:
                del bounds[i + 1 if i + 1 < len(bounds) - 1 else i]
                changed = True
                break
    scenes = [{"start": a, "end": b, "events": []} for a, b in zip(bounds, bounds[1:]) if b > a]
    if mode == "camera":  # カメラ撮影は長い場面を一定間隔で区切る（内容の把握用）
        out = []
        for sc in scenes:
            t = sc["start"]
            while t < sc["end"]:
                out.append({"start": t, "end": min(t + 20.0, sc["end"]), "events": []})
                t += 20.0
        scenes = out
    for i in range(1, len(times)):
        if SMALL_CHANGE <= scores[i] and (scores[i] < cut or times[i] not in bounds):
            for sc in scenes:
                if sc["start"] < times[i] < sc["end"]:
                    sc["events"].append({"time": round(times[i], 1), "change": round(scores[i], 4)})
                    break
    return scenes, mode


def ocr_image(ocr: media.OCR, path: Path) -> str:
    """スライド・画面向けのOCR: グレースケール化して、疎な文字（psm 11）と段落（psm 6）の結果を合わせる。"""
    if not ocr.available:
        return ""
    from PIL import Image
    g = path.with_name(path.stem + "_g.png")
    Image.open(path).convert("L").save(g)
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
               interval: float = 1.0, max_events_per_scene: int = 4, frame_width: int = 1800,
               asr_chunk: float = 600.0, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    info = media.probe(path)
    region = media.detect_content_region(path, info) if info.has_video else None
    rec: dict = {"kind": "video", "duration": round(info.duration, 2), "width": info.width, "height": info.height,
                 "has_audio": info.has_audio, "region": region.as_dict() if region else None,
                 "region_cropped": bool(region and not region.is_full), "scenes": [], "speech": []}
    if info.has_video and region is not None:
        crop = None if region.is_full else region.ffmpeg_crop()
        w = 240
        h = max(2, int(round(region.h * w / region.w / 2)) * 2)
        log(f"  画面の変化を解析（{info.duration / 60:.1f} 分、資料領域 {region.w}x{region.h}）")
        sampled = media.sample_gray_frames(path, 0.0, info.duration, interval, w=w, h=h, crop=crop)
        times = [t for t, _ in sampled]
        frames = [f for _, f in sampled]
        scenes, mode = segment_scenes(times, frames, info.duration)
        rec["mode"] = mode
        hashes: list[int] = []
        for i, sc in enumerate(scenes):
            # 代表画面: 場面の終わり寄り（スライドの書き込み・アニメーションが出そろった状態）
            t = sc["start"] + (sc["end"] - sc["start"]) * 0.8
            idx = min(range(len(times)), key=lambda k: abs(times[k] - t)) if times else 0
            hsh = dhash(frames[idx]) if frames else 0
            same = next((j for j, h0 in enumerate(hashes) if hamming(h0, hsh) <= 6), None)
            hashes.append(hsh)
            sc.update({"index": i, "frame_time": round(t, 2), "dhash": f"{hsh:064x}",
                       "same_as": same})
            if same is not None:
                sc["frame"] = scenes[same]["frame"]
                sc["ocr"] = scenes[same]["ocr"]
            else:
                jpg = media.extract_frame(path, t, out_dir / f"scene_{i:04d}.jpg", max_width=2400, region=region,
                                          min_width=frame_width)
                sc["frame"] = str(jpg)
                sc["ocr"] = ocr_image(ocr, jpg)
            # 場面内の操作・書き込み（大きいものから最大 N 件の画面を保存）
            evs = sorted(sc["events"], key=lambda e: -e["change"])[:max_events_per_scene]
            for e in sorted(evs, key=lambda e: e["time"]):
                jpg = media.extract_frame(path, e["time"] + 0.5, out_dir / f"scene_{i:04d}_ev_{int(e['time'] * 10):07d}.jpg",
                                          max_width=2400, region=region, min_width=frame_width)
                e["frame"] = str(jpg)
            sc["events_with_frames"] = [e for e in evs if e.get("frame")]
            sc["start"], sc["end"] = round(sc["start"], 2), round(sc["end"], 2)
        rec["scenes"] = scenes
        rec["n_unique_scenes"] = sum(1 for s in scenes if s["same_as"] is None)
    if info.has_audio and transcriber is not None and transcriber.available:
        log(f"  音声の文字起こし（{transcriber.name}）")
        t = 0.0
        while t < info.duration:
            length = min(asr_chunk, info.duration - t)
            wav = media.extract_audio(path, t, length, out_dir / "audio_chunk.wav")
            try:
                rec["speech"] += transcriber.transcribe(wav, t)
            finally:
                wav.unlink(missing_ok=True)
            t += length
        rec["transcriber"] = transcriber.name
    for sc in rec["scenes"]:
        sc["speech"] = [s for s in rec["speech"] if sc["start"] <= (s["start"] + s["end"]) / 2 < sc["end"]]
    return rec
