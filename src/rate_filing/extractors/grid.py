"""Header-driven coordinate table reader (carrier-agnostic).

Many rate-filing tables are whitespace-aligned text grids with no ruling lines,
so pdfplumber's line-based `extract_tables()` returns nothing (verified on both
GEICO and Progressive). This reader finds the table's columns from the vertical
alignment of its DATA tokens (numbers in a factor table line up in vertical
bands), then deterministically copies each cell into its column. Column labels
are reconstructed by dropping the header line(s) onto those same bands. Numbers
are copied verbatim; the LLM is never involved here.

GEICO's vehicle data-pages have no per-page header and keep the dedicated
`vehicle_tables` parser; this reader targets per-page factor/rate exhibits
(e.g. Progressive's "... Factor Table" pages).
"""

import re
from dataclasses import dataclass

import pdfplumber

_ROW_GAP = 3.5       # vertical gap (pt) separating two rows
_SPACE_GAP = 1.0     # horizontal gap (pt) above which two tokens get a space
_COL_GAP = 6.0       # horizontal gap (pt) between distinct column bands
_NUMISH = re.compile(r"^[-+]?\$?\d[\d,]*\.?\d*%?$")


def _cluster_rows(words: list[dict], row_gap: float = _ROW_GAP) -> list[list[dict]]:
    words = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    rows: list[list[dict]] = []
    cur: list[dict] = []
    cur_top: float | None = None
    for w in words:
        if cur_top is None or abs(w["top"] - cur_top) <= row_gap:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            rows.append(cur)
            cur = [w]
            cur_top = w["top"]
    if cur:
        rows.append(cur)
    return rows


def _join(tokens: list[dict], space_gap: float = _SPACE_GAP) -> str:
    tokens = sorted(tokens, key=lambda w: w["x0"])
    out = ""
    prev_x1 = None
    for w in tokens:
        if prev_x1 is not None:
            out += " " if (w["x0"] - prev_x1) > space_gap else ""
        out += w["text"]
        prev_x1 = w["x1"]
    return out.strip()


def _numfrac(row: list[dict]) -> float:
    if not row:
        return 0.0
    return sum(1 for w in row if _NUMISH.match(w["text"])) / len(row)


def _column_bands(rows: list[list[dict]], col_gap: float = _COL_GAP) -> list[float]:
    """Cluster all data-token left-edges into vertical column bands; return each
    band's representative x (its left edge)."""
    xs = sorted(w["x0"] for row in rows for w in row)
    if not xs:
        return []
    bands: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - bands[-1][-1] > col_gap:
            bands.append([x])
        else:
            bands[-1].append(x)
    # anchor = left edge of the band (min); midpoint kept for assignment
    return [min(b) for b in bands]


def _assign(token_x0: float, anchors: list[float]) -> int:
    """Assign a token to the nearest column anchor (by left edge)."""
    best, bd = 0, abs(token_x0 - anchors[0])
    for i, a in enumerate(anchors):
        d = abs(token_x0 - a)
        if d < bd:
            best, bd = i, d
    return best


@dataclass
class GridTable:
    page: int
    labels: list[str]
    anchors: list[float]
    rows: list[list[str]]            # data rows, each aligned to `anchors`

    def dicts(self) -> list[dict]:
        return [dict(zip(self.labels, r)) for r in self.rows]


def read_page(page: "pdfplumber.page.Page", header_keywords: list[str] | None = None,
              min_keyword_hits: int = 0, min_data_rows: int = 3,
              x_tolerance: float = 1.5) -> GridTable | None:
    """Extract a whitespace grid from `page`. Data rows (numeric-dense) define
    the columns; the rows above the first data row are treated as the header and
    dropped onto the same columns to form labels. If `header_keywords` is given,
    the header text must contain at least `min_keyword_hits` of them."""
    words = page.extract_words(x_tolerance=x_tolerance, y_tolerance=2)
    if not words:
        return None
    rows = _cluster_rows(words)

    # data rows = numeric-dense rows with enough tokens
    data_idx = [i for i, r in enumerate(rows) if len(r) >= 4 and _numfrac(r) >= 0.3]
    if len(data_idx) < min_data_rows:
        return None
    first_data = data_idx[0]
    header_rows = rows[:first_data]
    data_rows = [rows[i] for i in data_idx]

    if header_keywords:
        htext = " ".join(_join(r) for r in header_rows).upper()
        if sum(1 for k in header_keywords if k.upper() in htext) < min_keyword_hits:
            return None

    anchors = _column_bands(data_rows)
    if not anchors:
        return None
    ncol = len(anchors)

    # Keep only the true column-header line(s): rows whose tokens spread across
    # many columns. A centered title block ("Progressive Garden State ...") hits
    # only a few middle columns and is dropped.
    def _spread(hr: list[dict]) -> int:
        return len({_assign(w["x0"], anchors) for w in hr})
    col_header_rows = [hr for hr in header_rows
                       if _spread(hr) >= max(3, int(ncol * 0.4))]
    if not col_header_rows:  # fall back to the row just above the data
        col_header_rows = header_rows[-1:] if header_rows else []

    # labels: drop header tokens onto the same columns (top-to-bottom join)
    label_cols: list[list[dict]] = [[] for _ in range(ncol)]
    for hr in col_header_rows:
        for w in hr:
            label_cols[_assign(w["x0"], anchors)].append(w)
    labels = []
    for c in label_cols:
        c_sorted = sorted(c, key=lambda w: (round(w["top"], 1), w["x0"]))
        labels.append(" ".join(w["text"] for w in c_sorted).strip())

    data: list[list[str]] = []
    for row in data_rows:
        cols: list[list[dict]] = [[] for _ in range(ncol)]
        for w in row:
            cols[_assign(w["x0"], anchors)].append(w)
        data.append([_join(c) for c in cols])

    pno = page.page_number or 0
    return GridTable(pno, labels, anchors, data)


def extract(pdf_path, pages: list[int], header_keywords: list[str] | None = None,
            min_keyword_hits: int = 0) -> list[GridTable]:
    """Read the grid on each given 1-based page; skip pages with no grid."""
    out: list[GridTable] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pno in pages:
            if not (1 <= pno <= len(pdf.pages)):
                continue
            gt = read_page(pdf.pages[pno - 1], header_keywords, min_keyword_hits)
            if gt and gt.rows:
                gt.page = pno
                out.append(gt)
    return out
