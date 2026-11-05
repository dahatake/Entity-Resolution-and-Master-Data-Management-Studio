"""PoC-10: WDC Products(2022/2024公開ベンチマーク)の検証。
(1) ベンチマーク自身の主張の検証: 「訓練・評価でオファーが分離」「テストの未見製品率が 0/50/100%」を cluster_id で独立に確認(データ漏洩の点検)。
(2) 古典的教師あり分類器(特徴量+勾配ブースティング)で、未見製品が増えると性能が落ちるかを測る。
(3) PLM用に Ditto形式(COL x VAL y)へ変換して data/WDC/ に書き出す(poc06が使う)。
"""
import gzip
import json
import sys

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score, precision_score, recall_score

from common import DATA, RESULTS

sys.stdout.reconfigure(encoding="utf-8")
W = DATA / "wdc" / "80pair"


def load(name):
    return [json.loads(l) for l in gzip.open(W / f"wdcproducts80cc20rnd{name}.json.gz", "rt", encoding="utf-8")]


train, valid = load("000un_train_small"), load("000un_valid_small")
tests = {u: load(f"{u}un_gs") for u in ("000", "050", "100")}
out = {}

# (1) 漏洩・未見率の点検
tr_clusters = {r["cluster_id_left"] for r in train} | {r["cluster_id_right"] for r in train}
tr_offers = {r["id_left"] for r in train} | {r["id_right"] for r in train}
leak = {}
for u, rows in tests.items():
    off = {r["id_left"] for r in rows} | {r["id_right"] for r in rows}
    seen_pairs = sum(1 for r in rows if r["cluster_id_left"] in tr_clusters and r["cluster_id_right"] in tr_clusters)
    leak[u] = {"pairs": len(rows), "positives": sum(int(r["label"]) for r in rows),
               "hard_negatives": sum(str(r["is_hard_negative"]) == "True" for r in rows),
               "offers_shared_with_train": len(off & tr_offers),
               "pairs_with_both_clusters_seen_in_train": seen_pairs / len(rows)}
out["leakage_check"] = leak
print(json.dumps(leak, indent=1))


# (2) 古典的分類器
def txt(r, s):
    return " ".join(x for x in (r[f"brand_{s}"], r[f"title_{s}"]) if x and x != "None")


def toks(s):
    return set(s.lower().split())


tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit([txt(r, s) for r in train for s in ("left", "right")])


def feats(rows):
    L = tf.transform([txt(r, "left") for r in rows]); R = tf.transform([txt(r, "right") for r in rows])
    cos = np.asarray(L.multiply(R).sum(axis=1)).ravel()
    F = []
    for i, r in enumerate(rows):
        a, b = toks(txt(r, "left")), toks(txt(r, "right"))
        na = {t for t in a if any(c.isdigit() for c in t)}; nb = {t for t in b if any(c.isdigit() for c in t)}
        jac = len(a & b) / max(1, len(a | b))
        numj = len(na & nb) / max(1, len(na | nb))
        try:
            pa, pb = float(r["price_left"]), float(r["price_right"]); pr = min(pa, pb) / max(pa, pb) if max(pa, pb) > 0 else 0
        except Exception:
            pr = -1
        F.append([cos[i], jac, numj, len(na & nb), len(na ^ nb), pr, int(r["brand_left"] == r["brand_right"] != "None")])
    return np.array(F)


y = lambda rows: np.array([int(r["label"]) for r in rows])
clf = HistGradientBoostingClassifier(random_state=0, max_iter=200).fit(feats(train), y(train))
res = {}
for u, rows in tests.items():
    p = clf.predict(feats(rows))
    res[u] = {"precision": precision_score(y(rows), p), "recall": recall_score(y(rows), p), "f1": f1_score(y(rows), p)}
out["classical_gbm_trained_on_small(2500 pairs)"] = res
print(json.dumps(res, indent=1))

# (3) Ditto形式へ変換
D = DATA / "WDC"; D.mkdir(exist_ok=True)


def ser(r, s):
    return f"COL title VAL {r['title_' + s]} COL brand VAL {'' if r['brand_' + s] == 'None' else r['brand_' + s]}"


def dump(rows, fn):
    with open(D / fn, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(f"{ser(r, 'left')}\t{ser(r, 'right')}\t{r['label']}\n")


dump(train, "train.txt"); dump(valid, "valid.txt")
dump(tests["000"], "test.txt"); dump(tests["050"], "test_un050.txt"); dump(tests["100"], "test_un100.txt")
(RESULTS / "poc10_wdc.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

