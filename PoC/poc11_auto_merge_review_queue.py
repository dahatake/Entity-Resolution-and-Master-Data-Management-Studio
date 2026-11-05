"""PoC-11: MDM製品の公式ドキュメントに書かれた「自動マージ / 手動確認キュー」運用設計(Informatica match rule の自動/手動区分、
Reltio の自動マージ/潜在マッチキュー)を、Splinkのスコア+fake_1000の正解でシミュレートする。
これは「商用製品の性能」ではなく、「その運用設計が必然的に生む量とリスク」の検証。製品固有のマッチ精度は検証していない。
モデル: p>=hi は自動マージ、lo<=p<hi は人手確認キュー(確認者は完全に正しいと仮定=最良ケース)、p<lo は破棄。
"""
import ast
import itertools
import sys

import pandas as pd

from common import RESULTS, load_fake_1000, true_pairs

sys.stdout.reconfigure(encoding="utf-8")
df = load_fake_1000()
truth = true_pairs(df)
pred = pd.read_csv(RESULTS / "poc02_predictions.csv")
pred["pair"] = pred["pair"].map(ast.literal_eval)
pred["is_true"] = pred["pair"].map(lambda p: p in truth)
n = len(df)
rows = []
for hi, lo in itertools.product((0.99, 0.9, 0.5), (0.5, 0.1, 0.01, 0.001)):
    if lo > hi:
        continue
    auto = pred[pred["match_probability"] >= hi]
    queue = pred[(pred["match_probability"] >= lo) & (pred["match_probability"] < hi)]
    fp_auto = int((~auto["is_true"]).sum())
    tp_auto = int(auto["is_true"].sum())
    tp_q = int(queue["is_true"].sum())
    found = tp_auto + tp_q
    rows.append({
        "auto_merge_p>=": hi, "review_p>=": lo, "auto_pairs": len(auto), "auto_false_merges": fp_auto,
        "review_queue_pairs": len(queue), "queue_true_share": tp_q / len(queue) if len(queue) else None,
        "queue_per_1000_records": len(queue) / n * 1000,
        "recall_after_perfect_review": found / len(truth),
        "recall_without_review": tp_auto / len(truth),
        "missed_true_pairs": len(truth) - found,
    })
res = pd.DataFrame(rows)
res.to_csv(RESULTS / "poc11_auto_vs_review.csv", index=False)
print(res.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
