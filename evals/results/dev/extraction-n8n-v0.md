# Extraction eval: qwen3.5:9b

- Run: 2026-10-06 14:51 UTC
- Target: `n8n workflow `Ingest Invoice` (webhook, PDF text, model, validation, database)` (http://127.0.0.1:5678/webhook/invoice-upload), model `qwen3.5:9b`, format mode `json (n8n Ollama node)`, num_ctx 4096, temperature 0
- Dataset: `data-gen\out-dev` (seed 7, n=60); scored files: **59** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: n8n `Extract From File` (pdf.js text layer)
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 91.1% (226/248) | 86.9% to 94.1% | PASS |

With n=59, a 98% target allows at most 1 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 59/59
- Valid after one retry: 0/59
- needs_review (invalid twice): 0/59
- Latency per invoice (incl. retry): median 13.6s, p95 24.5s, total 13.9 min
- Tokens and context use: not visible through the workflow (see `ops.llm_calls`, planned for P6)
- Invoices with every field and every line correct: 86.4%
- Line items: 226 correct of 248 true lines; 248 lines predicted (precision 91.1%, recall 91.1%)

## Ingestion behaviour

Whether the pipeline did the right thing with each file, apart from extraction accuracy: `original` stored as new; `duplicate` (same invoice, different file) stored and linked to the original; `noop` (identical file) changes nothing.

| Expected | Files | Correct | Extraction failed (needs_review) | Indeterminate | Wrong |
|---|---:|---:|---:|---:|---:|
| duplicate | 4 | 4 | 0 | 0 | 0 |
| noop | 1 | 1 | 0 | 0 | 0 |
| original | 55 | 55 | 0 | 0 | 0 |

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
| `description` | 91.1% |
| `quantity` | 94.8% |
| `unit` | 71.8% |
| `unit_price` | 96.8% |
| `gst_applicable` | 95.2% |
| `line_total` | 96.8% |
| `line_gst` | 60.9% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 97.5% | 97.5% |
| B | 88.6% | 88.6% |
| C | 98.7% | 98.7% |
| D | 73.2% | 73.2% |

## Failures (8 invoices; first 8)

- `inv_0006` (template B, extracted, attempts 1)
  - line 2: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 1, 'unit_price_cents': 5890, 'line_total_cents': 5890}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L', 'quantity': 200, 'unit': 'Carton', 'unit_price': '58.90', 'gst_applicable': False, 'line_total': '58.90', 'line_gst': None}`
- `inv_0010` (template C, extracted, attempts 1)
  - line 1: expected `{'description': 'Takeaway Cups 8oz Carton 1000', 'quantity': 3, 'unit_price_cents': 8860, 'line_total_cents': 26580}`, got `{'supplier_code': None, 'description': 'Takeaway Cups 8oz Carton', 'quantity': 3, 'unit': 'each', 'unit_price': '88.60', 'gst_applicable': True, 'line_total': '265.80', 'line_gst': None}`
- `inv_0026` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 2, 'unit_price_cents': 6490, 'line_total_cents': 12980}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': True, 'line_total': '129.80', 'line_gst': None}`
- `inv_0030` (template D, extracted, attempts 1)
  - line 1: expected `{'description': 'Tomatoes Crushed 2.5kg Tin x6', 'quantity': 3, 'unit_price_cents': 2690, 'line_total_cents': 8070}`, got `{'supplier_code': 'G-TOM', 'description': 'Tomatoes Crushed 2.5kg Tin x6 3 carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '26.90', 'gst_applicable': False, 'line_total': '80.70', 'line_gst': None}`
  - line 2: expected `{'description': 'Flour Plain 12.5kg', 'quantity': 1, 'unit_price_cents': 1890, 'line_total_cents': 1890}`, got `{'supplier_code': 'G-FLR', 'description': 'Flour Plain 12.5kg 1 bag', 'quantity': 1, 'unit': 'bag', 'unit_price': '18.90', 'gst_applicable': False, 'line_total': '18.90', 'line_gst': None}`
- `inv_0034` (template D, extracted, attempts 1)
  - line 1: expected `{'description': 'Batteries AA 24pk', 'quantity': 8, 'unit_price_cents': 2190, 'line_total_cents': 17520}`, got `{'supplier_code': 'X-BAA', 'description': 'Batteries AA 24pk 8 pack', 'quantity': 21.9, 'unit': None, 'unit_price': '175.20', 'gst_applicable': True, 'line_total': '17.52', 'line_gst': '1.75'}`
  - line 2: expected `{'description': 'LED Globe 9W E27', 'quantity': 27, 'unit_price_cents': 590, 'line_total_cents': 15930}`, got `{'supplier_code': 'X-LED9', 'description': 'LED Globe 9W E27 27 each', 'quantity': 5.9, 'unit': None, 'unit_price': '159.30', 'gst_applicable': True, 'line_total': '15.93', 'line_gst': '1.59'}`
  - line 3: expected `{'description': 'Ceiling Paint White 10L', 'quantity': 2, 'unit_price_cents': 11900, 'line_total_cents': 23800}`, got `{'supplier_code': 'X-PNT', 'description': 'Ceiling Paint White 10L 2 tin', 'quantity': 119, 'unit': None, 'unit_price': '238.00', 'gst_applicable': True, 'line_total': '23.80', 'line_gst': '2.38'}`
  - line 4: expected `{'description': 'Shower Head Low Flow', 'quantity': 2, 'unit_price_cents': 4590, 'line_total_cents': 9180}`, got `{'supplier_code': 'X-SHW', 'description': 'Shower Head Low Flow 2 each', 'quantity': 45.9, 'unit': None, 'unit_price': '91.80', 'gst_applicable': True, 'line_total': '9.18', 'line_gst': '0.92'}`
- `inv_0041` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 2, 'unit_price_cents': 5890, 'line_total_cents': 11780}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '117.80', 'line_gst': '11.78'}`
  - line 2: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 1, 'unit_price_cents': 6490, 'line_total_cents': 6490}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': False, 'line_total': '64.90', 'line_gst': None}`
- `inv_0046` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Carrots 10kg Bag', 'quantity': 3, 'unit_price_cents': 1200, 'line_total_cents': 3600}`, got `{'supplier_code': 'P-CAR10', 'description': 'Carrots', 'quantity': 3, 'unit': 'bag', 'unit_price': '12.00', 'gst_applicable': False, 'line_total': '36.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Strawberries Punnet 250g', 'quantity': 24, 'unit_price_cents': 455, 'line_total_cents': 10920}`, got `{'supplier_code': 'P-STR', 'description': 'Strawberries', 'quantity': 24, 'unit': 'punnet', 'unit_price': '4.55', 'gst_applicable': False, 'line_total': '109.20', 'line_gst': None}`
- `inv_0060` (template D, extracted, attempts 1)
  - line 1: expected `{'description': 'Bacon Short Cut 1kg', 'quantity': 8, 'unit_price_cents': 1650, 'line_total_cents': 13200}`, got `{'supplier_code': 'M-BAC', 'description': 'Bacon Short Cut 1kg 8 pack', 'quantity': 1, 'unit': None, 'unit_price': '16.50', 'gst_applicable': False, 'line_total': '132.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Pork Belly Skin On', 'quantity': 4.992, 'unit_price_cents': 2150, 'line_total_cents': 10733}`, got `{'supplier_code': 'M-PKB', 'description': 'Pork Belly Skin On 4.992 kg', 'quantity': 1, 'unit': None, 'unit_price': '21.50', 'gst_applicable': False, 'line_total': '107.33', 'line_gst': None}`
  - line 3: expected `{'description': 'Beef Mince Premium', 'quantity': 11.428, 'unit_price_cents': 1490, 'line_total_cents': 17028}`, got `{'supplier_code': 'M-MIN', 'description': 'Beef Mince Premium 11.428 kg', 'quantity': 1, 'unit': None, 'unit_price': '14.90', 'gst_applicable': False, 'line_total': '170.28', 'line_gst': None}`
  - line 4: expected `{'description': 'Butchers Twine 500m Roll', 'quantity': 2, 'unit_price_cents': 1890, 'line_total_cents': 3780}`, got `{'supplier_code': 'M-TWN', 'description': 'Butchers Twine 500m Roll 2 roll', 'quantity': 2, 'unit': None, 'unit_price': '18.90', 'gst_applicable': True, 'line_total': '37.80', 'line_gst': '3.78'}`
