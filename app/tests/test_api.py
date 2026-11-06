import unittest

from helpers import Server, workspace


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app, cls.conn, cls.tmp, cls.rid = workspace("companies", run=True, users=True)
        cls.srv = Server(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def login(self, user, pw=None):
        c = self.srv.client()
        st, body = c.login(user, pw or ("adminpass1" if user == "admin" else f"{user}-pass-1234"))
        self.assertEqual(st, 200, body)
        return c

    def test_auth_required_and_bad_login(self):
        """[FR-GOV-04] 未ログインはAPIを使えず、誤ったパスワードは拒否する。"""
        c = self.srv.client()
        self.assertEqual(c.get("/api/status")[0], 401)
        self.assertEqual(c.login("admin", "wrong")[0], 401)

    def test_host_and_origin_checks(self):
        """[NFR-04] DNSリバインディングとクロスサイト要求を拒否する(Host/Origin検査)。"""
        c = self.login("admin")
        self.assertEqual(c.get("/api/status", host="evil.example.com")[0], 403)
        self.assertEqual(c.post("/api/tasks", {"title": "x"}, headers={"Origin": "http://evil.example.com"})[0], 403)
        self.assertEqual(c.get("/api/status")[0], 200)

    def test_security_headers_and_cookie(self):
        """[NFR-04] CSPを付け、Cookieは HttpOnly / SameSite=Strict。"""
        c = self.srv.client()
        st, _, h = c.req("GET", "/", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("default-src 'self'", h.get("content-security-policy", ""))
        import http.client, json
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port)
        conn.request("POST", "/api/login", json.dumps({"username": "admin", "password": "adminpass1"}), {"Content-Type": "application/json", "Host": f"127.0.0.1:{self.srv.port}"})
        r = conn.getresponse()
        r.read()
        sc = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)

    def test_static_path_traversal_blocked(self):
        """[NFR-04] 静的ファイル配信はweb/の外を返さない。"""
        c = self.srv.client()
        for p in ("/../ermdm/db.py", "/..%2fermdm%2fdb.py", "/%2e%2e/ermdm/db.py"):
            st, body, _ = c.req("GET", p, raw=True)
            self.assertNotEqual(st, 200, p)

    def test_non_json_post_rejected(self):
        """[NFR-04] 書き込みはJSONのみ受け付ける。"""
        c = self.login("admin")
        st, _ = c.req("POST", "/api/tasks", None, headers={"Content-Type": "text/plain"})
        self.assertIn(st, (400, 415))

    def test_rbac_enforced_by_api(self):
        """[FR-GOV-04] 役割に無い操作は403(下流利用者は実行・分割・設定変更ができない)。"""
        d = self.login("downstream")
        self.assertEqual(d.post("/api/runs", {})[0], 403)
        self.assertEqual(d.post("/api/entities/1/unmerge", {"parts": [[1]], "reason": "x"})[0], 403)
        self.assertEqual(d.get("/api/audit")[0], 403)
        self.assertEqual(d.get("/api/users")[0], 403)
        s = self.login("steward")
        self.assertEqual(s.get("/api/users")[0], 403)
        self.assertEqual(self.login("auditor").get("/api/audit")[0], 200)

    def test_raw_masking_for_roles_without_view_raw(self):
        """[FR-GOV-04][NFR-04] 生データの閲覧権限が無い役割には、レコードの値がマスクされる。"""
        a = self.login("admin").get("/api/records?limit=3")[1]
        d = self.login("downstream").get("/api/records?limit=3")[1]
        self.assertNotIn("●●●", str(a))
        self.assertIn("●●●", str(d))

    def test_entities_resolve_and_export(self):
        """[FR-XRF-01][FR-XRF-03] グローバルIDの解決(統合後の追跡)とCSV/JSONLの出力。"""
        c = self.login("admin")
        st, ents = c.get("/api/entities?limit=5")
        self.assertEqual(st, 200)
        gid = (ents["items"] if isinstance(ents, dict) else ents)[0]["gid"]
        st, r = c.get(f"/api/gid/{gid}/resolve")
        self.assertEqual((st, r["status"]), (200, "active"))
        st, body, h = c.req("GET", "/api/export/xref.csv", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("global_id", body.decode("utf-8-sig").splitlines()[0])
        st, body, h = c.req("GET", "/api/export/events.jsonl", raw=True)
        self.assertEqual(st, 200)

    def test_explain_endpoint(self):
        """[FR-MAT-02][NFR-03] 根拠表示API。"""
        c = self.login("admin")
        st, pairs = c.get(f"/api/runs/{self.rid}/pairs?limit=1")
        it = pairs["items"][0]
        st, ex = c.get(f"/api/explain?run={self.rid}&a={it['a']}&b={it['b']}")
        self.assertEqual(st, 200)
        self.assertTrue(ex["explanation"]["fields"])

    def test_eval_summary_never_standalone_accuracy(self):
        """[FR-EVL-02][FR-EVL-04] 評価要約に単独のAccuracyを出さない。PCと前提を併記する。"""
        c = self.login("admin")
        st, ev = c.get(f"/api/eval/summary?run={self.rid}")
        self.assertEqual(st, 200)
        self.assertNotIn("accuracy", str(ev["gold"]).lower())
        self.assertIn("単独のAccuracy", ev["definitions"]["Accuracy"])
        self.assertIn("pairs_completeness", str(ev))
        self.assertEqual(self.login("evaluator").post("/api/labels", {"sample_id": "x", "a": 1, "b": 2, "label": "match"})[0], 400)

    def test_labeling_workflow_blind_and_kappa(self):
        """[FR-EVL-01] ラベル付けは確率を見せない盲検。2人のラベルで一致度(κ)を出し、不一致は裁定できる。"""
        ev = self.login("evaluator")
        st, s = ev.post("/api/labels/samples", {"run_id": self.rid, "per_stratum": 5})
        self.assertEqual(st, 200, s)
        sid = s["sample_id"] if "sample_id" in s else s["id"]
        st, nxt = ev.get(f"/api/labels/samples/{sid}/next?limit=3")
        self.assertEqual(st, 200)
        items = nxt["items"] if isinstance(nxt, dict) else nxt
        self.assertTrue(items)
        self.assertNotIn('"p"', str(items))
        a = self.login("steward")
        for it in items:
            self.assertEqual(ev.post("/api/labels", {"sample_id": sid, "a": it["a"], "b": it["b"], "label": 1})[0], 200)
            self.assertEqual(a.post("/api/labels", {"sample_id": sid, "a": it["a"], "b": it["b"], "label": 0})[0], 200)
        st, cons = ev.get(f"/api/labels/samples/{sid}/consensus")
        self.assertEqual(st, 200)
        self.assertIn("kappa", str(cons).lower())

    def test_task_api_and_cycle(self):
        """[FR-TSK-01][FR-TSK-02] タスクAPI(作成・依存・循環拒否・先行未完了の着手拒否)。"""
        c = self.login("admin")
        self.assertEqual(c.post("/api/tasks", {"id": "X1", "title": "a"})[0], 200)
        self.assertEqual(c.post("/api/tasks", {"id": "X2", "title": "b", "deps": ["X1"]})[0], 200)
        self.assertEqual(c.put("/api/tasks/X1", {"deps": ["X2"]})[0], 400)
        self.assertEqual(c.put("/api/tasks/X2", {"status": "doing"})[0], 409)
        self.assertEqual(c.put("/api/tasks/X1", {"status": "done"})[0], 200)
        self.assertEqual(c.put("/api/tasks/X2", {"status": "doing"})[0], 200)

    def test_requirements_coverage_endpoint(self):
        """ 要求のカバレッジをAPIで確認できる。"""
        st, r = self.login("admin").get("/api/requirements")
        self.assertEqual(st, 200)

    def test_audit_verify_api(self):
        """[FR-GOV-05] 監査ログの検証API。"""
        st, r = self.login("auditor").get("/api/audit/verify")
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])

    def test_purpose_registration_by_legal_role(self):
        """[FR-GOV-01] 法務の役割が、画面と同じ形式(項目は配列、必須6項目)で利用目的を登録できる。必須が欠けると拒否される。"""
        c = self.login("legal")
        body = {"name": "n", "purpose": "p", "data_items": ["氏名", "住所"], "user_scope": "営業部", "manager": "部長", "notice_method": "公表"}
        self.assertEqual(c.post("/api/purposes", body)[0], 200)
        bad = dict(body); bad.pop("manager")
        self.assertEqual(c.post("/api/purposes", bad)[0], 400)

    def test_phase0_open_items_visible(self):
        """ Phase 0 の未決事項(仮の既定値)をステータスAPIで示す。"""
        st, r = self.login("admin").get("/api/status")
        self.assertEqual(st, 200)
        self.assertIn("phase0_open", r)


if __name__ == "__main__":
    unittest.main()
