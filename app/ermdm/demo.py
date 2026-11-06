"""デモ用の事前設定(同梱の合成データ)。fake_1000 は Splink 公開のデモデータ、companies_* は PoC-03 の日本語企業の合成データ。いずれも正解列付き。"""
from pathlib import Path

from . import audit, config_ops, governance, ingest
from .db import set_setting
from .util import ApiError, need

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"

PERSON_STD = {"label": "persons-v1", "fields": [
    {"name": "first_name", "ops": ["nfkc", "lower", "ws"], "sources": {"*": ["first_name"]}},
    {"name": "surname", "ops": ["nfkc", "lower", "ws"], "sources": {"*": ["surname"]}},
    {"name": "dob", "ops": ["date"], "sources": {"*": ["dob"]}},
    {"name": "city", "ops": ["nfkc", "lower", "ws"], "sources": {"*": ["city"]}},
    {"name": "email", "ops": ["email"], "sources": {"*": ["email"]}},
]}
PERSON_BLOCK = [
    {"name": "名+生年月日", "keys": [{"field": "first_name"}, {"field": "dob"}]},
    {"name": "姓", "keys": [{"field": "surname"}]},
    {"name": "生年月日", "keys": [{"field": "dob"}]},
    {"name": "メール", "keys": [{"field": "email"}]},
]
PERSON_MODEL = {"matcher": "fs", "fields": [{"name": "first_name", "cmp": "name"}, {"name": "surname", "cmp": "name"}, {"name": "dob", "cmp": "date"},
                                            {"name": "city", "cmp": "exact"}, {"name": "email", "cmp": "email"}]}

COMPANY_STD = {"label": "companies-v1", "fields": [
    {"name": "name", "ops": ["nfkc", "lower", "corp", "variant", "punct", "nospace"], "sources": {"*": ["name"]}},
    {"name": "addr", "ops": ["addr"], "sources": {"*": ["addr"]}},
    {"name": "town", "ops": ["addr", "addr_town"], "sources": {"*": ["addr"]}},
    {"name": "num", "ops": ["addr", "addr_num"], "sources": {"*": ["addr"]}},
]}
COMPANY_BLOCK = [
    {"name": "住所(正規化)一致", "keys": [{"field": "addr"}]},
    {"name": "名称先頭3+住所先頭6", "keys": [{"field": "name", "t": "prefix", "n": 3}, {"field": "addr", "t": "prefix", "n": 6}]},
]
COMPANY_MODEL = {"matcher": "fs", "fields": [{"name": "name", "cmp": "text"}, {"name": "town", "cmp": "text"}, {"name": "num", "cmp": "exact"}]}


def _csv(path):
    cols, rows = ingest.parse_csv(ingest.decode_bytes(path.read_bytes()))
    return cols, rows


def _source(conn, actor, name, key, truth, personal, purpose_id=None):
    cur = conn.execute("INSERT INTO sources(name,kind,uri,key_column,personal,purpose_id,truth_column,created) VALUES(?,?,?,?,?,?,?,datetime('now'))",
                       (name, "csv", name, key, 1 if personal else 0, purpose_id, truth))
    return conn.execute("SELECT * FROM sources WHERE id=?", (cur.lastrowid,)).fetchone()


def load_demo(conn, name, actor):
    need(not conn.execute("SELECT 1 FROM sources LIMIT 1").fetchone(), 409, "デモは空のワークスペースにだけ読み込めます")
    if name == "persons":
        pid = governance.create_purpose(conn, actor, {
            "name": "デモ: 顧客名寄せ(合成データ)", "purpose": "合成データによる名寄せ機能の検証。実在の個人データではない。",
            "data_items": ["氏名", "生年月日", "市区町村", "メールアドレス"], "user_scope": "本ワークスペースの利用者のみ", "manager": "デモ管理者",
            "notice_method": "デモ用のため通知なし(実運用では公表または通知が必要)"})
        src = _source(conn, actor, "fake1000", "unique_id", "cluster", True, pid)
        cols, rows = _csv(SAMPLE / "fake_1000.csv")
        res = [ingest.load(conn, src, cols, rows, actor)]
        std, block, model = PERSON_STD, PERSON_BLOCK, PERSON_MODEL
    elif name == "companies":
        res = []
        for s in "ABCD":
            src = _source(conn, actor, f"company_{s}", "rec_id", "true_id", False)
            cols, rows = _csv(SAMPLE / f"companies_{s}.csv")
            res.append(ingest.load(conn, src, cols, rows, actor))
        std, block, model = COMPANY_STD, COMPANY_BLOCK, COMPANY_MODEL
    else:
        raise ApiError(400, "デモ名は persons または companies")
    config_ops.save_std(conn, std, actor, note=f"demo:{name}")
    thr = {"auto": 0.9, "review_low": 0.5} if name == "persons" else {"auto": 0.5, "review_low": 0.2}
    config_ops.save_pipeline(conn, {"blocking": {"rules": block}, "thresholds": thr, "seed": 1}, actor)
    set_setting(conn, "identity_definition", {"text": f"(デモ)同じ{'人物' if name == 'persons' else '法人(本店所在地)'}を指す記録を同一とする。Phase 0 で業務側が正式に定義・承認すること。",
                                              "version": 0, "approved_by": None}, actor)
    set_setting(conn, "cost_ratio", {"c": 5.0, "note": "仮の既定値(Phase 0 の V2 で業務ごとに決定)"}, actor)
    mid = config_ops.create_model(conn, f"demo-{name}", model, actor)
    from . import pipeline
    pipeline.train_model(conn, mid, actor)
    config_ops.activate_model(conn, mid, actor)
    audit.append(conn, actor, "demo.load", name, {"loads": [{k: r[k] for k in ("insert", "update", "delete", "same")} for r in res]})
    conn.commit()
    return {"demo": name, "loads": res, "model_id": mid}
