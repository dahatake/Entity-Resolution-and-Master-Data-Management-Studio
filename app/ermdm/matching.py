"""ペア判定(FR-MAT-01/02/04)。Fellegi–Sunter 確率モデル(教師なしEM)・決定的ルール・ハイブリッドを、差し替え可能な Matcher として提供する。
外部ライブラリに依存せず、結果が環境間で一致するよう自前実装している。"""
import math
from collections import Counter
from functools import lru_cache

from .util import ApiError, jdump, sha

# ------------------------------------------------------------------ 文字列類似度
@lru_cache(maxsize=400_000)
def jaro_winkler(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0 if s1 else 0.0
    l1, l2 = len(s1), len(s2)
    if not l1 or not l2:
        return 0.0
    win = max(l1, l2) // 2 - 1
    win = max(win, 0)
    m1, m2 = [False] * l1, [False] * l2
    matches = 0
    for i, c in enumerate(s1):
        for j in range(max(0, i - win), min(l2, i + win + 1)):
            if not m2[j] and s2[j] == c:
                m1[i] = m2[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    t, k = 0, 0
    for i in range(l1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t //= 2
    jaro = (matches / l1 + matches / l2 + (matches - t) / matches) / 3
    if jaro > 0.7:
        pre = 0
        for a, b in zip(s1[:4], s2[:4]):
            if a != b:
                break
            pre += 1
        jaro += pre * 0.1 * (1 - jaro)
    return jaro


def lev_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        d = [i for i in range(len(a)) if a[i] != b[i]]
        return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]] and a[d[1]] == b[d[0]])
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(s) and s[i] == l[i]:
        i += 1
    return s[i:] == l[i + 1:]


# ------------------------------------------------------------------ 比較器(gamma: -1=欠損, 0=最良 … K-1=不一致)
def cmp_exact(a, b):
    if not a or not b:
        return -1
    return 0 if a == b else 1


def _jw_levels(a, b, cuts):
    if not a or not b:
        return -1
    if a == b:
        return 0
    s = jaro_winkler(a, b)
    for i, c in enumerate(cuts, start=1):
        if s >= c:
            return i
    return len(cuts) + 1


def cmp_name(a, b): return _jw_levels(a, b, (0.92, 0.85))
def cmp_text(a, b): return _jw_levels(a, b, (0.95, 0.88, 0.75))


def cmp_date(a, b):
    if not a or not b:
        return -1
    if a == b:
        return 0
    if lev_le1(a, b):
        return 1
    if a[:7] == b[:7]:
        return 2
    if a[:4] == b[:4]:
        return 3
    return 4


def cmp_email(a, b):
    if not a or not b:
        return -1
    if a == b:
        return 0
    if a.split("@")[0] == b.split("@")[0]:
        return 1
    return 2 if jaro_winkler(a, b) >= 0.9 else 3


def cmp_addr(a, b):
    """正規化済みの住所(例: 愛知県名古屋市中区5-16-17)。番地が違えば別の所在地として強い否定の根拠にする。"""
    if not a or not b:
        return -1
    if a == b:
        return 0
    import re
    ma, mb = re.match(r"^(\D*)(\d+(?:-\d+)*)$", a), re.match(r"^(\D*)(\d+(?:-\d+)*)$", b)
    if ma and mb and ma.group(1) == mb.group(1):
        na, nb = ma.group(2).split("-"), mb.group(2).split("-")
        if na[:-1] == nb[:-1] and len(na) > 1:
            return 1
        return 2
    return 3 if jaro_winkler(a, b) >= 0.9 else 4


COMPARATORS = {
    "exact": (cmp_exact, ["完全一致", "不一致"]),
    "name": (cmp_name, ["完全一致", "JW≥0.92", "JW≥0.85", "不一致"]),
    "text": (cmp_text, ["完全一致", "JW≥0.95", "JW≥0.88", "JW≥0.75", "不一致"]),
    "addr": (cmp_addr, ["完全一致", "同一街区(末尾の番号違い)", "同一町字(番地違い)", "JW≥0.9", "不一致"]),
    "date": (cmp_date, ["完全一致", "1文字差/入替", "同年月", "同年", "不一致"]),
    "email": (cmp_email, ["完全一致", "ローカル部一致", "JW≥0.9", "不一致"]),
}

EPS = 1e-6


def _pav(vals, weights, increasing):
    """重み付き等調回帰(Pool Adjacent Violators)。水準が悪いほど m は下がり、u は上がる、という単調性を課す。"""
    sign = 1 if increasing else -1
    blocks = []
    for v, w in zip(vals, weights):
        blocks.append([v * sign, max(w, 1e-12), 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            b2, b1 = blocks.pop(), blocks.pop()
            w = b1[1] + b2[1]
            blocks.append([(b1[0] * b1[1] + b2[0] * b2[1]) / w, w, b1[2] + b2[2]])
    out = []
    for v, w, n in blocks:
        out.extend([v * sign] * n)
    return out


def _init_m(k):
    raw = [0.55 ** i for i in range(k)]
    s = sum(raw)
    return [x / s for x in raw]


def validate_model_config(cfg):
    if cfg.get("matcher") not in MATCHERS:
        raise ApiError(400, f"matcher は {sorted(MATCHERS)} のいずれか")
    if cfg["matcher"] != "deterministic":
        fl = cfg.get("fields")
        if not fl:
            raise ApiError(400, "確率モデルには fields が必要です")
        for f in fl:
            if f.get("cmp") not in COMPARATORS or not f.get("name"):
                raise ApiError(400, f"不正なフィールド定義: {f}(cmp は {sorted(COMPARATORS)})")
    for r in cfg.get("rules", []):
        if not r.get("fields") or not (0 < float(r.get("p", 0)) <= 1):
            raise ApiError(400, f"不正な決定的ルール: {r}")


# ------------------------------------------------------------------ Matcher
class Matcher:
    name = "base"

    def __init__(self, cfg, params=None):
        self.cfg = cfg
        self.params = params

    def fit(self, std, cand, rids, seed):
        return {}

    def score(self, fa, fb):  # -> (p, w, gamma tuple or (), rule names)
        raise NotImplementedError

    def explain(self, fa, fb):
        raise NotImplementedError


def rule_hits(cfg, fa, fb):
    hits = []
    for r in cfg.get("rules", []):
        ok = all(fa.get(f) and fa.get(f) == fb.get(f) for f in r["fields"])
        if ok:
            hits.append((r["name"], float(r["p"])))
    return hits


class FSMatcher(Matcher):
    name = "fs"

    def _fields(self):
        return self.cfg["fields"]

    def gamma(self, fa, fb):
        return tuple(COMPARATORS[f["cmp"]][0](fa.get(f["name"], ""), fb.get(f["name"], "")) for f in self._fields())

    def fit(self, std, cand, rids, seed):
        """候補ペアに対する2成分混合(一致/非一致)の EM で m, u, λ を学習する(教師なし)。

        候補ペアはブロッキングで「一部の項目が一致」を条件に選ばれているため、ランダムペアの u を使うと全件一致へ潰れる(PoC相当の
        実験で確認)。そこで、複数項目が上位水準で一致する型を初期の「一致側」として M ステップから始め、事前カウントで平滑化する。
        得られる確率 p は『候補ペアの中で一致である確率』であり、全ペアに対する確率ではない(評価ワークベンチで実測で較正する)。
        """
        fields = self._fields()
        pats = Counter(self.gamma(std[a], std[b]) for (a, b) in cand)
        K = {f["name"]: len(COMPARATORS[f["cmp"]][1]) for f in fields}
        alpha = float(self.cfg.get("prior_count", 0.5))
        total = sum(pats.values()) or 1
        mono = bool(self.cfg.get("monotone", True))

        def m_step(resp):
            lam_ = sum(resp[g] * c for g, c in pats.items()) / total
            mm = {f["name"]: [alpha] * K[f["name"]] for f in fields}
            uu = {f["name"]: [alpha] * K[f["name"]] for f in fields}
            for g, c in pats.items():
                r = resp[g]
                for i, f in enumerate(fields):
                    if g[i] >= 0:
                        mm[f["name"]][g[i]] += r * c
                        uu[f["name"]][g[i]] += (1 - r) * c
            for f in fields:
                n = f["name"]
                sm_, su_ = sum(mm[n]), sum(uu[n])
                pm, pu = [x / sm_ for x in mm[n]], [x / su_ for x in uu[n]]
                if mono:
                    # 一致側 m だけに単調性(水準が悪いほど下がる)を課す。u は、ブロッキングで水準が偏るため制約しない。
                    pm = _pav(pm, mm[n], increasing=False)
                    pm = [x / sum(pm) for x in pm]
                mm[n] = [max(x, EPS) for x in pm]
                uu[n] = [max(x, EPS) for x in pu]
            return min(max(lam_, EPS), 1 - EPS), mm, uu

        # 初期の一致確率: 各項目の一致水準を 0〜1 に正規化した平均 s から滑らかに決める(s≤0.4 で非一致寄り、s≥0.8 で一致寄り)。
        informative = set()
        for i, f in enumerate(fields):
            c = Counter()
            for g, n in pats.items():
                if g[i] >= 0:
                    c[g[i]] += n
            if c and max(c.values()) < 0.95 * sum(c.values()):
                informative.add(i)  # ブロッキングでほぼ一定になった項目は、初期の一致判定に使わない
        resp = {}
        for g in pats:
            nn = [(i, x) for i, x in enumerate(g) if x >= 0 and (i in informative or not informative)]
            s = sum(1 - x / max(1, K[fields[i]["name"]] - 1) for i, x in nn) / len(nn) if nn else 0.0
            resp[g] = 0.05 + 0.9 * min(1.0, max(0.0, (s - 0.4) / 0.4))
        lam, m, u = m_step(resp)
        it, delta, prev_ll = 0, 1.0, None
        tol = float(self.cfg.get("ll_tol", 1e-4))
        for it in range(1, int(self.cfg.get("em_iter", 100)) + 1):
            resp, ll = {}, 0.0
            for g, c in pats.items():
                pm, pu = lam, 1 - lam
                for i, f in enumerate(fields):
                    if g[i] >= 0:
                        pm *= m[f["name"]][g[i]]
                        pu *= u[f["name"]][g[i]]
                resp[g] = pm / (pm + pu) if pm + pu > 0 else 0.0
                ll += c * math.log(max(pm + pu, 1e-300))
            nl, nm, nu = m_step(resp)
            delta = abs(nl - lam)
            for f in fields:
                n = f["name"]
                delta = max(delta, max(abs(a - b) for a, b in zip(nm[n], m[n])), max(abs(a - b) for a, b in zip(nu[n], u[n])))
            lam, m, u = nl, nm, nu
            # 尤度の改善が小さくなったら止める(尤度の尾根を長く辿ると、一致側が潰れる方向へ漂うため)
            if delta < 1e-6 or (prev_ll is not None and (ll - prev_ll) / total < tol):
                break
            prev_ll = ll
        self.params = {"lambda": lam, "m": m, "u": u, "n_candidates": len(cand), "n_patterns": len(pats), "iterations": it,
                       "converged": True, "seed": seed, "note": "p は候補ペア内での一致確率(全ペアに対する確率ではない)"}
        return self.params

    def _w(self, g):
        p = self.params
        w = math.log2(p["lambda"] / (1 - p["lambda"]))
        for i, f in enumerate(self._fields()):
            if g[i] >= 0:
                w += math.log2(p["m"][f["name"]][g[i]] / p["u"][f["name"]][g[i]])
        return w

    @staticmethod
    def _p(w):
        return 1.0 / (1.0 + 2.0 ** (-w)) if w > -60 else 0.0

    def score(self, fa, fb):
        g = self.gamma(fa, fb)
        w = self._w(g)
        return self._p(w), w, g, ()

    def explain(self, fa, fb):
        g = self.gamma(fa, fb)
        p = self.params
        rows = []
        for i, f in enumerate(self._fields()):
            labels = COMPARATORS[f["cmp"]][1]
            if g[i] < 0:
                rows.append({"field": f["name"], "a": fa.get(f["name"], ""), "b": fb.get(f["name"], ""), "level": "欠損(比較しない)", "m": None, "u": None, "bits": 0.0})
            else:
                m, u = p["m"][f["name"]][g[i]], p["u"][f["name"]][g[i]]
                rows.append({"field": f["name"], "a": fa.get(f["name"], ""), "b": fb.get(f["name"], ""), "level": labels[g[i]], "m": m, "u": u, "bits": math.log2(m / u)})
        w = self._w(g)
        return {"prior_bits": math.log2(p["lambda"] / (1 - p["lambda"])), "lambda": p["lambda"], "fields": rows, "weight": w, "p": self._p(w)}


class DeterministicMatcher(Matcher):
    name = "deterministic"

    def score(self, fa, fb):
        h = rule_hits(self.cfg, fa, fb)
        if not h:
            return 0.0, -60.0, (), ()
        p = max(x[1] for x in h)
        return p, math.log2(p / (1 - p)) if p < 1 else 60.0, (), tuple(x[0] for x in h)

    def explain(self, fa, fb):
        h = rule_hits(self.cfg, fa, fb)
        return {"rules_hit": [{"rule": n, "p": p} for n, p in h], "p": max([x[1] for x in h], default=0.0), "fields": [], "weight": None}


class HybridMatcher(FSMatcher):
    """決定的ルールに一致すれば、その確率を下限として採用する(既存キーは証拠の1つ。D4)。"""
    name = "hybrid"

    def score(self, fa, fb):
        p, w, g, _ = super().score(fa, fb)
        h = rule_hits(self.cfg, fa, fb)
        if h:
            rp = max(x[1] for x in h)
            if rp > p:
                p = rp
                w = math.log2(rp / (1 - rp)) if rp < 1 else 60.0
        return p, w, g, tuple(x[0] for x in h)

    def explain(self, fa, fb):
        e = super().explain(fa, fb)
        h = rule_hits(self.cfg, fa, fb)
        e["rules_hit"] = [{"rule": n, "p": p} for n, p in h]
        if h:
            e["p"] = max(e["p"], max(x[1] for x in h))
        return e


MATCHERS = {"fs": FSMatcher, "deterministic": DeterministicMatcher, "hybrid": HybridMatcher}


def register_matcher(name, cls):
    """プラグイン登録(FR-MAT-04)。"""
    MATCHERS[name] = cls


def make(cfg, params=None):
    return MATCHERS[cfg["matcher"]](cfg, params)


def model_hash(cfg, params):
    return sha(jdump([cfg, params]))


