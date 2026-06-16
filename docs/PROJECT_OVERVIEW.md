# Doclin-RAG — Insurance Rate-Filing → Bill-Pay Extraction + RAG

**Phase-1 POC.** A pipeline that ingests U.S. auto-insurance rate-filing PDFs,
automatically decides which are worth processing, extracts structured **bill-pay**
information (payment plans, downpayments, installments, billing fees), stores it
in a **Postgres + pgvector** knowledge base with full page-level traceability, and
exposes a **chat agent** that answers questions with cited sources. It runs on
demand (CLI) or automatically on file arrival (Airflow).

---

## 1. The problem

Insurance carriers file hundreds of pages of rate/rule manuals with each state.
Buried inside are the **billing rules** — how customers can pay (1-pay, monthly
installments), downpayment amounts, EFT/autopay discounts, NSF/late fees. Pulling
that out by hand across many carriers and thousands of pages is slow and
error-prone. These PDFs are also inconsistent: some are born-digital, some are
scanned images, and many filings contain *no* billing content at all (rate-level
or rating-model changes).

**Goal:** drop in any pile of filings → automatically get clean, structured,
queryable bill-pay data, with every value traceable back to the exact source page.

---

## 2. End-to-end flow (one PDF)

```
PDF ─▶ [1 Triage] ─▶ [2 Per-page Router] ─▶ [3 Identity] ─▶ [4 Bill-pay Gate]
                                                                   │
        skip ◀── not a filing            skip ◀── no billing content
                                                                   ▼
                              [5 Chunk + Embed] ─▶ [6 Extract] ─▶ [7 Link + Store]
                                                                   ▼
                                            Postgres (chunks · bill_pay · links)
                                                                   ▼
                                                       [8 Chat agent / RAG]
```

Two independent entry points run this exact flow:
- **CLI** — `rate-filing run --all`
- **Airflow** — auto-triggered when a PDF appears in `input/`

---

## 3. Each step in detail

### Step 1 — Relevance triage (`triage.py`)
**Decides: is this even an insurance filing?** Cheap and first, before any heavy work.
- Samples the first ~6 pages of text (pdfplumber, no OCR).
- Counts distinct insurance/rate-filing keyword signals (`rate manual`, `private
  passenger`, `SERFF`, `NAIC`, `premium`, `coverage`, `installment`, …).
- **≥3 signals → relevant.  0 signals → skip.  1–2 (borderline) → one cheap
  `gpt-4o-mini` call breaks the tie.**
- A scanned doc with no extractable text is processed by default (inclusive — never
  silently drop a real scanned filing).
- *Result:* offer letters, invoices, resumes, random reports are filtered out for free.

### Step 2 — Per-page router (`page_router.py`) — *cache-free*
**Decides, per page: read the text, or OCR it?** This is the core efficiency trick.
- For each page, pdfplumber checks: how much real text? how much of the page is a
  raster image?
- **Has a text layer → pdfplumber reads it instantly** (exact, no ML, milliseconds).
- **Scanned image (little text + mostly image) → that page only goes to docling OCR.**
- Blank/vector pages are skipped.
- Scanned pages are grouped into contiguous runs and merged back by global page number.
- **No cache file is written or read** — everything is in memory.
- *Why it matters:* a 1,366-page mostly-digital manual with 3 scanned pages OCRs
  only those 3 — seconds, not ~30+ minutes if docling ran on every page.

> **docling vs pdfplumber:** pdfplumber = "read text that's already there" (cheap,
> exact). docling = "look at a picture and figure out the words" (ML/OCR, ~1–2.4s/
> page) — only worth it where there's no text. docling runs **in-process** (no
> Docker service) and uses **Apple MPS** (or CUDA on NVIDIA) for acceleration. The
> OCR engine is platform-aware: **ocrmac** (Apple Vision) on macOS, **EasyOCR** on
> Windows/Linux — selected automatically.

### Step 3 — Filing identity (`metadata.py`)
- **Company** name(s) read by `gpt-4o-mini` from the cover/first pages.
- **SERFF #** and **RFC #** (the filing's ID numbers) read by deterministic regex —
  never the LLM. (SERFF # = the whole submission's tracking number, e.g.
  `PRGS-134119987`; RFC # = a component/library code, e.g. `NJP61989`.)

### Step 4 — Bill-pay content gate (`billpay.find_billpay_pages`)
**Decides: does this filing actually contain billing content?** Runs *before* any
embedding/extraction so we don't waste compute.
- Deterministic keyword scan over the whole document for bill-pay terms (strong:
  `payment plan`, `installment`, `down payment`, `paid in full`, …; weak: `NSF`,
  `late fee`, `EFT`, `billing`, …).
- **0 candidate pages → skip** ("no bill-pay content") — no embedding, no extraction.
- *Result:* genuine filings with no billing content (rate-level changes, rating-model
  tweaks, loss-cost revisions, memos) are correctly skipped.

### Step 5 — Chunk + embed (`chunking.py`, `clients.embed`)
- The document is split into **per-page chunks** (~800 chars, never spanning pages —
  so each chunk has one definite page).
- Each chunk is embedded with **OpenAI `text-embedding-3-small`** (1536-dim).
- Stored in the Postgres `chunks` table (pgvector, **HNSW cosine index**).

### Step 6 — Bill-pay extraction (`billpay.extract_billpay`)
- `gpt-4o` reads the bill-pay candidate pages and returns structured rows: one per
  payment plan, one per flat fee.
- Copies numbers exactly as written; splits value vs. unit (e.g. `20` + `%`).
- Returns a **per-row Source Page** (the exact page it found each row on) and a
  self-reported **Accuracy Score** (0–1 confidence).

### Step 7 — Link + store (`vectordb.store_bill_pay`)
- Each row is written to the `bill_pay` table.
- Each row is linked to the chunk(s) **on its source page** via the
  `bill_pay_chunks` join table — a true foreign-key, **page-provenance** link.
- Dates (`Effective Date`, `End Date`) are parsed from messy text
  (`"Effective 03/01/2014"` → `2014-03-01`) into real `DATE` columns.

### Step 8 — Chat agent / RAG (`chat_app.py`, `web/chat.html`)
- A FastAPI app + simple web UI.
- A question is embedded → nearest `chunks` retrieved by cosine similarity → the
  LLM answers using those chunks **plus** the structured `bill_pay` rows as context.
- Answers **cite the source file and page**, and the UI shows the retrieved chunks.

---

## 4. The data model (Postgres + pgvector)

| Table | What it holds |
|---|---|
| `chunks` | per-page text + `vector(1536)` embedding + `page_start`/`page_end`; HNSW cosine index |
| `bill_pay` | one row per extracted bill-pay record (company, fees, plans, dates, accuracy) |
| `bill_pay_chunks` | M:N link — which chunk(s) each bill-pay row came from (page provenance) |

**`bill_pay` columns:** Source File · Company · SERFF # · RFC # · Fee Type ·
Payment Plan · Fee · Eligibility Rule · Downpayment Amount/Unit · Each Installment
Value/Unit · Effective Date · End Date · Source Page · Accuracy Score · Chunks.

Writes are **idempotent per source PDF** (re-running a file replaces only its own
data). If the DB is unreachable, the run degrades gracefully (no crash).

---

## 5. The two-gate skip strategy (why it's accurate)

A file is **processed only if** it's a genuine insurance filing **and** contains
billing content. It is **skipped if** either gate fails:

| Gate | Question | Skip when |
|---|---|---|
| 1 — Triage | Is this an insurance filing? | 0 domain keywords (or LLM says no) |
| 2 — Bill-pay content | Does it have billing content? | 0 bill-pay candidate pages |

This was validated against a varied multi-carrier batch — the code's decisions
**matched an independent human read on every file**:
- **Processed:** Allstate, Liberty Mutual, Farmers, GEICO, Progressive (real manuals)
- **Skipped:** ISO loss-cost revisions, rating-rule filings, rate-increase memos,
  GEICO telematics/rate-only filings (genuinely no billing content)

---

## 6. Automation — Airflow (event-driven)

- DAG `rate_filing_ingest` is scheduled on an **Asset** watched by a custom
  `FileArrivalTrigger` (`airflow_triggers.py`).
- Drop a PDF in `input/` → the triggerer fires an event → a run starts → all PDFs
  are processed → each is **archived** out of `input/`: relevant → `processed/`,
  skipped → `skipped/` (which clears the watch path so the next arrival re-triggers).
- Runs entirely in-process; no separate Docker services except Postgres.

---

## 7. Tech stack

| Concern | Choice |
|---|---|
| Language / packaging | Python 3.12, `uv`, `src/` layout |
| Text extraction | pdfplumber (digital) + docling in-process (OCR) |
| OCR engine | Apple Vision (macOS) / EasyOCR (Win/Linux), auto-selected |
| Acceleration | Apple MPS / NVIDIA CUDA via docling AUTO |
| LLM | OpenAI `gpt-4o` (extract), `gpt-4o-mini` (triage/identity) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) |
| Vector store | Postgres 16 + pgvector (HNSW cosine) |
| API / UI | FastAPI + a single HTML page |
| Orchestration | Apache Airflow 3 (event-driven assets) |

---

## 8. Key engineering decisions (the journey)

1. **docling-serve (Docker) → docling in-process.** Removed the HTTP service;
   docling now runs in the same environment and uses Apple MPS. Warm speed
   ≈1.0 s/page (fast table mode) — faster than the CPU container, no Docker needed.
2. **Pre-built JSON cache → cache-free per-page router.** The old design needed a
   manually pre-OCR'd `<pdf>.docling.json`; without it, mixed PDFs silently lost
   their scanned pages. The router OCRs only what's needed, at runtime, in memory.
3. **Chunk over-linking bug → per-row source page.** A batch's page range
   (`"8-107"`) once linked a single row to 313 chunks. Now the LLM cites the exact
   page per row → **max ~3 links/row, 100% on the correct page.**
4. **Date text → real `DATE` columns.** `"Effective 03/01/2014"` is parsed to a
   proper date.
5. **Added the bill-pay content gate.** Filings with no billing content are now
   skipped *before* embedding — no wasted vector storage.
6. **Platform-conditional OCR.** `pyproject.toml` installs ocrmac on macOS and
   EasyOCR elsewhere, so `uv sync` works on Windows/Linux.

---

## 9. Results / validation

- **GEICO NJ** (746 pp, mixed digital+scanned): 16 bill-pay rows, 4,642 chunks.
  Full end-to-end run ≈ **4–5 min** (incl. OCR of 16 scanned pages + embedding).
- **Progressive** (700 pp, born-digital): 25 rows, 2,771 chunks, no OCR needed.
- **Farmers** (1,366 pp): 57 rows, 6,987 chunks — brand-new carrier, no tuning.
- **Allstate, Liberty Mutual:** processed successfully.
- **Correctly skipped:** ISO loss-cost filings, rate-increase memos, rate/model-only
  GEICO filings.
- **Link integrity:** every bill-pay row links only to chunks on its real source page.
- **Chat:** answers payment-plan / fee questions with exact page citations; declines
  when the answer isn't in the data (no hallucination).

---

## 10. How to run

```bash
uv sync                                   # install (auto-picks OCR engine per OS)
cp .env.example .env                       # add OPENAI_API_KEY
docker compose up -d postgres              # start the vector store (pgvector)

uv run pytest -q                           # 9 offline tests
uv run rate-filing run --all               # process every PDF in input/
uv run uvicorn rate_filing.chat_app:app --port 8000   # chat UI at :8000
./scripts/run_airflow.sh                   # (optional) event-driven ingestion, UI :8080
```

---

## 11. Limitations & next steps

- **Self-reported accuracy.** The Accuracy Score is the model grading itself, not a
  calibrated metric. Upgrade: log-probs, self-consistency voting, or verify the
  number appears on the cited page.
- **Whole-document embedding.** Every processed file is fully embedded; if only
  bill-pay provenance is needed, embed just the relevant pages to cut cost.
- **Row-by-row chunk inserts** are the main speed bottleneck on large files;
  batch `COPY` would cut minutes to seconds.
- **Keyword gates** are accurate today but vocabulary-bound; an LLM "is there
  billing content?" arbiter on borderline files would add a semantic safety net.
- **Sequential processing**; parallelizing the file loop would improve batch throughput.

---

*Phase-1 POC — validated across GEICO, Progressive, Farmers, Allstate, and Liberty
Mutual filings, with automatic skipping of non-billing filings.*
