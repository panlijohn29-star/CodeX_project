import io
import unittest
from unittest.mock import MagicMock, patch

from openpyxl import Workbook

import features.hts_code_update as m


def workbook_bytes(rows, header=("IMP_EXP", "FREE_FIELD1", "FREE_FIELD2", "FREE_FIELD3", "FREE_FIELD4")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ParseTests(unittest.TestCase):
    def test_columns_any_order_and_extra_ignored(self):
        data = workbook_bytes([("I", "0101210000", "HORSES", "NO", None)])
        parsed = m.parse_workbook(data)
        self.assertEqual(parsed, [(2, ["0101210000", "HORSES", "NO", None])])

    def test_missing_column(self):
        data = workbook_bytes([], header=("FREE_FIELD1", "FREE_FIELD2"))
        with self.assertRaises(ValueError) as ctx:
            m.parse_workbook(data)
        self.assertIn("FREE_FIELD3", str(ctx.exception))

    def test_blank_rows_skipped(self):
        data = workbook_bytes([(None, None, None, None, None), ("E", "0101210000", "A", None, None)])
        self.assertEqual(len(m.parse_workbook(data)), 1)

    def test_not_a_workbook(self):
        with self.assertRaises(ValueError):
            m.parse_workbook(b"not excel")


class ValidateTests(unittest.TestCase):
    def test_normalize_code(self):
        self.assertEqual(m.normalize_code("0101.21.0000"), ("0101210000", None))
        self.assertEqual(m.normalize_code(101210000), ("0101210000", "padded with leading zeros"))
        self.assertEqual(m.normalize_code(101210000.0)[0], "0101210000")
        self.assertIsNone(m.normalize_code("")[0])
        self.assertIsNone(m.normalize_code("ABC1234567")[0])
        self.assertIsNone(m.normalize_code("01012100001")[0])

    def test_validate_rows(self):
        parsed = [
            (2, ["0101210000", "HORSES", "NO", ""]),
            (3, ["0101210000", "DUP", None, None]),
            (4, ["bad", "X", None, None]),
            (5, ["101290000", "PADDED", "KG", "KG"]),
            (6, ["0101300000", "x" * 201, None, None]),
        ]
        valid, problems, padded = m.validate_rows(parsed)
        self.assertEqual([r["FREE_FIELD1"] for r in valid], ["0101210000", "0101290000"])
        self.assertIsNone(valid[0]["FREE_FIELD4"])
        self.assertEqual(padded, 1)
        self.assertEqual([p["row"] for p in problems], [3, 4, 6])

class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        patcher = patch.object(m, "STAGE_DIR", __import__("pathlib").Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def rows(self):
        return [
            {"row": 2, "FREE_FIELD1": "0101210000", "FREE_FIELD2": "A", "FREE_FIELD3": "KG", "FREE_FIELD4": None},
            {"row": 3, "FREE_FIELD1": "0101290000", "FREE_FIELD2": "B2", "FREE_FIELD3": "NO", "FREE_FIELD4": None},
        ]

    def test_execute_truncates_then_loads_with_today_remark(self):
        token = m._stage(self.rows())
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"n": 10}, {"n": 2}]
        with patch.object(m, "_connect", return_value=connection):
            result = m.execute_payload(token, "tester")
        self.assertEqual((result["loaded"], result["deleted"]), (2, 10))
        executed = [call[0][0] for call in cursor.execute.call_args_list]
        self.assertTrue(any(sql.startswith("TRUNCATE TABLE s_sys_dict_outside_code_hts202501") for sql in executed))
        self.assertFalse(any("DELETE" in sql for sql in executed))
        self.assertLess([i for i, s in enumerate(executed) if s.startswith("TRUNCATE")][0],
                        len(executed))
        insert_sql, insert_args = cursor.executemany.call_args[0]
        self.assertIn("REMARK", insert_sql)
        today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(result["remark"], today)
        self.assertEqual(insert_args[0][:3], (0, 2016110115490001, "0101210000"))
        self.assertTrue(all(args[6] == today for args in insert_args))
        connection.commit.assert_called_once()
        with self.assertRaises(ValueError):
            m._load_stage(token)

    def test_execute_rolls_back_on_error(self):
        token = m._stage(self.rows())
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"n": 1}
        cursor.executemany.side_effect = RuntimeError("boom")
        with patch.object(m, "_connect", return_value=connection), \
                patch.object(m, "today_text", return_value="2026-11-24"):
            with self.assertRaises(RuntimeError):
                m.execute_payload(token, "tester")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_bad_token(self):
        for token in ("", "../x", "0" * 32, None):
            with self.assertRaises(ValueError):
                m.execute_payload(token, "tester")


def census_line(code, desc="DESC", unit1="KG", unit2=""):
    return code.ljust(14) + "SHORT".ljust(50) + desc.ljust(160) + unit1.ljust(8) + unit2.ljust(8) + "00150     10140\r\n"


class CensusTextTests(unittest.TestCase):
    def test_parse_fixed_width(self):
        content = (census_line("0101210000", "HORSES, LIVE", "NO") + "\r\n" + census_line("0102210010", "CATTLE", "KG", "NO")
                   + " " * 14 + "orphan description\r\n" + " " * 20 + "\r\n").encode("latin-1")
        rows, skipped = m.parse_census_text(content, "EXP")
        self.assertEqual(rows, [("EXP line 1", ["0101210000", "HORSES, LIVE", "NO", None]),
                                ("EXP line 3", ["0102210010", "CATTLE", "KG", "NO"])])
        self.assertEqual(skipped, 1)

    def test_combine_exp_wins_and_sorted(self):
        exp = [("EXP line 1", ["0200000000", "exp", "NO", None]), ("EXP line 2", ["0100000000", "exp", "NO", None])]
        imp = [("IMP line 1", ["0200000000", "imp", "KG", None]), ("IMP line 2", ["0150000000", "imp", "KG", None])]
        combined, imp_only, overlap = m.combine_census(exp, imp)
        self.assertEqual([values[0] for _, values in combined], ["0100000000", "0150000000", "0200000000"])
        self.assertEqual(dict((v[0], v[1]) for _, v in combined)["0200000000"], "exp")
        self.assertEqual((imp_only, overlap), (1, 1))

    def test_excel_header_aliases(self):
        data = workbook_bytes([("EXP", "0101210000", "HORSES", "NO", None)], header=("IMP_EXP", "hs", "lalpha", "auq1", "AUQ2"))
        self.assertEqual(m.parse_workbook(data), [(2, ["0101210000", "HORSES", "NO", None])])

    def test_preview_from_text_files(self):
        class Up:
            def __init__(self, name, data):
                self.filename, self._d = name, data
            def read(self, n):
                return self._d[:n]
        exp = Up("exp-code.txt", (census_line("0101210000", "A", "NO")).encode("latin-1"))
        imp = Up("imp-code.txt", (census_line("0101210000", "A2", "KG") + census_line("0101290000", "B", "KG")).encode("latin-1"))
        with patch.object(m, "_connect", return_value=MagicMock()), \
                patch.object(m, "fetch_current_codes", return_value=["0101210000", "0999999999"]), \
                patch.object(m, "_stage", return_value="t" * 32):
            result = m.preview_payload(None, exp, imp)
        self.assertEqual((result["source"], result["to_load"], result["exp_rows"], result["imp_only"], result["overlap"]),
                         ("census_text", 2, 1, 1, 1))
        self.assertEqual((result["new_codes"], result["dropped_codes"], result["token"]), (1, 1, "t" * 32))
        with self.assertRaises(ValueError):
            m.preview_payload(None, exp, None)

class DiffTests(unittest.TestCase):
    def test_diff_batches(self):
        old = [
            {"FREE_FIELD1": "A", "FREE_FIELD2": "same", "FREE_FIELD3": "NO", "FREE_FIELD4": None},
            {"FREE_FIELD1": "B", "FREE_FIELD2": "old", "FREE_FIELD3": "KG", "FREE_FIELD4": None},
            {"FREE_FIELD1": "GONE", "FREE_FIELD2": "x", "FREE_FIELD3": None, "FREE_FIELD4": None},
        ]
        new = [
            {"FREE_FIELD1": "A", "FREE_FIELD2": "same", "FREE_FIELD3": "NO", "FREE_FIELD4": ""},
            {"FREE_FIELD1": "B", "FREE_FIELD2": "new", "FREE_FIELD3": "KG", "FREE_FIELD4": None},
            {"FREE_FIELD1": "NEW", "FREE_FIELD2": "y", "FREE_FIELD3": None, "FREE_FIELD4": None},
        ]
        added, removed, changed = m.diff_batches(old, new)
        self.assertEqual(added, ["NEW"])
        self.assertEqual(removed, ["GONE"])
        self.assertEqual(changed, [{"code": "B", "changes": {"FREE_FIELD2": {"old": "old", "new": "new"}}}])

    def test_id_range(self):
        low, high = m._id_range(__import__("datetime").datetime(2026, 11, 24))
        self.assertEqual(low, 261124000000001)
        self.assertEqual(high, 261124999999999)


class PublishTests(unittest.TestCase):
    def plan(self, **extra):
        plan = {
            "remark": "2026-11-24", "old_remark": "2026-07-29", "old_count": 2, "id_low": 261124000000001,
            "added": [], "removed": [], "changed": [],
            "new_rows": [
                {"FREE_FIELD1": "0101210000", "FREE_FIELD2": "A", "FREE_FIELD3": "NO", "FREE_FIELD4": None, "REMARK": "2026-11-24"},
                {"FREE_FIELD1": "0101290000", "FREE_FIELD2": "B", "FREE_FIELD3": "NO", "FREE_FIELD4": None, "REMARK": "2026-11-24"},
                {"FREE_FIELD1": "0101300000", "FREE_FIELD2": "C", "FREE_FIELD3": "NO", "FREE_FIELD4": None, "REMARK": "2026-11-24"},
            ],
        }
        plan.update(extra)
        return plan

    def run_publish(self, plan, rowcount=2, inserted=3, args=("2026-11-24", "2026-07-29", 2, 3)):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = rowcount
        cursor.fetchone.return_value = {"n": inserted}
        with patch.object(m, "_connect", return_value=connection), \
                patch.object(m, "_publish_plan", return_value=plan):
            try:
                result = m.publish_execute_payload(*args, "tester")
            except Exception as exc:
                return connection, cursor, exc
        return connection, cursor, result

    def test_publish_expires_then_inserts(self):
        connection, cursor, result = self.run_publish(self.plan())
        self.assertEqual((result["expired"], result["inserted"]), (2, 3))
        executed = [call[0][0] for call in cursor.execute.call_args_list]
        self.assertTrue(executed[0].startswith("UPDATE s_sys_dict_outside_code SET B_OCEAN_EXPORT = NULL"))
        self.assertNotIn("REMARK =", executed[0].split("WHERE")[0])
        sql, rows = cursor.executemany.call_args[0]
        self.assertIn("INSERT INTO s_sys_dict_outside_code", sql)
        self.assertEqual(rows[0][0], 261124000000001)
        self.assertEqual(rows[2][0], 261124000000003)
        self.assertEqual(rows[0][2:6], (1, 1, 1, 1))
        self.assertEqual(rows[0][6], "2026-11-24")
        connection.commit.assert_called_once()

    def test_publish_rejects_changed_preview(self):
        connection, cursor, exc = self.run_publish(self.plan(old_count=5))
        self.assertIsInstance(exc, ValueError)
        connection.rollback.assert_called_once()
        cursor.executemany.assert_not_called()

    def test_publish_rolls_back_on_expire_count_mismatch(self):
        connection, cursor, exc = self.run_publish(self.plan(), rowcount=1)
        self.assertIsInstance(exc, RuntimeError)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()

    def test_publish_rolls_back_on_insert_count_mismatch(self):
        connection, cursor, exc = self.run_publish(self.plan(), inserted=2)
        self.assertIsInstance(exc, RuntimeError)
        connection.commit.assert_not_called()

    def test_plan_rejects_existing_batch(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"FREE_FIELD1": "A", "FREE_FIELD2": "x", "FREE_FIELD3": None,
                                         "FREE_FIELD4": None, "REMARK": "2026-11-24"}]
        cursor.fetchone.return_value = {"n": 1}
        with self.assertRaises(ValueError) as ctx:
            m._publish_plan(connection)
        self.assertIn("already exists", str(ctx.exception))

    def test_plan_rejects_mixed_remarks_and_empty(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with self.assertRaises(ValueError):
            m._publish_plan(connection)
        cursor.fetchall.return_value = [{"FREE_FIELD1": "A", "REMARK": "x"}, {"FREE_FIELD1": "B", "REMARK": "y"}]
        with self.assertRaises(ValueError):
            m._publish_plan(connection)

if __name__ == "__main__":
    unittest.main()
