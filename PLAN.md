# Plan — Carrier-agnostic rebuild + universal schema

Goal: one pipeline + one output structure that works for GEICO, Progressive, and
other carriers' rate filings. Driven by the evidence from comparing the GEICO NJ
filing (746pp, vehicle-symbol grids) with the Progressive Garden State NJ filing
(~2,800pp, rule manual + factor exhibits + symbol series).

The two filings prove: page locations, table geometry, coverage labels, and even
the number of companies all differ. So **locations must be discovered, and the
output columns must be carrier-neutral** (your suggestion).

---

## Part A — Universal schema (replaces the GEICO-shaped 25 sheets)

The problem with the current 25 sheets: many encode GEICO's exact layout as fixed
column names — e.g. `12_Territory_Rates` has `BI Verbal, BI Zero, PD 5M, …`;
`14_Class_Factors` has `Single Male, Single Female, Married Male, Married Female`;
`17_Symbol_Relativities` has year columns `2010 … 1992`. Progressive's equivalents
are `BI PD COMP COLL LOAN PIP UM/UIM …` keyed by ZIP + driver age, plus dozens of
named "Factor Tables" (Youthful Driver, Annual Miles, Garaging Location, …). No
fixed wide column set fits both.

**Fix: move from WIDE carrier-specific columns to LONG (tidy) carrier-neutral
tables.** A coverage like "BI" or a label like "Single Male" becomes a *value in a
cell*, not a *column name*. Then any carrier's tables collapse into the same shape.

Proposed universal sheets (the new contract in `schema.py`):

1. **00_README** — provenance + how-to (unchanged in spirit).
2. **01_Filing_Identity** — one row per company. Columns: `Company, NAIC, State,
   Line, RFC #, SERFF #, Effective Date, Source Page`. Handles multi-company
   filings (Progressive has two: Garden State + Drive New Jersey).
3. **02_Rules** — raw rule text. `Rule Code, Rule Title, Category, Text,
   Source Page`. Covers GEICO General Rules **and** Progressive B01…V24 manual.
4. **03_Rule_Parameters** — params parsed from prose (LLM prose→param, validated).
   `Rule Code, Parameter, Value, Unit, Applies To / Condition, Source Page`.
   (Absorbs old 21B + 22B.)
5. **04_Rating_Factors** — THE universal long table. Absorbs old 12, 13, 14, 15,
   17 **and** every Progressive "… Factor Table". Columns:
   `Factor Type, Coverage, Dim1 Name, Dim1 Value, Dim2 Name, Dim2 Value,
   Dim3 Name, Dim3 Value, Factor Value, Source Page`.
   - A wide source table with N coverage columns becomes N rows (one per coverage)
     → coverage is data, not a column.
   - GEICO class factors → Factor Type "Class Factor", Dim1 Age, Dim2
     Marital/Gender, Value.
   - Progressive garaging table → Factor Type "Garaging Location", Coverage per
     row, Dim1 ZIP, Dim2 Driver Age, Value.
6. **05_Territory_Definitions** — `ZIP, Territory, Source Page` (carriers that have
   a discrete ZIP→territory map; empty + valid if absent).
7. **06_Vehicle_Symbols** — merges GEICO MBI + VLR + Progressive symbol series into
   one table. `Symbol Type, Model Year, Make, Model, Body Style, Drive,
   Engine/Cyl, Symbol Value, Source Page`. `Symbol Type` distinguishes what GEICO
   split across two sheets.
8. **07_Coverages_Limits_Deductibles** — `Coverage, Option/Limit/Deductible,
   Value, Unit, Source Page`.
9. **08_Fees_Discounts** — `Type (Fee/Discount/Surcharge), Name, Amount, Unit,
   Applies To, Conditions, Source Page`.
10. **09_Source_Map** — sheet → source pages → how detected (discovery vs hint).

All ~10 sheets are carrier-neutral. The old `01_Rules_Facts` "normalized fact"
idea was already the right spirit — this generalizes it to every data type.

### Reconciling with the GEICO golden
The fixed 25-sheet contract came from the golden v4 workbook. Moving to the
universal schema means the golden no longer maps 1:1. Plan:
- **Universal schema is the new truth.**
- Keep a thin `geico_view.py` reshaper that projects the universal tables back into
  the old 25-sheet layout **for regression only**, so we keep proving GEICO numbers
  are still byte-exact (23,402 MBI / 21,067 VLR / territory values / 150 class
  factors). Output workbook = universal; golden test = via the reshaper.

> One decision for you at approval: keep this dual (universal output + GEICO-view
> for tests) — recommended — or drop the GEICO view entirely and validate numbers
> directly inside the universal tables.

---

## Part B — Carrier-agnostic pipeline (discovery, not hardcoding)

Replace `profile.py`'s hardcoded page ranges and the fixed vehicle anchors with
discovery. Wire in the two already-built-but-idle modules (`classifier`, `mapper`).

1. **Parse** (unchanged) — batched docling + pdfplumber, cached per PDF.
2. **Page classification** — *wire `classifier.py`*. Label every page: identity /
   rules-index / rule-page / factor-exhibit / vehicle-symbol-table / territory-map
   / installment-prose / other. Uses gpt-4o-mini on page text + cheap heuristics
   (`Rule:` regex, `Exhibit … Table`, header signatures). Output = a **discovered
   profile** (section→page-ranges) that REPLACES hardcoded `profile.py`.
   `profile.py` survives only as an optional override/hint.
3. **Identity** — upgrade `metadata.detect` to read the p1 metadata **grid** and
   emit **multiple** companies; NAIC stays deterministic (regex, never LLM),
   flagged if absent.
4. **Header-driven table extractor** (new; generalizes `vehicle_tables.py`) — for
   any whitespace-grid table, derive column x-anchors **from the header row's word
   positions**, then deterministically copy each cell to its column. Works for
   GEICO MBI/VLR *and* Progressive exhibits. Fixed `MBI_ANCHORS/VLR_ANCHORS` become
   a fallback, not the rule.
5. **Column mapping** — *wire `mapper.py`*. Map each discovered table's header
   labels → universal columns (Factor Type / Coverage / Dimensions / Value). LLM
   does label→meaning (semantic) only; deterministic code copies the values.
   Low-confidence → quarantine.
6. **Prose→params** (existing) — rules + installments → `03_Rule_Parameters`.
7. **Validation gates** (existing + extended) — domain checks, ZIP 5-digit,
   installment fractions sum to 1.0, factor-range sanity.
8. **Assemble universal sheets → write workbook + quarantine CSV.**

### Guardrails preserved (non-negotiable, per the original spec)
- LLM does **only** semantic work: classify pages, map labels, parse prose→params.
  It **never transcribes a number.**
- Deterministic Python copies every numeric/tabular value verbatim.
- Failed / low-confidence rows → quarantine. Never silently dropped, never guessed.
- Deterministic & repeatable: temperature 0, cached parse → same input, same output.

---

## Part C — Build order (incremental, each step verifiable)

1. New `schema.py` (universal sheets) + `geico_view.py` reshaper; update tests.
2. Header-driven extractor; **prove** it reproduces GEICO MBI/VLR byte-exact
   (existing regression still green) AND extracts a Progressive exhibit.
3. Wire `classifier` → discovered profile; prove it finds GEICO sections matching
   the old hardcoded ranges, and finds Progressive's rule/exhibit/symbol regions.
4. Wire `mapper` → populate `04_Rating_Factors` for both carriers.
5. Upgrade `metadata` (grid + multi-company).
6. End-to-end on both PDFs: GEICO golden regression via reshaper passes;
   Progressive sections populated with honest quarantine for anything ambiguous.

### Notes / risks
- Progressive is ~2,800pp (4×700). First pass validates on `split_part_1.pdf`
  before parsing all four; parse cost/time is the main practical risk → batched +
  cached, possibly part-by-part.
- Schema stays *stable* after this change; it's designed to absorb future carriers
  without new columns (new factor types are just new `Factor Type` values).
