"""確認キューとスチュワードシップ(FR-REV-01〜03)。確認項目はタスクとしても扱う(FR-TSK-03)。"""
from . import audit, tasks
from .db import get_setting
from .util import ApiError, need, now


def queue_estimate(conn, run_id, hi, lo, n_records):
    """閾値の組合せごとの、自動マージ件数・確認キュー件数(FR-REV-02)。決める前に量を見積もる。"""
    need(0 <= lo <= hi <= 1, 400, "0 ≤ 下限 ≤ 自動マージ閾値 ≤ 1 である必要があります")
    auto = conn.execute("SELECT COUNT(*) c FROM pairs WHERE run_id=? AND p>=?", (run_id, hi)).fetchone()["c"]
    q = conn.execute("SELECT COUNT(*) c FROM pairs WHERE run_id=? AND p>=? AND p<?", (run_id, lo, hi)).fetchone()["c"]
    decided = conn.execute("SELECT COUNT(*) c FROM pairs p JOIN review_items r ON r.a=p.a AND r.b=p.b WHERE p.run_id=? AND p.p>=? AND p.p<? AND r.status='decided'",
                           (run_id, lo, hi)).fetchone()["c"]
    minutes = float(get_setting(conn, "minutes_per_review", 2.0))
    open_n = q - decided
    return {"auto_pairs": auto, "review_pairs": q, "already_decided": decided, "open_estimate": open_n,
            "per_1000_records": (open_n / n_records * 1000) if n_records else 0.0, "minutes_per_item_assumed": minutes,
            "hours_estimate": open_n * minutes / 60.0, "assumption": "1件あたりの確認時間は仮の既定値(Phase 0 で実測して置き換える)"}


def sync_queue(conn, run_id, actor, hi, lo):
    """run のレビュー帯(lo ≤ p < hi)を確認項目へ反映する。決定済みの組は再度キューに入れない。"""
    sla = float(get_setting(conn, "review_sla_hours", 72))
    created = 0
    rows = conn.execute("SELECT a,b,p FROM pairs WHERE run_id=? AND p>=? AND p<? ORDER BY p DESC, a, b", (run_id, lo, hi)).fetchall()
    for r in rows:
        ex = conn.execute("SELECT id,status FROM review_items WHERE a=? AND b=?", (r["a"], r["b"])).fetchone()
        if ex:
            conn.execute("UPDATE review_items SET last_run=?, p=? WHERE id=?", (run_id, r["p"], ex["id"]))
            continue
        cur = conn.execute("INSERT INTO review_items(a,b,first_run,last_run,p) VALUES(?,?,?,?,?)", (r["a"], r["b"], run_id, run_id, r["p"]))
        tid = f"R-{cur.lastrowid}"
        conn.execute("UPDATE review_items SET task_id=? WHERE id=?", (tid, cur.lastrowid))
        conn.execute("INSERT INTO tasks(id,title,kind,status,priority,due,detail,ref,created,updated,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, f"確認: レコード {r['a']} と {r['b']}(p={r['p']:.3f})", "review", "todo", "M", tasks.sla_due(sla), "", f"{r['a']},{r['b']}", now(), now(), actor))
        created += 1
    # 帯の外へ出た未決定の項目は、閉じずに残す(閾値を戻したときに復帰できる)。
    return {"new_items": created, "in_band": len(rows)}


def list_items(conn, status=None, assignee=None, limit=200, offset=0):
    sql = "SELECT r.*, t.owner, t.due, t.status tstatus FROM review_items r LEFT JOIN tasks t ON t.id=r.task_id WHERE 1=1"
    args = []
    if status:
        sql += " AND r.status=?"
        args.append(status)
    if assignee:
        sql += " AND t.owner=?"
        args.append(assignee)
    total = conn.execute(sql.replace("SELECT r.*, t.owner, t.due, t.status tstatus", "SELECT COUNT(*) c"), args).fetchone()["c"]
    sql += " ORDER BY r.p DESC, r.id LIMIT ? OFFSET ?"
    rows = [dict(x) for x in conn.execute(sql, args + [limit, offset])]
    for r in rows:
        r["overdue"] = bool(r["status"] == "open" and r["due"] and r["due"] < now())
    return total, rows


def decide(conn, item_id, decision, reason, actor):
    need(decision in ("match", "nonmatch", "unsure"), 400, "判定は match / nonmatch / unsure のいずれか")
    need(reason and reason.strip(), 400, "判定の理由を入力してください(監査のため必須)")
    it = conn.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    need(it, 404, "確認項目がありません")
    conn.execute("UPDATE review_items SET status='decided', decision=?, reason=?, decided_by=?, decided_at=? WHERE id=?", (decision, reason, actor, now(), item_id))
    kind = {"match": "must", "nonmatch": "cannot"}.get(decision)
    if kind:
        other = "cannot" if kind == "must" else "must"
        conn.execute("DELETE FROM constraints WHERE a=? AND b=? AND kind=? AND origin='review'", (it["a"], it["b"], other))
        conn.execute("INSERT OR REPLACE INTO constraints(a,b,kind,origin,actor,reason,ts) VALUES(?,?,?,?,?,?,?)", (it["a"], it["b"], kind, "review", actor, reason, now()))
    if it["task_id"]:
        conn.execute("UPDATE tasks SET status='done', updated=? WHERE id=?", (now(), it["task_id"]))
        conn.execute("INSERT INTO task_history(task_id,ts,actor,field,old,new) VALUES(?,?,?,?,?,?)", (it["task_id"], now(), actor, "status", "todo", "done"))
    audit.append(conn, actor, "review.decide", str(item_id), {"a": it["a"], "b": it["b"], "p": it["p"], "decision": decision, "reason": reason})
    conn.commit()
    return {"id": item_id, "decision": decision, "constraint": kind}


def assign(conn, item_id, owner, actor):
    it = conn.execute("SELECT * FROM review_items WHERE id=?", (item_id,)).fetchone()
    need(it, 404, "確認項目がありません")
    need(conn.execute("SELECT 1 FROM users WHERE username=?", (owner,)).fetchone(), 400, f"ユーザー {owner} がいません")
    old = conn.execute("SELECT owner FROM tasks WHERE id=?", (it["task_id"],)).fetchone()
    conn.execute("UPDATE tasks SET owner=?, updated=? WHERE id=?", (owner, now(), it["task_id"]))
    conn.execute("INSERT INTO task_history(task_id,ts,actor,field,old,new) VALUES(?,?,?,?,?,?)", (it["task_id"], now(), actor, "owner", old["owner"] if old else None, owner))
    audit.append(conn, actor, "review.assign", str(item_id), {"owner": owner})
    conn.commit()
