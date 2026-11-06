"""パイプラインの実行(標準化 → ブロッキング → ペア判定 → クラスタリング → xref → 確認キュー)。実行は再現可能で、設定・データ・結果のハッシュを残す。"""
import hashlib
import json
import threading
import time
import traceback
from collections import defaultdict
from itertools import combinations

from . import audit, blocking, clustering, governance, matching, review, standardize, xref
from .db import CODE_VERSION, get_setting, set_setting
from .util import ApiError, jdump, need, now, sha

RUN_LOCK = threading.Lock()

DEFAULT_PIPELINE = {
    "blocking": {"rules": [], "max_block": 500, "max_candidates": 2_000_000, "link_type": "all"},
    "thresholds": {"auto": 0.9, "review_low": 0.5},
    "clusterer": "components",
    "seed": 1,
}


def pipeline_config(conn):
    cfg = json.loads(json.dumps(DEFAULT_PIPELINE))
    saved = get_setting(conn, "pipeline", {}) or {}
    for k, v in saved.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def validate_pipeline(cfg):
    t = cfg["thresholds"]
    need(0 <= float(t["review_low"]) <= float(t["auto"]) <= 1, 400, "0 ≤ 確認下限 ≤ 自動マージ閾値 ≤ 1 である必要があります")
    need(cfg["clusterer"] in clustering.CLUSTERERS, 400, f"clusterer は {sorted(clustering.CLUSTERERS)}")
    need(cfg["blocking"]["link_type"] in ("all", "cross"), 400, "link_type は all または cross")
    if cfg["blocking"]["rules"]:
        blocking.validate(cfg["blocking"]["rules"])


def active_std(conn):
    r = conn.execute("SELECT * FROM std_configs ORDER BY version DESC").fetchall()
    ver = get_setting(conn, "active_std_version")
    if ver is None:
        return None, None
    row = conn.execute("SELECT * FROM std_configs WHERE version=?", (ver,)).fetchone()
    return (row["version"], json.loads(row["config"])) if row else (None, None)


def load_active(conn, std_version, std_cfg, with_std=True):
    """有効なレコードと標準化値を読み込む(標準化値は版ごとにキャッシュ)。"""
    rows = conn.execute("SELECT r.rid, s.name sname, r.raw, r.truth, r.raw_hash FROM records r JOIN sources s ON s.id=r.source_id WHERE r.status='active' ORDER BY r.rid").fetchall()
    std, source_of, truth, raw_hashes = {}, {}, {}, []
    cached = {r["rid"]: json.loads(r["fields"]) for r in conn.execute("SELECT rid, fields FROM std_values WHERE version=?", (std_version,))} if with_std else {}
    new = []
    for r in rows:
        source_of[r["rid"]] = r["sname"]
        if r["truth"]:
            truth[r["rid"]] = r["truth"]
        raw_hashes.append((r["rid"], r["raw_hash"]))
        if with_std:
            f = cached.get(r["rid"])
            if f is None:
                f = standardize.apply(std_cfg, r["sname"], json.loads(r["raw"]))
                new.append((r["rid"], std_version, jdump(f)))
            std[r["rid"]] = f
    if new:
        conn.executemany("INSERT OR REPLACE INTO std_values(rid,version,fields) VALUES(?,?,?)", new)
        conn.commit()
    return std, source_of, truth, raw_hashes


def load_constraints(conn, active):
    must, cannot = [], []
    for r in conn.execute("SELECT a,b,kind FROM constraints"):
        if r["a"] in active and r["b"] in active:
            (must if r["kind"] == "must" else cannot).append((r["a"], r["b"]))
    return must, cannot


def check_governance(conn, std_cfg):
    v, info = governance.run_violations(conn, std_cfg)
    if v:
        raise ApiError(409, "ガバナンス上の理由で実行できません: " + " / ".join(x["message"] for x in v), violations=v)
    return info


def train_model(conn, model_id, actor):
    m = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    need(m, 404, "モデルがありません")
    cfg = json.loads(m["config"])
    pc = pipeline_config(conn)
    ver, std_cfg = active_std(conn)
    need(std_cfg, 409, "有効な標準化設定がありません")
    need(pc["blocking"]["rules"], 409, "ブロッキングルールが未設定です")
    check_governance(conn, std_cfg)
    std, source_of, truth, _ = load_active(conn, ver, std_cfg)
    need(len(std) >= 2, 409, "レコードが足りません")
    t0 = time.time()
    cand, est = blocking.generate(pc["blocking"]["rules"], std, pc["blocking"]["max_block"], pc["blocking"]["max_candidates"], source_of, pc["blocking"]["link_type"])
    need(cand, 409, "候補ペアがありません。ブロッキングを見直してください")
    mt = matching.make(cfg)
    seed = int(pc["seed"])
    params = mt.fit(std, cand, sorted(std), seed) if cfg["matcher"] != "deterministic" else {}
    stats = {"train_seconds": round(time.time() - t0, 2), "n_records": len(std), "n_candidates": len(cand), "blocking": est}
    conn.execute("UPDATE models SET params=?, hash=?, std_version=?, seed=?, trained=?, trained_by=?, stats=? WHERE id=?",
                 (jdump(params), matching.model_hash(cfg, params), ver, seed, now(), actor, jdump(stats), model_id))
    audit.append(conn, actor, "model.train", str(model_id), {"matcher": cfg["matcher"], "n_candidates": len(cand), "iterations": params.get("iterations"), "lambda": params.get("lambda")})
    conn.commit()
    return {"params": params, "stats": stats}


def create_run(conn, actor, mode="full", recluster_from=None, overrides=None):
    cfg = pipeline_config(conn)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    validate_pipeline(cfg)
    ver, std_cfg = active_std(conn)
    need(std_cfg, 409, "有効な標準化設定がありません")
    check_governance(conn, std_cfg)
    mid = get_setting(conn, "active_model")
    if mode == "full":
        need(mid, 409, "有効なモデルがありません(作成して学習し、有効化してください)")
        m = conn.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        need(m and (m["params"] or json.loads(m["config"]).get("matcher") == "deterministic"), 409, "モデルが未学習です。学習してください")
        need(cfg["blocking"]["rules"], 409, "ブロッキングルールが未設定です")
    else:
        need(recluster_from and conn.execute("SELECT 1 FROM runs WHERE id=? AND status='done'", (recluster_from,)).fetchone(), 400, "再クラスタリング元の実行が不正です")
    cfg["std_version"], cfg["model_id"], cfg["mode"], cfg["recluster_from"] = ver, mid, mode, recluster_from
    running = conn.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone()
    need(not running, 409, "別の実行が進行中です")
    cur = conn.execute("INSERT INTO runs(ts,actor,status,mode,config,config_hash,seed,std_version,model_id,code_version) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (now(), actor, "queued", mode, jdump(cfg), sha(jdump(cfg)), int(cfg["seed"]), ver, mid, CODE_VERSION))
    audit.append(conn, actor, "run.create", str(cur.lastrowid), {"mode": mode, "config_hash": sha(jdump(cfg))[:12]})
    conn.commit()
    return cur.lastrowid


def execute_run(conn, run_id):
    """1回の実行。例外は run.error に残し、監査ログにも記録する。"""
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    cfg = json.loads(run["config"])
    actor = run["actor"]
    conn.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
    conn.commit()
    tm, metrics = {}, {}

    def stage(name, t0):
        tm[name] = round(time.time() - t0, 3)

    try:
        ver, std_cfg = cfg["std_version"], None
        row = conn.execute("SELECT config FROM std_configs WHERE version=?", (ver,)).fetchone()
        std_cfg = json.loads(row["config"])
        info = check_governance(conn, std_cfg)
        metrics["governance_notes"] = [x["message"] for x in info]
        t0 = time.time()
        std, source_of, truth, raw_hashes = load_active(conn, ver, std_cfg)
        stage("標準化", t0)
        rids = sorted(std)
        need(rids, 409, "有効なレコードがありません")
        data_hash = sha(jdump([raw_hashes, ver, sha(jdump(std_cfg))]))
        n = len(rids)
        total_pairs = n * (n - 1) // 2
        if cfg["blocking"]["link_type"] == "cross":
            from collections import Counter
            cs = Counter(source_of.values())
            total_pairs -= sum(c * (c - 1) // 2 for c in cs.values())
        metrics.update({"n_records": n, "n_sources": len(set(source_of.values())), "total_pairs": total_pairs})
        auto, lo = float(cfg["thresholds"]["auto"]), float(cfg["thresholds"]["review_low"])

        if cfg["mode"] == "full":
            m = conn.execute("SELECT * FROM models WHERE id=?", (cfg["model_id"],)).fetchone()
            mcfg = json.loads(m["config"])
            mt = matching.make(mcfg, json.loads(m["params"]) if m["params"] else None)
            t0 = time.time()
            cand, est = blocking.generate(cfg["blocking"]["rules"], std, cfg["blocking"]["max_block"], cfg["blocking"]["max_candidates"], source_of, cfg["blocking"]["link_type"])
            stage("ブロッキング", t0)
            metrics["blocking"] = est
            metrics["n_candidates"] = len(cand)
            metrics["reduction_ratio"] = 1 - len(cand) / total_pairs if total_pairs else 0
            t0 = time.time()
            names = [r["name"] for r in cfg["blocking"]["rules"]]
            batch = []
            for (a, b), ri in sorted(cand.items()):
                p, w, g, rh = mt.score(std[a], std[b])
                batch.append((run_id, a, b, round(p, 9), round(w, 6), jdump([names[i] for i in ri]), ",".join(map(str, g))))
                if len(batch) >= 20000:
                    conn.executemany("INSERT INTO pairs(run_id,a,b,p,w,rules,gam) VALUES(?,?,?,?,?,?,?)", batch)
                    batch = []
            if batch:
                conn.executemany("INSERT INTO pairs(run_id,a,b,p,w,rules,gam) VALUES(?,?,?,?,?,?,?)", batch)
            stage("ペア判定", t0)
        else:
            t0 = time.time()
            conn.execute("INSERT INTO pairs(run_id,a,b,p,w,rules,gam) SELECT ?,a,b,p,w,rules,gam FROM pairs WHERE run_id=?", (run_id, cfg["recluster_from"]))
            stage("スコア再利用", t0)
            metrics["n_candidates"] = conn.execute("SELECT COUNT(*) c FROM pairs WHERE run_id=?", (run_id,)).fetchone()["c"]
        conn.commit()

        # クラスタリング
        t0 = time.time()
        active = set(rids)
        must, cannot = load_constraints(conn, active)
        cannot_set = set(cannot)
        scored = {}
        for r in conn.execute("SELECT a,b,p FROM pairs WHERE run_id=?", (run_id,)):
            if r["a"] in active and r["b"] in active:
                scored[(r["a"], r["b"])] = r["p"]
        edge_p = {k: p for k, p in scored.items() if p >= auto and k not in cannot_set}
        for k in must:
            edge_p[k] = 1.0
        edges = [(a, b, p) for (a, b), p in edge_p.items()]
        clusters, removed = clustering.cluster(rids, edges, cfg["clusterer"], cannot)
        scored_eff = dict(scored)
        for k in cannot:
            scored_eff[k] = 0.0
        cstats = clustering.stats(clusters, edge_p, scored_eff, lo, truth if truth else None)
        stage("クラスタリング", t0)

        # xref
        t0 = time.time()
        res = xref.reconcile(conn, run_id, clusters, actor)
        for cs, cl in zip(cstats, clusters):
            cs["gid"] = res["assigned"][cs["cidx"]]
        conn.executemany("INSERT INTO clusters(run_id,rid,cidx) VALUES(?,?,?)", [(run_id, rid, i) for i, c in enumerate(clusters) for rid in c])
        conn.executemany("INSERT INTO cluster_info(run_id,cidx,gid,size,n_edges,n_rejected,n_transitive,min_p,impure_truth) VALUES(?,?,?,?,?,?,?,?,?)",
                         [(run_id, c["cidx"], c["gid"], c["size"], c["n_edges"], c["n_rejected"], c["n_transitive"], c["min_p"], c["impure_truth"]) for c in cstats])
        stage("xref反映", t0)

        t0 = time.time()
        rv = review.sync_queue(conn, run_id, actor, auto, lo)
        stage("確認キュー", t0)

        # 指標
        band = conn.execute("SELECT SUM(p>=?) a, SUM(p>=? AND p<?) r, SUM(p<?) d, COUNT(*) n FROM pairs WHERE run_id=?", (auto, lo, auto, lo, run_id)).fetchone()
        metrics["bands"] = {"auto": band["a"] or 0, "review": band["r"] or 0, "discard": band["d"] or 0}
        metrics["n_clusters"] = len(clusters)
        metrics["n_multi_clusters"] = sum(1 for c in clusters if len(c) > 1)
        metrics["largest_cluster"] = max(len(c) for c in clusters)
        metrics["n_flagged_clusters"] = sum(1 for c in cstats if c["n_rejected"] > 0)
        metrics["edges_removed_by_constraints"] = len(removed)
        metrics["constraints"] = {"must": len(must), "cannot": len(cannot)}
        metrics["events"] = res["events"]
        metrics["review"] = rv
        if truth:
            tg = defaultdict(int)
            for r in truth.values():
                tg[r] += 1
            true_total = sum(c * (c - 1) // 2 for c in tg.values())
            by_truth = defaultdict(list)
            for rid, t in truth.items():
                by_truth[t].append(rid)
            tp_cand = sum(1 for (a, b) in scored if truth.get(a) and truth.get(a) == truth.get(b))
            metrics["gold"] = {"true_pairs": true_total, "true_pairs_in_candidates": tp_cand, "pairs_completeness": tp_cand / true_total if true_total else None,
                               "impure_clusters": sum(1 for c in cstats if c["impure_truth"])}
        metrics["timings"] = tm

        h = hashlib.sha256()
        for (a, b), p in sorted(scored.items()):
            h.update(f"{a},{b},{p:.9f};".encode())
        for c in clusters:
            h.update(("|".join(map(str, c)) + ";").encode())
        result_hash = h.hexdigest()
        conn.execute("UPDATE runs SET status='done', data_hash=?, metrics=?, result_hash=?, finished=? WHERE id=?", (data_hash, jdump(metrics), result_hash, now(), run_id))
        audit.append(conn, actor, "run.done", str(run_id), {"result_hash": result_hash[:16], "clusters": len(clusters), "pairs": len(scored), "mode": cfg["mode"]})
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        msg = e.message if isinstance(e, ApiError) else f"{type(e).__name__}: {e}"
        conn.execute("DELETE FROM pairs WHERE run_id=?", (run_id,))
        conn.execute("UPDATE runs SET status='failed', error=?, finished=?, metrics=? WHERE id=?", (msg, now(), jdump({"timings": tm}), run_id))
        audit.append(conn, actor, "run.failed", str(run_id), {"error": msg})
        conn.commit()
        if not isinstance(e, ApiError):
            traceback.print_exc()


def run_sync(conn, actor, mode="full", recluster_from=None, overrides=None):
    """同期実行(テスト・CLI用)。"""
    with RUN_LOCK:
        rid = create_run(conn, actor, mode, recluster_from, overrides)
        execute_run(conn, rid)
    return rid
