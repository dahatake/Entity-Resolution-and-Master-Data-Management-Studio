"""PoC-12: 複数ローカルLLM × 複数プロンプト × 2データのプロンプト感度(arXiv:2310.11244の「最良プロンプトはモデル/データごとに異なる」)。
同一の層化標本(陽性1/3)を全組み合わせで使い、Wilson 95%信頼区間と、同一標本でのMcNemar風の不一致数を出す。
データは公開ベンチマークのみ、ローカル(localhost)のLM Studioにだけ送る。
"""
import argparse
import json
import math
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import DATA, RESULTS

ap = argparse.ArgumentParser()
ap.add_argument("--models", default="google/gemma-4-e4b,qwen/qwen3.5-9b,flint")
ap.add_argument("--datasets", default="Abt-Buy,DBLP-ACM")
ap.add_argument("--n", type=int, default=60)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--max_tokens", type=int, default=2000)
a = ap.parse_args()
sys.stdout.reconfigure(encoding="utf-8")

PROMPTS = {
    "A_plain": "Do the two product/publication descriptions refer to the same real-world entity? Answer with only 'Yes' or 'No'.",
    "B_strict": "You are a data-integration expert. Decide whether Record 1 and Record 2 are the same real-world entity. Different models/versions/editions are NOT the same. Answer with only 'Yes' or 'No'.",
    "C_lenient": "Two records from different catalogs are given. Differences in wording, abbreviation, missing fields or formatting are common even for the same entity. If they most likely describe the same entity, answer 'Yes'. Otherwise 'No'. Answer with only 'Yes' or 'No'.",
}


def clean(s):
    return re.sub(r"COL (\w+) VAL ", r"\1: ", s).strip()


def sample(ds):
    rows = [l.rstrip("\n").split("\t") for l in open(DATA / ds / "test.txt", encoding="utf-8")]
    rows = [(r[0], r[1], int(r[2])) for r in rows if len(r) == 3]
    rng = random.Random(1)
    pos = [r for r in rows if r[2] == 1]; neg = [r for r in rows if r[2] == 0]
    s = rng.sample(pos, a.n // 3) + rng.sample(neg, a.n - a.n // 3)
    rng.shuffle(s)
    return s


ERR = []


def ask(model, prompt, l, r):
    sysmsg = PROMPTS[prompt] + (" /no_think" if "qwen" in model else "")
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": a.max_tokens, "messages": [
        {"role": "system", "content": sysmsg},
        {"role": "user", "content": f"Record 1: {clean(l)[:400]}\nRecord 2: {clean(r)[:400]}"}]}).encode()
    req = urllib.request.Request("http://localhost:1234/v1/chat/completions", body, {"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=900).read())["choices"][0]["message"]
            break
        except Exception as ex:
            msg = repr(ex)[:100]
            if hasattr(ex, "read"):
                msg += " " + ex.read()[:150].decode("utf-8", "replace")
            ERR.append(msg)
            time.sleep(5 * (attempt + 1))
    else:
        return None
    if not (out.get("content") or "").strip():
        ERR.append("empty_content finish=" + str(out.get("finish_reason")))
    txt = re.sub(r"<think>.*?</think>", "", (out.get("content") or ""), flags=re.S).strip().lower()
    if not (txt.startswith("yes") or txt.startswith("no")):
        ERR.append("raw=" + repr(txt[:60]))
    if txt.startswith("yes"):
        return 1
    if txt.startswith("no"):
        return 0
    return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 1)
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


results = []
preds = {}
RES_F = RESULTS / "poc12_llm_prompt_sensitivity.json"
if RES_F.exists():
    old = json.loads(RES_F.read_text(encoding="utf-8"))
    results = [r for r in old["results"] if r["unparseable"] <= 2]
    preds = {f"{r['dataset']}|{r['model']}|{r['prompt']}": old["preds"][f"{r['dataset']}|{r['model']}|{r['prompt']}"] for r in results}
DONE = {(r["dataset"], r["model"], r["prompt"]) for r in results}
for ds in a.datasets.split(","):
    S = sample(ds)
    for model in a.models.split(","):
        for pr in PROMPTS:
            if (ds, model, pr) in DONE:
                continue
            ERR.clear()
            t0 = time.time()
            with ThreadPoolExecutor(a.workers) as ex:
                out = list(ex.map(lambda x: ask(model, pr, x[0], x[1]), S))
            y = [x[2] for x in S]
            unparse = sum(o is None for o in out)
            p = [0 if o is None else o for o in out]
            tp = sum(1 for a_, b in zip(p, y) if a_ == 1 and b == 1); fp = sum(1 for a_, b in zip(p, y) if a_ == 1 and b == 0)
            fn = sum(1 for a_, b in zip(p, y) if a_ == 0 and b == 1)
            prec = tp / (tp + fp) if tp + fp else 0; rec = tp / (tp + fn) if tp + fn else 0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
            acc_lo, acc_hi = wilson(sum(1 for a_, b in zip(p, y) if a_ == b), len(y))
            r = {"dataset": ds, "model": model, "prompt": pr, "n": len(y), "unparseable": unparse, "precision": prec,
                 "recall": rec, "f1": f1, "tp": tp, "fp": fp, "fn": fn, "acc_ci95": [acc_lo, acc_hi],
                 "seconds": round(time.time() - t0), "errors": ERR[-3:]}
            results.append(r); preds[f"{ds}|{model}|{pr}"] = p
            print(json.dumps(r, ensure_ascii=False), flush=True)
            (RESULTS / "poc12_llm_prompt_sensitivity.json").write_text(
                json.dumps({"results": results, "preds": preds}, ensure_ascii=False, indent=1), encoding="utf-8")



