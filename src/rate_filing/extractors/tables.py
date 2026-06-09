"""Deterministic copying of Docling table cells into target-sheet rows.

The LLM (mapper) decides which source column index feeds each target column;
this module copies the actual cell values verbatim and attaches Source Page.
Numbers are never produced by the model.
"""

import re

from .. import doc_model
from ..schema import METADATA_COLUMNS, SHEET_BY_NAME

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _looks_like_header(row: list[str], targets_present: int) -> bool:
    cells = [c.strip() for c in row]
    alpha = sum(1 for c in cells if c and not _NUM_RE.fullmatch(c.replace(",", "")))
    return alpha >= max(2, len(cells) // 2)


def copy_table(table: doc_model.Table, target_sheet: str,
               col_to_idx: dict[str, int | None], metadata: dict,
               skip_header: bool = True) -> list[dict]:
    """Copy a Docling table into rows keyed by the target sheet's columns."""
    spec = SHEET_BY_NAME[target_sheet]
    page_col = spec.page_col
    rows: list[dict] = []
    body = table.rows[1:] if skip_header and table.rows else table.rows
    for src in body:
        if not any(c.strip() for c in src):
            continue
        row: dict = {}
        if spec.has_metadata:
            for m in METADATA_COLUMNS:
                row[m] = metadata.get(m)
        for col, idx in col_to_idx.items():
            if idx is None or idx >= len(src):
                row[col] = None
            else:
                row[col] = src[idx].strip() or None
        if page_col and table.page is not None:
            row[page_col] = table.page
        rows.append(row)
    return rows


def two_column_pairs(table: doc_model.Table, left: str, right: str,
                     metadata: dict, target_sheet: str) -> list[dict]:
    """For simple key/value tables rendered as 2 columns (e.g. ZIP -> Territory)."""
    spec = SHEET_BY_NAME[target_sheet]
    rows: list[dict] = []
    for src in table.rows:
        cells = [c.strip() for c in src]
        if len(cells) < 2 or not cells[0]:
            continue
        row = {m: metadata.get(m) for m in METADATA_COLUMNS} if spec.has_metadata else {}
        row[left] = cells[0]
        row[right] = cells[1]
        rows.append(row)
    return rows
