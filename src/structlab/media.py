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
                       w: int = 320, h: int = 180) -> list[tuple[float, np.ndarray]]:
    """区間内を interval 秒ごとに低解像度グレースケールでサンプリング（シーン検出用）。"""
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path),
         "-vf", f"fps=1/{interval},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
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


def extract_frame(path: Path, t: float, out_jpg: Path, max_width: int = 1280) -> Path:
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
          "-vf", f"scale='min({max_width},iw)':-2", "-q:v", "3", str(out_jpg)])
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

    def __init__(self, model: str, language: str, device: str, compute_type: str):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise MediaError("faster-whisper がありません: pip install faster-whisper") from e
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.language = language

    def transcribe(self, wav: Path, offset: float) -> list[dict]:
        import wave
        # 16kHz モノラル WAV を自前で読み込んで渡す（PyAV のバージョン差異の影響を受けないため）
        with wave.open(str(wav)) as w:
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segs, _ = self.model.transcribe(audio, language=self.language, vad_filter=True)
        return [{"start": offset + s.start, "end": offset + s.end, "text": s.text.strip(),
                 "avg_logprob": getattr(s, "avg_logprob", None), "no_speech_prob": getattr(s, "no_speech_prob", None)}
                for s in segs if s.text.strip()]


def make_transcriber(cfg) -> Transcriber:
    t = cfg.data["transcription"]
    if t["backend"] == "faster_whisper":
        return FasterWhisperTranscriber(t["model"], t["language"], t["device"], t["compute_type"])
    if t["backend"] == "mock":
        return MockTranscriber(t.get("mock_script"))
    return Transcriber()
