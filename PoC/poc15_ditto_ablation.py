"""PoC-15: Dittoの3最適化(DK/SU/DA)の個別寄与のアブレーション(arXiv:2004.00584 Table 5の検証)。
簡易再実装(論文の実装そのものではない):
  DK : 正規表現による型付け(数字→[NUM]..[/NUM]、英数字混在トークン→[ID]..[/ID])。論文はNERモデル+正規表現。
  SU : TF-IDFが高い語を残して、各エントリを語数予算に収める(論文と同じ発想)。未使用時は通常の切り詰め。
  DA : データセット別の演算子(Abt-Buy: span_shuffle、Beer: attr_del; 論文Table 7)+entry_swapを、各例にp=0.5で毎エポック適用。
       論文のMixDA(埋め込み補間)は未実装。
モデル: 既定は論文ベースラインと同じDistilBERT。
"""
import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter

import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from common import DATA, RESULTS

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="Abt-Buy")
ap.add_argument("--variant", default="base", choices=["base", "dk", "su", "da", "all"])
ap.add_argument("--model", default="distilbert-base-uncased")
ap.add_argument("--epochs", type=int, default=15)
ap.add_argument("--max_len", type=int, default=128)
ap.add_argument("--bs", type=int, default=32)
ap.add_argument("--lr", type=float, default=3e-5)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--da_op", default="")
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")
random.seed(a.seed); np.random.seed(a.seed); torch.manual_seed(a.seed)
dev = "cuda" if torch.cuda.is_available() else "cpu"
USE_DK = a.variant in ("dk", "all"); USE_SU = a.variant in ("su", "all"); USE_DA = a.variant in ("da", "all")
DA_OP = a.da_op or {"Abt-Buy": "span_shuffle", "Beer": "attr_del"}.get(a.dataset, "span_del")

ITEM = re.compile(r"COL (\S+) VAL (.*?)(?= COL \S+ VAL |$)")


def parse(s):
    return [[k, v.strip()] for k, v in ITEM.findall(s)]


def ser(items):
    return " ".join(f"COL {k} VAL {v}" for k, v in items)


def load(split):
    rows = []
    for line in open(DATA / a.dataset / f"{split}.txt", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 3:
            rows.append((parse(p[0]), parse(p[1]), int(p[2])))
    return rows


train, valid, test = load("train"), load("valid"), load("test")

# IDF(訓練コーパス)
df = Counter()
for l, r, _ in train:
    for side in (l, r):
        df.update({w.lower() for _, v in side for w in v.split()})
N = 2 * len(train)
idf = lambda w: math.log((N + 1) / (df.get(w.lower(), 0) + 1))


def dk(items):
    out = []
    for k, v in items:
        toks = []
        for w in v.split():
            if re.fullmatch(r"[\d.,]+%?", w):
                toks.append(f"[NUM] {w} [/NUM]")
            elif len(w) >= 3 and re.search(r"\d", w) and re.search(r"[A-Za-z]", w):
                toks.append(f"[ID] {w} [/ID]")
            else:
                toks.append(w)
        out.append([k, " ".join(toks)])
    return out


def summarize(items, budget):
    words = [(i, j, w) for i, (_, v) in enumerate(items) for j, w in enumerate(v.split())]
    if len(words) <= budget:
        return items
    keep = sorted(sorted(words, key=lambda t: -idf(t[2]))[:budget], key=lambda t: (t[0], t[1]))
    out = [[k, []] for k, _ in items]
    for i, _, w in keep:
        out[i][1].append(w)
    return [[k, " ".join(ws)] for k, ws in out]


def augment(l, r):
    l = [x[:] for x in l]; r = [x[:] for x in r]
    side = l if random.random() < 0.5 else r
    if side:
        i = random.randrange(len(side)); ws = side[i][1].split()
        if DA_OP == "span_del" and len(ws) > 2:
            n = random.randint(1, min(4, len(ws) - 1)); s = random.randrange(len(ws) - n + 1); del ws[s:s + n]
        elif DA_OP == "span_shuffle" and len(ws) > 2:
            n = random.randint(2, min(4, len(ws))); s = random.randrange(len(ws) - n + 1)
            seg = ws[s:s + n]; random.shuffle(seg); ws[s:s + n] = seg
        elif DA_OP == "attr_del" and len(side) > 1:
            del side[i]
        elif DA_OP == "attr_shuffle":
            random.shuffle(side)
        if DA_OP in ("span_del", "span_shuffle") and i < len(side):
            side[i][1] = " ".join(ws)
    if random.random() < 0.5:
        l, r = r, l
    return l, r


def prep(l, r, aug=False):
    if aug and USE_DA:
        l, r = augment(l, r)
    if USE_DK:
        l, r = dk(l), dk(r)
    if USE_SU:
        b = int((a.max_len - 3) / 2 / 1.6)
        l, r = summarize(l, b), summarize(r, b)
    return ser(l), ser(r)


tok = AutoTokenizer.from_pretrained(a.model)
model = AutoModelForSequenceClassification.from_pretrained(a.model, num_labels=2).to(dev)


def enc(pairs):
    L, R = zip(*pairs)
    return tok(list(L), list(R), truncation=True, max_length=a.max_len, padding="max_length", return_tensors="pt").to(dev)


def predict(rows, bs=128):
    model.eval(); out = []
    with torch.no_grad():
        for i in range(0, len(rows), bs):
            e = enc([prep(l, r) for l, r, _ in rows[i:i + bs]])
            with torch.autocast(dev, dtype=torch.bfloat16, enabled=(dev == "cuda")):
                lg = model(**e).logits
            out.append(torch.softmax(lg.float(), -1)[:, 1].cpu().numpy())
    return np.concatenate(out)


def prf(y, p):
    return {"precision": precision_score(y, p, zero_division=0), "recall": recall_score(y, p, zero_division=0), "f1": f1_score(y, p, zero_division=0)}


yv = np.array([x[2] for x in valid]); yt = np.array([x[2] for x in test]); ytr = np.array([x[2] for x in train])
opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
steps = a.epochs * math.ceil(len(train) / a.bs)
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))
w = torch.tensor([1.0, float((ytr == 0).sum() / max(1, (ytr == 1).sum()))]).clamp(max=8.0).to(dev)
lossf = torch.nn.CrossEntropyLoss(weight=w)
best = {"valid_f1": -1}; t0 = time.time()
for ep in range(a.epochs):
    model.train(); order = np.random.RandomState(a.seed + ep).permutation(len(train))
    for i in range(0, len(order), a.bs):
        b = order[i:i + a.bs]
        e = enc([prep(train[j][0], train[j][1], aug=True) for j in b])
        with torch.autocast(dev, dtype=torch.bfloat16, enabled=(dev == "cuda")):
            lg = model(**e).logits
        loss = lossf(lg.float(), torch.tensor(ytr[b]).to(dev))
        loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); opt.step(); sched.step(); opt.zero_grad()
    f = f1_score(yv, predict(valid) >= 0.5)
    print(f"epoch {ep} valid F1 {f:.4f} t={time.time() - t0:.0f}s", flush=True)
    if f > best["valid_f1"]:
        best = {"valid_f1": f, "epoch": ep, **{"test_" + k: v for k, v in prf(yt, predict(test) >= 0.5).items()}}
res = {"dataset": a.dataset, "variant": a.variant, "model": a.model, "seed": a.seed, "epochs": a.epochs, "max_len": a.max_len,
       "da_op": DA_OP if USE_DA else None, "train_n": len(train), "test_n": len(test), "seconds": round(time.time() - t0), **best}
print(json.dumps(res))
(RESULTS / f"poc15_{a.dataset}_{a.variant}_{a.model.split('/')[-1]}_ml{a.max_len}_s{a.seed}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
