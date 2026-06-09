"""Batched Docling parse of a PDF into the normalized, cached form.

The docling-serve CPU worker OOMs on a single large accurate-table job, so we
slice the PDF into page batches, convert each, and merge with global page
numbers. The merged result is cached as `<pdf>.docling.json` and reused if
present and newer than the PDF.
"""

import json
import sys
import tempfile
from pathlib import Path

from . import doc_model
from .config import Config
from .docling_client import DoclingClient
from .pdfutil import page_count, slice_pages

DEFAULT_BATCH = 15


def parse_pdf(pdf_path: Path, client: DoclingClient, start: int = 1,
              end: int | None = None, batch: int = DEFAULT_BATCH,
              force: bool = False, log=print) -> dict:
    pdf_path = Path(pdf_path).resolve()
    cache = pdf_path.with_suffix(pdf_path.suffix + ".docling.json")
    if not force and cache.exists() and cache.stat().st_mtime >= pdf_path.stat().st_mtime:
        log(f"[parse] using cached {cache.name}")
        return json.loads(cache.read_text())

    total = page_count(pdf_path)
    end = total if end is None else min(end, total)
    parts: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for b0 in range(start, end + 1, batch):
            b1 = min(b0 + batch - 1, end)
            sl = td / f"b_{b0}_{b1}.pdf"
            slice_pages(pdf_path, b0, b1, sl)
            raw = client.convert_file(sl)
            parts.append(doc_model.normalize(raw, page_offset=b0 - 1))
            log(f"[parse] pages {b0}-{b1} done "
                f"(tables so far={sum(len(p['tables']) for p in parts)})")

    merged = doc_model.merge(parts)
    cache.write_text(json.dumps(merged))
    log(f"[parse] cached {cache.name}: pages={len(merged['pages'])} "
        f"texts={len(merged['texts'])} tables={len(merged['tables'])}")
    return merged


def _cli(argv: list[str]) -> int:
    cfg = Config.from_env()
    client = DoclingClient(cfg.docling_serve_url)
    if not client.health():
        print(f"docling-serve not reachable at {cfg.docling_serve_url}", file=sys.stderr)
        return 1
    pdf = Path(argv[1]) if len(argv) > 1 else cfg.input_dir / "geico.pdf"
    end = int(argv[2]) if len(argv) > 2 else None
    parse_pdf(pdf, client, end=end, force=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
