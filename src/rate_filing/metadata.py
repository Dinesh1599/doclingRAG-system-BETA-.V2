"""Detect the filing's identity: companies, State, Line + deterministic codes.

A filing may cover several companies (e.g. Progressive Garden State + Drive New
Jersey), so the LLM returns a LIST of companies plus State/Line. All numeric
codes (NAIC, SERFF, RFC) are read deterministically by regex -- never by the
LLM. The cover is often a multi-column grid, so we feed the LLM the first pages'
raw text and let it pull the legal names.
"""

import re

from . import doc_model
from .clients import structured
from .config import Config

# LLM returns NAMES only; never numeric codes.
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "Company": {"type": "string"},
                    "Role": {"type": "string"},  # e.g. group code "AG"/"DI" or "primary"
                },
                "required": ["Company", "Role"],
            },
        },
        "State": {"type": "string"},
        "Line": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["companies", "State", "Line", "confidence"],
}

_SYSTEM = (
    "You identify a U.S. auto-insurance rate filing from its cover/first pages, "
    "which are often laid out as a grid. Return every insurance COMPANY named on "
    "the cover (full legal name) with its short role/group code if shown in "
    "parentheses (e.g. 'AG', 'DI'); use 'primary' if none. Return State "
    "(2-letter) and Line (e.g. 'Private Passenger Auto'). Do NOT return any "
    "numeric codes. If a field is absent, use an empty string and lower confidence."
)

_NAIC_RE = re.compile(r"NAIC[^0-9]{0,12}(\d{4,5})", re.IGNORECASE)
_SERFF_RE = re.compile(r"\b([A-Z]{3,5}-\d{8,})\b")
# Require a digit in the code so a stray header word ("LAST", "UPDATE") on the
# cover's grid layout is not mistaken for an RFC number.
_RFC_RE = re.compile(r"\bRFC\s*#?\s*:?\s*([A-Z]{0,4}\d[A-Z0-9\-]{2,})", re.IGNORECASE)


def _doc_text(doc: dict, max_pages: int = 6) -> str:
    pages = doc_model.page_numbers(doc)[:max_pages]
    return "\n".join(t.text for p in pages
                     for t in doc_model.texts_on_pages(doc, p, p))


def naic_from_doc(doc: dict) -> str | None:
    """Deterministically read the NAIC code from document text (never LLM)."""
    text = "\n".join(t.text for t in doc_model.texts(doc))
    m = _NAIC_RE.search(text)
    return m.group(1) if m else None


def _codes_from_text(text: str) -> dict:
    serff = _SERFF_RE.search(text)
    rfc = _RFC_RE.search(text)
    return {"SERFF #": serff.group(1) if serff else "",
            "RFC #": rfc.group(1) if rfc else ""}


def detect(doc: dict, cfg: Config, max_pages: int = 6) -> dict:
    """Return {'companies': [...], 'Company': primary, 'State','Line','NAIC',
    'SERFF #','RFC #','confidence'}. NAIC/SERFF/RFC are deterministic."""
    text = _doc_text(doc, max_pages)
    res = structured(cfg.model_classify, _SYSTEM,
                     f"First pages text:\n{text[:4000]}", _SCHEMA, "filing_identity")
    companies = [c for c in res.get("companies", []) if c.get("Company")]
    res["companies"] = companies
    res["Company"] = companies[0]["Company"] if companies else ""
    res["NAIC"] = naic_from_doc(doc)               # deterministic, may be None
    res.update(_codes_from_text(text))             # deterministic SERFF/RFC
    return res
