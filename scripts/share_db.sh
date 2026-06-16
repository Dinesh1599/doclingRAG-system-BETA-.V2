#!/usr/bin/env bash
# Expose the Postgres vector store to a remote machine via an ngrok TCP tunnel,
# so a manager can query it directly (read-only). Postgres must already be
# HARDENED: a strong password on the `rag` app user and a read-only `manager_ro`
# login. NEVER expose the default rag/rag credentials.
#
#   One-time: create a free ngrok account, then
#       ngrok config add-authtoken <YOUR_TOKEN>
#
#   Run:
#       ./scripts/share_db.sh
#
#   ngrok prints a public address like  tcp://0.tcp.ngrok.io:12345
#   Share THIS with your manager (read-only creds):
#       psql "postgres://manager_ro:<MANAGER_PASSWORD>@0.tcp.ngrok.io:12345/rag"
#
# Ctrl+C closes the tunnel. The host machine must stay awake while it's shared.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ngrok >/dev/null || { echo "ERROR: ngrok not installed (brew install ngrok)" >&2; exit 1; }
PORT="${DB_PORT:-5436}"

echo "------------------------------------------------------------"
echo "  Exposing Postgres :$PORT via ngrok TCP."
echo "  Manager connects (READ-ONLY) with:"
echo "    psql \"postgres://manager_ro:<MANAGER_PASSWORD>@<host:port below>/rag\""
echo "  (host:port is the tcp://... address ngrok prints next)"
echo "------------------------------------------------------------"
exec ngrok tcp "$PORT"
