"""テスト用の合成の入力（実際の実験データではない）。書式・文字コード（CP932）だけを実物に合わせる。"""
from pathlib import Path


def write_inputs(d: Path, n: int = 200) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    lines = ["RC梁の破壊実験", "名前,測定日時 ,Load kN,Disp-1 mm,Disp-2 mm,Disp-3 mm,Strain-1 μ,Strain-2 μ,Strain-3 μ,Strain-4 μ"]
    for i in range(n):
        P = round(80.0 * i / (n - 1), 3)
        d2 = d3 = round(0.01 * i, 4)
        d1 = round(d2 + 0.02 * i, 4)
        lines.append(f"{i + 1},2000/01/01 00:{i // 20:02d}:{(i * 3) % 60:02d},{P},{d1},{d2},{d3},{10 * i},{i},{2 * i},{3 * i}")
    (d / "合成実験結果.csv").write_bytes("\r\n".join(lines).encode("cp932"))
    (d / "材料試験結果.txt").write_bytes(("試験体の材料強度(実測値)について\n\n鉄筋\n  主筋(D16)     -->  降伏点   σy = 370.0 (N/mm^2)\n"
                                    "                     引張強さ σm = 550.0 (N/mm^2)\n  あばら筋(D10) -->  降伏点   σy = 330.0 (N/mm^2)\n"
                                    "                     引張強さ σm = 470.0 (N/mm^2)\n\nコンクリート強度\n  σB = 22.0(N/mm^2)").encode("cp932"))
    k = 50  # 50 行目の荷重・補正たわみ
    P = round(80.0 * (k - 1) / (n - 1), 3)
    (d / "データ.txt").write_bytes((f"実験の結果\n\n曲げひび割れ時発生荷重\n  荷重 --> {P}(kN)\n  変位 --> {round(0.02 * (k - 1), 3)}(mm)\n\n"
                                  f"最大荷重\n80.0(kN)\n").encode("cp932"))
    (d / "readme.txt").write_bytes("・データ\n        Load(kN)     -->   荷重P\n        Disp-1(mm)   -->   たわみ(No.1)\n"
                                   "・材料強度.txt\n".encode("cp932"))
    return d
