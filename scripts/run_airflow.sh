#!/usr/bin/env bash
# Start Airflow (standalone: scheduler + triggerer + dag-processor + API/UI) wired
# to this repo's event-driven DAG. The DAG runs whenever a *.pdf appears in
# input/, processes it, and moves it to processed/ (or skipped/).
#
#   ./scripts/run_airflow.sh
#
# Then open the UI at http://localhost:8080 (login printed on first start).
set -euo pipefail
cd "$(dirname "$0")/.."                      # repo root

export AIRFLOW_HOME="$(pwd)/.airflow"        # self-contained, repo-local
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False

# Export pipeline secrets/config (OPENAI_API_KEY, DATABASE_URL, *_DIR) so Airflow
# task processes inherit them regardless of working directory.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

exec uv run airflow standalone
