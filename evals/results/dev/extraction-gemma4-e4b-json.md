# Extraction eval: gemma4:e4b

- Run: 2026-10-06 13:11 UTC
- Target: `ollama (extraction step only)` (http://localhost:11434), model `gemma4:e4b`, format mode `json`, num_ctx 4096, temperature 0, seed 42
- Dataset: `data-gen\out-dev` (seed 7, n=60); scored files: **59** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: pypdf text layer
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 99.2% (246/248) | 97.1% to 99.8% | PASS |

With n=59, a 98% target allows at most 1 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 59/59
- Valid after one retry: 0/59
- needs_review (invalid twice): 0/59
- Latency per invoice (incl. retry): median 4.8s, p95 6.8s, total 5.0 min
- Tokens per invoice: median prompt 1730, median completion 374
- Context: max 2662 of 4096 tokens used; 0 invoices within 5% of the limit (Ollama silently truncates beyond it)
- Invoices with every field and every line correct: 96.6%
- Line items: 246 correct of 248 true lines; 248 lines predicted (precision 99.2%, recall 99.2%)

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
| `supplier_code` | 98.8% |
| `description` | 99.2% |
| `quantity` | 100.0% |
| `unit` | 76.2% |
| `unit_price` | 100.0% |
| `gst_applicable` | 89.1% |
| `line_total` | 100.0% |
| `line_gst` | 74.6% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 98.8% | 98.8% |
| B | 97.1% | 97.1% |
| C | 100.0% | 100.0% |
| D | 100.0% | 100.0% |

## Failures (2 invoices; first 2)

- `inv_0009` (template A, extracted, attempts 1)
  - line 3: expected `{'description': 'Lettuce Iceberg Each', 'quantity': 15, 'unit_price_cents': 280, 'line_total_cents': 4200}`, got `{'supplier_code': 'P-LET01', 'description': 'Lettuce Iceberg', 'quantity': 15, 'unit': 'each', 'unit_price': '2.80', 'gst_applicable': False, 'line_total': '42.00', 'line_gst': None}`
- `inv_0041` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 2, 'unit_price_cents': 5890, 'line_total_cents': 11780}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '117.80', 'line_gst': None}`
