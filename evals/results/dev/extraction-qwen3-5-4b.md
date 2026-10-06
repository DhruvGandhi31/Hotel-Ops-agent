# Extraction eval: qwen3.5:4b

- Run: 2026-10-06 13:35 UTC
- Target: `ollama (extraction step only)` (http://localhost:11434), model `qwen3.5:4b`, format mode `schema`, num_ctx 4096, temperature 0, seed 42
- Dataset: `data-gen\out-dev` (seed 7, n=60); scored files: **59** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: pypdf text layer
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 98.3% (58/59) | 91.0% to 99.7% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (59/59) | 93.9% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 93.5% (232/248) | 89.8% to 96.0% | PASS |

With n=59, a 98% target allows at most 1 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 59/59
- Valid after one retry: 0/59
- needs_review (invalid twice): 0/59
- Latency per invoice (incl. retry): median 7.7s, p95 12.3s, total 8.0 min
- Tokens per invoice: median prompt 1699, median completion 355
- Context: max 2595 of 4096 tokens used; 0 invoices within 5% of the limit (Ollama silently truncates beyond it)
- Invoices with every field and every line correct: 81.4%
- Line items: 232 correct of 248 true lines; 257 lines predicted (precision 90.3%, recall 93.5%)

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
| `subtotal` | 98.3% | 100.0% | 100.0% | 100.0% | 92.3% |
| `gst` | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `total` * | 98.3% | 100.0% | 100.0% | 100.0% | 92.3% |

`*` gate field. Template columns: A (n=16), B (n=11), C (n=19), D (n=13).

## Line fields

Per-field accuracy over true lines, paired by position, and only counted when the invoice has the right number of lines (otherwise all its lines count as wrong).

| Field | Accuracy |
|---|---:|
| `supplier_code` | 91.1% |
| `description` | 95.2% |
| `quantity` | 98.8% |
| `unit` | 83.5% |
| `unit_price` | 96.4% |
| `gst_applicable` | 96.0% |
| `line_total` | 98.4% |
| `line_gst` | 57.3% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 100.0% | 100.0% |
| B | 85.7% | 85.7% |
| C | 87.0% | 77.9% |
| D | 98.2% | 98.2% |

## Failures (8 invoices; first 8)

- `inv_0006` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bleach 5L', 'quantity': 3, 'unit_price_cents': 1290, 'line_total_cents': 3870}`, got `{'supplier_code': 'C-BLE5', 'description': 'Bleach 5L ^', 'quantity': 3, 'unit': 'bottle', 'unit_price': '12.90', 'gst_applicable': True, 'line_total': '38.70', 'line_gst': '3.87'}`
  - line 2: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 1, 'unit_price_cents': 5890, 'line_total_cents': 5890}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton 200 ^', 'quantity': 1, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '58.90', 'line_gst': '5.89'}`
  - line 3: expected `{'description': 'Dishwasher Detergent 20L', 'quantity': 2, 'unit_price_cents': 8900, 'line_total_cents': 17800}`, got `{'supplier_code': 'C-DET20', 'description': 'Dishwasher Detergent 20L ^', 'quantity': 2, 'unit': 'drum', 'unit_price': '89.00', 'gst_applicable': True, 'line_total': '178.00', 'line_gst': '17.80'}`
- `inv_0017` (template C, extracted, attempts 1)
  - line 4: expected `{'description': 'Polystyrene Box Charge', 'quantity': 4, 'unit_price_cents': 500, 'line_total_cents': 2000}`, got `{'supplier_code': None, 'description': 'Polystyrene Box Charge *', 'quantity': 4, 'unit': 'each', 'unit_price': '5.00', 'gst_applicable': True, 'line_total': '20.00', 'line_gst': '2.00'}`
- `inv_0018` (template B, extracted, attempts 1)
  - line 2: expected `{'description': 'Ice Cream Vanilla 5L', 'quantity': 3, 'unit_price_cents': 2490, 'line_total_cents': 7470}`, got `{'supplier_code': 'SKU D-ICE5', 'description': 'Ice Cream Vanilla 5L ^', 'quantity': 3, 'unit': 'tub', 'unit_price': '24.90', 'gst_applicable': True, 'line_total': '74.70', 'line_gst': '7.47'}`
- `inv_0030` (template D, extracted, attempts 1)
  - subtotal: expected `99.6`, got `97.60`
  - total: expected `99.6`, got `97.60`
  - line 1: expected `{'description': 'Tomatoes Crushed 2.5kg Tin x6', 'quantity': 3, 'unit_price_cents': 2690, 'line_total_cents': 8070}`, got `{'supplier_code': 'G-TOM', 'description': 'Tomatoes Crushed 2.5kg Tin x6', 'quantity': 3, 'unit': 'carton', 'unit_price': '26.90', 'gst_applicable': False, 'line_total': '78.70', 'line_gst': '0.00'}`
- `inv_0052` (template B, extracted, attempts 1)
  - line 6: expected `{'description': 'Ice Cream Vanilla 5L', 'quantity': 4, 'unit_price_cents': 2490, 'line_total_cents': 9960}`, got `{'supplier_code': 'SKU D-ICE5', 'description': 'Ice Cream Vanilla 5L ^', 'quantity': 4, 'unit': 'tub', 'unit_price': '24.90', 'gst_applicable': True, 'line_total': '99.60', 'line_gst': '9.96'}`
- `inv_0054` (template C, extracted, attempts 1)
  - line 4: expected `{'description': 'Muffin Blueberry', 'quantity': 19, 'unit_price_cents': 260, 'line_total_cents': 4940}`, got `{'supplier_code': None, 'description': 'Muffin Blueberry *', 'quantity': 19, 'unit': None, 'unit_price': '2.60', 'gst_applicable': True, 'line_total': '49.40', 'line_gst': None}`
  - line 5: expected `{'description': 'Danish Pastry Assorted', 'quantity': 17, 'unit_price_cents': 290, 'line_total_cents': 4930}`, got `{'supplier_code': None, 'description': 'Danish Pastry Assorted *', 'quantity': 17, 'unit': None, 'unit_price': '2.90', 'gst_applicable': True, 'line_total': '49.30', 'line_gst': None}`
- `inv_0055` (template C, extracted, attempts 1)
  - line 1: expected `{'description': 'Single Origin Ethiopia 1kg', 'quantity': 4, 'unit_price_cents': 4290, 'line_total_cents': 17160}`, got `{'supplier_code': None, 'description': 'Single Origin Ethiopia 1kg', 'quantity': 4, 'unit': 'each', 'unit_price': '171.60', 'gst_applicable': False, 'line_total': '171.60', 'line_gst': None}`
  - line 4: expected `{'description': 'Coffee Beans House Blend 1kg', 'quantity': 6, 'unit_price_cents': 3290, 'line_total_cents': 19740}`, got `{'supplier_code': None, 'description': 'Coffee Beans House Blend 1kg', 'quantity': 6, 'unit': 'each', 'unit_price': '197.40', 'gst_applicable': False, 'line_total': '197.40', 'line_gst': None}`
  - line 5: expected `{'description': 'Coffee Beans Decaf 1kg', 'quantity': 4, 'unit_price_cents': 3690, 'line_total_cents': 14760}`, got `{'supplier_code': None, 'description': 'Coffee Beans Decaf 1kg', 'quantity': 4, 'unit': 'each', 'unit_price': '147.60', 'gst_applicable': False, 'line_total': '147.60', 'line_gst': None}`
  - line 6: expected `{'description': 'Takeaway Cups 8oz Carton 1000', 'quantity': 3, 'unit_price_cents': 8900, 'line_total_cents': 26700}`, got `{'supplier_code': None, 'description': 'Takeaway Cups 8oz Carton 1000', 'quantity': 3, 'unit': 'each', 'unit_price': '267.00', 'gst_applicable': True, 'line_total': '267.00', 'line_gst': None}`
- `inv_0056` (template C, extracted, attempts 1)
  - line 4: expected `{'description': 'Muffin Blueberry', 'quantity': 21, 'unit_price_cents': 260, 'line_total_cents': 5460}`, got `{'supplier_code': None, 'description': 'Muffin Blueberry *', 'quantity': 21, 'unit': 'each', 'unit_price': '2.60', 'gst_applicable': True, 'line_total': '54.60', 'line_gst': None}`
