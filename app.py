"""Local RFQ Inbox Assistant. Start with: python -m streamlit run app.py"""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from rfq_inbox.exports import build_exports, save_review
from rfq_inbox.model import ModelError, OpenAICompatibleProvider
from rfq_inbox.ocr_engine import png_bytes, render_page
from rfq_inbox.parsing import PROJECT, parse_case
from rfq_inbox.review import (blank_result, columns, confirm_review, derive_issues,
                              evidence_check, verify_prediction)

st.set_page_config(page_title="RFQ Inbox Assistant", page_icon="📨", layout="wide")
st.markdown("""<style>
.block-container {padding-top:1.8rem; max-width:1600px;}
h1 {font-size:2rem !important; letter-spacing:-.025em;}
div[data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
button {border-radius:8px !important;}
</style>""", unsafe_allow_html=True)  # Constant CSS only, never user HTML.

EXAMPLES = {
    "A · Complete synthetic RFQ": PROJECT / "data/synthetic/rfq-inbox/a-complete/inputs/request.eml",
    "B · Missing material": PROJECT / "data/synthetic/rfq-inbox/b-missing-material/inputs/request.eml",
    "C · Quantity conflict (100 / 120)": PROJECT / "data/synthetic/rfq-inbox/c-quantity-conflict/inputs/request.eml",
    "Synthetic invoice PDF email": PROJECT / "data/processed/rfq-demo/demo-emails/invoice-demo.eml",
    "Historical Narrogin RFQ email": PROJECT / "data/processed/rfq-demo/demo-emails/historical-rfq-demo.eml",
    "Blank manufacturing RFQ template": PROJECT / "data/raw/rfq-demo/manufacturing-rfq-template.xlsx",
}


def open_case(files, language):
    progress = st.progress(0, text="Parsing actual email and attachment bytes…")
    def update(name, count):
        progress.progress(min(90, 15 + count * 15), text="Parsed " + name)
    case = parse_case(files, progress=update, ocr_language=language)
    progress.progress(100, text="Source parsing complete")
    docs = [d for d in case["documents"] if d["document_type"] != "email_thread"]
    kind = "rfq_template" if docs and all(d["document_type"] == "rfq_template" for d in docs) else "invoice" if docs and all(d["document_type"] == "invoice" for d in docs) else "rfq"
    saved = Path(case["storage_dir"]) / "review.json"
    original = json.loads(saved.read_text(encoding="utf-8")) if saved.is_file() else None
    st.session_state["case"] = case
    st.session_state["draft"] = original["result"] if original else blank_result(kind)
    st.session_state["active_kind"] = st.session_state["draft"]["document_type"]
    st.session_state["revision"] = st.session_state.get("revision", 0) + 1
    st.session_state["confirmed"] = None
    st.session_state["confirmed_fingerprint"] = None
    st.session_state["original_prediction"] = original.get("original_prediction_file") if original else None
    st.session_state.pop("focus", None)


def focus_evidence(evidence, key):
    valid, message = evidence_check(evidence, st.session_state["case"])
    st.caption(("Location checked · " if valid else "Citation needs review · ") + message)
    st.caption(str(evidence.get("quote", "")))
    if st.button("View evidence", key=key):
        st.session_state["focus"] = evidence
        st.rerun()


def source_view(case):
    docs = case["documents"]
    if not docs:
        st.info("No readable documents in this case.")
        return
    focus = st.session_state.pop("focus", None)
    selector = "source_" + case["case_id"]
    if focus:
        st.session_state[selector] = focus["document_id"]
        st.session_state["active_evidence"] = focus
    selected = st.selectbox("Source document", [d["document_id"] for d in docs],
                            format_func=lambda value: next(d["display_name"] + " · " + d["document_type"] for d in docs if d["document_id"] == value), key=selector)
    doc = next(d for d in docs if d["document_id"] == selected)
    st.caption("Document ID: " + doc["document_id"])
    st.caption("Parse status: " + doc["processing_status"])
    for warning in doc["warnings"]:
        st.warning(warning)
    evidence = st.session_state.get("active_evidence")
    if evidence and evidence["document_id"] == selected:
        st.info("Selected evidence: " + evidence.get("quote", ""))
    if doc["email_text"] is not None:
        st.write("**" + doc["metadata"].get("subject", "Email") + "**")
        st.caption("From: " + doc["metadata"].get("from", ""))
        st.text_area("Email body", doc["email_text"], height=420, disabled=True, key="body_" + selected)
    if doc["pages"]:
        page_key = "page_" + selected
        if focus and focus.get("page"):
            st.session_state[page_key] = focus["page"]
        number = st.selectbox("PDF page", [p["page"] for p in doc["pages"]], key=page_key)
        page = next(p for p in doc["pages"] if p["page"] == number)
        st.caption("Page " + str(number) + " · " + page["extraction_method"] + " · " + page["status"])
        if page.get("requires_visual_review"):
            st.warning(page.get("visual_review_note", "Photo evidence: review the image manually"))
        for warning in page["warnings"]:
            st.caption(warning)
        text_tab, image_tab, technical = st.tabs(["Usable text", "Page image", "Native text / OCR"])
        with text_tab:
            st.text_area("Page text", page["text"], height=350, disabled=True, key="text_" + selected + str(number))
        with image_tab:
            source = Path(case["storage_dir"]) / doc["source_files"][0]["path"]
            try:
                image = render_page(source, number, dpi=125)
                st.image(image, caption=doc["display_name"] + " · PDF page " + str(number), width="stretch")
                image.close()
            except Exception:
                st.error("This page could not be rendered. Download the source PDF for manual inspection.")
        with technical:
            st.text_area("Native text", page["native_text"], height=180, disabled=True, key="native_" + selected + str(number))
            st.text_area("Raw OCR output", page.get("ocr_raw_text") or "", height=180, disabled=True, key="ocr_" + selected + str(number))
            if page.get("ocr"):
                st.json({k: v for k, v in page["ocr"].items() if k not in ("words", "raw_output", "alternate_raw_output")})
    if doc["sheets"]:
        sheet_key = "sheet_" + selected
        if focus and focus.get("sheet"):
            st.session_state[sheet_key] = focus["sheet"]
        sheet_name = st.selectbox("Worksheet", [s["name"] for s in doc["sheets"]], key=sheet_key)
        sheet = next(s for s in doc["sheets"] if s["name"] == sheet_name)
        cells = [c for c in sheet["cells"] if c["raw_value"] is not None]
        st.dataframe([{k: "" if c[k] is None else str(c[k]) for k in ("coordinate", "raw_value", "data_type", "number_format", "formula", "cached_value")} for c in cells], hide_index=True, width="stretch")
        cell_key = "cell_" + selected + sheet_name
        coordinates = [c["coordinate"] for c in sheet["cells"]]
        if focus and focus.get("cell") in coordinates:
            st.session_state[cell_key] = focus["cell"]
        coordinate = st.selectbox("Inspect cell", coordinates, key=cell_key)
        st.json(next(c for c in sheet["cells"] if c["coordinate"] == coordinate))
        st.caption("Merged ranges: " + ", ".join(sheet["merged_ranges"]))
    source = Path(case["storage_dir"]) / doc["source_files"][0]["path"]
    st.download_button("Download original source", source.read_bytes(), file_name=doc["display_name"], key="source_dl_" + selected)


st.title("RFQ Inbox Assistant")
st.caption("Local review workspace for manufacturing enquiries · source-linked evidence · human-confirmed exports")
provider = OpenAICompatibleProvider()
configured = provider.configured()
with st.sidebar:
    st.subheader("Inbox")
    example = st.selectbox("Choose an example", list(EXAMPLES), key="example")
    language = st.selectbox("OCR language", ["auto", "eng", "ara", "eng+ara"], help="Only pages without useful native text are sent to local OCR.")
    if st.button("Open example", type="primary", key="open_example"):
        path = EXAMPLES[example]
        try:
            with st.spinner("Opening and parsing sources…"):
                open_case([(path.name, path.read_bytes())], language)
            st.rerun()
        except Exception as exc:
            st.error("Could not open this example: " + str(exc))
    st.divider()
    uploads = st.file_uploader("Upload EML, PDF or XLSX", type=["eml", "pdf", "xlsx"], accept_multiple_files=True)
    if st.button("Process uploads", disabled=not uploads, key="process_uploads"):
        try:
            with st.spinner("Parsing email bodies and attachments…"):
                open_case([(f.name, f.getvalue()) for f in uploads], language)
            st.rerun()
        except Exception as exc:
            st.error("Upload processing stopped: " + str(exc))
    st.caption("Files and drafts are saved locally. Emails are never sent.")
    st.caption("Model status: " + ("Configured" if configured else "Model not configured"))

if not configured:
    st.warning("Model not configured — browse sources, enter reviewed fields and export locally. No model extraction has run.")
if "case" not in st.session_state:
    st.info("Choose an example in the sidebar or upload files to start a case.")
    st.stop()
case = st.session_state["case"]
st.caption("Case " + case["case_id"] + " · " + str(len(case["documents"])) + " unique documents")
with st.expander("Email and attachment relationships", expanded=False):
    for email in case["emails"]:
        st.write(email["subject"])
        st.caption("Email " + email["document_id"] + " → " + ", ".join(email["attachment_ids"]))
    st.dataframe(case["attachments"], hide_index=True, width="stretch")
    st.caption("Duplicate attachment bytes are parsed once. Repeated uploads do not multiply item quantities.")
for error in case["errors"]:
    st.error(error)
for warning in case["warnings"]:
    st.warning(warning)
left, right = st.columns([1, 1.15], gap="large")
with left:
    st.subheader("Sources & evidence")
    source_view(case)
with right:
    st.subheader("Review extraction")
    kinds = ["rfq", "invoice", "rfq_template"]
    kind = st.selectbox("Review as", kinds, index=kinds.index(st.session_state["active_kind"]), key="kind_" + case["case_id"])
    if kind != st.session_state["active_kind"]:
        st.session_state["draft"] = blank_result(kind)
        st.session_state["active_kind"] = kind
        st.session_state["revision"] += 1
        st.session_state["confirmed"] = None
        st.rerun()
    if st.button("Run model extraction", disabled=not configured or kind == "rfq_template", key="run_model"):
        try:
            with st.spinner("Extracting fields through the configured model…"):
                original = provider.extract(case, kind)
                folder = Path(case["storage_dir"]) / "predictions"
                folder.mkdir(exist_ok=True)
                path = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json")
                path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
                st.session_state["original_prediction"] = str(path.relative_to(Path(case["storage_dir"])))
                checked = verify_prediction(original["prediction"], case, kind)
                st.session_state["draft"] = checked
                st.session_state["revision"] += 1
                st.session_state["confirmed"] = None
                st.rerun()
        except ModelError as exc:
            st.error(str(exc))
        except Exception:
            st.error("The model result failed schema/evidence checks. The original response was retained locally; no fields were accepted.")
    if st.session_state.get("original_prediction"):
        with st.expander("Original model prediction (read only)"):
            path = Path(case["storage_dir"]) / st.session_state["original_prediction"]
            if path.is_file():
                st.json(json.loads(path.read_text(encoding="utf-8")))
    draft = st.session_state["draft"]
    prefix = case["case_id"] + "_" + str(st.session_state["revision"])
    if kind == "rfq_template":
        st.info("Template structure only. No customer order or item lines have been generated.")
    values = {}
    for key, value in draft["fields"].items():
        values[key] = st.text_input(key.replace("_", " ").title(), value="" if value["value"] is None else str(value["value"]), key=prefix + "_" + key)
        st.caption("Original status: " + value["status"])
        for i, evidence in enumerate(value["evidence"]):
            focus_evidence(evidence, prefix + key + "_e" + str(i))
    st.write("**Items**")
    rows = [{key: "" if item.get(key, {}).get("value") is None else str(item[key]["value"]) for key in columns(kind)} for item in draft["items"]]
    if not rows:
        rows = [{key: "" for key in columns(kind)}]
    edited = st.data_editor(rows, num_rows="dynamic", hide_index=True, disabled=kind == "rfq_template", key=prefix + "_items", width="stretch")
    with st.expander("Original item evidence"):
        for index, item in enumerate(draft["items"], 1):
            for key, f in item.items():
                st.caption("Item " + str(index) + " · " + key + " · " + f["status"])
                for n, e in enumerate(f["evidence"]):
                    focus_evidence(e, prefix + "_item" + str(index) + key + str(n))
    resolutions = {}
    if case["source_conflicts"]:
        st.error("Conflicting source quantities — a user decision is required.")
    for conflict in case["source_conflicts"]:
        st.write("**" + conflict["part_number"] + " · Quantity**")
        st.caption("Literal labelled-source check; this is not model inference.")
        for i, candidate in enumerate(conflict["candidates"]):
            st.write("Source value: " + str(candidate["value"]))
            for n, e in enumerate(candidate["evidence"]):
                focus_evidence(e, prefix + conflict["conflict_id"] + str(i) + str(n))
        choice = st.selectbox("Confirm quantity for " + conflict["part_number"], ["Unresolved"] + sorted({str(c["value"]) for c in conflict["candidates"]}), key=prefix + "_resolve_" + conflict["conflict_id"])
        if choice != "Unresolved":
            resolutions[conflict["conflict_id"]] = {"value": choice, "note": "Explicit source choice in the review UI"}
    notes = st.text_area("Review notes", "\n".join(draft.get("review_notes", [])), key=prefix + "_notes")
    preview = confirm_review(draft, values, [] if kind == "rfq_template" else edited, notes, case, resolutions)
    missing, conflicts = derive_issues(preview)
    with st.expander("Missing information & unresolved conflicts", expanded=bool(missing or conflicts)):
        for value in missing:
            st.warning("Missing: " + value.replace("_", " "))
        for value in conflicts:
            st.error("Conflict: " + value)
        if not missing and not conflicts:
            st.success("No missing or conflicting fields in the current review.")
    fingerprint = hashlib.sha256(json.dumps({"values": values, "items": edited, "notes": notes, "resolutions": resolutions}, sort_keys=True).encode()).hexdigest()
    if st.button("Confirm & save review", type="primary", key="confirm_review"):
        save_review(case, preview, st.session_state.get("original_prediction"))
        st.session_state["confirmed"] = preview
        st.session_state["confirmed_fingerprint"] = fingerprint
        st.success("Confirmed values saved locally. Original model output is retained separately.")
    ready = st.session_state.get("confirmed") is not None and st.session_state.get("confirmed_fingerprint") == fingerprint
    if not ready:
        st.caption("Confirm and save current edits to enable exports.")
    if ready:
        exports = build_exports(case, st.session_state["confirmed"])
        for name, content in exports.items():
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith("xlsx") else "application/json" if name.endswith("json") else "text/plain"
            st.download_button("Download " + name, content, file_name=name, mime=mime, key="export_" + name)
        summary, email = st.tabs(["RFQ / invoice summary", "Missing information draft"])
        with summary:
            st.text(exports["rfq-summary.txt"].decode("utf-8"))
        with email:
            st.text(exports["request-for-missing-information.txt"].decode("utf-8"))
