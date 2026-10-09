#!/usr/bin/env bash
# Create .env from .env.example with freshly generated secrets.
# Refuses to overwrite an existing .env (that would orphan the database and
# make stored n8n credentials undecryptable).
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -e .env ]]; then
  echo "init-env: .env already exists, leaving it alone" >&2
  exit 1
fi

secret() { head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
# Random; the fixed prefix only satisfies n8n's rule of a digit and an upper-case letter. Not a credential.
owner_pw="Ho1-$(secret | head -c 32)" # ggignore

sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(secret)|" \
  -e "s|^N8N_DB_PASSWORD=.*|N8N_DB_PASSWORD=$(secret)|" \
  -e "s|^OPS_DB_PASSWORD=.*|OPS_DB_PASSWORD=$(secret)|" \
  -e "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=$(secret)|" \
  -e "s|^INGEST_WEBHOOK_TOKEN=.*|INGEST_WEBHOOK_TOKEN=$(secret)|" \
  -e "s|^N8N_OWNER_PASSWORD=.*|N8N_OWNER_PASSWORD=$owner_pw|" \
  .env.example > .env

echo "init-env: wrote .env with generated secrets"
