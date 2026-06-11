"""End-to-end pipeline — Bill Pay extraction + RAG (carrier-agnostic).

Per PDF: load parsed text (cache-free per-page router: pdfplumber + selective
docling OCR) -> detect company/SERFF/RFC -> chunk the full text per page, embed,
and store in the Postgres `chunks` vector table -> find bill-pay pages -> LLM
extracts Bill-Pay rows -> link each row to its source chunk(s) by page and store
in the `bill_pay` table (FK into chunks). Rows are also appended into one Excel
workbook with a `Chunks` column. If the DB is unreachable, the run degrades to
Excel-only. No page locations are hardcoded.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import billpay, chunking, clients, metadata, page_router, triage, vectordb
from .config import Config
# Excel generation is disabled for now (data goes to Postgres). Kept, not deleted:
# re-enable by uncommenting this import and the write_workbook call in run().
# from .excel_writer import write_workbook
from .pdfutil import page_count


@dataclass
class PipelineResult:
    workbook: Path
    total_rows: int
    per_file: dict[str, int] = field(default_factory=dict)
    companies: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)   # file -> skip reason
    chunks_stored: dict[str, int] = field(default_factory=dict)  # file -> chunk count


def _store_chunks(pdf: Path, doc: dict, cfg: Config, conn, log) -> list[tuple[int, int]]:
    """Chunk the full document per page, embed, and store in the vector DB.
    Returns [(chunk_id, page), ...] for page-provenance linking. [] if no DB."""
    if conn is None or not cfg.openai_api_key:
        return []
    chunks = chunking.doc_to_chunks(doc, cfg.chunk_max_chars)
    if not chunks:
        return []
    embeds = clients.embed(cfg.embed_model, [c.content for c in chunks])
    chunk_pages = vectordb.replace_chunks(conn, pdf.name, chunks, embeds)
    log(f"[rag] {pdf.name}: stored {len(chunk_pages)} chunks (embedded {cfg.embed_model})")
    return chunk_pages


def extract_rows(pdf: Path, cfg: Config, conn=None, log=print) -> tuple[list[dict], str, int]:
    """Extract Bill-Pay rows for one PDF, store chunks + rows in the vector DB,
    and link each row to its source chunk(s) by page. Returns (rows, company,
    n_chunks)."""
    log(f"[ingest] {pdf.name} ({page_count(pdf)} pages)")
    doc = page_router.load_doc(pdf, cfg, log)   # cache-free: pdfplumber + selective OCR

    company = ""
    serff = rfc = ""
    if cfg.openai_api_key:
        try:
            det = metadata.detect(doc, cfg)
            company = det.get("Company", "") or ""
            serff = det.get("SERFF #", "") or ""
            rfc = det.get("RFC #", "") or ""
            log(f"[identity] {pdf.name}: company={company!r} SERFF={serff!r} RFC={rfc!r}")
        except Exception as e:  # noqa: BLE001
            log(f"[identity] {pdf.name}: detection failed ({e}); no company hint")

    chunk_pages = _store_chunks(pdf, doc, cfg, conn, log)

    pages = billpay.find_billpay_pages(doc, log)
    rows: list[dict] = []
    if not cfg.openai_api_key:
        log("[billpay] no OPENAI_API_KEY — cannot extract bill-pay rows")
    elif pages:
        rows = billpay.extract_billpay(doc, pages, company, cfg, log)

    for r in rows:
        r["Source File"] = pdf.name      # provenance: which file each row came from
        r["SERFF #"] = serff             # filing identifiers (deterministic, filing-level)
        r["RFC #"] = rfc
        r.setdefault("Chunks", "")

    if conn is not None:
        vectordb.store_bill_pay(conn, pdf.name, rows, chunk_pages)   # sets r["Chunks"]
        log(f"[rag] {pdf.name}: stored {len(rows)} bill_pay rows linked to chunks")
    return rows, company, len(chunk_pages)


def run(pdf_paths, cfg: Config | None = None, out_name: str = "bill_pay.xlsx",
        skip_triage: bool = False, log=print) -> PipelineResult:
    """Triage one or many PDFs, then APPEND every relevant file's Bill-Pay rows
    into one workbook. Irrelevant files are skipped (with a reason) and never hit
    the expensive docling/LLM extraction."""
    cfg = cfg or Config.from_env()
    if isinstance(pdf_paths, (str, Path)):
        pdf_paths = [pdf_paths]
    pdfs = [Path(p).resolve() for p in pdf_paths]

    # 1) relevance triage (cheap; before any extraction)
    relevant: list[Path] = []
    skipped: dict[str, str] = {}
    if skip_triage:
        relevant = pdfs
    else:
        log(f"[triage] screening {len(pdfs)} file(s)...")
        for pdf in pdfs:
            t = triage.classify(pdf, cfg, log)
            mark = "RELEVANT " if t.relevant else "skip     "
            log(f"[triage] {mark} {pdf.name}  ({t.method}, conf={t.confidence:.2f}): {t.reason}")
            if t.relevant:
                relevant.append(pdf)
            else:
                skipped[pdf.name] = t.reason

    # 2) open the vector DB (RAG). Degrade to Excel-only if unreachable.
    conn = None
    if cfg.database_url and cfg.openai_api_key:
        try:
            conn = vectordb.connect(cfg.database_url)
            vectordb.ensure_schema(conn)
            log(f"[rag] connected to vector store; chunks + bill_pay tables ready")
        except Exception as e:  # noqa: BLE001
            log(f"[rag] DB unavailable ({e}); continuing Excel-only")
            conn = None

    # 3) extract from relevant files only
    all_rows: list[dict] = []
    per_file: dict[str, int] = {}
    companies: dict[str, str] = {}
    chunks_stored: dict[str, int] = {}
    try:
        for pdf in relevant:
            rows, company, n_chunks = extract_rows(pdf, cfg, conn, log)
            all_rows.extend(rows)
            per_file[pdf.name] = len(rows)
            companies[pdf.name] = company
            chunks_stored[pdf.name] = n_chunks
    finally:
        if conn is not None:
            conn.close()

    wb_path = cfg.output_dir / out_name
    # Excel generation disabled for now — data is written to Postgres (chunks +
    # bill_pay). Re-enable by uncommenting the import above and the line below.
    # write_workbook(wb_path, {"Bill_Pay": all_rows})
    log(f"[done] {len(all_rows)} rows from {len(relevant)} relevant file(s); "
        f"{len(skipped)} skipped; {sum(chunks_stored.values())} chunks stored "
        f"(Excel output disabled)")
    return PipelineResult(wb_path, len(all_rows), per_file, companies, skipped,
                          chunks_stored)
