"""ffmpeg / tesseract / 文字起こしのラッパー。動画は区間ごとに切り出して処理し、全体をメモリに載せない。"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class MediaError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 1800, capture: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, timeout=timeout,
                              stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        raise MediaError(f"コマンドが見つかりません: {cmd[0]}（インストールしてください）") from e
    except subprocess.CalledProcessError as e:
        raise MediaError(f"{cmd[0]} 失敗: {e.stderr.decode(errors='replace')[-800:]}") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} がタイムアウトしました") from e


@dataclass
class ProbeInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0


def probe(path: Path) -> ProbeInfo:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               timeout=120).stdout
    info = json.loads(out)
    streams = info.get("streams", [])
    v = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    dur = float(info.get("format", {}).get("duration") or 0)
    if not dur:
        for s in streams:
            if s.get("duration"):
                dur = max(dur, float(s["duration"]))
    if dur <= 0:
        raise MediaError("動画の長さを取得できません（破損またはアップロード途中の可能性）")
    return ProbeInfo(duration=dur, has_video=bool(v), has_audio=bool(a),
                     width=int(v[0].get("width", 0)) if v else 0, height=int(v[0].get("height", 0)) if v else 0)


def extract_audio(path: Path, start: float, length: float, out_wav: Path) -> Path:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path),
          "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_wav)])
    return out_wav


def sample_gray_frames(path: Path, start: float, length: float, interval: float,
                       w: int = 320, h: int = 180, crop: str | None = None) -> list[tuple[float, np.ndarray]]:
    """区間内を interval 秒ごとに低解像度グレースケールでサンプリング（シーン検出用）。
    crop（ffmpeg の crop=…）を指定すると資料の領域だけで比較する（時計などの変化を拾わない）。"""
    vf = f"fps=1/{interval}," + (f"{crop}," if crop else "") + f"scale={w}:{h},format=gray"
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path),
         "-vf", vf, "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames = []
    size = w * h
    i = 0
    assert proc.stdout is not None
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        frames.append((start + i * interval, np.frombuffer(buf, dtype=np.uint8).reshape(h, w)))
        i += 1
    _, err = proc.communicate()
    if proc.returncode != 0:
        raise MediaError(f"フレーム抽出に失敗: {err.decode(errors='replace')[-500:]}")
    return frames


def iter_gray_frames(path: Path, start: float, length: float, interval: float, w: int, h: int,
                     crop: str | None = None):
    """sample_gray_frames と同じだが1枚ずつ返す（細かい間隔で長い動画を見てもメモリを使わない）。"""
    vf = f"fps=1/{interval}," + (f"{crop}," if crop else "") + f"scale={w}:{h},format=gray"
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path),
         "-vf", vf, "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h
    i = 0
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i * interval, np.frombuffer(buf, dtype=np.uint8).reshape(h, w)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


def save_lossless(src_png: Path) -> Path:
    """ffmpeg で取り出した PNG を WebP（可逆）にする。元の画素は1つも変えずに容量を小さくする。"""
    from PIL import Image
    out = src_png.with_suffix(".webp")
    with Image.open(src_png) as im:
        im.save(out, lossless=True, method=4)
    src_png.unlink(missing_ok=True)
    return out


def scene_scores(frames: list[tuple[float, np.ndarray]], pixel_delta: int = 24) -> list[float]:
    """直前のサンプルから「明るさが pixel_delta 以上変わった画素の割合」(0〜1)。
    画面録画では文字やセルだけが変わることが多いので、平均差分ではなく変化画素の割合を使う。"""
    scores = [1.0] if frames else []
    for (_, a), (_, b) in zip(frames, frames[1:]):
        diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
        scores.append(float(np.mean(diff >= pixel_delta)))
    return scores


@dataclass
class Region:
    """動画の中で資料（スライド・アプリ画面）が映っている矩形（元動画の画素座標）。"""
    x: int
    y: int
    w: int
    h: int
    full_w: int
    full_h: int
    # 端末を横にして録画した部分などで、画面の内容が 90 度回っているときの補正（0 / 90: 左に回す / 270: 右に回す）。
    # 座標・full_w・full_h は回した後の画面のもの
    rotate: int = 0

    @property
    def is_full(self) -> bool:
        return self.w * self.h >= 0.9 * self.full_w * self.full_h

    def ffmpeg_crop(self) -> str:
        # 録画の途中で画面の向き・大きさが変わっても失敗しないよう、切り出す範囲を画面の大きさに収める
        return (f"crop=w='min({self.w}\\,iw)':h='min({self.h}\\,ih)'"
                f":x='min({self.x}\\,iw-ow)':y='min({self.y}\\,ih-oh)'")

    def vf(self) -> str | None:
        """回転の補正と切り出しの ffmpeg フィルタ（どちらも不要なら None）。"""
        parts = [TRANSPOSE[self.rotate]] if self.rotate else []
        if not self.is_full:
            parts.append(self.ffmpeg_crop())
        return ",".join(parts) or None

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "full_w": self.full_w, "full_h": self.full_h,
                "rotate": self.rotate}


TRANSPOSE = {90: "transpose=2", 270: "transpose=1"}  # 90: 反時計回り（右側にあった画面の上端を上に戻す）


def _runs(flags: np.ndarray, max_gap: int = 0) -> list[tuple[int, int]]:
    """True が続く区間。max_gap 以下の短い途切れ（資料内の罫線など）はつなげる。"""
    out: list[tuple[int, int]] = []
    start = None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if out and start - out[-1][1] <= max_gap:
                out[-1] = (out[-1][0], i)
            else:
                out.append((start, i))
            start = None
    return out


def grab_gray(path: Path, t: float, w: int, h: int, crop: str | None = None) -> np.ndarray:
    vf = (f"{crop}," if crop else "") + f"scale={w}:{h},format=gray"
    out = _run(["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                "-vf", vf, "-f", "rawvideo", "-"], timeout=120).stdout
    if len(out) < w * h:
        raise MediaError(f"フレームを取得できません: t={t:.1f}")
    return np.frombuffer(out[:w * h], dtype=np.uint8).reshape(h, w)


def ui_orientation(frame: np.ndarray) -> int:
    """画面録画の1フレーム（縮小したグレースケール）で、ブラウザ・端末の明るい帯がどこにあるかから画面の向きを推定する。
    上にあれば 0（そのまま）、右にあれば 90、左にあれば 270。判断できなければ 0（カメラ映像など）。"""
    h, w = frame.shape
    b = max(2, int(min(h, w) * 0.11))
    top, right, left = frame[:b].mean(), frame[:, -b:].mean(), frame[:, :b].mean()
    if right > 150 and top < 100 and left < 100:
        return 90
    if left > 150 and top < 100 and right < 100:
        return 270
    return 0


def orientation_runs(times: list[float], frames: list[np.ndarray], min_len: int = 3) -> list[tuple[float, float, int]]:
    """フレームごとの向きを、min_len 回未満の短い揺れを前後に合わせてならし、(開始, 終了, 向き) の区間にする。"""
    if not times:
        return []
    lab = [ui_orientation(f) for f in frames]
    runs: list[list] = []
    for t, o in zip(times, lab):
        if runs and runs[-1][2] == o:
            runs[-1][1] = t
            runs[-1][3] += 1
        else:
            runs.append([t, t, o, 1])
    changed = True
    while changed and len(runs) > 1:  # 短い区間を長い方の隣に吸収する
        changed = False
        for i, r in enumerate(runs):
            if r[3] < min_len:
                j = i - 1 if i > 0 and (i == len(runs) - 1 or runs[i - 1][3] >= runs[i + 1][3]) else i + 1
                runs[j][0], runs[j][1] = min(runs[j][0], r[0]), max(runs[j][1], r[1])
                runs[j][3] += r[3]
                del runs[i]
                changed = True
                break
        merged: list[list] = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1], merged[-1][3] = r[1], merged[-1][3] + r[3]
            else:
                merged.append(r)
        runs = merged
    return [(r[0], r[1], r[2]) for r in runs]


def detect_content_region(path: Path, info: ProbeInfo, start: float = 0.0, length: float | None = None,
                          samples: int = 16, work_w: int = 360, rotate: int = 0) -> Region:
    """画面録画（スマホで再生したスライドなど）で、時計・アドレスバー・黒い余白を除いた資料の領域を求める。
    区間内の複数フレームで「背景色と違う画素」が多い行・列の最大の塊を資料とみなす。
    資料が画面いっぱいの動画（カメラ撮影など）では全体を返す。"""
    W, H = (info.height, info.width) if rotate else (info.width, info.height)
    full = Region(0, 0, W, H, W, H, rotate)
    pre = TRANSPOSE.get(rotate)
    if not (W and H):
        return full
    length = info.duration - start if length is None else length
    work_h = max(2, int(round(H * work_w / W / 2)) * 2)
    step = max(length / samples, 0.5)
    frames = []
    for i in range(samples):  # フレームごとに直接シーク（全体をデコードしない）
        t = start + step * (i + 0.5)
        if t >= start + length:
            break
        try:
            frames.append((t, grab_gray(path, t, work_w, work_h, crop=pre)))
        except MediaError:
            continue
    if len(frames) < 2:
        return full
    stack = np.stack([f for _, f in frames]).astype(np.int16)
    box = content_box(stack)
    if box is None:
        return full
    a, b, c0, c1 = box
    sx, sy = W / work_w, H / work_h
    pad = 2
    x, y = max(0, int((c0 - pad) * sx)), max(0, int((a - pad) * sy))
    x1, y1 = min(W, int((c1 + pad) * sx)), min(H, int((b + pad) * sy))
    reg = Region(x - x % 2, y - y % 2, (x1 - x) - (x1 - x) % 2, (y1 - y) - (y1 - y) % 2, W, H, rotate)
    # 小さすぎる・ほぼ全体なら切り出さない
    if reg.w * reg.h < 0.04 * W * H or reg.is_full:
        return full
    # 切り出しの安全確認用: 縮小した画面の中央値（外側＝余白・ブラウザの表示の「普段の姿」）と、その中の資料の範囲
    reg.ref = np.median(stack, axis=0).astype(np.int16)
    reg.ref_box = (max(0, a - pad), min(work_h, b + pad), max(0, c0 - pad), min(work_w, c1 + pad))
    return reg


def _content_box_once(stack: np.ndarray, frac: float, edge: float, line: float = 0.25) -> tuple[int, int, int, int] | None:
    n, h, w = stack.shape
    # 背景色: 左右の端（上下の端は時計・ツールバーなので除く）で最も多い明るさ（黒い余白・白い余白のどちらにも対応）
    m = max(2, int(w * 0.04))
    mid = stack[:, int(h * 0.1):int(h * 0.9), :]
    band = np.concatenate([mid[:, :, :m].reshape(-1), mid[:, :, -m:].reshape(-1)])
    bg = int(np.bincount(band.clip(0, 255).astype(np.int64), minlength=256).argmax())
    content = (np.abs(stack - bg) > 32).mean(axis=0) > frac
    rows = content.mean(axis=1) > line
    # 上下の端に接する細い塊（ステータスバー・ツールバー）は除く。端まで続く大きな塊（再生領域）は残す
    runs = [(a, b) for a, b in _runs(rows, max_gap=max(1, int(h * 0.01)))
            if b - a >= h * 0.05 and ((a > h * edge and b < h * (1 - edge)) or b - a >= h * 0.4)]
    if not runs:
        return None
    a, b = max(runs, key=lambda r: r[1] - r[0])
    cols = content[a:b].mean(axis=0) > line
    cruns = [r for r in _runs(cols, max_gap=max(1, int(w * 0.01))) if r[1] - r[0] >= w * 0.1]
    if not cruns:
        return None
    return a, b, min(r[0] for r in cruns), max(r[1] for r in cruns)


def crop_is_safe(path: Path, t: float, region: Region, limit: float = 0.02) -> bool:
    """時刻 t の画面で、切り出す範囲の外が普段の姿（余白・ブラウザの表示）のままかを確かめる。
    外にも普段と違う表示（拡大された動画、別の資料など）がある画面は、切り出さずに画面全体で保存する（情報を落とさない）。"""
    ref = getattr(region, "ref", None)
    if ref is None or region.is_full:
        return True
    h, w = ref.shape
    try:
        cur = grab_gray(path, t, w, h, crop=TRANSPOSE.get(region.rotate)).astype(np.int16)
    except MediaError:
        return False
    a, b, c0, c1 = region.ref_box
    diff = np.abs(cur - ref) > 40
    diff[a:b, c0:c1] = False
    return float(diff.mean()) <= limit


def outside_activity(stack: np.ndarray, box: tuple[int, int, int, int], pad: int = 3) -> float:
    """切り出す範囲（少し広げる）の外で、フレーム間で明るさが大きく変わる画素の割合。
    時計・シークバーの小さな変化は数%未満に収まり、資料が外にはみ出していれば大きくなる（切り出しの安全確認）。"""
    if len(stack) < 2:
        return 0.0
    a, b, c0, c1 = box
    moving = (stack.max(axis=0) - stack.min(axis=0)) > 40
    mask = np.ones(moving.shape, bool)
    mask[max(0, a - pad):b + pad, max(0, c0 - pad):c1 + pad] = False
    return float(moving[mask].mean()) if mask.any() else 0.0


def _player_frame(sub: np.ndarray) -> bool:
    """領域の外周が、ほぼ一様な暗い余白（ブラウザの動画プレーヤーの背景）かどうか。"""
    n, h, w = sub.shape
    if h < 10 or w < 10:
        return False
    m = max(1, int(min(h, w) * 0.03))
    border = np.concatenate([sub[:, :m, :].reshape(-1), sub[:, -m:, :].reshape(-1),
                             sub[:, :, :m].reshape(-1), sub[:, :, -m:].reshape(-1)])
    bg = int(np.median(border))
    return bg < 60 and float((np.abs(border - bg) <= 32).mean()) > 0.9


def content_box(stack: np.ndarray, max_depth: int = 2) -> tuple[int, int, int, int] | None:
    """縮小した画面（フレーム×高さ×幅）から資料の領域 (上, 下, 左, 右) を求める。
    ブラウザの動画プレーヤーのように「白い画面の中の黒い再生領域の中に動画」がある入れ子は、
    見つけた領域の中でもう一度探す（内側の余白の色を背景とし、過半数のフレームで違う画素だけを資料とみなすので、
    一時的に出る再生ボタン・シークバーは含めない）。"""
    box = _content_box_once(stack, 0.3, 0.03)
    if box is None:
        return None
    for _ in range(max_depth - 1):
        a, b, c0, c1 = box
        sub = stack[:, a:b, c0:c1]
        if sub.shape[1] < 20 or sub.shape[2] < 20:
            break
        if not _player_frame(sub[:, 2:-2, 2:-2]):
            break  # 外側が動画プレーヤーの暗い余白ではない（スライドそのものなど）ので、それ以上は探さない
        inner = _content_box_once(sub[:, 2:-2, 2:-2], 0.15, 0.0, line=0.1)  # 外枠の線を除く。途中で大きさの違う動画に切り替わっても収まるよう、一部のフレームでも映った範囲を含める
        if inner is None:
            break
        ia, ib, ic0, ic1 = inner
        if (ib - ia) * (ic1 - ic0) > 0.85 * sub.shape[1] * sub.shape[2] or (ib - ia) * (ic1 - ic0) < 0.04 * sub.shape[1] * sub.shape[2]:
            break  # ほとんど変わらない（入れ子ではない）か、小さすぎる
        box = (a + 2 + ia, a + 2 + ib, c0 + 2 + ic0, c0 + 2 + ic1)
    return box


def extract_frame(path: Path, t: float, out_jpg: Path, max_width: int = 1280, region: Region | None = None,
                  min_width: int = 0) -> Path:
    """t 秒のフレームを保存。region があれば資料の領域だけを切り出し、min_width まで拡大（小さい文字のOCR・AI用）。"""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    vf = []
    if region is not None and region.rotate:
        vf.append(TRANSPOSE[region.rotate])
    if region is not None and not region.is_full:
        vf.append(region.ffmpeg_crop())
        if min_width and region.w < min_width:
            vf.append(f"scale={min_width}:-2:flags=lanczos")
    vf.append(f"scale='min({max_width},iw)':-2")
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
          "-vf", ",".join(vf), "-q:v", "2", str(out_jpg)])
    if not out_jpg.exists():
        raise MediaError(f"フレームを取得できません: t={t:.1f}")
    return out_jpg


# ---------------- OCR ----------------
class OCR:
    def __init__(self, backend: str = "auto", languages: str = "jpn+eng"):
        self.available = backend != "none" and shutil.which("tesseract") is not None
        self.languages = languages
        if self.available:
            try:
                langs = _run(["tesseract", "--list-langs"], timeout=30).stdout.decode().split()
                wanted = [x for x in languages.split("+") if x in langs]
                self.languages = "+".join(wanted) if wanted else "eng"
            except MediaError:
                self.available = False

    @property
    def label(self) -> str:
        return f"tesseract({self.languages})" if self.available else "なし"

    def image_to_text(self, image: Path) -> str:
        if not self.available:
            return ""
        try:
            return _run(["tesseract", str(image), "stdout", "-l", self.languages], timeout=300
                        ).stdout.decode(errors="replace").strip()
        except MediaError:
            return ""


# ---------------- 文字起こし ----------------
class Transcriber:
    name = "none"
    available = False

    def transcribe(self, wav: Path, offset: float) -> list[dict]:
        """戻り値: [{start, end, text}]（動画先頭からの絶対秒）"""
        return []


class MockTranscriber(Transcriber):
    """テスト用: 設定 transcription.mock_script（[{start,end,text}]）のうち区間に重なるものを返す。"""
    name = "mock"
    available = True

    def __init__(self, script: list[dict] | None = None):
        self.script = script or []

    def transcribe(self, wav: Path, offset: float) -> list[dict]:
        import wave
        with wave.open(str(wav)) as w:
            dur = w.getnframes() / float(w.getframerate())
        # 実際の文字起こしと同じく、区間内で始まる発話だけを返す
        return [dict(s) for s in self.script if offset <= s["start"] < offset + dur]


class FasterWhisperTranscriber(Transcriber):
    name = "faster_whisper"
    available = True

    def __init__(self, model: str, language: str, device: str, compute_type: str,
                 initial_prompt: str | None = None, beam_size: int = 5):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise MediaError("faster-whisper がありません: pip install faster-whisper") from e
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.model_name = model
        self.language = language
        #: 専門用語の候補（例: 梁・柱・せん断・加力）。誤認識しやすい語を正しく書き起こすための手がかり
        self.initial_prompt = initial_prompt or None
        self.beam_size = beam_size

    def transcribe(self, wav: Path, offset: float) -> list[dict]:
        import wave
        # 16kHz モノラル WAV を自前で読み込んで渡す（PyAV のバージョン差異の影響を受けないため）
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language=self.language, vad_filter=True, beam_size=self.beam_size,
                                        initial_prompt=self.initial_prompt)
        return [{"start": offset + s.start, "end": offset + s.end, "text": s.text.strip(),
                 "avg_logprob": getattr(s, "avg_logprob", None), "no_speech_prob": getattr(s, "no_speech_prob", None)}
                for s in segs if s.text.strip()]


def make_transcriber(cfg) -> Transcriber:
    t = cfg.data["transcription"]
    if t["backend"] == "faster_whisper":
        return FasterWhisperTranscriber(t["model"], t["language"], t["device"], t["compute_type"],
                                        initial_prompt=t.get("initial_prompt"), beam_size=int(t.get("beam_size", 5)))
    if t["backend"] == "mock":
        return MockTranscriber(t.get("mock_script"))
    return Transcriber()
