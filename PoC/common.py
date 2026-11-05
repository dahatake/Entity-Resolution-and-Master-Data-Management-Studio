"""PoC共通: データ読込とペア/クラスタ指標。"""
from itertools import combinations
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
RESULTS = Path(__file__).parent / "results"
RESULTS.mkdir(exist_ok=True)


def load_fake_1000() -> pd.DataFrame:
    return pd.read_csv(DATA / "fake_1000.csv", keep_default_na=False, na_values=[""])


def true_pairs(df: pd.DataFrame, cluster_col="cluster", id_col="unique_id") -> set:
    pairs = set()
    for _, g in df.groupby(cluster_col):
        ids = sorted(g[id_col])
        pairs.update(combinations(ids, 2))
    return pairs


def pair_metrics(pred: set, truth: set) -> dict:
    tp = len(pred & truth)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(truth) if truth else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"pred": len(pred), "tp": tp, "precision": p, "recall": r, "f1": f1}


def clusters_to_pairs(mapping: dict) -> set:
    groups = {}
    for k, c in mapping.items():
        groups.setdefault(c, []).append(k)
    pairs = set()
    for ids in groups.values():
        pairs.update(combinations(sorted(ids), 2))
    return pairs


def norm_pair(a, b):
    return (a, b) if a < b else (b, a)
