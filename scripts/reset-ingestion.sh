#!/usr/bin/env bash
# Forget every ingested invoice so an eval starts from a clean slate. audit_log is append-only and is kept.
# Master data (suppliers, POs, receipts) is untouched.
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/.."
docker compose exec -T postgres sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$OPS_DB_USER" -d ops -c "TRUNCATE approvals, reconciliation_lines, reconciliations, invoice_lines, invoices, invoice_files RESTART IDENTITY"'
echo "reset-ingestion: invoice and reconciliation tables cleared"
