#!/bin/sh
# Applies $MIGRATIONS_DIR/NNN_*.sql to the database named by the PG* env vars,
# in filename order, each exactly once, each in its own transaction.
#
# Rules for migration files:
#   - never edit a migration after it has been applied; add a new one instead
#     (applied checksums are verified and a mismatch aborts the run);
#   - no BEGIN/COMMIT inside the file; the runner wraps it in a transaction.
set -eu

dir="${MIGRATIONS_DIR:-/migrations}"

export PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning"

q() { psql -X -q -t -A -v ON_ERROR_STOP=1 "$@"; }

q <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    text PRIMARY KEY,
  checksum   text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

applied_count=0
for f in "$dir"/[0-9][0-9][0-9]_*.sql; do
  [ -e "$f" ] || break
  version=$(basename "$f" .sql)
  checksum=$(sha256sum "$f" | cut -d' ' -f1)

  existing=$(echo "SELECT checksum FROM schema_migrations WHERE version = :'v';" | q -v v="$version")

  if [ -n "$existing" ]; then
    if [ "$existing" != "$checksum" ]; then
      echo "migrate: $version was modified after being applied (db $existing, file $checksum)" >&2
      exit 1
    fi
    continue
  fi

  echo "migrate: applying $version"
  {
    cat "$f"
    printf '\nINSERT INTO schema_migrations (version, checksum) VALUES (:%s, :%s);\n' "'v'" "'c'"
  } | q --single-transaction -v v="$version" -v c="$checksum" -f -
  applied_count=$((applied_count + 1))
done

echo "migrate: done, $applied_count applied"
