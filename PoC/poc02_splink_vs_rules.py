"""PoC-02: Fellegi-Sunter(Splink, 教師なしEM) vs 決定的ルール。 クラスタリングの推移閉包の影響も測る。
検証する主張: Splink公式の「教師なし学習で訓練可能」、レポート4.3/4.4章。
データ: fake_1000 (cluster列=正解)。乱数シード固定。
"""
import json
import random
import sys

import pandas as pd
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl

from common import RESULTS, clusters_to_pairs, load_fake_1000, norm_pair, pair_metrics, true_pairs

df = load_fake_1000()
truth = true_pairs(df)

settings = SettingsCreator(
    link_type="dedupe_only",
    comparisons=[
        cl.NameComparison("first_name"),
        cl.NameComparison("surname"),
        cl.DateOfBirthComparison("dob", input_is_string=True),
        cl.ExactMatch("city").configure(term_frequency_adjustments=True),
        cl.EmailComparison("email"),
    ],
    blocking_rules_to_generate_predictions=[
        block_on("first_name", "dob"),
        block_on("surname"),
        block_on("dob"),
        block_on("email"),
    ],
    retain_intermediate_calculation_columns=False,
)

db_api = DuckDBAPI()
linker = Linker(db_api.register(df), settings)
random.seed(1)
linker.training.estimate_probability_two_random_records_match(
    [block_on("first_name", "surname", "dob"), block_on("email")], recall=0.7
)
linker.training.estimate_u_using_random_sampling(max_pairs=500_000, seed=1)
linker.training.estimate_parameters_using_expectation_maximisation(block_on("first_name", "surname"))
linker.training.estimate_parameters_using_expectation_maximisation(block_on("dob"))

pred = linker.inference.predict(threshold_match_probability=0.001).as_pandas_dataframe()
pred["pair"] = [norm_pair(a, b) for a, b in zip(pred["unique_id_l"], pred["unique_id_r"])]
pred.to_csv(RESULTS / "poc02_predictions.csv", index=False)

rows = []
for t in (0.5, 0.8, 0.9, 0.95, 0.99):
    sel = set(pred.loc[pred["match_probability"] >= t, "pair"])
    m = pair_metrics(sel, truth)
    rows.append({"method": f"Splink pairwise p>={t}", **m})

# 決定的ルールのベースライン
d = df
det = set()
for keys in (["email"], ["first_name", "surname", "dob"]):
    sub = d.dropna(subset=keys)
    for _, g in sub.groupby(keys):
        ids = sorted(g["unique_id"])
        det.update((a, b) for i, a in enumerate(ids) for b in ids[i + 1:])
rows.append({"method": "決定的: email一致 or (姓,名,dob)一致", **pair_metrics(det, truth)})

# クラスタリング(連結成分)後のペア指標
for t in (0.01, 0.05, 0.2, 0.5, 0.9, 0.99):
    cl_df = linker.clustering.cluster_pairwise_predictions_at_threshold(
        linker.inference.predict(threshold_match_probability=0.001), threshold_match_probability=t
    ).as_pandas_dataframe()
    mapping = dict(zip(cl_df["unique_id"], cl_df["cluster_id"]))
    cp = clusters_to_pairs(mapping)
    sel = set(pred.loc[pred["match_probability"] >= t, "pair"])
    m = pair_metrics(cp, truth)
    # 連結成分で「追加された」ペア(直接はt未満または未比較)の質
    added = cp - sel
    rows.append({"method": f"Splink 連結成分クラスタ t={t}", **m,
                 "added_by_transitivity": len(added),
                 "added_precision": len(added & truth) / len(added) if added else None})
    rows[-1]["n_clusters"] = cl_df["cluster_id"].nunique()
    # 不純クラスタ(複数の正解clusterを含む)
    imp = cl_df.groupby("cluster_id")["cluster"].nunique()
    rows[-1]["impure_clusters"] = int((imp > 1).sum())

res = pd.DataFrame(rows)
res.to_csv(RESULTS / "poc02_splink_vs_rules.csv", index=False)
with open(RESULTS / "poc02_summary.json", "w", encoding="utf-8") as f:
    json.dump({"truth_pairs": len(truth), "candidate_pairs_scored": len(pred),
               "candidate_recall_ceiling": len(set(pred["pair"]) & truth) / len(truth)}, f, indent=2)
sys.stdout.reconfigure(encoding="utf-8")
print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
print("scored candidates:", len(pred), " recall ceiling:", len(set(pred['pair']) & truth) / len(truth))





