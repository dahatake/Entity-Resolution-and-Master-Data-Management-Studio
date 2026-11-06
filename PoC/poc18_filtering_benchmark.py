"""PoC-18: フィルタリング技術の縮小版ベンチマーク(VLDB J. 2024 "Open benchmark for filtering techniques in ER" の要旨の主張を検証)。
検証する主張(要旨): (1) ブロッキング・ワークフローとNN(最近傍)ワークフローの相対性能は設定パラメータに大きく依存する、
(2) スキーマ非依存/スキーマ認識で結果が変わる、(3) 再現率と適合率で最適化した設定を比較する。
本文は入手不能(Springer有料。arXiv版なし)のため、論文の数値の再現ではなく、要旨の主張の方向を確認する。
データ: Ditto分割の全ペアから左右のレコード集合を再構成(元の全表ではない)。正解=陽性ペア。
手法: ブロッキング=トークンブロッキング(ブロック除去+共通トークン数による枝刈り)、NN=kNN結合/ε結合(TF-IDF語・文字3-gram、bge-small埋め込み)。
最適化: PC(再現率)>=0.9 を満たす設定のうち、PQ(適合率=候補中の真ペア割合)が最大のもの。
"""
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer

HERE = Path(__file__).parent
DATA, RESULTS = HERE / "data", HERE / "results"
sys.stdout.reconfigure(encoding="utf-8")
ITEM = re.compile(r"COL (\S+) VAL (.*?)(?= COL \S+ VAL |$)")


def load_pools(ds):
    L, R, pos = {}, {}, set()
    for split in ("train", "valid", "test"):
        for line in open(DATA / ds / f"{split}.txt", encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) != 3:
                continue
            li = L.setdefault(p[0], len(L)); ri = R.setdefault(p[1], len(R))
            if p[2] == "1":
                pos.add((li, ri))
    return list(L), list(R), pos


def views(strings):
    agn = [" ".join(v for _, v in ITEM.findall(s)) for s in strings]
    aware = [(ITEM.findall(s) or [("", "")])[0][1] for s in strings]
    return {"schema-agnostic": agn, "schema-aware": aware}


def evaluate(cand, pos, n_l, n_r):
    c = len(cand); tp = len(cand & pos)
    return {"candidates": c, "PC": tp / len(pos), "PQ": tp / c if c else 0.0, "reduction": 1 - c / (n_l * n_r)}


def topk_pairs(S, k):
    idx = np.argpartition(-S, min(k, S.shape[1] - 1), axis=1)[:, :k]
    return {(i, int(j)) for i in range(S.shape[0]) for j in idx[i]}


def embed(texts, name="BAAI/bge-small-en-v1.5", bs=64):
    import torch
    from transformers import AutoModel, AutoTokenizer
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(name); m = AutoModel.from_pretrained(name).to(dev).eval(); out = []
    with torch.no_grad():
        for i in range(0, len(texts), bs):
            e = tok(texts[i:i + bs], padding=True, truncation=True, max_length=128, return_tensors="pt").to(dev)
            h = m(**e).last_hidden_state; msk = e["attention_mask"].unsqueeze(-1)
            out.append(torch.nn.functional.normalize((h * msk).sum(1) / msk.sum(1), dim=-1).cpu().numpy())
    return np.concatenate(out)


rows = []
for ds in ("Abt-Buy", "DBLP-ACM", "Amazon-Google"):
    Ls, Rs, pos = load_pools(ds)
    nl, nr = len(Ls), len(Rs)
    print(f"{ds}: |L|={nl} |R|={nr} matches={len(pos)} all pairs={nl * nr}", flush=True)
    for view in ("schema-agnostic", "schema-aware"):
        lt, rt = views(Ls)[view], views(Rs)[view]
        # ---- ブロッキング: トークンブロッキング + ブロック除去 + 共通トークン数(CBS)閾値 ----
        t0 = time.time()
        cv = CountVectorizer(binary=True, token_pattern=r"(?u)\b\w+\b", lowercase=True).fit(lt + rt)
        Bl, Br = cv.transform(lt), cv.transform(rt)
        df = np.asarray((Bl.sum(0) + Br.sum(0))).ravel()
        t_build = time.time() - t0
        for purge in (30, 100, 300, 10**9):
            keep = sp.diags((df <= purge).astype(np.float32))
            t1 = time.time()
            shared = ((Bl @ keep) @ Br.T).tocoo()
            for w in (1, 2, 3):
                msk = shared.data >= w
                cand = set(zip(shared.row[msk].tolist(), shared.col[msk].tolist()))
                rows.append({"dataset": ds, "view": view, "family": "blocking", "method": "token-blocking+purge+CBS",
                             "params": f"purge<={purge if purge < 10**9 else 'inf'},CBS>={w}", "join_sec": time.time() - t1 + t_build,
                             **evaluate(cand, pos, nl, nr)})
        # ---- NN: TF-IDF(語/文字3-gram) kNN結合・ε結合 ----
        for name, vec in (("tfidf-word", TfidfVectorizer(token_pattern=r"(?u)\b\w+\b")),
                          ("tfidf-char3", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 3)))):
            t0 = time.time(); vec.fit(lt + rt)
            S = (vec.transform(lt) @ vec.transform(rt).T).toarray(); tb = time.time() - t0
            for k in (1, 2, 3, 5, 10, 20, 50):
                t1 = time.time(); cand = topk_pairs(S, k)
                rows.append({"dataset": ds, "view": view, "family": "nn", "method": f"kNN-join {name}", "params": f"k={k}",
                             "join_sec": tb + time.time() - t1, **evaluate(cand, pos, nl, nr)})
            for th in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
                ii, jj = np.nonzero(S >= th); cand = set(zip(ii.tolist(), jj.tolist()))
                rows.append({"dataset": ds, "view": view, "family": "nn", "method": f"eps-join {name}", "params": f"eps={th}",
                             "join_sec": tb, **evaluate(cand, pos, nl, nr)})
        # ---- NN: 埋め込み kNN結合 ----
        t0 = time.time(); El, Er = embed(lt), embed(rt); S = El @ Er.T; tb = time.time() - t0
        for k in (1, 2, 3, 5, 10, 20, 50):
            cand = topk_pairs(S, k)
            rows.append({"dataset": ds, "view": view, "family": "nn", "method": "kNN-join bge-small", "params": f"k={k}",
                         "join_sec": tb, **evaluate(cand, pos, nl, nr)})
        print(ds, view, "done", flush=True)

df_all = pd.DataFrame(rows)
df_all.to_csv(RESULTS / "poc18_filtering_all_configs.csv", index=False)
best = []
for (ds, view, method), g in df_all.groupby(["dataset", "view", "method"]):
    ok = g[g.PC >= 0.9]
    if ok.empty:
        best.append({"dataset": ds, "view": view, "method": method, "best_params": "(PC>=0.9 not reachable)", "PC_max": g.PC.max()})
        continue
    r = ok.loc[ok.PQ.idxmax()]
    best.append({"dataset": ds, "view": view, "method": method, "best_params": r.params, "candidates": int(r.candidates),
                 "PC": r.PC, "PQ": r.PQ, "join_sec": r.join_sec, "PQ_range_at_PC>=0.9": f"{ok.PQ.min():.4f}-{ok.PQ.max():.4f}",
                 "n_configs_ok": len(ok), "n_configs": len(g)})
b = pd.DataFrame(best)
b.to_csv(RESULTS / "poc18_filtering_best.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 40)
print(b.round(4).to_string(index=False))
