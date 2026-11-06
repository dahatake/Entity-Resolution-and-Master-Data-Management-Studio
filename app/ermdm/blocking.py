"""ブロッキング(FR-BLK-01/02/05)。複数ルールの和集合、候補数の事前見積、上限超過時の停止。"""
from collections import defaultdict
from itertools import combinations

from .util import ApiError

TRANSFORMS = {"exact", "prefix", "suffix", "year", "ym"}


def validate(rules):
    if not isinstance(rules, list) or not rules:
        raise ApiError(400, "ブロッキングルールが1件以上必要です")
    names = set()
    for r in rules:
        n = r.get("name")
        if not n or n in names:
            raise ApiError(400, f"ルール名が空または重複しています: {n}")
        names.add(n)
        if not r.get("keys"):
            raise ApiError(400, f"{n}: keys が必要です")
        for k in r["keys"]:
            if k.get("t", "exact") not in TRANSFORMS or not k.get("field"):
                raise ApiError(400, f"{n}: 不正なキー定義 {k}")
            if k.get("t") in ("prefix", "suffix") and int(k.get("n", 0)) < 1:
                raise ApiError(400, f"{n}: prefix/suffix には n(1以上)が必要です")


def _key(std: dict, keys):
    out = []
    for k in keys:
        v = std.get(k["field"], "")
        if not v:
            return None
        t = k.get("t", "exact")
        if t == "prefix":
            v = v[: int(k["n"])]
        elif t == "suffix":
            v = v[-int(k["n"]):]
        elif t == "year":
            v = v[:4]
        elif t == "ym":
            v = v[:7]
        out.append(v)
    return tuple(out)


def build_blocks(rules, std_by_rid):
    blocks = []
    for r in rules:
        d = defaultdict(list)
        for rid in sorted(std_by_rid):
            k = _key(std_by_rid[rid], r["keys"])
            if k is not None:
                d[k].append(rid)
        blocks.append(d)
    return blocks


def estimate(rules, std_by_rid, max_block=500, source_of=None, link_type="all"):
    """候補ペア数の見積(ルールごと)。実際にペアを生成せず、ブロックの大きさだけから計算する。"""
    out, total = [], 0
    for r, d in zip(rules, build_blocks(rules, std_by_rid)):
        pairs = skipped = largest = 0
        for k, ids in d.items():
            n = len(ids)
            largest = max(largest, n)
            if n < 2:
                continue
            if n > max_block:
                skipped += 1
                continue
            if link_type == "cross" and source_of:
                cnt = defaultdict(int)
                for i in ids:
                    cnt[source_of[i]] += 1
                pairs += (n * (n - 1) // 2) - sum(c * (c - 1) // 2 for c in cnt.values())
            else:
                pairs += n * (n - 1) // 2
        out.append({"rule": r["name"], "blocks": sum(1 for ids in d.values() if len(ids) >= 2), "pairs_upper": pairs, "skipped_blocks": skipped, "largest_block": largest})
        total += pairs
    return {"rules": out, "total_upper": total}


def generate(rules, std_by_rid, max_block=500, max_candidates=2_000_000, source_of=None, link_type="all"):
    """候補ペア {(a,b): [ルール番号...]} を返す。見積が上限を超えたら生成せずに停止する(FR-BLK-05)。"""
    est = estimate(rules, std_by_rid, max_block, source_of, link_type)
    if est["total_upper"] > max_candidates:
        raise ApiError(409, f"候補ペアの見積({est['total_upper']:,}件)が上限({max_candidates:,}件)を超えるため停止しました。ブロッキングを絞ってください。", estimate=est)
    cand = {}
    for ri, (r, d) in enumerate(zip(rules, build_blocks(rules, std_by_rid))):
        for ids in d.values():
            if len(ids) < 2 or len(ids) > max_block:
                continue
            for a, b in combinations(ids, 2):
                if link_type == "cross" and source_of and source_of[a] == source_of[b]:
                    continue
                cand.setdefault((a, b), []).append(ri)
    return cand, est
