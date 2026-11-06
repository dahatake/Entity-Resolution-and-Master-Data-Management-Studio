import io
import json
import unittest
from contextlib import redirect_stderr

from helpers import Server, add_source, workspace

from ermdm import blocking, evaluation, matching, pipeline, xref
from ermdm.util import ApiError


class TestPhase1Remaining(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.conn, cls.tmp, cls.rid = workspace("persons", run=True)

    def test_blocking_union_of_rules(self):
        """[FR-BLK-01] 複数のブロッキングルールの和集合を候補にし、各ペアがどのルールで拾われたかを残す。"""
        std = {1: {"a": "x", "b": "p"}, 2: {"a": "x", "b": "q"}, 3: {"a": "y", "b": "p"}}
        rules = [{"name": "A", "keys": [{"field": "a"}]}, {"name": "B", "keys": [{"field": "b"}]}]
        cand, est = blocking.generate(rules, std)
        self.assertEqual(set(cand), {(1, 2), (1, 3)})
        self.assertEqual(cand[(1, 2)], [0])
        self.assertEqual(cand[(1, 3)], [1])
        self.assertEqual(len(est["rules"]), 2)

    def test_hybrid_deterministic_plus_probabilistic(self):
        """[FR-MAT-01] 決定的ルール(信頼できるキー)と確率モデルを併用でき、ルール一致は確率の下限になる。"""
        cfg = {"matcher": "hybrid", "fields": [{"name": "n", "cmp": "name"}, {"name": "d", "cmp": "date"}], "rules": [{"name": "same-id", "fields": ["id"], "p": 0.999}]}
        m = matching.make(cfg, {"lambda": 0.1, "m": {"n": [0.7, 0.15, 0.1, 0.05], "d": [0.6, 0.1, 0.1, 0.1, 0.1]}, "u": {"n": [0.01, 0.02, 0.07, 0.9], "d": [0.001, 0.005, 0.01, 0.05, 0.934]}})
        p, w, g, hits = m.score({"n": "aaa", "d": "2000-01-01", "id": "Z1"}, {"n": "bbb", "d": "1999-05-05", "id": "Z1"})
        self.assertGreaterEqual(p, 0.999)
        self.assertEqual(hits, ("same-id",))
        p2, *_ = m.score({"n": "aaa", "d": "2000-01-01", "id": "Z1"}, {"n": "bbb", "d": "1999-05-05", "id": "Z2"})
        self.assertLess(p2, 0.5)

    def test_matcher_plugin_and_model_versioning(self):
        """[FR-MAT-04] マッチャを登録して差し替えられ、モデルは設定とパラメータのハッシュで版管理される。"""
        class Always(matching.Matcher):
            name = "always"
            def score(self, fa, fb): return 1.0, 10.0, (), ()
            def explain(self, fa, fb): return {"p": 1.0, "fields": []}
        matching.register_matcher("always", Always)
        try:
            self.assertEqual(matching.make({"matcher": "always"}, None).score({}, {})[0], 1.0)
        finally:
            matching.MATCHERS.pop("always")
        h1 = matching.model_hash({"a": 1}, {"p": 1})
        self.assertEqual(h1, matching.model_hash({"a": 1}, {"p": 1}))
        self.assertNotEqual(h1, matching.model_hash({"a": 1}, {"p": 2}))
        models = self.conn.execute("SELECT hash FROM models").fetchall()
        self.assertTrue(models and all(len(m["hash"]) == 64 for m in models))

    def test_impure_clusters_detected(self):
        """[FR-CLU-02] クラスタ内に、下限未満(否定)のペアを含む不純クラスタを数えて警告する。"""
        m = json.loads(self.conn.execute("SELECT metrics FROM runs WHERE id=?", (self.rid,)).fetchone()["metrics"])
        self.assertIn("n_flagged_clusters", m)
        n = self.conn.execute("SELECT COUNT(*) FROM cluster_info WHERE run_id=? AND n_rejected>0", (self.rid,)).fetchone()[0]
        self.assertEqual(n, m["n_flagged_clusters"])

    def test_three_bands_by_threshold(self):
        """[FR-REV-01] 自動マージ/人手確認/破棄の3帯域を閾値で構成し、帯域の件数が確率と一致する。"""
        m = json.loads(self.conn.execute("SELECT metrics FROM runs WHERE id=?", (self.rid,)).fetchone()["metrics"])
        thr = pipeline.pipeline_config(self.conn)["thresholds"]
        c = self.conn
        auto = c.execute("SELECT COUNT(*) FROM pairs WHERE run_id=? AND p>=?", (self.rid, thr["auto"])).fetchone()[0]
        rev = c.execute("SELECT COUNT(*) FROM pairs WHERE run_id=? AND p>=? AND p<?", (self.rid, thr["review_low"], thr["auto"])).fetchone()[0]
        self.assertEqual((m["bands"]["auto"], m["bands"]["review"]), (auto, rev))
        with self.assertRaises(ApiError):
            pipeline.validate_pipeline({**pipeline.pipeline_config(c), "thresholds": {"auto": 0.4, "review_low": 0.6}})

    def test_xref_does_not_modify_source_data(self):
        """[FR-XRF-01] xref(ソースID↔グローバルID)を別表で持ち、元データのレコードを変更しない。"""
        c = self.conn
        before = c.execute("SELECT COUNT(*), SUM(LENGTH(raw)) FROM records").fetchone()[:]
        pipeline.run_sync(c, "admin")
        self.assertEqual(before, c.execute("SELECT COUNT(*), SUM(LENGTH(raw)) FROM records").fetchone()[:])
        n = c.execute("SELECT COUNT(*) FROM xref x JOIN records r ON r.rid=x.rid WHERE r.status='active'").fetchone()[0]
        self.assertEqual(n, c.execute("SELECT COUNT(*) FROM records WHERE status='active'").fetchone()[0])

    def test_unmerge_propagates_change_events(self):
        """[FR-XRF-02] アンマージは下流向けの変更イベント(split/update)を出し、差分取得できる。後継IDも追える。"""
        app, c, tmp, _ = workspace("persons", run=True)
        g = c.execute("SELECT gid FROM xref GROUP BY gid HAVING COUNT(*)>=3 LIMIT 1").fetchone()[0]
        mem = xref.entity_members(c, g)
        seq = c.execute("SELECT MAX(seq) FROM events").fetchone()[0]
        res = xref.unmerge(c, g, [[mem[0]]], "admin", "別人")
        evs = xref.events_since(c, seq)
        self.assertEqual({e["type"] for e in evs}, {"split", "update"})
        self.assertEqual(c.execute("SELECT predecessor FROM global_ids WHERE gid=?", (res["new_ids"][0],)).fetchone()[0], g)

    def test_merge_retires_id_with_successor(self):
        """[FR-XRF-03] 統合では若いIDが廃止され、後継IDへの対応で旧IDからも現在のIDを引ける。"""
        app, c, tmp, _ = workspace()
        rows = [{"id": i, "name": n} for i, n in enumerate(["aaa bbb", "aaa bbb", "ccc ddd"], 1)]
        src, _ = add_source(c, "s", rows)
        rids = [r[0] for r in c.execute("SELECT rid FROM records ORDER BY rid")]
        xref.reconcile(c, 0, [[rids[0]], [rids[1]], [rids[2]]], "admin")
        g0, g1 = [c.execute("SELECT gid FROM xref WHERE rid=?", (r,)).fetchone()[0] for r in rids[:2]]
        self.assertNotEqual(g0, g1)
        xref.reconcile(c, 0, [[rids[0], rids[1]], [rids[2]]], "admin")
        cur = xref.resolve(c, max(g0, g1))
        self.assertEqual(cur["current"], min(g0, g1))
        self.assertEqual(c.execute("SELECT gid FROM xref WHERE rid=?", (rids[1],)).fetchone()[0], min(g0, g1))

    def test_wilson_interval(self):
        """[FR-EVL-02] 適合率などの比率にWilson 95%信頼区間を付ける。"""
        lo, hi = evaluation.wilson(90, 100)
        self.assertAlmostEqual(lo, 0.8256, places=3)
        self.assertAlmostEqual(hi, 0.9448, places=3)
        self.assertEqual(evaluation.wilson(0, 0), (0.0, 1.0))

    def test_pseudonymized_source_is_personal_data(self):
        """[FR-GOV-02] 仮名加工した情報も個人情報として扱い、利用目的が無ければ実行を拒否する。"""
        app, c, tmp, _ = workspace()
        c.execute("INSERT INTO sources(name,kind,key_column,personal,pseudonymized,created) VALUES('p','csv','id',0,1,datetime('now'))")
        src = c.execute("SELECT * FROM sources WHERE name='p'").fetchone()
        from ermdm import ingest
        ingest.load(c, src, ["id", "name"], [{"id": "1", "name": "a"}], "admin")
        from ermdm import governance
        viol, _ = governance.run_violations(c)
        self.assertTrue(any("p" in v["message"] for v in viol))

    def test_cli_useradd(self):
        """[FR-GOV-04] CLI でユーザーを追加でき、役割の不正・重複は拒否される。監査ログに残る。"""
        import argparse
        from ermdm import __main__ as cli, auth
        app, c, tmp, _ = workspace()
        ns = argparse.Namespace(data=tmp.name, username="taro", roles="steward,evaluator", password="taro-pass-123", display=None)
        cli.cmd_useradd(ns)
        u = c.execute("SELECT roles FROM users WHERE username='taro'").fetchone()
        self.assertEqual(json.loads(u["roles"]), ["evaluator", "steward"])
        self.assertTrue(c.execute("SELECT 1 FROM audit_log WHERE action='user.create' AND target='taro'").fetchone())
        for bad in (argparse.Namespace(**{**vars(ns), "roles": "nope"}), argparse.Namespace(**vars(ns)), argparse.Namespace(**{**vars(ns), "username": "x", "password": "short"})):
            with self.assertRaises(SystemExit):
                cli.cmd_useradd(bad)

    def test_structured_logs(self):
        """[NFR-08] リクエストごとに構造化ログ(JSON1行)を出し、実行指標を保存する。"""
        app, c, tmp, _ = workspace(users=True)
        app.verbose = True
        buf = io.StringIO()
        srv = Server(app)
        try:
            with redirect_stderr(buf):
                cl = srv.client()
                cl.login("admin", "adminpass1")
                cl.get("/api/status")
        finally:
            srv.stop()
        lines = [json.loads(x) for x in buf.getvalue().splitlines() if x.startswith("{")]
        self.assertTrue(any(l["path"] == "/api/status" and l["status"] == 200 and "ms" in l for l in lines))

    def test_review_items_are_tasks_with_sla_and_reassign(self):
        """[FR-TSK-03] 確認キューの項目はタスクとしても存在し、期限(SLA)が付き、再割当できる。"""
        from ermdm import review
        c = self.conn
        it = c.execute("SELECT * FROM review_items WHERE status='open' ORDER BY id LIMIT 1").fetchone()
        t = c.execute("SELECT * FROM tasks WHERE id=?", (it["task_id"],)).fetchone()
        self.assertEqual(t["kind"], "review")
        self.assertTrue(t["due"])
        from ermdm import auth
        if not c.execute("SELECT 1 FROM users WHERE username='steward'").fetchone():
            auth.create_user(c, "steward", "steward-pass-1", ["steward"])
        review.assign(c, it["id"], "steward", "admin")
        self.assertEqual(c.execute("SELECT owner FROM tasks WHERE id=?", (it["task_id"],)).fetchone()[0], "steward")
        with self.assertRaises(ApiError):
            review.assign(c, it["id"], "nobody", "admin")

    def test_stdlib_only_and_no_outbound_network(self):
        """[NFR-06][NFR-07][NFR-04] 外部パッケージに依存せず(環境固定が不要)、外部へ通信するコードを持たない(個人データは組織外へ出ない)。"""
        import re, sys
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "ermdm"
        std = set(sys.stdlib_module_names)
        bad_imports, outbound = [], []
        for f in root.glob("*.py"):
            src = f.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", src, re.M):
                top = m.group(1)
                if top not in std and top != "ermdm":
                    bad_imports.append((f.name, top))
            if re.search(r"urllib\.request|http\.client|requests|socket\.create_connection|smtplib|ftplib", src):
                outbound.append(f.name)
        self.assertEqual(bad_imports, [])
        self.assertEqual(outbound, [])


if __name__ == "__main__":
    unittest.main()
