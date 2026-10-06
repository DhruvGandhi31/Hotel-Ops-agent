# Extraction eval: qwen3.5:9b

- Run: 2026-10-06 15:58 UTC
- Target: `n8n workflow `Ingest Invoice` (webhook, PDF text, model, validation, database)` (http://127.0.0.1:5678/webhook/invoice-upload), model `qwen3.5:9b`, format mode `json (n8n Ollama node)`, num_ctx 4096, temperature 0
- Dataset: `data-gen\out-gate2` (seed 43, n=200); scored files: **196** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: `pdf-text` service (pypdf) called by the workflow, the same code the dev evals use
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 99.2% (892/899) | 98.4% to 99.6% | PASS |

With n=196, a 98% target allows at most 3 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 195/196
- Valid after one retry: 1/196
- needs_review (invalid twice): 0/196
- Latency per invoice (incl. retry): median 13.6s, p95 25.0s, total 49.3 min
- Tokens and context use: not visible through the workflow (see `ops.llm_calls`, planned for P6)
- Invoices with every field and every line correct: 97.4%
- Line items: 892 correct of 899 true lines; 898 lines predicted (precision 99.3%, recall 99.2%)

## Ingestion behaviour

Whether the pipeline did the right thing with each file, apart from extraction accuracy: `original` stored as new; `duplicate` (same invoice, different file) stored and linked to the original; `noop` (identical file) changes nothing.

| Expected | Files | Correct | Extraction failed (needs_review) | Indeterminate | Wrong |
|---|---:|---:|---:|---:|---:|
| duplicate | 12 | 12 | 0 | 0 | 0 |
| noop | 4 | 4 | 0 | 0 | 0 |
| original | 184 | 184 | 0 | 0 | 0 |

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

`*` gate field. Template columns: A (n=38), B (n=52), C (n=54), D (n=52).

## Line fields

Per-field accuracy over true lines, paired by position, and only counted when the invoice has the right number of lines (otherwise all its lines count as wrong).

| Field | Accuracy |
|---|---:|
| `supplier_code` | 98.1% |
| `description` | 98.9% |
| `quantity` | 99.4% |
| `unit` | 79.6% |
| `unit_price` | 99.4% |
| `gst_applicable` | 88.7% |
| `line_total` | 99.3% |
| `line_gst` | 68.4% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 99.3% | 99.3% |
| B | 98.9% | 98.9% |
| C | 99.2% | 99.2% |
| D | 99.6% | 100.0% |

## Failures (5 invoices; first 5)

- `inv_0009` (template C, extracted, attempts 1)
  - line 6: expected `{'description': 'Oysters SRO x12', 'quantity': 8, 'unit_price_cents': 2400, 'line_total_cents': 19200}`, got `{'supplier_code': None, 'description': 'Oysters SRO', 'quantity': 8, 'unit': None, 'unit_price': '24.00', 'gst_applicable': True, 'line_total': '192.00', 'line_gst': None}`
- `inv_0026` (template B, extracted, attempts 1)
  - line 6: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 2, 'unit_price_cents': 5890, 'line_total_cents': 11780}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': False, 'line_total': '117.80', 'line_gst': None}`
  - line 7: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 2, 'unit_price_cents': 6490, 'line_total_cents': 12980}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': False, 'line_total': '129.80', 'line_gst': None}`
  - line 8: expected `{'description': 'Nitrile Gloves Large Box 100', 'quantity': 10, 'unit_price_cents': 1490, 'line_total_cents': 14900}`, got `{'supplier_code': 'C-GLV', 'description': 'Nitrile Gloves Large Box', 'quantity': 10, 'unit': 'box', 'unit_price': '14.90', 'gst_applicable': False, 'line_total': '149.00', 'line_gst': None}`
- `inv_0103` (template D, extracted, attempts 1)
  - line 4: expected `{'description': 'Low flow shower rose', 'quantity': 3, 'unit_price_cents': 4590, 'line_total_cents': 13770}`, got `{'supplier_code': 'X-BAA', 'description': 'Batteries AA 24pk', 'quantity': 7, 'unit': 'pack', 'unit_price': '21.90', 'gst_applicable': True, 'line_total': '153.30', 'line_gst': '15.33'}`
- `inv_0130` (template C, extracted, attempts 1)
  - line 1: expected `{'description': 'Cups paper 8 oz (1000)', 'quantity': 3, 'unit_price_cents': 8900, 'line_total_cents': 26700}`, got `{'supplier_code': None, 'description': 'Cups paper 8 oz', 'quantity': 3, 'unit': 'each', 'unit_price': '89.00', 'gst_applicable': True, 'line_total': '267.00', 'line_gst': None}`
- `inv_0191` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Shiraz Barossa 750ml Case 12', 'quantity': 1, 'unit_price_cents': 19800, 'line_total_cents': 19800}`, got `{'supplier_code': 'L-SHZ', 'description': 'Shiraz Barossa 750ml Case 12', 'quantity': 1, 'unit': 'case', 'unit_price': '198.00', 'gst_applicable': True, 'line_total': '217.80', 'line_gst': '19.80'}`
