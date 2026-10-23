"""東電建築部門OBリクルーターとの関係性 A〜D の主観的ベイズ仮説確率と感度分析。

注意: ここで出す数値は実測ベースレートではない（公表された実測値は確認できなかった）。
事前確率・尤度比はすべて調査者の仮置きであり、結果は入力に強く依存する。
相関のある証拠 E1〜E8 は独立に掛け合わせず、4つのクラスタにまとめて1回ずつ更新し、
さらに相関割引係数 w (0<w<=1) で尤度比を w 乗して過剰更新を抑える。
"""
import itertools

H = ["A", "B", "C", "D"]

# 証拠クラスタ（尤度比は A を 1 とした相対値）
# K1 = E1+E2+E4: 9月末先行案内／資料に「各学校OB/OG個別相談会 随時」明記／元々の大学訪問日に面談
#      → 公式施策の範囲内で説明でき、A〜Cでほぼ同等。Dなら非標準チャネルが出やすいので僅かに減。
# K2 = E3+E5: 応募書類相談の申し出、研究・キャリアへの継続的で個別具体的な回答、ゼミ優先の気遣い
# K3 = E6+E7: 第一志望を明言しつつ応募は保留 → C/Dなら「応募したら知らせて/人事につなぐ」が出やすい
# K4 = E8 (+10/15 「有利になるとは言い切れません」「お約束できません」): 推薦・特別選考の言及なし
CLUSTERS = ["K1", "K2", "K3", "K4"]

SCENARIOS = {
    "baseline": {
        "prior": {"A": 0.50, "B": 0.38, "C": 0.10, "D": 0.02},
        "lr": {"K1": [1, 1, 0.9, 0.7], "K2": [1, 2.0, 2.0, 1.5],
               "K3": [1, 1, 0.7, 0.4], "K4": [1, 1, 0.6, 0.2]},
    },
    "standard_is_thorough": {  # 大学別リクルーターの標準対応がそもそも手厚い
        "prior": {"A": 0.60, "B": 0.30, "C": 0.08, "D": 0.02},
        "lr": {"K1": [1, 1, 0.9, 0.7], "K2": [1, 1.3, 1.3, 1.1],
               "K3": [1, 1, 0.7, 0.4], "K4": [1, 1, 0.6, 0.2]},
    },
    "individual_nurturing_common": {  # 個別候補者としてのフォローが一般的
        "prior": {"A": 0.40, "B": 0.45, "C": 0.12, "D": 0.03},
        "lr": {"K1": [1, 1.1, 1.0, 0.7], "K2": [1, 3.0, 2.5, 1.5],
               "K3": [1, 1, 0.75, 0.4], "K4": [1, 1, 0.7, 0.25]},
    },
    "referral_route_exists": {  # OB推薦・社内紹介ルートが実在する前提
        "prior": {"A": 0.35, "B": 0.35, "C": 0.25, "D": 0.05},
        "lr": {"K1": [1, 1, 1.0, 0.8], "K2": [1, 2.0, 2.5, 2.0],
               "K3": [1, 1, 0.85, 0.5], "K4": [1, 1, 0.8, 0.3]},
    },
}


def posterior(prior, lr, w=0.75):
    p = [prior[h] for h in H]
    for k in CLUSTERS:
        p = [pi * (l ** w) for pi, l in zip(p, lr[k])]
    s = sum(p)
    return [x / s for x in p]


def fmt(p):
    return "  ".join(f"{h}={x*100:5.1f}%" for h, x in zip(H, p))


def main():
    print("== シナリオ別事後確率（主観的ベイズ、w=0.75） ==")
    results = {}
    for name, sc in SCENARIOS.items():
        p = posterior(sc["prior"], sc["lr"])
        results[name] = p
        print(f"{name:30s} {fmt(p)}  最頻={H[p.index(max(p))]}")

    print("\n== 相関割引係数 w の感度（baseline） ==")
    for w in (0.5, 0.75, 1.0):
        print(f"w={w:4.2f}  {fmt(posterior(SCENARIOS['baseline']['prior'], SCENARIOS['baseline']['lr'], w))}")

    # 全シナリオ x w x 事前確率の±50%摂動 でレンジを取る
    lo = {h: 1.0 for h in H}
    hi = {h: 0.0 for h in H}
    modes = set()
    for sc in SCENARIOS.values():
        for w in (0.5, 0.75, 1.0):
            for f in itertools.product((0.5, 1.0, 1.5), repeat=4):
                pr = {h: sc["prior"][h] * fi for h, fi in zip(H, f)}
                s = sum(pr.values())
                pr = {h: v / s for h, v in pr.items()}
                p = posterior(pr, sc["lr"], w)
                modes.add(H[p.index(max(p))])
                for h, x in zip(H, p):
                    lo[h] = min(lo[h], x)
                    hi[h] = max(hi[h], x)
    print("\n== 全摂動での最小〜最大レンジ ==")
    for h in H:
        print(f"{h}: {lo[h]*100:4.0f}% 〜 {hi[h]*100:4.0f}%")
    print("最頻になり得た仮説:", sorted(modes))

    # 逆転条件: baselineで B の K2 尤度比をいくつ以下にすると A が最頻になるか
    sc = SCENARIOS["baseline"]
    for x in (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 2.0):
        lr = {k: list(v) for k, v in sc["lr"].items()}
        lr["K2"][1] = x
        p = posterior(sc["prior"], lr)
        print(f"K2のB尤度比={x:.1f} → 最頻={H[p.index(max(p))]}  ({fmt(p)})")


if __name__ == "__main__":
    main()
