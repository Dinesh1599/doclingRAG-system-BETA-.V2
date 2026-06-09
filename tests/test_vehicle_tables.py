"""Coordinate parser must reproduce the golden vehicle rows exactly."""

import itertools
from pathlib import Path

import openpyxl
import pytest

from rate_filing.extractors import vehicle_tables as vt

PDF = Path("input/geico.pdf")
GOLDEN = Path("fixtures/GEICO_NJ_PPA_Rates_Rules_RAG_v4.xlsx")
pytestmark = pytest.mark.skipif(
    not (PDF.exists() and GOLDEN.exists()), reason="GEICO fixtures not present")


def _golden_page(sheet, pno):
    wb = openpyxl.load_workbook(GOLDEN, read_only=True)
    ws = wb[sheet]
    out = []
    for r in ws.iter_rows(min_row=5, values_only=True):
        if r[11] is not None and str(r[11]) == str(pno):
            out.append(tuple("" if x is None else str(x).strip() for x in r[4:11]))
    return out


def _norm(v):
    return (v.model_year, v.make, v.model, v.body_style, v.engine_type,
            v.four_wheel_drive, v.code)


@pytest.mark.parametrize("sheet,anchors,pno", [
    ("19_MBI_Codes", vt.MBI_ANCHORS, 263),
    ("19_MBI_Codes", vt.MBI_ANCHORS, 450),
    ("20_VLR_Symbols", vt.VLR_ANCHORS, 510),
])
def test_vehicle_page_matches_golden(sheet, anchors, pno):
    parsed = [_norm(v) for v, _ in vt.extract(PDF, pno, pno, anchors)]
    gold = _golden_page(sheet, pno)
    assert len(parsed) == len(gold)
    for a, b in itertools.zip_longest(parsed, gold):
        assert a == b
