"""Bill-Pay extraction.

Two steps:
1. find_billpay_pages -- a fast, deterministic keyword scan locates the pages
   that talk about billing / payment plans / installment & billing fees. This is
   carrier-agnostic (no hardcoded page numbers).
2. extract_billpay -- the LLM reads those pages and returns structured Bill-Pay
   rows (one per payment plan or flat billing fee), copying numbers as written
   and rating its own confidence (Accuracy Score). The LLM does the semantic
   understanding here; the Accuracy Score is the safeguard.

Output rows match the Bill_Pay schema in schema.py.
"""

import re

from . import doc_model
from .clients import structured
from .config import Config

# Strong signals (one is enough) vs weak signals (need a couple).
_STRONG = [r"bill\s*plan", r"payment\s*plan", r"installment", r"down\s*payment",
           r"\bpay\s*plan", r"premium\s+financ", r"paid\s+in\s+full"]
_WEAK = [r"\bNSF\b", r"late\s+fee", r"renewal\s+payment", r"\bEFT\b",
         r"automatic\s+card", r"billing", r"\bfee\b", r"down\s*pay"]
_STRONG_RE = [re.compile(p, re.IGNORECASE) for p in _STRONG]
_WEAK_RE = [re.compile(p, re.IGNORECASE) for p in _WEAK]


def find_billpay_pages(doc: dict, log=print) -> list[int]:
    """Pages whose text looks bill-pay related."""
    pages: list[int] = []
    for p in doc_model.page_numbers(doc):
        text = " ".join(t.text for t in doc_model.texts_on_pages(doc, p, p))
        if not text:
            continue
        strong = sum(1 for rx in _STRONG_RE if rx.search(text))
        weak = sum(1 for rx in _WEAK_RE if rx.search(text))
        if strong >= 1 or weak >= 2:
            pages.append(p)
    log(f"[billpay] {len(pages)} candidate pages: {pages[:20]}"
        f"{'...' if len(pages) > 20 else ''}")
    return pages


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "Company": {"type": "string"},
                    "Fee Type": {"type": "string"},          # Installment/NSF/Late/Renewal/Other
                    "Payment Plan": {"type": "string"},      # e.g. "5-pay"; blank for flat fees
                    "Fee": {"type": "string"},               # fee amount as written, e.g. "$6"
                    "Eligibility Rule": {"type": "string"},
                    "Downpayment Amount": {"type": "string"},
                    "Downpayment Unit": {"type": "string"},  # %, $, ...
                    "Each Installment Value": {"type": "string"},
                    "Each Installment Unit": {"type": "string"},
                    "Effective Date": {"type": "string"},    # when the rule/section takes effect
                    "End Date": {"type": "string"},          # sunset/expiration; usually blank
                    "Source Page": {"type": "integer"},      # page this row's info appears on
                    "Accuracy Score": {"type": "number"},    # 0..1 confidence
                },
                "required": ["Company", "Fee Type", "Payment Plan", "Fee",
                             "Eligibility Rule", "Downpayment Amount",
                             "Downpayment Unit", "Each Installment Value",
                             "Each Installment Unit", "Effective Date", "End Date",
                             "Source Page", "Accuracy Score"],
            },
        }
    },
    "required": ["rows"],
}

_SYSTEM = (
    "You extract BILL-PAY information from a U.S. auto-insurance rate filing. "
    "Return ONLY billing / payment-plan / billing-fee data; ignore everything "
    "else (rating factors, coverages, vehicle symbols, territory, etc.).\n"
    "Emit one row per payment plan (its downpayment and each-installment "
    "value/unit) AND one row per flat billing fee (Fee Type = NSF / Late / "
    "Renewal). For a payment-plan row use Fee Type = 'Installment'.\n"
    "Copy numbers EXACTLY as written (do not compute or round). Put the numeric "
    "amount in *Amount/Value and the unit ('%','$','months', etc.) in *Unit. "
    "If installments within a plan are not uniform, give the representative "
    "(first) installment and LOWER the Accuracy Score.\n"
    "Effective Date: the date the rule/section takes effect if shown on the page "
    "(e.g. a header 'Effective 03/01/2014' or 'Effective: September 21, 2018'); "
    "copy it as written. End Date: the rule's sunset/expiration date ONLY if "
    "explicitly stated (rate filings rarely print one — leave it blank otherwise; "
    "do NOT use a policy expiration date).\n"
    "Source Page: the exact page number this row's information appears on, taken "
    "from the '--- PAGE N ---' marker above that text. If the row's details span "
    "two pages, use the page where the plan/fee is defined. It MUST be one of the "
    "page numbers shown.\n"
    "Leave a field blank if it is not stated. Accuracy Score is your confidence "
    "in the row, 0..1. If a page has no bill-pay content, return no rows for it."
)


def _resolve_page(value, batch: list[int]) -> int:
    """The LLM's cited page if it's one of the batch's pages, else the batch's
    first page (defensive against a missing/hallucinated page number)."""
    try:
        pg = int(value)
    except (TypeError, ValueError):
        return batch[0]
    return pg if pg in batch else batch[0]


def extract_billpay(doc: dict, pages: list[int], company_hint: str,
                    cfg: Config, log=print, pages_per_call: int = 4) -> list[dict]:
    """Run the LLM over candidate pages (small batches) and return Bill-Pay rows
    tagged with their Source Page range."""
    rows: list[dict] = []
    for i in range(0, len(pages), pages_per_call):
        batch = pages[i:i + pages_per_call]
        text = "\n\n".join(
            f"--- PAGE {p} ---\n" + "\n".join(
                t.text for t in doc_model.texts_on_pages(doc, p, p))
            for p in batch)
        if not text.strip():
            continue
        src = f"{batch[0]}-{batch[-1]}" if len(batch) > 1 else str(batch[0])
        user = (f"Company (if a page does not name one, use this): {company_hint!r}\n\n"
                f"Extract bill-pay rows from these pages:\n\n{text}")
        try:
            res = structured(cfg.model_extract, _SYSTEM, user, _SCHEMA, "bill_pay")
        except Exception as e:  # noqa: BLE001
            log(f"[billpay] extraction failed on pages {src}: {e}")
            continue
        for r in res.get("rows", []):
            if not r.get("Company"):
                r["Company"] = company_hint
            try:
                acc = float(r.get("Accuracy Score", 0.0))
            except (TypeError, ValueError):
                acc = 0.0
            r["Accuracy Score"] = round(max(0.0, min(1.0, acc)), 2)
            # per-row page the LLM cited, validated to be one of the batch pages
            r["Source Page"] = str(_resolve_page(r.get("Source Page"), batch))
            rows.append(r)
        log(f"[billpay] pages {src}: +{len(res.get('rows', []))} rows")
    return rows
