"""クラスタリング(FR-CLU-01/02)。連結成分と中心法を差し替え可能にし、否定制約(cannot-link)を守る。"""
from collections import defaultdict
from itertools import combinations


class UF:
    def __init__(self, nodes):
        self.p = {n: n for n in nodes}

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # 常に小さい rid を代表にして、結果を決定的にする
            if ra < rb:
                self.p[rb] = ra
            else:
                self.p[ra] = rb


def groups(uf, nodes):
    g = defaultdict(list)
    for n in nodes:
        g[uf.find(n)].append(n)
    return [sorted(v) for v in sorted(g.values(), key=lambda v: min(v))]


def connected_components(nodes, edges):
    uf = UF(nodes)
    for a, b, _ in edges:
        uf.union(a, b)
    return groups(uf, nodes)


def center_clustering(nodes, edges):
    """中心法: 確率の高い辺から順に、未割当のノードを既存の中心へ付ける(連鎖による過剰統合を抑える)。"""
    center_of, centers = {}, set()
    for a, b, p in sorted(edges, key=lambda e: (-e[2], e[0], e[1])):
        ca, cb = center_of.get(a), center_of.get(b)
        if ca is None and cb is None:
            centers.add(a)
            center_of[a] = a
            center_of[b] = a
        elif ca is None and cb is not None:
            if cb == b:
                center_of[a] = b
        elif cb is None and ca is not None:
            if ca == a:
                center_of[b] = a
    uf = UF(nodes)
    for n, c in center_of.items():
        uf.union(n, c)
    return groups(uf, nodes)


CLUSTERERS = {"components": connected_components, "center": center_clustering}


def enforce_cannot(clusters, edges, cannot):
    """否定制約を満たすまで、制約を結ぶ最大全域木の経路上で最も弱い辺を取り除く(決定的)。戻り値: (クラスタ, 取り除いた辺)。"""
    if not cannot:
        return clusters, []
    cset = set(cannot)
    by_node = defaultdict(list)
    for a, b, p in edges:
        by_node[a].append((a, b, p))
        by_node[b].append((a, b, p))
    out, removed = [], []
    for cl in clusters:
        s = set(cl)
        viol = [(a, b) for (a, b) in cset if a in s and b in s]
        if not viol:
            out.append(cl)
            continue
        es = {(a, b): p for a, b, p in edges if a in s and b in s}
        for _ in range(len(es) + 1):
            comps = connected_components(cl, [(a, b, p) for (a, b), p in es.items()])
            cid = {n: i for i, c in enumerate(comps) for n in c}
            bad = sorted((a, b) for (a, b) in viol if cid[a] == cid[b])
            if not bad:
                break
            a0, b0 = bad[0]
            # 最大全域木(Kruskal)上の a0-b0 経路を探す
            comp = comps[cid[a0]]
            cs = set(comp)
            tree = defaultdict(list)
            uf = UF(comp)
            for (a, b), p in sorted(((k, v) for k, v in es.items() if k[0] in cs and k[1] in cs), key=lambda kv: (-kv[1], kv[0])):
                if uf.find(a) != uf.find(b):
                    uf.union(a, b)
                    tree[a].append((b, p))
                    tree[b].append((a, p))
            prev, stack = {a0: None}, [a0]
            while stack:
                x = stack.pop()
                for y, p in sorted(tree[x]):
                    if y not in prev:
                        prev[y] = (x, p)
                        stack.append(y)
            path, x = [], b0
            while prev.get(x):
                px, p = prev[x]
                path.append((min(px, x), max(px, x), p))
                x = px
            if not path:
                break
            weakest = min(path, key=lambda e: (e[2], e[0], e[1]))
            del es[(weakest[0], weakest[1])]
            removed.append((weakest[0], weakest[1]))
        out.extend(connected_components(cl, [(a, b, p) for (a, b), p in es.items()]))
    out = sorted(out, key=lambda v: min(v))
    return out, removed


def cluster(nodes, edges, method="components", cannot=()):
    cl = CLUSTERERS[method](nodes, edges)
    cl, removed = enforce_cannot(cl, edges, list(cannot))
    return cl, removed


def stats(clusters, edge_p, scored_p, lo, truth=None):
    """クラスタごとの診断。edge_p: 採用した辺 {(a,b):p}、scored_p: 直接比較した全候補 {(a,b):p}、lo: 否定とみなす確率の上限。"""
    where = {n: i for i, c in enumerate(clusters) for n in c}
    inner = defaultdict(list)
    for (a, b), p in edge_p.items():
        if where.get(a) is not None and where.get(a) == where.get(b):
            inner[where[a]].append(p)
    rej = defaultdict(int)
    for (a, b), p in scored_p.items():
        if p < lo and where.get(a) is not None and where.get(a) == where.get(b):
            rej[where[a]] += 1
    res = []
    for i, c in enumerate(clusters):
        n = len(c)
        ed = inner.get(i, [])
        imp = None
        if truth is not None:
            ts = {truth.get(r) for r in c if truth.get(r)}
            imp = 1 if len(ts) > 1 else 0
        res.append({"cidx": i, "size": n, "n_edges": len(ed), "n_rejected": rej.get(i, 0), "n_transitive": max(0, n * (n - 1) // 2 - len(ed)),
                    "min_p": min(ed) if ed else None, "impure_truth": imp})
    return res
