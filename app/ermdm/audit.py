"""追記専用・ハッシュ連鎖の監査ログ(FR-GOV-05、SM8)。"""
import threading

from .util import jdump, now, sha

_LOCK = threading.Lock()
GENESIS = "0" * 64


def append(conn, actor: str, action: str, target: str = "", detail=None) -> int:
    with _LOCK:
        row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        ts = now()
        d = jdump(detail if detail is not None else {})
        h = sha(prev + jdump([ts, actor, action, target, d]))
        cur = conn.execute("INSERT INTO audit_log(ts,actor,action,target,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
                           (ts, actor, action, target, d, prev, h))
        return cur.lastrowid


def verify(conn):
    """連鎖を先頭から再計算する。戻り値: (ok, 件数, 最初に壊れた seq)。"""
    prev, n = GENESIS, 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
        n += 1
        expect = sha(prev + jdump([r["ts"], r["actor"], r["action"], r["target"], r["detail"]]))
        if r["prev_hash"] != prev or r["hash"] != expect:
            return False, n, r["seq"]
        prev = r["hash"]
    return True, n, None
