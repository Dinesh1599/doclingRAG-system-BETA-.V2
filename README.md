# Rate-Filing PDF → Bill Pay (Excel + RAG)

Carrier-agnostic pipeline that ingests a U.S. auto-insurance rate-filing PDF and
extracts ONLY **Bill-Pay** information (payment plans + billing fees) into one
flat `Bill_Pay` sheet. The LLM reads the parsed document, understands the
billing content, and returns structured rows with an **Accuracy Score**
(its confidence per row). No page locations are hardcoded. Validated on GEICO NJ
(746pp) and Progressive Garden State NJ (`split_part_1.pdf`, 700pp).

The full document is also chunked, embedded, and stored in **Postgres + pgvector**;
each bill-pay row links back to the chunk(s) it came from (by page), so the Excel
`Chunks` column and the `bill_pay` table both point into the vector store for
retrieval/RAG.

## How it works

0. **Relevance triage** (`triage.py`) — before any expensive work, each PDF is
   screened: a cheap first-pages text sample is scored for insurance/rate-filing
   signals. Clear filings pass, clear non-filings (offer letters, invoices,
   reports) are skipped, and only genuinely borderline files cost one
   `gpt-4o-mini` call. So you can drop 10 mixed PDFs in `input/` and only the
   real filings run through the pipeline; skipped files are listed (with reason)
   in the console. Use `run(..., skip_triage=True)` to bypass.
1. **Load text — per-page router** (`page_router.py`) — for each page, pdfplumber
   checks whether it has a real text layer or is a scanned image (little/no text
   AND mostly covered by a raster image). Text pages are read instantly with
   pdfplumber; **only the scanned pages are sent to docling for OCR**, then
   everything is merged in memory by global page number. No cache file is read or
   written. This handles mixed PDFs correctly: e.g. `geico.pdf` is 730/746 digital
   pages with 16 scanned ones (incl. the bill-pay pages 4–8) — only those 16 are
   OCR'd. Docling runs **in-process** via the `docling` package (no Docker); on
   Apple Silicon it uses MPS.
2. **Company hint** — `metadata.detect` (LLM) reads the filing's company name to
   fill the `Company` column consistently.
3. **Find bill-pay pages** (`billpay.find_billpay_pages`) — a fast, deterministic
   keyword scan (bill plan / installment / down payment / NSF / late fee / ...).
4. **Chunk + embed (RAG)** (`chunking.py`, `vectordb.py`) — the full document is
   split into per-page chunks, embedded with `text-embedding-3-small` (1536-dim),
   and stored in the Postgres `chunks` table (pgvector, HNSW cosine index). Idempotent
   per source PDF.
5. **Extract** (`billpay.extract_billpay`) — the LLM (`gpt-4o`) reads the bill-pay
   pages and returns Bill-Pay rows: one per payment plan and one per flat billing
   fee, copying numbers as written and scoring its own confidence. Other content
   (rating factors, coverages, vehicle symbols, territory) is ignored.
6. **Link + store** (`vectordb.store_bill_pay`) — each row is written to the
   `bill_pay` table and linked to the chunk(s) on its `Source Page` via the
   `bill_pay_chunks` join table (a true FK into `chunks`). The same chunk ids fill
   the Excel `Chunks` column. If `DATABASE_URL` is empty/unreachable, the run
   degrades to Excel-only.

Output columns: `Source File, Company, SERFF #, RFC #, Fee Type, Payment Plan,
Fee, Eligibility Rule, Downpayment Amount, Downpayment Unit, Each Installment
Value, Each Installment Unit, Effective Date, End Date, Source Page, Accuracy
Score`. `SERFF #`/`RFC #` are the filing identifiers (deterministic regex from
the cover; blank only for documents that carry none). `Effective Date` is read
from the rule/section header by the LLM; `End Date` is usually blank (rate
filings stay in force until superseded).

## Setup

```bash
uv venv -p 3.12
uv sync                     # installs docling (+ torch) and the OCR engine
cp .env.example .env        # fill in OPENAI_API_KEY
```

No Docker needed for docling — it runs in-process. The OCR engine is selected
automatically by platform: `uv sync` installs Apple Vision (`ocrmac`) on macOS
and EasyOCR on Windows/Linux (platform markers in `pyproject.toml`), and
`DOCLING_OCR_ENGINE=auto` picks the right one at runtime. (The vector store still
needs a Postgres+pgvector container — see Vector store below.)

Environment (`.env`): `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL_EXTRACT`
(gpt-4o), `LLM_MODEL_CLASSIFY` (gpt-4o-mini), `DOCLING_OCR_ENGINE` (`auto`),
`DOCLING_TABLE_MODE` (`fast`), `DOCLING_DO_OCR`, `DOCLING_NUM_THREADS`,
`DOCLING_OCR_MIN_CHARS` (50), `DOCLING_OCR_MIN_IMAGE_COVERAGE` (0.5),
`INPUT_DIR`, `OUTPUT_DIR`.

## Run

```bash
# CLI (no Airflow) — all PDFs are appended into ONE workbook
uv run rate-filing run input/geico.pdf input/split_part_1.pdf
uv run rate-filing run --all          # every PDF in INPUT_DIR

# Airflow 3 — event-driven (see below)
./scripts/run_airflow.sh
```

No pre-processing step: drop a PDF in `input/` and run. The per-page router OCRs
any scanned pages on the fly (in memory); nothing needs to be cached first.

### Airflow — event-driven ingestion

`dags/rate_filing_pipeline.py` defines `rate_filing_ingest`, scheduled on an
**Asset** watched by a custom `FileArrivalTrigger` (the standard provider only
ships file *delete* event triggers): when a `*.pdf` appears in `input/`, the
triggerer emits an asset event that starts a run. The run processes every PDF
and then archives it out of `input/` — relevant files to `processed/`, irrelevant
(triaged-out) ones to `skipped/` — which clears the watch path so the next
arrival re-triggers.

```bash
uv sync --extra airflow          # one-time: install Airflow 3 + standard provider
./scripts/run_airflow.sh         # starts scheduler + triggerer + UI (localhost:8080)
```

The script uses a repo-local `AIRFLOW_HOME=.airflow`, points the dags folder at
`dags/`, and exports `.env` so tasks get `OPENAI_API_KEY`/`DATABASE_URL`. The DAG
is already unpaused. Note: the trigger polls (`poll_interval=10s`) in the
triggerer — event-driven to the DAG, polling underneath (no OS inotify). Starting
Airflow while PDFs are already in `input/` will immediately kick off a run on them.

Output: one combined `output/bill_pay.xlsx` (single `Bill_Pay` sheet) with rows
from every processed PDF appended; the `Source File` column says which PDF each
row came from.

## Schema

The contract lives in one place: `src/rate_filing/schema.py` — a single
`Bill_Pay` sheet. Columns: `Source File, Company, SERFF #, RFC #, Fee Type,
Payment Plan, Fee, Eligibility Rule, Downpayment Amount, Downpayment Unit, Each
Installment Value, Each Installment Unit, Effective Date, End Date, Source Page,
Accuracy Score, Chunks`. Layout: row 1 title, row 2 note, row 4 headers, row 5+
data. `Source Page` = 1-based PDF page; `Chunks` = the linked vector-store chunk
id(s).

## Vector store (Postgres + pgvector)

`docker run` a `pgvector/pgvector:pg16` image (this repo uses one on port 5436);
set `DATABASE_URL`. The pipeline creates/uses three tables:

| Table | What |
|---|---|
| `chunks` | per-page text + `vector(1536)` embedding (+ `page_start`/`page_end`), HNSW cosine index |
| `bill_pay` | one row per extracted bill-pay record |
| `bill_pay_chunks` | M:N link `bill_pay.id` ↔ `chunks.id` (page-provenance FK) |

Each bill-pay row links to the chunk(s) on its `Source Page`. Retrieval example —
nearest chunks to a query vector:

```sql
SELECT source, page_start, content
FROM chunks ORDER BY embedding <=> :query_vector LIMIT 5;
```

Writes are idempotent per source PDF (re-running replaces that file's chunks and
rows). Empty/unreachable `DATABASE_URL` → Excel-only.

## Chat agent (RAG over Postgres)

A FastAPI app (`rate_filing.chat_app`, UI in `web/chat.html`) answers questions
from the vector store: it embeds the question, retrieves the nearest `chunks` by
cosine similarity, and the LLM answers using those chunks **plus** the full
`bill_pay` table as structured context, citing source file + page.

```bash
uv run uvicorn rate_filing.chat_app:app --port 8000   # then open http://localhost:8000
```

Endpoints: `GET /` (chat UI), `POST /chat {question, top_k?}` → `{answer, sources[]}`,
`GET /health`. Needs `OPENAI_API_KEY` + a reachable `DATABASE_URL`.

## Tests

```bash
uv run pytest tests/test_billpay.py tests/test_schema_contract.py
```

`test_billpay` checks the page finder + schema columns (offline, no LLM).
`test_schema_contract` checks the sheet/columns. (Older tests under `tests/`
target the retired universal-extractor modules and can be removed.)

## Performance — docling (in-process)

docling OCR is only run on the **scanned pages** the router flags; pages with a
clean text layer (most of GEICO, all of Progressive) are read by pdfplumber and
never touch docling. So cost scales with the number of scanned pages, not the
document size — a 700-page mostly-digital PDF with a handful of scanned pages
OCRs only that handful. The **first** OCR on a machine downloads the
layout/tableformer models (~hundreds of MB, one time). After that, warm speed on
this Apple Silicon Mac is ≈**1.0 s/page** with `DOCLING_TABLE_MODE=fast` and
≈2.4 s/page with `accurate`. Because the bill-pay extractor reads text only (not
table grids), `fast` is the default and yields identical text. Tuning, by impact:

1. **Acceleration is automatic.** docling's accelerator is set to `AUTO`, so it
   uses Apple **MPS** here, **CUDA** on an NVIDIA box, else CPU — no config.
2. **`DOCLING_TABLE_MODE=fast`** (default) ≈2× faster than `accurate`; only switch
   to `accurate` if you later need precise table structure.
3. **`DOCLING_NUM_THREADS`** (default 4) — raise toward your physical core count.
4. **OCR engine** (`DOCLING_OCR_ENGINE`): `auto` picks Apple Vision (`ocrmac`) on
   macOS — fast, native, no model download. On Windows/Linux use `easyocr`
   (GPU-capable) or `rapidocr` (light, ONNX/CPU).
5. The parse is **cached** per document, so all of the above is a one-time cost.
