# Rate-Filing PDF → Bill Pay (Excel)

Carrier-agnostic pipeline that ingests a U.S. auto-insurance rate-filing PDF and
extracts ONLY **Bill-Pay** information (payment plans + billing fees) into one
flat `Bill_Pay` sheet. The LLM reads the parsed document, understands the
billing content, and returns structured rows with an **Accuracy Score**
(its confidence per row). No page locations are hardcoded. Validated on GEICO NJ
(746pp) and Progressive Garden State NJ (`split_part_1.pdf`, 700pp). This is a
POC; a later phase adds a `chunks` column for RAG. See `PLAN.md`.

## How it works

1. **Load text** — uses a `docling-serve` parse cached at `input/<pdf>.docling.json`
   if present (e.g. GEICO's scanned manual), otherwise reads the PDF's text layer
   directly with pdfplumber (e.g. Progressive). Docling is optional.
2. **Company hint** — `metadata.detect` (LLM) reads the filing's company name to
   fill the `Company` column consistently.
3. **Find bill-pay pages** (`billpay.find_billpay_pages`) — a fast, deterministic
   keyword scan (bill plan / installment / down payment / NSF / late fee / ...).
4. **Extract** (`billpay.extract_billpay`) — the LLM (`gpt-4o`) reads those pages
   and returns Bill-Pay rows: one per payment plan and one per flat billing fee,
   copying numbers as written and scoring its own confidence. Other content
   (rating factors, coverages, vehicle symbols, territory) is ignored.

Output columns: `Company, Fee Type, Payment Plan, Fee, Eligibility Rule,
Downpayment Amount, Downpayment Unit, Each Installment Value, Each Installment
Unit, Source Page, Accuracy Score`.

> Note: a number of modules from the earlier "universal extractor" design
> (`discover.py`, `factors.py`, `extractors/`, `sheet_builders.py`, `geico_view.py`,
> `validators.py`, `prose_parser.py`, `mapper.py`, `classifier.py`, `profile.py`)
> are now **unused** and kept only until the repo is committed and they can be
> safely removed.

## Setup

```bash
uv venv -p 3.12
uv pip install -e .
cp .env.example .env        # fill in OPENAI_API_KEY

# Docling Serve (CPU) on localhost:5001
docker compose up -d
```

Environment (`.env`): `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL_EXTRACT`
(gpt-4o), `LLM_MODEL_CLASSIFY` (gpt-4o-mini), `DOCLING_SERVE_URL`
(http://localhost:5001), `INPUT_DIR`, `OUTPUT_DIR`.

## Run

```bash
# CLI (no Airflow)
uv run rate-filing run input/geico.pdf
uv run rate-filing run --all          # every PDF in INPUT_DIR

# Cache the Docling parse only
uv run python -m rate_filing.parse input/geico.pdf

# Airflow 3 (new SDK): dags/rate_filing_pipeline.py
```

Output: `output/<pdf>.xlsx` — a single `Bill_Pay` sheet.

## Schema

The contract lives in one place: `src/rate_filing/schema.py` — a single
`Bill_Pay` sheet. Columns: `Company, Fee Type, Payment Plan, Fee, Eligibility
Rule, Downpayment Amount, Downpayment Unit, Each Installment Value, Each
Installment Unit, Source Page, Accuracy Score`. Layout: row 1 title, row 2 note,
row 4 headers, row 5+ data. `Source Page` = 1-based PDF page.

## Tests

```bash
uv run pytest tests/test_billpay.py tests/test_schema_contract.py
```

`test_billpay` checks the page finder + schema columns (offline, no LLM).
`test_schema_contract` checks the sheet/columns. (Older tests under `tests/`
target the retired universal-extractor modules and can be removed.)
