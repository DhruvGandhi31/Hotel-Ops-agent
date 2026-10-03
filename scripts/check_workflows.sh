#!/usr/bin/env bash
# Static checks on workflows/*.json. Run by CI and at the end of export.sh. Needs jq.
#   - every file is valid JSON
#   - no non-empty pinData (may contain real execution payloads)
#   - credential references carry only {id, name}
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v jq >/dev/null; then
  if [[ "${CI:-}" == "true" ]]; then
    echo "check_workflows: jq not found" >&2
    exit 1
  fi
  echo "check_workflows: jq not found, skipping (CI runs this check)" >&2
  exit 0
fi

shopt -s nullglob
fail=0
for f in workflows/*.json; do
  if ! jq empty "$f" 2>/dev/null; then
    echo "FAIL $f: invalid JSON"; fail=1; continue
  fi
  if ! jq -e '(.pinData // {}) | length == 0' "$f" >/dev/null; then
    echo "FAIL $f: non-empty pinData"; fail=1
  fi
  if ! jq -e '[.nodes[]?.credentials // {} | .[] | keys - ["id", "name"] | .[]] | length == 0' "$f" >/dev/null; then
    echo "FAIL $f: credential reference has fields beyond id/name"; fail=1
  fi
done

[[ $fail -eq 0 ]] && echo "check_workflows: ok"
exit $fail
