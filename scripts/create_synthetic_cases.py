"""Generate fictional RFQ cases from facts; references never enter runtime inputs."""
import io
import json
import zipfile
import re
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from openpyxl import Workbook

PROJECT = Path(__file__).resolve().parents[1]


def main():
    root = PROJECT / "data/synthetic/rfq-inbox"
    references = root / "references"
    references.mkdir(parents=True, exist_ok=True)
    common = {"customer_name": "Banksia Precision Demo Pty Ltd", "contact_name": "Alex Morgan",
              "contact_email": "alex@banksia-demo.example.com", "quote_deadline": "2026-11-05",
              "requested_delivery_date": "2026-12-01", "delivery_location": "Geelong VIC, Australia",
              "part_number": "0007-A", "revision": "B", "description": "Machined spacer plate",
              "material": "316 stainless steel", "quantity": 100, "unit": "EA", "surface_finish": "Bead blasted"}
    cases = [("a-complete", "SYN-A-001", "Complete fictional RFQ", common["material"], 100),
             ("b-missing-material", "SYN-B-001", "Material is genuinely omitted", None, 100),
             ("c-quantity-conflict", "SYN-C-001", "Email 100 versus Excel 120", common["material"], 120)]
    for slug, reference, note, material, excel_qty in cases:
        facts = dict(common, rfq_reference=reference, material=material)
        folder = root / slug / "inputs"
        folder.mkdir(parents=True, exist_ok=True)
        book = Workbook()
        sheet = book.active
        sheet.title = "RFQ Items"
        sheet["A1"] = "SYNTHETIC DEMO - fictional RFQ"
        headers = ["Part number", "Revision", "Description", "Material", "Quantity", "Unit", "Surface finish"]
        sheet.append([])
        sheet.append(headers)
        sheet.append([facts["part_number"], facts["revision"], facts["description"], material, excel_qty, "EA", facts["surface_finish"]])
        sheet["A4"].number_format = "@"
        stream = io.BytesIO()
        book.save(stream)
        canonical = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive, zipfile.ZipFile(canonical, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name in sorted(archive.namelist()):
                data = archive.read(name)
                if name == "docProps/core.xml":
                    data = re.sub(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", b"2026-10-20T10:00:00Z", data)
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 20, 10, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(info, data)
        stream = canonical
        body = ["SYNTHETIC DEMO - fictional local review example.", "",
                "Customer name: " + facts["customer_name"], "Contact name: " + facts["contact_name"],
                "Contact email: " + facts["contact_email"], "RFQ reference: " + reference,
                "Quote deadline: " + facts["quote_deadline"], "Requested delivery date: " + facts["requested_delivery_date"],
                "Delivery location: " + facts["delivery_location"], "", "Part number: " + facts["part_number"],
                "Revision: " + facts["revision"], "Description: " + facts["description"]]
        if material:
            body.append("Material: " + material)
        body += ["Quantity: 100 EA", "Surface finish: " + facts["surface_finish"], "", "Please review the attached item schedule.\n"]
        msg = EmailMessage(policy=SMTP)
        msg["From"] = "Alex Morgan <alex@banksia-demo.example.com>"
        msg["To"] = "Quotes Team <quotes@workshop.example.com>"
        msg["Subject"] = "SYNTHETIC DEMO - " + reference
        msg["Date"] = "Tue, 20 Oct 2026 10:00:00 +0000"
        msg["Message-ID"] = "<" + slug + "@example.com>"
        msg.set_content("\n".join(body))
        msg.add_attachment(stream.getvalue(), maintype="application", subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="items.xlsx")
        msg.set_boundary("synthetic-demo-" + slug)
        (folder / "request.eml").write_bytes(msg.as_bytes())
        (folder / "items.xlsx").write_bytes(stream.getvalue())
        answer = {"synthetic": True, "purpose": "functional demonstration, not an independent accuracy benchmark",
                  "scenario": note, "facts": facts, "email_quantity": 100, "excel_quantity": excel_qty,
                  "expected_missing": ["material"] if material is None else [],
                  "expected_conflicts": [{"part_number": facts["part_number"], "field": "quantity", "values": [100, 120]}] if excel_qty != 100 else []}
        (references / (slug + ".json")).write_text(json.dumps(answer, indent=2), encoding="utf-8")
    print("Created 3 synthetic EML/XLSX examples and separate reference facts.")


if __name__ == "__main__":
    main()
