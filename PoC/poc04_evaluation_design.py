"""PoC-04: 評価設計の検証(レポート5章)。
(a) ラベル付きサンプル数と精度推定の信頼区間幅(Wilson)。 (b) 誤統合コスト比で最適閾値がどう動くか。
入力: poc02_splink_vs_rules.py が出力した予測(results/poc02_predictions.csv)と、正解=fake_1000のcluster列。
"""
import ast
import math
import random

import pandas as pd

from common import RESULTS, load_fake_1000, true_pairs

df = load_fake_1000()
truth = true_pairs(df)
pred = pd.read_csv(RESULTS / "poc02_predictions.csv")
pred["pair"] = pred["pair"].map(ast.literal_eval)
pred["is_true"] = pred["pair"].map(lambda p: p in truth)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


# (a) 0.001<=p<0.5 の「グレーゾーン」ペア集合の精度を、n件のラベルで推定する
gray = pred[(pred["match_probability"] >= 0.001) & (pred["match_probability"] < 0.5)]
true_prec = gray["is_true"].mean()
rng = random.Random(7)
rows = []
for n in (10, 20, 50, 100, 200):
    cover, widths = 0, []
    for _ in range(2000):
        s = gray.sample(n=min(n, len(gray)), random_state=rng.randrange(10**9))
        lo, hi = wilson(int(s["is_true"].sum()), len(s))
        cover += lo <= true_prec <= hi
        widths.append(hi - lo)
    rows.append({"labeled_n": n, "true_precision_of_gray_zone": true_prec, "gray_zone_pairs": len(gray),
                 "mean_CI_width": sum(widths) / len(widths), "coverage_95CI": cover / 2000})
pd.DataFrame(rows).to_csv(RESULTS / "poc04_ci_vs_sample.csv", index=False)
print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.3f}"))

# (b) コスト比: 誤統合1件のコストをc、見逃し1件のコストを1とした総コストを最小化する閾値(ペア単位)
rows = []
total_true_scored = int(pred["is_true"].sum())
missed_not_scored = len(truth) - total_true_scored
for c in (1, 5, 20, 100):
    best = None
    for t in (0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99):
        sel = pred[pred["match_probability"] >= t]
        fp = int((~sel["is_true"]).sum())
        fn = len(truth) - int(sel["is_true"].sum())
        cost = c * fp + fn
        if best is None or cost < best["cost"]:
            best = {"false_merge_cost_ratio": c, "best_threshold": t, "fp": fp, "fn": fn, "cost": cost}
    rows.append(best)
pd.DataFrame(rows).to_csv(RESULTS / "poc04_cost_threshold.csv", index=False)
print(pd.DataFrame(rows).to_string(index=False))
print("候補に入らなかった真ペア(ブロッキング由来の見逃し):", missed_not_scored)
