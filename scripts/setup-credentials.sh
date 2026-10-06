#!/usr/bin/env bash
# Create the n8n credentials the workflows reference, from .env. Idempotent: re-running updates
# them in place (fixed ids), so a changed password or token takes effect.
#
# Credentials are NEVER exported into the repo: workflows hold only {id, name}. A fresh clone
# runs this once after `docker compose up`, before importing workflows.
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "setup-credentials: .env not found; run scripts/init-env.sh" >&2; exit 1; }
caller_ollama_url="${OLLAMA_BASE_URL:-}"  # an explicit override on the command line wins over .env
set -a; . ./.env; set +a
: "${OPS_DB_USER:?}" "${OPS_DB_PASSWORD:?}" "${INGEST_WEBHOOK_TOKEN:?}"
OLLAMA_BASE_URL="${caller_ollama_url:-${OLLAMA_BASE_URL:-http://host.docker.internal:11434}}"

trap 'docker compose exec -T n8n rm -f /tmp/hotel-ops-credentials.json 2>/dev/null || true' EXIT

# Built with Python's json so a password containing quotes or backslashes stays valid JSON, and
# streamed straight into the container: no plaintext secrets file on the host.
OPS_DB_USER="$OPS_DB_USER" OPS_DB_PASSWORD="$OPS_DB_PASSWORD" INGEST_WEBHOOK_TOKEN="$INGEST_WEBHOOK_TOKEN" \
OLLAMA_BASE_URL="$OLLAMA_BASE_URL" python - <<'PY' | docker compose exec -T n8n sh -c 'umask 077; cat > /tmp/hotel-ops-credentials.json'
import json, os

creds = [
    {
        "id": "hoaCredOpsPg0001",
        "name": "Ops Postgres",
        "type": "postgres",
        "data": {
            "host": "postgres", "port": 5432, "database": "ops",
            "user": os.environ["OPS_DB_USER"], "password": os.environ["OPS_DB_PASSWORD"],
            "ssl": "disable", "allowUnauthorizedCerts": False, "maxConnections": 10,
            "sshTunnel": False,
        },
    },
    {
        "id": "hoaCredOllama001",
        "name": "Ollama",
        "type": "ollamaApi",
        "data": {"baseUrl": os.environ["OLLAMA_BASE_URL"]},
    },
    {
        "id": "hoaCredIngestTk1",
        "name": "Ingest Webhook Token",
        "type": "httpHeaderAuth",
        "data": {"name": "X-Ingest-Token", "value": os.environ["INGEST_WEBHOOK_TOKEN"]},
    },
]
print(json.dumps(creds))
PY

docker compose exec -T n8n n8n import:credentials --input=/tmp/hotel-ops-credentials.json
echo "setup-credentials: ok (Ops Postgres, Ollama at $OLLAMA_BASE_URL, Ingest Webhook Token)"
