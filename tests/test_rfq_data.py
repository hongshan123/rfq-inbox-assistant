"""Regression checks using disposable copies; never edit production raw fixtures."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import prepare_rfq_data as prepare


class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.base = (PROJECT / "data/processed/rfq-demo").resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="regression-", dir=str(self.base))
        self.root = Path(self.temp.name).resolve()
        self.root.relative_to(self.base)  # Cleanup is restricted to the explicitly allocated scratch tree.
        self.raw, self.out = self.root / "raw", self.root / "out"
        self.raw.mkdir()

    def tearDown(self):
        self.root.resolve().relative_to(self.base)
        self.temp.cleanup()

    def fixture(self, name):
        publisher = PROJECT / "data/raw/rfq-demo/invoice-test-pack/shopify-invoice-test-pack-main"
        target = self.raw / "wrapper/fixtures" / name
        shutil.copytree(publisher / "fixtures" / name, target)
        if not (self.raw / "wrapper/schema").exists():
            shutil.copytree(publisher / "schema", self.raw / "wrapper/schema")
        return target

    def command(self, script, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(PROJECT / "scripts" / script), *map(str, args)],
                              cwd=str(PROJECT), env=env, capture_output=True, text=True, encoding="utf-8")

    def process(self):
        run = self.command("prepare_rfq_data.py", "--raw-dir", self.raw, "--out-dir", self.out, "--ocr", "off")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def records(self, name):
        return [json.loads(line) for line in (self.out / name).read_text(encoding="utf-8").splitlines()]

    def test_repeatability_label_independence_and_custom_file_preservation(self):
        fixture = self.fixture("01-simple-english")
        self.fixture("02-multipage")
        raw_before = prepare.snapshot(self.raw)
        self.process()
        wanted = ["inputs.jsonl", "gold.jsonl", "demo-emails/invoice-demo.eml"]
        baseline = {name: (self.out / name).read_bytes() for name in wanted}
        (self.out / "user-notes.txt").write_text("preserve me", encoding="utf-8")
        self.process()
        self.assertEqual(prepare.snapshot(self.raw), raw_before)
        self.assertEqual({name: (self.out / name).read_bytes() for name in wanted}, baseline)
        self.assertEqual((self.out / "user-notes.txt").read_text(), "preserve me")
        label = prepare.read_json(fixture / "expected.json")
        label["LEAK_SENTINEL_DO_NOT_USE"] = "label perturbation must not affect input"
        (fixture / "expected.json").write_text(json.dumps(label), encoding="utf-8")
        self.process()
        self.assertEqual((self.out / "inputs.jsonl").read_bytes(), baseline["inputs.jsonl"])
        self.assertEqual((self.out / "demo-emails/invoice-demo.eml").read_bytes(), baseline["demo-emails/invoice-demo.eml"])
        self.assertIn("LEAK_SENTINEL", (self.out / "gold.jsonl").read_text())
        for path in (self.out / "text").glob("*"):
            self.assertNotIn("LEAK_SENTINEL", path.read_text(encoding="utf-8"))
        checked = self.command("validate_rfq_data.py", "--data-dir", self.out)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_corrupt_file_continues_and_excel_preserves_identifiers_and_null_cache(self):
        from openpyxl import Workbook
        (self.raw / "broken.pdf").write_bytes(b"This is not a PDF")
        book = Workbook()
        sheet = book.active
        sheet["A1"] = "000123"
        sheet["A2"] = "Part-Xy Ø12 ±0.01 mm"
        sheet["A3"] = 1.23
        sheet["A3"].number_format = "0.000"
        sheet["B1"] = "=SUM(A3,0)"
        sheet.merge_cells("A5:B5")
        book.save(self.raw / "template.xlsx")
        self.process()
        docs = self.records("inputs.jsonl")
        self.assertEqual(len(docs), 2)
        self.assertEqual(next(d for d in docs if d["document_type"] == "rfq")["processing_status"], "failed")
        excel = next(d for d in docs if d["document_type"] == "rfq_template")
        cells = {c["coordinate"]: c for c in excel["sheets"][0]["cells"]}
        self.assertEqual(cells["A1"]["raw_value"], "000123")
        self.assertEqual(cells["A2"]["raw_value"], "Part-Xy Ø12 ±0.01 mm")
        self.assertEqual(cells["A3"]["number_format"], "0.000")
        self.assertIsNone(cells["B1"]["cached_value"])
        checked = self.command("validate_rfq_data.py", "--data-dir", self.out)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_ocr_renders_each_pdf_page_and_missing_language_is_not_success(self):
        import pypdfium2
        import pytesseract
        folder = self.fixture("02-multipage")
        pdf = folder / "invoice.pdf"
        ocr = prepare.LocalOCR("off")
        ocr.available = True
        ocr.pdfium = pypdfium2
        ocr.tesseract = pytesseract
        ocr.engine = "simulated-test-engine"
        ocr.languages = ["eng"]
        pixels = []
        def recognize(image, *args, **kwargs):
            pixels.append(hashlib.sha256(image.tobytes()).hexdigest())
            text = "OCR branch regression text for this PDF page"
            return text, "ok", ["Simulated OCR in regression only"], {"raw_output": text}
        doc = prepare.document("test", "invoice", "test", [prepare.source_file(pdf, self.raw)])
        with patch("pypdf._page.PageObject.extract_text", return_value=""), patch("rfq_inbox.ocr_engine.recognize", side_effect=recognize):
            prepare.process_pdf(doc, pdf, self.raw, ocr)
        self.assertEqual(len(doc["pages"]), 2)
        self.assertEqual(len(set(pixels)), 2, "Page 2 must not reuse the first-page PNG/render")
        self.assertEqual([p["ocr"]["rendered_pdf_page"] for p in doc["pages"]], [1, 2])
        self.assertTrue(all(p["extraction_method"] == "ocr" for p in doc["pages"]))
        text, status, warning, detail = ocr.extract(self.raw / "03-arabic/invoice.pdf", 1)
        self.assertIsNone(text)
        self.assertEqual(status, "needs_ocr")
        self.assertIn("ara", warning)

    def test_validator_rejects_injected_labels_and_missing_pdf_page(self):
        self.fixture("02-multipage")
        self.process()
        docs = self.records("inputs.jsonl")
        docs[0]["answer"] = {"supplier": "injected"}
        docs[0]["pages"].pop()
        (self.out / "inputs.jsonl").write_text(json.dumps(docs[0]), encoding="utf-8")
        checked = self.command("validate_rfq_data.py", "--data-dir", self.out)
        self.assertEqual(checked.returncode, 1)
        errors = prepare.read_json(self.out / "validation.json")["errors"]
        self.assertTrue(any("answer" in e for e in errors), errors)
        self.assertTrue(any("page count" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
