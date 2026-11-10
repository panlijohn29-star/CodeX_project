import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock

from pydantic import ValidationError

# The backend reads its data directory when first imported, so it is imported in setUpClass (not here) and
# dropped again afterwards; otherwise it would claim the directory before other test modules set theirs.
db = extractor = main = pipeline = validate = None
_TEMP = None

HEADER = [
    {"key": "invoice_no", "label": "Invoice No", "type": "text", "required": True},
    {"key": "date", "label": "Date", "type": "date", "required": False},
    {"key": "po", "label": "PO", "type": "text", "required": False},
]


def tpl_values(threshold=0, **extra):
    return main._template_values(main.Template(
        name="Vendor A", header_fields=HEADER, auto_approve_score=threshold, **extra))


def result(header=None):
    return {"data": {"header": header if header is not None else {"invoice_no": "INV-1", "date": "2026-10-01"},
                     "items": []},
            "uncertain": [], "notes": "", "model_used": "mock/model"}


class AutoApproveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global db, extractor, main, pipeline, validate, _TEMP
        _TEMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.env = mock.patch.dict(os.environ, {"PDF_EXTRACT_DATA_DIR": os.path.join(_TEMP.name, "pdf_extract")})
        cls.env.start()
        for name in [m for m in sys.modules if m.startswith("pdf_extract.backend")]:
            del sys.modules[name]
        main = importlib.import_module("pdf_extract.backend.main")
        db, extractor, pipeline, validate = (importlib.import_module(f"pdf_extract.backend.{n}")
                                             for n in ("db", "extractor", "pipeline", "validate"))

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        for name in [m for m in sys.modules if m.startswith("pdf_extract.backend")]:
            del sys.modules[name]
        _TEMP.cleanup()

    def setUp(self):
        self.db_file = os.path.join(_TEMP.name, f"{self._testMethodName}.db")
        patcher = mock.patch.object(db, "DB_PATH", self.db_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()

    def run_pipeline(self, threshold, res, has_text=1):
        tid = db.insert("templates", tpl_values(threshold))
        did = db.insert("documents", {"filename": "a.pdf", "stored_path": "a.pdf", "page_count": 1,
                                      "has_text": has_text, "status": "pending"})
        with mock.patch.object(extractor, "extract", return_value=res):
            pipeline._process(did, tid)
        return db.get("documents", did), db.get("templates", tid)

    def confidence(self, doc, tpl):
        return validate.confidence(doc, tpl)

    def test_threshold_must_be_off_or_between_50_and_100(self):
        for bad in (-1, 1, 49, 101):
            with self.assertRaises(ValidationError, msg=bad):
                main.Template(name="x", auto_approve_score=bad)
        for ok in (0, 50, 95, 100):
            self.assertEqual(main.Template(name="x", auto_approve_score=ok).auto_approve_score, ok)

    def test_default_is_off_and_nothing_is_skipped(self):
        doc, _ = self.run_pipeline(0, result())
        self.assertEqual((doc["status"], doc["auto_approved"], doc["verified"]), ("done", 0, 0))

    def test_score_at_or_above_threshold_skips_review_but_keeps_the_real_score(self):
        # one of three header fields is empty, so the score is below 100 (no human check involved)
        doc, tpl = self.run_pipeline(95, result())
        self.assertEqual((doc["auto_approved"], doc["verified"]), (1, 0))
        c = self.confidence(doc, tpl)
        self.assertLess(c["score"], 100)
        self.assertGreaterEqual(c["score"], 95)
        self.assertIn("Auto-approved", c["reasons"][0]["en"])

    def test_score_below_threshold_still_needs_a_person(self):
        doc, tpl = self.run_pipeline(99, result())
        self.assertEqual(doc["auto_approved"], 0)
        self.assertLess(self.confidence(doc, tpl)["score"], 99)

    def test_an_error_is_never_auto_approved_even_with_a_low_threshold(self):
        doc, _ = self.run_pipeline(50, result({"invoice_no": "", "date": "2026-10-01", "po": "P1"}))
        self.assertTrue(any(i["level"] == "error" for i in doc["issues"]))
        self.assertEqual(doc["auto_approved"], 0)

    def test_failed_sum_check_blocks_auto_approval(self):
        template = {"auto_approve_score": 50, "header_fields": HEADER, "item_fields": [{"key": "amt"}]}
        base = {"data": {"header": {"invoice_no": "A", "date": "2026-10-01", "po": "P"}, "items": [{"amt": 1}]},
                "has_text": 1}
        ok = {**base, "issues": []}
        bad = {**base, "issues": [{"path": "header.total", "level": "warning", "kind": "sum", "msg": {}}]}
        self.assertTrue(validate.should_auto_approve(template, ok))
        self.assertFalse(validate.should_auto_approve(template, bad))

    def test_duplicate_notice_does_not_cost_points_but_other_warnings_do(self):
        full = {"invoice_no": "A", "date": "2026-10-01", "po": "P"}
        template = {"header_fields": HEADER, "item_fields": [], "checks": []}
        base = {"status": "done", "has_text": 1, "verified": 0, "data": {"header": full, "items": []}}
        dup = {"path": "header.invoice_no", "level": "warning", "kind": "duplicate",
               "msg": {"zh": "可能重复：与文件“a.pdf”的Invoice No相同（A）", "en": "Possible duplicate: ..."}}
        legacy_dup = {k: v for k, v in dup.items() if k != "kind"}  # stored before issues carried a kind
        other = {"path": "header.date", "level": "warning", "msg": {"zh": "日期格式无法识别", "en": "bad date"}}

        self.assertEqual(validate.confidence({**base, "issues": []}, template)["score"], 100)
        for d in (dup, legacy_dup):
            c = validate.confidence({**base, "issues": [d]}, template)
            self.assertEqual(c["score"], 100)
            self.assertTrue(any("duplicate" in r["en"].lower() for r in c["reasons"]))
        self.assertEqual(validate.confidence({**base, "issues": [dup, other]}, template)["score"], 93)

    def test_duplicate_document_gets_full_score_yet_is_not_auto_approved(self):
        full = {"invoice_no": "INV-1", "date": "2026-10-01", "po": "P1"}
        tid = db.insert("templates", tpl_values(95, unique_field="invoice_no"))
        ids = []
        for _ in range(2):
            did = db.insert("documents", {"filename": "a.pdf", "stored_path": "a.pdf", "page_count": 1,
                                          "has_text": 1, "status": "pending"})
            with mock.patch.object(extractor, "extract", return_value=result(full)):
                pipeline._process(did, tid)
            ids.append(did)
        second = db.get("documents", ids[1])
        self.assertTrue(any(validate.is_duplicate(i) for i in second["issues"]))
        self.assertEqual(validate.confidence(second, db.get("templates", tid))["score"], 100)
        self.assertEqual(second["auto_approved"], 0)

    def test_human_save_replaces_auto_approval(self):
        doc, _ = self.run_pipeline(95, result())
        self.assertEqual(doc["auto_approved"], 1)
        data = doc["data"]
        saved = main.save_result(doc["id"], main.SaveReq(data=data, verified=False))
        self.assertEqual((saved["verified"], saved["auto_approved"]), (0, 0))

        db.update("documents", doc["id"], {"auto_approved": 1})
        saved = main.save_result(doc["id"], main.SaveReq(data=data, verified=True))
        self.assertEqual((saved["verified"], saved["auto_approved"]), (1, 0))
        self.assertEqual(validate.confidence(saved, db.get("templates", saved["template_id"]))["score"], 100)

    def test_rule_change_that_fails_an_auto_approved_document_revokes_it(self):
        doc, tpl = self.run_pipeline(95, result())
        self.assertEqual(doc["auto_approved"], 1)
        stricter = [{**f, "required": True} if f["key"] == "po" else f for f in HEADER]
        main.update_template(tpl["id"], main.Template(name="Vendor A", header_fields=stricter, auto_approve_score=95))
        self.assertEqual(db.get("documents", doc["id"])["auto_approved"], 0)

    def test_unrelated_template_edit_keeps_auto_approval(self):
        doc, tpl = self.run_pipeline(95, result())
        main.update_template(tpl["id"], main.Template(name="Renamed", header_fields=HEADER, auto_approve_score=95))
        self.assertEqual(db.get("documents", doc["id"])["auto_approved"], 1)

    def test_auto_approved_documents_are_listed_and_counted(self):
        doc, tpl = self.run_pipeline(95, result())
        row = next(d for d in main.list_documents() if d["id"] == doc["id"])
        self.assertEqual((row["auto_approved"], row["verified"]), (1, 0))
        self.assertLess(row["confidence"]["score"], 100)
        counts = next(t for t in main.list_templates() if t["id"] == tpl["id"])
        self.assertEqual((counts["docs"], counts["verified"], counts["auto_approved"]), (1, 0, 1))

    def test_reprocessing_starts_from_not_approved(self):
        doc, tpl = self.run_pipeline(95, result())
        with mock.patch.object(pipeline, "executor") as ex:
            pipeline.submit(doc["id"], tpl["id"])
        self.assertEqual(db.get("documents", doc["id"])["auto_approved"], 0)
        ex.return_value.submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
