"""Airflow 3 DAG — event-driven bill-pay + RAG ingestion.

Runs automatically whenever a PDF appears in INPUT_DIR. The trigger is an Asset
watched by a FileTrigger (Airflow 3 event-driven scheduling): the triggerer polls
the watch path and, when a `*.pdf` is present, emits an asset event that starts a
DAG run. The run processes every PDF through the pipeline (triage -> per-page
router/OCR -> chunk + embed -> extract -> store in Postgres) and then ARCHIVES
each file out of INPUT_DIR: relevant files to PROCESSED_DIR, irrelevant (skipped)
files to SKIPPED_DIR. Clearing INPUT_DIR is what lets the next arrival re-trigger.

Paths are resolved absolute from the repo root so they are independent of
Airflow's working directory; they can be overridden via INPUT_DIR / PROCESSED_DIR
/ SKIPPED_DIR env vars.
"""

import os
import shutil
from pathlib import Path

from airflow.sdk import Asset, AssetWatcher, dag, task

from rate_filing.airflow_triggers import FileArrivalTrigger

# Resolve project dirs to absolute paths and export them so Config.from_env()
# (used by the pipeline) and this DAG agree regardless of CWD.
_REPO = Path(__file__).resolve().parent.parent
_INPUT = Path(os.environ.setdefault("INPUT_DIR", str(_REPO / "input"))).resolve()
_PROCESSED = Path(os.environ.setdefault("PROCESSED_DIR", str(_REPO / "processed"))).resolve()
_SKIPPED = Path(os.environ.setdefault("SKIPPED_DIR", str(_REPO / "skipped"))).resolve()
os.environ["INPUT_DIR"] = str(_INPUT)

# Event-driven trigger: fire when any PDF is present in INPUT_DIR.
_pdf_arrival = Asset(
    "rate_filing_input_pdfs",
    watchers=[AssetWatcher(
        name="input_pdf_watcher",
        trigger=FileArrivalTrigger(filepath=str(_INPUT / "*.pdf"), poll_interval=10),
    )],
)


def _archive(name: str, dest: Path) -> None:
    src = _INPUT / name
    if src.exists():
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest / name))


@dag(dag_id="rate_filing_ingest", schedule=[_pdf_arrival], catchup=False,
     tags=["rate-filing", "rag"])
def rate_filing_ingest():

    @task
    def process_and_archive() -> dict:
        from rate_filing.config import Config
        from rate_filing.pipeline import run

        cfg = Config.from_env()
        pdfs = sorted(cfg.input_dir.glob("*.pdf"))
        if not pdfs:
            return {"processed": [], "skipped": [], "note": "no PDFs in input"}

        res = run(pdfs, cfg)                       # triage + extract + store in PG
        for name in res.per_file:                  # relevant -> processed/
            _archive(name, _PROCESSED)
        for name in res.skipped:                   # irrelevant -> skipped/
            _archive(name, _SKIPPED)
        return {"rows": res.total_rows,
                "chunks": sum(res.chunks_stored.values()),
                "processed": list(res.per_file), "skipped": list(res.skipped)}

    process_and_archive()


rate_filing_ingest()
