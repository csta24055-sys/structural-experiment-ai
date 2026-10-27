"""試験対策システムのテスト用の合成資料（実在の授業資料ではない）。

2 科目（環境工学Ⅳ・鉄筋コンクリート構造Ⅱ）のスライド PDF、字幕つきの短い動画、過去問、課題、
試験範囲の連絡、Box 共有リンクの .url、科目を判定できない資料、重複投入した資料を作る。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ENV_SLIDES = [
    ("第3回 音の反射と残響", ["環境工学Ⅳ 第3回", "担当: 試験 太郎"]),
    ("音の反射", ["音が壁に当たると一部が反射し、一部が吸収・透過する。",
                  "反射面が平らなとき、入射角と反射角は等しい。", "重要: 反射音は直接音より遅れて届く。"]),
    ("鏡像音源法", ["反射面に対して音源と対称な位置に鏡像音源を置き、反射音の経路を求める方法を鏡像音源法という。",
                    "反射音の行路差は、鏡像音源から受音点までの距離と直接音の距離の差である。"]),
    ("残響時間", ["音を止めてから音圧レベルが60dB減衰するまでの時間を残響時間という。",
                  "T = 0.16V/A", "T：残響時間 [s]、V：室容積 [m3]、A：等価吸音面積 [m2]",
                  "室容積が大きいほど残響時間は長くなる。"]),
    ("音の回折", ["音は障害物の裏側にも回り込む。これを回折という。", "波長が長い低音ほど回折しやすい。"]),
    ("吸音率", ["吸音率は入射した音のエネルギーのうち反射しなかった割合である。", "α = 1 - R",
                "α：吸音率、R：反射率"]),
]
RC_SLIDES = [
    ("第2回 曲げを受ける部材", ["鉄筋コンクリート構造Ⅱ 第2回"]),
    ("曲げ応力", ["部材に生じる曲げ応力は曲げモーメントを断面係数で割って求める。", "σ = M/Z",
                  "σ：曲げ応力 [N/mm2]、M：曲げモーメント [N・mm]、Z：断面係数 [mm3]"]),
    ("断面係数", ["長方形断面の断面係数は幅と高さの2乗に比例する。", "Z = bh²/6", "b：幅 [mm]、h：高さ [mm]"]),
    ("中立軸", ["曲げを受ける部材で、応力が0となる軸を中立軸という。", "中立軸より上は圧縮、下は引張となる。"]),
]
ENV_SPEECH = [
    (5, 12, "今日は音の反射と残響について説明します。"),
    (30, 40, "反射音は直接音より遅れて届きます。"),
    (60, 70, "鏡像音源法は試験に出します。行路差の求め方を理解してください。"),
    (90, 100, "残響時間の式は重要です。計算できるようにしてください。"),
    (130, 140, "音の回折は試験には出しません。"),
    (160, 170, "吸音率は資料を読んでおいてください。"),
]


def make_pdf(path: Path, slides: list[tuple[str, list[str]]]) -> Path:
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.pdfgen import canvas
    from structlab.exam.render import jp_font
    f = jp_font()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    w, h = landscape(A4)
    for title, lines in slides:
        c.setFont(f, 28)
        c.drawString(50, h - 70, title)
        c.setFont(f, 16)
        for i, ln in enumerate(lines):
            c.drawString(60, h - 130 - i * 34, ln)
        c.showPage()
    c.save()
    return path


def make_text_pdf(path: Path, title: str, paragraphs: list[str]) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from structlab.exam.render import jp_font
    f = jp_font()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont(f, 16)
    c.drawString(50, h - 60, title)
    c.setFont(f, 11)
    y = h - 100
    for p in paragraphs:
        for ln in p.split("\n"):
            c.drawString(50, y, ln)
            y -= 20
        y -= 8
    c.save()
    return path


def make_video(pdf: Path, out: Path, seconds_per_page: int = 30) -> Path | None:
    """スライド PDF の各ページを順に映し、音（正弦波）を付けた短い動画。ffmpeg が無ければ作らない。"""
    if not shutil.which("ffmpeg"):
        return None
    import pymupdf
    tmp = out.parent / (out.stem + "_frames")
    tmp.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(pdf)) as doc:
        n = doc.page_count
        for i, page in enumerate(doc):
            page.get_pixmap(dpi=40).save(str(tmp / f"f{i:02d}.png"))
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file 'f{i:02d}.png'\nduration {seconds_per_page}\n" for i in range(n))
                   + f"file 'f{n - 1:02d}.png'\n", encoding="utf-8")
    total = n * seconds_per_page
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={total}", "-vf",
                    "scale=320:-2,format=yuv420p", "-r", "2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(out)], check=True)
    shutil.rmtree(tmp)
    return out


def vtt(lines: list[tuple[int, int, str]]) -> str:
    ts = lambda s: f"00:{s // 60:02d}:{s % 60:02d}.000"  # noqa: E731
    return "WEBVTT\n\n" + "\n\n".join(f"{ts(a)} --> {ts(b)}\n{t}" for a, b, t in lines) + "\n"


def build(root: Path, video: bool = True) -> Path:
    env = root / "環境工学Ⅳ"
    rc = root / "鉄筋コンクリート構造Ⅱ"
    slides = make_pdf(env / "第03回_講義資料.pdf", ENV_SLIDES)
    make_pdf(rc / "第02回_講義資料.pdf", RC_SLIDES)
    if video:
        make_video(slides, env / "第03回_授業動画.mp4", seconds_per_page=30)
        (env / "第03回_授業動画.vtt").write_text(vtt(ENV_SPEECH), encoding="utf-8")
    make_text_pdf(env / "過去問" / "2024年度_期末試験.pdf", "環境工学Ⅳ 2024年度 期末試験", [
        "問1 室容積 500 m3、等価吸音面積 80 m2 の室の残響時間 T = 0.16V/A を用いて求めよ。",
        "問2 鏡像音源法を用いて、反射音の行路差の求め方を説明せよ。",
    ])
    make_text_pdf(env / "過去問" / "2023年度_期末試験.pdf", "環境工学Ⅳ 2023年度 期末試験", [
        "問1 残響時間の定義を述べ、室容積との関係を説明せよ。",
        "問2 気象と温熱環境について述べよ。",
    ])
    make_text_pdf(env / "課題_第3回.pdf", "環境工学Ⅳ 第3回 課題", [
        "問1 吸音率 α = 1 - R の関係を用いて、反射率 0.3 のときの吸音率を求めよ。",
    ])
    (env / "試験範囲のお知らせ.txt").write_text("環境工学Ⅳ 試験範囲\n試験範囲は第1回〜第5回です。\n", encoding="utf-8")
    (env / "授業動画").mkdir(parents=True, exist_ok=True)
    (env / "授業動画" / "第04回.url").write_bytes(
        "[InternetShortcut]\r\nURL=https://nihon-u.app.box.com/s/synthetictest0001?utm_source=x\r\n".encode("cp932"))
    (rc / "第02回_講義資料_コピー.pdf").write_bytes((rc / "第02回_講義資料.pdf").read_bytes())  # 重複投入
    make_text_pdf(root / "メモ.pdf", "メモ", ["買い物 牛乳 パン"])
    return root
