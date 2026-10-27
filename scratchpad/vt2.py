import sys, json, time; sys.path.insert(0, "/home/user/structural-experiment-ai/src")
from pathlib import Path
from structlab import media
from structlab.reading.video_read import read_video
M = Path(sys.argv[1]); name = sys.argv[2]; t = time.time()
rec = read_video(M / name, M / ("vr_" + name), ocr=media.OCR("auto", "jpn+eng"), transcriber=None,
                 role={"kind": "experiment", "window": 15, "trim_end": True})
json.dump(rec, open(M / f"vr_{name}.json", "w"), ensure_ascii=False, indent=1, default=str)
print("secs", round(time.time() - t), "segments", rec["segments"], "valid_end", rec["valid_end"])
print("trim", json.dumps(rec.get("trim"), ensure_ascii=False))
print("audio", rec["audio"]["track_seconds"], rec["audio"]["silences"][-3:])
print("scenes", len(rec["scenes"]), rec["scenes"][-1]["start"], rec["scenes"][-1]["end"])
