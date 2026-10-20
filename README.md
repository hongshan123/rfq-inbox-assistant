# RFQ Inbox Assistant

Local Streamlit demo for reviewing manufacturing RFQs, invoice attachments and blank RFQ templates. The interface is in English. Documents are parsed locally; model extraction requires your own configured service.

## Public repository contents

This repository includes application code, the three fictional EML/XLSX demonstrations, and official English/Arabic OCR traineddata with its licence and provenance. API keys, virtual environments, uploads, model responses, confirmed reviews, downloaded reference documents and processed source copies remain local and are ignored by Git.

After cloning, install dependencies, copy `.env.example` to `.env` and fill your own model settings. The three synthetic RFQ examples are included. The invoice, historical RFQ and blank-template examples, dataset validation and historical local report links require the optional data described in [data/README.md](data/README.md). The environment and engine described below record the original development machine; a fresh clone needs its own Python environment and Tesseract installation.

## Run on Windows

Open PowerShell in this project directory. The existing environment is `.venv-rfq-data` (Python 3.9.1); Streamlit 1.50.0 is installed and compatible with it.

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
```

Open **http://127.0.0.1:8501**. Keep that terminal running; Ctrl+C stops the server. To recreate an environment:

```powershell
python -m venv .venv-rfq-data
& '.\.venv-rfq-data\Scripts\python.exe' -m pip install -r requirements-rfq-app.txt
```

`requirements-rfq-app.txt` records the installed application dependencies. `requirements-rfq-data.txt` is the smaller data-processing dependency set. On this machine the old Python OpenSSL build could not connect to PyPI. `scripts/install_local_dependencies.py` is the fallback: it uses system curl with HTTPS certificate verification, checks official PyPI wheel SHA-256 hashes, then installs offline. For example:

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' scripts/install_local_dependencies.py streamlit==1.50.0
```

## Model configuration

With no configuration the UI displays **Model not configured**, disables model extraction and leaves fields empty for manual review. No gold answers or synthetic references are used to fill them.

Copy `.env.example` to `.env`, then fill these values locally (or set the same environment variables). Restart Streamlit after changing configuration.

| Setting | Meaning |
|---|---|
| `RFQ_MODEL_BASE_URL` | OpenAI compatible API base URL, including `/v1` if required |
| `RFQ_MODEL_NAME` | Model identifier accepted by your provider |
| `RFQ_MODEL_API_KEY` | Provider key; optional only for localhost services |
| `RFQ_MODEL_TIMEOUT` | Request timeout in seconds; default 120 |
| `RFQ_MODEL_JSON_MODE` | `true` requests JSON object mode; set `false` for compatible services without that option |
| `RFQ_MODEL_TRUST_ENV` | `false` connects directly without inherited system/environment proxies; default `true` |

The adapter in `rfq_inbox/model.py` calls `POST <base>/chat/completions`. See the [official API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create). Remote services require HTTPS. Never commit `.env` or Streamlit secrets. Parsing works without a model; clicking **Run model extraction** sends parsed source text to the configured provider. There are no model tools, command execution or email sending. Source content is explicitly treated as untrusted data.

No real model request was made during delivery because no provider/model/key was configured. Contract tests use a local HTTP test double and are not model accuracy measurements.

If extraction reports `ProxyError`, check the local proxy. On Windows, Requests can inherit a registry proxy even when no `HTTP_PROXY` environment variable exists. To connect directly, set `RFQ_MODEL_TRUST_ENV=false` in `.env` and restart the app. This affects only model requests, preserves TLS certificate verification, and does not change Windows proxy settings.

## Review workflow

1. Open a sidebar example or upload EML/PDF/XLSX files (one batch is one case, up to 50 MB). EML body and attachments keep their own document IDs and relationships. Identical attachment bytes are parsed once; duplicate references remain visible.
2. Inspect the original on the left: email headers/body, each PDF page/image/text, or Excel sheets/cells/formulas/cached values. Uploaded files actually pass through the parsers regardless of filename.
3. Select the appropriate RFQ, invoice or template review schema. A blank template has no customer order or item lines. Historical Narrogin content is marked for manual checking.
4. With a model configured, run extraction. Fields carry value/status/evidence; PDF page and Excel sheet/cell quotes are checked against parsed sources. This check establishes that the quote exists, not that its meaning proves the value. Review the source with **View evidence**.
5. Edit fields and item rows, resolve conflicts explicitly, then **Confirm & save review**. Until then downloads are withheld. Further edits require another confirmation. A manual value without verified source evidence remains flagged for review and is recorded as user supplied.
6. Download confirmed JSON, an XLSX item table, a summary and an English missing-information email draft. Drafts use current missing/conflicting fields only. Downloads and local saves do not send email.

RFQ quote deadline and delivery date are separate. Invoice supplier/buyer, invoice dates, currency and amounts have a separate schema. Conflicts preserve candidates and sources instead of choosing a value automatically. A limited literal quantity cross-check also flags the demonstration's labelled email quantity and Excel quantity; it is not general model extraction.

## Files and audit trail

| Location | Contents |
|---|---|
| `data/raw/rfq-demo/` | Original downloaded files, source manifest, licence and publisher answers |
| `data/processed/rfq-demo/` | Stable input IDs, parsed pages/cells, separate `gold.jsonl`, validation, OCR reports/images |
| `data/synthetic/rfq-inbox/` | Fictional A complete, B missing material, C email 100 / Excel 120 inputs |
| `data/synthetic/rfq-inbox/references/` | Independent demonstration references; runtime never opens them |
| `data/local-inbox/cases/<case_id>/` | Uploaded source bytes, parsed case, immutable model prediction files, separate confirmed `review.json` and `exports/` |

Case files are local and persist after a session. `RFQ_CASE_STORAGE` optionally selects another case storage directory. Do not place sensitive uploads in a shared project directory. Reopening identical inputs restores a saved review as the editable draft; confirm it again before downloading. Existing model audit files remain on disk.

`scripts/create_synthetic_cases.py` defines fictional facts before generating EML/XLSX inputs and independent references. Addresses use example.com; B really omits material; C retains both source quantities. These are functional examples, not independent accuracy benchmarks. The application and OCR code do not load `gold.jsonl`, `expected.json` or the synthetic references. The separate validator/evaluation layer can read labels.

## Local OCR

Installed engine: `C:\Program Files\Tesseract-OCR\tesseract.exe`, version `tesseract v5.5.3.20260724`. Installation used the official winget package `tesseract-ocr.tesseract`. Provenance is in `tools/ocr-installation.json`. Local `eng` and `ara` traineddata and Apache licence are in `tools/tessdata/`, with pinned official repository revision and hashes in its manifest. No automatic orientation detection is used, so `osd` is not required.

Override the engine with `TESSERACT_CMD`, and language directory with `TESSDATA_PREFIX`. English pages use eng; mixed pages eng+ara; the Arabic-dominant scan uses ara+eng because it also contains Latin identifiers. New uploads offer a language selector. Pages render individually at 300 DPI. This Windows engine has a Unicode absolute-path issue: Python retains the original Chinese paths and invokes the real OCR executable with ASCII relative temporary image/output paths from the project directory. The pytesseract binding's real engine probe also succeeds.

Incrementally complete existing pending/OCR pages and validate:

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' scripts/complete_ocr.py
& '.\.venv-rfq-data\Scripts\python.exe' scripts/validate_rfq_data.py --data-dir data/processed/rfq-demo
```

The OCR cache keys source/page/render/engine/language/traineddata/quality version. Native text, raw OCR, final text, confidence/boxes, alternate PSM transcription, page images and quality flags are retained. Six formerly pending scan pages now have real OCR, all six remain `needs_review` for numeric/identifier/table uncertainty. Actual Narrogin page **20 is a licence receipt scan**; the trailer photos are page **21**, retained with raw photo OCR and a visual-review notice. Review the image before interpreting cropped plate text.

Reports: [OCR](data/processed/rfq-demo/ocr_report.md), [processing](data/processed/rfq-demo/processing_report.md), [preservation audit](data/processed/rfq-demo/ocr_preservation_audit.json), [acceptance](data/processed/rfq-demo/app_acceptance_report.md). OCR confidence and nonempty text do not establish field accuracy. No independent gold comparison was claimed for model predictions; the historical RFQ has no field gold.

## Validation and regression checks

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' -m unittest discover -s tests -v
& '.\.venv-rfq-data\Scripts\python.exe' -m pip check
```

The suite covers data/label separation, raw preservation, incremental parsing, damaged files, real PDF page rendering, Excel cell evidence, duplicate/distinct attachments, no-model manual editing, confirmed exports, conflict retention and real Streamlit widget execution through AppTest. Test data and generated predictions are explicitly synthetic/test-only. AppTest is functional execution; no connected browser was available for manual visual inspection during delivery.

## Source licences

SalorWorks invoice test pack: CC BY 4.0; original README, LICENSE, CITATION.cff, schema and fixtures retained. Narrogin PDF and Smartsheet Excel have no confirmed open redistribution licence and are local reference materials. Do not infer current procurement terms from the historical PDF or real purchase data from blank template cells. See `data/raw/rfq-demo/manifest.json` for source URLs, hashes and download records.
