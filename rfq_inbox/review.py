"""Typed review records, evidence checks and conservative source consistency checks."""
import copy
import hashlib
import json
import re

RFQ_FIELDS = ["customer_name", "contact_name", "contact_email", "rfq_reference", "quote_deadline",
              "requested_delivery_date", "delivery_location"]
RFQ_ITEM_FIELDS = ["part_number", "revision", "description", "material", "quantity", "unit", "surface_finish"]
INVOICE_FIELDS = ["supplier_name", "buyer_name", "contact_email", "invoice_number", "invoice_date",
                  "currency", "subtotal", "tax", "freight", "duty", "total"]
INVOICE_ITEM_FIELDS = ["part_number", "description", "quantity", "unit", "unit_price", "line_total"]
STATUSES = ["supported", "missing", "conflicting", "needs_review"]


def columns(kind):
    return INVOICE_ITEM_FIELDS if kind == "invoice" else RFQ_ITEM_FIELDS


def field(value=None, evidence=None, status=None):
    return {"value": value, "status": status or ("missing" if value is None else "needs_review"),
            "evidence": evidence or [], "candidates": []}


def blank_result(kind):
    keys = [] if kind == "rfq_template" else INVOICE_FIELDS if kind == "invoice" else RFQ_FIELDS
    return {"document_type": kind, "fields": {key: field() for key in keys}, "items": [],
            "missing_information": [], "conflicts": [], "review_notes": [], "origin": "manual_draft"}


def evidence_check(evidence, case):
    doc = next((d for d in case["documents"] if d["document_id"] == evidence.get("document_id")), None)
    if not doc:
        return False, "Source document does not exist"
    quote = evidence.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return False, "Empty quotation"
    if evidence.get("page") is not None:
        page = next((p for p in doc["pages"] if p["page"] == evidence["page"]), None)
        if not page:
            return False, "PDF page does not exist"
        text = page["text"]
    elif evidence.get("sheet") is not None and evidence.get("cell") is not None:
        sheet = next((s for s in doc["sheets"] if s["name"] == evidence["sheet"]), None)
        cell = next((c for c in sheet["cells"] if c["coordinate"] == evidence["cell"]), None) if sheet else None
        if not cell:
            return False, "Worksheet/cell does not exist"
        text = "\n".join(str(cell[k]) for k in ("raw_value", "formula", "cached_value") if cell.get(k) is not None)
    elif evidence.get("section") == "body" and doc["email_text"] is not None:
        text = doc["email_text"]
    else:
        return False, "Evidence needs a PDF page, worksheet/cell or email body location"
    normalize = lambda s: re.sub(r"\s+", " ", s).strip()
    if normalize(quote) not in normalize(text):
        return False, "Quoted text is absent at this location"
    return True, "Location and quoted text verified; semantic support still requires review"


def source_quantity_conflicts(case):
    """Literal labelled quantity consistency only, never a replacement for model extraction.

    Recognizes explicit 'Part number:'/'Quantity:' blocks and named Excel columns.
    It never fills an RFQ from reference files or infers unstated business facts.
    """
    observations = []
    for doc in case["documents"]:
        base = {"document_id": doc["document_id"], "source_file": doc["display_name"]}
        body = doc.get("email_text")
        if body:
            part = re.search(r"(?im)^Part number:\s*(\S+)", body)
            qty = re.search(r"(?im)^Quantity:\s*(\d+(?:\.\d+)?)\s*([^\n]*)", body)
            if part and qty:
                observations.append({"part_number": part[1], "value": qty[1],
                                     "evidence": [dict(base, section="body", quote=part[0]), dict(base, section="body", quote=qty[0])]})
        for sheet in doc["sheets"]:
            cells = {c["coordinate"]: c for c in sheet["cells"]}
            rows = {}
            for c in sheet["cells"]:
                match = re.match(r"([A-Z]+)(\d+)$", c["coordinate"])
                rows.setdefault(int(match[2]), {})[match[1]] = c
            for row, values in rows.items():
                headers = {str(c["raw_value"]).strip().lower().replace("_", " "): col for col, c in values.items()}
                if "part number" not in headers or "quantity" not in headers:
                    continue
                for index in range(row + 1, sheet["max_row"] + 1):
                    pc = cells.get(headers["part number"] + str(index)); qc = cells.get(headers["quantity"] + str(index))
                    if not pc or not qc or pc["raw_value"] is None or qc["raw_value"] is None:
                        continue
                    value = str(qc["raw_value"])
                    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
                        continue
                    observations.append({"part_number": str(pc["raw_value"]), "value": value,
                                         "evidence": [dict(base, sheet=sheet["name"], cell=pc["coordinate"], quote=str(pc["raw_value"])),
                                                      dict(base, sheet=sheet["name"], cell=qc["coordinate"], quote=value)]})
    conflicts = []
    for part in sorted({o["part_number"] for o in observations}):
        matches = [o for o in observations if o["part_number"] == part]
        if len({float(o["value"]) for o in matches}) > 1:
            key = hashlib.sha256((part + json.dumps(matches, sort_keys=True)).encode()).hexdigest()[:12]
            conflicts.append({"conflict_id": key, "field": "quantity", "part_number": part,
                              "status": "conflicting", "value": None, "candidates": matches,
                              "origin": "literal_source_consistency_check", "resolution": None})
    return conflicts


def result_schema(kind):
    evidence = {"type": "object", "properties": {"document_id": {"type": "string"}, "quote": {"type": "string"},
                "page": {"type": ["integer", "null"]}, "sheet": {"type": ["string", "null"]},
                "cell": {"type": ["string", "null"]}, "section": {"type": ["string", "null"]},
                "source_file": {"type": ["string", "null"]}}, "required": ["document_id", "quote"], "additionalProperties": False}
    candidate = {"type": "object", "properties": {"value": {"type": ["string", "number", "null"]},
                 "evidence": {"type": "array", "items": evidence}}, "required": ["value", "evidence"], "additionalProperties": False}
    f = {"type": "object", "properties": {"value": {"type": ["string", "number", "null"]},
         "status": {"enum": STATUSES}, "evidence": {"type": "array", "items": evidence},
         "candidates": {"type": "array", "items": candidate}}, "required": ["value", "status", "evidence"], "additionalProperties": False}
    keys = INVOICE_FIELDS if kind == "invoice" else RFQ_FIELDS if kind == "rfq" else []
    fields = {"type": "object", "properties": {k: f for k in keys}, "required": keys, "additionalProperties": False}
    item = {"type": "object", "properties": {k: f for k in columns(kind)}, "required": columns(kind), "additionalProperties": False}
    return {"type": "object", "properties": {"document_type": {"const": kind}, "fields": fields,
            "items": {"type": "array", "items": item}, "missing_information": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}}, "review_notes": {"type": "array", "items": {"type": "string"}}},
            "required": ["document_type", "fields", "items", "missing_information", "conflicts", "review_notes"], "additionalProperties": False}


def verify_prediction(prediction, case, kind):
    import jsonschema
    jsonschema.validate(prediction, result_schema(kind))
    result = copy.deepcopy(prediction)
    result["origin"] = "model_prediction"
    result["review_notes"] += ["Model-reported conflict: " + value for value in prediction["conflicts"]]
    for f in list(result["fields"].values()) + [v for row in result["items"] for v in row.values()]:
        for e in f["evidence"]:
            e["verified"], e["verification_note"] = evidence_check(e, case)
        for candidate in f.get("candidates", []):
            for e in candidate["evidence"]:
                e["verified"], e["verification_note"] = evidence_check(e, case)
        if f["status"] == "conflicting":
            f["value"] = None
            if len({str(c["value"]) for c in f.get("candidates", [])}) < 2:
                f["status"] = "needs_review"
        elif f["value"] is None:
            f["status"] = "missing"
        elif not f["evidence"] or not all(e["verified"] for e in f["evidence"]):
            f["status"] = "needs_review"
    # A model picking one quantity cannot erase an explicit source discrepancy.
    for conflict in case["source_conflicts"]:
        for row in result["items"]:
            if str(row.get("part_number", {}).get("value")) == conflict["part_number"]:
                row["quantity"] = field(None, status="conflicting")
                row["quantity"]["candidates"] = copy.deepcopy(conflict["candidates"])
    return result


def confirm_review(draft, values, rows, notes, case, resolutions=None):
    result = copy.deepcopy(draft)
    result["review_notes"] = [notes] if notes else []
    def reviewed(old, value):
        value = None if value is None or str(value).strip() == "" else value
        if value == old.get("value"):
            value_field = copy.deepcopy(old)
        else:
            value_field = field(value, status="missing" if value is None else "needs_review")
            if old.get("candidates"):
                value_field["original_candidates"] = copy.deepcopy(old["candidates"])
                value_field["resolution"] = {"value": value, "confirmed_by": "user"}
        value_field["review_state"] = "confirmed_by_user"
        value_field["origin"] = "user"
        return value_field
    for key in result["fields"]:
        result["fields"][key] = reviewed(result["fields"][key], values.get(key))
    items = []
    for index, row in enumerate(rows):
        if not any(v is not None and str(v).strip() for v in row.values()):
            continue
        old = draft["items"][index] if index < len(draft["items"]) else {}
        items.append({key: reviewed(old.get(key, field()), row.get(key)) for key in columns(result["document_type"])})
    result["items"] = items
    result["source_conflicts"] = copy.deepcopy(case["source_conflicts"])
    resolutions = resolutions or {}
    for conflict in result["source_conflicts"]:
        resolution = resolutions.get(conflict["conflict_id"])
        if resolution and resolution.get("value") is not None:
            chosen = next((c for c in conflict["candidates"] if str(c["value"]) == str(resolution["value"])), None)
            if not chosen:
                raise ValueError("Resolve a discrepancy using a listed source value")
            conflict["resolution"] = dict(resolution, confirmed_by="user")
            for item in items:
                if str(item["part_number"]["value"]) == conflict["part_number"]:
                    item["quantity"] = reviewed(field(chosen["value"], chosen["evidence"], "supported"), chosen["value"])
    result["origin"] = "user_confirmed_review"
    result["missing_information"], result["conflicts"] = derive_issues(result)
    return result


def derive_issues(result):
    if result["document_type"] == "rfq_template":
        return [], []
    missing = [key for key, f in result["fields"].items() if f["value"] is None]
    if not result["items"]:
        missing.append("items (no reviewed material lines)")
    for index, item in enumerate(result["items"], 1):
        for key, f in item.items():
            if f["value"] is None:
                missing.append("item " + str(index) + ": " + key)
    conflicts = [key for key, f in result["fields"].items() if f["status"] == "conflicting"]
    conflicts += ["item " + str(i) + ": " + key for i, item in enumerate(result["items"], 1) for key, f in item.items() if f["status"] == "conflicting"]
    conflicts += [c["part_number"] + " quantity: " + " vs ".join(str(x["value"]) for x in c["candidates"])
                  for c in result.get("source_conflicts", []) if not c.get("resolution")]
    return missing, conflicts
