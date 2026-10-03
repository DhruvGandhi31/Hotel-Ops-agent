# Decisions log

Format: `YYYY-MM-DD: decision — reason`. Append only; reversals get a new entry that references the old one.

2026-10-03: Pin n8n to `n8nio/n8n:2.41.6` — latest stable at P0; satisfies the ≥ 2.20.0 requirement for instance-level MCP (`N8N_MCP_ACCESS_ENABLED`).
2026-10-03: Pin Postgres to `postgres:17.11-alpine3.23` — 18.x changes the data directory layout/volume path; 17 is stable and supported by n8n, nothing here needs 18.
2026-10-03: One login role per database (`n8n` owns db `n8n`, `ops_app` owns db `ops`), created by `db/init/` on first start — n8n's Postgres credential for app data can't touch n8n internals, and vice versa.
2026-10-03: Migrations run by a one-shot `migrate` compose service (plain `psql` + `schema_migrations` table with sha256 checksums); n8n waits for it to exit 0 — no extra tool (Flyway/sqitch) for a handful of SQL files; edited-after-apply migrations abort loudly.
2026-10-03: Migration 001 contains only `audit_log` (append-only via triggers); domain tables land in the phase that first uses them (suppliers/POs/receiving in P1, invoices in P2, reconciliations in P3) — avoids locking in an invoice schema before the idempotency vs. duplicate-detection semantics are settled.
2026-10-03: Exported workflow files are named by slugified workflow name, and export strips `pinData`, `staticData` and volatile metadata (timestamps, version ids, sharing) — readable filenames, no execution payloads in git, and a re-export of unchanged workflows is byte-identical (enforced in CI).
2026-10-03: Workflow ids are preserved in exports — import updates in place, so `import.sh` is idempotent.
2026-10-03: gitleaks runs from the pinned `zricethezav/gitleaks:v8.30.1` image rather than gitleaks-action — same binary locally and in CI, no licence key.
2026-10-03: Host ports bind to 127.0.0.1; Postgres defaults to host port 5433 and Ollama to 11435 — dev machine already runs Postgres on 5432 and may run a native Ollama on 11434.
