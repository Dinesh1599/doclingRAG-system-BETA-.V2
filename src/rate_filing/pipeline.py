"""End-to-end pipeline — Bill Pay extraction (carrier-agnostic).

Stages: load parsed text (docling cache if present, else fast pdfplumber text)
-> detect company (LLM, for a Company hint) -> find bill-pay pages (deterministic
keyword scan) -> LLM extracts Bill-Pay rows with an Accuracy score -> write one
Bill_Pay sheet. No page locations are hardcoded.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import billpay, doc_model, metadata
from .config import Config
from .excel_writer import write_workbook
from .pdfutil import page_count


@dataclass
class PipelineResult:
    workbook: Path
    company: str
    rows: int
    candidate_pages: int
    notes: dict = field(default_factory=dict)


def _load_text_doc(pdf: Path, log) -> dict:
    """Docling cache if present & fresh, else a fast pdfplumber text layer."""
    cache = pdf.with_suffix(pdf.suffix + ".docling.json")
    if cache.exists() and cache.stat().st_mtime >= pdf.stat().st_mtime:
        log(f"[text] using docling cache {cache.name}")
        return json.loads(cache.read_text())
    log("[text] no docling cache; reading pdf text layer (pdfplumber)")
    return doc_model.from_pdf_text(pdf)


def run(pdf_path: str | Path, cfg: Config | None = None, log=print) -> PipelineResult:
    cfg = cfg or Config.from_env()
    pdf = Path(pdf_path).resolve()
    log(f"[ingest] {pdf.name} ({page_count(pdf)} pages)")

    doc = _load_text_doc(pdf, log)

    # Company hint (LLM names only; helps fill the Company column consistently).
    company = ""
    if cfg.openai_api_key:
        try:
            det = metadata.detect(doc, cfg)
            company = det.get("Company", "") or ""
            log(f"[identity] company hint = {company!r}")
        except Exception as e:  # noqa: BLE001
            log(f"[identity] detection failed ({e}); no company hint")

    pages = billpay.find_billpay_pages(doc, log)

    rows: list[dict] = []
    if not cfg.openai_api_key:
        log("[billpay] no OPENAI_API_KEY — cannot extract bill-pay rows")
    elif pages:
        rows = billpay.extract_billpay(doc, pages, company, cfg, log)

    out_dir = cfg.output_dir
    wb_path = out_dir / f"{pdf.stem}.xlsx"
    write_workbook(wb_path, {}, {"Bill_Pay": rows})

    log(f"[write] {wb_path.name} | Bill_Pay rows={len(rows)}")
    return PipelineResult(wb_path, company, len(rows), len(pages))
