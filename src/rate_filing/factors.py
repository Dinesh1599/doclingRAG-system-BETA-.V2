"""Melt wide factor tables into universal long-format Rating-Factor rows.

A factor exhibit prints one row per key combination with several coverage
columns (BI, PD, COMP, ...). The carrier-neutral form is LONG: one row per
(key combination, coverage). This module classifies each grid column as a KEY
(dimension) or a VALUE (a coverage's factor) and emits one universal row per
data-cell. Values are copied verbatim -- the LLM is never used to read a number.

Column roles are decided deterministically:
- VALUE column  = its header names a coverage code, OR its data are factor-like
  decimals (e.g. 0.99, 1.06) in the bulk of rows.
- KEY column    = everything else (Experience, Policy Type, Driver Age, ZIP, ...).

Optional: mapper.name_factor_columns() (LLM) can clean noisy dimension names;
the melt itself never depends on it.
"""

import re

from .extractors.grid import GridTable

_COVERAGE_CODES = {"BI", "PD", "COMP", "COLL", "PIP", "UM/UIM", "UM", "UIM",
                   "UMPD", "RENT", "TOW", "LOAN", "MED", "ACPE", "RR",
                   "COMP-TRLR", "COLL-TRLR", "UM&UND", "UIMPD", "CONTENTS"}
_FACTOR_RE = re.compile(r"^\d{1,2}\.\d{2,4}$")   # 0.99, 1.06, 10.0000
_NUMISH = re.compile(r"^[-+]?\$?\d[\d,]*\.?\d*%?$")


def _coverage_in_label(label: str) -> str | None:
    toks = label.replace("/", " / ").split()
    # check multi-token coverage codes first (UM/UIM, COMP-TRLR)
    up = label.upper()
    for code in sorted(_COVERAGE_CODES, key=len, reverse=True):
        if code in up.split() or code in up.replace(" ", "").split("|") or code in up:
            # require a word-ish boundary to avoid 'PD' inside 'SPDX'
            if re.search(rf"(^|[^A-Z]){re.escape(code)}([^A-Z]|$)", up):
                return code
    for t in toks:
        if t.upper() in _COVERAGE_CODES:
            return t.upper()
    return None


def _classify_columns(gt: GridTable) -> tuple[list[int], list[tuple[int, str]]]:
    """Return (key_col_indices, value_cols) where value_cols is a list of
    (col_index, coverage_label)."""
    ncol = len(gt.anchors)
    col_vals: list[list[str]] = [[] for _ in range(ncol)]
    for row in gt.rows:
        for i in range(ncol):
            if i < len(row) and row[i]:
                col_vals[i].append(row[i])

    value_cols: list[tuple[int, str]] = []
    key_cols: list[int] = []
    for i in range(ncol):
        label = gt.labels[i] if i < len(gt.labels) else ""
        cov = _coverage_in_label(label)
        vals = col_vals[i]
        factor_like = vals and sum(1 for v in vals if _FACTOR_RE.match(v)) / len(vals) >= 0.6
        if cov or factor_like:
            value_cols.append((i, cov or label.strip() or f"col{i}"))
        else:
            key_cols.append(i)
    return key_cols, value_cols


def melt_grid(gt: GridTable, factor_type: str, md: dict,
              col_names: dict[int, str] | None = None) -> list[dict]:
    """Convert one grid table into universal Rating-Factor rows."""
    col_names = col_names or {}
    key_cols, value_cols = _classify_columns(gt)
    if not value_cols:
        return []

    def keyname(i: int) -> str:
        return (col_names.get(i)
                or (gt.labels[i].strip() if i < len(gt.labels) and gt.labels[i].strip()
                    else f"col{i}"))

    out: list[dict] = []
    for row in gt.rows:
        dims = [(keyname(i), row[i]) for i in key_cols
                if i < len(row) and row[i].strip()]
        dims_all = " | ".join(f"{n}={v}" for n, v in dims)
        base = {**md, "Factor Type": factor_type, "Dimensions (all)": dims_all,
                "Source Page": str(gt.page)}
        for slot, (n, v) in enumerate(dims[:3], start=1):
            base[f"Dim{slot} Name"] = n
            base[f"Dim{slot} Value"] = v
        for ci, cov in value_cols:
            if ci >= len(row):
                continue
            val = row[ci].strip()
            if not val or not _NUMISH.match(val):
                continue
            out.append({**base, "Coverage": cov, "Factor Value": val})
    return out
