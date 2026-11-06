"""設定の保存(標準化の版管理、モデル、パイプライン設定)。"""
import json

from . import audit, matching, pipeline, standardize
from .db import get_setting, set_setting
from .util import ApiError, jdump, need, now, sha


def save_std(conn, cfg, actor, note="", activate=True):
    standardize.validate(cfg)
    q = set()
    for s in conn.execute("SELECT quarantine FROM sources"):
        q.update(json.loads(s["quarantine"] or "[]"))
    bad = sorted(standardize.referenced_columns(cfg) & q)
    need(not bad, 400, f"個人番号として隔離された列は照合に使えません: {bad}")
    h = standardize.config_hash(cfg)
    ex = conn.execute("SELECT version FROM std_configs WHERE hash=?", (h,)).fetchone()
    if ex:
        ver, created = ex["version"], False
    else:
        cur = conn.execute("INSERT INTO std_configs(config,hash,created,created_by,note) VALUES(?,?,?,?,?)", (jdump(cfg), h, now(), actor, note))
        ver, created = cur.lastrowid, True
    if activate:
        set_setting(conn, "active_std_version", ver, actor)
    audit.append(conn, actor, "std.save", str(ver), {"hash": h[:12], "created": created, "activated": activate, "note": note})
    conn.commit()
    return {"version": ver, "created": created, "hash": h}


def save_pipeline(conn, patch, actor):
    cfg = pipeline.pipeline_config(conn)
    for k, v in patch.items():
        if k not in pipeline.DEFAULT_PIPELINE:
            raise ApiError(400, f"未知の設定項目: {k}")
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    pipeline.validate_pipeline(cfg)
    set_setting(conn, "pipeline", cfg, actor)
    audit.append(conn, actor, "pipeline.save", "", {"patch": patch})
    conn.commit()
    return cfg


def create_model(conn, name, cfg, actor):
    matching.validate_model_config(cfg)
    cur = conn.execute("INSERT INTO models(name,matcher,config,created) VALUES(?,?,?,?)", (name, cfg["matcher"], jdump(cfg), now()))
    audit.append(conn, actor, "model.create", str(cur.lastrowid), {"name": name, "matcher": cfg["matcher"]})
    conn.commit()
    return cur.lastrowid


def activate_model(conn, model_id, actor):
    m = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    need(m, 404, "モデルがありません")
    need(m["params"] or m["matcher"] == "deterministic", 409, "未学習のモデルは有効化できません")
    set_setting(conn, "active_model", model_id, actor)
    audit.append(conn, actor, "model.activate", str(model_id), {})
    conn.commit()
