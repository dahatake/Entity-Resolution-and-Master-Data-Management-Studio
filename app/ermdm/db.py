"""SQLite スキーマと接続。"""
import sqlite3

from .util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display TEXT, salt TEXT NOT NULL,
  pw_hash TEXT NOT NULL, roles TEXT NOT NULL, active INTEGER DEFAULT 1, created TEXT);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT, expires TEXT);
CREATE TABLE IF NOT EXISTS audit_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, target TEXT,
  detail TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, uri TEXT,
  key_column TEXT NOT NULL, personal INTEGER DEFAULT 0, pseudonymized INTEGER DEFAULT 0, purpose_id INTEGER,
  columns TEXT, schema_hash TEXT, pending_schema TEXT, truth_column TEXT, quarantine TEXT DEFAULT '[]', created TEXT);
CREATE TABLE IF NOT EXISTS loads(id INTEGER PRIMARY KEY, source_id INTEGER, ts TEXT, actor TEXT, n_insert INT, n_update INT,
  n_delete INT, n_same INT, content_hash TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS records(rid INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, skey TEXT NOT NULL, raw TEXT NOT NULL,
  raw_hash TEXT, status TEXT DEFAULT 'active', truth TEXT, first_load INT, last_load INT, UNIQUE(source_id, skey));
CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE TABLE IF NOT EXISTS record_history(id INTEGER PRIMARY KEY, rid INT, load_id INT, op TEXT, raw_hash TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS std_configs(version INTEGER PRIMARY KEY AUTOINCREMENT, config TEXT, hash TEXT, created TEXT,
  created_by TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS std_values(rid INT, version INT, fields TEXT, PRIMARY KEY(rid, version));
CREATE TABLE IF NOT EXISTS models(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, matcher TEXT, config TEXT, params TEXT,
  hash TEXT, std_version INT, seed INT, trained TEXT, trained_by TEXT, stats TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, status TEXT, mode TEXT, config TEXT,
  config_hash TEXT, data_hash TEXT, seed INT, std_version INT, model_id INT, metrics TEXT, result_hash TEXT, error TEXT,
  finished TEXT, code_version TEXT);
CREATE TABLE IF NOT EXISTS pairs(run_id INT, a INT, b INT, p REAL, w REAL, rules TEXT, gam TEXT, PRIMARY KEY(run_id, a, b)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS clusters(run_id INT, rid INT, cidx INT, PRIMARY KEY(run_id, rid)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cluster_info(run_id INT, cidx INT, gid INT, size INT, n_edges INT, n_rejected INT, n_transitive INT,
  min_p REAL, impure_truth INT, PRIMARY KEY(run_id, cidx)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS global_ids(gid INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT, successor INT, predecessor INT,
  created_seq INT, created TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS xref(rid INTEGER PRIMARY KEY, gid INT NOT NULL, since_seq INT);
CREATE INDEX IF NOT EXISTS idx_xref_gid ON xref(gid);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, gid INT, detail TEXT, actor TEXT, run_id INT);
CREATE TABLE IF NOT EXISTS constraints(id INTEGER PRIMARY KEY AUTOINCREMENT, a INT, b INT, kind TEXT, origin TEXT, actor TEXT,
  reason TEXT, ts TEXT, UNIQUE(a, b, kind));
CREATE TABLE IF NOT EXISTS review_items(id INTEGER PRIMARY KEY AUTOINCREMENT, a INT, b INT, first_run INT, last_run INT, p REAL,
  status TEXT DEFAULT 'open', decision TEXT, reason TEXT, decided_by TEXT, decided_at TEXT, task_id TEXT, UNIQUE(a, b));
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT, kind TEXT, phase INT, owner TEXT, status TEXT, priority TEXT,
  due TEXT, detail TEXT, ref TEXT, created TEXT, updated TEXT, created_by TEXT);
CREATE TABLE IF NOT EXISTS task_deps(task_id TEXT, dep_id TEXT, PRIMARY KEY(task_id, dep_id));
CREATE TABLE IF NOT EXISTS task_history(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts TEXT, actor TEXT, field TEXT, old TEXT, new TEXT);
CREATE TABLE IF NOT EXISTS purposes(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, purpose TEXT, data_items TEXT, user_scope TEXT,
  manager TEXT, notice_method TEXT, joint_use INTEGER DEFAULT 1, registered_by TEXT, ts TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS label_samples_meta(sample_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INT, seed INT, spec TEXT,
  created TEXT, created_by TEXT);
CREATE TABLE IF NOT EXISTS label_samples(sample_id INT, a INT, b INT, stratum TEXT, p REAL, PRIMARY KEY(sample_id, a, b)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS labels(id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id INT, a INT, b INT, labeler TEXT, label INT, ts TEXT,
  UNIQUE(sample_id, a, b, labeler));
CREATE TABLE IF NOT EXISTS adjudications(sample_id INT, a INT, b INT, label INT, by TEXT, ts TEXT, PRIMARY KEY(sample_id, a, b));
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""

CODE_VERSION = "1.0.0-phase1"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT 1 FROM meta WHERE key='created'").fetchone():
        conn.execute("INSERT INTO meta(key,value) VALUES('created',?)", (now(),))
    conn.commit()


def get_setting(conn, key, default=None):
    import json
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(conn, key, value, actor="system"):
    import json
    conn.execute("INSERT INTO settings(key,value,updated,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "value=excluded.value, updated=excluded.updated, updated_by=excluded.updated_by",
                 (key, json.dumps(value, ensure_ascii=False), now(), actor))
