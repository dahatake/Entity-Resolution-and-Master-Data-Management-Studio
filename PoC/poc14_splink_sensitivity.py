"""PoC-14: Splink教師なし学習の感度。EMのu推定シード(5種)と、事前確率推定に使う「再現率の仮定」(0.5/0.7/0.9)を振り、結果が安定するかを見る。
PoC-02は仮定0.7・シード1のみだった(レポート4.1章の「教師なしで訓練可能」の頑健性の点検)。
"""
import logging
import sys

import pandas as pd
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl

from common import RESULTS, clusters_to_pairs, load_fake_1000, norm_pair, pair_metrics, true_pairs

logging.getLogger("splink").setLevel(logging.ERROR)
sys.stdout.reconfigure(encoding="utf-8")
df = load_fake_1000()
truth = true_pairs(df)
rows = []
for recall in (0.5, 0.7, 0.9):
    for seed in (1, 2, 3, 4, 5):
        settings = SettingsCreator(
            link_type="dedupe_only",
            comparisons=[cl.NameComparison("first_name"), cl.NameComparison("surname"),
                         cl.DateOfBirthComparison("dob", input_is_string=True),
                         cl.ExactMatch("city").configure(term_frequency_adjustments=True), cl.EmailComparison("email")],
            blocking_rules_to_generate_predictions=[block_on("first_name", "dob"), block_on("surname"), block_on("dob"), block_on("email")])
        linker = Linker(DuckDBAPI().register(df), settings)
        linker.training.estimate_probability_two_random_records_match([block_on("first_name", "surname", "dob"), block_on("email")], recall=recall)
        linker.training.estimate_u_using_random_sampling(max_pairs=500_000, seed=seed)
        linker.training.estimate_parameters_using_expectation_maximisation(block_on("first_name", "surname"))
        linker.training.estimate_parameters_using_expectation_maximisation(block_on("dob"))
        sp = linker.inference.predict(threshold_match_probability=0.001)
        pred = sp.as_pandas_dataframe()
        sel = {norm_pair(a, b) for a, b, p in zip(pred["unique_id_l"], pred["unique_id_r"], pred["match_probability"]) if p >= 0.5}
        cd = linker.clustering.cluster_pairwise_predictions_at_threshold(sp, threshold_match_probability=0.5).as_pandas_dataframe()
        cm = clusters_to_pairs(dict(zip(cd["unique_id"], cd["cluster_id"])))
        rows.append({"assumed_recall": recall, "seed": seed, "lambda": float(linker._settings_obj._probability_two_random_records_match),
                     **{"pair_" + k: v for k, v in pair_metrics(sel, truth).items() if k in ("precision", "recall", "f1")},
                     **{"cluster_" + k: v for k, v in pair_metrics(cm, truth).items() if k in ("precision", "recall", "f1")}})
        print(rows[-1], flush=True)
res = pd.DataFrame(rows)
res.to_csv(RESULTS / "poc14_splink_sensitivity.csv", index=False)
print(res.groupby("assumed_recall")[["lambda", "pair_precision", "pair_recall", "pair_f1", "cluster_precision", "cluster_recall", "cluster_f1"]].agg(["mean", "std"]).round(4).T.to_string())
