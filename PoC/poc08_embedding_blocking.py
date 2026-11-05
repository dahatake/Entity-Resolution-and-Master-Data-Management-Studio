"""PoC-08: 埋め込みによるブロッキング(候補生成)。arXiv:2304.12329 の「事前学習埋め込みは(ブロッキングで)有効か」を小規模に検証。
データ: Ditto分割のtest(左右レコードの集合を候補プールとする。真の対応=陽性ペア)。プールはtest分割内のレコードのみで、元の全表ではない。
比較: 文字n-gram TF-IDF(古典) と nomic-embed-text-v1.5(ローカルLM Studio経由の公開重み)。指標: recall@k (k=1,5,10)。
"""
import json
import re
import sys
import urllib.request

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from common import DATA, RESULTS

sys.stdout.reconfigure(encoding="utf-8")


def clean(s):
    return re.sub(r"COL (\w+) VAL ", r"\1: ", s).strip()[:600]


def embed(texts, bs=16):
    out = []
    for i in range(0, len(texts), bs):
        body = json.dumps({"model": "text-embedding-nomic-embed-text-v1.5",
                           "input": ["clustering: " + t for t in texts[i:i + bs]]}).encode()
        req = urllib.request.Request("http://localhost:1234/v1/embeddings", body, {"Content-Type": "application/json"})
        out += [d["embedding"] for d in json.loads(urllib.request.urlopen(req, timeout=300).read())["data"]]
    v = np.array(out, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def recall_at_k(S, true_map, ks=(1, 5, 10)):
    res = {k: 0 for k in ks}
    n = 0
    for li, ris in true_map.items():
        order = np.argsort(-S[li])
        rank = min(int(np.where(order == r)[0][0]) for r in ris)
        n += 1
        for k in ks:
            res[k] += rank < k
    return {f"recall@{k}": res[k] / n for k in ks} | {"n_left_with_match": n}


allres = {}
for ds in ("DBLP-ACM", "Abt-Buy"):
    rows = [l.rstrip("\n").split("\t") for l in open(DATA / ds / "test.txt", encoding="utf-8")]
    rows = [r for r in rows if len(r) == 3]
    Ls = sorted({r[0] for r in rows}); Rs = sorted({r[1] for r in rows})
    li = {t: i for i, t in enumerate(Ls)}; ri = {t: i for i, t in enumerate(Rs)}
    tm = {}
    for l, r, y in rows:
        if y == "1":
            tm.setdefault(li[l], set()).add(ri[r])
    tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit(Ls + Rs)
    S_tf = (tf.transform(Ls) @ tf.transform(Rs).T).toarray()
    EL, ER = embed([clean(t) for t in Ls]), embed([clean(t) for t in Rs])
    S_em = EL @ ER.T
    allres[ds] = {"pool_left": len(Ls), "pool_right": len(Rs),
                  "tfidf_char_ngram": recall_at_k(S_tf, tm), "nomic_embed": recall_at_k(S_em, tm),
                  "tfidf+embed(score平均)": recall_at_k((S_tf - S_tf.mean()) / S_tf.std() + (S_em - S_em.mean()) / S_em.std(), tm)}
    print(ds, json.dumps(allres[ds], indent=1), flush=True)
(RESULTS / "poc08_embedding_blocking.json").write_text(json.dumps(allres, indent=2), encoding="utf-8")
