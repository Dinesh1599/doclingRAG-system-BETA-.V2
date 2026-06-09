"""Lightweight PDF helpers (page count, slicing a page range for the spike)."""

from pathlib import Path

from pypdf import PdfReader, PdfWriter


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)


def slice_pages(pdf_path: Path, start: int, end: int, out_path: Path) -> Path:
    """Write pages [start, end] (1-based, inclusive) to out_path."""
    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    for i in range(start - 1, min(end, len(reader.pages))):
        writer.add_page(reader.pages[i])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return out_path
