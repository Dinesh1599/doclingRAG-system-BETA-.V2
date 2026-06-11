"""Bill-pay page finder + schema contract (offline; no LLM/network)."""

from rate_filing import billpay
from rate_filing.schema import BILL_PAY


def _doc(pages_text: dict[int, str]) -> dict:
    return {"_normalized": True, "pages": sorted(pages_text),
            "texts": [{"page": p, "label": "text", "text": t}
                      for p, t in pages_text.items()],
            "tables": []}


def test_finds_billing_pages_and_skips_others():
    doc = _doc({
        1: "Rate manual cover page. State NJ.",
        2: "Rule B01 Bill Plans. Down payment and installment payments apply.",
        3: "Rule V12 Excess Vehicle Factor. Symbol relativities by model year.",
        4: "NSF fee and late fee charges; renewal payment billing.",
        5: "Territory definitions: ZIP to territory mapping.",
    })
    pages = billpay.find_billpay_pages(doc, log=lambda *a, **k: None)
    assert 2 in pages          # strong: bill plan / installment / down payment
    assert 4 in pages          # weak x>=2: NSF + late fee + billing + renewal
    assert 3 not in pages and 5 not in pages and 1 not in pages


def test_triage_keyword_decisions(monkeypatch):
    """Deterministic keyword triage: clear filing -> relevant; clear non-filing
    -> skip; both decided without an LLM call."""
    from pathlib import Path

    from rate_filing import triage
    from rate_filing.config import Config

    cfg = Config(openai_api_key="", openai_base_url="", model_extract="gpt-4o",
                 model_classify="gpt-4o-mini", docling_do_ocr=True,
                 docling_table_mode="fast", docling_ocr_engine="auto",
                 docling_num_threads=4, docling_ocr_min_chars=50,
                 docling_ocr_min_image_coverage=0.5, database_url="",
                 embed_model="text-embedding-3-small", chunk_max_chars=800,
                 input_dir=Path("."), output_dir=Path("."),
                 processed_dir=Path("."), skipped_dir=Path("."))

    filing = ("Rate / Rule Manual. Private Passenger Auto. Underwriting rules, "
              "premium, coverage, deductible, installment bill plan, SERFF, NAIC.")
    junk = "Offer Letter. We are pleased to offer you the position of Engineer."
    monkeypatch.setattr(triage, "_sample_text", lambda pdf, max_pages=6:
                        filing if "good" in str(pdf) else junk)

    assert triage.classify("good.pdf", cfg, log=lambda *a, **k: None).relevant is True
    assert triage.classify("junk.pdf", cfg, log=lambda *a, **k: None).relevant is False


def test_page_router_runs_grouping():
    """Scattered scanned pages collapse into contiguous (start, end) runs so
    docling is called once per run, not once per page."""
    from rate_filing.page_router import _runs
    assert _runs([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 16, 17, 18, 19, 24]) == [
        (2, 11), (13, 13), (16, 19), (24, 24)]
    assert _runs([]) == []
    assert _runs([5]) == [(5, 5)]


def test_bill_pay_schema_columns():
    cols = BILL_PAY.columns
    assert cols[0] == "Source File"
    for must in ("Company", "SERFF #", "RFC #", "Fee Type", "Payment Plan",
                 "Downpayment Amount", "Each Installment Value", "Effective Date",
                 "End Date", "Source Page", "Accuracy Score", "Chunks"):
        assert must in cols


def test_resolve_source_page():
    """Per-row page is the LLM's cited page when valid, else the batch's first
    page — so a row links to one page, not the whole batch span."""
    from rate_filing.billpay import _resolve_page
    batch = [8, 31, 61, 107]
    assert _resolve_page(31, batch) == 31        # valid cited page
    assert _resolve_page("61", batch) == 61      # string coerced
    assert _resolve_page(50, batch) == 8         # not in batch -> first page
    assert _resolve_page(None, batch) == 8       # missing -> first page


def test_chunking_per_page_and_index():
    """Chunks stay within one page, split long pages, and number sequentially."""
    from rate_filing import chunking
    doc = _doc({1: "short page one", 2: "x" * 50 + "\n" + "y" * 50})
    chunks = chunking.doc_to_chunks(doc, max_chars=60)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.page_start == c.page_end for c in chunks)   # never spans pages
    assert chunks[0].page_start == 1
    assert sum(1 for c in chunks if c.page_start == 2) >= 2  # long page split


def test_vectordb_parse_date():
    """Messy date text -> real date; junk/empty -> None."""
    import datetime
    from rate_filing.vectordb import parse_date
    assert parse_date("Effective 03/01/2014") == datetime.date(2014, 3, 1)
    assert parse_date("September 21, 2018") == datetime.date(2018, 9, 21)
    assert parse_date("") is None and parse_date(None) is None
    assert parse_date("n/a") is None


def test_vectordb_page_range_parsing():
    """Source Page '4-7' / '4' parse to inclusive ranges for chunk linking."""
    from rate_filing.vectordb import _page_range
    assert _page_range("4-7") == (4, 7)
    assert _page_range("31") == (31, 31)
    assert _page_range("") is None and _page_range(None) is None
