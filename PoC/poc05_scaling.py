"""PoC-05: 規模に対する所要時間の傾向(Splink公式の「100万件を約1分/ノートPC」主張の、小規模での傾向確認)。
合成データ(正解は構成上既知)。ここでの実測は、この環境(x64エミュレーション上のARM64 Windows)での値であり、
公式主張(100万件)の再現ではない。n=1万〜10万の傾向のみ。
"""
import platform
import sys
import random
import time

import pandas as pd
from splink import DuckDBAPI, Linker, SettingsCreator, block_on
import splink.comparison_library as cl

from common import RESULTS, load_fake_1000, pair_metrics, true_pairs

base = load_fake_1000()
firsts = base["first_name"].dropna().unique().tolist()
surs = base["surname"].dropna().unique().tolist()
rng = random.Random(11)


def typo(s):
    if not s or len(s) < 3 or rng.random() > 0.3:
        return s
    i = rng.randrange(len(s) - 1)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def make(n_entities):
    rows, uid = [], 0
    for e in range(n_entities):
        f, s = rng.choice(firsts), rng.choice(surs)
        dob = f"{rng.randint(1940, 2005)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        email = f"{f.lower()}{rng.randint(1, 99999)}@mail.test"
        for k in range(2 if rng.random() < 0.3 else 1):
            rows.append({"unique_id": uid, "first_name": typo(f), "surname": typo(s), "dob": dob,
                         "city": rng.choice(["London", "Leeds", "Bath", None]), "email": email if rng.random() < .7 else None,
                         "cluster": e})
            uid += 1
    return pd.DataFrame(rows)


out = []
SIZES = [int(x) for x in sys.argv[1:]] or [7_000, 20_000, 70_000]
for n_ent in SIZES:
    df = make(n_ent)
    truth = true_pairs(df)
    t0 = time.time()
    db = DuckDBAPI()
    settings = SettingsCreator(
        link_type="dedupe_only",
        comparisons=[cl.NameComparison("first_name"), cl.NameComparison("surname"),
                     cl.DateOfBirthComparison("dob", input_is_string=True), cl.ExactMatch("city"),
                     cl.EmailComparison("email")],
        blocking_rules_to_generate_predictions=[block_on("surname", "dob"), block_on("first_name", "dob"), block_on("email")],
    )
    linker = Linker(db.register(df), settings)
    linker.training.estimate_probability_two_random_records_match([block_on("first_name", "surname", "dob")], recall=0.7)
    linker.training.estimate_u_using_random_sampling(max_pairs=1_000_000, seed=1)
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("first_name", "surname"))
    linker.training.estimate_parameters_using_expectation_maximisation(block_on("dob"))
    t_train = time.time() - t0
    t1 = time.time()
    pred = linker.inference.predict(threshold_match_probability=0.5).as_pandas_dataframe()
    t_pred = time.time() - t1
    sel = {tuple(sorted(p)) for p in zip(pred["unique_id_l"], pred["unique_id_r"])}
    out.append({"records": len(df), "train_sec": round(t_train, 1), "predict_sec": round(t_pred, 1),
                "all_pairs": len(df) * (len(df) - 1) // 2, **pair_metrics(sel, truth)})
    print(out[-1], flush=True)

res = pd.DataFrame(out)
res.to_csv(RESULTS / ("poc05_scaling.csv" if len(sys.argv) == 1 else "poc09_scaling_1M.csv"), index=False)
print(platform.platform(), platform.machine())

