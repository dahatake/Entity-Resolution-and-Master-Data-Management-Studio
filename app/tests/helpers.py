"""テスト共通。外部依存なし(unittest)。"""
import http.client
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ermdm import auth, db, demo, ingest, pipeline  # noqa: E402
from ermdm.server import App  # noqa: E402

ALL_ROLES = list(auth.PERMS)


def workspace(demo_name=None, run=False, users=False):
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    app = App(tmp.name, verbose=False)
    conn = db.connect(app.db_path)
    auth.create_user(conn, "admin", "adminpass1", ["admin"])
    if users:
        for r in ALL_ROLES:
            if r != "admin":
                auth.create_user(conn, r, f"{r}-pass-1234", [r])
    conn.commit()
    rid = None
    if demo_name:
        demo.load_demo(conn, demo_name, "admin")
    if run:
        rid = pipeline.run_sync(conn, "admin")
    return app, conn, tmp, rid


def add_source(conn, name, rows, key="id", truth=None, personal=0):
    cur = conn.execute("INSERT INTO sources(name,kind,key_column,personal,truth_column,created) VALUES(?,?,?,?,?,datetime('now'))", (name, "csv", key, personal, truth))
    src = conn.execute("SELECT * FROM sources WHERE id=?", (cur.lastrowid,)).fetchone()
    cols = list(rows[0].keys())
    return src, ingest.load(conn, src, cols, [{k: str(v) for k, v in r.items()} for r in rows], "admin")


class Server:
    """実サーバーを空きポートで起動し、HTTPで叩く。"""

    def __init__(self, app):
        self.srv = app.make_server(0)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def client(self):
        return Client(self.port)


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None, host=None, headers=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith("sid="):
            self.cookie = sc.split(";")[0] if len(sc.split(";")[0]) > 4 else None
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        if raw:
            return r.status, payload, hdrs
        try:
            return r.status, json.loads(payload.decode("utf-8") or "null")
        except ValueError:
            return r.status, payload.decode("utf-8", "replace")

    def get(self, path, **k): return self.req("GET", path, **k)
    def post(self, path, body=None, **k): return self.req("POST", path, body if body is not None else {}, **k)
    def put(self, path, body=None, **k): return self.req("PUT", path, body if body is not None else {}, **k)

    def login(self, user, pw):
        return self.post("/api/login", {"username": user, "password": pw})
