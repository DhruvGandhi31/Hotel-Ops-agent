# Extraction eval: qwen3.5:9b

- Run: 2026-10-06 14:34 UTC
- Target: `n8n workflow `Ingest Invoice` (webhook, PDF text, model, validation, database)` (http://127.0.0.1:5678/webhook/invoice-upload), model `qwen3.5:9b`, format mode `json (n8n Ollama node)`, num_ctx 4096, temperature 0
- Dataset: `data-gen\out` (seed 42, n=200); scored files: **196** (exact re-sends are excluded from extraction scoring: same bytes as an original)
- Input text: n8n `Extract From File` (pdf.js text layer)
- Prompt sha256: `359f40e755cd`, schema sha256: `e5780e8f0a47`

## Gate targets

| Metric | Target | Result | 95% CI | Pass |
|---|---:|---:|---:|:---:|
| `total` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| `invoice_number` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| `po_number` | >= 98.0% | 100.0% (196/196) | 98.1% to 100.0% | PASS |
| line items (strict: description, quantity, unit price, line total) | >= 90.0% | 86.8% (762/878) | 84.4% to 88.9% | FAIL |

With n=196, a 98% target allows at most 3 misses per field; one run is a weak signal near the threshold, so read the interval as well as the point estimate.

## Pipeline health

- Valid first attempt: 195/196
- Valid after one retry: 1/196
- needs_review (invalid twice): 0/196
- Latency per invoice (incl. retry): median 13.9s, p95 24.7s, total 48.2 min
- Tokens and context use: not visible through the workflow (see `ops.llm_calls`, planned for P6)
- Invoices with every field and every line correct: 77.6%
- Line items: 762 correct of 878 true lines; 878 lines predicted (precision 86.8%, recall 86.8%)

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

`*` gate field. Template columns: A (n=49), B (n=47), C (n=49), D (n=51).

## Line fields

Per-field accuracy over true lines, paired by position, and only counted when the invoice has the right number of lines (otherwise all its lines count as wrong).

| Field | Accuracy |
|---|---:|
| `supplier_code` | 98.7% |
| `description` | 87.7% |
| `quantity` | 98.7% |
| `unit` | 82.3% |
| `unit_price` | 99.2% |
| `gst_applicable` | 92.7% |
| `line_total` | 98.7% |
| `line_gst` | 66.1% |

| Template | Line recall | Line precision |
|---|---:|---:|
| A | 90.9% | 90.9% |
| B | 65.3% | 65.3% |
| C | 98.1% | 98.1% |
| D | 90.4% | 90.4% |

## Failures (44 invoices; first 25)

- `inv_0014` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 3, 'unit_price_cents': 5890, 'line_total_cents': 17670}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '176.70', 'line_gst': None}`
  - line 2: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 2, 'unit_price_cents': 6490, 'line_total_cents': 12980}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': True, 'line_total': '129.80', 'line_gst': None}`
- `inv_0017` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Delivery and Handling Fee', 'quantity': 1, 'unit_price_cents': 2481, 'line_total_cents': 2481}`, got `{'supplier_code': 'N-DEL', 'description': 'Delivery and Handling Fee', 'quantity': 1, 'unit': 'each', 'unit_price': '24.81', 'gst_applicable': True, 'line_total': '27.29', 'line_gst': '2.48'}`
- `inv_0020` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Potatoes Washed 20kg Bag', 'quantity': 4, 'unit_price_cents': 2600, 'line_total_cents': 10400}`, got `{'supplier_code': 'P-POT20', 'description': 'Potatoes Washed', 'quantity': 4, 'unit': 'bag', 'unit_price': '26.00', 'gst_applicable': False, 'line_total': '104.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Avocado Hass Tray 20', 'quantity': 3, 'unit_price_cents': 4800, 'line_total_cents': 14400}`, got `{'supplier_code': 'P-AVO20', 'description': 'Avocado Hass', 'quantity': 3, 'unit': 'tray', 'unit_price': '48.00', 'gst_applicable': False, 'line_total': '144.00', 'line_gst': None}`
  - line 3: expected `{'description': 'Spinach Baby Leaf 1kg', 'quantity': 4, 'unit_price_cents': 1150, 'line_total_cents': 4600}`, got `{'supplier_code': 'P-SPN1', 'description': 'Spinach Baby Leaf', 'quantity': 4, 'unit': 'bag', 'unit_price': '11.50', 'gst_applicable': False, 'line_total': '46.00', 'line_gst': None}`
- `inv_0021` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 3, 'unit_price_cents': 6490, 'line_total_cents': 19470}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': True, 'line_total': '194.70', 'line_gst': None}`
- `inv_0028` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Body Wash 30ml Carton 300', 'quantity': 3, 'unit_price_cents': 17900, 'line_total_cents': 53700}`, got `{'supplier_code': 'A-BDW', 'description': 'Body Wash 30ml Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '179.00', 'gst_applicable': True, 'line_total': '537.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Facial Tissues Carton 48', 'quantity': 3, 'unit_price_cents': 5290, 'line_total_cents': 15870}`, got `{'supplier_code': 'A-TIS', 'description': 'Facial Tissues Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '52.90', 'gst_applicable': False, 'line_total': '158.70', 'line_gst': None}`
  - line 3: expected `{'description': 'Shampoo 30ml Carton 300', 'quantity': 2, 'unit_price_cents': 18900, 'line_total_cents': 37800}`, got `{'supplier_code': 'A-SHM', 'description': 'Shampoo 30ml Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '189.00', 'gst_applicable': True, 'line_total': '378.00', 'line_gst': None}`
  - line 4: expected `{'description': 'Dental Kit Carton 250', 'quantity': 1, 'unit_price_cents': 13750, 'line_total_cents': 13750}`, got `{'supplier_code': 'A-DEN', 'description': 'Dental Kit Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '137.50', 'gst_applicable': False, 'line_total': '137.50', 'line_gst': None}`
- `inv_0031` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Body Wash 30ml Carton 300', 'quantity': 2, 'unit_price_cents': 17900, 'line_total_cents': 35800}`, got `{'supplier_code': 'A-BDW', 'description': 'Body Wash 30ml Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '179.00', 'gst_applicable': True, 'line_total': '358.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Dental Kit Carton 250', 'quantity': 1, 'unit_price_cents': 13750, 'line_total_cents': 13750}`, got `{'supplier_code': 'A-DEN', 'description': 'Dental Kit Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '137.50', 'gst_applicable': True, 'line_total': '137.50', 'line_gst': None}`
  - line 3: expected `{'description': 'Toilet Paper 2ply Carton 48', 'quantity': 3, 'unit_price_cents': 4590, 'line_total_cents': 13770}`, got `{'supplier_code': 'A-TPR', 'description': 'Toilet Paper 2ply Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '45.90', 'gst_applicable': False, 'line_total': '137.70', 'line_gst': None}`
- `inv_0036` (template C, extracted, attempts 1)
  - line 1: expected `{'description': 'Oysters Sydney Rock Dozen', 'quantity': 8, 'unit_price_cents': 2400, 'line_total_cents': 19200}`, got `{'supplier_code': None, 'description': 'Oysters Sydney Rock Dozen', 'quantity': 8, 'unit': 'each', 'unit_price': '192.00', 'gst_applicable': False, 'line_total': '192.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Mussels Live 1kg Bag', 'quantity': 7, 'unit_price_cents': 1198, 'line_total_cents': 8386}`, got `{'supplier_code': None, 'description': 'Mussels Live 1kg Bag', 'quantity': 7, 'unit': 'each', 'unit_price': '83.86', 'gst_applicable': False, 'line_total': '83.86', 'line_gst': None}`
- `inv_0042` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Vodka 700ml', 'quantity': 5, 'unit_price_cents': 3990, 'line_total_cents': 19950}`, got `{'supplier_code': 'L-VOD', 'description': 'Vodka 700ml 5 bottle', 'quantity': 5, 'unit': 'bottle', 'unit_price': '39.90', 'gst_applicable': True, 'line_total': '199.50', 'line_gst': '19.95'}`
  - line 2: expected `{'description': 'Sauvignon Blanc 750ml Case 12', 'quantity': 1, 'unit_price_cents': 16800, 'line_total_cents': 16800}`, got `{'supplier_code': 'L-SAV', 'description': 'Sauvignon Blanc 750ml Case 12 1 case', 'quantity': 1, 'unit': 'case', 'unit_price': '168.00', 'gst_applicable': True, 'line_total': '168.00', 'line_gst': '16.80'}`
  - line 3: expected `{'description': 'Gin London Dry 700ml', 'quantity': 6, 'unit_price_cents': 4890, 'line_total_cents': 29340}`, got `{'supplier_code': 'L-GIN', 'description': 'Gin London Dry 700ml 6 bottle', 'quantity': 6, 'unit': 'bottle', 'unit_price': '48.90', 'gst_applicable': True, 'line_total': '293.40', 'line_gst': '29.34'}`
  - line 4: expected `{'description': 'Lager Bottles 375ml Case 24', 'quantity': 4, 'unit_price_cents': 5990, 'line_total_cents': 23960}`, got `{'supplier_code': 'L-LAG', 'description': 'Lager Bottles 375ml Case 24 4 case', 'quantity': 4, 'unit': 'case', 'unit_price': '59.90', 'gst_applicable': True, 'line_total': '239.60', 'line_gst': '23.96'}`
- `inv_0046` (template A, extracted, attempts 1)
  - line 3: expected `{'description': 'Lettuce Iceberg Each', 'quantity': 12, 'unit_price_cents': 280, 'line_total_cents': 3360}`, got `{'supplier_code': 'P-LET01', 'description': 'Lettuce Iceberg', 'quantity': 12, 'unit': 'each', 'unit_price': '2.80', 'gst_applicable': False, 'line_total': '33.60', 'line_gst': None}`
- `inv_0051` (template B, extracted, attempts 1)
  - line 3: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 1, 'unit_price_cents': 5890, 'line_total_cents': 5890}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': False, 'line_total': '58.90', 'line_gst': None}`
- `inv_0053` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Avocado Hass Tray 20', 'quantity': 3, 'unit_price_cents': 4800, 'line_total_cents': 14400}`, got `{'supplier_code': 'P-AVO20', 'description': 'Avocado Hass Tray', 'quantity': 3, 'unit': 'tray', 'unit_price': '48.00', 'gst_applicable': False, 'line_total': '144.00', 'line_gst': None}`
- `inv_0055` (template A, extracted, attempts 1)
  - line 1: expected `{'description': 'Delivery and Handling Fee', 'quantity': 1, 'unit_price_cents': 2500, 'line_total_cents': 2500}`, got `{'supplier_code': 'N-DEL', 'description': 'Delivery and Handling Fee', 'quantity': 1, 'unit': 'each', 'unit_price': '25.00', 'gst_applicable': True, 'line_total': '27.50', 'line_gst': '2.50'}`
  - line 2: expected `{'description': 'Bath Mat Laundered', 'quantity': 60, 'unit_price_cents': 90, 'line_total_cents': 5400}`, got `{'supplier_code': 'N-BMT', 'description': 'Bath Mat Laundered', 'quantity': 60, 'unit': 'each', 'unit_price': '0.90', 'gst_applicable': True, 'line_total': '54.90', 'line_gst': '5.40'}`
- `inv_0057` (template C, extracted, attempts 1)
  - line 2: expected `{'description': 'Oysters Sydney Rock Dozen', 'quantity': 5, 'unit_price_cents': 2400, 'line_total_cents': 12000}`, got `{'supplier_code': None, 'description': 'Oysters Sydney Rock', 'quantity': 5, 'unit': 'each', 'unit_price': '24.00', 'gst_applicable': True, 'line_total': '120.00', 'line_gst': None}`
- `inv_0059` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 3, 'unit_price_cents': 5890, 'line_total_cents': 17670}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '176.70', 'line_gst': None}`
  - line 2: expected `{'description': 'Nitrile Gloves Large Box 100', 'quantity': 8, 'unit_price_cents': 1490, 'line_total_cents': 11920}`, got `{'supplier_code': 'C-GLV', 'description': 'Nitrile Gloves Large Box', 'quantity': 8, 'unit': 'box', 'unit_price': '14.90', 'gst_applicable': True, 'line_total': '119.20', 'line_gst': None}`
- `inv_0066` (template D, extracted, attempts 1)
  - line 1: expected `{'description': 'Tomatoes Crushed 2.5kg Tin x6', 'quantity': 1, 'unit_price_cents': 2690, 'line_total_cents': 2690}`, got `{'supplier_code': 'G-TOM', 'description': 'Tomatoes Crushed 2.5kg Tin x6 1 carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '26.90', 'gst_applicable': False, 'line_total': '26.90', 'line_gst': None}`
  - line 2: expected `{'description': 'Rice Jasmine 10kg', 'quantity': 2, 'unit_price_cents': 2290, 'line_total_cents': 4580}`, got `{'supplier_code': 'G-RIC', 'description': 'Rice Jasmine 10kg 2 bag', 'quantity': 2, 'unit': 'bag', 'unit_price': '22.90', 'gst_applicable': False, 'line_total': '45.80', 'line_gst': None}`
  - line 3: expected `{'description': 'Olive Oil Extra Virgin 4L', 'quantity': 3, 'unit_price_cents': 4590, 'line_total_cents': 13770}`, got `{'supplier_code': 'G-OIL', 'description': 'Olive Oil Extra Virgin 4L 3 tin', 'quantity': 3, 'unit': 'tin', 'unit_price': '45.90', 'gst_applicable': False, 'line_total': '137.70', 'line_gst': None}`
  - line 4: expected `{'description': 'Soft Drink Cans 375ml 24pk', 'quantity': 6, 'unit_price_cents': 2890, 'line_total_cents': 17340}`, got `{'supplier_code': 'G-SFT', 'description': 'Soft Drink Cans 375ml 24pk 6 carton', 'quantity': 6, 'unit': 'carton', 'unit_price': '28.90', 'gst_applicable': True, 'line_total': '173.40', 'line_gst': '17.34'}`
- `inv_0067` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Facial Tissues Carton 48', 'quantity': 3, 'unit_price_cents': 5290, 'line_total_cents': 15870}`, got `{'supplier_code': 'A-TIS', 'description': 'Facial Tissues', 'quantity': 3, 'unit': 'carton', 'unit_price': '52.90', 'gst_applicable': True, 'line_total': '158.70', 'line_gst': None}`
  - line 2: expected `{'description': 'Dental Kit Carton 250', 'quantity': 1, 'unit_price_cents': 13750, 'line_total_cents': 13750}`, got `{'supplier_code': 'A-DEN', 'description': 'Dental Kit', 'quantity': 1, 'unit': 'carton', 'unit_price': '137.50', 'gst_applicable': True, 'line_total': '137.50', 'line_gst': None}`
- `inv_0071` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Nitrile Gloves Large Box 100', 'quantity': 4, 'unit_price_cents': 1490, 'line_total_cents': 5960}`, got `{'supplier_code': 'C-GLV', 'description': 'Nitrile Gloves Large Box', 'quantity': 4, 'unit': 'box', 'unit_price': '14.90', 'gst_applicable': True, 'line_total': '59.60', 'line_gst': None}`
  - line 3: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 1, 'unit_price_cents': 5890, 'line_total_cents': 5890}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': False, 'line_total': '58.90', 'line_gst': None}`
- `inv_0075` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 3, 'unit_price_cents': 5890, 'line_total_cents': 17670}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '176.70', 'line_gst': None}`
- `inv_0085` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 2, 'unit_price_cents': 5890, 'line_total_cents': 11780}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '117.80', 'line_gst': None}`
  - line 2: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 1, 'unit_price_cents': 6490, 'line_total_cents': 6490}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 1, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': False, 'line_total': '64.90', 'line_gst': None}`
- `inv_0091` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Slippers Disposable Carton 100', 'quantity': 3, 'unit_price_cents': 12900, 'line_total_cents': 38700}`, got `{'supplier_code': 'A-SLP', 'description': 'Slippers Disposable Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '129.00', 'gst_applicable': True, 'line_total': '387.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Dental Kit Carton 250', 'quantity': 2, 'unit_price_cents': 13750, 'line_total_cents': 27500}`, got `{'supplier_code': 'A-DEN', 'description': 'Dental Kit Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '137.50', 'gst_applicable': True, 'line_total': '275.00', 'line_gst': None}`
  - line 3: expected `{'description': 'Conditioner 30ml Carton 300', 'quantity': 3, 'unit_price_cents': 18900, 'line_total_cents': 56700}`, got `{'supplier_code': 'A-CON', 'description': 'Conditioner 30ml Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '189.00', 'gst_applicable': True, 'line_total': '567.00', 'line_gst': None}`
  - line 4: expected `{'description': 'Shampoo 30ml Carton 300', 'quantity': 2, 'unit_price_cents': 18900, 'line_total_cents': 37800}`, got `{'supplier_code': 'A-SHM', 'description': 'Shampoo 30ml Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '189.00', 'gst_applicable': True, 'line_total': '378.00', 'line_gst': None}`
- `inv_0092` (template C, extracted, attempts 1)
  - line 1: expected `{'description': 'Brioche burger buns x6', 'quantity': 5, 'unit_price_cents': 720, 'line_total_cents': 3600}`, got `{'supplier_code': None, 'description': 'Brioche burger buns', 'quantity': 5, 'unit': 'each', 'unit_price': '7.20', 'gst_applicable': False, 'line_total': '36.00', 'line_gst': None}`
- `inv_0100` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 2, 'unit_price_cents': 5890, 'line_total_cents': 11780}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '117.80', 'line_gst': None}`
- `inv_0102` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Bin Liners 120L Carton 200', 'quantity': 3, 'unit_price_cents': 5890, 'line_total_cents': 17670}`, got `{'supplier_code': 'C-BIN', 'description': 'Bin Liners 120L Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '58.90', 'gst_applicable': True, 'line_total': '176.70', 'line_gst': None}`
  - line 2: expected `{'description': 'Paper Hand Towel Carton 16', 'quantity': 3, 'unit_price_cents': 6490, 'line_total_cents': 19470}`, got `{'supplier_code': 'C-TWL', 'description': 'Paper Hand Towel Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '64.90', 'gst_applicable': True, 'line_total': '194.70', 'line_gst': None}`
- `inv_0103` (template D, extracted, attempts 1)
  - line 1: expected `{'description': 'Batteries AA 24pk', 'quantity': 7, 'unit_price_cents': 2190, 'line_total_cents': 15330}`, got `{'supplier_code': 'X-BAA', 'description': 'Batteries AA 24pk 7 pack', 'quantity': 21.9, 'unit': None, 'unit_price': '153.30', 'gst_applicable': True, 'line_total': '23.00', 'line_gst': '2.30'}`
  - line 2: expected `{'description': 'Shower Head Low Flow', 'quantity': 9, 'unit_price_cents': 4590, 'line_total_cents': 41310}`, got `{'supplier_code': 'X-SHW', 'description': 'Shower Head Low Flow 9 each', 'quantity': 45.9, 'unit': None, 'unit_price': '413.10', 'gst_applicable': True, 'line_total': '61.97', 'line_gst': '6.20'}`
  - line 3: expected `{'description': 'LED Globe 9W E27', 'quantity': 16, 'unit_price_cents': 590, 'line_total_cents': 9440}`, got `{'supplier_code': 'X-LED9', 'description': 'LED Globe 9W E27 16 each', 'quantity': 5.9, 'unit': None, 'unit_price': '94.40', 'gst_applicable': True, 'line_total': '14.16', 'line_gst': '1.42'}`
  - line 4: expected `{'description': 'Lubricant Spray 300g', 'quantity': 7, 'unit_price_cents': 1290, 'line_total_cents': 9030}`, got `{'supplier_code': 'X-LUB', 'description': 'Lubricant Spray 300g 7 can', 'quantity': 12.9, 'unit': None, 'unit_price': '90.30', 'gst_applicable': True, 'line_total': '13.55', 'line_gst': '1.36'}`
- `inv_0105` (template B, extracted, attempts 1)
  - line 1: expected `{'description': 'Slippers Disposable Carton 100', 'quantity': 3, 'unit_price_cents': 12900, 'line_total_cents': 38700}`, got `{'supplier_code': 'A-SLP', 'description': 'Slippers Disposable Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '129.00', 'gst_applicable': True, 'line_total': '387.00', 'line_gst': None}`
  - line 2: expected `{'description': 'Dental Kit Carton 250', 'quantity': 2, 'unit_price_cents': 13750, 'line_total_cents': 27500}`, got `{'supplier_code': 'A-DEN', 'description': 'Dental Kit Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '137.50', 'gst_applicable': True, 'line_total': '275.00', 'line_gst': None}`
  - line 3: expected `{'description': 'Conditioner 30ml Carton 300', 'quantity': 3, 'unit_price_cents': 18900, 'line_total_cents': 56700}`, got `{'supplier_code': 'A-CON', 'description': 'Conditioner 30ml Carton', 'quantity': 3, 'unit': 'carton', 'unit_price': '189.00', 'gst_applicable': True, 'line_total': '567.00', 'line_gst': None}`
  - line 4: expected `{'description': 'Shampoo 30ml Carton 300', 'quantity': 2, 'unit_price_cents': 18900, 'line_total_cents': 37800}`, got `{'supplier_code': 'A-SHM', 'description': 'Shampoo 30ml Carton', 'quantity': 2, 'unit': 'carton', 'unit_price': '189.00', 'gst_applicable': True, 'line_total': '378.00', 'line_gst': None}`
