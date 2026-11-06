"""法令・ガバナンス(FR-GOV-01/02/03)。法的助言ではなく、確認すべき事項の登録と、実行前の統制を提供する。"""
import json

from . import audit
from .util import ApiError, need, now


def create_purpose(conn, actor, data):
    need(data.get("name") and data.get("purpose"), 400, "名称と利用目的は必須です")
    need(data.get("manager"), 400, "管理責任者の氏名または名称は必須です(法27条5項3号)")
    items = data.get("data_items") or []
    need(isinstance(items, list) and items, 400, "共同して利用する個人データの項目(1件以上)は必須です")
    need(data.get("user_scope"), 400, "共同利用する者の範囲は必須です")
    need(data.get("notice_method"), 400, "本人への通知または容易に知り得る状態に置く方法は必須です")
    cur = conn.execute("INSERT INTO purposes(name,purpose,data_items,user_scope,manager,notice_method,joint_use,registered_by,ts) VALUES(?,?,?,?,?,?,?,?,?)",
                       (data["name"], data["purpose"], json.dumps(items, ensure_ascii=False), data["user_scope"], data["manager"], data["notice_method"],
                        1 if data.get("joint_use", True) else 0, actor, now()))
    audit.append(conn, actor, "purpose.create", str(cur.lastrowid), {"name": data["name"]})
    conn.commit()
    return cur.lastrowid


def list_purposes(conn):
    out = []
    for r in conn.execute("SELECT * FROM purposes ORDER BY id"):
        d = dict(r)
        d["data_items"] = json.loads(d["data_items"])
        out.append(d)
    return out


def personal_sources(conn):
    return conn.execute("SELECT * FROM sources WHERE personal=1 OR pseudonymized=1").fetchall()


def run_violations(conn, std_cfg=None):
    """実行前の統制。個人データ(仮名加工情報を含む)を含むソースは、有効な利用目的の登録が無いと実行できない。"""
    v = []
    for s in personal_sources(conn):
        p = conn.execute("SELECT * FROM purposes WHERE id=? AND active=1", (s["purpose_id"],)).fetchone() if s["purpose_id"] else None
        if not p:
            v.append({"code": "no_purpose", "source": s["name"], "message": f"ソース {s['name']} は個人データを含みますが、有効な利用目的(共同利用の登録)が紐づいていません。"})
        if s["pseudonymized"]:
            v.append({"code": "pseudonymized_is_personal", "source": s["name"], "level": "info",
                      "message": f"ソース {s['name']} は仮名加工情報として登録されています。照合で元の個人に戻せる状態のため、個人情報として扱います(第三者提供は原則不可)。"})
    if std_cfg:
        from . import standardize
        q = set()
        for s in conn.execute("SELECT name, quarantine FROM sources"):
            q.update(json.loads(s["quarantine"] or "[]"))
        bad = sorted(standardize.referenced_columns(std_cfg) & q)
        if bad:
            v.append({"code": "forbidden_key", "message": f"標準化設定が、個人番号として隔離された列を参照しています: {bad}(番号法により照合キーに使えません)"})
    return [x for x in v if x.get("level") != "info"], [x for x in v if x.get("level") == "info"]
