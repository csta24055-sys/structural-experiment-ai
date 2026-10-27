import sys, json, time; sys.path.insert(0, "/home/user/structural-experiment-ai/src")
from pathlib import Path
from structlab import media
from structlab.reading.video_read import read_video
M = Path(sys.argv[1]); t = time.time()
rec = read_video(M / "r1.mkv", M / "vr_r1", ocr=media.OCR("auto", "jpn+eng"), transcriber=None, meta_path=M / "r1.mp4")
print("secs", round(time.time() - t), "mode", rec.get("mode"), "segments", [(s["start"], s["end"], s["w"], s["h"], s["region"]) for s in rec["segments"]])
print("audio", rec["audio"]["track_seconds"], len(rec["audio"]["silences"]), "scenes", len(rec["scenes"]), "unique", rec.get("n_unique_scenes"))
for sc in rec["scenes"][:60]:
    print(sc["index"], sc["start"], sc["end"], sc.get("mode"), sc.get("same_as"), len(sc.get("events_with_frames", [])), sc.get("notification"), (sc.get("ocr") or "")[:60].replace("\n", " "))
