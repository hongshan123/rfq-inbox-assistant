"""Actual Streamlit widget execution. Does not make real model calls."""
import json
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class AppChecks(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data/local-inbox/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="app-test-", dir=str(root))
        self.patch = patch.dict(os.environ, {"RFQ_CASE_STORAGE": self.temp.name})
        self.patch.start()

    def tearDown(self):
        Path(self.temp.name).resolve().relative_to((Path(__file__).resolve().parents[1] / "data/local-inbox/test-runs").resolve())
        self.patch.stop()
        self.temp.cleanup()

    def app(self):
        return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=90).run()

    def test_examples_model_missing_manual_edit_export_and_cell_evidence(self):
        with patch.dict(os.environ, {"RFQ_MODEL_BASE_URL": "", "RFQ_MODEL_NAME": "", "RFQ_MODEL_API_KEY": ""}):
            at = self.app()
            self.assertFalse(at.exception)
            self.assertTrue(any("Model not configured" in w.value for w in at.warning))
            at.button(key="open_example").click().run()
            self.assertFalse(at.exception)
            self.assertTrue(at.button(key="run_model").disabled)
            customer = next(x for x in at.text_input if x.label == "Customer Name")
            customer.set_value("Reviewed Demo Customer").run()
            at.button(key="confirm_review").click().run()
            self.assertFalse(at.exception)
            result = at.session_state["confirmed"]
            self.assertEqual(result["fields"]["customer_name"]["value"], "Reviewed Demo Customer")
            root = Path(at.session_state["case"]["storage_dir"])
            payload = json.loads((root / "exports/review.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["result"]["fields"]["customer_name"]["value"], "Reviewed Demo Customer")
            # Unsaved edits must disable the export UI rather than export stale/draft values.
            next(x for x in at.text_input if x.label == "Customer Name").set_value("Unsaved change").run()
            self.assertFalse(at.get("download_button") and any("review.json" in getattr(x, "label", "") for x in at.get("download_button")))
            at.selectbox(key="example").select("C · Quantity conflict (100 / 120)")
            at.button(key="open_example").click().run()
            self.assertFalse(at.exception)
            self.assertTrue(any("Conflicting source quantities" in e.value for e in at.error))
            buttons = [b for b in at.button if b.label == "View evidence"]
            buttons[-1].click().run()
            self.assertFalse(at.exception)
            self.assertEqual(next(s.value for s in at.selectbox if s.label == "Worksheet"), "RFQ Items")
            self.assertEqual(next(s.value for s in at.selectbox if s.label == "Inspect cell"), "E4")

    def test_invoice_template_and_pdf_page_view(self):
        at = self.app()
        for example in ("Synthetic invoice PDF email", "Blank manufacturing RFQ template", "Historical Narrogin RFQ email"):
            at.selectbox(key="example").select(example)
            at.button(key="open_example").click().run()
            self.assertFalse(at.exception, [e.message for e in at.exception])
            if example.startswith("Synthetic"):
                self.assertEqual(next(s.value for s in at.selectbox if s.label == "Review as"), "invoice")
                self.assertTrue(any(i.label == "Supplier Name" for i in at.text_input))
                self.assertTrue(any(i.label == "Buyer Name" for i in at.text_input))
            if example.startswith("Blank"):
                self.assertTrue(any("Template structure only" in i.value for i in at.info))
        pdf = next(d for d in at.session_state["case"]["documents"] if d["pages"])
        key = "source_" + at.session_state["case"]["case_id"]
        at.selectbox(key=key).select(pdf["document_id"]).run()
        next(s for s in at.selectbox if s.label == "PDF page").select(20).run()
        self.assertFalse(at.exception)
        self.assertEqual(next(s.value for s in at.selectbox if s.label == "PDF page"), 20)
        self.assertTrue(any("GOVERNMENT" in t.value.upper() or "LICENCE" in t.value.upper() for t in at.text_area if t.label == "Page text"))
        # Test-only draft citation: exercise the PDF evidence button, not only its page selector.
        from rfq_inbox.review import field
        draft = at.session_state["draft"]
        draft["fields"]["rfq_reference"] = field("UI test only", [{"document_id": pdf["document_id"], "page": 3,
            "quote": pdf["pages"][2]["text"][:60]}], "needs_review")
        at.session_state["draft"] = draft
        at.session_state["revision"] += 1
        at.run()
        next(b for b in at.button if b.label == "View evidence").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(next(s.value for s in at.selectbox if s.label == "PDF page"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
