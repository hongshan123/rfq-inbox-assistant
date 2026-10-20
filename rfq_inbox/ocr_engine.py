"""Local OCR and page rendering. No labels, network requests or business-field inference."""
import hashlib
import io
import re
import csv
import subprocess
import tempfile
from pathlib import Path
from statistics import mean


def render_page(path, page_number, dpi=300):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                return bitmap.to_pil().copy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()


def png_bytes(image):
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def recognize(image, engine, languages, tessdata, version, compare=True):
    # The current Windows Tesseract build rejects non-ASCII absolute paths.
    # Python renders Unicode source paths; Tesseract receives ASCII relative paths
    # with a Unicode-aware CreateProcess cwd. No global chdir or shared temp state.
    project = Path(__file__).resolve().parents[1]
    temp_root = project / "data/local-inbox/ocr-temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    data_args = []
    if tessdata:
        try:
            data_path = Path(tessdata).resolve().relative_to(project).as_posix()
        except ValueError:
            data_path = str(tessdata)
        if not data_path.isascii():
            raise ValueError("Place language files under project tools/tessdata for this Windows engine")
        data_args = ["--tessdata-dir", data_path]
    lang = "+".join(languages)
    config = "--oem 1 --psm 3"
    with tempfile.TemporaryDirectory(prefix="ocr-", dir=str(temp_root)) as directory:
        work = Path(directory).resolve()
        work.relative_to(temp_root.resolve())
        image_path = work / "page.png"
        image.save(image_path)
        source = image_path.relative_to(project).as_posix()
        base = (work / "result").relative_to(project).as_posix()
        command = [engine, source, base, "-l", lang] + data_args + ["--oem", "1", "--psm", "3", "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=1"]
        subprocess.run(command, cwd=str(project), capture_output=True, check=True, timeout=120)
        raw = (work / "result.txt").read_text(encoding="utf-8")
        records = list(csv.DictReader(io.StringIO((work / "result.tsv").read_text(encoding="utf-8")), delimiter="\t"))
        alternate = None
        if compare and raw.strip():
            alt = [engine, source, base + "-psm6", "-l", lang] + data_args + ["--oem", "1", "--psm", "6"]
            subprocess.run(alt, cwd=str(project), capture_output=True, check=True, timeout=120)
            alternate = (work / "result-psm6.txt").read_text(encoding="utf-8")
    words = []
    for row in records:
        word = row.get("text") or ""
        if not word.strip():
            continue
        words.append({"text": word, "confidence": float(row["conf"]),
                      "bbox": [int(row[k]) for k in ("left", "top", "width", "height")]})
    confidences = [w["confidence"] for w in words if w["confidence"] >= 0]
    average = mean(confidences) if confidences else 0
    warnings = ["OCR text is machine transcription and has not been human-confirmed"]
    flags = []
    if not raw.strip() or len(words) < 3:
        flags.append("Insufficient readable text")
    if average < 80:
        flags.append("Mean word confidence below 80")
    uncertain_numbers = [w["text"] for w in words if re.search(r"\d", w["text"]) and w["confidence"] < 75]
    if uncertain_numbers:
        flags.append("Low-confidence identifiers/numbers/decimals require review")
    if raw.count("\ufffd") or re.search(r"\d\s+\.\s+\d", raw):
        flags.append("Suspect characters or split decimal point")
    numbers = lambda text: sorted(re.findall(r"\d+(?:[.,]\d+)*", text))
    if alternate is not None:
        if numbers(raw) != numbers(alternate):
            flags.append("Numeric tokens differ between PSM 3 and PSM 6; check table rows and decimals")
    detail = {"raw_output": raw, "alternate_raw_output": alternate, "language": lang,
              "engine": engine, "engine_version": version, "parameters": config,
              "tessdata_dir": str(tessdata) if tessdata else None, "words": words,
              "mean_word_confidence": round(average, 2), "quality_flags": flags,
              "uncertain_numeric_tokens": uncertain_numbers,
              "image_sha256": hashlib.sha256(png_bytes(image)).hexdigest()}
    return raw, ("needs_review" if flags else "ok"), warnings + flags, detail
