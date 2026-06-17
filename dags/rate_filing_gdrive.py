"""Airflow DAG — ingest from a Google Drive folder (rclone sync).

Polls a Drive `input` folder every few minutes; when PDFs are present it pulls
them to a local staging dir, runs the bill-pay + RAG pipeline, then moves each
file in Drive to `processed/` (relevant) or `skipped/`. Drive auth + folder paths
come from rclone (see rate_filing.gdrive).

Polling (not push) is used because Drive has no cheap filesystem event; 5 min is
a reasonable cadence (tune via the schedule). This DAG stages into `_gdrive_stage/`,
NOT `input/`, so it never collides with the local-folder DAG (rate_filing_ingest).
"""

import os
import shutil
from pathlib import Path

from airflow.sdk import dag, task

_REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("INPUT_DIR", str(_REPO / "input"))
_STAGE = _REPO / "_gdrive_stage"


@dag(dag_id="rate_filing_gdrive", schedule="*/5 * * * *", catchup=False,
     max_active_runs=1, tags=["rate-filing", "gdrive"])
def rate_filing_gdrive():

    @task
    def sync_and_process() -> dict:
        from rate_filing import gdrive
        from rate_filing.config import Config
        from rate_filing.pipeline import run

        names = gdrive.list_input()
        if not names:
            return {"note": "no PDFs in Drive input", "processed": [], "skipped": []}

        if _STAGE.exists():
            shutil.rmtree(_STAGE)
        pdfs = gdrive.pull(_STAGE)                      # Drive input -> local staging
        res = run(pdfs, Config.from_env())             # triage + extract + store in PG

        for name in res.per_file:                      # relevant -> Drive processed/
            gdrive.archive(name, relevant=True)
        for name in res.skipped:                       # skipped  -> Drive skipped/
            gdrive.archive(name, relevant=False)

        shutil.rmtree(_STAGE, ignore_errors=True)
        return {"rows": res.total_rows,
                "chunks": sum(res.chunks_stored.values()),
                "processed": list(res.per_file), "skipped": list(res.skipped)}

    sync_and_process()


rate_filing_gdrive()
