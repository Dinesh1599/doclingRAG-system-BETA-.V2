"""Validation gates (domain + numeric checks).

Failures route rows to the quarantine report; valid rows pass through unchanged.
Conservative by design: a value that fails is flagged, never coerced or guessed.
"""

import re

from .quarantine import Quarantine
from .schema import MBI_CODE_DOMAIN

_ZIP_RE = re.compile(r"^\d{5}$")


def _to_float(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip())
    except (ValueError, AttributeError):
        return None


def validate_vehicle_symbols(rows: list[dict], q: Quarantine) -> list[dict]:
    """MBI symbols must be in the MBI domain; other symbol types must be non-empty."""
    ok = []
    for r in rows:
        st = r.get("Symbol Type")
        sym = str(r.get("Symbol", "")).strip()
        if st == "MBI":
            if sym not in MBI_CODE_DOMAIN:
                q.add("06_Vehicle_Symbols", f"MBI symbol {sym!r} outside domain",
                      r.get("Source Page", ""), detail=str(r))
                continue
        elif not sym:
            q.add("06_Vehicle_Symbols", f"empty {st} symbol",
                  r.get("Source Page", ""), detail=str(r))
            continue
        ok.append(r)
    return ok


def validate_zip(rows: list[dict], q: Quarantine) -> list[dict]:
    ok = []
    for r in rows:
        z = str(r.get("ZIP Code", "")).strip()
        if not _ZIP_RE.match(z):
            q.add("05_Territory_Definitions", f"ZIP {z!r} not 5 digits", detail=str(r))
            continue
        ok.append(r)
    return ok


def validate_factor_values(rows: list[dict], q: Quarantine,
                           lo: float = 0.0, hi: float = 100.0) -> list[dict]:
    """Sanity-range numeric factor values; flag out-of-range, keep the row."""
    for r in rows:
        v = _to_float(r.get("Factor Value"))
        if v is not None and not (lo <= v <= hi):
            q.add("04_Rating_Factors",
                  f"factor value {v} outside [{lo},{hi}]",
                  r.get("Source Page", ""), detail=str(r))
    return rows
