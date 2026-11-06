"""PRD可視化の参照プロトタイプ用データを、元データ(PoC/results、PoC/data)とPRD(work/prd.md)から生成する。
出力: work/prd-visuals/data.js (window.PRD_DATA = {...})。手書きの値は埋め込まない。
検証: PoC-02の報告値(不純クラスタ数、クラスタ指標)との照合、タスク表の依存の非循環、要求とタスクの網羅を検査して出力に含める。
"""
import csv
import datetime
import json
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "PoC" / "results"
DATA = ROOT / "PoC" / "data"
PRD = ROOT / "work" / "prd.md"
OUT = ROOT / "work" / "prd-visuals" / "data.js"
sys.stdout.reconfigure(encoding="utf-8")
checks = []


def check(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("OK  " if ok else "FAIL"), name, detail)


# ---------------------------------------------------------------- PoC-02: 閾値・クラスタ
fake = pd.read_csv(DATA / "fake_1000.csv", keep_default_na=False, na_values=[""])
cl_of = dict(zip(fake.unique_id, fake.cluster))
truth = set()
for _, g in fake.groupby("cluster"):
    truth.update(combinations(sorted(g.unique_id), 2))
pred = pd.read_csv(RES / "poc02_predictions.csv")
pred["a"] = pred[["unique_id_l", "unique_id_r"]].min(axis=1)
pred["b"] = pred[["unique_id_l", "unique_id_r"]].max(axis=1)
P = {(int(a), int(b)): float(p) for a, b, p in zip(pred.a, pred.b, pred.match_probability)}
T = [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99]


def components(pairs, nodes):
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comp = defaultdict(list)
    for n in nodes:
        comp[find(n)].append(n)
    return [c for c in comp.values() if len(c) > 1]


def metrics(sel):
    tp = len(sel & truth)
    fp = len(sel) - tp
    fn = len(truth) - tp
    p = tp / len(sel) if sel else 0.0
    r = tp / len(truth)
    return {"n": len(sel), "tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


nodes_all = [int(x) for x in fake.unique_id]
thr_rows = []
for t in T:
    sel = {k for k, v in P.items() if v >= t}
    comps = components(sel, nodes_all)
    cpairs = set()
    for c in comps:
        cpairs.update(combinations(sorted(c), 2))
    impure = sum(1 for c in comps if len({cl_of[n] for n in c}) > 1)
    added = cpairs - sel
    thr_rows.append({"t": t, "pair": metrics(sel), "cluster": metrics(cpairs), "impure": impure, "n_components": len(comps),
                     "added": len(added), "added_precision": (len(added & truth) / len(added)) if added else None})
ref = pd.read_csv(RES / "poc02_splink_vs_rules.csv")
for _, r in ref[ref.method.str.contains("連結成分")].iterrows():
    t = float(re.search(r"t=([\d.]+)", r.method).group(1))
    row = next((x for x in thr_rows if abs(x["t"] - t) < 1e-9), None)
    if row:
        check(f"PoC-02と一致: t={t} 不純クラスタ数", row["impure"] == int(r.impure_clusters), f"build={row['impure']} poc02={int(r.impure_clusters)}")
        check(f"PoC-02と一致: t={t} クラスタ適合率・再現率", abs(row["cluster"]["precision"] - r.precision) < 1e-9 and abs(row["cluster"]["recall"] - r.recall) < 1e-9,
              f"P {row['cluster']['precision']:.4f}/{r.precision:.4f} R {row['cluster']['recall']:.4f}/{r.recall:.4f}")
ceiling = len(set(P) & truth) / len(truth)
check("PoC-02: 候補の再現率の上限が0.7336", abs(ceiling - 0.7336287543082225) < 1e-9, f"{ceiling:.4f}")

# コスト比(PoC-04と同じ式)の再現
cost_ref = pd.read_csv(RES / "poc04_cost_threshold.csv")
for _, r in cost_ref.iterrows():
    best = min(thr_rows, key=lambda x: (r.false_merge_cost_ratio * x["pair"]["fp"] + x["pair"]["fn"], x["t"]))
    check(f"PoC-04と一致: コスト比{int(r.false_merge_cost_ratio)}の最適閾値", abs(best["t"] - r.best_threshold) < 1e-9, f"build={best['t']} poc04={r.best_threshold}")

# クラスタ・グラフ: 閾値0.01で不純なコンポーネントのノードと、その間の候補ペア
comps01 = components({k for k, v in P.items() if v >= 0.01}, nodes_all)
impure01 = [c for c in comps01 if len({cl_of[n] for n in c}) > 1]
check("閾値0.01の不純クラスタ数が28(PRの報告値)", len(impure01) == 28, str(len(impure01)))
gn = sorted({n for c in impure01 for n in c})
gset = set(gn)
info = fake.set_index("unique_id")
graph = {
    "nodes": [{"id": int(n), "tc": int(cl_of[n]), "name": f"{info.loc[n, 'first_name']} {info.loc[n, 'surname']}".replace("nan", "?"),
               "dob": str(info.loc[n, "dob"]), "city": str(info.loc[n, "city"]), "email": str(info.loc[n, "email"])} for n in gn],
    "links": [{"s": a, "t": b, "p": round(p, 5), "true": (a, b) in truth} for (a, b), p in P.items() if a in gset and b in gset],
}
print("graph:", len(graph["nodes"]), "nodes", len(graph["links"]), "links")

# ---------------------------------------------------------------- その他のPoC結果
b1 = pd.read_csv(RES / "poc01_blocking.csv")
s1 = json.load(open(RES / "poc01_summary.json", encoding="utf-8"))
blocking = [{"scheme": r.scheme, "candidates": int(r.candidates), "pc": r["pairs_completeness(recall)"], "pq": r["pairs_quality(precision)"],
             "reduction": r.reduction_ratio} for _, r in b1.iterrows()]
queue = pd.read_csv(RES / "poc11_auto_vs_review.csv").rename(columns={"auto_merge_p>=": "hi", "review_p>=": "lo"}).to_dict("records")

rows = list(csv.reader(open(RES / "poc13_poc03_multiseed.csv", encoding="utf-8")))
metric_names = [x for x in rows[0][1:]]
stat_names = rows[1][1:]
norm = []
for r in rows[3:]:
    d = {"method": r[0]}
    for mn, sn, v in zip(metric_names, stat_names, r[1:]):
        d.setdefault(mn, {})[sn] = float(v)
    norm.append(d)

s14 = pd.read_csv(RES / "poc14_splink_sensitivity.csv").groupby("assumed_recall").mean(numeric_only=True).reset_index()
sens = s14.drop(columns=["seed"]).to_dict("records")
ci = pd.read_csv(RES / "poc04_ci_vs_sample.csv").to_dict("records")

lab = []
for f in sorted(RES.glob("poc06_Abt-Buy_roberta-base_*gpu15ep*.json")):
    if "lr1e-5" in f.name or "_s43_" in f.name:
        continue
    j = json.load(open(f, encoding="utf-8"))
    lab.append({"n": j["train_n"], "f1": j["plm_finetuned"]["test_f1"], "p": j["plm_finetuned"]["test_precision"], "r": j["plm_finetuned"]["test_recall"],
                "tfidf": j["tfidf_char_cosine"]["f1"]})
lab.sort(key=lambda x: x["n"])
label_eff = {"points": lab, "tfidf": lab[0]["tfidf"], "paper_deepmatcher": 0.628, "paper_ditto": 0.8933,
             "paper_note": "論文値(arXiv:2004.00584 Table 5, Abt-Buy)。参照用"}

llm = pd.read_csv(RES / "poc12_summary.csv").to_dict("records")
w = json.load(open(RES / "poc16_wdc_relabel_summary.json", encoding="utf-8"))
adj = json.load(open(RES / "wdc_relabel_adjudication.json", encoding="utf-8"))
wdc = {"n": w["n"], "disagree": w["hosted_disagree_n"], "pos_label_dis": w["hosted_disagree_pos_label"], "neg_label_dis": w["hosted_disagree_neg_label"],
       "W": len(adj["W"]), "B": len(adj["B"]), "J": len(adj["J"]), "agree": w["hosted_vs_orig_agree"], "kappa": w["hosted_vs_orig_kappa"],
       "by_group": w["disagree_rate_by_group"], "high_conf": w["hosted_disagree_high_conf(3)"]}
check("WDC: W+B+J = 不一致数(43)", wdc["W"] + wdc["B"] + wdc["J"] == wdc["disagree"], f'{wdc["W"]}+{wdc["B"]}+{wdc["J"]}={wdc["disagree"]}')

sc = pd.concat([pd.read_csv(RES / "poc05_scaling.csv"), pd.read_csv(RES / "poc09_scaling_1M.csv")]).drop_duplicates("records").sort_values("records")
scaling = sc.to_dict("records")
fb = pd.read_csv(RES / "poc18_filtering_best.csv")
fb = fb[(fb.view == "schema-agnostic") & fb.PQ.notna()]
filtering = [{"dataset": r.dataset, "method": r.method, "params": r.best_params, "candidates": int(r.candidates), "pc": r.PC, "pq": r.PQ,
              "range": r["PQ_range_at_PC>=0.9"]} for _, r in fb.iterrows()]

# ---------------------------------------------------------------- PRDの解析(要求・フェーズ・タスク)
lines = PRD.read_text(encoding="utf-8").splitlines()


def expand(text):
    out = []
    for m in re.finditer(r"((?:N?FR)-(?:[A-Z]+-)?)(\d\d)((?:〜\d\d)?)((?:/\d\d)*)", text):
        pre, a, rng, sl = m.groups()
        nums = list(range(int(a), int(rng[1:]) + 1)) if rng else [int(a)]
        nums += [int(s) for s in sl.split("/")[1:]]
        out += [f"{pre}{n:02d}" for n in nums]
    return out


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


reqs = []
sec = None
for ln in lines:
    hm = re.match(r"^## (\d+)\.", ln)
    if hm:
        sec = int(hm.group(1))
    if sec not in (6, 7):
        continue
    m = re.match(r"^\| ((?:FR)-[A-Z]+-\d\d) \|", ln)
    n = re.match(r"^\| (NFR-\d\d) \|", ln)
    if m:
        c = cells(ln)
        reqs.append({"id": c[0], "group": c[0].split("-")[1], "title": re.sub(r"\*\*", "", c[1])[:90], "prio": c[2], "evidence": c[3]})
    elif n:
        c = cells(ln)
        reqs.append({"id": c[0], "group": "NFR", "title": re.sub(r"\*\*", "", c[1] + ": " + c[2])[:90], "prio": "-", "evidence": c[3]})
phase_of, cur = {}, None
for ln in lines:
    m = re.match(r"^### Phase (\d)", ln)
    if m:
        cur = int(m.group(1))
    if cur and ln.startswith("対象:"):
        for rid in expand(ln):
            phase_of[rid] = cur
for r in reqs:
    r["phase"] = phase_of.get(r["id"])
    r["ks"] = sorted({int(x) for x in re.findall(r"\bK(\d{1,2})\b", r["evidence"])})
    e = r["evidence"]
    r["strength"] = "❓" if "❓" in e else "✅" if ("✅" in e or r["ks"]) else "📄" if "📄" in e else "⚠"
ids = {r["id"] for r in reqs}
check("PRDから要求を解析", len(reqs) >= 70, f"{len(reqs)}件(FR {sum(1 for r in reqs if r['group'] != 'NFR')} / NFR {sum(1 for r in reqs if r['group'] == 'NFR')})")
bad = [x for x in phase_of if x not in ids]
check("フェーズ表の要求IDがすべて実在", not bad, str(bad))

tasks = []
for ln in lines:
    if re.match(r"^\| T\d-\d\d \|", ln):
        c = cells(ln)
        tasks.append({"id": c[0], "title": c[1], "phase": int(c[2]), "owner": c[3], "deps": re.findall(r"T\d-\d\d", c[4]), "done": c[5],
                      "related": c[6], "frs": expand(c[6]), "status": c[7]})
tid = {t["id"] for t in tasks}
check("タスクを解析", len(tasks) >= 35, f"{len(tasks)}件")
check("依存先がすべて実在", all(d in tid for t in tasks for d in t["deps"]), str([d for t in tasks for d in t["deps"] if d not in tid]))
# 循環検査(トポロジカルソート)
indeg = {t["id"]: len(t["deps"]) for t in tasks}
children = defaultdict(list)
for t in tasks:
    for d in t["deps"]:
        children[d].append(t["id"])
queue_ = [k for k, v in indeg.items() if v == 0]
order = []
while queue_:
    x = queue_.pop()
    order.append(x)
    for c in children[x]:
        indeg[c] -= 1
        if indeg[c] == 0:
            queue_.append(c)
check("タスクの依存が非循環(DAG)", len(order) == len(tasks), f"{len(order)}/{len(tasks)}")
# 要求の網羅: フェーズに割り当てた要求が、同じフェーズのタスクの「関連」に含まれるか
cover = defaultdict(set)
for t in tasks:
    for f in t["frs"]:
        cover[f].add(t["phase"])
unc = sorted(r for r, p in phase_of.items() if p not in cover.get(r, set()) and r not in ("NFR-09", "NFR-10"))
check("フェーズに割当済みの要求が、同フェーズのタスクに含まれる", not unc, "未網羅: " + ",".join(unc))
wrong = sorted({(f, t["id"]) for t in tasks for f in t["frs"] if f in phase_of and phase_of[f] != t["phase"]})
check("タスクの関連要求のフェーズが、フェーズ表と一致", not wrong, str(wrong))
unassigned = sorted(r["id"] for r in reqs if r["phase"] is None)
print("フェーズ未割当の要求:", unassigned)
# 先行制約: 依存先のフェーズが後ろでない
late = [(t["id"], d) for t in tasks for d in t["deps"] if next(x for x in tasks if x["id"] == d)["phase"] > t["phase"]]
check("依存先のフェーズが後のフェーズでない", not late, str(late))
# 最長の依存連鎖(クリティカル・チェーン。工数は不明のため件数)
depth = {}


def d_(x):
    if x not in depth:
        t = next(y for y in tasks if y["id"] == x)
        depth[x] = 1 + max([d_(d) for d in t["deps"]], default=0)
    return depth[x]


for t in tasks:
    t["depth"] = d_(t["id"])

# 優先度別の根拠の強さ
strength = Counter((r["prio"], r["strength"]) for r in reqs if r["group"] != "NFR")

data = {
    "meta": {"generated": datetime.datetime.now().isoformat(timespec="seconds"), "prd": "work/prd.md",
             "sources": ["PoC/results/*", "PoC/data/fake_1000.csv", "work/prd.md"]},
    "k1": {"all_pairs": s1["all_pairs"], "true_pairs": s1["true_match_pairs"], "accuracy": s1["all_negative_accuracy"], "records": s1["records"]},
    "ceiling": ceiling, "T": T, "thr": thr_rows, "graph": graph, "blocking": blocking, "queue": queue, "norm": norm, "sens": sens, "ci": ci,
    "label_eff": label_eff, "llm": llm, "wdc": wdc, "scaling": scaling, "filtering": filtering,
    "reqs": reqs, "tasks": tasks, "strength": [{"prio": k[0], "strength": k[1], "n": v} for k, v in strength.items()],
    "checks": checks, "unassigned": unassigned,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("window.PRD_DATA = " + json.dumps(data, ensure_ascii=False, default=float) + ";\n", encoding="utf-8")
print("wrote", OUT, OUT.stat().st_size // 1024, "KB;", sum(c["ok"] for c in checks), "/", len(checks), "checks OK")
sys.exit(0 if all(c["ok"] for c in checks) else 1)

