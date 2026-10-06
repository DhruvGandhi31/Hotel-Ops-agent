#!/usr/bin/env bash
# Run db/tests/*.sql against the running ops database. Each file is one rolled-back
# transaction made of ASSERTs, so it leaves no data behind and works on a seeded database.
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

shopt -s nullglob
files=(db/tests/*.sql)
[[ ${#files[@]} -gt 0 ]] || { echo "test-db: no tests in db/tests/" >&2; exit 1; }

for f in "${files[@]}"; do
  echo "test-db: $f"
  docker compose exec -T postgres sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$OPS_DB_USER" -d ops' < "$f"
done
echo "test-db: ok"
