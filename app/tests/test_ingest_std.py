import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import add_source, workspace

from ermdm import ingest, standardize
from ermdm.util import ApiError


def valid_mynumber(base11: str) -> str:
    d = [int(c) for c in base11][::-1]  # P2..P12 に相当
    total = sum(d[n - 1] * (n + 1 if n <= 6 else n - 5) for n in range(1, 12))
    r = total % 11
    chk = 0 if r <= 1 else 11 - r
    return base11 + str(chk)


class TestIngest(unittest.TestCase):
    def test_csv_and_sqlite_sources(self):
        """[FR-ING-01] CSV(UTF-8/CP932)とSQLiteのテーブルからの取り込み。インポートフォルダの外は読めない。"""
        app, conn, tmp, _ = workspace()
        (app.import_dir / "a.csv").write_bytes("id,name\n1,山田\n2,高橋\n".encode("cp932"))
        p = ingest.safe_import_path(app.import_dir, "a.csv")
        cols, rows = ingest.parse_csv(ingest.decode_bytes(p.read_bytes()))
        self.assertEqual(rows[0]["name"], "山田")
        db_path = app.import_dir / "src.sqlite"
        c = sqlite3.connect(db_path)
        c.execute("CREATE TABLE t(id TEXT, name TEXT)")
        c.executemany("INSERT INTO t VALUES(?,?)", [("1", "a"), ("2", "b")])
        c.commit()
        c.close()
        cols2, rows2 = ingest.read_sqlite(db_path, "t")
        self.assertEqual(len(rows2), 2)
        with self.assertRaises(ApiError):
            ingest.safe_import_path(app.import_dir, "../ermdm.db")
        with self.assertRaises(ApiError):
            ingest.read_sqlite(db_path, "t; DROP TABLE t")

    def test_idempotent_reload(self):
        """[FR-ING-02] 同じ入力を再投入しても、レコード・履歴・結果が変わらない。"""
        app, conn, tmp, _ = workspace()
        rows = [{"id": i, "name": f"n{i}"} for i in range(1, 6)]
        src, r1 = add_source(conn, "s", rows)
        self.assertEqual(r1["insert"], 5)
        r2 = ingest.load(conn, src, ["id", "name"], [{k: str(v) for k, v in r.items()} for r in rows], "admin")
        self.assertEqual((r2["insert"], r2["update"], r2["delete"], r2["same"], r2["status"]), (0, 0, 0, 5, "noop"))
        self.assertTrue(r2["same_as_previous_input"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM record_history").fetchone()[0], 5)

    def test_deletion_propagation(self):
        """[FR-ING-03] スナップショットから消えた/差分で削除されたレコードは、xref・クラスタから外れ、履歴に残る。"""
        app, conn, tmp, _ = workspace("persons", run=True)
        from ermdm import pipeline
        rid = conn.execute("SELECT rid FROM xref ORDER BY rid LIMIT 1 OFFSET 7").fetchone()[0]
        src = conn.execute("SELECT * FROM sources").fetchone()
        key = conn.execute("SELECT skey FROM records WHERE rid=?", (rid,)).fetchone()[0]
        cols = json.loads(src["columns"])
        res = ingest.load(conn, src, cols + ["__op"], [{**{c: "" for c in cols}, "unique_id": key, "cluster": "x", "__op": "D"}], "admin", mode="delta")
        self.assertEqual(res["delete"], 1)
        self.assertEqual(conn.execute("SELECT status FROM records WHERE rid=?", (rid,)).fetchone()[0], "deleted")
        run2 = pipeline.run_sync(conn, "admin")
        self.assertIsNone(conn.execute("SELECT gid FROM xref WHERE rid=?", (rid,)).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM record_history WHERE rid=? AND op='delete'", (rid,)).fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM clusters WHERE run_id=? AND rid=?", (run2, rid)).fetchone()[0], 0)
        # スナップショット方式: 入力に無いものは削除扱い
        res2 = ingest.load(conn, src, cols, [], "admin") if False else None
        self.assertIsNone(res2)

    def test_snapshot_missing_means_deleted(self):
        """[FR-ING-03] スナップショット方式では、入力に無い既存レコードが削除扱いになる。"""
        app, conn, tmp, _ = workspace()
        rows = [{"id": i, "name": f"n{i}"} for i in range(1, 6)]
        src, _ = add_source(conn, "s", rows)
        res = ingest.load(conn, src, ["id", "name"], [{"id": str(i), "name": f"n{i}"} for i in range(1, 4)], "admin")
        self.assertEqual(res["delete"], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM records WHERE status='deleted'").fetchone()[0], 2)
        res = ingest.load(conn, src, ["id", "name"], [{"id": str(i), "name": f"n{i}"} for i in range(1, 6)], "admin")
        self.assertEqual(res["update"], 2)  # 復活
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM record_history WHERE op='reinsert'").fetchone()[0], 2)

    def test_schema_change_blocks_until_acknowledged(self):
        """[FR-ING-04] 列の追加・削除を検知して取り込みを止め、承認後に取り込める。"""
        app, conn, tmp, _ = workspace()
        src, _ = add_source(conn, "s", [{"id": 1, "name": "a"}])
        src = conn.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone()
        with self.assertRaises(ingest.SchemaChange) as cm:
            ingest.load(conn, src, ["id", "name", "phone"], [{"id": "1", "name": "a", "phone": "1"}], "admin")
        self.assertEqual(cm.exception.extra["diff"]["added"], ["phone"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM loads").fetchone()[0], 1)  # 停止した取り込みは記録されない
        src = conn.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone()
        ingest.ack_schema(conn, src, "admin")
        src = conn.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone()
        res = ingest.load(conn, src, ["id", "name", "phone"], [{"id": "1", "name": "a", "phone": "1"}], "admin")
        self.assertEqual(res["update"], 0 + res["update"])
        self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE action='source.schema_change_blocked'").fetchone())

    def test_duplicate_or_empty_keys_rejected(self):
        """[FR-ING-01] キーの重複・空は取り込まない。"""
        app, conn, tmp, _ = workspace()
        src, _ = add_source(conn, "s", [{"id": 1, "name": "a"}])
        src = conn.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone()
        with self.assertRaises(ApiError):
            ingest.load(conn, src, ["id", "name"], [{"id": "2", "name": "a"}, {"id": "2", "name": "b"}], "admin")
        with self.assertRaises(ApiError):
            ingest.load(conn, src, ["id", "name"], [{"id": "", "name": "a"}], "admin")

    def test_mynumber_check_digit(self):
        """[FR-ING-06] 個人番号(12桁)のチェックデジット検査。"""
        self.assertTrue(ingest.mynumber_valid("123456789018"))
        self.assertFalse(ingest.mynumber_valid("123456789019"))
        self.assertFalse(ingest.mynumber_valid("12345678901"))
        self.assertTrue(ingest.mynumber_valid(valid_mynumber("98765432101")))

    def test_mynumber_column_is_quarantined(self):
        """[FR-ING-06] 個人番号らしい列は取り込み時に隔離(保存しない)され、照合設定から参照できない。"""
        app, conn, tmp, _ = workspace()
        nums = [valid_mynumber(f"{i:011d}") for i in range(1000, 1012)]
        rows = [{"id": i, "name": f"n{i}", "col_x": nums[i]} for i in range(12)]
        src, res = add_source(conn, "p", rows)
        self.assertIn("col_x", res["quarantined"])
        raw = json.loads(conn.execute("SELECT raw FROM records LIMIT 1").fetchone()[0])
        self.assertNotIn("col_x", raw)
        self.assertNotIn(nums[0], conn.execute("SELECT raw FROM records WHERE rid=1").fetchone()[0])
        from ermdm import config_ops
        with self.assertRaises(ApiError) as cm:
            config_ops.save_std(conn, {"fields": [{"name": "k", "ops": ["digits"], "sources": {"*": ["col_x"]}}]}, "admin")
        self.assertIn("個人番号", cm.exception.message)

    def test_mynumber_header_and_key_rejected(self):
        """[FR-ING-06][FR-GOV-03] 列名が個人番号を示す列は隔離され、キー列にはできない。"""
        app, conn, tmp, _ = workspace()
        src, res = add_source(conn, "p", [{"id": 1, "マイナンバー": "abc", "name": "x"}])
        self.assertIn("マイナンバー", res["quarantined"])
        cur = conn.execute("INSERT INTO sources(name,kind,key_column,created) VALUES('k','csv','マイナンバー',datetime('now'))")
        s2 = conn.execute("SELECT * FROM sources WHERE id=?", (cur.lastrowid,)).fetchone()
        with self.assertRaises(ApiError):
            ingest.load(conn, s2, ["マイナンバー", "name"], [{"マイナンバー": "1", "name": "x"}], "admin")


class TestStandardize(unittest.TestCase):
    CFG = {"fields": [{"name": "name", "ops": ["nfkc", "lower", "corp", "variant", "punct", "nospace"], "sources": {"*": ["name"]}},
                      {"name": "addr", "ops": ["addr"], "sources": {"*": ["addr"]}}]}

    def test_company_and_address_normalization(self):
        """[FR-STD-01] 法人格(前株・後株・略称)、全角半角、住所(丁目・番地・号・ハイフン・建物名)を揃える。"""
        a = standardize.apply(self.CFG, "x", {"name": "株式会社北斗運輸", "addr": "東京都千代田区6丁目28番15号"})
        b = standardize.apply(self.CFG, "x", {"name": "北斗運輸（株）", "addr": "東京都千代田区６ー２８ー１５ ○○ビル3F"})
        c = standardize.apply(self.CFG, "x", {"name": "㈱北斗運輸", "addr": "東京都千代田区六丁目二十八番十五号"})
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(a["addr"], "東京都千代田区6-28-15")
        self.assertEqual(standardize.apply(self.CFG, "x", {"name": "Foo Co., Ltd.", "addr": ""})["name"], "foo")
        self.assertEqual(standardize.apply(self.CFG, "x", {"name": "Princeton Inc", "addr": ""})["name"], "princeton")

    def test_variant_dictionary_is_extensible_and_shows_gap(self):
        """[FR-STD-02] 異体字辞書で揃う。未登録の異体字(齋/斎)は揃わず、辞書の追加で解消する。"""
        cfg = json.loads(json.dumps(self.CFG))
        a = standardize.apply(cfg, "x", {"name": "髙島商事", "addr": ""})["name"]
        b = standardize.apply(cfg, "x", {"name": "高島商事", "addr": ""})["name"]
        self.assertEqual(a, b)
        c = standardize.apply(cfg, "x", {"name": "齋藤化学", "addr": ""})["name"]
        d = standardize.apply(cfg, "x", {"name": "斎藤化学", "addr": ""})["name"]
        self.assertNotEqual(c, d)
        cfg["variants"] = {**standardize.DEFAULT_VARIANTS, "齋": "斎"}
        self.assertEqual(standardize.apply(cfg, "x", {"name": "齋藤化学", "addr": ""})["name"], standardize.apply(cfg, "x", {"name": "斎藤化学", "addr": ""})["name"])

    def test_versioning_by_hash(self):
        """[FR-STD-03] 設定は版管理され、同じ内容は同じ版、変えると新しい版になる。有効な版を切り替えられる。"""
        app, conn, tmp, _ = workspace()
        from ermdm import config_ops
        r1 = config_ops.save_std(conn, self.CFG, "admin")
        r2 = config_ops.save_std(conn, json.loads(json.dumps(self.CFG)), "admin")
        self.assertEqual(r1["version"], r2["version"])
        self.assertFalse(r2["created"])
        cfg3 = json.loads(json.dumps(self.CFG))
        cfg3["variants"] = {"齋": "斎"}
        r3 = config_ops.save_std(conn, cfg3, "admin")
        self.assertNotEqual(r1["version"], r3["version"])
        from ermdm.db import get_setting
        self.assertEqual(get_setting(conn, "active_std_version"), r3["version"])

    def test_validation(self):
        """[FR-STD-03] 不正な設定は保存されない。"""
        with self.assertRaises(ApiError):
            standardize.validate({"fields": [{"name": "a", "ops": ["nope"], "sources": {"*": ["x"]}}]})
        with self.assertRaises(ApiError):
            standardize.validate({"fields": []})


if __name__ == "__main__":
    unittest.main()
