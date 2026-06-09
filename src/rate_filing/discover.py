"""Section discovery — find WHERE each kind of content lives, per carrier.

Replaces hardcoded page ranges (the old profile.py): different carriers put
sections on completely different pages, so we detect them from page content.

Deterministic first: a fast pdfplumber text+geometry scan classifies every page
using structural signals (rule headers, factor-table coverage codes, model-year
rows, ZIP grids). The LLM classifier is an optional refiner for pages the
heuristics leave as "other" -- it only labels, never reads data values.

Page kinds (map to universal sheets):
    identity        -> 01_Filing_Identity
    rule            -> 02_Rules / 03_Rule_Parameters
    factor_table    -> 04_Rating_Factors
    vehicle_symbols -> 06_Vehicle_Symbols
    territory_map   -> 05_Territory_Definitions
    index / other   -> ignored
"""

import re
from dataclasses import dataclass, field

import pdfplumber

# Coverage codes that mark a rating-factor exhibit (carrier-neutral set).
_COVERAGE_CODES = {"BI", "PD", "COMP", "COLL", "PIP", "UM/UIM", "UM", "UIM",
                   "UMPD", "RENT", "TOW", "LOAN", "MED", "ACPE", "RR",
                   "COMP-TRLR", "COLL-TRLR", "UM&UND", "UIMPD"}
_RULE_RE = re.compile(r"\bRule\s*(?:Title)?\s*[:\-]\s*", re.IGNORECASE)
_RULE_CODE_RE = re.compile(r"\bRule:\s*([A-Z]?\d{1,3}[A-Z]?)\b")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_ZIP_RE = re.compile(r"^\d{5}$")
_NUMISH = re.compile(r"^[-+]?\$?\d[\d,]*\.?\d*%?$")
_FACTOR_TITLE_RE = re.compile(r"(Factor Table|Relativit|Rate Table|Symbol "
                              r"Relativit|Increased Limit)", re.IGNORECASE)


@dataclass
class PageKind:
    page: int
    kind: str
    title: str = ""
    signals: dict = field(default_factory=dict)


@dataclass
class Discovered:
    pages: list[PageKind]

    def of_kind(self, kind: str) -> list[int]:
        return [p.page for p in self.pages if p.kind == kind]

    def ranges(self, kind: str) -> list[tuple[int, int]]:
        ps = sorted(self.of_kind(kind))
        out: list[tuple[int, int]] = []
        for p in ps:
            if out and p == out[-1][1] + 1:
                out[-1] = (out[-1][0], p)
            else:
                out.append((p, p))
        return out

    def title_for(self, page: int) -> str:
        for p in self.pages:
            if p.page == page:
                return p.title
        return ""

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.pages:
            out[p.kind] = out.get(p.kind, 0) + 1
        return out


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _first_title(lines: list[str]) -> str:
    """Best-effort section title: a line mentioning a Factor/Rate/Symbol table,
    else the first non-boilerplate line."""
    for ln in lines:
        if _FACTOR_TITLE_RE.search(ln):
            return ln[:80]
    return (lines[0][:80] if lines else "")


def _classify(text: str) -> tuple[str, str, dict]:
    lines = _lines(text)
    tokens = text.split()
    if not tokens:
        return "other", "", {}

    year_rows = sum(1 for ln in lines if _YEAR_RE.match(ln.split()[0]) if ln.split())
    zip_tokens = sum(1 for t in tokens if _ZIP_RE.match(t))
    cov_hits = sum(1 for t in tokens if t.upper() in _COVERAGE_CODES)
    num_tokens = sum(1 for t in tokens if _NUMISH.match(t))
    num_density = num_tokens / max(len(tokens), 1)
    has_rule = bool(_RULE_RE.search(text))
    rule_code = _RULE_CODE_RE.search(text)
    has_factor_title = bool(_FACTOR_TITLE_RE.search(text))
    upper = text.upper()

    sig = {"year_rows": year_rows, "zip_tokens": zip_tokens, "cov_hits": cov_hits,
           "num_density": round(num_density, 2)}

    # vehicle symbol tables: many rows whose first token is a model year
    if year_rows >= 8:
        # sub-type the symbol so the parser can pick column geometry / Symbol Type
        if "LIABILITY SYMBOL" in upper or "(VLR)" in upper or "VLR" in upper:
            symtype = "Liability"
        elif "MBI" in upper:
            symtype = "MBI"
        else:
            symtype = "Symbol"
        sig["symbol_type"] = symtype
        return "vehicle_symbols", symtype, sig
    # rate/factor exhibits: coverage-code header + numeric grid
    if (cov_hits >= 3 and num_density >= 0.3) or (has_factor_title and num_density >= 0.3):
        return "factor_table", _first_title(lines), sig
    # territory definitions: dense ZIP grid mapped to small territory numbers
    if zip_tokens >= 10 and num_density >= 0.3:
        return "territory_map", _first_title(lines), sig
    # rule manual page
    if has_rule or rule_code:
        title = rule_code.group(1) if rule_code else ""
        return "rule", title, sig
    # identity / cover
    if ("RATE / RULE MANUAL" in upper or "RATE/RULE MANUAL" in upper
            or ("NAIC" in upper and "COMPANY" in upper)):
        return "identity", _first_title(lines), sig
    return "other", "", sig


def discover(pdf_path, pages: list[int] | None = None, log=print) -> Discovered:
    """Scan the PDF deterministically and classify each page. `pages` limits the
    scan (1-based); default = all pages."""
    out: list[PageKind] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        n = len(pdf.pages)
        scan = pages or range(1, n + 1)
        for pno in scan:
            if not (1 <= pno <= n):
                continue
            text = pdf.pages[pno - 1].extract_text() or ""
            kind, title, sig = _classify(text)
            out.append(PageKind(pno, kind, title, sig))
    d = Discovered(out)
    log(f"[discover] {pdf_path} -> {d.summary()}")
    return d
