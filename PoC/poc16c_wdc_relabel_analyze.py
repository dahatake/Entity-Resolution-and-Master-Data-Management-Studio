"""PoC-16c: WDCラベル再評価の集計。判定者=ホスト型LLM(エージェント)、ローカルqwen、(あれば)人手でなく私による不一致事例の目視。
指標: 原ラベルとの一致率、推定ノイズ率(判定者が原ラベルと不一致の割合)、陽性/陰性別、判定者間κ。
"""
import glob
import json
import sys
from collections import Counter

from common import RESULTS

sys.stdout.reconfigure(encoding="utf-8")
T = {t["id"]: t for t in json.load(open(RESULTS / "wdc_relabel_truth.json", encoding="utf-8"))}
B = {b["id"]: b for b in json.load(open(RESULTS / "wdc_relabel_pairs_blind.json", encoding="utf-8"))}
H = {}
for f in sorted(glob.glob(str(RESULTS / "wdc_hosted_out_*.json"))):
    for x in json.load(open(f)):
        H[x["id"]] = x
lf = RESULTS / "wdc_local_out_google-gemma-4-e4b.json"
Q = {x["id"]: x for x in json.load(open(lf))} if lf.exists() else {}
ids = sorted(T)
assert len(H) == 600, len(H)


def kappa(a, b):
    n = len(a); po = sum(x == y for x, y in zip(a, b)) / n
    p1a, p1b = sum(a) / n, sum(b) / n
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


orig = [T[i]["label"] for i in ids]
hm = [H[i]["match"] for i in ids]
out = {"n": 600, "orig_pos": sum(orig)}
out["hosted_vs_orig_agree"] = sum(x == y for x, y in zip(orig, hm)) / 600
out["hosted_vs_orig_kappa"] = kappa(orig, hm)
dis = [i for i in ids if H[i]["match"] != T[i]["label"]]
out["hosted_disagree_n"] = len(dis)
out["hosted_disagree_pos_label"] = sum(1 for i in dis if T[i]["label"] == 1)
out["hosted_disagree_neg_label"] = sum(1 for i in dis if T[i]["label"] == 0)
out["hosted_disagree_high_conf(3)"] = sum(1 for i in dis if H[i]["conf"] == 3)
by = Counter(); tot = Counter()
for i in ids:
    for key in (("cc", T[i]["cc"]), ("unseen", T[i]["unseen"]), ("label", T[i]["label"])):
        tot[key] += 1; by[key] += (H[i]["match"] != T[i]["label"])
out["disagree_rate_by_group"] = {f"{k[0]}={k[1]}": round(by[k] / tot[k], 3) for k in sorted(tot, key=str)}
if len(Q) == 600 and all(Q[i]["match"] is not None for i in ids):
    qm = [Q[i]["match"] for i in ids]
    out["local_gemma_vs_orig_agree"] = sum(x == y for x, y in zip(orig, qm)) / 600
    out["local_gemma_vs_orig_kappa"] = kappa(orig, qm)
    out["hosted_vs_local_kappa"] = kappa(hm, qm)
    out["both_judges_disagree_with_orig"] = [i for i in ids if H[i]["match"] == Q[i]["match"] != T[i]["label"]]
else:
    out["local_done"] = len(Q)
(RESULTS / "poc16_wdc_relabel_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
with open(RESULTS / "wdc_relabel_disagreements_hosted.txt", "w", encoding="utf-8") as f:
    for i in dis:
        f.write(f"### id={i} orig_label={T[i]['label']} hosted={H[i]['match']} conf={H[i]['conf']} cc={T[i]['cc']} unseen={T[i]['unseen']} hardneg={T[i]['hard_negative']}\nA: {B[i]['a']}\nB: {B[i]['b']}\n\n")
print(json.dumps({k: v for k, v in out.items() if k != "both_judges_disagree_with_orig"}, ensure_ascii=False, indent=1))

