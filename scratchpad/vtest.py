import sys, json, time; sys.path.insert(0, "/home/user/structural-experiment-ai/src")
from pathlib import Path
from structlab import media
from structlab.reading.video_read import read_video
M = Path(sys.argv[1]); t = time.time()
rec = read_video(M / "setsumei1.mp4", M / "vread", ocr=media.OCR("auto", "jpn+eng"), transcriber=None)
json.dump(rec, open(M / "vread.json", "w"), ensure_ascii=False, indent=1)
print("secs", round(time.time() - t), "mode", rec.get("mode"), "scenes", len(rec["scenes"]), "unique", rec.get("n_unique_scenes"))
for s in rec["scenes"]:
    print(s["index"], s["start"], s["end"], "same_as", s["same_as"], "events", len(s["events"]), "|", (s["ocr"] or "")[:50].replace("\n", " / "))
