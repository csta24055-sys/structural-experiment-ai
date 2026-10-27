import sys, json, time, wave
import numpy as np
from faster_whisper import WhisperModel
wav, model, out = sys.argv[1], sys.argv[2], sys.argv[3]
prompt = sys.argv[4] if len(sys.argv) > 4 else None
with wave.open(wav) as w:
    audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
t0 = time.time()
m = WhisperModel(model, device="cpu", compute_type="int8")
segs, info = m.transcribe(audio, language="ja", vad_filter=True, beam_size=5, initial_prompt=prompt)
res = [{"start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob, "no_speech_prob": s.no_speech_prob} for s in segs]
json.dump({"model": model, "prompt": prompt, "secs": time.time() - t0, "segments": res}, open(out, "w"), ensure_ascii=False, indent=0)
print(model, "done", round(time.time() - t0), len(res))
