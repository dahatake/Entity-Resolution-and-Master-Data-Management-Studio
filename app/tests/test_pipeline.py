import json
import unittest

from helpers import ALL_ROLES, workspace

from ermdm import audit, auth, blocking, clustering, evaluation, governance, pipeline, review, tasks, xref
from ermdm.util import ApiError


class TestPersonsPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.conn, cls.tmp, cls.rid = workspace("persons", run=True)

    def test_blocking_recall_and_cap(self):
        """[FR-BLK-02][FR-BLK-05] 候補ペアの見落とし率(PC)を正解付きで測り、上限超過は生成前に止める。"""
        c = self.conn
        run = c.execute("SELECT * FROM runs WHERE id=?", (self.rid,)).fetchone()
        m = json.loads(run["metrics"])
        self.assertGreater(m["gold"]["pairs_completeness"], 0.6)
        self.assertGreater(m["n_candidates"], 0)
        std = {1: {"n": "a"}, 2: {"n": "a"}, 3: {"n": "a"}}
        rules = [{"name": "r", "keys": [{"field": "n"}]}]
        est = blocking.estimate(rules, std, max_block=500)
        self.assertEqual(est["total_upper"], 3)
        with self.assertRaises(ApiError):
            blocking.generate(rules, std, max_candidates=2)

    def test_fs_precision_at_threshold(self):
        """[FR-MAT-01] Fellegi–Sunter の確率pで、閾値0.9ではペア精度が高い。"""
        curve = evaluation.gold_curve(self.conn, self.rid, [0.5, 0.9])
        rows = {r["t"]: r for r in curve["rows"]}
        self.assertGreater(rows[0.9]["pair"]["precision"], 0.95)
        self.assertGreater(rows[0.5]["pair"]["precision"], 0.9)
        self.assertIn("pairs_completeness", curve)

    def test_explain_returns_field_contributions(self):
        """[FR-MAT-02][NFR-03] ペアの根拠(項目ごとの一致水準と寄与)を返す。"""
        from ermdm.server import explain_pair
        row = self.conn.execute("SELECT a,b,p FROM pairs WHERE run_id=? ORDER BY p DESC LIMIT 1", (self.rid,)).fetchone()
        run = self.conn.execute("SELECT * FROM runs WHERE id=?", (self.rid,)).fetchone()
        ex = explain_pair(self.conn, {"perms": {"*"}}, run, row["a"], row["b"])
        self.assertGreaterEqual(len(ex["explanation"]["fields"]), 2)
        self.assertAlmostEqual(ex["stored_p"], ex["explanation"]["p"], places=6)

    def test_reproducible_result_hash(self):
        """[NFR-02][FR-EVL-08] 同じ入力・設定で再実行すると、結果のハッシュが一致する。"""
        app, c, tmp, r1 = workspace("persons", run=True)
        r2 = pipeline.run_sync(c, "admin")
        h1 = c.execute("SELECT result_hash FROM runs WHERE id=?", (r1,)).fetchone()[0]
        h2 = c.execute("SELECT result_hash FROM runs WHERE id=?", (r2,)).fetchone()[0]
        self.assertTrue(h1)
        self.assertEqual(h1, h2)

    def test_global_ids_stable_across_reruns(self):
        """[FR-XRF-03] 再実行してもグローバルIDは変わらず、イベントは増えない。"""
        before = dict(self.conn.execute("SELECT rid,gid FROM xref").fetchall())
        n_ev = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        pipeline.run_sync(self.conn, "admin")
        after = dict(self.conn.execute("SELECT rid,gid FROM xref").fetchall())
        self.assertEqual(before, after)
        self.assertEqual(n_ev, self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def test_run_records_versions_and_timings(self):
        """[NFR-01][FR-EVL-08] 実行は、入力・設定・モデルの版と段階ごとの時間を記録する。"""
        r = self.conn.execute("SELECT * FROM runs WHERE id=?", (self.rid,)).fetchone()
        self.assertEqual(r["status"], "done")
        self.assertTrue(r["std_version"] and r["code_version"])
        self.assertTrue(json.loads(r["metrics"])["timings"])

    def test_manual_unmerge_persists_across_rerun(self):
        """[FR-XRF-02][FR-XRF-03] 手動の分割は否定制約として残り、再実行でも再統合されない。理由は必須。"""
        c = self.conn
        g = c.execute("SELECT gid,COUNT(*) n FROM xref GROUP BY gid HAVING n>=3 LIMIT 1").fetchone()
        self.assertIsNotNone(g)
        mem = xref.entity_members(c, g["gid"])
        with self.assertRaises(ApiError):
            xref.unmerge(c, g["gid"], [[mem[0]]], "admin", "")
        res = xref.unmerge(c, g["gid"], [[mem[0]]], "admin", "別人のため")
        self.assertEqual(len(res["new_ids"]), 1)
        newg = res["new_ids"][0]
        self.assertEqual(c.execute("SELECT gid FROM xref WHERE rid=?", (mem[0],)).fetchone()[0], newg)
        self.assertEqual(xref.resolve(c, newg)["current"], newg)
        pipeline.run_sync(c, "admin")
        self.assertNotEqual(c.execute("SELECT gid FROM xref WHERE rid=?", (mem[0],)).fetchone()[0], c.execute("SELECT gid FROM xref WHERE rid=?", (mem[1],)).fetchone()[0])
        self.assertTrue(audit.verify(c)[0])

    def test_review_decision_becomes_constraint(self):
        """[FR-REV-03][FR-TSK-03] 確認の判定は must/cannot 制約になり、理由が必須。タスクも完了する。"""
        c = self.conn
        it = c.execute("SELECT * FROM review_items WHERE status='open' ORDER BY id LIMIT 1").fetchone()
        self.assertIsNotNone(it)
        with self.assertRaises(ApiError):
            review.decide(c, it["id"], "nonmatch", " ", "admin")
        review.decide(c, it["id"], "nonmatch", "住所が異なる", "admin")
        k = c.execute("SELECT kind FROM constraints WHERE a=? AND b=?", (it["a"], it["b"])).fetchone()[0]
        self.assertEqual(k, "cannot")
        self.assertEqual(c.execute("SELECT status FROM tasks WHERE id=?", (it["task_id"],)).fetchone()[0], "done")

    def test_review_queue_estimate(self):
        """[FR-REV-02] 閾値を決める前に、確認キューの件数と作業時間を見積もれる(仮の前提を明示)。"""
        e = review.queue_estimate(self.conn, self.rid, 0.9, 0.5, 1000)
        self.assertGreaterEqual(e["review_pairs"], 0)
        self.assertIn("assumption", e)
        with self.assertRaises(ApiError):
            review.queue_estimate(self.conn, self.rid, 0.4, 0.5, 1000)

    def test_cost_optimum_and_pc_separate(self):
        """[FR-EVL-03] 誤統合:見逃しのコスト比で最適な閾値を示す。PCは別に表示する。"""
        curve = evaluation.gold_curve(self.conn, self.rid)
        a = evaluation.cost_optimum(curve["rows"], 1.0)
        b = evaluation.cost_optimum(curve["rows"], 50.0)
        self.assertGreaterEqual(b["best"]["t"], a["best"]["t"])

    def test_cluster_metrics_show_transitive_damage(self):
        """[FR-CLU-01][FR-EVL-02] 推移閉包によるクラスタの混在を、ペア指標とは別に報告する。"""
        curve = evaluation.gold_curve(self.conn, self.rid, [0.5])
        self.assertIn("cluster", curve["rows"][0])
        self.assertIn("impure", curve["rows"][0])
        self.assertIn("added_precision", curve["rows"][0])

    def test_governance_blocks_personal_run_without_purpose(self):
        """[FR-GOV-01] 個人データのソースに有効な利用目的が無いと、実行を拒否する。"""
        c = self.conn
        old = c.execute("SELECT id,personal,purpose_id FROM sources").fetchall()
        c.execute("UPDATE sources SET personal=1, purpose_id=NULL")
        c.commit()
        try:
            with self.assertRaises(ApiError):
                pipeline.create_run(c, "admin")
            self.assertTrue(governance.run_violations(c))
        finally:
            for o in old:
                c.execute("UPDATE sources SET personal=?, purpose_id=? WHERE id=?", (o["personal"], o["purpose_id"], o["id"]))
            c.commit()

    def test_audit_chain_detects_tampering(self):
        """[FR-GOV-05][NFR-05] 監査ログはハッシュで連鎖し、UPDATE/DELETEは拒否される。"""
        c = self.conn
        self.assertTrue(audit.verify(c)[0])
        with self.assertRaises(Exception):
            c.execute("UPDATE audit_log SET actor='x' WHERE seq=1")
        with self.assertRaises(Exception):
            c.execute("DELETE FROM audit_log")


class TestClustering(unittest.TestCase):
    def test_transitive_closure_and_center(self):
        """[FR-CLU-01] 連結成分は鎖でつながる別人も1つにする。センター法は混在を抑える。"""
        nodes = [1, 2, 3, 4]
        edges = [(1, 2, 0.99), (2, 3, 0.9), (3, 4, 0.99)]
        self.assertEqual(len(clustering.cluster(nodes, edges, "components")[0]), 1)
        self.assertGreater(len(clustering.cluster(nodes, edges, "center")[0]), 1)

    def test_cannot_link_enforced(self):
        """[FR-XRF-02] 否定制約があれば、連結していても分ける。"""
        out, removed = clustering.cluster([1, 2, 3], [(1, 2, 0.9), (2, 3, 0.95), (1, 3, 0.5)], "components", cannot=[(1, 3)])
        for cl in out:
            self.assertFalse({1, 3} <= set(cl))


class TestTasks(unittest.TestCase):
    def test_dag_cycle_rejected_and_derived_status(self):
        """[FR-TSK-01][FR-TSK-02] 依存の循環は拒否し、依存先が未完了なら待ち状態になる。"""
        app, c, tmp, _ = workspace()
        tasks.create(c, "admin", {"id": "T1", "title": "a"})
        tasks.create(c, "admin", {"id": "T2", "title": "b", "deps": ["T1"]})
        with self.assertRaises(ApiError):
            tasks.update(c, "admin", "T1", {"deps": ["T2"]})
        t2 = [t for t in tasks.list_all(c) if t["id"] == "T2"][0]
        self.assertEqual(t2["effective_status"], "blocked")
        tasks.update(c, "admin", "T1", {"status": "done"})
        t2 = [t for t in tasks.list_all(c) if t["id"] == "T2"][0]
        self.assertTrue(t2["ready"])

    def test_import_prd(self):
        """[FR-TSK-02] PRDの15章のタスク表を取り込める。"""
        from helpers import ROOT
        app, c, tmp, _ = workspace()
        r = tasks.import_prd(c, "admin", ROOT.parent / "work" / "prd.md")
        self.assertGreaterEqual(r["imported"], 30)


class TestAuthRoles(unittest.TestCase):
    def test_rbac_matrix(self):
        """[FR-GOV-04] 役割ごとに権限が分かれている(管理者以外は管理操作できない等)。"""
        self.assertTrue(auth.can({"perms": auth.perms_of(["admin"])}, "admin"))
        for r in ALL_ROLES:
            if r != "admin":
                self.assertFalse(auth.can({"perms": auth.perms_of([r])}, "admin"), r)
        self.assertTrue(auth.can({"perms": auth.perms_of(["steward"])}, "review"))
        self.assertFalse(auth.can({"perms": auth.perms_of(["downstream"])}, "review"))
        self.assertFalse(auth.can({"perms": auth.perms_of(["downstream"])}, "view_raw"))


if __name__ == "__main__":
    unittest.main()
