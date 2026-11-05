"""PoC-01: (a) Accuracyが無意味になる件 (b) ブロッキングの再現率の天井。
検証する主張: レポート5章「単純なAccuracyは無意味」、4.2章「ブロッキングが再現率の天井になる」。
データ: Splink公開データ fake_1000 (cluster列=正解)。
"""
import json
from itertools import combinations

import pandas as pd

from common import RESULTS, load_fake_1000, pair_metrics, true_pairs

df = load_fake_1000()
n = len(df)
total_pairs = n * (n - 1) // 2
truth = true_pairs(df)

out = {"records": n, "all_pairs": total_pairs, "true_match_pairs": len(truth)}

# (a) 「全部非一致」と答える分類器のAccuracy
out["all_negative_accuracy"] = 1 - len(truth) / total_pairs
out["all_negative_recall"] = 0.0

# (b) ブロッキング: 各キー方式の Pairs Completeness / Reduction Ratio
d = df.copy()
d["dob_year"] = d["dob"].str[:4]
d["dob_month"] = d["dob"].str[:7]
d["sur_init"] = d["surname"].str[:1]
d["fn_init"] = d["first_name"].str[:1]
d["sur_soundlike"] = d["surname"].str.lower().str[:3]


def block_pairs(keys):
    sub = d.dropna(subset=keys)
    pairs = set()
    for _, g in sub.groupby(keys):
        pairs.update(combinations(sorted(g["unique_id"]), 2))
    return pairs


schemes = {
    "dob完全一致": ["dob"],
    "surname完全一致": ["surname"],
    "first_name完全一致": ["first_name"],
    "email完全一致": ["email"],
    "dob年+姓頭文字": ["dob_year", "sur_init"],
    "dob年月": ["dob_month"],
    "姓先頭3文字": ["sur_soundlike"],
}
rows = []
for name, keys in schemes.items():
    cand = block_pairs(keys)
    m = pair_metrics(cand, truth)
    rows.append({
        "scheme": name,
        "candidates": len(cand),
        "reduction_ratio": 1 - len(cand) / total_pairs,
        "pairs_completeness(recall)": m["recall"],
        "pairs_quality(precision)": m["precision"],
    })

# 複数ブロッキングの和集合(Splinkの複数blocking_ruleと同じ考え方)
union = set()
for keys in (["dob"], ["surname"], ["email"], ["first_name"]):
    union |= block_pairs(keys)
m = pair_metrics(union, truth)
rows.append({
    "scheme": "和集合(dob|surname|email|first_name)",
    "candidates": len(union),
    "reduction_ratio": 1 - len(union) / total_pairs,
    "pairs_completeness(recall)": m["recall"],
    "pairs_quality(precision)": m["precision"],
})

res = pd.DataFrame(rows)
res.to_csv(RESULTS / "poc01_blocking.csv", index=False)
(RESULTS / "poc01_summary.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
print(json.dumps(out, indent=2, ensure_ascii=False))
print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
