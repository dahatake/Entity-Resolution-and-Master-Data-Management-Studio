"""CLI: python -m ermdm <init|serve|demo|verify|coverage>"""
import argparse
import getpass
import json
import re
import secrets
import sys
from pathlib import Path

from . import audit, auth, db, demo, pipeline
from .util import ApiError


def cmd_init(a):
    from .server import App
    app = App(a.data, verbose=False)
    conn = db.connect(app.db_path)
    created = None
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        pw = a.admin_password or secrets.token_urlsafe(12)
        auth.create_user(conn, "admin", pw, ["admin"], "管理者")
        audit.append(conn, "system", "init", "", {"version": db.CODE_VERSION})
        conn.commit()
        created = pw
    if a.demo_users:
        for role in auth.PERMS:
            if role == "admin" or conn.execute("SELECT 1 FROM users WHERE username=?", (role,)).fetchone():
                continue
            auth.create_user(conn, role, f"demo-{role}-pass", [role], auth.ROLE_LABELS[role])
        conn.commit()
    conn.close()
    print(f"データディレクトリ: {app.data_dir}")
    if created:
        print(f"管理者ユーザー: admin / パスワード: {created}  (この表示は一度だけです。安全な場所に控えてください)")
    if a.demo_users:
        print("デモ用ユーザー(ローカル検証専用。本番では作らないこと): " + ", ".join(f"{r} / demo-{r}-pass" for r in auth.PERMS if r != "admin"))
    return app


def cmd_serve(a):
    app = cmd_init(a)
    app.verbose = True
    srv = app.make_server(a.port)
    print(f"http://127.0.0.1:{a.port}/ で待ち受け中(127.0.0.1 限定)。Ctrl+C で終了。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("終了します")
    finally:
        srv.server_close()


def cmd_demo(a):
    from .server import App
    app = App(a.data, verbose=False)
    conn = db.connect(app.db_path)
    r = demo.load_demo(conn, a.name, "admin")
    rid = pipeline.run_sync(conn, "admin")
    print(json.dumps({"demo": r["demo"], "run_id": rid}, ensure_ascii=False))


def cmd_verify(a):
    from .server import App
    app = App(a.data, verbose=False)
    conn = db.connect(app.db_path)
    ok, n, bad = audit.verify(conn)
    print(f"監査ログ {n} 件: " + ("連鎖は正常です" if ok else f"seq={bad} で連鎖が壊れています(改ざんの疑い)"))
    sys.exit(0 if ok else 1)


def cmd_coverage(a):
    """テストの docstring にある [FR-xxx-nn] タグと、PRD の Phase 1 の要求を突き合わせ、カバレッジ表を生成する。"""
    root = Path(__file__).resolve().parent.parent
    prd = (root.parent / "work" / "prd.md").read_text(encoding="utf-8")
    phase1 = None
    for ln in prd.splitlines():
        if ln.startswith("### Phase 1"):
            phase1 = True
            continue
        if phase1 and ln.startswith("対象:"):
            text = ln
            break
    ids = []
    for m in re.finditer(r"((?:N?FR)-(?:[A-Z]+-)?)(\d\d)((?:〜\d\d)?)((?:/\d\d)*)", text):
        pre, x, rng, sl = m.groups()
        nums = list(range(int(x), int(rng[1:]) + 1)) if rng else [int(x)]
        nums += [int(s) for s in sl.split("/")[1:]]
        ids += [f"{pre}{n:02d}" for n in nums]
    tests = {}
    for f in sorted((root / "tests").glob("test_*.py")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"def (test_\w+)\(self\):\s*\n\s*\"\"\"([^\"]*)\"\"\"", src):
            for tag in re.findall(r"\[((?:N?FR)-[A-Z]*-?\d\d)\]", m.group(2)):
                tests.setdefault(tag, []).append(f"{f.name}::{m.group(1)}")
    manual = {
        "FR-VIZ-01": "ブラウザで手動確認(評価画面: 閾値曲線、ペア/クラスタ切替、コスト比スライダー)。自動テストは無い",
        "FR-VIZ-02": "ブラウザで手動確認(評価画面: ルール別の候補ペア数と真の一致の拾得数、設定画面: 事前見積)。自動テストは無い",
        "FR-VIZ-03": "ブラウザで手動確認(エンティティ画面: 力学レイアウト、辺の強さ、下限未満の辺の破線)。閾値スライダーと橋渡し/最弱リンクの強調は未実装(Phase 2: FR-CLU-03)",
        "NFR-11": "各図に出典・n・確度を表示(ダッシュボード・実行・評価・エンティティ画面)。ブラウザで手動確認。全図への適用は未検証",
        "NFR-12": "表と CSV(評価画面の閾値曲線)を併設。キーボード操作と色以外の符号化は部分的。大規模(1万ノード超)の集約は未実装",
    }
    rows = [{"id": i, "tests": tests.get(i, []), "manual": manual.get(i)} for i in ids]
    covered = sum(1 for r in rows if r["tests"])
    out = {"phase": 1, "required": len(rows), "covered": covered, "uncovered_by_tests": [r["id"] for r in rows if not r["tests"]], "manual_only": [r["id"] for r in rows if not r["tests"] and r["manual"]], "rows": rows,
           "note": "テストのdocstringの [FR-xxx-nn] タグから自動生成。要求を満たすかの最終判断はPRDの受入基準を参照。"}
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "requirements_coverage.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Phase 1 の要求 {len(rows)} 件のうち、テストで検証済み {covered} 件。自動テストが無いもの: {out['uncovered_by_tests']}(うち手動確認のみ: {out['manual_only']})")


def cmd_useradd(a):
    from .server import App
    app = App(a.data, verbose=False)
    conn = db.connect(app.db_path)
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    bad = [r for r in roles if r not in auth.PERMS]
    if bad or not roles:
        sys.exit(f"役割が不正です: {bad or '(空)'}。使える役割: {', '.join(auth.PERMS)}")
    pw = a.password or getpass.getpass("パスワード(8文字以上): ")
    if len(pw) < 8:
        sys.exit("パスワードは8文字以上にしてください")
    try:
        auth.create_user(conn, a.username, pw, roles, a.display)
    except ApiError as e:
        sys.exit(e.message)
    audit.append(conn, "cli", "user.create", a.username, {"roles": roles})
    conn.commit()
    print(f"ユーザー {a.username}(役割: {', '.join(roles)})を作成しました")


def main():
    p = argparse.ArgumentParser(prog="ermdm")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("init", cmd_init), ("serve", cmd_serve), ("demo", cmd_demo), ("verify", cmd_verify), ("coverage", cmd_coverage), ("useradd", cmd_useradd)):
        s = sub.add_parser(name)
        s.add_argument("--data", default="./data", help="データディレクトリ(SQLite と取り込み用フォルダ)")
        s.set_defaults(fn=fn)
        if name == "useradd":
            s.add_argument("username")
            s.add_argument("roles", help="カンマ区切り。例: steward,evaluator")
            s.add_argument("--password", default=None)
            s.add_argument("--display", default=None)
        if name in ("init", "serve"):
            s.add_argument("--admin-password", default=None)
            s.add_argument("--demo-users", action="store_true", help="役割ごとのデモユーザーを作る(ローカル検証専用)")
        if name == "serve":
            s.add_argument("--port", type=int, default=8800)
        if name == "demo":
            s.add_argument("name", choices=["persons", "companies"])
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
