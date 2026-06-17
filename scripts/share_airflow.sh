#!/usr/bin/env bash
# Expose the Airflow UI (port 8080) to a remote machine via a Cloudflare quick
# tunnel. Airflow must ALREADY be running (./scripts/run_airflow.sh). Airflow has
# its own login (admin + the generated password in
# .airflow/simple_auth_manager_passwords.json.generated), so no extra auth here.
#
#   Terminal 1:  ./scripts/run_airflow.sh      # starts Airflow on :8080
#   Terminal 2:  ./scripts/share_airflow.sh    # opens the public tunnel
#
# Share the printed https://...trycloudflare.com URL + the admin login.
# Ctrl+C closes the tunnel (Airflow keeps running).
set -euo pipefail
cd "$(dirname "$0")/.."
command -v cloudflared >/dev/null || { echo "ERROR: cloudflared not installed (brew install cloudflared)" >&2; exit 1; }
PORT="${AIRFLOW_PORT:-8080}"

if ! curl -s -o /dev/null --max-time 3 "http://localhost:$PORT" 2>/dev/null; then
  echo "WARNING: nothing is responding on :$PORT — start Airflow first" >&2
  echo "         (run ./scripts/run_airflow.sh in another terminal)" >&2
fi

echo "------------------------------------------------------------"
echo "  Exposing the Airflow UI (:$PORT). Login with your admin user"
echo "  + the password in .airflow/simple_auth_manager_passwords.json.generated"
echo "  Share the https URL below:"
echo "------------------------------------------------------------"
exec cloudflared tunnel --url "http://localhost:$PORT"
