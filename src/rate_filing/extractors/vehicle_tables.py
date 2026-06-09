"""Coordinate-based parser for the two large vehicle tables (sheets 19 & 20).

Docling merges adjacent rows on these pages (see SPIKE.md), so we read the
digital text layer directly with pdfplumber and reconstruct rows from word
coordinates. Same code path for MBI (19) and VLR (20); only the last column's
name differs (MBI Code vs Liability Symbol).
"""

import re
from dataclasses import dataclass

import pdfplumber

# Per-table column anchors = the left x of each of the 7 columns' data, read
# off the filing's fixed page geometry and validated cell-for-cell against the
# golden workbook. A token at x0 is assigned to the rightmost anchor whose
# value <= x0 + ANCHOR_TOL; the small tolerance absorbs sub-pixel x jitter and
# minor header/data left-alignment differences. MBI (sheet 19) and VLR (sheet
# 20) share the parser but have different anchors (VLR's columns sit further
# right because its last column is the wider "Liability Symbol").
_COLS = ["Model Year", "Make", "Model", "Body Style", "Engine Type",
         "Four Wheel Drive", "code"]
MBI_ANCHORS = [52.0, 85.0, 187.0, 334.0, 372.0, 450.0, 490.0]
VLR_ANCHORS = [52.0, 88.0, 193.0, 367.0, 413.0, 496.0, 528.0]
_ANCHOR_TOL = 3.0

_ROW_GAP = 3.5      # vertical gap (pt) separating two data rows
_SPACE_GAP = 1.0    # horizontal gap (pt) above which two tokens get a space
_YEAR_RE = re.compile(r"^\d{4}$")


def _col_index(x0: float, anchors: list[float]) -> int:
    idx = 0
    for i, a in enumerate(anchors):
        if x0 + _ANCHOR_TOL >= a:
            idx = i
    return idx


def _join_tokens(tokens: list[dict]) -> str:
    """Join column tokens left-to-right, inserting a space only at real word
    gaps (letter-spaced glyphs within a word nearly touch)."""
    tokens = sorted(tokens, key=lambda w: w["x0"])
    out = ""
    prev_x1 = None
    for w in tokens:
        if prev_x1 is not None:
            out += " " if (w["x0"] - prev_x1) > _SPACE_GAP else ""
        out += w["text"]
        prev_x1 = w["x1"]
    return out.strip()


@dataclass
class VehicleRow:
    model_year: str
    make: str
    model: str
    body_style: str
    engine_type: str
    four_wheel_drive: str
    code: str  # MBI Code or Liability Symbol


def parse_page(page: pdfplumber.page.Page, anchors: list[float]) -> list[VehicleRow]:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    if not words:
        return []
    words.sort(key=lambda w: (w["top"], w["x0"]))

    rows: list[list[dict]] = []
    cur: list[dict] = []
    cur_top: float | None = None
    for w in words:
        if cur_top is None or abs(w["top"] - cur_top) <= _ROW_GAP:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur = [w]
            cur_top = w["top"]
    if cur:
        rows.append(cur)

    out: list[VehicleRow] = []
    for row in rows:
        cols: list[list[dict]] = [[] for _ in range(7)]
        for w in row:
            cols[_col_index(w["x0"], anchors)].append(w)
        vals = [_join_tokens(c) for c in cols]
        # A data row's Model Year cell is a 4-digit year; this drops the
        # column header, the page title, and footnote rows.
        if not _YEAR_RE.match(vals[0]):
            continue
        out.append(VehicleRow(*vals))
    return out


def extract(pdf_path, start_page: int, end_page: int,
            anchors: list[float]) -> list[tuple[VehicleRow, int]]:
    """Return (row, 1-based source page) over the inclusive page range."""
    results: list[tuple[VehicleRow, int]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno in range(start_page, end_page + 1):
            page = pdf.pages[pno - 1]
            for vr in parse_page(page, anchors):
                results.append((vr, pno))
    return results
