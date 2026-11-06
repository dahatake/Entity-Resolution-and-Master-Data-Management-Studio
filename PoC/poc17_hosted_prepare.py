"""PoC-17(準備): ホスト型LLM評価用の標本。poc12と同一の層化45組(seed=1)を、ラベルなしで書き出す。公開ベンチマークのみ。"""
import json
import random
import re
import sys

from common import DATA, RESULTS

sys.stdout.reconfigure(encoding="utf-8")


def clean(s):
    return re.sub(r"COL (\w+) VAL ", r"\1: ", s).strip()


for ds in ("Abt-Buy", "DBLP-ACM"):
    rows = [l.rstrip("\n").split("\t") for l in open(DATA / ds / "test.txt", encoding="utf-8")]
    rows = [(r[0], r[1], int(r[2])) for r in rows if len(r) == 3]
    rng = random.Random(1)
    pos = [r for r in rows if r[2] == 1]; neg = [r for r in rows if r[2] == 0]
    s = rng.sample(pos, 15) + rng.sample(neg, 30)
    rng.shuffle(s)
    json.dump([{"id": i, "a": clean(x[0])[:400], "b": clean(x[1])[:400]} for i, x in enumerate(s)],
              open(RESULTS / f"hosted_{ds}_blind.json", "w", encoding="utf-8"), ensure_ascii=False)
    json.dump([x[2] for x in s], open(RESULTS / f"hosted_{ds}_truth.json", "w"))
    print(ds, len(s), sum(x[2] for x in s))
