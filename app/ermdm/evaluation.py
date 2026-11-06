"""評価ワークベンチ(FR-EVL-01〜04/08)。

2つのモードを持つ:
  gold  : 正解列(truth)がある場合。全候補ペアに正解があるので、ペア/クラスタ指標、PC、コストを正確に計算する。
  sample: 正解がない場合。スコア帯で層化した人手ラベルから、重み付きで適合率などを推定する(Wilson 区間、層化分散)。
単独の Accuracy は返さない(全ペアの大半が非一致のため。K1)。
"""
import math
import random
from collections import Counter, defaultdict
from itertools import combinations

from . import audit
from .clustering import UF
from .util import ApiError, need, now

THRESHOLDS = [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 0.95, 0.99]
BINS = [0.0, 0.01, 0.05, 0.2, 0.5, 0.9, 1.0000001]


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def gold_truth(conn):
    return {r["rid"]: r["truth"] for r in conn.execute("SELECT rid, truth FROM records WHERE status='active' AND truth IS NOT NULL AND truth!=''")}


def gold_available(conn):
    return bool(conn.execute("SELECT 1 FROM records WHERE status='active' AND truth IS NOT NULL AND truth!='' LIMIT 1").fetchone())


def run_pairs(conn, run_id):
    return [(r["a"], r["b"], r["p"]) for r in conn.execute("SELECT a,b,p FROM pairs WHERE run_id=?", (run_id,))]


def gold_curve(conn, run_id, thresholds=None):
    truth = gold_truth(conn)
    need(truth, 409, "正解列を持つレコードがありません(gold モードは使えません)")
    pairs = run_pairs(conn, run_id)
    all_rids = [r["rid"] for r in conn.execute("SELECT rid FROM records WHERE status='active'")]
    groups = Counter(truth.values())
    true_total = sum(c * (c - 1) // 2 for c in groups.values())
    tp_cand = sum(1 for a, b, _ in pairs if truth.get(a) and truth.get(a) == truth.get(b))
    rows = []
    for t in thresholds or THRESHOLDS:
        sel = [(a, b) for a, b, p in pairs if p >= t]
        tp = sum(1 for a, b in sel if truth.get(a) and truth.get(a) == truth.get(b))
        pair = _prf(tp, len(sel) - tp, true_total - tp)
        uf = UF(all_rids)
        for a, b in sel:
            uf.union(a, b)
        comp = defaultdict(Counter)
        for rid in all_rids:
            comp[uf.find(rid)][truth.get(rid) or f"_u{rid}"] += 1
        pred = sum(sum(c.values()) * (sum(c.values()) - 1) // 2 for c in comp.values())
        tpc = sum(x * (x - 1) // 2 for c in comp.values() for x in c.values())
        impure = sum(1 for c in comp.values() if len([k for k in c if not str(k).startswith("_u")]) > 1)
        added = pred - len(sel)
        rows.append({"t": t, "pair": pair, "cluster": _prf(tpc, pred - tpc, true_total - tpc), "impure": impure, "added": added,
                     "added_precision": ((tpc - tp) / added) if added > 0 else None})
    return {"mode": "gold", "true_pairs": true_total, "pairs_completeness": tp_cand / true_total if true_total else None, "true_in_candidates": tp_cand,
            "n_candidates": len(pairs), "rows": rows,
            "definition": "PC=真の一致ペアのうち候補に入った割合(全体の再現率の上限)。ペア単位とクラスタ単位は定義が異なり、クラスタ化後の再現率はPCを超えうる。"}


def cost_optimum(curve_rows, c, level="pair"):
    """コスト = c × 誤統合 + 見逃し。同コストなら低い閾値(PoC-04と同じ規則)。"""
    out = []
    for r in curve_rows:
        m = r[level]
        if m["fp"] is None or m["fn"] is None:
            continue
        out.append({"t": r["t"], "fp": m["fp"], "fn": m["fn"], "cost": c * m["fp"] + m["fn"]})
    best = min(out, key=lambda x: (x["cost"], x["t"])) if out else None
    return {"c": c, "level": level, "curve": out, "best": best}


# ------------------------------------------------------------------ ラベルワークフロー(FR-EVL-01)
def _bin_of(p):
    for i in range(len(BINS) - 1):
        if BINS[i] <= p < BINS[i + 1]:
            return i
    return len(BINS) - 2


def bin_name(i):
    hi = BINS[i + 1]
    return f"p∈[{BINS[i]:g},{min(hi, 1.0):g}{']' if hi > 1 else ')'}"


def create_sample(conn, run_id, actor, per_stratum=30, seed=1, noncandidate=0):
    need(conn.execute("SELECT 1 FROM runs WHERE id=? AND status='done'", (run_id,)).fetchone(), 400, "完了した実行を指定してください")
    rng = random.Random(seed)
    cur = conn.execute("INSERT INTO label_samples_meta(run_id,seed,spec,created,created_by) VALUES(?,?,?,?,?)", (run_id, seed, f"per_stratum={per_stratum};noncandidate={noncandidate}", now(), actor))
    sid = cur.lastrowid
    by = defaultdict(list)
    for r in conn.execute("SELECT a,b,p FROM pairs WHERE run_id=? ORDER BY a,b", (run_id,)):
        by[_bin_of(r["p"])].append((r["a"], r["b"], r["p"]))
    out = {}
    for i in sorted(by):
        pick = rng.sample(by[i], min(per_stratum, len(by[i])))
        for a, b, p in pick:
            conn.execute("INSERT INTO label_samples(sample_id,a,b,stratum,p) VALUES(?,?,?,?,?)", (sid, a, b, str(i), p))
        out[bin_name(i)] = {"population": len(by[i]), "sampled": len(pick)}
    if noncandidate:
        rids = [r["rid"] for r in conn.execute("SELECT rid FROM records WHERE status='active' ORDER BY rid")]
        have = {(r["a"], r["b"]) for r in conn.execute("SELECT a,b FROM pairs WHERE run_id=?", (run_id,))}
        got, tries = 0, 0
        while got < noncandidate and tries < noncandidate * 50 and len(rids) >= 2:
            tries += 1
            a, b = sorted(rng.sample(rids, 2))
            if (a, b) in have or conn.execute("SELECT 1 FROM label_samples WHERE sample_id=? AND a=? AND b=?", (sid, a, b)).fetchone():
                continue
            conn.execute("INSERT INTO label_samples(sample_id,a,b,stratum,p) VALUES(?,?,?,?,NULL)", (sid, a, b, "nc"))
            got += 1
        out["候補外(ランダム)"] = {"population": None, "sampled": got}
    audit.append(conn, actor, "label.sample_create", str(sid), {"run": run_id, "seed": seed, "per_stratum": per_stratum, "strata": out})
    conn.commit()
    return sid, out


def next_items(conn, sample_id, labeler, limit=10):
    rows = conn.execute("SELECT s.a,s.b,s.stratum FROM label_samples s WHERE s.sample_id=? AND NOT EXISTS "
                        "(SELECT 1 FROM labels l WHERE l.sample_id=s.sample_id AND l.a=s.a AND l.b=s.b AND l.labeler=?) ORDER BY s.a,s.b LIMIT ?", (sample_id, labeler, limit)).fetchall()
    total = conn.execute("SELECT COUNT(*) c FROM label_samples WHERE sample_id=?", (sample_id,)).fetchone()["c"]
    done = conn.execute("SELECT COUNT(*) c FROM labels WHERE sample_id=? AND labeler=?", (sample_id, labeler)).fetchone()["c"]
    return {"items": [dict(r) for r in rows], "total": total, "done": done}


def submit_label(conn, sample_id, a, b, labeler, label):
    need(label in (0, 1, -1), 400, "label は 1(一致) / 0(非一致) / -1(判断不能)")
    a, b = min(a, b), max(a, b)
    need(conn.execute("SELECT 1 FROM label_samples WHERE sample_id=? AND a=? AND b=?", (sample_id, a, b)).fetchone(), 404, "標本にないペアです")
    conn.execute("INSERT INTO labels(sample_id,a,b,labeler,label,ts) VALUES(?,?,?,?,?,?) ON CONFLICT(sample_id,a,b,labeler) DO UPDATE SET label=excluded.label, ts=excluded.ts",
                 (sample_id, a, b, labeler, label, now()))


def adjudicate(conn, sample_id, a, b, label, by):
    need(label in (0, 1), 400, "裁定は 1 または 0")
    a, b = min(a, b), max(a, b)
    conn.execute("INSERT INTO adjudications(sample_id,a,b,label,by,ts) VALUES(?,?,?,?,?,?) ON CONFLICT(sample_id,a,b) DO UPDATE SET label=excluded.label, by=excluded.by, ts=excluded.ts",
                 (sample_id, a, b, label, by, now()))
    audit.append(conn, by, "label.adjudicate", f"{sample_id}:{a}-{b}", {"label": label})


def consensus(conn, sample_id):
    """裁定があれば裁定、無ければ全員一致の確定ラベル。不一致・判断不能は除外し、件数を返す。"""
    L = defaultdict(dict)
    for r in conn.execute("SELECT a,b,labeler,label FROM labels WHERE sample_id=?", (sample_id,)):
        L[(r["a"], r["b"])][r["labeler"]] = r["label"]
    adj = {(r["a"], r["b"]): r["label"] for r in conn.execute("SELECT a,b,label FROM adjudications WHERE sample_id=?", (sample_id,))}
    final, disputed, unsure = {}, [], 0
    for k, d in L.items():
        if k in adj:
            final[k] = adj[k]
            continue
        vals = set(d.values())
        if -1 in vals:
            unsure += 1
        elif len(vals) == 1:
            final[k] = vals.pop()
        else:
            disputed.append(k)
    return final, {"disputed": [list(k) for k in sorted(disputed)], "unsure": unsure, "labeled_pairs": len(L), "adjudicated": len(adj)}


def kappa_matrix(conn, sample_id):
    L = defaultdict(dict)
    for r in conn.execute("SELECT a,b,labeler,label FROM labels WHERE sample_id=? AND label IN (0,1)", (sample_id,)):
        L[r["labeler"]][(r["a"], r["b"])] = r["label"]
    out = []
    for u, v in combinations(sorted(L), 2):
        common = sorted(set(L[u]) & set(L[v]))
        n = len(common)
        if n == 0:
            continue
        po = sum(1 for k in common if L[u][k] == L[v][k]) / n
        pu, pv = sum(L[u][k] for k in common) / n, sum(L[v][k] for k in common) / n
        pe = pu * pv + (1 - pu) * (1 - pv)
        out.append({"a": u, "b": v, "n": n, "agreement": po, "kappa": (po - pe) / (1 - pe) if pe < 1 else 1.0})
    return out


def sample_estimates(conn, run_id, sample_ids=None):
    """層化ラベルからの推定(sample モード)。層=スコア帯。人口は実行の pairs から数える。"""
    if sample_ids is None:
        sample_ids = [r["sample_id"] for r in conn.execute("SELECT sample_id FROM label_samples_meta WHERE run_id=?", (run_id,))]
    need(sample_ids, 409, "この実行のラベル標本がありません。標本を作成してラベルを付けてください")
    pop = Counter()
    for r in conn.execute("SELECT p FROM pairs WHERE run_id=?", (run_id,)):
        pop[_bin_of(r["p"])] += 1
    pmap = {(r["a"], r["b"]): r["p"] for r in conn.execute("SELECT a,b,p FROM pairs WHERE run_id=?", (run_id,))}
    n_rec = conn.execute("SELECT COUNT(*) c FROM records WHERE status='active'").fetchone()["c"]
    nc_pop = n_rec * (n_rec - 1) // 2 - sum(pop.values())
    pos, tot, nc_pos, nc_tot = Counter(), Counter(), 0, 0
    info = {"disputed": 0, "unsure": 0, "labeled_pairs": 0}
    for sid in sample_ids:
        final, i = consensus(conn, sid)
        for k in ("disputed",):
            info[k] += len(i[k])
        info["unsure"] += i["unsure"]
        info["labeled_pairs"] += i["labeled_pairs"]
        for (a, b), lab in final.items():
            p = pmap.get((a, b))
            if p is None:
                nc_tot += 1
                nc_pos += lab
            else:
                i_ = _bin_of(p)
                tot[i_] += 1
                pos[i_] += lab
    strata = []
    for i in range(len(BINS) - 1):
        n, k = tot[i], pos[i]
        lo, hi = wilson(k, n)
        strata.append({"stratum": bin_name(i), "lo": BINS[i], "population": pop[i], "labeled": n, "positives": k, "rate": (k / n) if n else None, "ci": [lo, hi]})
    rows = []
    for ti in range(1, len(BINS) - 1):
        t = BINS[ti]
        tp = fp = fn = 0.0
        var = 0.0
        sel_pop = 0
        incomplete = False
        for s in strata:
            if s["population"] == 0:
                continue
            if s["labeled"] == 0:
                incomplete = True
                continue
            est = s["population"] * s["rate"]
            if s["lo"] >= t:
                tp += est
                fp += s["population"] - est
                sel_pop += s["population"]
                r_ = s["rate"]
                fpc = 1 - s["labeled"] / s["population"]
                var += s["population"] ** 2 * r_ * (1 - r_) / s["labeled"] * fpc
            else:
                fn += est
        nc_est = nc_pop * (nc_pos / nc_tot) if nc_tot else None
        sd = math.sqrt(var) / sel_pop if sel_pop else None
        prec = tp / sel_pop if sel_pop else None
        rows.append({"t": t, "pair": {"tp": tp, "fp": fp, "fn": fn + (nc_est or 0), "precision": prec, "recall": tp / (tp + fn + (nc_est or 0)) if tp + fn + (nc_est or 0) else None,
                                       "precision_ci": [max(0.0, prec - 1.96 * sd), min(1.0, prec + 1.96 * sd)] if prec is not None and sd is not None else None},
                     "n_selected": sel_pop, "incomplete": incomplete})
    return {"mode": "sample", "strata": strata, "rows": rows, "noncandidate": {"population": nc_pop, "labeled": nc_tot, "positives": nc_pos},
            "label_info": info, "kappa": [k for sid in sample_ids for k in kappa_matrix(conn, sid)],
            "notes": ["再現率は『候補に入ったペア』の中での推定。候補外ラベルが無い/少ないと、ブロッキングの見逃しは反映されない(下限ではなく上限寄り)。",
                      "適合率の区間は、層化抽出の分散(有限母集団補正つき)による正規近似。ラベルの無い層は推定から除外した。"]}
