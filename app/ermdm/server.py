"""ローカルHTTPサーバー(標準ライブラリのみ)。127.0.0.1 限定、Host/Origin 検査、セッションCookie、役割ベースの権限。"""
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (audit, auth, blocking, clustering, config_ops, db, demo, evaluation, governance, ingest, matching, pipeline, review,
               standardize, tasks, xref)
from .util import ApiError, jdump, need, now, sha

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 120 * 1024 * 1024
ROUTES = []


class Resp:
    def __init__(self, body, status=200, ctype="application/json; charset=utf-8", headers=None):
        self.body, self.status, self.ctype, self.headers = body, status, ctype, headers or {}


class Ctx:
    def __init__(self, app, conn, user, body, q, m, token):
        self.app, self.conn, self.user, self.body, self.q, self.m, self.token = app, conn, user, body, q, m, token

    def need_perm(self, perm):
        auth.require(self.user, perm)

    def qi(self, name, default=None, lo=None, hi=None):
        v = self.q.get(name)
        if v in (None, ""):
            return default
        try:
            x = int(v)
        except ValueError:
            raise ApiError(400, f"{name} は整数で指定してください")
        if lo is not None:
            x = max(lo, x)
        if hi is not None:
            x = min(hi, x)
        return x

    def qf(self, name, default=None):
        v = self.q.get(name)
        if v in (None, ""):
            return default
        try:
            return float(v)
        except ValueError:
            raise ApiError(400, f"{name} は数値で指定してください")


def route(method, pattern, perm=None, public=False):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn, perm, public))
        return fn
    return deco


# ------------------------------------------------------------------ 共通
def latest_run(conn, rid=None):
    if rid:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        need(r, 404, "実行がありません")
        return r
    r = conn.execute("SELECT * FROM runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
    return r


def mask(user, d):
    return d if auth.can(user, "view_raw") else {k: "●●●" for k in d}


def rec_view(conn, user, r, std=None):
    d = {"rid": r["rid"], "source": r["sname"], "key": r["skey"], "status": r["status"], "raw": mask(user, json.loads(r["raw"]))}
    if std is not None:
        d["std"] = mask(user, std)
    if auth.can(user, "view_raw"):
        d["truth"] = r["truth"]
    d["gid"] = r["gid"] if "gid" in r.keys() else None
    return d


def records_by_rids(conn, rids):
    if not rids:
        return []
    q = ",".join("?" * len(rids))
    return conn.execute(f"SELECT r.*, s.name sname, x.gid FROM records r JOIN sources s ON s.id=r.source_id LEFT JOIN xref x ON x.rid=r.rid WHERE r.rid IN ({q}) ORDER BY r.rid", list(rids)).fetchall()


def page(ctx, default=50):
    size = ctx.qi("size", default, 1, 500)
    pg = ctx.qi("page", 1, 1)
    return size, (pg - 1) * size


def json_body(ctx, required=()):
    need(isinstance(ctx.body, dict), 400, "JSON オブジェクトを送ってください")
    for k in required:
        need(k in ctx.body and ctx.body[k] not in (None, ""), 400, f"{k} は必須です")
    return ctx.body


# ------------------------------------------------------------------ 認証
@route("POST", "/api/login", public=True)
def api_login(ctx):
    b = json_body(ctx, ["username", "password"])
    token, user = auth.login(ctx.conn, str(b["username"]), str(b["password"]))
    audit.append(ctx.conn, user["username"], "auth.login", "", {})
    ctx.conn.commit()
    return Resp(json.dumps({"user": public_user(user)}, ensure_ascii=False),
                headers={"Set-Cookie": f"sid={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"})


def public_user(u):
    return {"username": u["username"], "display": u["display"], "roles": u["roles"], "perms": sorted(u["perms"]),
            "role_labels": [auth.ROLE_LABELS.get(r, r) for r in u["roles"]]}


@route("POST", "/api/logout")
def api_logout(ctx):
    auth.logout(ctx.conn, ctx.token)
    return Resp(json.dumps({"ok": True}), headers={"Set-Cookie": "sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})


@route("GET", "/api/me")
def api_me(ctx):
    return {"user": public_user(ctx.user)}


# ------------------------------------------------------------------ ダッシュボード・設定
@route("GET", "/api/status", perm="read")
def api_status(ctx):
    c = ctx.conn
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
    run = latest_run(c)
    last = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    ok, n, bad = audit.verify(c)
    viol, info = governance.run_violations(c)
    ver, _ = pipeline.active_std(c)
    pc = pipeline.pipeline_config(c)
    return {
        "records": {"active": one("SELECT COUNT(*) FROM records WHERE status='active'"), "deleted": one("SELECT COUNT(*) FROM records WHERE status='deleted'")},
        "sources": one("SELECT COUNT(*) FROM sources"), "entities": one("SELECT COUNT(DISTINCT gid) FROM xref"),
        "multi_entities": one("SELECT COUNT(*) FROM (SELECT gid FROM xref GROUP BY gid HAVING COUNT(*)>1)"),
        "review_open": one("SELECT COUNT(*) FROM review_items WHERE status='open'"),
        "review_overdue": one("SELECT COUNT(*) FROM review_items r JOIN tasks t ON t.id=r.task_id WHERE r.status='open' AND t.due<?", now()),
        "tasks_open": one("SELECT COUNT(*) FROM tasks WHERE status!='done'"),
        "constraints": {"cannot": one("SELECT COUNT(*) FROM constraints WHERE kind='cannot'"), "must": one("SELECT COUNT(*) FROM constraints WHERE kind='must'")},
        "last_run": run_summary(last) if last else None,
        "last_done_run": run_summary(run) if run else None,
        "audit": {"ok": ok, "entries": n, "first_bad": bad},
        "governance": {"violations": viol, "notes": info},
        "settings": {"identity_definition": db.get_setting(c, "identity_definition"), "cost_ratio": db.get_setting(c, "cost_ratio"), "pipeline": pc,
                     "active_std_version": ver, "active_model": db.get_setting(c, "active_model")},
        "phase0_open": phase0_open(c),
    }


def phase0_open(c):
    """Phase 0 ゲート(PRD 9章)のうち、アプリ上で未入力のもの。仮の既定値のまま運用しないための表示。"""
    out = []
    idf = db.get_setting(c, "identity_definition")
    if not idf or not idf.get("approved_by"):
        out.append("同一性の定義が、データオーナーによって承認されていません(V1)")
    cr = db.get_setting(c, "cost_ratio")
    if not cr or not cr.get("approved_by"):
        out.append("誤統合/誤分割のコスト比が、業務側で承認されていません(V2)。現在は仮の既定値です")
    if not c.execute("SELECT 1 FROM label_samples_meta LIMIT 1").fetchone() and not evaluation.gold_available(c):
        out.append("ラベル付きサンプルがありません(V4)。閾値は実測で決まっていません")
    if not c.execute("SELECT 1 FROM purposes WHERE active=1 LIMIT 1").fetchone() and governance.personal_sources(c):
        out.append("個人データの利用目的(共同利用)が未登録です(V7)")
    return out


def run_summary(r):
    d = {k: r[k] for k in ("id", "ts", "actor", "status", "mode", "result_hash", "error", "finished", "seed", "std_version", "model_id")}
    d["metrics"] = json.loads(r["metrics"]) if r["metrics"] else None
    return d


@route("GET", "/api/settings", perm="read")
def api_settings_get(ctx):
    c = ctx.conn
    return {"identity_definition": db.get_setting(c, "identity_definition"), "cost_ratio": db.get_setting(c, "cost_ratio"),
            "minutes_per_review": db.get_setting(c, "minutes_per_review", 2.0), "review_sla_hours": db.get_setting(c, "review_sla_hours", 72)}


@route("PUT", "/api/settings", perm="settings")
def api_settings_put(ctx):
    b = json_body(ctx)
    c = ctx.conn
    u = ctx.user["username"]
    if "identity_definition" in b:
        cur = db.get_setting(c, "identity_definition") or {"version": 0}
        text = str(b["identity_definition"].get("text", "")).strip()
        need(len(text) >= 10, 400, "同一性の定義は10文字以上で、粒度(例: 法人/事業所)と時間(社名変更の扱い)を明記してください")
        new = {"text": text, "version": cur.get("version", 0) + 1, "approved_by": u if b["identity_definition"].get("approve") else None,
               "approved_at": now() if b["identity_definition"].get("approve") else None}
        db.set_setting(c, "identity_definition", new, u)
        audit.append(c, u, "settings.identity_definition", str(new["version"]), {"approved": bool(new["approved_by"]), "text": text})
    if "cost_ratio" in b:
        x = float(b["cost_ratio"].get("c", 0))
        need(x > 0, 400, "コスト比は正の数です")
        new = {"c": x, "note": b["cost_ratio"].get("note", ""), "approved_by": u if b["cost_ratio"].get("approve") else None}
        db.set_setting(c, "cost_ratio", new, u)
        audit.append(c, u, "settings.cost_ratio", "", new)
    for k in ("minutes_per_review", "review_sla_hours"):
        if k in b:
            need(float(b[k]) > 0, 400, f"{k} は正の数です")
            db.set_setting(c, k, float(b[k]), u)
            audit.append(c, u, f"settings.{k}", "", {"value": float(b[k])})
    c.commit()
    return api_settings_get(ctx)


@route("GET", "/api/pipeline", perm="read")
def api_pipeline_get(ctx):
    return pipeline.pipeline_config(ctx.conn)


@route("PUT", "/api/pipeline", perm="config")
def api_pipeline_put(ctx):
    return config_ops.save_pipeline(ctx.conn, json_body(ctx), ctx.user["username"])


@route("PUT", "/api/thresholds", perm="settings")
def api_thresholds_put(ctx):
    b = json_body(ctx, ["auto", "review_low"])
    return config_ops.save_pipeline(ctx.conn, {"thresholds": {"auto": float(b["auto"]), "review_low": float(b["review_low"])}}, ctx.user["username"])


@route("POST", "/api/blocking/estimate", perm="config")
def api_blocking_estimate(ctx):
    b = ctx.body or {}
    pc = pipeline.pipeline_config(ctx.conn)
    rules = b.get("rules") or pc["blocking"]["rules"]
    need(rules, 409, "ブロッキングルールがありません")
    blocking.validate(rules)
    ver, cfg = pipeline.active_std(ctx.conn)
    need(cfg, 409, "有効な標準化設定がありません")
    std, source_of, truth, _ = pipeline.load_active(ctx.conn, ver, cfg)
    mb = int(b.get("max_block", pc["blocking"]["max_block"]))
    est = blocking.estimate(rules, std, mb, source_of, b.get("link_type", pc["blocking"]["link_type"]))
    n = len(std)
    est["total_pairs"] = n * (n - 1) // 2
    est["max_candidates"] = int(b.get("max_candidates", pc["blocking"]["max_candidates"]))
    est["over_limit"] = est["total_upper"] > est["max_candidates"]
    if truth:
        cand, _ = blocking.generate(rules, std, mb, max(est["total_upper"], 1), source_of, b.get("link_type", pc["blocking"]["link_type"]))
        from collections import Counter
        g = Counter(truth.values())
        true_total = sum(c * (c - 1) // 2 for c in g.values())
        tp = sum(1 for (a, bb) in cand if truth.get(a) and truth.get(a) == truth.get(bb))
        est["gold"] = {"true_pairs": true_total, "in_candidates": tp, "pairs_completeness": tp / true_total if true_total else None, "candidates": len(cand)}
    est["reduction_ratio_upper"] = 1 - est["total_upper"] / est["total_pairs"] if est["total_pairs"] else None
    return est


# ------------------------------------------------------------------ ソース・取り込み
def source_dict(c, s):
    d = dict(s)
    d["columns"] = json.loads(d["columns"] or "[]")
    d["quarantine"] = json.loads(d["quarantine"] or "[]")
    d["pending_schema"] = json.loads(d["pending_schema"]) if d["pending_schema"] else None
    d["records"] = c.execute("SELECT COUNT(*) FROM records WHERE source_id=? AND status='active'", (s["id"],)).fetchone()[0]
    d["deleted"] = c.execute("SELECT COUNT(*) FROM records WHERE source_id=? AND status='deleted'", (s["id"],)).fetchone()[0]
    last = c.execute("SELECT * FROM loads WHERE source_id=? ORDER BY id DESC LIMIT 1", (s["id"],)).fetchone()
    d["last_load"] = dict(last) if last else None
    p = c.execute("SELECT name FROM purposes WHERE id=?", (s["purpose_id"],)).fetchone() if s["purpose_id"] else None
    d["purpose_name"] = p["name"] if p else None
    return d


@route("GET", "/api/sources", perm="read")
def api_sources(ctx):
    return [source_dict(ctx.conn, s) for s in ctx.conn.execute("SELECT * FROM sources ORDER BY id")]


@route("POST", "/api/sources", perm="ingest")
def api_source_create(ctx):
    b = json_body(ctx, ["name", "key_column"])
    c = ctx.conn
    need(re.fullmatch(r"[\w\-.ぁ-んァ-ヶ一-龠]{1,60}", b["name"]), 400, "ソース名は60文字以内の英数字・日本語・-_. です")
    need(not c.execute("SELECT 1 FROM sources WHERE name=?", (b["name"],)).fetchone(), 409, "同名のソースがあります")
    pid = b.get("purpose_id")
    if pid:
        need(c.execute("SELECT 1 FROM purposes WHERE id=? AND active=1", (pid,)).fetchone(), 400, "利用目的の登録がありません")
    cur = c.execute("INSERT INTO sources(name,kind,uri,key_column,personal,pseudonymized,purpose_id,truth_column,created) VALUES(?,?,?,?,?,?,?,?,?)",
                    (b["name"], b.get("kind", "csv"), b.get("uri"), b["key_column"], 1 if b.get("personal") else 0, 1 if b.get("pseudonymized") else 0, pid, b.get("truth_column") or None, now()))
    audit.append(c, ctx.user["username"], "source.create", b["name"], {"personal": bool(b.get("personal")), "pseudonymized": bool(b.get("pseudonymized"))})
    c.commit()
    return source_dict(c, c.execute("SELECT * FROM sources WHERE id=?", (cur.lastrowid,)).fetchone())


def get_source(ctx):
    s = ctx.conn.execute("SELECT * FROM sources WHERE id=?", (int(ctx.m[0]),)).fetchone()
    need(s, 404, "ソースがありません")
    return s


@route("PUT", r"/api/sources/(\d+)", perm="ingest")
def api_source_update(ctx):
    s = get_source(ctx)
    b = json_body(ctx)
    c = ctx.conn
    if "purpose_id" in b and b["purpose_id"]:
        need(c.execute("SELECT 1 FROM purposes WHERE id=? AND active=1", (b["purpose_id"],)).fetchone(), 400, "利用目的の登録がありません")
    for k, col in (("personal", "personal"), ("pseudonymized", "pseudonymized"), ("purpose_id", "purpose_id")):
        if k in b:
            v = b[k]
            c.execute(f"UPDATE sources SET {col}=? WHERE id=?", ((1 if v else 0) if k != "purpose_id" else (v or None), s["id"]))
    audit.append(c, ctx.user["username"], "source.update", s["name"], {k: b[k] for k in ("personal", "pseudonymized", "purpose_id") if k in b})
    c.commit()
    return source_dict(c, c.execute("SELECT * FROM sources WHERE id=?", (s["id"],)).fetchone())


@route("POST", r"/api/sources/(\d+)/load", perm="ingest")
def api_source_load(ctx):
    s = get_source(ctx)
    b = json_body(ctx)
    mode = b.get("mode", "snapshot")
    need(mode in ("snapshot", "delta"), 400, "mode は snapshot または delta")
    if b.get("csv_text") is not None:
        cols, rows = ingest.parse_csv(str(b["csv_text"]))
    elif b.get("import_path"):
        p = ingest.safe_import_path(ctx.app.import_dir, str(b["import_path"]))
        if p.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            cols, rows = ingest.read_sqlite(p, str(b.get("table", "")))
        else:
            cols, rows = ingest.parse_csv(ingest.decode_bytes(p.read_bytes()))
    else:
        raise ApiError(400, "csv_text または import_path を指定してください")
    return ingest.load(ctx.conn, s, cols, rows, ctx.user["username"], mode)


@route("POST", r"/api/sources/(\d+)/ack-schema", perm="ingest")
def api_source_ack(ctx):
    ingest.ack_schema(ctx.conn, get_source(ctx), ctx.user["username"])
    return {"ok": True}


@route("GET", r"/api/sources/(\d+)/loads", perm="read")
def api_source_loads(ctx):
    s = get_source(ctx)
    return [dict(r) for r in ctx.conn.execute("SELECT * FROM loads WHERE source_id=? ORDER BY id DESC LIMIT 100", (s["id"],))]


@route("GET", "/api/import-files", perm="ingest")
def api_import_files(ctx):
    base = ctx.app.import_dir
    return sorted(str(p.relative_to(base)).replace("\\", "/") for p in base.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".db", ".sqlite", ".sqlite3"))[:500]


@route("GET", "/api/records", perm="read")
def api_records(ctx):
    size, off = page(ctx, 50)
    sql, args = "FROM records r JOIN sources s ON s.id=r.source_id LEFT JOIN xref x ON x.rid=r.rid WHERE 1=1", []
    if ctx.qi("source_id"):
        sql += " AND r.source_id=?"
        args.append(ctx.qi("source_id"))
    if ctx.q.get("status"):
        sql += " AND r.status=?"
        args.append(ctx.q["status"])
    if ctx.q.get("q"):
        sql += " AND (r.skey LIKE ? OR r.raw LIKE ?)"
        like = "%" + ctx.q["q"].replace("%", "") + "%"
        args += [like, like]
    total = ctx.conn.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]
    rows = ctx.conn.execute("SELECT r.*, s.name sname, x.gid " + sql + " ORDER BY r.rid LIMIT ? OFFSET ?", args + [size, off]).fetchall()
    return {"total": total, "items": [rec_view(ctx.conn, ctx.user, r) for r in rows]}


@route("POST", "/api/demo", perm="admin")
def api_demo(ctx):
    b = json_body(ctx, ["name"])
    return demo.load_demo(ctx.conn, b["name"], ctx.user["username"])


# ------------------------------------------------------------------ 標準化・モデル
@route("GET", "/api/std", perm="read")
def api_std_list(ctx):
    active = db.get_setting(ctx.conn, "active_std_version")
    rows = [dict(r) for r in ctx.conn.execute("SELECT version,hash,created,created_by,note FROM std_configs ORDER BY version DESC")]
    for r in rows:
        r["active"] = r["version"] == active
    ver, cfg = pipeline.active_std(ctx.conn)
    return {"versions": rows, "active": ver, "config": cfg, "ops": sorted(standardize.OPS), "default_corp_forms": standardize.DEFAULT_CORP, "default_variants": standardize.DEFAULT_VARIANTS}


@route("POST", "/api/std", perm="config")
def api_std_save(ctx):
    b = json_body(ctx, ["config"])
    return config_ops.save_std(ctx.conn, b["config"], ctx.user["username"], b.get("note", ""), b.get("activate", True))


@route("POST", r"/api/std/(\d+)/activate", perm="config")
def api_std_activate(ctx):
    v = int(ctx.m[0])
    need(ctx.conn.execute("SELECT 1 FROM std_configs WHERE version=?", (v,)).fetchone(), 404, "版がありません")
    db.set_setting(ctx.conn, "active_std_version", v, ctx.user["username"])
    audit.append(ctx.conn, ctx.user["username"], "std.activate", str(v), {})
    ctx.conn.commit()
    return {"active": v}


@route("POST", "/api/std/preview", perm="config")
def api_std_preview(ctx):
    b = json_body(ctx, ["config"])
    standardize.validate(b["config"])
    limit = min(int(b.get("limit", 20)), 100)
    rows = ctx.conn.execute("SELECT r.*, s.name sname, NULL gid FROM records r JOIN sources s ON s.id=r.source_id WHERE r.status='active' ORDER BY r.rid LIMIT ?", (limit,)).fetchall()
    return [rec_view(ctx.conn, ctx.user, r, standardize.apply(b["config"], r["sname"], json.loads(r["raw"]))) for r in rows]


def model_dict(r, full=False):
    d = {k: r[k] for k in ("id", "name", "matcher", "hash", "std_version", "seed", "trained", "trained_by", "created")}
    d["config"] = json.loads(r["config"])
    d["params"] = json.loads(r["params"]) if r["params"] else None
    d["stats"] = json.loads(r["stats"]) if r["stats"] else None
    d["comparators"] = {k: v[1] for k, v in matching.COMPARATORS.items()} if full else None
    return d


@route("GET", "/api/models", perm="read")
def api_models(ctx):
    active = db.get_setting(ctx.conn, "active_model")
    out = []
    for r in ctx.conn.execute("SELECT * FROM models ORDER BY id DESC"):
        d = model_dict(r)
        d["active"] = d["id"] == active
        out.append(d)
    return {"models": out, "matchers": sorted(matching.MATCHERS), "comparators": {k: v[1] for k, v in matching.COMPARATORS.items()}}


@route("POST", "/api/models", perm="config")
def api_model_create(ctx):
    b = json_body(ctx, ["name", "config"])
    return {"id": config_ops.create_model(ctx.conn, b["name"], b["config"], ctx.user["username"])}


@route("POST", r"/api/models/(\d+)/train", perm="config")
def api_model_train(ctx):
    return pipeline.train_model(ctx.conn, int(ctx.m[0]), ctx.user["username"])


@route("POST", r"/api/models/(\d+)/activate", perm="config")
def api_model_activate(ctx):
    config_ops.activate_model(ctx.conn, int(ctx.m[0]), ctx.user["username"])
    return {"active": int(ctx.m[0])}


# ------------------------------------------------------------------ 実行
@route("GET", "/api/runs", perm="read")
def api_runs(ctx):
    return [run_summary(r) for r in ctx.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 100")]


@route("POST", "/api/runs", perm="run")
def api_run_start(ctx):
    b = ctx.body or {}
    rid = pipeline.create_run(ctx.conn, ctx.user["username"], b.get("mode", "full"), b.get("recluster_from"), b.get("overrides"))
    ctx.app.start_run(rid)
    return {"run_id": rid, "status": "queued"}


@route("GET", r"/api/runs/(\d+)", perm="read")
def api_run_get(ctx):
    r = latest_run(ctx.conn, int(ctx.m[0]))
    d = run_summary(r)
    d["config"] = json.loads(r["config"])
    d["data_hash"] = r["data_hash"]
    d["config_hash"] = r["config_hash"]
    d["code_version"] = r["code_version"]
    return d


@route("GET", r"/api/runs/(\d+)/pairs", perm="read")
def api_run_pairs(ctx):
    rid = int(ctx.m[0])
    size, off = page(ctx, 50)
    lo, hi = ctx.qf("min_p", 0.0), ctx.qf("max_p", 1.0000001)
    total = ctx.conn.execute("SELECT COUNT(*) FROM pairs WHERE run_id=? AND p>=? AND p<?", (rid, lo, hi)).fetchone()[0]
    rows = ctx.conn.execute("SELECT a,b,p,w,rules FROM pairs WHERE run_id=? AND p>=? AND p<? ORDER BY p DESC, a, b LIMIT ? OFFSET ?", (rid, lo, hi, size, off)).fetchall()
    return {"total": total, "items": [{"a": r["a"], "b": r["b"], "p": r["p"], "w": r["w"], "rules": json.loads(r["rules"])} for r in rows]}


def explain_pair(conn, user, run, a, b):
    cfg = json.loads(run["config"])
    m = conn.execute("SELECT * FROM models WHERE id=?", (cfg["model_id"],)).fetchone() if cfg.get("model_id") else None
    need(m, 409, "この実行のモデルが見つかりません")
    mt = matching.make(json.loads(m["config"]), json.loads(m["params"]) if m["params"] else None)
    row = conn.execute("SELECT config FROM std_configs WHERE version=?", (cfg["std_version"],)).fetchone()
    scfg = json.loads(row["config"])
    recs = records_by_rids(conn, [a, b])
    need(len(recs) == 2, 404, "レコードがありません")
    std = {r["rid"]: standardize.apply(scfg, r["sname"], json.loads(r["raw"])) for r in recs}
    stored = conn.execute("SELECT p,rules FROM pairs WHERE run_id=? AND a=? AND b=?", (run["id"], min(a, b), max(a, b))).fetchone()
    e = mt.explain(std[min(a, b)], std[max(a, b)])
    if not auth.can(user, "view_raw"):
        for f in e.get("fields", []):
            f["a"], f["b"] = "●●●", "●●●"
    return {"records": [rec_view(conn, user, r, std[r["rid"]]) for r in recs], "explanation": e, "stored_p": stored["p"] if stored else None,
            "blocking_rules": json.loads(stored["rules"]) if stored else [], "model": {"id": m["id"], "name": m["name"], "hash": m["hash"]}}


@route("GET", "/api/explain", perm="read")
def api_explain(ctx):
    run = latest_run(ctx.conn, ctx.qi("run"))
    need(run, 409, "完了した実行がありません")
    a, b = ctx.qi("a"), ctx.qi("b")
    need(a and b, 400, "a と b(rid)を指定してください")
    return explain_pair(ctx.conn, ctx.user, run, a, b)


@route("GET", r"/api/runs/(\d+)/compare/(\d+)", perm="read")
def api_run_compare(ctx):
    x, y = latest_run(ctx.conn, int(ctx.m[0])), latest_run(ctx.conn, int(ctx.m[1]))
    return {"a": x["id"], "b": y["id"], "same_result_hash": x["result_hash"] == y["result_hash"] and x["result_hash"] is not None,
            "same_config_hash": x["config_hash"] == y["config_hash"], "same_data_hash": x["data_hash"] == y["data_hash"]}


# ------------------------------------------------------------------ エンティティ・xref・アンマージ
@route("GET", "/api/entities", perm="read")
def api_entities(ctx):
    c = ctx.conn
    size, off = page(ctx, 50)
    run = latest_run(c)
    min_size = ctx.qi("min_size", 1, 1)
    where, args = "WHERE g.status='active'", []
    if ctx.q.get("q"):
        like = "%" + ctx.q["q"].replace("%", "") + "%"
        rids = [r[0] for r in c.execute("SELECT rid FROM records WHERE skey LIKE ? OR raw LIKE ? LIMIT 300", (like, like))]
        if not rids:
            return {"total": 0, "items": []}
        where += f" AND x.rid IN ({','.join('?' * len(rids))})"
        args += rids
    flagged = ctx.q.get("flagged") == "1"
    rid_run = run["id"] if run else -1
    base = (f"FROM xref x JOIN global_ids g ON g.gid=x.gid LEFT JOIN cluster_info ci ON ci.run_id={rid_run} AND ci.gid=x.gid {where} GROUP BY x.gid "
            f"HAVING COUNT(*)>=? " + ("AND COALESCE(MAX(ci.n_rejected),0)>0 " if flagged else ""))
    total = c.execute(f"SELECT COUNT(*) FROM (SELECT x.gid {base})", args + [min_size]).fetchone()[0]
    rows = c.execute(f"SELECT x.gid, COUNT(*) size, MIN(x.rid) first_rid, COALESCE(MAX(ci.n_rejected),0) rejected, MAX(ci.min_p) min_p, COALESCE(MAX(ci.impure_truth),0) impure {base} "
                     f"ORDER BY size DESC, x.gid LIMIT ? OFFSET ?", args + [min_size, size, off]).fetchall()
    items = []
    for r in rows:
        rec = records_by_rids(c, [r["first_rid"]])[0]
        d = {"gid": r["gid"], "size": r["size"], "rejected": r["rejected"], "min_p": r["min_p"], "label": f"{rec['sname']}:{rec['skey']}"}
        if auth.can(ctx.user, "view_raw"):
            d["impure_truth"] = r["impure"]
            vals = [v for v in json.loads(rec["raw"]).values() if v][:2]
            d["preview"] = " / ".join(vals)
        items.append(d)
    return {"total": total, "items": items}


@route("GET", r"/api/entities/(\d+)", perm="read")
def api_entity(ctx):
    c = ctx.conn
    gid = int(ctx.m[0])
    g = c.execute("SELECT * FROM global_ids WHERE gid=?", (gid,)).fetchone()
    need(g, 404, "グローバルIDがありません")
    if g["status"] != "active":
        return {"gid": gid, "status": g["status"], "resolve": xref.resolve(c, gid), "members": [], "edges": []}
    rids = xref.entity_members(c, gid)
    need(len(rids) <= 3000, 400, "メンバーが多すぎて表示できません(3,000件まで)")
    run = latest_run(c)
    thr = pipeline.pipeline_config(c)["thresholds"]
    edges, rejected = [], 0
    if run:
        q = ",".join("?" * len(rids))
        for r in c.execute(f"SELECT a,b,p FROM pairs WHERE run_id=? AND a IN ({q}) AND b IN ({q})", [run["id"]] + rids + rids):
            edges.append({"a": r["a"], "b": r["b"], "p": r["p"]})
            if r["p"] < thr["review_low"]:
                rejected += 1
    cons = [dict(r) for r in c.execute(f"SELECT a,b,kind,origin,actor,reason,ts FROM constraints WHERE a IN ({','.join('?' * len(rids))}) AND b IN ({','.join('?' * len(rids))})", rids + rids)] if rids else []
    ev = [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "detail": json.loads(r["detail"]), "actor": r["actor"]}
          for r in c.execute("SELECT * FROM events WHERE gid=? ORDER BY seq DESC LIMIT 30", (gid,))]
    return {"gid": gid, "status": "active", "predecessor": g["predecessor"], "note": g["note"], "created": g["created"],
            "members": [rec_view(c, ctx.user, r) for r in records_by_rids(c, rids)], "edges": edges, "rejected_pairs": rejected, "constraints": cons,
            "events": ev, "thresholds": thr}


@route("POST", r"/api/entities/(\d+)/unmerge", perm="unmerge")
def api_unmerge(ctx):
    b = json_body(ctx, ["parts", "reason"])
    return xref.unmerge(ctx.conn, int(ctx.m[0]), b["parts"], ctx.user["username"], b["reason"])


@route("GET", r"/api/gid/(\d+)/resolve", perm="read")
def api_gid_resolve(ctx):
    return xref.resolve(ctx.conn, int(ctx.m[0]))


@route("GET", "/api/xref", perm="read")
def api_xref(ctx):
    src, key = ctx.q.get("source"), ctx.q.get("key")
    need(src and key, 400, "source と key を指定してください")
    r = ctx.conn.execute("SELECT r.rid, x.gid FROM records r JOIN sources s ON s.id=r.source_id LEFT JOIN xref x ON x.rid=r.rid WHERE s.name=? AND r.skey=?", (src, key)).fetchone()
    need(r, 404, "レコードがありません")
    members = [{"rid": m["rid"], "source": m["sname"], "key": m["skey"]} for m in records_by_rids(ctx.conn, xref.entity_members(ctx.conn, r["gid"]))] if r["gid"] else []
    return {"rid": r["rid"], "gid": r["gid"], "members": members}


@route("GET", "/api/events", perm="read")
def api_events(ctx):
    return xref.events_since(ctx.conn, ctx.qi("since", 0, 0), ctx.qi("limit", 200, 1, 2000))


@route("GET", "/api/export/xref.csv", perm="export")
def api_export_xref(ctx):
    lines = ["source,key,global_id"]
    for r in ctx.conn.execute("SELECT s.name, r.skey, x.gid FROM xref x JOIN records r ON r.rid=x.rid JOIN sources s ON s.id=r.source_id ORDER BY x.gid, r.rid"):
        lines.append(",".join('"' + str(v).replace('"', '""') + '"' for v in (r[0], r[1], r[2])))
    audit.append(ctx.conn, ctx.user["username"], "export.xref", "", {"rows": len(lines) - 1})
    ctx.conn.commit()
    return Resp("\ufeff" + "\n".join(lines) + "\n", ctype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=xref.csv"})


@route("GET", "/api/export/events.jsonl", perm="export")
def api_export_events(ctx):
    evs = xref.events_since(ctx.conn, ctx.qi("since", 0, 0), 100000)
    audit.append(ctx.conn, ctx.user["username"], "export.events", "", {"rows": len(evs)})
    ctx.conn.commit()
    return Resp("\n".join(json.dumps(e, ensure_ascii=False) for e in evs) + "\n", ctype="application/x-ndjson; charset=utf-8", headers={"Content-Disposition": "attachment; filename=events.jsonl"})


# ------------------------------------------------------------------ 確認キュー
@route("GET", "/api/review", perm="read")
def api_review(ctx):
    size, off = page(ctx, 50)
    total, items = review.list_items(ctx.conn, ctx.q.get("status") or None, ctx.q.get("assignee") or None, size, off)
    return {"total": total, "items": items}


@route("GET", "/api/review/estimate", perm="read")
def api_review_estimate(ctx):
    run = latest_run(ctx.conn)
    need(run, 409, "完了した実行がありません")
    pc = pipeline.pipeline_config(ctx.conn)["thresholds"]
    hi, lo = ctx.qf("hi", pc["auto"]), ctx.qf("lo", pc["review_low"])
    n = ctx.conn.execute("SELECT COUNT(*) FROM records WHERE status='active'").fetchone()[0]
    grid = []
    for h in sorted({0.5, 0.7, 0.9, 0.95, 0.99, hi}):
        for l in sorted({0.001, 0.01, 0.1, 0.3, 0.5, lo}):
            if l <= h:
                e = review.queue_estimate(ctx.conn, run["id"], h, l, n)
                grid.append({"hi": h, "lo": l, **e})
    return {"run_id": run["id"], "current": review.queue_estimate(ctx.conn, run["id"], hi, lo, n), "grid": grid,
            "note": "確認者が完全に正しくても、ブロッキングで候補に入らなかったペアは回収できない(再現率の上限は評価画面のPC)"}


@route("GET", r"/api/review/(\d+)", perm="read")
def api_review_item(ctx):
    it = ctx.conn.execute("SELECT * FROM review_items WHERE id=?", (int(ctx.m[0]),)).fetchone()
    need(it, 404, "確認項目がありません")
    run = latest_run(ctx.conn)
    d = explain_pair(ctx.conn, ctx.user, run, it["a"], it["b"]) if run else {}
    d["item"] = dict(it)
    return d


@route("POST", r"/api/review/(\d+)/decide", perm="review")
def api_review_decide(ctx):
    b = json_body(ctx, ["decision", "reason"])
    return review.decide(ctx.conn, int(ctx.m[0]), b["decision"], b["reason"], ctx.user["username"])


@route("POST", r"/api/review/(\d+)/assign", perm="task")
def api_review_assign(ctx):
    b = json_body(ctx, ["owner"])
    review.assign(ctx.conn, int(ctx.m[0]), b["owner"], ctx.user["username"])
    return {"ok": True}


# ------------------------------------------------------------------ 評価
def cost_c(ctx):
    c = ctx.qf("c")
    if c is None:
        cr = db.get_setting(ctx.conn, "cost_ratio") or {"c": 5.0}
        c = float(cr["c"])
    return c


@route("GET", "/api/eval/summary", perm="read")
def api_eval_summary(ctx):
    run = latest_run(ctx.conn, ctx.qi("run"))
    need(run, 409, "完了した実行がありません")
    c = cost_c(ctx)
    out = {"run_id": run["id"], "c": c, "gold_available": evaluation.gold_available(ctx.conn),
           "definitions": {"自動化率": "自動マージ帯のペア ÷ (自動マージ帯 + 人手確認帯)。人手を介さずに確定する割合。",
                           "PC": "真の一致ペアのうち、ブロッキングの候補に入った割合。全体の再現率の上限。",
                           "ペア単位/クラスタ単位": "ペア単位は直接比較したペアで、クラスタ単位は連結後に同じクラスタに入った全ペアで数える。クラスタ化後の再現率はPCを超えうる。",
                           "Accuracy": "全ペアの大半が非一致のため、単独のAccuracyは表示しない(K1)。"}}
    m = json.loads(run["metrics"]) if run["metrics"] else {}
    b = m.get("bands", {})
    a, r_ = b.get("auto", 0), b.get("review", 0)
    out["automation_rate"] = a / (a + r_) if a + r_ else None
    out["bands"] = b
    if out["gold_available"]:
        cur = evaluation.gold_curve(ctx.conn, run["id"])
        out["gold"] = cur
        out["cost"] = {"pair": evaluation.cost_optimum(cur["rows"], c, "pair"), "cluster": evaluation.cost_optimum(cur["rows"], c, "cluster")}
    samples = ctx.conn.execute("SELECT sample_id FROM label_samples_meta WHERE run_id=?", (run["id"],)).fetchall()
    if samples:
        try:
            est = evaluation.sample_estimates(ctx.conn, run["id"])
            out["sample"] = est
            rows = [{"t": r["t"], "pair": {"fp": r["pair"]["fp"], "fn": r["pair"]["fn"]}} for r in est["rows"]]
            out["cost_sample"] = evaluation.cost_optimum(rows, c, "pair")
        except ApiError as e:
            out["sample_error"] = e.message
    return out


@route("GET", "/api/eval/blocking", perm="read")
def api_eval_blocking(ctx):
    run = latest_run(ctx.conn, ctx.qi("run"))
    need(run, 409, "完了した実行がありません")
    m = json.loads(run["metrics"]) if run["metrics"] else {}
    per_rule = {}
    for r in ctx.conn.execute("SELECT rules FROM pairs WHERE run_id=?", (run["id"],)):
        for n in json.loads(r["rules"]):
            per_rule[n] = per_rule.get(n, 0) + 1
    truth = evaluation.gold_truth(ctx.conn)
    gold_rule = {}
    if truth:
        for r in ctx.conn.execute("SELECT a,b,rules FROM pairs WHERE run_id=?", (run["id"],)):
            if truth.get(r["a"]) and truth.get(r["a"]) == truth.get(r["b"]):
                for n in json.loads(r["rules"]):
                    gold_rule[n] = gold_rule.get(n, 0) + 1
    return {"run_id": run["id"], "blocking": m.get("blocking"), "n_candidates": m.get("n_candidates"), "reduction_ratio": m.get("reduction_ratio"), "gold": m.get("gold"),
            "pairs_per_rule": per_rule, "true_pairs_per_rule": gold_rule}


@route("POST", "/api/labels/samples", perm="label")
def api_sample_create(ctx):
    b = json_body(ctx, ["run_id"])
    sid, out = evaluation.create_sample(ctx.conn, int(b["run_id"]), ctx.user["username"], int(b.get("per_stratum", 30)), int(b.get("seed", 1)), int(b.get("noncandidate", 0)))
    return {"sample_id": sid, "strata": out}


@route("GET", "/api/labels/samples", perm="read")
def api_sample_list(ctx):
    out = []
    for r in ctx.conn.execute("SELECT * FROM label_samples_meta ORDER BY sample_id DESC"):
        d = dict(r)
        d["n_pairs"] = ctx.conn.execute("SELECT COUNT(*) FROM label_samples WHERE sample_id=?", (r["sample_id"],)).fetchone()[0]
        d["labelers"] = [x[0] for x in ctx.conn.execute("SELECT DISTINCT labeler FROM labels WHERE sample_id=?", (r["sample_id"],))]
        out.append(d)
    return out


@route("GET", r"/api/labels/samples/(\d+)/next", perm="label")
def api_sample_next(ctx):
    sid = int(ctx.m[0])
    n = evaluation.next_items(ctx.conn, sid, ctx.user["username"], ctx.qi("limit", 5, 1, 50))
    run = latest_run(ctx.conn, ctx.conn.execute("SELECT run_id FROM label_samples_meta WHERE sample_id=?", (sid,)).fetchone()["run_id"])
    for it in n["items"]:
        it["pair"] = explain_pair(ctx.conn, ctx.user, run, it["a"], it["b"])
        # ラベル付けでスコアを見せると判断が引きずられるため、確率は返さない(盲検)
        it["pair"].pop("explanation", None)
        it["pair"].pop("stored_p", None)
    return n


@route("POST", "/api/labels", perm="label")
def api_label_submit(ctx):
    b = json_body(ctx, ["sample_id", "a", "b", "label"])
    evaluation.submit_label(ctx.conn, int(b["sample_id"]), int(b["a"]), int(b["b"]), ctx.user["username"], int(b["label"]))
    ctx.conn.commit()
    return {"ok": True}


@route("GET", r"/api/labels/samples/(\d+)/consensus", perm="read")
def api_sample_consensus(ctx):
    sid = int(ctx.m[0])
    final, info = evaluation.consensus(ctx.conn, sid)
    return {"info": info, "n_final": len(final), "kappa": evaluation.kappa_matrix(ctx.conn, sid)}


@route("POST", "/api/labels/adjudicate", perm="adjudicate")
def api_adjudicate(ctx):
    b = json_body(ctx, ["sample_id", "a", "b", "label"])
    evaluation.adjudicate(ctx.conn, int(b["sample_id"]), int(b["a"]), int(b["b"]), int(b["label"]), ctx.user["username"])
    ctx.conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ ガバナンス
@route("GET", "/api/purposes", perm="read")
def api_purposes(ctx):
    return governance.list_purposes(ctx.conn)


@route("POST", "/api/purposes", perm="purpose")
def api_purpose_create(ctx):
    return {"id": governance.create_purpose(ctx.conn, ctx.user["username"], json_body(ctx))}


@route("GET", "/api/governance/check", perm="read")
def api_gov_check(ctx):
    ver, cfg = pipeline.active_std(ctx.conn)
    v, info = governance.run_violations(ctx.conn, cfg)
    return {"violations": v, "notes": info, "phase0_open": phase0_open(ctx.conn)}


@route("GET", "/api/users", perm="admin")
def api_users(ctx):
    return [{"username": r["username"], "display": r["display"], "roles": json.loads(r["roles"]), "active": bool(r["active"])} for r in ctx.conn.execute("SELECT * FROM users ORDER BY id")]


@route("GET", "/api/users/names", perm="read")
def api_user_names(ctx):
    return [r["username"] for r in ctx.conn.execute("SELECT username FROM users WHERE active=1 ORDER BY username")]


@route("POST", "/api/users", perm="admin")
def api_user_create(ctx):
    b = json_body(ctx, ["username", "password", "roles"])
    auth.create_user(ctx.conn, b["username"], b["password"], b["roles"], b.get("display"))
    audit.append(ctx.conn, ctx.user["username"], "user.create", b["username"], {"roles": b["roles"]})
    ctx.conn.commit()
    return {"ok": True}


@route("PUT", r"/api/users/([^/]+)/roles", perm="admin")
def api_user_roles(ctx):
    b = json_body(ctx, ["roles"])
    bad = [r for r in b["roles"] if r not in auth.PERMS]
    need(not bad, 400, f"未知の役割: {bad}")
    n = ctx.conn.execute("UPDATE users SET roles=? WHERE username=?", (json.dumps(sorted(b["roles"])), ctx.m[0])).rowcount
    need(n, 404, "ユーザーがいません")
    audit.append(ctx.conn, ctx.user["username"], "user.roles", ctx.m[0], {"roles": b["roles"]})
    ctx.conn.commit()
    return {"ok": True}


@route("GET", "/api/audit", perm="audit")
def api_audit(ctx):
    limit = ctx.qi("limit", 100, 1, 1000)
    before = ctx.qi("before")
    sql, args = "SELECT * FROM audit_log", []
    if before:
        sql += " WHERE seq<?"
        args.append(before)
    rows = ctx.conn.execute(sql + " ORDER BY seq DESC LIMIT ?", args + [limit]).fetchall()
    return [{"seq": r["seq"], "ts": r["ts"], "actor": r["actor"], "action": r["action"], "target": r["target"], "detail": json.loads(r["detail"]), "hash": r["hash"][:16]} for r in rows]


@route("GET", "/api/audit/verify", perm="audit")
def api_audit_verify(ctx):
    ok, n, bad = audit.verify(ctx.conn)
    return {"ok": ok, "entries": n, "first_bad_seq": bad}


# ------------------------------------------------------------------ タスク
@route("GET", "/api/tasks", perm="read")
def api_tasks(ctx):
    return tasks.list_all(ctx.conn, kind=ctx.q.get("kind"), owner=ctx.q.get("owner"), status=ctx.q.get("status"), phase=ctx.q.get("phase"))


@route("POST", "/api/tasks", perm="task")
def api_task_create(ctx):
    tid = tasks.create(ctx.conn, ctx.user["username"], json_body(ctx))
    ctx.conn.commit()
    return {"id": tid}


@route("PUT", r"/api/tasks/([\w\-]+)", perm="task")
def api_task_update(ctx):
    tasks.update(ctx.conn, ctx.user["username"], ctx.m[0], json_body(ctx))
    ctx.conn.commit()
    return {"ok": True}


@route("GET", r"/api/tasks/([\w\-]+)/history", perm="read")
def api_task_history(ctx):
    return tasks.history(ctx.conn, ctx.m[0])


@route("POST", "/api/tasks/import-prd", perm="task")
def api_task_import(ctx):
    r = tasks.import_prd(ctx.conn, ctx.user["username"], ctx.app.prd_path)
    ctx.conn.commit()
    return r


@route("GET", "/api/tasks-summary", perm="read")
def api_task_summary(ctx):
    c = ctx.conn
    by = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status")}
    return {"by_status": by, "longest_chain": tasks.longest_chain(c), "ready": len([t for t in tasks.list_all(c) if t["ready"]]),
            "overdue": len([t for t in tasks.list_all(c, kind="review") if t["overdue"]])}


@route("GET", "/api/requirements", perm="read")
def api_requirements(ctx):
    p = Path(__file__).resolve().parent.parent / "docs" / "requirements_coverage.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"note": "python -m ermdm coverage で生成してください"}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "ERMDM/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 標準のアクセスログは使わず、構造化ログを出す
        pass

    def _send(self, status, body: bytes, ctype, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _handle(self, method):
        t0 = time.time()
        status, who = 500, "-"
        try:
            if not self._host_ok():
                status = 403
                return self._send(403, b'{"error":"Host header is not allowed"}', "application/json")
            u = urlparse(self.path)
            path = u.path
            if not path.startswith("/api/"):
                if method != "GET":
                    status = 405
                    return self._send(405, b"", "text/plain")
                status = self._static(path)
                return
            origin = self.headers.get("Origin")
            if method != "GET" and origin and urlparse(origin).netloc != self.headers.get("Host"):
                status = 403
                return self._send(403, json.dumps({"error": "不正なOriginです"}).encode(), "application/json")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                status = 413
                return self._send(413, json.dumps({"error": "本文が大きすぎます"}).encode(), "application/json")
            raw = self.rfile.read(length) if length else b""
            body = None
            if raw:
                if "json" not in (self.headers.get("Content-Type") or ""):
                    raise ApiError(415, "Content-Type は application/json です")
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise ApiError(400, "JSON として解釈できません")
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            fn = None
            for meth, pat, f, perm, public in ROUTES:
                m = pat.match(path)
                if m and meth == method:
                    fn, mm, rperm, rpub = f, m.groups(), perm, public
                    break
            if not fn:
                if any(p.match(path) for _, p, _, _, _ in ROUTES):
                    raise ApiError(405, "このメソッドは使えません")
                raise ApiError(404, "見つかりません")
            conn = db.connect(self.server.app.db_path)
            try:
                ck = SimpleCookie(self.headers.get("Cookie") or "")
                token = ck["sid"].value if "sid" in ck else None
                user = auth.user_from_token(conn, token)
                who = user["username"] if user else "-"
                if not rpub and not user:
                    raise ApiError(401, "ログインが必要です")
                ctx = Ctx(self.server.app, conn, user, body, q, mm, token)
                if rperm:
                    auth.require(user, rperm)
                res = fn(ctx)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            if isinstance(res, Resp):
                body_b = res.body.encode("utf-8") if isinstance(res.body, str) else res.body
                status = res.status
                return self._send(res.status, body_b, res.ctype, res.headers)
            status = 200
            return self._send(200, json.dumps(res, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
        except ApiError as e:
            status = e.status
            payload = {"error": e.message, **e.extra}
            return self._send(e.status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")
        except (ValueError, TypeError, KeyError) as e:
            status = 400
            return self._send(400, json.dumps({"error": f"入力が不正です: {type(e).__name__}"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        except Exception:  # noqa: BLE001
            status = 500
            return self._send(500, json.dumps({"error": "内部エラーが発生しました"}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        finally:
            if self.server.app.verbose:
                sys.stderr.write(jdump({"ts": now(), "method": method, "path": self.path.split("?")[0], "status": status, "user": who, "ms": round((time.time() - t0) * 1000)}) + "\n")

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        p = (WEB / rel).resolve()
        if WEB.resolve() not in p.parents and p != WEB.resolve():
            self._send(404, b"not found", "text/plain")
            return 404
        if not p.is_file():
            self._send(404, b"not found", "text/plain")
            return 404
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if p.suffix in (".js", ".css", ".html"):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)
        return 200

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PUT(self): self._handle("PUT")
    def do_DELETE(self): self._handle("DELETE")
    def do_HEAD(self): self._handle("GET")


class App:
    def __init__(self, data_dir, prd_path=None, verbose=True):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.import_dir = self.data_dir / "import"
        self.import_dir.mkdir(exist_ok=True)
        self.db_path = str(self.data_dir / "ermdm.db")
        self.prd_path = Path(prd_path) if prd_path else Path(__file__).resolve().parents[2] / "work" / "prd.md"
        self.verbose = verbose
        conn = db.connect(self.db_path)
        db.init(conn)
        conn.close()

    def start_run(self, run_id):
        def work():
            conn = db.connect(self.db_path)
            try:
                with pipeline.RUN_LOCK:
                    pipeline.execute_run(conn, run_id)
            finally:
                conn.close()
        threading.Thread(target=work, daemon=True).start()

    def make_server(self, port=8800):
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        srv.app = self
        srv.daemon_threads = True
        return srv
