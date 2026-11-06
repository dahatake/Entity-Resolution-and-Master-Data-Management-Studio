"""PoC-15b: アブレーション結果の集計。変種ごとの平均±SD、ベースラインとのシード対応差(平均、t統計、ブートストラップ95%区間)。"""
import glob
import json
import sys

import numpy as np
import pandas as pd

from common import RESULTS

sys.stdout.reconfigure(encoding="utf-8")
rows = [json.load(open(f, encoding="utf-8")) for f in glob.glob(str(RESULTS / "poc15_*_ml128_s*.json"))]
d = pd.DataFrame(rows)
rng = np.random.RandomState(0)
out = []
for ds, g in d.groupby("dataset"):
    piv = g.pivot_table(index="seed", columns="variant", values="test_f1")
    for v in ["base", "dk", "su", "da", "all"]:
        if v not in piv:
            continue
        x = piv[v].dropna()
        row = {"dataset": ds, "variant": v, "n_seeds": len(x), "mean_test_f1": x.mean(), "sd": x.std(ddof=1) if len(x) > 1 else np.nan,
               "min": x.min(), "max": x.max()}
        if v != "base" and "base" in piv:
            both = piv[[v, "base"]].dropna(); diff = (both[v] - both["base"]).values
            row["paired_diff_vs_base"] = diff.mean()
            if len(diff) > 1:
                se = diff.std(ddof=1) / np.sqrt(len(diff)); row["t"] = diff.mean() / se if se > 0 else np.nan
                bs = [rng.choice(diff, len(diff)).mean() for _ in range(5000)]
                row["boot95"] = f"[{np.percentile(bs, 2.5):+.4f}, {np.percentile(bs, 97.5):+.4f}]"
        out.append(row)
o = pd.DataFrame(out)
o.to_csv(RESULTS / "poc15_ablation_summary.csv", index=False)
pd.set_option("display.width", 220)
print(o.round(4).to_string(index=False))
