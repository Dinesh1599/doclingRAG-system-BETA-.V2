"""Airflow 3 DAG (new SDK style) running the rate-filing pipeline end to end.

The core pipeline also runs as plain Python via the `rate-filing` CLI; this DAG
just wires the same stages as tasks so it can run on a schedule. Section
locations are discovered per-PDF (no hardcoded ranges), so the DAG is
carrier-agnostic: it fans out over every PDF in INPUT_DIR.
"""

from airflow.sdk import dag, task


@dag(dag_id="rate_filing_pipeline", schedule=None, catchup=False, tags=["rate-filing"])
def rate_filing_pipeline():

    @task.python
    def list_pdfs() -> list[str]:
        from rate_filing.config import Config
        cfg = Config.from_env()
        return [str(p) for p in sorted(cfg.input_dir.glob("*.pdf"))]

    @task.python
    def extract_and_write(pdf: str) -> dict:
        from rate_filing.pipeline import run
        res = run(pdf)
        return {"workbook": str(res.workbook), "company": res.company,
                "rows": res.rows, "candidate_pages": res.candidate_pages}

    extract_and_write.expand(pdf=list_pdfs())


rate_filing_pipeline()
