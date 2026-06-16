#!/usr/bin/env bash
# Expose the RAG chat agent to a remote machine via a Cloudflare quick tunnel,
# protected by HTTP basic auth. Gives a public https URL to share with your manager.
#
#   1. In .env, set CHAT_PASSWORD=<something strong>  (and optionally CHAT_USERNAME)
#   2. Make sure Postgres is up with data + OPENAI_API_KEY is set
#   3. ./scripts/share_chat.sh
#   4. Share the printed https://...trycloudflare.com URL + the login with your manager
#
# Ctrl+C stops both the tunnel and the chat server. Postgres is NOT exposed — the
# chat reaches it locally; only the chat app is public.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { set -a; . ./.env; set +a; }

if [ -z "${CHAT_PASSWORD:-}" ]; then
  echo "ERROR: set CHAT_PASSWORD in .env before exposing the chat publicly." >&2
  echo "       (protects your OpenAI key — anyone with the URL would otherwise have access)" >&2
  exit 1
fi
command -v cloudflared >/dev/null || { echo "ERROR: cloudflared not installed (brew install cloudflared)" >&2; exit 1; }

echo "Starting chat app on 127.0.0.1:8000 ..."
uv run uvicorn rate_filing.chat_app:app --host 127.0.0.1 --port 8000 --log-level warning &
UVPID=$!
trap 'kill $UVPID 2>/dev/null || true' EXIT
sleep 5

echo "------------------------------------------------------------"
echo "  Login   ->  user: ${CHAT_USERNAME:-manager}   password: (your CHAT_PASSWORD)"
echo "  Sharing the https URL below with your manager:"
echo "------------------------------------------------------------"
exec cloudflared tunnel --url http://localhost:8000
