"""Builders that turn discovered/extracted content into universal-schema rows.

All numeric/tabular values are copied verbatim by deterministic code (coordinate
parsers + grid melt). The LLM only supplies semantic labels (identity names,
prose->parameters). Anything ambiguous is flagged to quarantine, never guessed.
"""

import re

from . import doc_model, factors, prose_parser
from .config import Config
from .discover import Discovered
from .extractors import grid
from .extractors import vehicle_tables as vt
from .quarantine import Quarantine

_ZIP_RE = re.compile(r"^\d{5}$")
_INT_RE = re.compile(r"^\d+$")
_RULE_HEAD_RE = re.compile(r"Rule:\s*([A-Z]?\d{1,3}[A-Z]?)\s*(?:Rule Title:\s*)?(.*)",
                           re.IGNORECASE)

# GEICO class-factor coverage banner (only present in GEICO-style filings).
_COVERAGE_FROM_BANNER = [
    ("PROPERTY DAMAGE", "Property Damage"),
    ("BODILY INJURY", "Bodily Injury"),
    ("PERSONAL INJURY PROTECTION", "Basic PIP"),
    ("COMPREHENSIVE", "Comprehensive"),
    ("COLLISION", "Collision"),
]
_MARITAL_GENDER = ["Single Male", "Single Female", "Married Male", "Married Female"]
_NUMISH = re.compile(r"^-?\d[\d,]*\.?\d*$")


# --- 01 Filing Identity ----------------------------------------------------

def filing_identity_rows(det: dict, md: dict) -> list[dict]:
    companies = det.get("companies") or [{"Company": md.get("Company", ""),
                                          "Role": "primary"}]
    rows = []
    for c in companies:
        rows.append({"Company": c.get("Company", ""), "NAIC": md.get("NAIC") or "",
                     "State": md.get("State", ""), "Line": md.get("Line", ""),
                     "Role": c.get("Role", ""), "RFC #": det.get("RFC #", ""),
                     "SERFF #": det.get("SERFF #", ""),
                     "Effective Date": det.get("Effective Date", ""),
                     "Source Page": "1"})
    return rows


# --- 02 Rules (raw text) ---------------------------------------------------

def build_rules(doc: dict, discovered: Discovered, md: dict) -> list[dict]:
    rows = []
    for pno in discovered.of_kind("rule"):
        text = "\n".join(t.text for t in doc_model.texts_on_pages(doc, pno, pno))
        if not text.strip():
            continue
        m = _RULE_HEAD_RE.search(text)
        code = m.group(1) if m else (discovered.title_for(pno) or "")
        # Rule Title: take the text right after "Rule Title:" if present.
        title = ""
        mt = re.search(r"Rule Title:\s*(.+)", text)
        if mt:
            title = mt.group(1).splitlines()[0].strip()[:120]
        rows.append({**md, "Rule Code": code, "Rule Title": title,
                     "Category": "", "Text": text.strip(), "Source Page": str(pno)})
    return rows


# --- 03 Rule Parameters (LLM prose -> params, bounded) ---------------------

def build_rule_parameters(doc: dict, discovered: Discovered, md: dict,
                          cfg: Config, q: Quarantine, log=print,
                          max_calls: int = 12, pages_per_call: int = 10) -> list[dict]:
    rule_pages = discovered.of_kind("rule")
    if not rule_pages:
        return []
    rows: list[dict] = []
    calls = 0
    for i in range(0, len(rule_pages), pages_per_call):
        if calls >= max_calls:
            q.add("03_Rule_Parameters", f"prose-parse cap reached: rule pages "
                  f"beyond {rule_pages[i-1]} not parsed (raw text is in 02_Rules)",
                  detail=f"{len(rule_pages) - i} pages skipped")
            break
        batch = rule_pages[i:i + pages_per_call]
        text = "\n\n".join("\n".join(t.text for t in
                           doc_model.texts_on_pages(doc, p, p)) for p in batch)
        if not text.strip():
            continue
        try:
            parsed = prose_parser.parse_general_rules(
                text, f"p.{batch[0]}-{batch[-1]}", cfg)
            for r in parsed:
                rows.append({**md, "Rule Code": r.get("Rule", ""),
                             "Rule Title": r.get("Title", ""),
                             "Parameter": r.get("Parameter", ""),
                             "Value": r.get("Value", ""), "Unit": r.get("Unit", ""),
                             "Applies To / Condition": r.get("Applies To / Condition", ""),
                             "Source Page": r.get("Source", "")})
            calls += 1
        except Exception as e:  # noqa: BLE001
            q.add("03_Rule_Parameters", f"prose parse failed on p.{batch[0]}-{batch[-1]}: {e}")
    log(f"[rules] params parsed in {calls} LLM call(s) -> {len(rows)} rows")
    return rows


# --- 04 Rating Factors -----------------------------------------------------

def _coverage_for_page(doc: dict, page: int) -> str | None:
    txt = " ".join(t.text for t in doc_model.texts_on_pages(doc, page, page)).upper()
    if "CLASS FACTOR" not in txt or "APPLICABLE FOR" not in txt:
        return None
    banner = txt[txt.index("APPLICABLE FOR"):txt.index("APPLICABLE FOR") + 80]
    for needle, cov in _COVERAGE_FROM_BANNER:
        if needle in banner:
            return cov
    return None


def _geico_class_factors(doc: dict, md: dict) -> tuple[list[dict], set[int]]:
    """GEICO-style class factors via docling tables + coverage banner. Emits
    LONG universal rows (one per marital/gender). Returns (rows, pages_used)."""
    rows: list[dict] = []
    used: set[int] = set()
    for page in doc_model.page_numbers(doc):
        cov = _coverage_for_page(doc, page)
        if not cov:
            continue
        for t in doc_model.tables_on_pages(doc, page, page):
            if t.num_cols < 5:
                continue
            for src in t.rows:
                cells = [c.strip() for c in src]
                age = cells[0]
                facs = cells[1:5]
                if not age or not all(_NUMISH.match(c.replace(",", "")) for c in facs if c):
                    continue
                if not any(facs):
                    continue
                used.add(page)
                for label, val in zip(_MARITAL_GENDER, facs):
                    if not val:
                        continue
                    rows.append({**md, "Factor Type": "Class Factor", "Coverage": cov,
                                 "Dim1 Name": "Age of Driver", "Dim1 Value": age,
                                 "Dim2 Name": "Marital/Gender", "Dim2 Value": label,
                                 "Dim3 Name": "", "Dim3 Value": "",
                                 "Dimensions (all)": f"Age of Driver={age} | Marital/Gender={label}",
                                 "Factor Value": val, "Source Page": str(page)})
    return rows, used


def build_rating_factors(pdf, doc: dict, discovered: Discovered, md: dict,
                         q: Quarantine, log=print) -> list[dict]:
    rows: list[dict] = []
    # 1) GEICO-style class factors (docling + banner), if present.
    cf_rows, cf_pages = _geico_class_factors(doc, md)
    rows.extend(cf_rows)
    if cf_rows:
        log(f"[factors] class factors (banner): {len(cf_rows)} long rows "
            f"from {len(cf_pages)} pages")

    # 2) Generic factor exhibits: header-driven grid melt over remaining pages.
    factor_pages = [p for p in discovered.of_kind("factor_table") if p not in cf_pages]
    melted = 0
    skipped = 0
    for gt in grid.extract(pdf, factor_pages):
        ftype = discovered.title_for(gt.page) or "Rating Factor"
        new = factors.melt_grid(gt, ftype, md)
        if new:
            rows.extend(new)
            melted += 1
        else:
            skipped += 1
            q.add("04_Rating_Factors", "factor table found but no coverage/value "
                  "columns recognized", gt.page, detail=f"labels={gt.labels[:6]}")
    log(f"[factors] grid melt: {melted} tables -> "
        f"{len(rows) - len(cf_rows)} rows ({skipped} unrecognized)")
    return rows


# --- 05 Territory Definitions ----------------------------------------------

def build_territory(doc: dict, discovered: Discovered, md: dict,
                    q: Quarantine, log=print) -> list[dict]:
    """ZIP -> Rating Territory from docling tables on territory_map pages
    (GEICO grid of paired cells). Carriers without a discrete map yield none."""
    rows: list[dict] = []
    pages = discovered.of_kind("territory_map")
    if not pages:
        return rows
    for t in doc_model.tables(doc):
        if t.page not in pages:
            continue
        for r in t.rows:
            cells = [c.strip() for c in r]
            for i in range(0, len(cells) - 1, 2):
                zs = cells[i].split()
                ts = cells[i + 1].split()
                zips = [z for z in zs if _ZIP_RE.match(z)]
                if not zips:
                    continue
                if len(zips) == len(ts):
                    for z, terr in zip(zips, ts):
                        rows.append({**md, "ZIP Code": z,
                                     "Rating Territory": int(terr) if _INT_RE.match(terr) else terr,
                                     "Source Page": str(t.page)})
                else:
                    q.add("05_Territory_Definitions",
                          f"ambiguous ZIP/territory cell ({len(zips)} ZIPs vs "
                          f"{len(ts)} territories)", t.page,
                          detail=f"{cells[i]!r} / {cells[i+1]!r}")
    log(f"[territory] {len(rows)} ZIP rows")
    return rows


# --- 06 Vehicle Symbols ----------------------------------------------------

_ANCHORS_BY_TYPE = {"MBI": vt.MBI_ANCHORS, "Liability": vt.VLR_ANCHORS}


def _symbol_runs(discovered: Discovered) -> list[tuple[str, int, int]]:
    """Contiguous (symbol_type, start, end) runs over vehicle_symbols pages."""
    runs: list[tuple[str, int, int]] = []
    for pk in sorted([p for p in discovered.pages if p.kind == "vehicle_symbols"],
                     key=lambda p: p.page):
        st = pk.signals.get("symbol_type", "Symbol")
        if runs and runs[-1][0] == st and pk.page == runs[-1][2] + 1:
            runs[-1] = (st, runs[-1][1], pk.page)
        else:
            runs.append((st, pk.page, pk.page))
    return runs


def build_vehicle_symbols(pdf, discovered: Discovered, md: dict,
                          q: Quarantine, log=print) -> list[dict]:
    rows: list[dict] = []
    for st, start, end in _symbol_runs(discovered):
        anchors = _ANCHORS_BY_TYPE.get(st)
        if not anchors:
            q.add("06_Vehicle_Symbols", f"vehicle-symbol pages {start}-{end} of "
                  f"type {st!r} have no known column geometry — needs header-driven "
                  "geometry for this carrier", start)
            continue
        for v, page in vt.extract(pdf, start, end, anchors):
            rows.append({**md, "Symbol Type": st, "Model Year": v.model_year,
                         "Make": v.make, "Model": v.model,
                         "Body Style": v.body_style or None,
                         "Engine Type": v.engine_type or None,
                         "Four Wheel Drive": v.four_wheel_drive or None,
                         "Symbol": v.code, "Source Page": str(page)})
    log(f"[vehicles] {len(rows)} symbol rows")
    return rows


# --- 09 Source Map / 10 Quarantine ----------------------------------------

def source_map(discovered: Discovered) -> list[dict]:
    kind_to_sheet = {
        "identity": "01_Filing_Identity", "rule": "02_Rules / 03_Rule_Parameters",
        "factor_table": "04_Rating_Factors", "territory_map": "05_Territory_Definitions",
        "vehicle_symbols": "06_Vehicle_Symbols",
    }
    out = []
    for kind, sheet in kind_to_sheet.items():
        rngs = discovered.ranges(kind)
        if not rngs:
            continue
        pages = ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in rngs)
        out.append({"Sheet": sheet, "Detected By": "discovery (content scan)",
                    "Source Pages": pages, "Columns": ""})
    return out


def quarantine_rows(q: Quarantine) -> list[dict]:
    return [{"Sheet": it.sheet, "Reason": it.reason,
             "Source Page": it.source_page, "Detail": it.detail} for it in q.items]
