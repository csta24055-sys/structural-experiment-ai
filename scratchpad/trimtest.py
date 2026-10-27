import sys, json, numpy as np; sys.path.insert(0, "/home/user/structural-experiment-ai/src")
from pathlib import Path
from structlab import media
from structlab.reading.video_read import audio_profile, detect_return_to_start
mkv, orig = Path(sys.argv[1]), Path(sys.argv[2])
info = media.probe(orig)
npz = mkv.with_suffix(".npz")
if npz.exists():
    z = np.load(npz); times, frames = list(z["t"]), list(z["f"])
else:
    sm = media.sample_gray_frames(mkv, 0, info.duration, 1.0, w=240, h=110)
    times, frames = [t for t, _ in sm], [f for _, f in sm]
    np.savez(npz, t=np.array(times), f=np.stack(frames))
au = audio_profile(orig, info, info.duration)
print(orig.name, "dur", info.duration, "track", au["track_seconds"], "sil", au["silences"][-3:])
print(json.dumps(detect_return_to_start(times, frames, au, info.duration), ensure_ascii=False))
