"""FastAPI "Studio" — a Tableau-like, no-code dashboard builder over Postgres.

The browser never sends SQL. It sends a small JSON *query spec* (table, dimension
columns, aggregated measures, filters) which is validated against the live,
introspected schema and compiled into one parameterized SELECT. New tables that
appear in the database (more `bill_pay`-like extracts) show up in the UI
automatically — nothing here is hardcoded to bill_pay.

Endpoints
  GET  /              the Studio single-page app (web/studio.html)
  GET  /api/schema    tables + columns with inferred roles (dimension/measure/date)
  POST /api/query     run one aggregation query spec -> rows
  POST /api/distinct  distinct values of a column (filter dropdowns)
  POST /chat          RAG chat (same retrieval as chat_app), dashboard-aware
  POST /api/suggest   LLM critique: how to improve the current dashboard

Run:   uv run uvicorn rate_filing.studio_app:app --port 8010
Open:  http://localhost:8010

Auth mirrors chat_app: set CHAT_PASSWORD to enable HTTP basic auth.
"""

import os
import secrets
from pathlib import Path
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import clients, vectordb
from .config import Config

app = FastAPI(title="Rate-Filing Studio")
_cfg = Config.from_env()
_REPO = Path(__file__).resolve().parent.parent.parent
_HTML = _REPO / "web" / "studio.html"

_security = HTTPBasic(auto_error=False)


def _require_auth(creds: HTTPBasicCredentials | None = Depends(_security)) -> None:
    """HTTP basic auth when CHAT_PASSWORD is set; otherwise a no-op (local use)."""
    password = os.environ.get("CHAT_PASSWORD", "")
    if not password:
        return
    username = os.environ.get("CHAT_USERNAME", "manager")
    ok = creds and secrets.compare_digest(creds.username, username) \
        and secrets.compare_digest(creds.password, password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})


# --- schema introspection -----------------------------------------------------
# Columns the query builder refuses to touch (unaggregatable blobs).
_HIDDEN_TYPES = {"vector", "jsonb", "json", "bytea"}
# Studio's own bookkeeping tables never show up as data sources.
_EXCLUDE_TABLES = {"studio_dashboards"}
_NUMERIC_TYPES = {"smallint", "integer", "bigint", "real", "double precision", "numeric"}
_DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}


def _connect():
    conn = psycopg.connect(_cfg.database_url, connect_timeout=5, row_factory=dict_row)
    conn.execute("SET statement_timeout = 15000")  # runaway-query guard (15s)
    return conn


def _role(col: str, data_type: str) -> str:
    """dimension | measure | date. Ids are dimensions: summing them is nonsense."""
    if data_type in _DATE_TYPES:
        return "date"
    if data_type in _NUMERIC_TYPES and not (col == "id" or col.endswith("_id")):
        return "measure"
    return "dimension"


def introspect(conn) -> dict[str, dict[str, dict]]:
    """{table: {column: {type, role}}} for public base tables, hidden types dropped.
    Pure link tables (only *_id columns) are dropped too — noise for non-coders."""
    rows = conn.execute("""
        SELECT c.table_name, c.column_name, c.data_type, c.udt_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position""").fetchall()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r["table_name"] in _EXCLUDE_TABLES:
            continue
        dt = r["data_type"] if r["data_type"] != "USER-DEFINED" else r["udt_name"]
        if dt in _HIDDEN_TYPES or r["udt_name"] in _HIDDEN_TYPES:
            continue
        out.setdefault(r["table_name"], {})[r["column_name"]] = {
            "type": dt, "role": _role(r["column_name"], dt)}
    return {t: cols for t, cols in out.items()
            if any(not (c == "id" or c.endswith("_id")) for c in cols)}


@app.get("/api/schema", dependencies=[Depends(_require_auth)])
def api_schema() -> dict:
    try:
        conn = _connect()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"database unreachable: {e}"}, status_code=503)
    try:
        schema = introspect(conn)
        tables = []
        for name, cols in schema.items():
            n = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}")
                             .format(sql.Identifier(name))).fetchone()["n"]
            tables.append({"name": name, "rows": n, "columns": [
                {"name": c, **meta} for c, meta in cols.items()]})
        # biggest "fact-like" tables first, chunks-style giants last
        tables.sort(key=lambda t: (t["name"] == "chunks", t["name"]))
        return {"tables": tables}
    finally:
        conn.close()


# --- query builder ------------------------------------------------------------
_BUCKETS = {"year", "quarter", "month", "week", "day"}
# agg -> (SQL template over a safely-quoted column, allowed roles)
_NUM_CLEAN = "NULLIF(regexp_replace({col}::text, '[^0-9.\\-]', '', 'g'), '')::numeric"
_AGGS = {
    "count":          ("COUNT(*)",                        {"dimension", "measure", "date"}),
    "count_distinct": ("COUNT(DISTINCT {col})",           {"dimension", "measure", "date"}),
    "sum":            ("SUM({col})",                      {"measure"}),
    "avg":            ("AVG({col})",                      {"measure"}),
    "min":            ("MIN({col})",                      {"measure"}),
    "max":            ("MAX({col})",                      {"measure"}),
    # text columns holding money-ish values ("$15", "3,073,619") -> parse then agg
    "sum_num":        (f"SUM({_NUM_CLEAN})",              {"dimension"}),
    "avg_num":        (f"AVG({_NUM_CLEAN})",              {"dimension"}),
    "min_num":        (f"MIN({_NUM_CLEAN})",              {"dimension"}),
    "max_num":        (f"MAX({_NUM_CLEAN})",              {"dimension"}),
}
_FILTER_OPS = {"in", "not_in", "contains", "eq", "gte", "lte", "between", "not_null"}


class Dim(BaseModel):
    col: str
    bucket: str | None = None            # for date columns: year|quarter|month|week|day


class Measure(BaseModel):
    col: str                             # "*" allowed for plain count
    agg: str = "count"


class Filter(BaseModel):
    col: str
    op: str = "in"
    values: list[Any] = []


class QuerySpec(BaseModel):
    table: str
    dims: list[Dim] = []
    measures: list[Measure] = []
    filters: list[Filter] = []
    sort: str = "desc"                   # by first measure; "asc"|"desc"|"dim"
    limit: int = 500


def _check_col(schema: dict, table: str, col: str) -> dict:
    cols = schema.get(table)
    if cols is None:
        raise HTTPException(400, f"unknown table {table!r}")
    if col not in cols:
        raise HTTPException(400, f"unknown column {table}.{col}")
    return cols[col]


def _filter_sql(f: Filter, meta: dict, params: list) -> sql.Composable:
    ident = sql.Identifier(f.col)
    if f.op not in _FILTER_OPS:
        raise HTTPException(400, f"unknown filter op {f.op!r}")
    if f.op == "not_null":
        return sql.SQL("{} IS NOT NULL").format(ident)
    if not f.values:
        raise HTTPException(400, f"filter on {f.col!r} has no values")
    if f.op in ("in", "not_in"):
        params.append(f.values)
        neg = sql.SQL("NOT ") if f.op == "not_in" else sql.SQL("")
        return sql.SQL("{}({}::text = ANY(%s))").format(neg, ident)
    if f.op == "contains":
        params.append(f"%{f.values[0]}%")
        return sql.SQL("{}::text ILIKE %s").format(ident)
    if f.op == "between":
        if len(f.values) != 2:
            raise HTTPException(400, "between needs [low, high]")
        params.extend(f.values)
        return sql.SQL("{} BETWEEN %s AND %s").format(ident)
    op = {"eq": "=", "gte": ">=", "lte": "<="}[f.op]
    params.append(f.values[0])
    return sql.SQL("{} {} %s").format(ident, sql.SQL(op))


def build_query(spec: QuerySpec, schema: dict) -> tuple[sql.Composed, list]:
    """Compile a validated spec into one parameterized SELECT. Every identifier is
    checked against the introspected schema, so nothing user-typed reaches SQL."""
    if spec.table not in schema:
        raise HTTPException(400, f"unknown table {spec.table!r}")
    select: list[sql.Composable] = []
    group: list[sql.Composable] = []
    params: list = []

    for i, d in enumerate(spec.dims):
        meta = _check_col(schema, spec.table, d.col)
        ident = sql.Identifier(d.col)
        if d.bucket:
            if meta["role"] != "date" or d.bucket not in _BUCKETS:
                raise HTTPException(400, f"bad bucket {d.bucket!r} for {d.col}")
            expr = sql.SQL("date_trunc({}, {})::date").format(
                sql.Literal(d.bucket), ident)
        else:
            expr = ident
        select.append(sql.SQL("{} AS {}").format(expr, sql.Identifier(f"d{i}")))
        group.append(sql.SQL("{}").format(sql.Identifier(f"d{i}")))

    if not spec.measures:
        spec.measures = [Measure(col="*", agg="count")]
    for i, m in enumerate(spec.measures):
        if m.agg not in _AGGS:
            raise HTTPException(400, f"unknown agg {m.agg!r}")
        template, roles = _AGGS[m.agg]
        if m.agg == "count" and m.col == "*":
            expr = sql.SQL("COUNT(*)")
        else:
            meta = _check_col(schema, spec.table, m.col)
            if meta["role"] not in roles:
                raise HTTPException(400, f"agg {m.agg!r} not valid for {m.col}")
            expr = sql.SQL(template.replace("{col}", "{c}")).format(
                c=sql.Identifier(m.col))
        select.append(sql.SQL("{} AS {}").format(expr, sql.Identifier(f"m{i}")))

    where: list[sql.Composable] = []
    for f in spec.filters:
        meta = _check_col(schema, spec.table, f.col)
        where.append(_filter_sql(f, meta, params))

    q = sql.SQL("SELECT ") + sql.SQL(", ").join(select) \
        + sql.SQL(" FROM {}").format(sql.Identifier(spec.table))
    if where:
        q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where)
    if group:
        q += sql.SQL(" GROUP BY ") + sql.SQL(", ").join(group)
    if spec.sort == "dim" and group:
        q += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(group)
    elif spec.measures:
        q += sql.SQL(" ORDER BY {} {}").format(
            sql.Identifier("m0"),
            sql.SQL("ASC" if spec.sort == "asc" else "DESC"))
    q += sql.SQL(" LIMIT {}").format(sql.Literal(max(1, min(spec.limit, 10_000))))
    return q, params


@app.post("/api/query", dependencies=[Depends(_require_auth)])
def api_query(spec: QuerySpec) -> dict:
    conn = _connect()
    try:
        schema = introspect(conn)
        q, params = build_query(spec, schema)
        rows = conn.execute(q, params).fetchall()
        return {"rows": rows, "sql": q.as_string(conn)}   # sql echoed for transparency
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:300]}, status_code=400)
    finally:
        conn.close()


class DistinctSpec(BaseModel):
    table: str
    col: str
    limit: int = 200


@app.post("/api/distinct", dependencies=[Depends(_require_auth)])
def api_distinct(spec: DistinctSpec) -> dict:
    conn = _connect()
    try:
        _check_col(introspect(conn), spec.table, spec.col)
        rows = conn.execute(
            sql.SQL("SELECT DISTINCT {c}::text AS v FROM {t} "
                    "WHERE {c} IS NOT NULL ORDER BY 1 LIMIT {n}").format(
                c=sql.Identifier(spec.col), t=sql.Identifier(spec.table),
                n=sql.Literal(max(1, min(spec.limit, 1000))))).fetchall()
        return {"values": [r["v"] for r in rows]}
    finally:
        conn.close()


# --- drill-through: underlying rows + source evidence --------------------------
class RowsSpec(BaseModel):
    table: str
    filters: list[Filter] = []
    limit: int = 200


@app.post("/api/rows", dependencies=[Depends(_require_auth)])
def api_rows(spec: RowsSpec) -> dict:
    """The raw (visible) rows behind a chart, honoring the same filters."""
    conn = _connect()
    try:
        schema = introspect(conn)
        if spec.table not in schema:
            raise HTTPException(400, f"unknown table {spec.table!r}")
        cols = list(schema[spec.table])
        params: list = []
        where = [_filter_sql(f, _check_col(schema, spec.table, f.col), params)
                 for f in spec.filters]
        q = sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)), sql.Identifier(spec.table))
        if where:
            q += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where)
        q += sql.SQL(" ORDER BY 1 LIMIT {}").format(
            sql.Literal(max(1, min(spec.limit, 1000))))
        rows = conn.execute(q, params).fetchall()
        # evidence exists when the "<table>_chunks" link-table convention is followed
        link = conn.execute("SELECT to_regclass(%s) AS r",
                            (f"{spec.table}_chunks",)).fetchone()["r"]
        return {"columns": cols, "rows": rows, "has_evidence": bool(link)}
    finally:
        conn.close()


class EvidenceSpec(BaseModel):
    table: str
    id: int


@app.post("/api/evidence", dependencies=[Depends(_require_auth)])
def api_evidence(spec: EvidenceSpec) -> dict:
    """Chunks linked to one row via the <table>_chunks join table — the page-level
    provenance trail back into the source PDF."""
    conn = _connect()
    try:
        if spec.table not in introspect(conn):
            raise HTTPException(400, f"unknown table {spec.table!r}")
        link = f"{spec.table}_chunks"
        if not conn.execute("SELECT to_regclass(%s) AS r", (link,)).fetchone()["r"]:
            return {"chunks": []}
        q = sql.SQL(
            "SELECT c.id, c.source, c.page_start, c.page_end, "
            "left(c.content, 400) AS snippet "
            "FROM {link} j JOIN chunks c ON c.id = j.chunk_id "
            "WHERE j.{fk} = %s "
            "ORDER BY c.page_start NULLS LAST, c.chunk_index LIMIT 20").format(
                link=sql.Identifier(link), fk=sql.Identifier(f"{spec.table}_id"))
        return {"chunks": conn.execute(q, (spec.id,)).fetchall()}
    finally:
        conn.close()


@app.get("/files/{name}", dependencies=[Depends(_require_auth)])
def files(name: str):
    """Serve a source PDF so evidence links can deep-link to a page (#page=N)."""
    fname = Path(name).name                      # strip any path components
    upload_dir = Path(os.environ.get("UPLOAD_DIR", str(_REPO / "upload")))
    for d in (_cfg.processed_dir, upload_dir, _cfg.input_dir, _cfg.skipped_dir):
        p = (d if d.is_absolute() else _REPO / d) / fname
        if p.is_file():
            return FileResponse(p, media_type="application/pdf", filename=fname)
    raise HTTPException(404, f"{fname} not found")


# --- saved dashboards (shared across users/browsers) ----------------------------
def _ensure_dash_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS studio_dashboards (
            id bigserial PRIMARY KEY,
            name text UNIQUE NOT NULL,
            spec jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now())""")
    conn.commit()


class DashSave(BaseModel):
    name: str
    spec: dict


@app.get("/api/dashboards", dependencies=[Depends(_require_auth)])
def dash_list() -> dict:
    conn = _connect()
    try:
        _ensure_dash_table(conn)
        rows = conn.execute("SELECT name, updated_at FROM studio_dashboards "
                            "ORDER BY updated_at DESC").fetchall()
        return {"dashboards": rows}
    finally:
        conn.close()


@app.post("/api/dashboards", dependencies=[Depends(_require_auth)])
def dash_save(req: DashSave) -> dict:
    name = (req.name or "").strip()[:80]
    if not name:
        raise HTTPException(400, "dashboard name required")
    conn = _connect()
    try:
        _ensure_dash_table(conn)
        conn.execute("INSERT INTO studio_dashboards (name, spec) VALUES (%s, %s) "
                     "ON CONFLICT (name) DO UPDATE "
                     "SET spec = EXCLUDED.spec, updated_at = now()",
                     (name, Jsonb(req.spec)))
        conn.commit()
        return {"ok": True, "name": name}
    finally:
        conn.close()


@app.get("/api/dashboards/{name}", dependencies=[Depends(_require_auth)])
def dash_get(name: str) -> dict:
    conn = _connect()
    try:
        _ensure_dash_table(conn)
        row = conn.execute("SELECT name, spec, updated_at FROM studio_dashboards "
                           "WHERE name = %s", (name,)).fetchone()
        if not row:
            raise HTTPException(404, f"dashboard {name!r} not found")
        return row
    finally:
        conn.close()


@app.delete("/api/dashboards/{name}", dependencies=[Depends(_require_auth)])
def dash_delete(name: str) -> dict:
    conn = _connect()
    try:
        _ensure_dash_table(conn)
        conn.execute("DELETE FROM studio_dashboards WHERE name = %s", (name,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# --- chat (same retrieval as chat_app) + dashboard-aware context ---------------
_SYSTEM = (
    "You are an assistant inside a dashboard-builder ('Studio') for U.S. "
    "auto-insurance rate filings, focused on BILL-PAY information (payment plans, "
    "downpayments, installments, billing fees). Answer ONLY from the provided "
    "context: a structured BILL_PAY table, retrieved document chunks, and — when "
    "present — the user's CURRENT DASHBOARD spec and the CONVERSATION SO FAR. "
    "Use the conversation history to resolve references like 'it', 'they', or "
    "'that fee'. If asked about 'this chart' or 'this dashboard', use the "
    "dashboard spec. If the answer is not in the context, say you don't know. "
    "Quote numbers as written and cite the source file and page (e.g. "
    "'geico.pdf p4'). Be concise."
)


class ChatTurn(BaseModel):
    role: str                            # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    top_k: int = 6
    dashboard: dict | None = None        # the current dashboard spec, for context
    history: list[ChatTurn] = []         # prior turns, so follow-ups can say "it"


@app.post("/chat", dependencies=[Depends(_require_auth)])
def chat(req: ChatRequest):
    q = (req.question or "").strip()
    if not q:
        return JSONResponse({"error": "empty question"}, status_code=400)
    if not _cfg.openai_api_key:
        return JSONResponse({"error": "OPENAI_API_KEY not set"}, status_code=500)
    # Retrieval must see the conversation too: "tell me more about it" embeds to
    # nothing useful on its own, so fold the last user turns into the query.
    prev_user = [t.content for t in req.history if t.role == "user"][-2:]
    retrieval_q = " \n".join(prev_user + [q])[-2000:]
    try:
        qvec = clients.embed(_cfg.embed_model, [retrieval_q])[0]
        conn = vectordb.connect(_cfg.database_url)
        try:
            chunks = vectordb.search_chunks(conn, qvec, top_k=max(1, min(req.top_k, 20)))
            billpay = vectordb.all_bill_pay(conn)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"retrieval failed: {e}"}, status_code=500)
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
    dash = f"\n\n=== CURRENT DASHBOARD ===\n{req.dashboard}" if req.dashboard else ""
    hist = "\n".join(f"{'USER' if t.role == 'user' else 'ASSISTANT'}: {t.content[:1200]}"
                     for t in req.history[-8:])
    hist = f"\n\n=== CONVERSATION SO FAR ===\n{hist}" if hist else ""
    answer = clients.chat(
        _cfg.model_extract, _SYSTEM,
        f"=== BILL_PAY TABLE ===\n{bp}\n\n"
        f"=== RETRIEVED DOCUMENT CHUNKS ===\n{docs}{dash}{hist}\n\n"
        f"CURRENT QUESTION: {q}")
    sources = [{"id": c["id"], "source": c["source"], "page": c["page"],
                "similarity": round(c["similarity"], 3),
                "snippet": (c["content"][:200] + ("…" if len(c["content"]) > 200 else ""))}
               for c in chunks]
    return {"answer": answer, "sources": sources}


# --- AI dashboard critic --------------------------------------------------------
_SUGGEST_SYSTEM = (
    "You are a data-visualization / UX reviewer for a self-serve dashboard tool "
    "used by insurance analysts who do not code. You get the database schema and "
    "the user's current dashboard spec (charts with their table, dimensions, "
    "measures, chart types, plus global filters). Reply in short markdown "
    "bullets, max ~8, grouped under '**Quick wins**' and '**Consider next**'. "
    "Suggest: better chart-type choices for the data's job, fields worth adding, "
    "filters that would help, misleading encodings to fix (e.g. pie with many "
    "slices, summed text columns), and one dashboard-level improvement. Be "
    "specific to the actual fields — never generic advice."
)


class SuggestRequest(BaseModel):
    dashboard: dict


@app.post("/api/suggest", dependencies=[Depends(_require_auth)])
def api_suggest(req: SuggestRequest):
    if not _cfg.openai_api_key:
        return JSONResponse({"error": "OPENAI_API_KEY not set"}, status_code=500)
    try:
        conn = _connect()
        try:
            schema = introspect(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        schema = {}
    answer = clients.chat(
        _cfg.model_extract, _SUGGEST_SYSTEM,
        f"SCHEMA: {schema}\n\nCURRENT DASHBOARD: {req.dashboard}")
    return {"suggestions": answer}


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(_require_auth)])
def index() -> str:
    return _HTML.read_text(encoding="utf-8")   # explicit: Windows defaults to cp1252


@app.get("/health")
def health() -> dict:
    return {"ok": True, "db": bool(_cfg.database_url), "key": bool(_cfg.openai_api_key)}
