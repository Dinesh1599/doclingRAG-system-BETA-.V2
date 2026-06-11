"""Per-page routing — cache-free hybrid text loading.

For each page we ask one cheap question with pdfplumber: does this page already
have a real text layer, or is it a scanned image? Then:

  * text-bearing page  -> take its pdfplumber text (instant, free)
  * scanned image page -> OCR JUST that page with docling
  * blank / vector page -> nothing to read, skip

Only the scanned pages are sent to docling, so a mixed PDF (mostly digital with a
few scanned pages, e.g. geico.pdf — pages 2–11 are scanned, including the
bill-pay pages 4–8) is handled correctly and cheaply. The merged result is built
in memory and returned in the normalized doc form; NO `<pdf>.docling.json` cache
is written or read. A page needs OCR only when it is BOTH text-poor AND mostly
covered by a raster image, so a sparse-but-digital page keeps whatever text it has.
"""

import tempfile
from pathlib import Path

import pdfplumber

from . import doc_model
from .config import Config
from .docling_client import DoclingConverter
from .pdfutil import slice_pages


def _classify(pdf_path: Path, min_chars: int, min_image_coverage: float):
    """One pdfplumber pass. Returns (digital_texts, ocr_pages, total_pages)."""
    digital_texts: list[dict] = []
    ocr_pages: list[int] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        for i, pg in enumerate(pdf.pages, 1):
            txt = (pg.extract_text() or "").strip()
            parea = (pg.width or 0) * (pg.height or 0)
            icov = 0.0
            if parea:
                icov = sum((im["x1"] - im["x0"]) * (im["bottom"] - im["top"])
                           for im in pg.images) / parea
            if len(txt) < min_chars and icov >= min_image_coverage:
                ocr_pages.append(i)            # scanned image -> OCR
            elif txt:
                digital_texts.append({"page": i, "label": "text", "text": txt})
            # else: blank / vector page with no text and no big image -> skip
    return digital_texts, ocr_pages, total


def _runs(pages: list[int]) -> list[tuple[int, int]]:
    """Group a sorted page list into contiguous (start, end) runs so docling is
    called once per run instead of once per page."""
    runs: list[list[int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return [(a, b) for a, b in runs]


def load_doc(pdf_path, cfg: Config, log=print) -> dict:
    """Cache-free hybrid load: pdfplumber text for digital pages + selective
    docling OCR for scanned pages, merged into the normalized doc form."""
    pdf_path = Path(pdf_path)
    digital_texts, ocr_pages, total = _classify(
        pdf_path, cfg.docling_ocr_min_chars, cfg.docling_ocr_min_image_coverage)
    log(f"[router] {pdf_path.name}: {total} pages | {len(digital_texts)} digital, "
        f"{len(ocr_pages)} scanned -> OCR {_runs(ocr_pages) if ocr_pages else '[]'}")

    parts: list[dict] = [{"_normalized": True,
                          "pages": [t["page"] for t in digital_texts],
                          "texts": digital_texts, "tables": []}]

    if ocr_pages:
        converter = DoclingConverter(do_ocr=cfg.docling_do_ocr,
                                     table_mode=cfg.docling_table_mode,
                                     ocr_engine=cfg.docling_ocr_engine,
                                     num_threads=cfg.docling_num_threads)
        if not converter.health():
            log(f"[router] docling not installed; {len(ocr_pages)} scanned page(s) "
                f"will be MISSING. Run: uv add docling")
        else:
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                for start, end in _runs(ocr_pages):
                    sl = td / f"ocr_{start}_{end}.pdf"
                    slice_pages(pdf_path, start, end, sl)
                    raw = converter.convert_file(sl)
                    parts.append(doc_model.normalize(raw, page_offset=start - 1))
                    log(f"[router] OCR pages {start}-{end} done")

    return doc_model.merge(parts)
