#!/usr/bin/env bash
# Import workflows/*.json into the running n8n container.
# Workflows keep their ids, so re-running updates in place instead of duplicating.
# Imported workflows are left inactive (n8n default); activate deliberately.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stop Git Bash on Windows rewriting /tmp/... arguments

cd "$(dirname "$0")/.."

shopt -s nullglob
files=(workflows/*.json)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "import: workflows/ is empty, nothing to do"
  exit 0
fi

tmp=/tmp/hotel-ops-import

docker compose exec -T n8n sh -c "rm -rf $tmp && mkdir -p $tmp"
docker compose cp workflows/. "n8n:$tmp/"
docker compose exec -T n8n n8n import:workflow --separate --input="$tmp"
docker compose exec -T n8n rm -rf "$tmp"

echo "import: ${#files[@]} workflow file(s) imported"
