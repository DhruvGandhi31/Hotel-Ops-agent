# evals

Measures how well the invoice ingestion pipeline turns PDFs into the structured fields in
[`schemas/invoice_extraction.json`](../schemas/invoice_extraction.json), by comparing it with the
ground truth that [`data-gen`](../data-gen/README.md) writes for every invoice. Two separate
questions, answered by separate tools:

| Question | Tool | Needs |
|---|---|---|
| How accurate is the extraction? | `run.py` | a real model (GPU), run by hand |
| Does the pipeline behave correctly (retry, `needs_review`, outage, duplicates, idempotency, reconciliation)? | `pipeline_check.py`, `run.py --mock` | no GPU: a mock model |
| How good are the reconciliation decisions (`flag` precision and recall per discrepancy type)? | `run.py --suite reconciliation` | rules only: nothing; the other two modes: a real model |

## Running it

```bash
python data-gen/generate.py --n 200 --seed 42                          # the gate dataset
python data-gen/generate.py --n 60 --seed 7 --out data-gen/out-dev     # the dev dataset

# THE GATE RUN: every file uploaded through the n8n webhook, as a demo or customer would
bash scripts/reset-ingestion.sh                  # clean slate (audit_log is append-only and kept)
set -a; . ./.env; set +a                         # INGEST_WEBHOOK_TOKEN
python evals/run.py --suite extraction --target n8n

# the extraction step alone against a local model (model selection, prompt tuning)
python evals/run.py --suite extraction --target ollama --model qwen3.5:9b --data data-gen/out-dev \
    --out evals/results/dev
```

Needs Ollama running with the model pulled (`ollama pull qwen3.5:9b`). Exit status is 0 when all
gate targets (and, for `--target n8n`, all ingestion checks) pass, 1 when any fails, 2 on bad
usage, 3 when a server failed (resume with `--resume`).

| Flag | Meaning |
|---|---|
| `--target` | `n8n` (through the webhook: the whole pipeline) or `ollama` (extraction step only) |
| `--tag` | suffix for the output files (`--tag v2` writes `extraction-n8n-v2.md`) |
| `--overwrite` | allow replacing an existing report or raw file; refused by default, because a gate report is evidence |
| `--model`, `--host` | Ollama tag and URL (target `ollama`) |
| `--webhook` | webhook URL (target `n8n`; default `$WEBHOOK`, else `http://127.0.0.1:5678/webhook/invoice-upload`) |
| `--data` | dataset directory (default `data-gen/out`) |
| `--limit N` | first N files only; prints the report instead of writing it |
| `--format-mode` | `schema` (decoding constrained to the JSON schema), `json` (Ollama's generic JSON mode, all that n8n's Ollama node offers), `none` (prompt only) |
| `--num-ctx` | context window, default 4096: a 9B model then fits fully on an 8 GB GPU; 6144 spills about 12% to CPU |
| `--resume` | continue a run that stopped; skips files already done |
| `--mock` | the model behind n8n is the mock: stamps the report and writes `extraction-n8n-mock.md` |
| `--out` | where reports go (default `evals/results`) |

If the model server drops mid-run the runner retries with backoff, then stops with exit 3 rather
than scoring the outage as wrong answers; `--resume` picks up where it left off.

## Protocol: dev set versus gate set

Prompt wording and model choice are tuned on the **dev set** (seed 7, 60 invoices). The **gate
set** (seed 42, 200 invoices, 196 scored) is run only for a final number, with the prompt and
model already fixed. Tuning on the set you report would inflate the result.

Dev reports live in `evals/results/dev/`. Only the gate report `evals/results/extraction-n8n.md`
counts toward the phase gate. Disclosure: five seed-42 invoices were used in a smoke run before
this split existed, and that run informed three prompt fixes (see the decisions log). Dev results
were also discarded and regenerated once after a prompt-embedding bug was found (also logged).

## What is scored

Exact re-sends are excluded from extraction scoring (identical bytes to an original, so they
would double-count) but are checked for ingestion behaviour. Everything else is scored,
including duplicates.

| Field | Rule |
|---|---|
| `invoice_number`, `po_number` | exact after collapsing whitespace; `po_number` null must be null, and a value must not appear where none is printed |
| `supplier_abn` | digits only, so spacing is forgiven |
| `supplier_name`, `bill_to_name`, `description` | whitespace collapsed and case ignored |
| `subtotal`, `gst`, `total`, line amounts | exact cents; a value that is not a plain two-decimal string counts as wrong |
| `quantity` | numeric equality (`2.42` equals `2.420`) |
| dates, `currency` | exact |

A **line item counts as correct only if description, quantity, unit price and line total all
match.** Lines are matched as a bag, so a missed line does not shift every later line into
error. Reported as recall (correct / true lines) and precision (correct / predicted lines); the
gate target applies to recall. Per-field line accuracy (unit, supplier code, GST flag, per-line
GST) is positional and only counted when the invoice has the right number of lines.

A `needs_review` file, or one the workflow failed on, scores every field wrong.

## Reconciliation suite (P3)

Scores the reconciler's decision for each invoice against the labels in the ground truth: the status
(`recommend_approve`, `flag`, `needs_review`) and the reason codes. The P3 gate numbers are precision
and recall for the `flag` class, overall and per discrepancy type, with 95% confidence intervals.
Conditions that must **not** flag (reworded lines, partial deliveries, price within tolerance) are
reported separately as false positives.

```bash
# 1. rules only: ground-truth extraction and ground-truth line matches, SQL only, no model, seconds
bash scripts/reset-ingestion.sh
python evals/run.py --suite reconciliation --expect-perfect

# 2. matcher: ground-truth extraction, the real model matches the reworded lines (through n8n)
bash scripts/reset-ingestion.sh
python evals/run.py --suite reconciliation --source truth --matcher n8n

# 3. end to end: the PDFs through the ingestion webhook, which reconciles (THE NUMBER THAT COUNTS)
bash scripts/reset-ingestion.sh
python evals/run.py --suite reconciliation --source pdf --data data-gen/out-gate3
```

Modes 2 and 3 need `INGEST_WEBHOOK_TOKEN` in the environment (`set -a; . ./.env; set +a`). The ops
database must hold the master data of the dataset being scored and no invoices (the quantity rule is
cumulative per PO, so leftovers would change the answer); the suite refuses to run otherwise and prints
the commands to fix it (`bash scripts/reset-ops-data.sh && bash scripts/seed.sh <dataset>/seed.sql`).

| Flag | Meaning |
|---|---|
| `--source` | `truth` (ground-truth extraction written straight to the database) or `pdf` (the PDFs through the webhook) |
| `--matcher` | with `--source truth`: `oracle` (the true line matches, rules only) or `n8n` (the real model) |
| `--reconcile-webhook` | URL of `/webhook/reconcile` (mode 2; default `$RECONCILE_WEBHOOK`, else `http://127.0.0.1:5678/webhook/reconcile`) |
| `--expect-perfect` | exit 1 unless every invoice is exactly right (used in CI, with the mock) |
| `--mock` | the model behind n8n is the mock: the report is stamped and written to a `-mock` file |

**What a score means.** The generator that injects the discrepancies and the reconciler encode the same
rules, so the rules-only score is expected to be perfect and proves implementation, not suitability.
The meaningful comparison is between modes: the difference between mode 1 and mode 2 is what the model's
line matching costs; between mode 2 and mode 3, what extraction costs. Every report says this at the top.
The same dev/gate discipline as extraction applies: thresholds are tuned on a dev dataset, and the final
number is taken once on a dataset that was never examined.

Data the synthetic set never produces (unknown supplier, a PO belonging to another supplier, arithmetic
errors on a line or the totals, quantity above the PO, no receiving record) is covered by
`db/tests/reconcile.sql`, not by this suite.

## Ingestion behaviour (target `n8n`)

Scored separately from accuracy, against the ground-truth labels:

| Expected | Correct means |
|---|---|
| `original` | stored as new, `duplicate_of` empty |
| `duplicate` (same invoice, different file) | stored, `duplicate_of` = the original's invoice id |
| `noop` (identical file) | status `noop` and the original's invoice id; nothing new stored |

A file whose extraction failed cannot be judged on this and is counted apart, as is a copy of such
a file. A workflow error on any file counts as wrong. The runner refuses to start if the first
file is already known (dirty state) and tells you to run `scripts/reset-ingestion.sh`.

## Targets

| Metric | Target |
|---|---|
| `total` | ≥ 98% |
| `invoice_number` | ≥ 98% |
| `po_number` | ≥ 98% |
| line items | ≥ 90% |

Each is reported with a 95% Wilson interval. With 196 scored invoices a 98% target allows at most
3 misses, so a result near the threshold is weak evidence either way.

## Reading a report

- **Gate targets**: result, interval, pass or fail.
- **Pipeline health**: first-attempt validity, retry rate, `needs_review` and workflow-error
  counts, latency, and (target `ollama`) tokens and how close any invoice came to the context
  limit, since Ollama truncates silently.
- **Ingestion behaviour** (target `n8n`).
- **Header fields** by template: shows whether one layout drags the average down.
- **Line fields**: where the model invents or drops values even when the gate fields pass.
- **Failures**: the first 25 invoices with what was expected versus what came back.

Raw per-invoice output is written to `evals/results/raw/` and `evals/results/dev/raw/`, both
gitignored.

## Pipeline behaviour without a GPU

`mock_ollama.py` stands in for Ollama: it finds the invoice in the prompt by invoice number and
ABN and replies with the perfect extraction, so any difference downstream is the pipeline's
doing. It has failure modes (`garbage_once`, `garbage_always`, `server_error`) switched at run
time. It says **nothing** about model accuracy, so reports made with it are stamped
"MOCK MODEL", written to a separate file, and never count toward the gate.

```bash
python evals/mock_ollama.py --data data-gen/out --port 11500 &
OLLAMA_BASE_URL=http://host.docker.internal:11500 bash scripts/setup-credentials.sh
python evals/pipeline_check.py                              # behaviour checks (resets ingestion)
python evals/run.py --suite extraction --target n8n --mock --limit 100 --out /tmp/eval
bash scripts/setup-credentials.sh                           # point n8n back at the real Ollama
```

`pipeline_check.py` verifies, against a live stack: a normal ingest and its audit row; a repeat
upload is a no-op that never calls the model; one bad reply is rescued by the retry; two bad
replies end in `needs_review` and are not silently retried; **a model-server outage returns a
5xx, records nothing, is audited, and the same file succeeds once the server is back**; a PDF
with no text layer goes to `needs_review` without a model call; a wrong token, non-PDF and
missing file are refused and recorded nowhere. CI runs all of this.

## How extraction runs

`hotel_evals/extract.py` mirrors the workflow's contract, and `tests/test_workflow_code.py` runs
the workflow's real Code-node JavaScript under Node to prove the two agree:

1. System prompt = [`prompts/invoice_extraction.md`](../prompts/invoice_extraction.md) with the
   schema (descriptions removed) substituted for `{{SCHEMA}}`; mirrored in
   [`docs/prompts.md`](../docs/prompts.md), with a test keeping them identical.
2. The text is the PDF text layer from `services/pdf-text` (pypdf), the same function the workflow's
   service runs (`run.py` imports it, and a test enforces that). This matters: n8n's own `Extract From
   File` space-joins table cells and cost about 9 points of line-item accuracy on identical invoices.
   Output is validated against the schema, plus real calendar dates, the ABN checksum and quantity
   precision.
3. On failure, one retry: a single message with the invoice text, the failed output and the
   error list ([`prompts/invoice_extraction_retry.md`](../prompts/invoice_extraction_retry.md)).
   A second failure returns `needs_review` with no output.

The eval harness uses `temperature 0`, `seed 42` and `think: false` (reasoning models only add
latency to a transcription task); the n8n Ollama node uses `temperature 0` and `think: false`.

## Layout

```
evals/
  run.py                  eval CLI (targets: n8n, ollama)
  pipeline_check.py       pipeline behaviour checks against a live stack + mock model
  mock_ollama.py          entry point for the mock model
  results/README.md       index of the gate runs and how they relate
  hotel_evals/
    extract.py            prompt build, validation, Ollama client, retry, transport errors
    scoring.py            field and line matching, aggregation
    ingestion.py          did ingestion do the right thing with each file
    n8n_client.py         multipart upload to the webhook
    mock_ollama.py        the mock model
    report.py             markdown report, Wilson intervals
    oracle.py             ground truth rendered as a perfect extraction
  js/run_code_node.js     runs a workflow Code node's JS under Node (for tests)
  tests/                  scorer, extractor contract, workflow-code parity, mock, transport
  results/                committed gate report (and dev/ reports)
```

## Limits

- Synthetic invoices, four layouts. Good for regression and comparison, not a promise about real
  suppliers.
- Token counts and context use are not visible through the workflow (`ops.llm_calls` arrives in
  P6); the `ollama` target reports them.
