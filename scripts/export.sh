#!/usr/bin/env bash
# Export every workflow from the running n8n container into workflows/,
# one file per workflow, normalised for git (pinData and volatile metadata stripped).
#
# workflows/ is replaced wholesale so deletions and renames in n8n show up in git.
# On a fresh instance, run ./scripts/import.sh first or you will export nothing.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stop Git Bash on Windows rewriting /tmp/... arguments

cd "$(dirname "$0")/.."

tmp=/tmp/hotel-ops-export

docker compose exec -T n8n sh -c "rm -rf $tmp && mkdir -p $tmp/raw $tmp/clean"
docker compose exec -T n8n n8n export:workflow --all --separate --output="$tmp/raw/"
docker compose exec -T -e SRC_DIR="$tmp/raw" -e DST_DIR="$tmp/clean" n8n node - < scripts/lib/normalize-workflows.js

count=$(docker compose exec -T n8n sh -c "ls $tmp/clean | wc -l" | tr -d '[:space:]')
shopt -s nullglob
existing=(workflows/*.json)
if [[ "$count" == "0" && ${#existing[@]} -gt 0 ]]; then
  echo "export: n8n has no workflows but workflows/ has ${#existing[@]}; refusing to wipe. Run ./scripts/import.sh first." >&2
  exit 1
fi

mkdir -p workflows
rm -f workflows/*.json
docker compose cp "n8n:$tmp/clean/." workflows/
docker compose exec -T n8n rm -rf "$tmp"

bash scripts/check_workflows.sh
