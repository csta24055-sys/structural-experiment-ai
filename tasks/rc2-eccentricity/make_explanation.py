#!/usr/bin/env python3
"""Build the detailed explanation PDF (original content, safe to commit).

    python3 make_explanation.py   ->  solution/explanation.pdf (+ PNG previews)

Numbers are taken from solver.calculate() so the document cannot drift
from the verified calculation.  Rendered with headless Chromium (CHROME env var or /opt/pw-browsers/chromium).
"""
import os
import shutil
import subprocess
from math import sqrt
from pathlib import Path

import independent_check as ic
from solver import calculate

HERE = Path(__file__).resolve().parent
OUT = HERE / "solution"


def plan_svg(a):
    s, ox, oy = 30, 70, 310  # px per m, origin in SVG coords
    X = lambda x: ox + x * s
    Y = lambda y: oy - y * s
    cols = [(0, 0, 100), (6, 0, 200), (12, 0, 100), (0, 8, 100), (6, 8, 200), (12, 8, 100)]
    p = [f'<svg viewBox="0 0 600 360" width="100%" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="IPAGothic, sans-serif" font-size="12">']
    # frame
    p.append(f'<rect x="{X(0)}" y="{Y(8)}" width="{12*s}" height="{8*s}" fill="none" stroke="#999"/>')
    p.append(f'<line x1="{X(6)}" y1="{Y(0)}" x2="{X(6)}" y2="{Y(8)}" stroke="#bbb" stroke-dasharray="4 3"/>')
    # wall
    p.append(f'<rect x="{X(12)-5}" y="{Y(8)}" width="10" height="{8*s}" fill="#333"/>')
    p.append(f'<text x="{X(12)+12}" y="{Y(4.3)}">耐震壁（柱＋壁）</text>')
    p.append(f'<text x="{X(12)+12}" y="{Y(4.3)+16}" font-weight="bold">KY = 8K（1回）</text>')
    p.append(f'<text x="{X(12)+12}" y="{Y(4.3)+32}" fill="#555">X方向：柱のみ各K</text>')
    for x, y, n in cols:
        p.append(f'<rect x="{X(x)-7}" y="{Y(y)-7}" width="14" height="14" fill="#fff" stroke="#000" stroke-width="2"/>')
        dy = 22 if y == 0 else -14
        p.append(f'<text x="{X(x)}" y="{Y(y)+dy}" text-anchor="middle">N={n}kN</text>')
    # G and R
    gx, gy, rx, ry = (float(a[k]) for k in ("gx", "gy", "rx", "ry"))
    p.append(f'<circle cx="{X(gx)}" cy="{Y(gy)}" r="6" fill="#1565c0"/>')
    p.append(f'<text x="{X(gx)-8}" y="{Y(gy)-10}" text-anchor="end" fill="#1565c0">G({gx:g}, {gy:g})</text>')
    p.append(f'<circle cx="{X(rx)}" cy="{Y(ry)}" r="6" fill="#c62828"/>')
    p.append(f'<text x="{X(rx)}" y="{Y(ry)+22}" text-anchor="middle" fill="#c62828">R({rx:g}, {ry:g})</text>')
    p.append(f'<line x1="{X(gx)}" y1="{Y(gy)}" x2="{X(rx)}" y2="{Y(ry)}" stroke="#c62828" stroke-width="2" marker-end="url(#a)"/>')
    p.append(f'<text x="{X((gx+rx)/2)}" y="{Y(gy)-8}" text-anchor="middle" fill="#c62828">eX = {float(a["ex"]):g} m</text>')
    p.append('<defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
             '<path d="M0,0 L8,4 L0,8 z" fill="#c62828"/></marker></defs>')
    # dims & axes
    for x0, x1 in ((0, 6), (6, 12)):
        p.append(f'<text x="{X((x0+x1)/2)}" y="{Y(0)+42}" text-anchor="middle">6,000</text>')
    p.append(f'<text x="{X(0)-12}" y="{Y(4)}" text-anchor="end">8,000</text>')
    p.append(f'<text x="{X(0)-12}" y="{Y(0)+4}" text-anchor="end">(0,0)</text>')
    p.append(f'<text x="{X(12.6)}" y="{Y(-1)+6}">X →</text><text x="{X(-1.9)}" y="{Y(8.0)+4}">↑ Y</text>')
    p.append('</svg>')
    return "".join(p)


def html(a):
    rex, rey = a["rex"], a["rey"]
    rows = "".join(
        f"<tr{' class=ok' if i == 0 else ''}><td>{n}</td><td>{v['sum_kx']}K</td><td>{v['sum_ky']}K</td>"
        f"<td>{float(v['rx']):.3f}</td><td>{float(v['kr']):.1f}</td><td>{v['Rey']:.3f}</td></tr>"
        for i, (n, v) in enumerate(ic.sensitivity_cases()))
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 16mm 15mm; }}
body {{ font-family: 'IPAGothic', sans-serif; font-size: 10.5pt; line-height: 1.6; color:#111; }}
h1 {{ font-size: 16pt; border-bottom: 2px solid #333; padding-bottom: 4px; margin: 0 0 6px; }}
h2 {{ font-size: 12.5pt; background:#eef2f7; padding: 3px 8px; margin: 14px 0 6px; break-after: avoid; }}
.eq {{ font-family: 'IPAGothic', monospace; background:#fafafa; border-left:3px solid #1565c0;
       padding: 4px 10px; margin: 4px 0; white-space: pre-wrap; }}
.res {{ font-weight: bold; color:#0d47a1; }}
.ng {{ color:#b71c1c; font-weight:bold; }}
table {{ border-collapse: collapse; width:100%; margin:6px 0; font-size:9.5pt; }}
td, th {{ border:1px solid #999; padding: 2px 6px; text-align:center; }}
tr.ok td {{ background:#e8f5e9; font-weight:bold; }}
.note {{ font-size: 9pt; color:#444; border:1px solid #ccc; padding:4px 8px; }}
section {{ break-inside: avoid; }}
</style></head><body>
<h1>鉄筋コンクリート構造Ⅱ　偏心率の算定　解説</h1>
<p class="note">図は課題図の転記値（PROBLEM.md）から作成した模式図。講義ノート pp.20–22 の手順（重心→剛心→偏心距離→ねじり剛性→弾力半径→偏心率）に従う。
Kは基準剛性として記号のまま扱う。</p>
{plan_svg(a)}
<section><h2>0. モデル化の要点</h2>
<ul>
<li>右端の<b>耐震壁（柱＋壁）KY＝8K</b>は、端柱2本と壁をまとめた<b>1つの等価ばね</b>。Y方向では x＝12 m に8Kを<b>1回だけ</b>置き、端柱のKを足さない。</li>
<li>「X方向入力に対しては柱のみ抵抗」→ X方向は<b>柱6本が各K</b>（ΣKX＝6K）。壁はX方向剛性を持たない。</li>
<li><b>RX には KY</b>、<b>RY には KX</b> を使う。ReX＝eY/reX、ReY＝eX/reY（加力方向と直交する偏心がねじりを生む）。</li>
</ul></section>
<section><h2>1. 重心</h2>
<div class="eq">ΣN = 100+200+100+100+200+100 = 800 kN
GX = Σ(N·X)/ΣN = (100×0 + 200×6 + 100×12 + 100×0 + 200×6 + 100×12)/800 = 4800/800 = <span class="res">6.0 m</span>
GY = Σ(N·Y)/ΣN = {{(100+200+100)×0 + (100+200+100)×8}}/800 = 3200/800 = <span class="res">4.0 m</span></div></section>
<section><h2>2. 剛心</h2>
<div class="eq">RX = Σ(KY·X)/ΣKY = (2K×0 + 2K×6 + 8K×12)/(2K + 2K + 8K) = 108K/12K = <span class="res">9.0 m</span>
RY = Σ(KX·Y)/ΣKX = (3K×0 + 3K×8)/(3K + 3K) = 24K/6K = <span class="res">4.0 m</span></div>
<p>剛心は剛性の大きい耐震壁側へ寄り、R＝(9.0, 4.0) m。</p></section>
<section><h2>3. 偏心距離</h2>
<div class="eq">eX = |RX − GX| = |9.0 − 6.0| = <span class="res">3.0 m</span>
eY = |RY − GY| = |4.0 − 4.0| = <span class="res">0 m</span></div></section>
<section><h2>4. ねじり剛性</h2>
<div class="eq">Σ{{KX(Y−RY)²}} = 3K×(0−4)² + 3K×(8−4)² = 48K + 48K = 96K m²
Σ{{KY(X−RX)²}} = 2K×(0−9)² + 2K×(6−9)² + 8K×(12−9)² = 162K + 18K + 72K = 252K m²
KR = 96K + 252K = <span class="res">{a['kr']}K m²</span></div></section>
<section><h2>5. 弾力半径</h2>
<div class="eq">reX = √(KR/ΣKX) = √(348K/6K) = √58 = <span class="res">{rex:.2f} m</span>
reY = √(KR/ΣKY) = √(348K/12K) = √29 = <span class="res">{rey:.2f} m</span></div></section>
<section><h2>6. 偏心率</h2>
<div class="eq">ReX = eY/reX = 0/{rex:.2f} = <span class="res">0</span>  ≦ 0.15　OK
ReY = eX/reY = 3.0/{rey:.2f} = <span class="ng">{a['eccentricity_y']:.3f}</span>  ＞ 0.15　NG</div>
<p><b>Y方向の偏心率 ReY＝{a['eccentricity_y']:.3f} が 0.15 を超える。</b>右端だけに剛な耐震壁があるため剛心が重心から3 m右へずれ、
Y方向の地震力に対して床が大きくねじれる。講義ノートp.13のとおり、0.15超では形状係数Feによる必要保有水平耐力の割増しを検討する
（左端にも耐震壁を設けるなど、剛性配置の均衡で改善できる）。</p></section>
<section><h2>7. 独立検算（剛床の剛性マトリクス法）</h2>
<div class="eq">原点まわり：Kuu=ΣKX=6K、Kvv=ΣKY=12K、Kuθ=−ΣKX·y=−24K m、Kvθ=ΣKY·x=108K m
Kθθ = ΣKX·y² + ΣKY·x² = 3K×8² + (2K×6² + 8K×12²) = 192K + 1224K = 1416K m²
RX = Kvθ/Kvv = 108/12 = 9 m、RY = −Kuθ/Kuu = 24/6 = 4 m
KR = 1416K − (24K)²/6K − (108K)²/12K = 1416K − 96K − 972K = 348K m²  （一致）
G に PY=1 を与えて K·d=f を解く → |θ|·KR = 3.000 m = eX  （一致）</div></section>
<section><h2>8. 解釈・取り違えの感度</h2>
<table><tr><th>ケース</th><th>ΣKX</th><th>ΣKY</th><th>RX [m]</th><th>KR [K m²]</th><th>ReY</th></tr>{rows}</table>
<p class="note">二重計上（8K＋端柱2K）では ReY が約2割過大。X/Y取り違えでは偏心ゼロと誤判定する。壁のy座標はKRに影響しない。</p></section>
</body></html>"""


def main():
    a = calculate()
    assert a["kr"] == 348 and abs(a["eccentricity_y"] - 3 / sqrt(29)) < 1e-12
    OUT.mkdir(exist_ok=True)
    src = OUT / "explanation.html"
    src.write_text(html(a), encoding="utf-8")
    pdf = OUT / "explanation.pdf"
    chrome = os.environ.get("CHROME") or next(
        (c for c in ("/opt/pw-browsers/chromium", shutil.which("chromium"),
                     shutil.which("google-chrome"), shutil.which("chromium-browser")) if c and Path(c).exists()),
        None)
    if not chrome:
        raise SystemExit("Chromium not found; set CHROME=/path/to/chrome")
    subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu",
                    "--no-pdf-header-footer", f"--print-to-pdf={pdf}", src.as_uri()],
                   check=True, capture_output=True)
    (HERE / "output").mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "110", str(pdf), str(HERE / "output" / "explanation")], check=True)
    print("wrote", pdf)


if __name__ == "__main__":
    main()
