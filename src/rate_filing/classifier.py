"""Section classification (LLM, gpt-4o-mini).

Labels each page by content type and the target sheet(s) it maps to. Typed
output. The LLM only labels; it never emits data values.
"""

from . import doc_model
from .clients import structured
from .config import Config
from .schema import SHEET_NAMES

_SYSTEM = (
    "You classify pages of a U.S. auto-insurance rate-filing manual. For each "
    "page you are given its text and any table headers. Decide which target "
    "workbook sheet(s) the page's content belongs to. Choose only from the "
    "provided sheet names. Do NOT transcribe any data values; only label."
)

_SHEET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "target_sheets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["page", "content_type", "target_sheets"],
            },
        }
    },
    "required": ["pages"],
}


def _page_digest(doc: dict, page: int, max_chars: int = 1200) -> str:
    txt = " ".join(t.text for t in doc_model.texts_on_pages(doc, page, page))[:max_chars]
    tbls = doc_model.tables_on_pages(doc, page, page)
    heads = []
    for t in tbls[:4]:
        if t.rows:
            heads.append(" | ".join(t.rows[0][:8]))
    head_str = "  ;  ".join(heads)
    return f"PAGE {page}\nTEXT: {txt}\nTABLE_HEADERS: {head_str}"


def classify_pages(doc: dict, pages: list[int], cfg: Config,
                   chunk: int = 12) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for i in range(0, len(pages), chunk):
        batch = pages[i:i + chunk]
        digest = "\n\n".join(_page_digest(doc, p) for p in batch)
        user = (f"Valid sheet names:\n{', '.join(SHEET_NAMES)}\n\n"
                f"Classify these pages:\n\n{digest}")
        res = structured(cfg.model_classify, _SYSTEM, user,
                         _SHEET_SCHEMA, "page_classification")
        for p in res.get("pages", []):
            out[p["page"]] = {"content_type": p.get("content_type", ""),
                              "target_sheets": [s for s in p.get("target_sheets", [])
                                                if s in SHEET_NAMES]}
    return out
