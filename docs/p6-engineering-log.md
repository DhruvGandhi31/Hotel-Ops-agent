# P6 engineering log

Write-up and observability, trimmed. Same rules as the earlier logs ([p4](p4-engineering-log.md)): what was done,
what went wrong, what is unverified. Decision: [decisions.md](decisions.md), 2026-10-09.

## 1. Scope

| Item in CLAUDE.md | Status | Why |
|---|---|---|
| README with diagram, results, failure analysis, "where no LLM" | **done** | the part a reader sees first |
| A simple dashboard | **done**: `evals/dashboard.py` writes a static HTML snapshot (`docs/dashboard.html`) | counts over `audit_log`, `reconciliations` and `approvals`; no workflow change, so no risk to the verified pipeline |
| `ops.llm_calls` (tokens, cost, latency per call) | **not built** | would rework the extraction workflow; local model, no per-token price. Not tested whether the chain node exposes token counts |
| P5 ops inbox triage | **not built** | user's choice: stretch, deadline |

## 2. Dashboard

- The page is rendered by a pure function (`render`) and the SQL is one query, so the rendering is tested without a database
  (`evals/tests/test_dashboard.py`). The escaping test was shown to fail with escaping removed (mutation check), per the standing rule.
- First run against the user's database (199 invoices from the P3 gate run, 3 approvals): it showed **21 workflow errors**. Read
  from `audit_log`, all on 2026-10-06: 13 name the mock model server (the outage tests), 5 are connection or DNS failures, and 3
  are others (a JSON parse error twice and a "wrong type" error, whose messages point at development-time bugs of that day; their
  causes were not re-investigated here). The cause of each connection or DNS error was not individually verified. Because
  `audit_log` survives data resets, the figure spans runs; a footnote on the page says so instead of hiding the number.
- The snapshot is static: regenerate it with the command in CLAUDE.md. Times are UTC.

## 3. Unverified

- The dashboard was generated from the user's database only; its numbers were not cross-checked against the evals' reports
  (they count different things: the dashboard counts rows now in the database, the reports score a labelled run).
- The README claims were copied from the committed reports and logs, not re-measured in this phase.

## 4. GitGuardian findings on the P4 commit (2026-10-09)

- **What**: the pull-request scan reported three "Generic Password" incidents on commit `4f40ad5`: `evals/tests/test_demo.py`,
  `scripts/setup-owner.sh`, `scripts/init-env.sh`. CI's own gitleaks run had passed.
- **Assessment**: from reading the code, not from GitGuardian's matched strings (not seen): a fake value `Pw1-x` in a test, and two
  places that generate a random owner password with a fixed `Ho1-` prefix (the prefix exists because n8n requires a digit and an
  upper-case letter). `.env` is in `.gitignore` and `git ls-files` does not list it. `.env.example` holds only a placeholder.
- **Change**: `# ggignore` markers on the three lines; in `init-env.sh` the value moved to its own line, because a comment inside the
  multi-line `sed` command would break its backslash continuation. `init-env.sh` re-run in a temporary directory: it still writes a
  36-character random `N8N_OWNER_PASSWORD`, no leftover variable name in the output. Lint and the demo tests pass.
- **Not done**: history is not rewritten (nothing was leaked) and the existing incidents must be closed as false positives in the
  GitGuardian dashboard; the markers only affect future scans. Whether the markers silence the exact matches is **unverified** until
  the next scan of a pull request.
