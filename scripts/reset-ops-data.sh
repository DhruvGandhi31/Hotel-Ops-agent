#!/usr/bin/env bash
# Empty the ops database's application data so a different generated dataset can be loaded:
# suppliers, purchase orders, receipts, invoices, reconciliations. Keeps audit_log (append-only),
# schema_migrations and reconciliation_settings.
#
#   bash scripts/reset-ops-data.sh && python data-gen/generate.py --n 200 --seed 44 --out data-gen/out-gate3 \
#     && bash scripts/seed.sh data-gen/out-gate3/seed.sql
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."

docker compose exec -T postgres sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$OPS_DB_USER" -d ops -c "
  TRUNCATE approvals, reconciliation_lines, reconciliations, invoice_lines, invoices, invoice_files,
           receipt_lines, receipts, purchase_order_lines, purchase_orders, suppliers, seed_metadata
  RESTART IDENTITY"'
echo "reset-ops-data: application data cleared (audit_log and settings kept)"
