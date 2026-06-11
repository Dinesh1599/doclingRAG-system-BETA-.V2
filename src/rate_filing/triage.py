"""Relevance triage — decide which PDFs are auto-insurance rate/rule filings
worth running through the bill-pay pipeline, and which are irrelevant.

Runs BEFORE any docling/extraction work (the expensive part), so dropping 10
random files into input/ only spends real effort on the genuine filings.

Layered, cheap:
1. Sample the first pages' text (docling cache if present, else pdfplumber; no
   new docling parse).
2. Deterministic domain score — count insurance/rate-filing signals.
   - many signals  -> relevant   (keyword)
   - no signals    -> irrelevant (keyword)
3. For the borderline middle, one cheap gpt-4o-mini call decides (llm).
A scanned PDF with no extractable text is processed by default (flagged), so a
real scanned filing is never silently skipped.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import doc_model
from .clients import structured
from .config import Config

# Insurance / rate-filing domain signals (word-boundary, case-insensitive).
_DOMAIN_TERMS = [
    r"rate\s*/?\s*rule\s+manual", r"rate\s+filing", r"rule\s+manual",
    r"private\s+passenger", r"personal\s+auto", r"private\s+passenger\s+auto",
    r"\bunderwriting\b", r"\bSERFF\b", r"\bNAIC\b", r"rating\s+territor",
    r"\bpremium\b", r"\bdeductible\b", r"\bendorsement\b", r"policy\s+form",
    r"\bcoverage\b", r"bodily\s+injury", r"comprehensive", r"\binsurance\s+company\b",
    r"installment", r"bill\s*plan", r"payment\s+plan",
]
_DOMAIN_RE = [re.compile(t, re.IGNORECASE) for t in _DOMAIN_TERMS]

_STRONG_HITS = 3   # >= this many distinct domain terms -> relevant outright
# (1..STRONG_HITS-1 distinct hits -> borderline -> LLM tie-breaker)


@dataclass
class Triage:
    pdf: str
    relevant: bool
    confidence: float
    reason: str
    method: str       # 'keyword' | 'llm' | 'no-text-default'


def _sample_text(pdf: Path, max_pages: int = 6) -> str:
    """Cheap first-pages text via pdfplumber (no OCR — triage stays fast). A
    fully-scanned doc yields little here and falls to the no-text default
    (processed inclusively); a mixed doc usually has enough digital text on the
    cover pages to score."""
    doc = doc_model.from_pdf_text(pdf, pages=list(range(1, max_pages + 1)))
    return "\n".join(t.text for t in doc_model.texts(doc))


_LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevant": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["relevant", "confidence", "reason"],
}

_LLM_SYSTEM = (
    "You decide whether a document is a U.S. personal-lines INSURANCE RATE/RULE "
    "FILING (the kind that contains rating rules, premiums, coverages, and "
    "billing/payment-plan rules). Answer relevant=true only for such filings; "
    "relevant=false for unrelated documents (offer letters, invoices, resumes, "
    "generic reports, etc.). Give a short reason and a confidence in [0,1]."
)


def classify(pdf, cfg: Config, log=print) -> Triage:
    pdf = Path(pdf)
    text = _sample_text(pdf)
    if not text.strip():
        return Triage(pdf.name, True, 0.3,
                      "no extractable text (scanned); processed without triage "
                      "confidence", "no-text-default")

    hits = sorted({rx.pattern for rx in _DOMAIN_RE if rx.search(text)})
    n = len(hits)
    if n >= _STRONG_HITS:
        return Triage(pdf.name, True, min(0.6 + 0.1 * n, 0.99),
                      f"{n} domain signals", "keyword")
    if n == 0:
        return Triage(pdf.name, False, 0.9,
                      "no insurance / rate-filing terms found", "keyword")

    # borderline (1..STRONG_HITS-1): cheap LLM tie-breaker if available
    if cfg.openai_api_key:
        try:
            res = structured(cfg.model_classify, _LLM_SYSTEM,
                             f"First pages text:\n{text[:4000]}", _LLM_SCHEMA,
                             "relevance")
            return Triage(pdf.name, bool(res.get("relevant")),
                          float(res.get("confidence", 0.5)),
                          res.get("reason", "") or f"{n} domain signals", "llm")
        except Exception as e:  # noqa: BLE001
            log(f"[triage] {pdf.name}: LLM tie-breaker failed ({e}); using keyword score")
    # no key (or LLM failed): lean inclusive on a weak positive
    return Triage(pdf.name, True, 0.5, f"{n} domain signal(s) (weak)", "keyword")
