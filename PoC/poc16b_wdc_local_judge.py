"""PoC-16b: WDC再評価600組を、ローカルLLM(LM Studio)でも判定する(第2の判定者)。ラベルは見せない。再開可能。"""
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import RESULTS

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen/qwen3.5-9b"
OUT = RESULTS / f"wdc_local_out_{MODEL.replace('/', '-')}.json"
pairs = json.load(open(RESULTS / "wdc_relabel_pairs_blind.json", encoding="utf-8"))
done = {d["id"]: d for d in json.load(open(OUT, encoding="utf-8"))} if OUT.exists() else {}
SYS = ("Decide whether the two product offers describe the SAME real-world product. Different models, versions, variants, sizes, colors, "
       "capacities or pack counts are DIFFERENT products. Missing fields are common. Answer with only 'Yes' or 'No'.")


def ask(p):
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": 2500, "messages": [
        {"role": "system", "content": SYS + (" /no_think" if "qwen" in MODEL else "")},
        {"role": "user", "content": f"Offer 1: {p['a'][:500]}\nOffer 2: {p['b'][:500]}"}]}).encode()
    for attempt in range(3):
        try:
            m = json.loads(urllib.request.urlopen(urllib.request.Request("http://localhost:1234/v1/chat/completions", body,
                           {"Content-Type": "application/json"}), timeout=900).read())["choices"][0]["message"]
            t = re.sub(r"<think>.*?</think>", "", m.get("content") or "", flags=re.S).strip().lower()
            if t.startswith("yes"):
                return {"id": p["id"], "match": 1}
            if t.startswith("no"):
                return {"id": p["id"], "match": 0}
        except Exception:
            time.sleep(5 * (attempt + 1))
    return {"id": p["id"], "match": None}


todo = [p for p in pairs if p["id"] not in done or done[p["id"]]["match"] is None]
t0 = time.time()
with ThreadPoolExecutor(3) as ex:
    for i, r in enumerate(ex.map(ask, todo)):
        done[r["id"]] = r
        if i % 20 == 0:
            json.dump(list(done.values()), open(OUT, "w", encoding="utf-8"))
            print(i, len(todo), round(time.time() - t0), "s", flush=True)
json.dump(list(done.values()), open(OUT, "w", encoding="utf-8"))
print("finished", sum(1 for d in done.values() if d["match"] is None), "unparseable")
