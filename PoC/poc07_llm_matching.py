"""PoC-07: ローカルLLM(LM Studio)によるEntity Matching。ゼロショット/少数例。
検証する主張: arXiv:2310.11244 「LLMは訓練例なし/少数でも微調整PLMに匹敵し得る」「最良のプロンプトはモデル/データで異なる(プロンプト感度)」。
データは公開ベンチマークのみで、ローカルのエンドポイント(localhost)にだけ送る(外部送信なし)。
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.request

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import f1_score, precision_score, recall_score

from common import DATA, RESULTS

ap = argparse.ArgumentParser()
ap.add_argument("--dataset", default="Abt-Buy")
ap.add_argument("--model", default="google/gemma-4-e4b")
ap.add_argument("--n", type=int, default=300)
ap.add_argument("--shots", type=int, default=0)
ap.add_argument("--prompt", default="A")
ap.add_argument("--seed", type=int, default=1)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")


def load(split):
    rows = []
    for line in open(DATA / a.dataset / f"{split}.txt", encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) == 3:
            rows.append((p[0], p[1], int(p[2])))
    return rows


def clean(s):  # COL x VAL y -> "x: y"
    return re.sub(r"COL (\w+) VAL ", r"\1: ", s).strip()


train, test = load("train"), load("test")
rng = random.Random(a.seed)
pos_t = [r for r in test if r[2] == 1]; neg_t = [r for r in test if r[2] == 0]
sub = rng.sample(pos_t, a.n // 3) + rng.sample(neg_t, a.n - a.n // 3)  # 陽性1/3に層化(自然比率では陽性が少なすぎて不安定)
rng.shuffle(sub)
demos = []
if a.shots:
    pos = [r for r in train if r[2] == 1]
    neg = [r for r in train if r[2] == 0]
    demos = rng.sample(pos, a.shots // 2) + rng.sample(neg, a.shots - a.shots // 2)
    rng.shuffle(demos)

PROMPTS = {
    "A": "Do the two product/publication descriptions refer to the same real-world entity? Answer with only 'Yes' or 'No'.",
    "B": "You are a data-integration expert. Decide whether Record 1 and Record 2 are the same real-world entity. Different models/versions/editions are NOT the same. Answer with only 'Yes' or 'No'.",
}


def ask(l, r):
    msgs = [{"role": "system", "content": PROMPTS[a.prompt]}]
    for dl, dr, dy in demos:
        msgs.append({"role": "user", "content": f"Record 1: {clean(dl)}\nRecord 2: {clean(dr)}"})
        msgs.append({"role": "assistant", "content": "Yes" if dy else "No"})
    msgs.append({"role": "user", "content": f"Record 1: {clean(l)[:700]}\nRecord 2: {clean(r)[:700]}"})
    body = json.dumps({"model": a.model, "messages": msgs, "temperature": 0, "max_tokens": 700}).encode()
    req = urllib.request.Request("http://localhost:1234/v1/chat/completions", body, {"Content-Type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=300).read())["choices"][0]["message"]
    txt = (out.get("content") or "").strip().lower()
    return txt


y, pred, bad = [], [], 0
t0 = time.time()
for i, (l, r, lab) in enumerate(sub):
    txt = ask(l, r)
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
    if txt.startswith("yes"):
        p = 1
    elif txt.startswith("no"):
        p = 0
    else:
        p, bad = 0, bad + 1
    y.append(lab); pred.append(p)
    if i % 25 == 0:
        print(i, round(time.time() - t0), "s", flush=True)
y, pred = np.array(y), np.array(pred)

tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4)).fit([x[0] for x in train] + [x[1] for x in train])
cv = lambda rows: np.asarray(tf.transform([x[0] for x in rows]).multiply(tf.transform([x[1] for x in rows])).sum(axis=1)).ravel()
vrows = load("valid")
ths = np.linspace(0.05, 0.95, 91)
bt = ths[int(np.argmax([f1_score([x[2] for x in vrows], cv(vrows) >= t) for t in ths]))]
tfp = (cv(sub) >= bt).astype(int)
res = {"dataset": a.dataset, "model": a.model, "n": a.n, "positives_in_sample": int(y.sum()), "shots": a.shots,
       "prompt": a.prompt, "unparseable": bad, "seconds": round(time.time() - t0),
       "llm": {"precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred, zero_division=0),
               "f1": f1_score(y, pred, zero_division=0)},
       "tfidf_same_subset": {"f1": f1_score(y, tfp, zero_division=0)}}
print(json.dumps(res, indent=2))
tag = f"{a.dataset}_{a.model.replace('/', '-')}_k{a.shots}_p{a.prompt}_n{a.n}"
(RESULTS / f"poc07_{tag}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

