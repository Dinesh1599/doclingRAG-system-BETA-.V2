"""Reading the DoclingDocument JSON and a normalized, batch-mergeable form.

Large PDFs are parsed in page batches (the docling-serve CPU worker OOMs on a
single 262-page accurate-table job). Each batch's raw DoclingDocument is
normalized to a small dict — only what the extractors need — and merged with a
page offset so page numbers stay global (1-based original PDF pages).

Normalized form:
    {"_normalized": true,
     "pages": [1, 2, ...],
     "texts":  [{"page": N, "label": "...", "text": "..."}],
     "tables": [{"page": N, "num_rows": R, "num_cols": C, "rows": [[str,...],...]}]}
"""

from dataclasses import dataclass


def _prov_page(item: dict) -> int | None:
    prov = item.get("prov") or []
    if prov and isinstance(prov, list):
        p = prov[0]
        return p.get("page_no") or p.get("page")
    return item.get("page_no") or item.get("page")


def _grid_to_rows(data: dict) -> list[list[str]]:
    grid = data.get("grid")
    if isinstance(grid, list) and grid:
        return [[(str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip())
                 for c in row] for row in grid]
    cells = data.get("table_cells") or []
    nr = data.get("num_rows", 0)
    nc = data.get("num_cols", 0)
    if not nr or not nc:
        for cell in cells:
            nr = max(nr, int(cell.get("end_row_offset_idx", cell.get("start_row_offset_idx", 0) + 1)))
            nc = max(nc, int(cell.get("end_col_offset_idx", cell.get("start_col_offset_idx", 0) + 1)))
    rows = [["" for _ in range(nc)] for _ in range(nr)]
    for cell in cells:
        r = int(cell.get("start_row_offset_idx", 0))
        c = int(cell.get("start_col_offset_idx", 0))
        if 0 <= r < nr and 0 <= c < nc:
            rows[r][c] = str(cell.get("text", "")).strip()
    return rows


def normalize(raw: dict, page_offset: int = 0) -> dict:
    """Convert a raw DoclingDocument to the normalized form, adding page_offset
    to every page number (used to make batch pages global)."""
    pages_obj = raw.get("pages")
    if isinstance(pages_obj, dict):
        page_nums = [int(k) + page_offset for k in pages_obj.keys()]
    elif isinstance(pages_obj, list):
        page_nums = [p.get("page_no", i + 1) + page_offset for i, p in enumerate(pages_obj)]
    else:
        page_nums = []

    texts = []
    for item in raw.get("texts", []) or []:
        if not isinstance(item, dict):
            continue
        txt = (item.get("text") or item.get("orig") or "").strip()
        if not txt:
            continue
        pg = _prov_page(item)
        texts.append({"page": (pg + page_offset) if pg else None,
                      "label": item.get("label", ""), "text": txt})

    tables = []
    for t in raw.get("tables", []) or []:
        data = t.get("data") or {}
        rows = _grid_to_rows(data)
        pg = _prov_page(t)
        tables.append({"page": (pg + page_offset) if pg else None,
                       "num_rows": data.get("num_rows", len(rows)),
                       "num_cols": data.get("num_cols", max((len(r) for r in rows), default=0)),
                       "rows": rows})

    return {"_normalized": True, "pages": sorted(set(page_nums)),
            "texts": texts, "tables": tables}


def from_pdf_text(pdf_path, pages: list[int] | None = None) -> dict:
    """Build the normalized form from a PDF's digital text layer via pdfplumber.

    Fast, deterministic, no services. Used for carriers whose PDFs have a clean
    text layer (e.g. Progressive), so identity/rule-text steps don't require a
    full docling parse. Carries texts only (no tables); coordinate/table work
    reads the PDF directly through the extractors.
    """
    import pdfplumber

    texts: list[dict] = []
    page_nums: list[int] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        n = len(pdf.pages)
        scan = pages or range(1, n + 1)
        for pno in scan:
            if not (1 <= pno <= n):
                continue
            page_nums.append(pno)
            txt = (pdf.pages[pno - 1].extract_text() or "").strip()
            if txt:
                texts.append({"page": pno, "label": "text", "text": txt})
    return {"_normalized": True, "pages": sorted(set(page_nums)),
            "texts": texts, "tables": []}


def merge(docs: list[dict]) -> dict:
    out = {"_normalized": True, "pages": [], "texts": [], "tables": []}
    pages: set[int] = set()
    for d in docs:
        pages.update(d.get("pages", []))
        out["texts"].extend(d.get("texts", []))
        out["tables"].extend(d.get("tables", []))
    out["pages"] = sorted(pages)
    return out


# --- accessors over the normalized form -----------------------------------

@dataclass
class Table:
    page: int | None
    num_rows: int
    num_cols: int
    rows: list[list[str]]

    def cell(self, r: int, c: int) -> str:
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]):
            return self.rows[r][c]
        return ""


@dataclass
class TextItem:
    page: int | None
    label: str
    text: str


def _ensure_normalized(doc: dict) -> dict:
    return doc if doc.get("_normalized") else normalize(doc)


def tables(doc: dict) -> list[Table]:
    doc = _ensure_normalized(doc)
    return [Table(t["page"], t["num_rows"], t["num_cols"], t["rows"])
            for t in doc.get("tables", [])]


def tables_on_pages(doc: dict, start: int, end: int) -> list[Table]:
    return [t for t in tables(doc) if t.page is not None and start <= t.page <= end]


def texts(doc: dict) -> list[TextItem]:
    doc = _ensure_normalized(doc)
    return [TextItem(t["page"], t["label"], t["text"]) for t in doc.get("texts", [])]


def texts_on_pages(doc: dict, start: int, end: int) -> list[TextItem]:
    return [t for t in texts(doc) if t.page is not None and start <= t.page <= end]


def page_numbers(doc: dict) -> list[int]:
    return _ensure_normalized(doc).get("pages", [])
