"""Actual local upload parsing. Runtime never reads evaluation labels or references."""
import hashlib
import json
import os
import re
import sys
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT / "scripts"))
from prepare_rfq_data import LocalOCR, document, process_excel, process_pdf, sha256, stable_id


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("br", "p", "div", "tr"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_name(name):
    name = re.split(r"[/\\]", name or "attachment")[-1]
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)[:150] or "attachment"


def load_existing_ids():
    path = PROJECT / "data/processed/rfq-demo/inputs.jsonl"
    mapping = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            for source in item["source_files"]:
                if source["role"] == "primary":
                    mapping[source["sha256"]] = item["document_id"]
    return mapping


def parse_case(uploads, storage=None, progress=None, ocr_language="auto"):
    """A batch is one case. EML/attachment edges retained; identical bytes parsed once."""
    storage = Path(storage or os.environ.get("RFQ_CASE_STORAGE") or PROJECT / "data/local-inbox/cases").resolve()
    unique_uploads = sorted({(clean_name(name), hashlib.sha256(data).hexdigest()): data for name, data in uploads}.items())
    if not unique_uploads:
        raise ValueError("Select an example or upload at least one EML, PDF or XLSX file.")
    if sum(len(b) for _, b in unique_uploads) > 50 * 1024 * 1024:
        raise ValueError("This local demo accepts at most 50 MB per case.")
    identity = hashlib.sha256(json.dumps([(n, h) for (n, h), _ in unique_uploads]).encode()).hexdigest()[:24]
    root = storage / ("case_" + identity)
    root.mkdir(parents=True, exist_ok=True)
    case = {"case_id": root.name, "storage_dir": str(root), "documents": [], "emails": [],
            "attachments": [], "warnings": [], "errors": [], "source_conflicts": []}
    seen, known = {}, load_existing_ids()
    ocr = LocalOCR("auto")
    if ocr_language != "auto":
        ocr.languages_for = lambda path, native: ocr_language.split("+")
    def parse(name, data, parent=None, depth=0):
        digest = hashlib.sha256(data).hexdigest()
        name = clean_name(name)
        extension = Path(name).suffix.lower()
        if extension not in (".eml", ".pdf", ".xlsx"):
            case["warnings"].append("Unsupported attachment retained as a listing only: " + name)
            case["attachments"].append({"filename": name, "sha256": digest, "document_id": None, "parent_email_id": parent, "unsupported": True})
            return None
        if digest in seen:
            case["attachments"].append({"filename": name, "sha256": digest, "document_id": seen[digest],
                                        "parent_email_id": parent, "duplicate": True})
            return seen[digest]
        path = root / "uploads" / (digest[:16] + "-" + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        ident = known.get(digest, stable_id("uploaded-content:" + digest))
        seen[digest] = ident
        source = {"path": path.relative_to(root).as_posix(), "role": "primary", "sha256": digest,
                  "original_filename": name}
        doc = document("uploaded-content:" + digest, "rfq", "local upload", [source])
        doc["document_id"] = ident
        doc["display_name"] = name
        doc["parent_email_id"] = parent
        case["documents"].append(doc)
        case["attachments"].append({"filename": name, "sha256": digest, "document_id": ident,
                                    "parent_email_id": parent, "duplicate": False})
        try:
            if extension == ".eml":
                if depth > 2:
                    raise ValueError("Nested EML depth exceeds local demo limit")
                message = BytesParser(policy=policy.default).parsebytes(data)
                body = message.get_body(preferencelist=("plain", "html"))
                content = body.get_content() if body else ""
                if body and body.get_content_type() == "text/html":
                    html = PlainHTML(); html.feed(content); content = "".join(html.parts)
                doc.update({"document_type": "email_thread", "email_text": content, "processing_status": "ok" if content.strip() else "partial",
                            "task_scope": "full_thread", "metadata": {"subject": str(message.get("Subject", "")),
                            "from": str(message.get("From", "")), "to": str(message.get("To", "")), "date": str(message.get("Date", ""))}})
                email = {"document_id": ident, "subject": doc["metadata"]["subject"], "attachment_ids": [], "defects": [str(d) for d in message.defects]}
                case["emails"].append(email)
                for part in message.iter_attachments():
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        case["warnings"].append("Attachment could not be decoded: " + str(part.get_filename()))
                        continue
                    child = parse(part.get_filename() or "attachment", payload, ident, depth + 1)
                    if child and child not in email["attachment_ids"]:
                        email["attachment_ids"].append(child)
            elif extension == ".pdf":
                if not data.startswith(b"%PDF-"):
                    raise ValueError("Not a PDF file: missing %PDF- signature")
                process_pdf(doc, path, root, ocr)
                classifications = PROJECT / "tools/ocr-page-types.json"
                if classifications.is_file():
                    types = json.loads(classifications.read_text(encoding="utf-8"))
                    for page in doc["pages"]:
                        classification = types.get(digest + ":" + str(page["page"]))
                        if classification and classification["page_type"] == "photo":
                            page["page_type"] = "photo"
                            page["requires_visual_review"] = True
                            page["visual_review_note"] = classification["note"]
                first = doc["pages"][0]["text"].lower() if doc["pages"] else ""
                if any(term in first for term in ("request for tender", "request for quotation", "rfq reference")):
                    doc["document_type"] = "rfq"
                elif "invoice" in first:
                    doc["document_type"] = "invoice"
                doc["metadata"]["historical"] = digest == "d148f4d642b5eca591ceaa69fcb80a8b30300acf04f576c7099d6c8a15159e32"
                if doc["metadata"]["historical"]:
                    doc["warnings"].append("Historical procurement document: all extracted dates require human review")
            else:
                process_excel(doc, path, root)
                values = [str(c["raw_value"]) for s in doc["sheets"] for c in s["cells"] if c["raw_value"] is not None]
                template = any("manufacturing rfq template" in v.lower() for v in values)
                doc["document_type"] = "rfq_template" if template else "rfq"
                doc["metadata"] = {"template_only": template, "customer_data": not template}
                if not template:
                    doc["warnings"] = [w for w in doc["warnings"] if not w.startswith("Blank RFQ template:")]
        except Exception as exc:
            doc["processing_status"] = "failed"
            doc["warnings"].append("Parsing failed: " + str(exc))
            case["errors"].append(name + ": " + str(exc))
        if progress:
            progress(name, len(case["documents"]))
        return ident
    for (name, _), data in unique_uploads:
        parse(name, data)
    from rfq_inbox.review import source_quantity_conflicts
    case["source_conflicts"] = source_quantity_conflicts(case)
    (root / "parsed_case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    return case


def source_units(case):
    """Model-readable source fragments, each with a verifiable location. No labels."""
    units = []
    for doc in case["documents"]:
        base = {"document_id": doc["document_id"], "source_file": doc["display_name"]}
        if doc["email_text"] is not None:
            units.append(dict(base, section="body", text=doc["email_text"]))
        for page in doc["pages"]:
            units.append(dict(base, page=page["page"], text=page["text"], status=page["status"], page_type=page.get("page_type")))
        for sheet in doc["sheets"]:
            for cell in sheet["cells"]:
                if cell["raw_value"] is not None:
                    units.append(dict(base, sheet=sheet["name"], cell=cell["coordinate"], text=str(cell["raw_value"])))
    return units
