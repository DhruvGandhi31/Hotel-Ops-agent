# P3 engineering log

Reconciliation: decide `recommend_approve`, `flag` (with reason codes) or `needs_review` for each
ingested invoice. Same rules as [p2-engineering-log.md](p2-engineering-log.md): this page records the
history (every run, bug and fix, mistake and its corrective decision, what is unverified) as it
happens, while [architecture.md](architecture.md) records the design.

Contents: [1 Timeline](#1-timeline) · [2 Every run](#2-every-run) ·
[3 Bugs and fixes](#3-bugs-and-fixes) · [4 Mistakes during the session](#4-mistakes-during-the-session) ·
[5 Error handling](#5-error-handling) · [6 Unverified and open](#6-unverified-and-open)

---

## 1. Timeline

| Step | What happened | Outcome |
|---|---|---|
| Start | User accepted the plan and its four recommendations (cumulative quantity, unclear cases to `needs_review`, matcher threshold 0.85 tuned on dev, price below PO not flagged) | logged 2026-10-07 |
| Branch | Local `main` did not contain the P2 merge (the merge happened on GitHub and local `main` was not pulled; `git pull` is not an allowed command), so the P3 branch was cut from `p2-extraction`, whose content equals what was merged | see M1 |
| Sizing | Measured how many invoice lines each matching tier handles, on two datasets | see 2.1 |
| Rules engine | Migration 006 (`reconciliation_settings`, `reconciliations`, `reconciliation_lines`, `deterministic_line_matches`, `reconcile_candidates`, `reconcile_invoice`) and `db/tests/reconcile.sql` | tests found a real logic error (B1) |
| Eval | Scoring (precision and recall per code, the flag class, condition false positives, matcher accuracy), a loader that runs ground truth through the database, a report, `--expect-perfect` for CI | rules-only on seed 42: see 2.2 |

| Workflows | `Reconcile Invoice` (candidates, matcher call, validation with one retry, `reconcile_invoice`), `Reconcile Invoice API` (`POST /webhook/reconcile`), and a reconciliation hand-off at the end of `Ingest Invoice` (continue-on-fail) | built with a scratch assembler, imported, published; found B5 on the first live call |
| Matcher path | Exercised every matcher outcome through n8n with the **mock** model (not the real one) | all pass: see 2.3 |
| Tests | Parity tests for the matcher's Code nodes against the Python reference, tests for the reference and for the mock's matcher answers, prompts mirrored in `docs/prompts.md` | 200 tests pass |
| Export | `scripts/export.sh` run for the four workflows | byte-stable on a second export; only n8n metadata added (see 2.4) |
| CI | Three reconciliation eval steps added to the smoke job (rules only; through n8n with the mock matcher; end to end from PDFs with the mock), each with `--expect-perfect` | replayed as a whole in the local CI simulation (clean clone-equivalent, isolated compose project, mock model): every step passed, including the three reconciliation modes and the byte-for-byte export round trip. The real GitHub run is still to be seen by the user |

| Gate | One end-to-end run on a fresh dataset (seed 44), then the two diagnostic modes | flag recall 100%, precision 98.6%; five invoices differ, all traced to extraction: see 2.6 |

*(Further rows are added as the work proceeds.)*

---

## 2. Every run

### 2.1 Line-matching tier sizing (a data measurement, before any code)

Ground-truth invoices, exact re-sends excluded, `no_po` = unknown or missing PO (nothing to match to).

| Dataset | Lines | Printed SKU matches a PO line | Normalised description matches | Needs the LLM | No PO |
|---|---:|---:|---:|---:|---:|
| seed 42 (196 invoices) | 878 | 572 (65.1%) | 202 (23.0%) | **43 (4.9%)**, in 24 invoices | 61 (6.9%) |
| seed 43 (196 invoices) | 899 | 566 (63.0%) | 220 (24.5%) | **44 (4.9%)**, in 26 invoices | 69 (7.7%) |

All lines that need the LLM are the deliberately reworded ones (`description_mismatch`); none are other
kinds. Three of the twelve suppliers print no item codes at all, but their descriptions match the PO
exactly, so the normalised-description tier handles them without the model.

### 2.2 Rules-only reconciliation, seed 42 (ground-truth extraction and ground-truth line matches; SQL only)

196 invoices reconciled (exact re-sends are ingestion no-ops). Report from the run (not yet committed as a
result; the dataset used for the final number will be a fresh seed).

| Measure | Result |
|---|---|
| `flag` class | precision 100% (71/71), recall 100% (71/71), 0 truly flagged invoices approved, 125/125 clean invoices approved, 0 `needs_review` |
| Per code (expected = predicted = TP) | price_variance 20, short_delivery 16, duplicate_invoice 12, unknown_po 8, missing_po 6, gst_miscalculated 14; zero false positives or negatives |
| Conditions that must not flag | description_mismatch 19/19 approved, partial_delivery 10/10, price_within_tolerance 9/9 |
| Exact reason-code set | 196/196; 5/5 on invoices with two or more true reasons |
| Line matching | 817/817 lines matched to the right PO line: 572 by SKU, 202 by description, 43 by the supplied (oracle) match |

A perfect result is **expected** here, because the generator and the reconciler encode the same rules
(see decisions). It shows the rules are implemented as specified. It says nothing yet about the model.

**Proof the eval can fail** (mutating a setting, then restoring it):

| Mutation | Effect on the eval |
|---|---|
| `price_tolerance_pct` 2.0 to 5 | no change (the injected overcharges are 5 to 25%, so a 5% tolerance still catches all of them): **a weak mutation, see M4** |
| `price_tolerance_pct` 2.0 to 12 | `price_variance` recall fell to 80.0% (16/20), flag recall to 94.4% (67/71), 4 truly flagged invoices were approved |
| SQL test run with the tolerance at 3 | the "one cent past 2% flags" assertion failed |
| SQL test run with the match threshold at 0.9 | the "exactly at the threshold is accepted" assertion failed |

### 2.3 Pipeline runs with the mock model (plumbing only: none of these is an accuracy result)

The mock answers from the ground-truth labels, so these runs test that the workflows, the database and the
eval agree with each other. Reports are stamped `MOCK` and written to `-mock` files.

| Run | Result |
|---|---|
| `--source truth --matcher n8n --mock` (seed 42, 196 invoices, 24 matcher calls) | flag precision and recall 100%, 0 mismatching invoices, 0 `needs_review`, ~32 s |
| `--source pdf --mock` (the PDFs through the ingestion webhook, which reconciles) | 0 mismatching invoices |
| `evals/pipeline_check.py`, reconciliation section | passes: clean invoice reconciled in the same response without the matcher; reworded line matched by the model (`match_method` `llm`, confidence recorded); bad JSON once, the retry rescues it (`attempts` 2); bad JSON on every matcher call, invoice stored and `needs_review` with `unmatched_line`; confidence 0.5, `low_confidence_match`; "not on the PO", `unmatched_line`; model server error during matching, invoice still ingested, reconciliation `pending` with the error, nothing stored, re-running `/reconcile` completes it; `/reconcile` is idempotent (one row), 400 without or with a non-integer id, 404 for a missing invoice, 403 for a wrong token |

Not exercised through n8n: the mock's `matcher_wrong` mode (a confident wrong match). It is covered by the
mock's own tests only. By design nothing in the pipeline can detect a confident wrong match; only the eval
scoring (matcher accuracy by method) can.

### 2.4 Workflow export

`scripts/export.sh` on the four workflows: a second export is byte-identical to the first. Compared with the
assembler's output, every node's parameters and every connection are identical; the export only adds the
top-level `description`, `meta` and `nodeGroups` fields. No `pinData`.

### 2.5 Matcher dev run, real model (seed 42 as the dev set)

`run.py --suite reconciliation --source truth --matcher n8n`, `qwen3.5:9b` through n8n, ground-truth
extraction, so only the line matching is the model's. 196 invoices, 24 matcher calls (one per invoice that
has lines needing it), 70 s for the whole set. Report:
[`evals/results/dev/reconciliation-matcher-matcher-dev1.md`](../evals/results/dev/reconciliation-matcher-matcher-dev1.md).

| Measure | Result |
|---|---|
| Lines matched by the model | 43 of 43 correct (572 by SKU and 202 by description were also all correct) |
| Confidence of the model's matches | min 0.90, mean 0.948 (all above the 0.85 threshold, so it never discarded one) |
| `flag` class | precision 100% (71/71), recall 100% (71/71), 0 `needs_review` |

What this does and does not show: the model handles the reworded lines the generator produces, and the
threshold never mattered. It cannot show where a *wrong* answer's confidence would sit (there were none),
so the 0.85 threshold is untested in the one situation it exists for. 43 of 43 has a 95% lower bound of
about 92%. The generator's rewording may be gentler than a real supplier's. The decision was to leave the
threshold at 0.85 rather than invent a value (see decisions).

### 2.6 THE GATE RUN: end to end from the PDFs, seed 44 (fresh, never examined), real `qwen3.5:9b`

Protocol (decided before the run, see decisions): one run, rules and thresholds as they stood (2%, half a
cent per line, 0.85), no tuning afterwards; the other two modes were run on the same data **afterwards** as
diagnostics. 200 files, 196 reconciled (4 exact re-sends are ingestion no-ops), about 14 s per invoice.
Reports: [`reconciliation-e2e-seed44.md`](../evals/results/reconciliation-e2e-seed44.md) (the gate),
[`reconciliation-matcher-seed44.md`](../evals/results/reconciliation-matcher-seed44.md) and
[`reconciliation-rules-seed44.md`](../evals/results/reconciliation-rules-seed44.md) (diagnostics).

| Measure (196 invoices) | Rules only | Matcher (real model matches lines) | **End to end (the gate)** |
|---|---:|---:|---:|
| `flag` precision | 100% (71/71) | 100% (71/71) | **98.6% (71/72)** |
| `flag` recall | 100% (71/71) | 100% (71/71) | **100% (71/71)**, 95% CI 94.9 to 100% |
| Truly flagged but approved | 0 | 0 | **0** |
| Exact reason-code set correct | 195/196 | 195/196 | **192/196** |
| Sent to `needs_review` | 0 | 0 | 1 |
| Lines matched to the right PO line | (oracle) | 819/819; the model's 46 of 46 | 807/819 |

Per discrepancy type, end to end: recall 100% for all six (`price_variance` 20/20, `short_delivery` 16/16,
`duplicate_invoice` 12/12, `unknown_po` 8/8, `missing_po` 6/6, `gst_miscalculated` 14/14); precision
`duplicate_invoice`, `unknown_po`, `missing_po` 100%, `price_variance` 90.9% (20/22), `short_delivery`
88.9% (16/18), `gst_miscalculated` 87.5% (14/16). Conditions that must not flag: `partial_delivery` 7/7 and
`price_within_tolerance` 6/6 approved; `description_mismatch` 15 of 17 approved, 1 wrongly flagged
(`inv_0038`), 1 sent to `needs_review` (`inv_0101`).

**Why the five invoices differ, each checked against what was extracted** (stored extraction compared with
the ground truth, line by line, with `diagnose_gate.py` from the session scratch folder; method below):

| Invoice | Result | Cause (tested) |
|---|---|---|
| `inv_0038` (clean, reworded lines) | falsely `flag`: `price_variance`, `short_delivery` | **The model shifted the printed item codes up one row**: line 1 has no code on the invoice, the model gave it the code of line 2, line 2 the code of line 3, and so on. The reconciler's SKU tier trusts a printed code, so lines 1 to 3 were matched **with confidence** to the wrong PO lines (prices and quantities then differ). Line 4 got a code already used, so it stayed unmatched (`unmatched_line`) |
| `inv_0059` (a duplicate of `inv_0038`) | right flag `duplicate_invoice`, plus the same two false codes | the same shifted codes (same layout, same misreading) |
| `inv_0101` (clean) | `needs_review`, `totals_arithmetic` | **The model dropped a line** (5 lines on the invoice, 4 extracted) and renumbered the rest; the next three lines then SKU-matched to the wrong PO lines without issues, but the lines no longer sum to the subtotal and the **totals check caught it** and sent it to a human. The system did the right thing for the wrong reason |
| `inv_0009` (`unknown_po`) | extra `gst_miscalculated` | the model read the one line's GST marker as taxable (the truth: GST-free). With no PO there is nothing to judge GST against, so the reconciler trusts the invoice's markers (basis `invoice`): 10% of the line expected, 0 stated |
| `inv_0033` (`missing_po`) | extra `gst_miscalculated` | the model marked 6 of 9 taxable lines as GST-free; the same no-PO basis |

All five are **extraction errors in fields the P2 gate did not score** (the item code, the per-line GST
marker, a dropped line), met by a reconciler that trusts what extraction gives it. The nine wrong SKU matches
are 3 each in `inv_0038`, `inv_0059` and `inv_0101` (the three invoices above). The three "not matched"
lines are `inv_0038` line 4, `inv_0059` line 4, and the line `inv_0101` lost.

**What went right:** recall is 100% and **no truly flagged invoice was approved**; the model's own line
matching was correct on all 46 lines (89 of 89 with the dev run); the totals check turned one extraction
fault into a human review.

**The one rules-only mismatch, `inv_0162`** (a label the reconciler cannot meet): labelled
`missing_po` + `gst_miscalculated` (variant `gst_on_gst_free`: GST charged on a line that is GST-free *on the
PO*), reconciled as `missing_po` only. The invoice carries no PO number, so the reconciler judges GST from the
invoice's own markers; the invoice marks line 2 taxable and the stated GST is exactly 10% of it, so there is
nothing to flag from the page. The label was derived from the true PO, which the page does not show. The
end-to-end run happened to match the label on this invoice; **I did not look at why** (its data was reset for
the diagnostic runs), so whether that was a marker misread is untested. This is a gap between what the
label assumes and what the invoice reveals, not a rules bug: seeds 42 and 43 never produced the combination.

**Nothing was changed after seeing these results** (protocol). The follow-ups are proposals for the user
(see the end of this section and decisions), and any change needs a fresh dataset to be measured honestly.

Proposals, none applied:

1. *Do not trust a printed item code blindly.* When a SKU match disagrees with the line on description,
   unit price or quantity (a cheap deterministic plausibility check, not an LLM), send the line to
   the existing model matcher or to `needs_review` instead of confidently matching. Would have turned
   `inv_0038` and `inv_0059` into `needs_review`, never a false `flag`.
2. *No PO, no GST verdict from markers the model reads at about 90%.* For invoices without a resolvable PO,
   either skip the GST check and say so, or send a disagreement to `needs_review`.
3. *Score the fields reconciliation depends on* (item code, per-line GST marker, line count) in the
   extraction eval, so the extraction gate covers what P3 needs.

---

## 3. Bugs and fixes

| ID | Sev | What was wrong | Cause | How found | Fix | Guard |
|---|---|---|---|---|---|---|
| B1 | **High** | A duplicate invoice was also flagged `short_delivery` and `quantity_exceeds_po` | the cumulative quantity rule counted the *original's* billing against its own copy, so the copy looked like billing twice what arrived | the SQL test "the copy is flagged as a duplicate and nothing else" | a copy is judged as the original was: the original is excluded from "billed before" (other copies already are) | `db/tests/reconcile.sql` duplicate section |
| B2 | Med | `reconcile_invoice` failed on its first call: `Array value must start with "{"` | `codes := codes \|\| 'duplicate_invoice'`: Postgres reads a bare string literal as an array literal | first run of the SQL tests | `array_append(codes, '...')` for every append | all reason codes exercised in the tests |
| B3 | Med | The P2 test `ingest.sql` failed on this machine (and would on any used database) | it asserted exactly 2 `invoice.needs_review` audit rows in total, but `audit_log` is append-only and earlier runs had left rows | first run of all SQL tests on the used database | counts the delta from the start of the test | passes on a fresh and on a used database |
| B4 | Low | `scripts/reset-ingestion.sh` failed: "cannot truncate a table referenced in a foreign key constraint" | the script predates `reconciliation_lines` | first eval run | it truncates the reconciliation tables too | used by every eval run |
| B5 | **High** | The first live call of the chained workflow failed in `Invoice Exists?`: "Wrong type: '1' is a string but was expecting a number" | the Postgres node returns `bigint` as a string (known from P2) and the IF node compared it as a number | first live `POST /webhook/reconcile` | the condition is a string `notEmpty` test on the id (the assembler's `cond()` helper was extended to support it) | pipeline check: reconcile of an existing and of a missing invoice (200 and 404) |
| B6 | Med | Reconciliation matcher: with the invoice already stored, a model-server failure during matching had no defined result | the hand-off had only the success path | designing the failure checks | the hand-off is continue-on-fail: ingestion reports `reconciliation: {status: "pending", error}`, nothing is stored for it, `POST /webhook/reconcile` completes it later | pipeline check "the matcher's model call fails after the invoice was stored" |

---

## 4. Mistakes during the session

| # | Mistake | Consequence | What changed |
|---|---|---|---|
| M1 | Assumed "committed and merged" meant local `main` had the merge | local `main` lacked all of P2, and switching to it removed the P2 files from the working tree (they were safe on `p2-extraction`) | checked with `git diff main p2-extraction --stat` before building anything; cut the P3 branch from `p2-extraction`; asked the user to `git pull` `main` when convenient |
| M2 | Wrote the SQL fixtures with shared file hashes, so `ingest_invoice` treated later invoices as no-ops, and let invoices accumulate against the same PO lines, so the cumulative rule (working correctly) made unrelated scenarios interfere | the first test draft would have failed for reasons unrelated to the rules | caught by reading the test before running it; unique hashes from a sequence, a `reset()` per independent scenario |
| M3 | Converted 30 test statements with one regular expression, leaving an extra closing parenthesis on each | the converted file did not parse | stopped patching by regex; rewrote the file once, cleanly |
| M4 | First mutation test of the eval used a 5% tolerance, which the injected overcharges (5 to 25%) mostly still exceed | it looked as though the eval could not detect the change | recognised the mutation was too weak, not the eval; used 12%, which it detects |
| M5 | Named test variables `id`, which collided with column names inside embedded queries | the test aborted with "column reference is ambiguous" | renamed `v_id`, `v_id2` |
| M6 | Patched `reconcile_eval.py` with a script whose search string was computed from the file and came out **empty**; `str.replace("", x)` inserts `x` between every character | the file was reduced to garbage (ruff reported dozens of syntax errors); a rewrite from my own source in the session restored it | every scripted replacement now asserts `count == 1` on a non-empty target before replacing; a changed file is re-read before it is rewritten |
| M7 | The first draft of the reconciliation pipeline checks (a) picked invoices that the earlier sections of the same script had already ingested (a re-upload is a no-op, so no reconciliation came back), (b) counted audit rows by `entity_id`, though `audit_log` is append-only and invoice ids restart at 1 after each reset, and (c) used the mock's `garbage_always`, which corrupts extraction too, so the matcher was never reached | three false failures; none was a system fault | (a) the reconciliation section excludes the files already used; (b) it counts deltas; (c) a matcher-only `matcher_garbage_always` mode was added to the mock |
| M8 | Wrote the gate-diagnosis script against guessed table and column names (`invoice_files.id`) | the first run failed with a column error; no data was affected | read the real columns from `information_schema` before the second attempt, and do that first in any query script |
| M9 | Left `# fmt: skip` comments on long lines in new tests to avoid wrapping them | they hid lint and would have kept the code unformatted | removed; the formatter owns layout |

---

## 5. Error handling

Principle (same as P2): a fault in the **model's output** is the model's problem and ends in a review state;
a fault in **infrastructure** fails loudly and loses nothing. Every row below was exercised in
`evals/pipeline_check.py` with the mock model unless noted.

| Fault | Behaviour |
|---|---|
| Matcher returns invalid JSON or an invalid answer once | one retry with the validation errors appended; if it validates, the result is used (`matcher.attempts` 2) |
| Matcher invalid on both attempts | no match is used; the lines stay unmatched, so the invoice is `needs_review` with `unmatched_line`, never a guess; the invoice itself is stored |
| Matcher confidence below the threshold (0.85) | the match is discarded and recorded as `low_confidence_match`; the invoice goes to `needs_review` |
| Matcher says the line is not on the PO | the line stays unmatched: `needs_review` with `unmatched_line` |
| Matcher gives a confident wrong match | **not detectable at run time**; measured only by the eval's matcher accuracy (open item) |
| Model server down during matching | the reconcile execution fails and is audited; after an ingest the invoice is kept and the response says `reconciliation: pending`; `POST /webhook/reconcile` completes it |
| `/reconcile` with no or non-integer id | 400 |
| `/reconcile` for an unknown invoice | 404 |
| `/reconcile` with a wrong token | 403 |
| `/reconcile` run twice on the same invoice | same result, one `reconciliations` row (an audit row is written per run, by design) |
| Schema keyword the Code-node validator does not implement | the node throws, rather than silently skipping the check (tested) |

---

## 6. Unverified and open

- **The matcher's confidence threshold (0.85) is untested where it matters.** The real model matched 89 of 89
  reworded lines correctly (dev and gate sets), so no wrong answer was available to show where a wrong
  answer's confidence sits.
- **One gate run.** One seed and 196 invoices: 71 truly flagged invoices, so recall's 95% interval is
  94.9 to 100%. Per-type counts are small (6 to 20).
- **Why `inv_0162` matched its label in the end-to-end run** was not examined (see 2.6).
- **The proposals at the end of 2.6 are unmeasured.** Nothing was changed after the gate run.
- **The labels and the reconciler share rules**, so a perfect rules-only score proves the implementation
  matches the specification, not that the rules suit a real hotel.
- **Cases the synthetic data never produces** (unknown supplier, PO of another supplier, line or totals
  arithmetic errors, quantity above the PO, no receiving record) are tested in SQL but are not part of the
  gate numbers.
- **GST basis `invoice`** (no PO line to judge the GST status against) trusts the invoice's own markers.
- CI has been replayed locally, not on GitHub: the first real run happens when the user pushes.
- Wording of schema errors differs between the Python reference (`jsonschema`) and the JavaScript
  interpreter. The verdicts agree (tested on ~100 generated outputs); the retry prompt shows the JavaScript
  wording, which is what the model sees in production.
