"""PoC-16(準備): WDC Productsのテスト分割から、論文と同じ方法でラベル再評価用の600組を層化抽出する。
論文: 9つのテスト分割それぞれから陽性・陰性を同数、コーナーケース率80/50/20%で 100/60/40 組 → 計600組(陽性300/陰性300)。
判定者にはラベルを見せない(blind)。正解側情報は別ファイルに保存。
"""
import gzip
import json
import random
from pathlib import Path

W = Path(__file__).parent / "data" / "wdc"
R_DIR = Path(__file__).parent / "results"
rng = random.Random(2026)
rows = []
for cc, per in (("80", 100), ("50", 60), ("20", 40)):
    for un in ("000", "050", "100"):
        fn = W / f"{cc}pair" / f"wdcproducts{cc}cc{100 - int(cc)}rnd{un}un_gs.json.gz"
        R = [json.loads(l) for l in gzip.open(fn, "rt", encoding="utf-8")]
        pos = [r for r in R if int(r["label"]) == 1]
        neg = [r for r in R if int(r["label"]) == 0]
        for r in rng.sample(pos, per // 2) + rng.sample(neg, per // 2):
            r["_cc"], r["_un"] = cc, un
            rows.append(r)
rng.shuffle(rows)


def side(r, s):
    f = lambda k: "" if r[f"{k}_{s}"] in (None, "None", "") else str(r[f"{k}_{s}"])
    return f"brand: {f('brand')} | title: {f('title')} | description: {f('description')[:250]} | price: {f('price')} {f('priceCurrency')}"


blind = [{"id": i, "a": side(r, "left"), "b": side(r, "right")} for i, r in enumerate(rows)]
truth = [{"id": i, "label": int(r["label"]), "cc": r["_cc"], "unseen": r["_un"], "hard_negative": str(r["is_hard_negative"]),
          "id_left": r["id_left"], "id_right": r["id_right"], "cluster_left": r["cluster_id_left"], "cluster_right": r["cluster_id_right"]}
         for i, r in enumerate(rows)]
json.dump(blind, open(R_DIR / "wdc_relabel_pairs_blind.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump(truth, open(R_DIR / "wdc_relabel_truth.json", "w", encoding="utf-8"), ensure_ascii=False)
for b in range(12):
    json.dump(blind[b * 50:(b + 1) * 50], open(R_DIR / f"wdc_batch_{b:02d}.json", "w", encoding="utf-8"), ensure_ascii=False)
print(len(rows), "pairs; positives:", sum(t["label"] for t in truth))


