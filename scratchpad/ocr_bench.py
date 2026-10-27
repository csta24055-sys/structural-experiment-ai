import sys, difflib, subprocess, unicodedata, collections
from pathlib import Path
import pymupdf
from PIL import Image
pdf = Path(sys.argv[1]); out = pdf.parent / "bench"; out.mkdir(exist_ok=True)
def norm(s): return unicodedata.normalize("NFKC", s).replace(" ", "").replace("\n", "").replace("−","-").replace("ー","-")
def char_recall(gold, text):
    g, t = collections.Counter(norm(gold)), collections.Counter(norm(text))
    tot = sum(g.values()); return (sum(min(c, t[k]) for k, c in g.items()) / tot) if tot else None, tot
doc = pymupdf.open(str(pdf)); res = collections.defaultdict(list)
for p in doc:
    gold = p.get_text("text")
    if len(norm(gold)) < 20: continue
    f = out / f"p{p.number+1}.png"
    p.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(str(f))
    g = out / f"p{p.number+1}_g.png"; Image.open(f).convert("L").save(g)
    for psm in ("3", "6", "11"):
        txt = subprocess.run(["tesseract", str(g), "stdout", "-l", "jpn+eng", "--psm", psm], capture_output=True, text=True).stdout
        r, n = char_recall(gold, txt); res[psm].append((r, n))
for psm, v in res.items():
    tot = sum(n for _, n in v); w = sum(r * n for r, n in v) / tot
    print(f"psm{psm}: pages={len(v)} chars={tot} weighted char recall={w:.3f} min={min(r for r,_ in v):.2f}")
