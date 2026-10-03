#!/usr/bin/env bash
# Load generated master data (suppliers, POs, receipts) into the ops database.
# Idempotent: existing rows are skipped. Refuses to mix data from a different seed.
# Invoices are not seeded; they arrive through the ingestion workflow (P2).
#
#   python data-gen/generate.py --n 200 --seed 42 && bash scripts/seed.sh
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

seed_file=${1:-data-gen/out/seed.sql}
if [[ ! -f "$seed_file" ]]; then
  echo "seed: $seed_file not found; run python data-gen/generate.py first" >&2
  exit 1
fi

psql_ops() { docker compose exec -T postgres sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$OPS_DB_USER" -d ops "$@"' psql "$@"; }

psql_ops --single-transaction -f - < "$seed_file"
psql_ops -tA -F ' ' -c "SELECT 'suppliers', count(*) FROM suppliers
  UNION ALL SELECT 'purchase_orders', count(*) FROM purchase_orders
  UNION ALL SELECT 'purchase_order_lines', count(*) FROM purchase_order_lines
  UNION ALL SELECT 'receipts', count(*) FROM receipts
  UNION ALL SELECT 'receipt_lines', count(*) FROM receipt_lines"
