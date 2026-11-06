"""認証・役割ベースのアクセス制御(FR-GOV-04)。ローカル利用を前提に、パスワードはPBKDF2でハッシュ化する。"""
import datetime
import hashlib
import hmac
import json
import secrets

from .util import ApiError, now, sha

ITER = 100_000

# 役割 U1〜U7(PRD 4.1)に対応。admin は運用管理者。
ROLE_LABELS = {
    "admin": "管理者", "owner": "データオーナー(U2)", "steward": "データスチュワード(U1)", "engineer": "データエンジニア(U3)",
    "evaluator": "評価担当(U4)", "legal": "法務・コンプライアンス(U5)", "downstream": "下流システム管理者(U6)", "auditor": "監査担当(U7)",
}
PERMS = {
    "admin": {"*"},
    "owner": {"read", "view_raw", "settings", "task", "review", "unmerge", "adjudicate", "export"},
    "steward": {"read", "view_raw", "review", "unmerge", "task", "label"},
    "engineer": {"read", "view_raw", "config", "ingest", "run", "task", "export"},
    "evaluator": {"read", "view_raw", "label", "adjudicate", "run", "config", "task", "export"},
    "legal": {"read", "purpose", "audit"},
    "downstream": {"read", "export"},
    "auditor": {"read", "audit"},
}


def hash_pw(password: str, salt: str = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITER).hex()
    return salt, h


def perms_of(roles):
    out = set()
    for r in roles:
        out |= PERMS.get(r, set())
    return out


def can(user, perm: str) -> bool:
    p = user["perms"]
    return "*" in p or perm in p


def require(user, perm: str):
    if not user:
        raise ApiError(401, "ログインが必要です")
    if not can(user, perm):
        raise ApiError(403, f"この操作の権限がありません(必要な権限: {perm})")


def create_user(conn, username, password, roles, display=None):
    if not username or len(password) < 8:
        raise ApiError(400, "ユーザー名と8文字以上のパスワードが必要です")
    bad = [r for r in roles if r not in PERMS]
    if bad:
        raise ApiError(400, f"未知の役割: {bad}")
    salt, h = hash_pw(password)
    try:
        conn.execute("INSERT INTO users(username,display,salt,pw_hash,roles,created) VALUES(?,?,?,?,?,?)",
                     (username, display or username, salt, h, json.dumps(sorted(roles)), now()))
    except Exception as e:
        raise ApiError(409, f"ユーザーを作成できません: {e}")


def _user_row_to_dict(r):
    roles = json.loads(r["roles"])
    return {"id": r["id"], "username": r["username"], "display": r["display"], "roles": roles, "perms": perms_of(roles), "active": bool(r["active"])}


def login(conn, username, password, hours=12):
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    # 存在しないユーザーでも同じ計算量を使い、時間差でユーザーの有無を推測されにくくする
    salt = r["salt"] if r else "00" * 16
    _, h = hash_pw(password, salt)
    if not r or not r["active"] or not hmac.compare_digest(h, r["pw_hash"]):
        raise ApiError(401, "ユーザー名またはパスワードが違います")
    token = secrets.token_urlsafe(32)
    exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT INTO sessions(token_hash,user_id,created,expires) VALUES(?,?,?,?)", (sha(token), r["id"], now(), exp))
    return token, _user_row_to_dict(r)


def user_from_token(conn, token):
    if not token:
        return None
    s = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (sha(token),)).fetchone()
    if not s or s["expires"] < now():
        return None
    r = conn.execute("SELECT * FROM users WHERE id=?", (s["user_id"],)).fetchone()
    return _user_row_to_dict(r) if r and r["active"] else None


def logout(conn, token):
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (sha(token),))
