"""PoC-08b: 事前学習埋め込みによる教師なしマッチング(arXiv:2304.12329 の「複数の言語モデルを比較」を小規模に再現)。
各モデルで左右レコードを埋め込み、コサイン類似度の閾値(validでF1最大化)でtestのペアを判定。訓練不要。
モデル: nomic-embed(LM Studio経由) / all-MiniLM-L6-v2 / bge-small-en-v1.5 / e5-small-v2 (HF公開重み) と TF-IDF。
"""
import json
import re
import sys
import urllib.request

import numpy as np
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from transformers import AutoModel, AutoTokenizer

from common import DATA, RESULTS

sys.stdout.reconfigure(encoding="utf-8")
torch.set_num_threads(8)


def clean(s):
    return re.sub(r"COL (\w+) VAL ", r"\1: ", s).strip()[:500]


def load(ds, split):
    rows = [l.rstrip("\n").split("\t") for l in open(DATA / ds / f"{split}.txt", encoding="utf-8")]
    return [r for r in rows if len(r) == 3]


def hf_embed(name, texts, prefix="", bs=32):
    tok = AutoTokenizer.from_pretrained(name); m = AutoModel.from_pretrained(name).eval(); out = []
    with torch.no_grad():
        for i in range(0, len(texts), bs):
            e = tok([prefix + t for t in texts[i:i + bs]], padding=True, truncation=True, max_length=128, return_tensors="pt")
            h = m(**e).last_hidden_state; msk = e["attention_mask"].unsqueeze(-1)
            v = (h * msk).sum(1) / msk.sum(1)
            out.append(torch.nn.functional.normalize(v, dim=-1).numpy())
    return np.concatenate(out)


def nomic(texts, bs=16):
    out = []
    for i in range(0, len(texts), bs):
        body = json.dumps({"model": "text-embedding-nomic-embed-text-v1.5", "input": ["clustering: " + t for t in texts[i:i + bs]]}).encode()
        req = urllib.request.Request("http://localhost:1234/v1/embeddings", body, {"Content-Type": "application/json"})
        out += [d["embedding"] for d in json.loads(urllib.request.urlopen(req, timeout=300).read())["data"]]
    v = np.array(out, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


MODELS = {
    "nomic-embed-v1.5": nomic,
    "all-MiniLM-L6-v2": lambda t: hf_embed("sentence-transformers/all-MiniLM-L6-v2", t),
    "bge-small-en-v1.5": lambda t: hf_embed("BAAI/bge-small-en-v1.5", t),
    "e5-small-v2": lambda t: hf_embed("intfloat/e5-small-v2", t, "query: "),
}
allres = {}
for ds in ("DBLP-ACM", "Abt-Buy"):
    va, te = load(ds, "valid"), load(ds, "test")
    va = [va[i] for i in np.random.RandomState(0).permutation(len(va))[:800]]
    te = [te[i] for i in np.random.RandomState(0).permutation(len(te))[:800]]
    texts = sorted({clean(x) for r in va + te for x in r[:2]})
    idx = {t: i for i, t in enumerate(texts)}
    yv, yt = np.array([int(r[2]) for r in va]), np.array([int(r[2]) for r in te])
    ths = np.linspace(-0.2, 1.0, 121)
    res = {}

    def evaluate(E, name):
        sim = lambda rows: np.array([E[idx[clean(r[0])]] @ E[idx[clean(r[1])]] for r in rows])
        sv, st = sim(va), sim(te)
        bt = ths[int(np.argmax([f1_score(yv, sv >= t) for t in ths]))]
        res[name] = {"threshold": float(bt), "precision": precision_score(yt, st >= bt, zero_division=0),
                     "recall": recall_score(yt, st >= bt), "f1": f1_score(yt, st >= bt), "auc": roc_auc_score(yt, st)}
        print(ds, name, res[name], flush=True)

    tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit(texts)
    T = tf.transform(texts).toarray().astype(np.float32)
    evaluate(T, "tfidf_char_ngram")
    for name, fn in MODELS.items():
        try:
            evaluate(fn(texts), name)
        except Exception as ex:
            res[name] = {"error": str(ex)[:200]}; print(ds, name, "ERROR", ex, flush=True)
    allres[ds] = {"valid_n": len(va), "test_n": len(te), "test_positives": int(yt.sum()), "results": res}
(RESULTS / "poc08b_embedding_matching.json").write_text(json.dumps(allres, indent=2), encoding="utf-8")
