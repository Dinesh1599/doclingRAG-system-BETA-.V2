"""FastAPI chat agent over the Postgres RAG store (chunks + bill_pay).

A question is embedded, the nearest document chunks are retrieved by vector
similarity, and the LLM answers using those chunks PLUS the full (small)
`bill_pay` table as structured context. The answer and the retrieved source
chunks (file + page) are returned and shown in a simple chat UI.

Run:   uv run uvicorn rate_filing.chat_app:app --port 8000
Open:  http://localhost:8000

Optional HTTP basic auth (recommended before exposing publicly): set CHAT_PASSWORD
(and optionally CHAT_USERNAME, default 'manager'). If CHAT_PASSWORD is unset, auth
is disabled (fine for local use).
"""

import os
import secrets
import threading
from pathlib import Path

from fastapi import (BackgroundTasks, Depends, FastAPI, File, HTTPException,
                     UploadFile, status)
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from . import clients, pipeline, vectordb
from .config import Config

app = FastAPI(title="Bill-Pay RAG Chat")
_cfg = Config.from_env()
_REPO = Path(__file__).resolve().parent.parent.parent
_HTML = _REPO / "web" / "chat.html"
# Uploads land here — a DEDICATED folder, NOT input/. Airflow watches input/, so
# keeping uploads separate means the chat upload and the Airflow DAG never touch
# the same file (no double-processing). Files stay here after processing.
_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(_REPO / "upload")))

_security = HTTPBasic(auto_error=False)


def _require_auth(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """Enforce HTTP basic auth when CHAT_PASSWORD is set; otherwise no-op."""
    password = os.environ.get("CHAT_PASSWORD", "")
    if not password:
        return  # auth disabled (local use)
    username = os.environ.get("CHAT_USERNAME", "manager")
    ok = creds and secrets.compare_digest(creds.username, username) \
        and secrets.compare_digest(creds.password, password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"})

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


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(_require_auth)])
def index() -> str:
    return _HTML.read_text(encoding="utf-8")   # explicit: Windows defaults to cp1252


@app.get("/health")
def health() -> dict:
    return {"ok": True, "db": bool(_cfg.database_url), "key": bool(_cfg.openai_api_key)}


# --- file upload -> ingest (drag-drop a PDF, it runs through the pipeline) -----
_jobs: dict[str, dict] = {}          # filename -> {state, detail}
_jobs_lock = threading.Lock()


def _process_upload(path: Path) -> None:
    name = path.name
    with _jobs_lock:
        _jobs[name] = {"state": "processing", "detail": "extracting…"}
    try:
        res = pipeline.run([path], _cfg)
        if name in res.per_file:
            st, detail = "done", (f"{res.per_file[name]} rows, "
                                  f"{res.chunks_stored.get(name, 0)} chunks")
        elif name in res.skipped:
            st, detail = "skipped", res.skipped[name]
        else:
            st, detail = "done", "no rows"
    except Exception as e:  # noqa: BLE001
        st, detail = "error", str(e)[:200]
    with _jobs_lock:
        _jobs[name] = {"state": st, "detail": detail}


@app.post("/upload", dependencies=[Depends(_require_auth)])
async def upload(background: BackgroundTasks, file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        return JSONResponse({"error": "only .pdf files are accepted"}, status_code=400)
    dest_dir = _UPLOAD_DIR                            # NOT input/ — avoids the Airflow race
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / Path(file.filename).name      # strip any path components
    path.write_bytes(await file.read())
    with _jobs_lock:
        _jobs[path.name] = {"state": "queued", "detail": "waiting…"}
    background.add_task(_process_upload, path)        # runs in a threadpool
    return {"filename": path.name, "state": "queued"}


@app.get("/status", dependencies=[Depends(_require_auth)])
def status_endpoint() -> dict:
    with _jobs_lock:
        return dict(_jobs)


@app.post("/chat", dependencies=[Depends(_require_auth)])
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
