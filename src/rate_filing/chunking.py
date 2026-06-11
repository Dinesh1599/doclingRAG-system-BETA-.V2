"""Split a parsed document into per-page chunks for the vector store.

Each chunk stays within a single page so a bill-pay row can be linked back to its
source chunk(s) by page (page-provenance). A long page is split into ~chunk_max
character windows on paragraph/line boundaries; a short page is one chunk. Chunks
are numbered sequentially across the document (chunk_index), matching the
existing `chunks` table convention (source, chunk_index unique).
"""

from dataclasses import dataclass

from . import doc_model


@dataclass
class Chunk:
    chunk_index: int
    content: str
    page_start: int
    page_end: int


def _split_page(text: str, max_chars: int) -> list[str]:
    """Pack a page's paragraphs/lines into <=max_chars windows without splitting
    mid-line (unless a single line itself exceeds max_chars)."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if buf and len(buf) + 1 + len(line) > max_chars:
            parts.append(buf)
            buf = ""
        if len(line) > max_chars:           # a single very long line: hard-split
            if buf:
                parts.append(buf); buf = ""
            for i in range(0, len(line), max_chars):
                parts.append(line[i:i + max_chars])
            continue
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def doc_to_chunks(doc: dict, max_chars: int = 800) -> list[Chunk]:
    """Per-page chunks in reading order, sequentially numbered from 0."""
    chunks: list[Chunk] = []
    idx = 0
    for page in doc_model.page_numbers(doc):
        page_text = "\n".join(t.text for t in doc_model.texts_on_pages(doc, page, page)).strip()
        if not page_text:
            continue
        for piece in _split_page(page_text, max_chars):
            chunks.append(Chunk(idx, piece, page, page))
            idx += 1
    return chunks
