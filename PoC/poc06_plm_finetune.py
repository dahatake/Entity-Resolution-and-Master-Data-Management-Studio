"""PoC-06: Ditto型(事前学習LM + ペア分類の微調整)の再現と、古典ベースラインとの比較。
検証する主張: arXiv:2004.00584 「PLMの微調整は従来手法を大きく上回る(特にテキスト主体のAbt-Buy)」「少ない訓練データでも有効」。
データ: Ditto公式リポジトリのer_magellan分割(train/valid/test)。正解は付属ラベル。
注意: Dittoの最適化(ドメイン知識注入、要約、データ拡張)と RoBERTa-base は使わず、DistilBERT+シリアライズ(COL/VAL)のみ。
"""
import argparse
import json
import random
import sys
import time

import numpy as np
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score, precision_score, recall_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from common import DATA, RESULTS

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="DBLP-ACM")
ap.add_argument("--model", default="distilbert-base-uncased")
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--max_len", type=int, default=128)
ap.add_argument("--train_frac", type=float, default=1.0)
ap.add_argument("--bs", type=int, default=32)
ap.add_argument("--lr", type=float, default=3e-5)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--threads", type=int, default=16)
ap.add_argument("--valid_n", type=int, default=600)
ap.add_argument("--test_n", type=int, default=0)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
torch.set_num_threads(a.threads)
random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)


def load(split):
    L, R, y = [], [], []
    for line in open(DATA / a.dataset / f"{split}.txt", encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 3:
            L.append(parts[0]); R.append(parts[1]); y.append(int(parts[2]))
    return L, R, np.array(y)


trL, trR, trY = load("train")
vaL, vaR, vaY = load("valid")
teL, teR, teY = load("test")
if a.test_n:
    ti = np.random.RandomState(0).permutation(len(teY))[: a.test_n]
    teL, teR, teY = [teL[i] for i in ti], [teR[i] for i in ti], teY[ti]
if a.train_frac < 1.0:
    idx = np.random.RandomState(a.seed).permutation(len(trY))[: int(len(trY) * a.train_frac)]
    trL, trR, trY = [trL[i] for i in idx], [trR[i] for i in idx], trY[idx]
vi = np.random.RandomState(0).permutation(len(vaY))[: a.valid_n]
vaL_s, vaR_s, vaY_s = [vaL[i] for i in vi], [vaR[i] for i in vi], vaY[vi]
print(f"{a.dataset}: train={len(trY)} (pos {trY.sum()}) valid={len(vaY)} test={len(teY)} (pos {teY.sum()})", flush=True)


def prf(y, p):
    return {"precision": precision_score(y, p, zero_division=0), "recall": recall_score(y, p, zero_division=0),
            "f1": f1_score(y, p, zero_division=0)}


res = {"dataset": a.dataset, "model": a.model, "train_n": int(len(trY)), "epochs": a.epochs, "seed": a.seed}

# ---- 古典ベースライン: TF-IDF コサイン類似度 + validで選んだ閾値 ----
tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit(trL + trR + vaL + vaR)


def cos(L, R):
    A, B = tf.transform(L), tf.transform(R)
    return np.asarray(A.multiply(B).sum(axis=1)).ravel()


cv, ct = cos(vaL, vaR), cos(teL, teR)
ths = np.linspace(0.05, 0.95, 91)
bt = ths[int(np.argmax([f1_score(vaY, cv >= t) for t in ths]))]
res["tfidf_char_cosine"] = {"threshold": float(bt), **prf(teY, ct >= bt)}
print("TF-IDF baseline:", res["tfidf_char_cosine"], flush=True)

# ---- PLM微調整(Ditto風シリアライズ: 左 [SEP] 右) ----
tok = AutoTokenizer.from_pretrained(a.model)
model = AutoModelForSequenceClassification.from_pretrained(a.model, num_labels=2)


def enc(L, R):
    return tok(L, R, truncation=True, max_length=a.max_len, padding="max_length", return_tensors="pt")


def predict(L, R, bs=64):
    model.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(L), bs):
            e = enc(L[i:i + bs], R[i:i + bs])
            out.append(torch.softmax(model(**e).logits, -1)[:, 1].numpy())
    return np.concatenate(out)


opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
steps = a.epochs * ((len(trY) + a.bs - 1) // a.bs)
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))
w = torch.tensor([1.0, float((trY == 0).sum() / max(1, (trY == 1).sum()))]).clamp(max=8.0)
lossf = torch.nn.CrossEntropyLoss(weight=w)
best = {"f1": -1}
t0 = time.time()
for ep in range(a.epochs):
    model.train()
    order = np.random.RandomState(a.seed + ep).permutation(len(trY))
    for bi, i in enumerate(range(0, len(order), a.bs)):
        b = order[i:i + a.bs]
        e = enc([trL[j] for j in b], [trR[j] for j in b])
        loss = lossf(model(**e).logits, torch.tensor(trY[b]))
        loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step(); sched.step(); opt.zero_grad()
        if bi % 20 == 0:
            print(f"ep{ep} step{bi} loss {loss.item():.4f} t={time.time() - t0:.0f}s", flush=True)
    pv = predict(vaL, vaR)
    f = f1_score(vaY, pv >= 0.5)
    print(f"epoch {ep} valid F1 {f:.4f} t={time.time() - t0:.0f}s", flush=True)
    if f > best["f1"]:
        pt = predict(teL, teR)
        best = {"f1": f, "epoch": ep, **{"test_" + k: v for k, v in prf(teY, pt >= 0.5).items()}}
res["plm_finetuned"] = best
# 追加テスト(WDCの未見製品率違い): 最終エポックのモデルで評価。test_n>0なら固定シードで部分抽出
extras = {}
for fn in sorted((DATA / a.dataset).glob("test_*.txt")):
    rows = [l.rstrip("\n").split("\t") for l in open(fn, encoding="utf-8")]
    rows = [r for r in rows if len(r) == 3]
    ii = np.random.RandomState(0).permutation(len(rows))[: (a.test_n or len(rows))]
    L_, R_, y_ = [rows[i][0] for i in ii], [rows[i][1] for i in ii], np.array([int(rows[i][2]) for i in ii])
    pe = predict(L_, R_)
    cv_ = cos(L_, R_)
    extras[fn.stem] = {"n": len(y_), "positives": int(y_.sum()), "plm_final_epoch": prf(y_, pe >= 0.5), "tfidf_char_cosine": prf(y_, cv_ >= bt)}
    print(fn.stem, extras[fn.stem], flush=True)
res["extra_tests"] = extras
res["train_seconds"] = round(time.time() - t0)
print(json.dumps(res, indent=2, ensure_ascii=False))
tag = f"{a.dataset}_{a.model.replace('/', '-')}_n{len(trY)}_s{a.seed}"
(RESULTS / f"poc06_{tag}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")



