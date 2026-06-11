"""Postgres + pgvector store for the RAG phase.

Three tables:
  * chunks          — the chunked PDF text + 1536-dim embedding (reuses the
                      pre-existing table; we add nullable page_start/page_end for
                      page-provenance linking).
  * bill_pay        — one row per extracted bill-pay record.
  * bill_pay_chunks — many-to-many link: which chunk(s) each bill-pay row came
                      from (by page overlap), the foreign key into chunks.

Writes are idempotent per source PDF: re-running a file deletes its old chunks
and bill_pay rows first, then re-inserts. If the DB is unreachable the pipeline
degrades to Excel-only (the caller passes conn=None).
"""

import datetime

import psycopg
from dateutil import parser as _dateparser
from pgvector.psycopg import register_vector

from .chunking import Chunk


def parse_date(value) -> datetime.date | None:
    """Parse a messy date string ('Effective 03/01/2014', 'September 21, 2018')
    into a date, or None if there's no parseable date. Fuzzy so leading words
    like 'Effective' are ignored."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return _dateparser.parse(s, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def connect(database_url: str):
    conn = psycopg.connect(database_url, connect_timeout=5)
    register_vector(conn)
    return conn


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id bigserial PRIMARY KEY,
                source text NOT NULL,
                chunk_index int NOT NULL,
                content text NOT NULL,
                image_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
                embedding vector(1536) NOT NULL,
                page_start int,
                page_end int,
                created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (source, chunk_index)
            );""")
        # the pre-existing table may lack page columns -> add them (nullable)
        cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_start int;")
        cur.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page_end int;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bill_pay (
                id bigserial PRIMARY KEY,
                source text NOT NULL,
                company text, serff text, rfc text,
                fee_type text, payment_plan text, fee text,
                eligibility_rule text,
                downpayment_amount text, downpayment_unit text,
                each_installment_value text, each_installment_unit text,
                effective_date date, end_date date,
                source_page text, accuracy_score real,
                created_at timestamptz NOT NULL DEFAULT now()
            );""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bill_pay_chunks (
                bill_pay_id bigint NOT NULL REFERENCES bill_pay(id) ON DELETE CASCADE,
                chunk_id bigint NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                PRIMARY KEY (bill_pay_id, chunk_id)
            );""")
    conn.commit()


def replace_chunks(conn, source: str, chunks: list[Chunk],
                   embeddings: list[list[float]]) -> list[tuple[int, int]]:
    """Delete this source's chunks and insert fresh ones. Returns a list of
    (chunk_id, page) for the inserted chunks (page = the chunk's single page)."""
    out: list[tuple[int, int]] = []
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE source = %s", (source,))
        for ch, emb in zip(chunks, embeddings):
            cur.execute(
                "INSERT INTO chunks (source, chunk_index, content, embedding, "
                "page_start, page_end) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, ch.chunk_index, ch.content, emb, ch.page_start, ch.page_end))
            out.append((cur.fetchone()[0], ch.page_start))
    conn.commit()
    return out


def search_chunks(conn, query_embedding: list[float], top_k: int = 6) -> list[dict]:
    """Nearest chunks to a query embedding (cosine). Returns id/source/page/
    content/similarity, most similar first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, source, page_start, content, 1 - (embedding <=> %s::vector) "
            "FROM chunks ORDER BY embedding <=> %s::vector LIMIT %s",
            (query_embedding, query_embedding, top_k))
        return [{"id": r[0], "source": r[1], "page": r[2], "content": r[3],
                 "similarity": float(r[4])} for r in cur.fetchall()]


def all_bill_pay(conn, limit: int = 500) -> list[dict]:
    """Every bill_pay row as dicts (small table; full structured context)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source, company, fee_type, payment_plan, fee, eligibility_rule, "
            "downpayment_amount, downpayment_unit, each_installment_value, "
            "each_installment_unit, effective_date, end_date, source_page "
            "FROM bill_pay ORDER BY source, id LIMIT %s", (limit,))
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _page_range(source_page) -> tuple[int, int] | None:
    """'4-7' -> (4,7); '4' -> (4,4); anything else -> None."""
    s = str(source_page or "").strip()
    try:
        if "-" in s:
            a, b = s.split("-", 1)
            return int(a), int(b)
        return int(s), int(s)
    except ValueError:
        return None


def _acc(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def store_bill_pay(conn, source: str, rows: list[dict],
                   chunk_pages: list[tuple[int, int]]) -> None:
    """Insert this source's bill_pay rows, link each to the chunk(s) whose page
    falls in the row's Source Page range, and set row['Chunks'] to those ids."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM bill_pay WHERE source = %s", (source,))
        for r in rows:
            # Normalize messy date text -> real dates; mirror the clean MM/DD/YYYY
            # back into the row dict (so any Excel/return output shows just the date).
            eff = parse_date(r.get("Effective Date"))
            end = parse_date(r.get("End Date"))
            r["Effective Date"] = eff.strftime("%m/%d/%Y") if eff else ""
            r["End Date"] = end.strftime("%m/%d/%Y") if end else ""
            cur.execute(
                "INSERT INTO bill_pay (source, company, serff, rfc, fee_type, "
                "payment_plan, fee, eligibility_rule, downpayment_amount, "
                "downpayment_unit, each_installment_value, each_installment_unit, "
                "effective_date, end_date, source_page, accuracy_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (source, r.get("Company"), r.get("SERFF #"), r.get("RFC #"),
                 r.get("Fee Type"), r.get("Payment Plan"), r.get("Fee"),
                 r.get("Eligibility Rule"), r.get("Downpayment Amount"),
                 r.get("Downpayment Unit"), r.get("Each Installment Value"),
                 r.get("Each Installment Unit"), eff, end,
                 r.get("Source Page"), _acc(r.get("Accuracy Score"))))
            bp_id = cur.fetchone()[0]
            rng = _page_range(r.get("Source Page"))
            linked = ([cid for cid, pg in chunk_pages if pg is not None and rng[0] <= pg <= rng[1]]
                      if rng else [])
            for cid in linked:
                cur.execute("INSERT INTO bill_pay_chunks (bill_pay_id, chunk_id) "
                            "VALUES (%s,%s) ON CONFLICT DO NOTHING", (bp_id, cid))
            r["Chunks"] = ",".join(str(c) for c in linked)
    conn.commit()
