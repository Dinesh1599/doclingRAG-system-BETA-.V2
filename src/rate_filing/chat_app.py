"""FastAPI chat agent over the Postgres RAG store (chunks + bill_pay).

A question is embedded, the nearest document chunks are retrieved by vector
similarity, and the LLM answers using those chunks PLUS the full (small)
`bill_pay` table as structured context. The answer and the retrieved source
chunks (file + page) are returned and shown in a simple chat UI.

Run:   uv run uvicorn rate_filing.chat_app:app --port 8000
Open:  http://localhost:8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import clients, vectordb
from .config import Config

app = FastAPI(title="Bill-Pay RAG Chat")
_cfg = Config.from_env()
_HTML = (Path(__file__).resolve().parent.parent.parent / "web" / "chat.html")

_SYSTEM = (
    "You are an assistant for U.S. auto-insurance rate filings, focused on "
    "BILL-PAY information (payment plans, downpayments, installments, billing "
    "fees). Answer ONLY from the provided context: a structured BILL_PAY table "
    "and retrieved document chunks. If the answer is not in the context, say you "
    "don't know rather than guessing. Quote concrete numbers as written and cite "
    "the source file and page (e.g. 'geico.pdf p4'). Be concise."
)


class ChatRequest(BaseModel):
    question: str
    top_k: int = 6


def _build_context(question: str, top_k: int):
    """Embed the question, retrieve chunks + bill_pay rows from Postgres."""
    qvec = clients.embed(_cfg.embed_model, [question])[0]
    conn = vectordb.connect(_cfg.database_url)
    try:
        chunks = vectordb.search_chunks(conn, qvec, top_k=top_k)
        billpay = vectordb.all_bill_pay(conn)
    finally:
        conn.close()
    return chunks, billpay


def _format(chunks, billpay) -> str:
    bp = "\n".join(
        f"- {b.get('company')} | {b.get('fee_type')} | plan={b.get('payment_plan') or '-'} "
        f"| fee={b.get('fee') or '-'} | down={b.get('downpayment_amount') or '-'}"
        f"{b.get('downpayment_unit') or ''} | each={b.get('each_installment_value') or '-'}"
        f"{b.get('each_installment_unit') or ''} | eff={b.get('effective_date') or '-'} "
        f"| {b.get('source')} p{b.get('source_page')}"
        for b in billpay) or "(no bill_pay rows)"
    docs = "\n\n".join(
        f"[chunk {c['id']} | {c['source']} p{c['page']} | sim={c['similarity']:.2f}]\n{c['content']}"
        for c in chunks) or "(no chunks)"
    return f"=== BILL_PAY TABLE ===\n{bp}\n\n=== RETRIEVED DOCUMENT CHUNKS ===\n{docs}"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _HTML.read_text()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "db": bool(_cfg.database_url), "key": bool(_cfg.openai_api_key)}


@app.post("/chat")
def chat(req: ChatRequest):
    q = (req.question or "").strip()
    if not q:
        return JSONResponse({"error": "empty question"}, status_code=400)
    if not _cfg.openai_api_key:
        return JSONResponse({"error": "OPENAI_API_KEY not set"}, status_code=500)
    try:
        chunks, billpay = _build_context(q, max(1, min(req.top_k, 20)))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"retrieval failed: {e}"}, status_code=500)
    answer = clients.chat(_cfg.model_extract, _SYSTEM, f"QUESTION: {q}\n\n{_format(chunks, billpay)}")
    sources = [{"id": c["id"], "source": c["source"], "page": c["page"],
                "similarity": round(c["similarity"], 3),
                "snippet": (c["content"][:200] + ("…" if len(c["content"]) > 200 else ""))}
               for c in chunks]
    return {"answer": answer, "sources": sources}
