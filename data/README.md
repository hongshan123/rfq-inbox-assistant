# Demo data

## Included synthetic cases

`synthetic/rfq-inbox/` contains three fictional manufacturing RFQs:

- A: complete key information.
- B: material is absent from both the email and spreadsheet.
- C: the email says quantity 100 while the spreadsheet says 120 for the same part.

Each case has `inputs/request.eml` and `inputs/items.xlsx`. Separate reference facts in `references/` are for development only and are never loaded by the application. These are functional examples, not a real customer dataset or independent accuracy benchmark.

Regenerate them from the project root:

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' scripts/create_synthetic_cases.py
```

## Optional local reference data

Downloaded files, parsed copies and local processing reports are excluded from this public repository. Obtain the original files from their publishers if you need those examples. Do not place API keys or customer documents in the committed synthetic folder.

| Source | Local target, relative to project root | Licence note from the development data record |
|---|---|---|
| [SalorWorks synthetic invoice test pack](https://codeload.github.com/SalorWorks/shopify-invoice-test-pack/zip/refs/heads/main) | `data/raw/rfq-demo/shopify-invoice-test-pack.zip` | CC BY 4.0; retain original attribution, README, LICENSE and CITATION.cff |
| [Historical Narrogin side-tippers RFQ](https://www.narrogin.wa.gov.au/Profiles/narrogin/Assets/ClientData/Documents/TENDER/RFT_202425-05_Supply_of_Two_Side_Tip_Trailers_Complied_Document.pdf) | `data/raw/rfq-demo/narrogin-side-tippers-rfq-2024.pdf` | Open redistribution permission was not confirmed; local reference material |
| [Smartsheet manufacturing RFQ template](https://www.smartsheet.com/sites/default/files/2024-08/ic-excel-manufacturing-rfq-template-12139.xlsx) | `data/raw/rfq-demo/manufacturing-rfq-template.xlsx` | Open redistribution permission was not confirmed; local reference material |

Extract the invoice ZIP into `data/raw/rfq-demo/invoice-test-pack/`, retaining the archive's `shopify-invoice-test-pack-main/` directory and its fixtures/schema/licence files. The development snapshot contained 15 complete PDF/PNG/expected.json scenarios. Publisher content can change; check the downloaded archive rather than assuming that count.

With the expected files installed, generate the local parsed dataset and demonstration emails:

```powershell
& '.\.venv-rfq-data\Scripts\python.exe' scripts/prepare_rfq_data.py --raw-dir data/raw/rfq-demo --out-dir data/processed/rfq-demo --ocr auto
& '.\.venv-rfq-data\Scripts\python.exe' scripts/complete_ocr.py
& '.\.venv-rfq-data\Scripts\python.exe' scripts/validate_rfq_data.py --data-dir data/processed/rfq-demo
```

The historical RFQ has no field answer key. Empty spreadsheet template cells are not purchase facts. OCR scripts do not read publisher answer keys; the separate preparation/evaluation and validation steps keep labels separate from inputs.

## Local application output

`data/local-inbox/` stores uploaded bytes, parsed cases, model outputs and confirmed exports. It is ignored by Git. Optional dataset-dependent regression checks also need the local reference data above; the repository does not redistribute those files through saved EML attachments or OCR/page-text copies.
