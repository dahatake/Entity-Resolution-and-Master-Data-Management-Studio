"""xref・グローバルID・アンマージ・変更イベント(FR-XRF-01〜03)。

グローバルIDの継承規則(FR-XRF-03。Q6 で最終決定するまでの既定):
  * マージ: 統合される旧IDのうち最も古い(番号が最小の)IDが存続し、他は retired(successor=存続ID)になる。
  * 分割  : 旧IDのメンバーを最も多く引き継ぐクラスタが旧IDを保ち、残りは新しいID(predecessor=旧ID)を得る。同数なら最小 rid を含む側。
  * 空になったIDは retired にする。retired の ID は successor を辿って現行IDへ解決できる。
元データ(records.raw)は書き換えない。
"""
import json
from collections import Counter, defaultdict
from itertools import combinations

from . import audit
from .util import ApiError, jdump, need, now

MAX_CONSTRAINTS = 20000


def _detail(conn, rids):
    q = ",".join("?" * len(rids))
    rows = conn.execute(f"SELECT r.rid, s.name, r.skey FROM records r JOIN sources s ON s.id=r.source_id WHERE r.rid IN ({q})", list(rids)).fetchall() if rids else []
    return [{"rid": r["rid"], "source": r["name"], "key": r["skey"]} for r in sorted(rows, key=lambda r: r["rid"])]


def _event(conn, typ, gid, detail, actor, run_id):
    cur = conn.execute("INSERT INTO events(ts,type,gid,detail,actor,run_id) VALUES(?,?,?,?,?,?)", (now(), typ, gid, jdump(detail), actor, run_id))
    return cur.lastrowid


def _new_gid(conn, predecessor=None, note=""):
    cur = conn.execute("INSERT INTO global_ids(status,predecessor,created,note) VALUES('active',?,?,?)", (predecessor, now(), note))
    return cur.lastrowid


def reconcile(conn, run_id, clusters, actor):
    """クラスタ結果をグローバルIDへ反映する。同じ入力を再度反映してもイベントは増えない(冪等)。"""
    active = {rid for c in clusters for rid in c}
    old = {r["rid"]: r["gid"] for r in conn.execute("SELECT rid,gid FROM xref")}
    stats = Counter()

    # 削除されたレコード
    gone = [rid for rid in old if rid not in active]
    gone_by_gid = defaultdict(list)
    for rid in gone:
        gone_by_gid[old[rid]].append(rid)
    for rid in gone:
        conn.execute("DELETE FROM xref WHERE rid=?", (rid,))
        del old[rid]

    old_members = defaultdict(set)
    for rid, g in old.items():
        old_members[g].add(rid)
    idx_of = {}
    for i, c in enumerate(clusters):
        for rid in c:
            idx_of[rid] = i

    # 旧IDごとに、最も多くのメンバーを引き継ぐクラスタ(請求者)を決める
    claimant, overlaps = {}, defaultdict(Counter)
    for g, mem in old_members.items():
        cnt = Counter(idx_of[r] for r in mem)
        for i, n in cnt.items():
            overlaps[i][g] = n
        best = sorted(cnt.items(), key=lambda kv: (-kv[1], min(clusters[kv[0]])))[0][0]
        claimant[g] = best
    claims = defaultdict(list)
    for g, i in claimant.items():
        claims[i].append(g)

    assigned, fresh, retired_now = {}, set(), {}
    for i, c in enumerate(clusters):
        cl = sorted(claims.get(i, []))
        if cl:
            surv = cl[0]
            assigned[i] = surv
            for g in cl[1:]:
                conn.execute("UPDATE global_ids SET status='retired', successor=?, note='merged' WHERE gid=?", (surv, g))
                _event(conn, "merge", surv, {"survivor": surv, "retired": g, "members": _detail(conn, sorted(old_members[g]))}, actor, run_id)
                retired_now[g] = surv
                stats["merge"] += 1
        else:
            pred = overlaps[i].most_common(1)[0][0] if overlaps.get(i) else None
            g = _new_gid(conn, predecessor=pred, note="split" if pred else "new")
            assigned[i] = g
            fresh.add(g)
            if pred:
                _event(conn, "split", g, {"from": pred, "members": _detail(conn, c)}, actor, run_id)
                stats["split"] += 1
            else:
                _event(conn, "create", g, {"members": _detail(conn, c)}, actor, run_id)
                stats["create"] += 1

    live = set(assigned.values())
    for g, rids in gone_by_gid.items():
        if g not in live and g not in retired_now:
            conn.execute("UPDATE global_ids SET status='retired', note='empty' WHERE gid=? AND status='active'", (g,))
            _event(conn, "retire", g, {"reason": "empty", "removed": _detail(conn, rids)}, actor, run_id)
            stats["retire"] += 1

    added, removed = defaultdict(list), defaultdict(list)
    for i, c in enumerate(clusters):
        g = assigned[i]
        for rid in c:
            og = old.get(rid)
            if og == g:
                continue
            conn.execute("INSERT INTO xref(rid,gid,since_seq) VALUES(?,?,0) ON CONFLICT(rid) DO UPDATE SET gid=excluded.gid, since_seq=0", (rid, g))
            if g not in fresh and not (og in retired_now and retired_now[og] == g):
                added[g].append(rid)
            if og is not None and og not in retired_now and og in live:
                removed[og].append(rid)
    for g, rids in gone_by_gid.items():
        if g in live:
            removed[g].extend(rids)
    for g in sorted((set(added) | set(removed)) & live):
        if g in fresh:
            continue
        _event(conn, "update", g, {"added": _detail(conn, added.get(g, [])), "removed": _detail(conn, removed.get(g, []))}, actor, run_id)
        stats["update"] += 1
    last = conn.execute("SELECT COALESCE(MAX(seq),0) m FROM events").fetchone()["m"]
    conn.execute("UPDATE xref SET since_seq=? WHERE since_seq=0", (last,))
    conn.execute("UPDATE global_ids SET created_seq=? WHERE created_seq IS NULL", (last,))
    return {"assigned": {i: g for i, g in assigned.items()}, "events": dict(stats)}


def entity_members(conn, gid):
    return [r["rid"] for r in conn.execute("SELECT rid FROM xref WHERE gid=? ORDER BY rid", (gid,))]


def resolve(conn, gid):
    seen = []
    cur = gid
    while True:
        r = conn.execute("SELECT * FROM global_ids WHERE gid=?", (cur,)).fetchone()
        need(r, 404, f"グローバルID {gid} は存在しません")
        seen.append(cur)
        if r["status"] == "active":
            return {"requested": gid, "current": cur, "chain": seen, "status": "active"}
        if r["successor"] is None or r["successor"] in seen:
            return {"requested": gid, "current": None, "chain": seen, "status": r["status"], "note": r["note"]}
        cur = r["successor"]


def unmerge(conn, gid, parts, actor, reason):
    """gid のメンバーのうち parts(rid配列の配列)を分割する。分割は否定制約として永続化し、再実行でも再統合されない。"""
    need(reason and reason.strip(), 400, "分割の理由を入力してください")
    members = entity_members(conn, gid)
    need(members, 404, f"グローバルID {gid} は有効なメンバーを持ちません")
    ms = set(members)
    parts = [sorted(set(int(x) for x in p)) for p in parts if p]
    need(parts, 400, "分割するレコードを選んでください")
    flat = [x for p in parts for x in p]
    need(len(flat) == len(set(flat)), 400, "分割グループが重複しています")
    need(all(x in ms for x in flat), 400, "このIDのメンバーではないレコードが含まれています")
    rest = sorted(ms - set(flat))
    groups = ([rest] if rest else []) + parts
    need(len(groups) >= 2, 400, "分割後に2グループ以上になる必要があります")
    n_cons = sum(len(a) * len(b) for a, b in combinations(groups, 2))
    need(n_cons <= MAX_CONSTRAINTS, 400, f"制約が多すぎます({n_cons}件)。分割を小さくしてください")
    keeper = sorted(groups, key=lambda g: (-len(g), min(g)))[0]
    for a_, b_ in combinations(groups, 2):
        for x in a_:
            for y in b_:
                conn.execute("INSERT OR IGNORE INTO constraints(a,b,kind,origin,actor,reason,ts) VALUES(?,?,?,?,?,?,?)",
                             (min(x, y), max(x, y), "cannot", "unmerge", actor, reason, now()))
    new_ids = []
    for g in groups:
        if g is keeper:
            continue
        ng = _new_gid(conn, predecessor=gid, note="unmerge: " + reason)
        new_ids.append(ng)
        for rid in g:
            conn.execute("UPDATE xref SET gid=? WHERE rid=?", (ng, rid))
        _event(conn, "split", ng, {"from": gid, "reason": reason, "manual": True, "members": _detail(conn, g)}, actor, None)
    moved = [r for g in groups if g is not keeper for r in g]
    _event(conn, "update", gid, {"added": [], "removed": _detail(conn, moved), "reason": reason, "manual": True}, actor, None)
    last = conn.execute("SELECT COALESCE(MAX(seq),0) m FROM events").fetchone()["m"]
    conn.execute("UPDATE xref SET since_seq=? WHERE since_seq IS NULL OR since_seq=0", (last,))
    conn.execute("UPDATE global_ids SET created_seq=? WHERE created_seq IS NULL", (last,))
    audit.append(conn, actor, "entity.unmerge", str(gid), {"keeper_size": len(keeper), "new_ids": new_ids, "constraints": n_cons, "reason": reason})
    conn.commit()
    return {"kept": gid, "kept_members": keeper, "new_ids": new_ids, "constraints_added": n_cons}


def events_since(conn, since=0, limit=500):
    rows = conn.execute("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (since, limit)).fetchall()
    return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "gid": r["gid"], "detail": json.loads(r["detail"]), "actor": r["actor"], "run_id": r["run_id"]} for r in rows]
