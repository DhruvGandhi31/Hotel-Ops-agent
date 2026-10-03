# hotel-ops-agent

Back-office automation for small/mid hotels on self-hosted n8n: reconcile supplier invoices
against purchase orders and receiving records, flag discrepancies with reason codes, and route
everything through human approval.

**Status:** Phase 0 (infra + CI). Full write-up arrives in P6.

## Quickstart

Requires Docker with Compose v2 and bash (Git Bash works on Windows).

```bash
bash scripts/init-env.sh     # .env with generated secrets (or: cp .env.example .env)
docker compose up -d --wait  # postgres -> migrations -> n8n
bash scripts/import.sh       # load workflows/ into n8n
```

n8n: http://localhost:5678 · Postgres (host): `localhost:5433`, databases `n8n` and `ops`.

With local Ollama on the GPU: `docker compose --profile local-llm up -d`
(reach it from n8n at `http://ollama:11434`).

## Working on workflows

The n8n UI is an editor; `workflows/` is the source of truth.

```bash
bash scripts/export.sh   # after editing in the UI: export, strip pinData, normalise
```

`export.sh` replaces `workflows/` with exactly what's in n8n, so deletions and renames show up
in `git diff`. On a fresh instance, import before you export.

## Database migrations

Plain SQL in `db/migrations/NNN_name.sql`, applied in order by the `migrate` service on every
`docker compose up`. Never edit an applied migration; add a new one. Re-run manually with
`docker compose run --rm migrate`.

## Layout

```
docker-compose.yml   n8n + postgres + migrate (+ ollama profile)
db/init/             first-start role/database creation
db/migrations/       numbered SQL migrations for the ops database
workflows/           exported n8n workflows, one file per workflow
scripts/             init-env, import, export, migrate, check_workflows
docs/decisions.md    decisions log
```
