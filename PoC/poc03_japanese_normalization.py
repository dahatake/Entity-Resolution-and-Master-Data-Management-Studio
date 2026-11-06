"""PoC-03: 日本語の法人名・住所の表記揺れと正規化の効果(レポート6B-3の検証)。
合成データ(正解は構成上既知)。生成した揺れに対し、正規化の段階を上げたとき再現率/適合率がどう変わるかを測る。
注意: 揺れは本スクリプトの生成器が作るため実データの分布を代表しない。示すのは「手法の限界の形」であり、実運用精度ではない。
"""
import json
import random
import re
import sys
import unicodedata
from itertools import combinations

import pandas as pd
from rapidfuzz import fuzz

from common import RESULTS, pair_metrics

sys.stdout.reconfigure(encoding="utf-8")
import os
SEED = int(os.environ.get("POC_SEED", "20261105"))
SUF = "" if SEED == 20261105 else f"_seed{SEED}"
rng = random.Random(SEED)

STEMS = ["東京", "大阪", "日本", "山田", "高橋", "渡邊", "齋藤", "髙島", "吉田", "神﨑", "中村", "國分",
         "三菱", "旭", "富士", "北斗", "青葉", "大和", "桜", "明星", "太陽", "光洋", "共栄", "新生"]
KINDS = ["電機", "商事", "製作所", "運輸", "食品", "建設", "化学", "物産", "システム", "工業"]
CITIES = [("東京都", "千代田区"), ("東京都", "港区"), ("大阪府", "大阪市北区"), ("愛知県", "名古屋市中区"),
          ("福岡県", "福岡市博多区"), ("北海道", "札幌市中央区"), ("神奈川県", "横浜市西区")]

# 異体字(生成器が使う揺れ)。normalizer_v2 は一部だけ知っている(未知の揺れを残すため)。
VARIANTS = {"高": "髙", "髙": "高", "崎": "﨑", "﨑": "崎", "邊": "邉", "邉": "邊", "齋": "斎", "斎": "齋", "國": "国", "国": "國"}
KNOWN_BY_NORMALIZER = {"髙": "高", "﨑": "崎", "邉": "邊"}  # 齋/斎・國/国 は意図的に未登録

KANSUJI = str.maketrans("0123456789", "〇一二三四五六七八九")


def make_base(i):
    stem = rng.choice(STEMS)
    kind = rng.choice(KINDS)
    pref, city = rng.choice(CITIES)
    return {
        "id": i, "name": f"{stem}{kind}", "pref": pref, "city": city,
        "chome": rng.randint(1, 9), "ban": rng.randint(1, 30), "go": rng.randint(1, 20),
    }


def render(b, system):
    name = b["name"]
    kinds = []
    # 揺れの種類は確率で適用(各システムが独自の癖を持つ)
    if rng.random() < 0.5:
        name = ("株式会社" + name) if rng.random() < 0.5 else (name + "株式会社")
    else:
        name = ("(株)" + name) if rng.random() < 0.5 else (name + "(株)")
        kinds.append("abbr")
    if rng.random() < 0.25:
        name = name.translate(str.maketrans({k: v for k, v in VARIANTS.items()}))
        kinds.append("variant_kanji")
    if rng.random() < 0.10 and len(name) > 6:
        pos = rng.randrange(1, len(name) - 1)
        name = name[:pos] + name[pos + 1:]  # 1文字欠落(誤入力)
        kinds.append("typo")
    if rng.random() < 0.3:
        name = name.replace("(株)", "（株）")
    style = rng.choice(["kanji", "hyphen", "fullwidth"])
    if style == "kanji":
        addr = f"{b['pref']}{b['city']}{b['chome']}丁目{b['ban']}番{b['go']}号"
    elif style == "hyphen":
        addr = f"{b['pref']}{b['city']}{b['chome']}-{b['ban']}-{b['go']}"
    else:
        addr = f"{b['pref']}{b['city']}{b['chome']}ー{b['ban']}ー{b['go']}".translate(
            str.maketrans("0123456789", "０１２３４５６７８９"))
    if rng.random() < 0.3:
        addr += " ○○ビル" + str(rng.randint(2, 9)) + "F"
    return {"system": system, "true_id": b["id"], "name": name, "addr": addr, "kinds": ",".join(kinds)}


def build():
    bases = [make_base(i) for i in range(150)]
    # 難しい負例: 同名・別所在地 / 同所在地・別名
    for k in range(20):
        b = dict(rng.choice(bases))
        b["id"] = 1000 + k
        pref, city = rng.choice(CITIES)
        b["pref"], b["city"] = pref, city
        bases.append(b)  # 同名別地
    for k in range(20):
        b = dict(rng.choice(bases))
        b["id"] = 2000 + k
        b["name"] = rng.choice(STEMS) + rng.choice(KINDS)
        bases.append(b)  # 同地別名
    rows = []
    for b in bases:
        for s in rng.sample(["A", "B", "C", "D"], rng.randint(2, 3)):
            rows.append(render(b, s))
    df = pd.DataFrame(rows)
    df.insert(0, "rec_id", range(len(df)))
    return df


def to_pairs(groups):
    p = set()
    for ids in groups:
        p.update(combinations(sorted(ids), 2))
    return p


# ---- 正規化 ----
SUFFIX = re.compile(r"(株式会社|\(株\)|㈱)")


def n0(s):  # 何もしない
    return s


def n1(s):  # NFKC + 空白除去 + 法人格除去
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return SUFFIX.sub("", s)


def n2(s):  # + 既知の異体字
    s = n1(s)
    return "".join(KNOWN_BY_NORMALIZER.get(c, c) for c in s)


KAN = {"〇": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def addr_norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s*○○ビル\d+F", "", s)
    s = s.replace("丁目", "-").replace("番", "-").replace("号", "").replace("ー", "-")
    return re.sub(r"\s+", "", s)


def group_by_key(df, keyfn):
    g = {}
    for rid, k in zip(df["rec_id"], keyfn(df)):
        g.setdefault(k, []).append(rid)
    return [v for v in g.values() if len(v) > 1]


df = build()
df.to_csv(RESULTS / f"poc03_synthetic_companies{SUF}.csv", index=False, encoding="utf-8-sig")
truth = to_pairs([list(g["rec_id"]) for _, g in df.groupby("true_id")])
# 同一true_idは同一システム内に複数持たない(各systemは1回): 同systemの重複はない

rows = []


def add(name, pairs):
    rows.append({"method": name, **pair_metrics(pairs, truth)})


add("R0: 生の(名称,住所)完全一致", to_pairs(group_by_key(df, lambda d: list(zip(d["name"].map(n0), d["addr"].map(n0))))))
add("R1: NFKC+法人格除去 完全一致", to_pairs(group_by_key(df, lambda d: list(zip(d["name"].map(n1), d["addr"].map(addr_norm))))))
add("R2: R1+既知の異体字 完全一致", to_pairs(group_by_key(df, lambda d: list(zip(d["name"].map(n2), d["addr"].map(addr_norm))))))


def fuzzy(df, th):
    nn = df["name"].map(n2).tolist()
    aa = df["addr"].map(addr_norm).tolist()
    ids = df["rec_id"].tolist()
    pairs = set()
    for i, j in combinations(range(len(df)), 2):
        if aa[i] != aa[j]:  # 住所正規化一致をブロッキングとして使う
            continue
        if fuzz.ratio(nn[i], nn[j]) >= th:
            pairs.add((ids[i], ids[j]))
    return pairs


for th in (95, 85, 75):
    add(f"R3: R2+住所一致ブロック内 名称ファジー(ratio>={th})", fuzzy(df, th))

res = pd.DataFrame(rows)
res.to_csv(RESULTS / f"poc03_japanese_normalization{SUF}.csv", index=False, encoding="utf-8-sig")
print("records:", len(df), "true pairs:", len(truth))
print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

# どの揺れが取りこぼしを生むか(R2で未検出の真ペアの要因)
r2 = to_pairs(group_by_key(df, lambda d: list(zip(d["name"].map(n2), d["addr"].map(addr_norm)))))
missed = truth - r2
kind_of = dict(zip(df["rec_id"], df["kinds"]))
cnt = {}
for a, b in missed:
    ks = set((kind_of[a] + "," + kind_of[b]).split(",")) - {""}
    key = "+".join(sorted(ks)) or "(揺れ記録なし:異体字未登録/その他)"
    cnt[key] = cnt.get(key, 0) + 1
print("R2の取りこぼし要因:", json.dumps(cnt, ensure_ascii=False))
(RESULTS / f"poc03_r2_missed_causes{SUF}.json").write_text(json.dumps(cnt, ensure_ascii=False, indent=2), encoding="utf-8")


fp = sorted(r2 - truth)
print("R2の誤統合ペア数:", len(fp))
fp_rows = [{"a": df.loc[a, "name"], "a_true": int(df.loc[a, "true_id"]), "b": df.loc[b, "name"],
            "b_true": int(df.loc[b, "true_id"]), "addr": df.loc[a, "addr"]} for a, b in fp]
pd.DataFrame(fp_rows).to_csv(RESULTS / f"poc03_r2_false_merges{SUF}.csv", index=False, encoding="utf-8-sig")
for r in fp_rows[:5]:
    print(r)

