"""タスク管理(FR-TSK-01〜03)。依存関係(DAG)、着手可否の導出、確認キューのタスク化、SLA。"""
import datetime
import re
from pathlib import Path

from . import audit
from .util import ApiError, need, now

STATUSES = ["todo", "doing", "blocked", "done"]
LABEL = {"todo": "未着手", "doing": "進行中", "blocked": "ブロック", "done": "完了"}
JA2EN = {v: k for k, v in LABEL.items()}


def _deps(conn, tid):
    return [r["dep_id"] for r in conn.execute("SELECT dep_id FROM task_deps WHERE task_id=?", (tid,))]


def has_cycle(conn, tid, new_deps):
    """tid が new_deps に依存すると循環になるか。"""
    graph = {}
    for r in conn.execute("SELECT task_id, dep_id FROM task_deps"):
        graph.setdefault(r["task_id"], set()).add(r["dep_id"])
    graph[tid] = set(new_deps)
    seen, stack = set(), [tid]
    path_stack = set()

    def dfs(x):
        if x in path_stack:
            return True
        if x in seen:
            return False
        seen.add(x)
        path_stack.add(x)
        for y in graph.get(x, ()):
            if dfs(y):
                return True
        path_stack.discard(x)
        return False
    return dfs(tid)


def waiting_on(conn, tid):
    return [r["dep_id"] for r in conn.execute("SELECT d.dep_id FROM task_deps d JOIN tasks t ON t.id=d.dep_id WHERE d.task_id=? AND t.status!='done'", (tid,))]


def row_to_dict(conn, r):
    d = dict(r)
    d["deps"] = _deps(conn, r["id"])
    w = waiting_on(conn, r["id"])
    d["waiting_on"] = w
    d["effective_status"] = "blocked" if (r["status"] in ("todo", "blocked") and w) else r["status"]
    d["ready"] = r["status"] == "todo" and not w
    d["overdue"] = bool(r["due"] and r["status"] != "done" and r["due"] < now())
    return d


def create(conn, actor, data):
    tid = (data.get("id") or "").strip()
    if not tid:
        n = conn.execute("SELECT COUNT(*) c FROM tasks WHERE id LIKE 'T-%'").fetchone()["c"] + 1
        tid = f"T-{n:04d}"
        while conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
            n += 1
            tid = f"T-{n:04d}"
    need(data.get("title"), 400, "タイトルは必須です")
    need(not conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone(), 409, f"タスクID {tid} は既に存在します")
    status = data.get("status", "todo")
    need(status in STATUSES, 400, f"状態は {STATUSES}")
    deps = data.get("deps") or []
    for d in deps:
        need(conn.execute("SELECT 1 FROM tasks WHERE id=?", (d,)).fetchone(), 400, f"依存先 {d} がありません")
    need(not has_cycle(conn, tid, deps), 400, "依存関係が循環します")
    conn.execute("INSERT INTO tasks(id,title,kind,phase,owner,status,priority,due,detail,ref,created,updated,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (tid, data["title"], data.get("kind", "general"), data.get("phase"), data.get("owner"), status, data.get("priority", "M"), data.get("due"),
                  data.get("detail", ""), data.get("ref"), now(), now(), actor))
    for d in deps:
        conn.execute("INSERT OR IGNORE INTO task_deps(task_id,dep_id) VALUES(?,?)", (tid, d))
    audit.append(conn, actor, "task.create", tid, {"title": data["title"], "deps": deps})
    return tid


def update(conn, actor, tid, data):
    r = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    need(r, 404, f"タスク {tid} がありません")
    if "deps" in data:
        deps = data["deps"] or []
        for d in deps:
            need(d != tid and conn.execute("SELECT 1 FROM tasks WHERE id=?", (d,)).fetchone(), 400, f"依存先 {d} が不正です")
        need(not has_cycle(conn, tid, deps), 400, "依存関係が循環します")
        old = sorted(_deps(conn, tid))
        conn.execute("DELETE FROM task_deps WHERE task_id=?", (tid,))
        for d in deps:
            conn.execute("INSERT INTO task_deps(task_id,dep_id) VALUES(?,?)", (tid, d))
        if old != sorted(deps):
            conn.execute("INSERT INTO task_history(task_id,ts,actor,field,old,new) VALUES(?,?,?,?,?,?)", (tid, now(), actor, "deps", ",".join(old), ",".join(sorted(deps))))
    if "status" in data and data["status"] != r["status"]:
        st = data["status"]
        need(st in STATUSES, 400, f"状態は {STATUSES}")
        if st in ("doing", "done"):
            w = waiting_on(conn, tid)
            need(not w, 409, f"先行タスクが未完了のため着手できません: {w}", waiting_on=w)
    for f in ("title", "owner", "status", "priority", "due", "detail", "phase"):
        if f in data and data[f] != r[f]:
            conn.execute(f"UPDATE tasks SET {f}=?, updated=? WHERE id=?", (data[f], now(), tid))
            conn.execute("INSERT INTO task_history(task_id,ts,actor,field,old,new) VALUES(?,?,?,?,?,?)", (tid, now(), actor, f, str(r[f]), str(data[f])))
            audit.append(conn, actor, "task.update", tid, {"field": f, "old": r[f], "new": data[f]})


def list_all(conn, **flt):
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    for k in ("kind", "owner", "status", "phase"):
        if flt.get(k) not in (None, ""):
            sql += f" AND {k}=?"
            args.append(flt[k])
    sql += " ORDER BY CASE WHEN id LIKE 'T%' THEN 0 ELSE 1 END, id"
    return [row_to_dict(conn, r) for r in conn.execute(sql, args)]


def history(conn, tid):
    return [dict(r) for r in conn.execute("SELECT * FROM task_history WHERE task_id=? ORDER BY id", (tid,))]


def import_prd(conn, actor, prd_path: Path):
    """PRD 15章のタスク表を読み込む(担当は仮置きのまま)。既存IDはスキップ。"""
    need(prd_path.is_file(), 404, f"PRD が見つかりません: {prd_path}")
    rows = []
    for ln in prd_path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\| T\d-\d\d \|", ln):
            c = [x.strip() for x in ln.strip().strip("|").split("|")]
            rows.append({"id": c[0], "title": c[1], "phase": int(c[2]), "owner": c[3], "deps": re.findall(r"T\d-\d\d", c[4]), "detail": c[5],
                         "ref": c[6], "status": JA2EN.get(c[7], "todo")})
    n = 0
    for r in rows:
        if not conn.execute("SELECT 1 FROM tasks WHERE id=?", (r["id"],)).fetchone():
            conn.execute("INSERT INTO tasks(id,title,kind,phase,owner,status,priority,detail,ref,created,updated,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (r["id"], r["title"], "prd", r["phase"], r["owner"], r["status"], "M", r["detail"], r["ref"], now(), now(), actor))
            n += 1
    for r in rows:
        for d in r["deps"]:
            conn.execute("INSERT OR IGNORE INTO task_deps(task_id,dep_id) VALUES(?,?)", (r["id"], d))
    audit.append(conn, actor, "task.import_prd", str(prd_path.name), {"parsed": len(rows), "imported": n})
    return {"parsed": len(rows), "imported": n}


def longest_chain(conn):
    graph = {r["id"]: _deps(conn, r["id"]) for r in conn.execute("SELECT id FROM tasks")}
    memo = {}

    def d(x):
        if x not in memo:
            memo[x] = 1 + max([d(y) for y in graph.get(x, []) if y in graph], default=0)
        return memo[x]
    return max([d(x) for x in graph], default=0)


def sla_due(hours):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
