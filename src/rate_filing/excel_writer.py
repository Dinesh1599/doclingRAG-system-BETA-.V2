"""Assemble the output workbook from extracted rows, following the fixed layout.

Input is a mapping {sheet_name: list[dict]} where each dict is keyed by the
sheet's column labels (from schema.py). Metadata columns are auto-filled from
`metadata` when a row omits them. Every sheet in the schema is written even if
it has no rows.
"""

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import schema
from .schema import (DATA_START_ROW, HEADER_ROW, METADATA_COLUMNS, NOTE_ROW,
                     SHEETS, TITLE_ROW, SheetSpec)

_TITLE_FONT = Font(bold=True, size=12)
_NOTE_FONT = Font(italic=True, size=9, color="555555")
_HEADER_FONT = Font(bold=True)


def _write_sheet(ws, spec: SheetSpec, rows: list[dict], metadata: dict,
                 note: str | None) -> None:
    ncols = len(spec.columns)
    ws.cell(row=TITLE_ROW, column=1, value=spec.title).font = _TITLE_FONT
    if ncols > 1:
        ws.merge_cells(start_row=TITLE_ROW, start_column=1,
                       end_row=TITLE_ROW, end_column=ncols)
    note_text = note if note is not None else spec.note
    if note_text:
        c = ws.cell(row=NOTE_ROW, column=1, value=note_text)
        c.font = _NOTE_FONT
        if ncols > 1:
            ws.merge_cells(start_row=NOTE_ROW, start_column=1,
                           end_row=NOTE_ROW, end_column=ncols)

    for j, col in enumerate(spec.columns, start=1):
        ws.cell(row=HEADER_ROW, column=j, value=col).font = _HEADER_FONT

    for i, row in enumerate(rows):
        r = DATA_START_ROW + i
        for j, col in enumerate(spec.columns, start=1):
            val = row.get(col)
            if val is None and spec.has_metadata and col in METADATA_COLUMNS:
                val = metadata.get(col)
            ws.cell(row=r, column=j, value=val)

    for j, col in enumerate(spec.columns, start=1):
        width = min(max(len(str(col)) + 2, 10), 40)
        ws.column_dimensions[get_column_letter(j)].width = width


def _readme_rows(metadata: dict, sheet_rows: dict[str, list[dict]]) -> list[dict]:
    counts = {k: len(v) for k, v in sheet_rows.items() if v}
    rows = [
        {"How to use": "Carrier-neutral, long-format dataset. Coverages and "
         "labels are CELL VALUES (not columns), so any carrier stacks into the "
         "same sheets. New factor types are new values, never new columns.",
         "Detail": ""},
        {"How to use": "", "Detail": ""},
        {"How to use": "Filing identity", "Detail": " | ".join(
            f"{k}: {metadata.get(k, '') or '(none)'}" for k in METADATA_COLUMNS)},
        {"How to use": "Provenance", "Detail": "See 09_Source_Map for which pages "
         "fed each sheet; 10_Quarantine lists anything flagged for review."},
        {"How to use": "", "Detail": ""},
        {"How to use": "Row counts", "Detail": ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items()))},
    ]
    return rows


def write_workbook(out_path: Path, metadata: dict,
                   sheet_rows: dict[str, list[dict]],
                   notes: dict[str, str] | None = None) -> Path:
    notes = notes or {}
    wb = Workbook()
    wb.remove(wb.active)
    for spec in SHEETS:
        ws = wb.create_sheet(title=spec.name)
        if spec.name == "00_README":
            rows = _readme_rows(metadata, sheet_rows)
        else:
            rows = sheet_rows.get(spec.name, [])
        _write_sheet(ws, spec, rows, metadata, notes.get(spec.name))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
