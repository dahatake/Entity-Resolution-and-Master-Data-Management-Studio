"""取り込み(FR-ING-01〜04/06)。スナップショット/差分、冪等性、削除の伝播、スキーマ変更の検知、個人番号の隔離。"""
import csv
import io
import json
import re
import sqlite3
from pathlib import Path

from . import audit
from .util import ApiError, jdump, need, now, sha

MYNUMBER_HEADERS = re.compile(r"マイナンバー|個人番号|my[_\- ]?number|mynumber", re.I)


def mynumber_valid(s: str) -> bool:
    """個人番号(12桁)のチェックデジット検査。P1(末尾)が検査用数字。"""
    if not re.fullmatch(r"\d{12}", s):
        return False
    d = [int(c) for c in s][::-1]  # d[0]=P1 … d[11]=P12
    total = 0
    for n in range(1, 12):
        q = n + 1 if n <= 6 else n - 5
        total += d[n] * q
    r = total % 11
    chk = 0 if r <= 1 else 11 - r
    return chk == d[0]


def detect_mynumber_columns(columns, rows, sample=2000):
    flagged = {}
    for c in columns:
        if MYNUMBER_HEADERS.search(c):
            flagged[c] = "列名が個人番号を示す"
            continue
        vals = [str(r.get(c, "") or "").strip() for r in rows[:sample]]
        vals = [v for v in vals if v]
        if len(vals) >= 5:
            ok = sum(1 for v in vals if mynumber_valid(v.replace("-", "").replace(" ", "")))
            if ok / len(vals) >= 0.6:
                flagged[c] = f"値の{ok}/{len(vals)}件が個人番号のチェックデジットに一致"
    return flagged


def decode_bytes(b: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ApiError(400, "文字コードを判定できません(UTF-8 または CP932 が必要です)")


def parse_csv(text: str):
    text = text.lstrip("\ufeff")
    rd = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(rd)]
    except StopIteration:
        raise ApiError(400, "CSVが空です")
    need(all(header), 400, "空の列名があります")
    need(len(set(header)) == len(header), 400, "列名が重複しています")
    rows = []
    for i, r in enumerate(rd, start=2):
        if not r or all(not x.strip() for x in r):
            continue
        need(len(r) == len(header), 400, f"{i}行目: 列数が見出し({len(header)})と一致しません({len(r)})")
        rows.append(dict(zip(header, r)))
    return header, rows


def read_sqlite(path: Path, table: str):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table or ""):
        raise ApiError(400, "テーブル名が不正です")
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        cur = c.execute(f'SELECT * FROM "{table}"')
        header = [d[0] for d in cur.description]
        rows = [{h: ("" if v is None else str(v)) for h, v in zip(header, r)} for r in cur]
    finally:
        c.close()
    return header, rows


def safe_import_path(import_dir: Path, rel: str) -> Path:
    base = import_dir.resolve()
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        raise ApiError(400, "取り込みディレクトリの外は読めません")
    if not p.is_file():
        raise ApiError(404, f"ファイルがありません: {rel}")
    return p


class SchemaChange(ApiError):
    pass


def schema_hash(columns):
    return sha(jdump(sorted(columns)))


def load(conn, source, columns, rows, actor, mode="snapshot"):
    """1ソースの取り込み。mode='snapshot': 入力に無い既存レコードは削除扱い。'delta': __op 列(I/U/D)で明示する。"""
    sid = source["id"]
    key, truth_col = source["key_column"], source["truth_column"]
    delta = mode == "delta"
    data_cols = [c for c in columns if c != "__op"]
    need(key in data_cols, 400, f"キー列 {key} が入力にありません")
    if truth_col:
        need(truth_col in data_cols, 400, f"正解列 {truth_col} が入力にありません")

    # 個人番号の隔離(FR-ING-06)。キー/正解列が個人番号なら取り込まない。
    quarantine = json.loads(source["quarantine"] or "[]")
    found = detect_mynumber_columns(data_cols, rows)
    for c in found:
        if c in (key, truth_col):
            raise ApiError(400, f"列 {c} は個人番号と判定されたため、キー/正解列に使えません(番号法)", column=c, reason=found[c])
    new_q = sorted(set(quarantine) | set(found))
    if set(new_q) != set(quarantine):
        conn.execute("UPDATE sources SET quarantine=? WHERE id=?", (json.dumps(new_q, ensure_ascii=False), sid))
        audit.append(conn, actor, "mynumber.quarantine", source["name"], {"columns": sorted(found), "reasons": found})
    stored_cols = [c for c in data_cols if c not in new_q and c != truth_col]

    # スキーマ変更の検知(FR-ING-04): 承認されるまで取り込みを止める
    sh = schema_hash(data_cols)
    if source["schema_hash"] and source["schema_hash"] != sh:
        old = set(json.loads(source["columns"] or "[]"))
        diff = {"added": sorted(set(data_cols) - old), "removed": sorted(old - set(data_cols))}
        conn.execute("UPDATE sources SET pending_schema=? WHERE id=?", (jdump({"hash": sh, "columns": data_cols}), sid))
        audit.append(conn, actor, "source.schema_change_blocked", source["name"], diff)
        conn.commit()
        raise SchemaChange(409, "スキーマが変わったため取り込みを停止しました。差分を確認し、承認してください。", diff=diff)

    # キーの検査
    seen = {}
    for i, r in enumerate(rows, start=1):
        k = (r.get(key) or "").strip()
        need(k, 400, f"{i}件目: キー({key})が空です")
        if k in seen and not (delta and (r.get("__op") or "").upper() == "D"):
            raise ApiError(400, f"キー {k!r} が重複しています(同一ソース内のキーは一意である必要があります)")
        seen[k] = r

    ts = now()
    content_hash = sha(jdump([sorted(stored_cols), mode, sorted((k, sha(jdump({c: (r.get(c) or "") for c in stored_cols + [truth_col or ""] if c}))) for k, r in seen.items())]))
    last = conn.execute("SELECT content_hash FROM loads WHERE source_id=? AND status IN ('ok','noop') ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    cur = conn.execute("INSERT INTO loads(source_id,ts,actor,n_insert,n_update,n_delete,n_same,content_hash,status) VALUES(?,?,?,0,0,0,0,?,?)",
                       (sid, ts, actor, content_hash, "running"))
    lid = cur.lastrowid
    existing = {r["skey"]: r for r in conn.execute("SELECT rid,skey,raw_hash,status,truth FROM records WHERE source_id=?", (sid,))}
    ins = upd = dele = same = 0
    for k, r in seen.items():
        op = (r.get("__op") or "").strip().upper() if delta else ""
        raw = {c: (r.get(c) or "").strip() for c in stored_cols}
        h = sha(jdump(raw))
        truth = (r.get(truth_col) or "").strip() if truth_col else None
        ex = existing.get(k)
        if op == "D":
            if ex and ex["status"] == "active":
                conn.execute("UPDATE records SET status='deleted', last_load=? WHERE rid=?", (lid, ex["rid"]))
                conn.execute("INSERT INTO record_history(rid,load_id,op,raw_hash,ts) VALUES(?,?,?,?,?)", (ex["rid"], lid, "delete", ex["raw_hash"], ts))
                dele += 1
            continue
        if not ex:
            cur2 = conn.execute("INSERT INTO records(source_id,skey,raw,raw_hash,truth,first_load,last_load) VALUES(?,?,?,?,?,?,?)",
                                (sid, k, jdump(raw), h, truth, lid, lid))
            conn.execute("INSERT INTO record_history(rid,load_id,op,raw_hash,ts) VALUES(?,?,?,?,?)", (cur2.lastrowid, lid, "insert", h, ts))
            ins += 1
        elif ex["status"] == "deleted" or ex["raw_hash"] != h or (truth is not None and ex["truth"] != truth):
            conn.execute("UPDATE records SET raw=?, raw_hash=?, truth=?, status='active', last_load=? WHERE rid=?", (jdump(raw), h, truth, lid, ex["rid"]))
            conn.execute("DELETE FROM std_values WHERE rid=?", (ex["rid"],))
            conn.execute("INSERT INTO record_history(rid,load_id,op,raw_hash,ts) VALUES(?,?,?,?,?)",
                         (ex["rid"], lid, "reinsert" if ex["status"] == "deleted" else "update", h, ts))
            upd += 1
        else:
            conn.execute("UPDATE records SET last_load=? WHERE rid=?", (lid, ex["rid"]))
            same += 1
    if not delta:
        for k, ex in existing.items():
            if k not in seen and ex["status"] == "active":
                conn.execute("UPDATE records SET status='deleted', last_load=? WHERE rid=?", (lid, ex["rid"]))
                conn.execute("INSERT INTO record_history(rid,load_id,op,raw_hash,ts) VALUES(?,?,?,?,?)", (ex["rid"], lid, "delete", ex["raw_hash"], ts))
                dele += 1
    status = "noop" if (ins + upd + dele == 0) else "ok"
    conn.execute("UPDATE loads SET n_insert=?,n_update=?,n_delete=?,n_same=?,status=? WHERE id=?", (ins, upd, dele, same, status, lid))
    conn.execute("UPDATE sources SET columns=?, schema_hash=?, pending_schema=NULL WHERE id=?", (json.dumps(data_cols, ensure_ascii=False), sh, sid))
    audit.append(conn, actor, "source.load", source["name"], {"load": lid, "insert": ins, "update": upd, "delete": dele, "same": same,
                                                              "mode": mode, "quarantined": sorted(found)})
    conn.commit()
    return {"load_id": lid, "insert": ins, "update": upd, "delete": dele, "same": same, "status": status, "quarantined": found,
            "same_as_previous_input": bool(last and last["content_hash"] == content_hash)}


def ack_schema(conn, source, actor):
    need(source["pending_schema"], 400, "承認待ちのスキーマ変更はありません")
    p = json.loads(source["pending_schema"])
    conn.execute("UPDATE sources SET schema_hash=?, columns=?, pending_schema=NULL WHERE id=?", (p["hash"], json.dumps(p["columns"], ensure_ascii=False), source["id"]))
    audit.append(conn, actor, "source.schema_change_ack", source["name"], {"columns": p["columns"]})
    conn.commit()
