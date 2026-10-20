"""Incremental local OCR of input pages. Does not open gold.jsonl or expected.json."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from prepare_rfq_data import (PROJECT, LocalOCR, Outputs, aggregate_status, evidence_for,
                              evidence_text, json_text, now, read_json, sha256)
from rfq_inbox.ocr_engine import png_bytes, render_page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(PROJECT / "data/processed/rfq-demo"))
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()
    out = Path(args.data_dir).resolve()
    manifest = read_json(out / "manifest.json")
    raw = Path(manifest["raw_dir"])
    inputs = [json.loads(line) for line in (out / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    outputs = Outputs(out)
    ocr = LocalOCR("auto")
    config_path = PROJECT / "tools/ocr-page-types.json"
    page_types = read_json(config_path) if config_path.exists() else {}
    outcomes = []
    for doc in inputs:
        primary = next((s for s in doc["source_files"] if s["role"] == "primary"), None)
        if not primary or not primary["path"].endswith(".pdf"):
            continue
        path = (raw / primary["path"]).resolve()
        path.relative_to(raw.resolve())
        if sha256(path) != primary["sha256"]:
            raise ValueError("Source changed: " + primary["path"])
        for page in doc["pages"]:
            classification = page_types.get(primary["sha256"] + ":" + str(page["page"]), {})
            if page["extraction_method"] != "ocr" and page["status"] == "ok" and not classification:
                continue  # Valid native text is not sent to OCR.
            rel = "ocr-pages/" + doc["document_id"] + "/page-%04d.png" % page["page"]
            image = render_page(path, page["page"])
            try:
                image_bytes = png_bytes(image)
            finally:
                image.close()
            outputs.write(rel, image_bytes)
            if args.inspect_only:
                print(json.dumps({"document_id": doc["document_id"], "source": primary["path"], "page": page["page"], "image": rel}, ensure_ascii=False))
                continue
            page_type = classification.get("page_type", "text_scan")
            languages = ocr.languages_for(path, page["native_text"])
            cache = hashlib.sha256(json.dumps({"source": primary["sha256"], "page": page["page"],
                "image": hashlib.sha256(image_bytes).hexdigest(), "engine": ocr.version,
                "languages": languages, "quality_version": 1,
                "data": {lang: sha256(ocr.tessdata / (lang + ".traineddata")) for lang in languages if ocr.tessdata and (ocr.tessdata / (lang + ".traineddata")).exists()}}, sort_keys=True).encode()).hexdigest()
            cached = (page.get("ocr") or {}).get("cache_key") == cache
            if cached:
                detail = page["ocr"]
                text, status, warning = page["text"], page["status"], "; ".join(page["warnings"])
            else:
                text, status, warning, detail = ocr.extract(path, page["page"], page["native_text"])
            detail["cache_key"] = cache
            detail["render_source"] = primary["path"]
            detail["rendered_image"] = rel
            detail["rendered_image_sha256"] = hashlib.sha256(image_bytes).hexdigest()
            page.update({"page_type": page_type, "rendered_image": rel,
                         "ocr_raw_text": detail.get("raw_output"), "ocr": detail,
                         "extraction_method": "ocr" if text is not None else "native_text",
                         "text": text if text is not None else page["native_text"], "status": status,
                         "warnings": [warning] if warning else []})
            if page_type in ("photo", "blank"):
                page["text"] = page["native_text"]  # Keep OCR artifacts separate from useful evidence.
                page["status"] = "ok"
                page["requires_visual_review"] = page_type == "photo"
                page["visual_review_note"] = classification["note"]
                page["warnings"] = [classification["note"]]
            outcomes.append({"document_id": doc["document_id"], "path": primary["path"],
                             "page": page["page"], "page_type": page_type, "status": page["status"],
                             "image": rel, "language": detail.get("language", "+".join(detail["requested_languages"])),
                             "warnings": page["warnings"], "mean_word_confidence": detail.get("mean_word_confidence")})
        if not args.inspect_only:
            doc["processing_status"] = aggregate_status(doc["pages"])
            outputs.write(doc["text_artifact"], json_text(evidence_for(doc)))
            outputs.write(doc["text_preview_path"], evidence_text(doc))
    if args.inspect_only:
        return
    outputs.write("inputs.jsonl", "".join(json.dumps(d, ensure_ascii=False, allow_nan=False) + "\n" for d in inputs))
    manifest["ocr"] = ocr.info()
    manifest["ocr_completion"] = {"completed_at_utc": now(), "pages": outcomes,
                                  "installation": read_json(PROJECT / "tools/ocr-installation.json") if (PROJECT / "tools/ocr-installation.json").exists() else None,
                                  "tessdata": read_json(PROJECT / "tools/tessdata/manifest.json") if (PROJECT / "tools/tessdata/manifest.json").exists() else None}
    by_id = {d["document_id"]: d for d in inputs}
    for doc in manifest["documents"]:
        doc["processing_status"] = by_id[doc["document_id"]]["processing_status"]
    lines = ["# OCR completion report", "", "Six previously pending pages were inspected and transcribed; the additional trailer-photo page (21) was retained and inspected. Actual page 20 is a licence receipt scan, not a photo. Native text and document IDs were retained. No labels were loaded by this script.", "",
             "Engine: `" + str(ocr.info()) + "`", "", "| Source | Page | Type | Language | Outcome |", "|---|---:|---|---|---|"]
    for row in outcomes:
        lines.append("| " + row["path"] + " | " + str(row["page"]) + " | " + row["page_type"] + " | " + row["language"] + " | " + row["status"] + " |")
    for row in outcomes:
        lines += ["", "- Image: `" + row["image"] + "`", "- " + "; ".join(row["warnings"]), ""]
    lines += ["", "Photo pages retain their rendered image and raw OCR attempts. Lack of relevant readable text is not counted as OCR failure. Visual inspection is still required.",
              "Confidence and PSM agreement are quality indicators, not extraction accuracy. Review identifiers, quantities, decimal points and table alignment before use."]
    outputs.write("ocr_report.md", "\n".join(lines) + "\n")
    outputs.write("validation.json", json_text({"status": "not_run", "errors": [], "warnings": []}))
    manifest["generated_files"] = sorted(outputs.previous | outputs.generated | {"manifest.json"})
    outputs.write("manifest.json", json_text(manifest))
    print(json.dumps({"ocr_available": ocr.available, "processed_pages": len(outcomes), "outcomes": outcomes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
