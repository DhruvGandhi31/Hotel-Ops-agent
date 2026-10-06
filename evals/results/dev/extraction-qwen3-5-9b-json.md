# Extraction eval: qwen3.5:9b

- Run: 2026-10-06 13:27 UTC
- Target: `ollama (extraction step only)` (http://localhost:11434), model `qwen3.5:9b`, format mode `json`, num_ctx 4096, temperature 0, seed 42
- Dataset: `data-gen\out-dev` (seed 7, n=60); scored files: **59** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: pypdf text layer
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 100.0% (248/248) | 98.5% to 100.0% | PASS |

With n=59, a 98% target allows at most 1 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 59/59
- Valid after one retry: 0/59
- needs_review (invalid twice): 0/59
- Latency per invoice (incl. retry): median 15.9s, p95 26.7s, total 16.2 min
- Tokens per invoice: median prompt 1699, median completion 547
- Context: max 2971 of 4096 tokens used; 0 invoices within 5% of the limit (Ollama silently truncates beyond it)
- Invoices with every field and every line correct: 100.0%
- Line items: 248 correct of 248 true lines; 248 lines predicted (precision 100.0%, recall 100.0%)

## Header fields

| Field | Accuracy | A | B | C | D |
|---|---:|---:|---:|---:|---:|
| `supplier_name` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `supplier_abn` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `bill_to_name` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `invoice_number` * | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `invoice_date` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `due_date` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `po_number` * | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `currency` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `subtotal` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `gst` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `total` * | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

`*` gate field. Template columns: A (n=16), B (n=11), C (n=19), D (n=13).

## Line fields

Per-field accuracy over true lines, paired by position, and only counted when the invoice has the right number of lines (otherwise all its lines count as wrong).

| Field | Accuracy |
|---|---:|
| `supplier_code` | 100.0% |
| `description` | 100.0% |
| `quantity` | 100.0% |
| `unit` | 77.4% |
| `unit_price` | 100.0% |
| `gst_applicable` | 91.5% |
| `line_total` | 100.0% |
| `line_gst` | 63.7% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 100.0% | 100.0% |
| B | 100.0% | 100.0% |
| C | 100.0% | 100.0% |
| D | 100.0% | 100.0% |

## Failures (0 invoices; first 0)

