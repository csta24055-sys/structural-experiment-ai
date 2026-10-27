import subprocess, sys, json
from pathlib import Path
from PIL import Image
p = Path(sys.argv[1]); out = Path(sys.argv[2]); step = float(sys.argv[3])
dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(p)],capture_output=True,text=True).stdout)
dims = []; thumbs = []
t = 1.0
while t < dur:
    r = subprocess.run(["ffprobe","-v","error","-select_streams","v","-read_intervals",f"{t}%+#1","-show_entries","frame=width,height","-of","csv=p=0",str(p)],capture_output=True,text=True).stdout.split()
    dims.append((round(t), r[0] if r else "?"))
    jpg = out / f"{p.stem}_{int(t):05d}.jpg"
    subprocess.run(["ffmpeg","-nostdin","-v","error","-y","-ss",str(t),"-i",str(p),"-frames:v","1","-vf","scale=180:-2",str(jpg)])
    thumbs.append((t, jpg)); t += step
print("dims", [(t,d) for t,d in dims if True][:200])
ims = [Image.open(j) for _, j in thumbs if j.exists()]
cols = 10; w, h = ims[0].size; rows = (len(ims)+cols-1)//cols
sheet = Image.new("RGB", (cols*w, rows*(h+14)), "white")
from PIL import ImageDraw
d = ImageDraw.Draw(sheet)
for i, (im, (t, _)) in enumerate(zip(ims, thumbs)):
    x, y = (i%cols)*w, (i//cols)*(h+14); sheet.paste(im, (x, y+14)); d.text((x+2, y), f"{int(t)//60}:{int(t)%60:02d}", fill="black")
sheet.save(out / f"{p.stem}_sheet.jpg")
sil = subprocess.run(["ffmpeg","-nostdin","-v","info","-i",str(p),"-vn","-af","silencedetect=noise=-50dB:d=3","-f","null","-"],capture_output=True,text=True).stderr
print("silence", [l.split("]")[-1].strip() for l in sil.splitlines() if "silence_" in l][:40])
