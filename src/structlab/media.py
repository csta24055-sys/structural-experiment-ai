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

    @property
    def is_full(self) -> bool:
        return self.w * self.h >= 0.9 * self.full_w * self.full_h

    def ffmpeg_crop(self) -> str:
        # 録画の途中で画面の向き・大きさが変わっても失敗しないよう、切り出す範囲を画面の大きさに収める
        return (f"crop=w='min({self.w}\\,iw)':h='min({self.h}\\,ih)'"
                f":x='min({self.x}\\,iw-ow)':y='min({self.y}\\,ih-oh)'")

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h, "full_w": self.full_w, "full_h": self.full_h}


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


def detect_content_region(path: Path, info: ProbeInfo, start: float = 0.0, length: float | None = None,
                          samples: int = 16, work_w: int = 360) -> Region:
    """画面録画（スマホで再生したスライドなど）で、時計・アドレスバー・黒い余白を除いた資料の領域を求める。
    区間内の複数フレームで「背景色と違う画素」が多い行・列の最大の塊を資料とみなす。
    資料が画面いっぱいの動画（カメラ撮影など）では全体を返す。"""
    W, H = info.width, info.height
    full = Region(0, 0, W, H, W, H)
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
            frames.append((t, grab_gray(path, t, work_w, work_h)))
        except MediaError:
            continue
    if len(frames) < 2:
        return full
    stack = np.stack([f for _, f in frames]).astype(np.int16)
    # 背景色: 左右の端（上下の端は時計・ツールバーなので除く）で最も多い明るさ（黒い余白・白い余白のどちらにも対応）
    m = max(2, int(work_w * 0.04))
    mid = stack[:, int(work_h * 0.1):int(work_h * 0.9), :]
    band = np.concatenate([mid[:, :, :m].reshape(-1), mid[:, :, -m:].reshape(-1)])
    bg = int(np.bincount(band.clip(0, 255).astype(np.int64), minlength=256).argmax())
    content = (np.abs(stack - bg) > 32).mean(axis=0) > 0.3      # 半分弱以上のフレームで背景と違う画素
    rows = content.mean(axis=1) > 0.25
    # 上下の端に接する塊（ステータスバー・ツールバー）は除く
    runs = [(a, b) for a, b in _runs(rows, max_gap=max(1, int(work_h * 0.01))) if a > work_h * 0.03 and b < work_h * 0.97 and b - a >= work_h * 0.05]
    if not runs:
        return full
    a, b = max(runs, key=lambda r: r[1] - r[0])
    cols = content[a:b].mean(axis=0) > 0.25
    cruns = [r for r in _runs(cols, max_gap=max(1, int(work_w * 0.01))) if r[1] - r[0] >= work_w * 0.1]
    if not cruns:
        return full
    c0, c1 = min(r[0] for r in cruns), max(r[1] for r in cruns)
    sx, sy = W / work_w, H / work_h
    pad = 2
    x, y = max(0, int((c0 - pad) * sx)), max(0, int((a - pad) * sy))
    x1, y1 = min(W, int((c1 + pad) * sx)), min(H, int((b + pad) * sy))
    reg = Region(x - x % 2, y - y % 2, (x1 - x) - (x1 - x) % 2, (y1 - y) - (y1 - y) % 2, W, H)
    # 小さすぎる・ほぼ全体なら切り出さない
    if reg.w * reg.h < 0.04 * W * H or reg.is_full:
        return full
    return reg


def extract_frame(path: Path, t: float, out_jpg: Path, max_width: int = 1280, region: Region | None = None,
                  min_width: int = 0) -> Path:
    """t 秒のフレームを保存。region があれば資料の領域だけを切り出し、min_width まで拡大（小さい文字のOCR・AI用）。"""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    vf = []
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
