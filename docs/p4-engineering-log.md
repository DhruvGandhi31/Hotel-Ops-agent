# P4 engineering log

Human-in-the-loop: every reconciled invoice gets an approval request, a person approves or rejects it,
and the decision, approver and timestamp land in `audit_log`. Same rules as the earlier logs
([p2](p2-engineering-log.md), [p3](p3-engineering-log.md)): this page records the history as it happens
(every spike and run, bug and fix, mistake and its corrective decision, what is unverified), while
[architecture.md](architecture.md) records the design.

Contents: [1 Timeline](#1-timeline) · [2 The form spike](#2-the-form-spike) ·
[3 Bugs and fixes](#3-bugs-and-fixes) · [4 Mistakes during the session](#4-mistakes-during-the-session) ·
[5 Error handling](#5-error-handling) · [6 Unverified and open](#6-unverified-and-open) · [7 Demo runs](#7-demo-runs) · [8 The first real-browser run](#8-the-first-real-browser-run-by-the-user-2026-10-09)

---

## 1. Timeline

| Step | What happened | Outcome |
|---|---|---|
| Start | User merged P3 into `main` and said to proceed; branch `p4-approval` cut from `main` | gate acceptance logged 2026-10-09 |
| Plan | Rundown given with defaults; the user chose the channel: **n8n Form plus a pending-approvals page** | logged |
| Spike | Tested the Form Trigger and Form node of n8n 2.41.6 in a throwaway instance (own compose project and ports, never the user's stack) before building on them | see section 2 |

| Database | Migration `007_approvals.sql` (`approvals`, `request_approval`, `record_approval_decision`, `pending_approvals`, `approval_detail`, `invoice_audit_trail`, `approval_trim`) and `db/tests/approvals.sql` | the tests found two real defects before anything else ran: B1, B2 |
| Workflows | `Approval Form` (Form Trigger with n8n sign-in, list page, decision pages, result pages) and a `Request Approval` step at the end of `Reconcile Invoice` | live-tested on a throwaway instance with the mock model: see 2.2 |
| Client and checks | `evals/hotel_evals/approval_client.py` (a scripted browser), `pipeline_check.py` approvals section, `evals/tests/test_approval_code.py`, `evals/demo.py` | all pass; mutation checks on both the SQL and the JavaScript tests found one vacuous test (M4) |
| Cleanup of waiting runs | found that scripted sessions left runs `waiting` forever; fixed in the client and bounded in the workflow | see B5 |

| CI replay | The whole CI smoke job replayed from a clean clone-equivalent (own compose project, ports and address): three runs | run 1 failed at the owner step (B4, the real cause), run 2 at the form (B7), run 3 **passed every step**: owner created then recognised, pipeline checks including approvals, the three reconciliation modes perfect with the mock, export byte-identical |
| Demo | `evals/demo.py` run end to end with the **real model** (`qwen3.5:9b`) on the isolated copy, a flagged and a clean invoice | both complete; see section 7 |

| Real browser | The user ran `setup-owner.sh` and the demo on their own stack and went through the form in a browser (screenshots in `docs/images/`) | worked end to end; five things to fix, none in the rules: section 8 |

*(Further rows are added as the work proceeds.)*

---

## 2. The form spike

Why: the plan depended on four things nobody had tested here: a hidden form field prefilled from the URL,
dynamic content on a second page, a way to know *who* approved, and being able to test all of it without a
browser. Findings, each observed on the throwaway instance (n8n 2.41.6):

| Question | Finding |
|---|---|
| Where is the form served? | `/form/<webhookId>` of the Form Trigger node. For Form Trigger versions 2.2 and up the `path` parameter is hidden and ignored, so **the node's `webhookId` is the URL** and must be pinned in the workflow JSON. A first attempt with `path: spike-approve` gave a 404 ("GET spike-approve is not registered") |
| Prefill from the URL | works: `?approval_id=5` fills a `hiddenField` (`value="5"` in the rendered HTML), and the query survives the login redirects |
| Dynamic second page | works: a Form node page with a "Custom HTML" field built from an expression showed `Approval 5 for approver@example.com` |
| Who is the approver? | Form Trigger authentication **`n8nUserAuth`** (version 2.6): the submitter logs in with an n8n account and the trigger's output carries `json.user` (`id`, `email`, `firstName`, `lastName`). That is an *authenticated* identity, unlike a typed name or a shared basic-auth password. It needs an owner (or other user) account in n8n; the user's instance has none yet (`showSetupOnFirstLoad: true`), so a setup script is part of P4 |
| Is it scriptable without a browser? | yes, with a client that: logs in (`POST /rest/login`), opens the form (an OAuth-style redirect to `/oauth/consent`), calls `GET /rest/consent/details` and `POST /rest/consent/approve {"approved": true}`, follows the returned redirect to the form HTML, reads the page's `authToken` and sends it as the `x-auth-token` header with a multipart `POST`, then fetches the `formWaitingUrl` it returns for the next page |
| HTML sanitisation | n8n strips `<script>`, `onerror=` and `javascript:` links from an HTML field and keeps tables and ordinary links. Our code still escapes all database text, so the page shows it literally instead of relying on that |
| Links on the waiting page | a relative link such as `?approval_id=7` resolves against `/form-waiting/…` and is wrong; links must be absolute paths (`/form/<webhookId>?approval_id=7`) |
| Abandoned sessions | each opened session is a waiting execution until submitted. The Form node has a top-level `limitWaitTime` parameter (`limitType`, `resumeAmount`, `resumeUnit`); my first spike put it under `options`, where it does nothing |

### Quirks of the scripted client (all would be invisible in a browser, and are recorded so they are not rediscovered)

| Symptom | Cause (tested) | Handling |
|---|---|---|
| `GET /rest/consent/details` returned 401 from Python but worked from curl | n8n marks its cookies `Secure`; Python's cookie jar will not send those over plain `http://`, curl and browsers do for localhost | the client clears the `Secure` flag after every response (test instance on localhost only) |
| Ten logins in a row returned 429 "Too many requests" | n8n rate-limits `/rest/login` | log in once per session and reuse it |
| The first `GET` of the next page after a submit hangs (full timeout), the second answers in 0.1 s | **not understood.** Not the `Accept` header (a 2x2 test of Accept on the POST and the GET gave inconsistent results), not `Sec-Fetch-*` (0 of 5 first-try successes), not `Referer` alone (2 of 5). 9 of 10 first requests hung in a controlled loop | the client uses a short timeout and retries; in the one run where the first request worked it was the first of its session. **Unverified whether a real browser sees this** |

### What a finished session leaves behind (found after the workflow was built)

Every opened form session is an n8n execution. A `waiting` one holds a row in n8n's database until it is
resumed or its time limit passes. Observed on the integration copy:

| Session | What happens to its execution |
|---|---|
| A question page (list, decision) left unsubmitted | waits until the page's own limit (30 minutes in the workflow), then **ends as `success`, having recorded nothing**: tested with the limit cut to 1 minute, the run ended 61 seconds after it started and the approval stayed `pending` |
| A decision submitted, completion page shown in a browser | the **completion page POSTs an empty body to itself** (with the page's `x-auth-token`) as soon as it loads, which ends the run; the page then polls `…/n8n-execution-status` until it reports `success` |
| A decision submitted by a client that never makes that POST | the run stays `waiting` with `waitTill` in the year 3000, i.e. forever. This is what my first scripted client did, leaving one run per session |
| Same, with a time limit on the completion page | released by the limit: with 1 minute the run ended as `success` after 64 seconds. The workflow now sets a 10-minute limit on every completion page, so a closed tab cannot leave a run waiting forever |

The decision itself is stored when the decision page is submitted, never by the completion page, so none of
the above can lose or duplicate a decision.

### Spike runs

| Run | What | Result |
|---|---|---|
| S1 | Form Trigger (`n8nUserAuth`) with a hidden field, a Code node, a dynamic Form page, a completion page | whole flow completed headless; completion page said `decision=approve by approver@example.com for 5` |
| S2 | One Form Trigger with an IF: with `approval_id` a decision page, without it a list page with a table, links and hostile text | landing page, list page and sanitisation observed as above |
| S3 to S6 | Diagnosis of the client quirks above (Secure cookies; Accept header matrices; Sec-Fetch and Referer; retry loop) | table above |
| S7 to S9 | Which executions stay `waiting` and why (n8n's `execution_entity` rows, the Form node's source, the completion page script), then the two sessions A and B above with 1-minute limits | the table above |

The spike instance is a copy of the repo run as compose project `hoa-spike` (ports 5691 and 5435) and is
removed when P4 is finished.

---

## 3. Bugs and fixes

| ID | Sev | What was wrong | Cause | How found | Fix | Guard |
|---|---|---|---|---|---|---|
| B1 | **High** | A flagged invoice could be approved, or any invoice rejected, with **no reason** at the table level (`comment` NULL), and a decision with no approver was accepted | a `CHECK` that evaluates to NULL **passes** in SQL: `length(btrim(NULL)) > 0` is NULL. Only the function guarded it | `db/tests/approvals.sql`, the assertion that writes directly to the table | `COALESCE(length(...), 0) > 0` in both constraints | the same assertions; a mutation run that drops the constraint makes the tests fail |
| B2 | **High** | A reason made only of line breaks or tabs counted as written (`E'  \n '` was accepted) | Postgres `btrim(text)` removes only spaces | the SQL test for a blank reason | `approval_trim()` removes space, tab, line breaks, form feed, vertical tab, non-breaking and zero-width space; used by the function and the constraints | SQL test, and a mutation that swaps it for `btrim` fails the tests |
| B3 | Med | Truncating `invoices` (reset scripts, the pipeline check) would fail once approvals exist | `approvals.invoice_id` is a foreign key **without** cascade, deliberately, so a decision cannot vanish with its invoice | designing the schema | `approvals` added to the three `TRUNCATE` statements | `pipeline_check` truncates first, every run |
| B4 | **High** | `setup-owner.sh` created the owner account and then died with `curl: (23) client returned ERROR on write of 4319 bytes`, reporting nothing | `curl -o /dev/null` for the owner-setup and login requests. `curl.exe` is a native Windows program, so `/dev/null` only works through Git Bash's path conversion, which `MSYS_NO_PATHCONV=1` (set by the other scripts and by the CI simulation) switches off. The ~4 KB response could not be written, curl exited 23, and `set -e` ended the script **after** n8n had created the account | the first run on the integration copy, and then the CI simulation, which reproduced it. **My first diagnosis was wrong**: I blamed `curl \| grep -q` and `pipefail`; that response is only 474 bytes, and the 4319 bytes in the error are the owner-setup response. The grep change was harmless and is kept, but it was never the cause (see M8) | the body and status are captured with `-w '\n%{http_code}'` into a variable (`post_json`), nothing is written to a path | CI creates the owner and requires the message "created the owner account", then runs the script again and requires "already exists" |
| B5 | Med | Every scripted approval session left an n8n execution `waiting` forever | the completion page ends the run by POSTing to itself; my client never did (and completion pages had no time limit) | the same `waiting` rows with `waitTill` in the year 3000 after the tests | the client finishes completion pages like a browser and closes pages it only read; the workflow limits completion pages to 10 minutes | pipeline check: no run of the Approval Form is left `waiting` after the checks |
| B6 | Low | A hostile comment, or any database text, must never become markup on an approver's screen | text from supplier PDFs passes through a model into the database and into HTML | design review before building | `esc()` on every dynamic value in every page node; n8n also strips scripts but that is a second layer | `test_approval_code.py` (under Node, and mutation-checked); the live check that a stored `<script>` comment is shown as text |
| B7 | **High** (for CI) | The form worked on the integration copy but not in the CI simulation: an anonymous request was redirected to `http://localhost:5678/oauth/authorize…` and a signed-in client got HTTP 400 | n8n builds the sign-in redirects from **`N8N_WEBHOOK_URL`**, not from the address the request used. `.env.example` says `http://localhost:5678/`; the checks reached n8n at `127.0.0.1`, and cookies belong to one host name, so sign-in could never complete. The integration copy only worked because I had set its `N8N_WEBHOOK_URL` to `127.0.0.1` by hand. Would have failed on GitHub too | the CI simulation (a clean `.env.example` with different ports), then the 302 `Location` header | scripted clients and the demo reach the form at the address in `N8N_WEBHOOK_URL`; the access-control check now probes the 302 without following it and asserts it points at `/oauth/authorize`; the simulation sets a consistent `N8N_WEBHOOK_URL`; README and architecture say to use one address everywhere | the pipeline check's access-control assertion (302 to `/oauth/authorize`) and the whole form section, which cannot complete under a mismatch |

---

---

## 4. Mistakes during the session

| # | Mistake | Consequence | What changed |
|---|---|---|---|
| M1 | In the first spike workflow I put `limitWaitTime`, `resumeAmount` and `resumeUnit` under the Form node's `options` | n8n imported it without complaint and ignored the settings; I would have shipped a form with no timeout believing it had one | read the node's parameter definition inside the container before relying on a parameter; recorded here so the real workflow uses the top-level parameters |
| M2 | Guessed that a Form Trigger URL is `/form/<path>` and spent a run on a 404 | one wasted diagnosis step | looked at the node source and the `webhookId` rule instead of guessing a second time |
| M3 | Wrote `\t\n` escapes into the migration through a shell command; the harness altered the backslash sequences, so the SQL file contained **real tab, line-feed, form-feed and vertical-tab characters** inside a string literal | the function still worked (the literal was valid), so tests passed; it would have been unreadable and fragile under any line-ending conversion. The same quirk had bitten in P2 and P3 | found with `cat -A`; rewritten with `chr()` codes, which contain no backslashes at all; the migration is now checked for control characters. Standing rule kept: write anything with backslashes using the Write or Edit tool, never a shell string |
| M4 | The first SQL test for the snapshot constraint inserted a row for an invoice that **already had an approval**, so the unique constraint refused it before the snapshot rule was reached | the test passed even with the snapshot constraint dropped: **a vacuous test** | found by a mutation run (drop the constraint, expect failure) that did not fail; the test now uses fresh invoices and covers all three snapshot shapes; the other expected-failure checks were narrowed from `WHEN OTHERS` to the specific error each rule raises |
| M5 | Ran queries in the integration copy without `COMPOSE_PROJECT_NAME`. `docker-compose.yml` pins `name: hotel-ops-agent`, so compose picked **the real stack** | two read-only `SELECT`s ran against the user's database; nothing changed. It was caught because the audit ids were too high for a fresh database | every integration command now goes through a wrapper that sets the override and refuses to run unless the `hoa-spike` containers exist |
| M6 | Expectations in `pipeline_check.py` that I had not updated: audit rows per upload (now three, not two), and a trail check that ran after a deliberate re-run | two false failures; not system faults | corrected; the trail check now states the re-run it expects |
| M7 | Added `# fmt: skip` to new test and demo files to avoid wrapping long lines (a habit already recorded in P3, M9) | hidden lint | removed again; the formatter owns layout |
| M8 | Stated the cause of the `setup-owner.sh` failure (`curl \| grep -q` and `pipefail`) after one observation, and fixed that, without checking the number in the error (4319 bytes) against the response I blamed (474 bytes) | the bug stayed; it was found again, and properly, by the CI simulation. I had broken the rule "never claim a cause that was not tested" | the fix and the real cause are in B4 with the contradicting evidence; before naming a cause, compare every figure in the error with the suspect |
| M9 | The first CI simulation run followed n8n's redirect to `http://localhost:5678`, which on this machine is **the real n8n** (the simulation was on port 5690) | only unauthenticated GETs of the sign-in redirect reached the real instance; nothing was changed. It happened because the simulation's `N8N_WEBHOOK_URL` still pointed at the default | the simulation now sets its own `N8N_WEBHOOK_URL`; an experiment copy must set its address as well as its ports (also added to CLAUDE.md next to the compose-project rule) |

---

## 5. Error handling

Every row was exercised in `evals/pipeline_check.py` (live, scripted client, mock model) or `db/tests/approvals.sql`.

| Fault | Behaviour |
|---|---|
| Reconciliation stored, then `request_approval` fails | the response carries `approval: {result: "pending", error}`; the invoice and its reconciliation stay; no approval row; reconciling again (`POST /webhook/reconcile`) creates it. Tested by renaming the function and restoring it |
| `request_approval` for an invoice that was never reconciled | `{result: "error", reason: "not_reconciled"}`, no row |
| Reconciliation re-run while the approval is pending and the result changed | the approval is refreshed to the latest result and the refresh is audited with before and after |
| Reconciliation re-run after a decision | the decision stands; `stale` is reported and audited if the result differs |
| Not signed in | n8n's own sign-in; the form is never shown |
| Wrong password | refused by n8n (and rate limited after a handful of attempts) |
| Unknown approval number, or a malformed one | a page saying so; nothing recorded |
| Approving a flagged or doubtful invoice, or rejecting any, with no reason (or whitespace) | refused by the function and again by the table; the page says why; the row stays pending; no `approval.decided` row |
| Second decision on an approval | refused; reports who decided first and when; the first decision stands; nothing more audited |
| Two people open the same approval | the first to submit wins; the second is told |
| Decision page abandoned | the run ends at its limit (30 minutes) as `success`, nothing recorded; tested with the limit cut to 1 minute |
| Browser closed on the completion page | the run is released by the completion page's own limit (10 minutes); the decision was already stored. Tested with 1 minute |
| Database error while recording | the execution fails and the Error Handler audits it; nothing is recorded |
| Hostile text in an invoice, a reason or a reason code | escaped before it reaches a page; stored exactly as written |
| Someone with database access edits `approvals` directly | decided rows are protected by a trigger and constraints; the audit log is append-only; neither is a security boundary against the table owner |

---

## 6. Unverified and open

- `setup-owner.sh` was fixed (B4) and its create and already-exists paths then passed in the clean CI
  simulation (third run); it has not been run on the user's own instance, which has no owner yet.
- The timeouts were tested with the limits cut to 1 minute, not with the real 30 and 10 minutes.
- **Files that fail extraction never reach an approver** (no invoice, so no approval). Whether they should appear
  on the pending list is open and not built; it needs a decision.
- Approval is **not wired to anything**: no payment or export reads `approvals.status`.
- Nobody is notified; approvers must open the pending list.
- Approval rights are "can sign in to this n8n". No roles, amount limits or separation of duties.
- A decision cannot be undone in the application.
- The displayed times are in the database session's time zone (shown with their offset, `+00:00` here), which is
  UTC and not the user's local time (+11).

- Until section 8, everything was observed with a scripted client on a throwaway instance. **The user has now
  used the form in a real browser** (section 8): sign-in, list, decision, result all worked. The first-request hang
  was not reported, so it looks like a quirk of the scripted client; a browser *sign-in consent page* was also
  not mentioned, so whether it appears for a person is still unknown.
- `n8nUserAuth` was tested with the **owner** account only. Whether n8n's self-hosted community edition lets
  you add further users, and what an approver account may then see in the editor, is not verified.

---

## 7. Demo runs

`python evals/demo.py` on the isolated copy (not the user's instance), real `qwen3.5:9b` (Ollama showed the model
loaded afterwards; it had none loaded before), the script deciding through the form as the owner account. These
show the pipeline works end to end; they are two invoices, not an accuracy result.

| Run | Invoice | What happened |
|---|---|---|
| 1 | `inv_0003.pdf`, a labelled price variance | extracted (3 lines), `flag` with `price_variance`, approval requested, **approved with a reason** by `owner@example.com`; audit trail: `invoice.ingested`, `invoice.reconciled`, `approval.requested`, `approval.decided` (the approver saw `flag`, reason stored). 29 s in total, most of it the model |
| 2 | a clean invoice | `recommend_approve`, approved **without** a reason as the rules allow; same four audit rows |

Not done: the interactive path (a person in a browser pressing through the pages). The first time is the user's.

---

## 8. The first real-browser run (by the user, 2026-10-09)

The user ran `bash scripts/setup-owner.sh` and `python evals/demo.py` on their own stack, signed in, opened the
pending list and an invoice, rejected it with a reason, and sent five screenshots (kept in
[`images/`](images/)). Everything worked end to end; the audit trail was printed by the demo.

| # | Screenshot | What it shows |
|---|---|---|
| 1 | [`p4-1-landing.png`](images/p4-1-landing.png) | the form's first page ("Invoice approvals", the approver is the signed-in account) with a Continue button |
| 2 | [`p4-2-pending-list-before-layout-fix.png`](images/p4-2-pending-list-before-layout-fix.png) | the pending list: one `NEEDS REVIEW` invoice, reasons `supplier_unknown, unmatched_line`, a Review link |
| 3 | [`p4-3-decision-page-top.png`](images/p4-3-decision-page-top.png) | the decision page: "You are deciding as owner@example.com", the reasons in words, supplier, GST check, the lines |
| 4 | [`p4-4-decision-page-form.png`](images/p4-4-decision-page-form.png) | Decision (required) and Comment (**required**, marked with an asterisk because this was not a clean recommendation) |
| 5 | [`p4-5-result.png`](images/p4-5-result.png) | "Recorded: rejected. Approval 70 was rejected by owner@example.com." |

### What the screenshots showed, and what was done

| Finding | Cause | Change |
|---|---|---|
| **The invoice was `supplier_unknown` with every line unmatched** | not a bug in the rules: the demo picked an invoice from the seed-42 dataset, but the user's database still held the **seed-44** master data I had loaded for the P3 gate run and left there. Its suppliers do not exist in that data. (The eval refuses to run in that state; the demo did not check.) | `demo.py` now compares the loaded master data with the dataset it picks invoices from and stops with the commands to fix it (`tests/test_demo.py`, mutation-checked). The user needs `bash scripts/reset-ops-data.sh && bash scripts/seed.sh` once to get meaningful demo invoices |
| The pending list overflowed the card: the 7-column table was wider than the form, so the Review link sat outside it and headings ran together | a table in a narrow card | a compact list: verdict, invoice, supplier and total on one line, the reasons, the waiting time and a "Review and decide" link |
| The list page's button said "Continue" but only closes the page | no label set | `buttonLabel` "Close" |
| The decision page's button said "Continue" but records a decision | no label set | `buttonLabel` "Record decision" |
| The result page was a dead end | no link | every page that ends a session (result, not found, already decided, invalid link) now links back to the pending list |
| The waiting time is shown in UTC (`+00:00`) while the user is at +11 | times come from the database session | not changed: shown with its offset, listed as a known limit |

What was **not** a problem: no hang between pages, no failed sign-in, the one-time consent step was not mentioned.
The fixes are covered by tests under Node, and the whole form was exercised again live on a rebuilt isolated
instance (pipeline check passes) before the workflows were re-exported and imported into the user's stack.

