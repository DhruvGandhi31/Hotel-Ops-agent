# Reconciliation eval

- Run: 2026-10-06 18:09 UTC
- Mode: **matcher**, ground-truth extraction; the model matches the lines that need it (through n8n)
- Dataset: `data-gen\out-gate3` (seed 44); invoices reconciled: **196** (exact re-sends are ingestion no-ops and are not reconciled)
- Rules and tolerances: gst_rate_pct = 10, quantity_epsilon = 0.0005, price_tolerance_pct = 2.0, line_match_min_confidence = 0.85, line_total_tolerance_cents = 1, gst_rounding_cents_per_taxable_line = 0.5
- Model for line matching and extraction: `qwen3.5:9b`

> **Read this first.** The synthetic labels and the reconciler share rules (the generator and the
> reconciler both encode the 2% price tolerance and the half-cent-per-line GST allowance), so a
> near-perfect *rules-only* score shows the rules are implemented as specified, not that they suit a
> real hotel. The honest signal is the gap between the modes: what extraction and line matching errors
> cost on top of the rules.

## The `flag` class

| Measure | Result |
|---|---:|
| Precision (flagged invoices that truly had a discrepancy) | 100.0% (71/71) |
| Recall (truly flagged invoices that were flagged) | 100.0% (71/71), 95% CI 94.9 to 100.0% |
| Caught or escalated to a human (flag or needs_review) | 100.0% |
| Truly flagged but **approved** | 0 |
| Clean invoices correctly recommended for approval | 100.0% |
| Invoices sent to `needs_review` | 0 of 196 |

Status, expected (rows) against recommended (columns):

| Expected \ got | recommend_approve | flag | needs_review |
|---|---:|---:|---:|
| flag | 0 | 71 | 0 |
| recommend_approve | 125 | 0 | 0 |

## Precision and recall per discrepancy type

| Reason code | Expected | Predicted | TP | FP | FN | Precision | Recall | Recall 95% CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `price_variance` | 20 | 20 | 20 | 0 | 0 | 100.0% | 100.0% | 83.9 to 100.0% |
| `short_delivery` | 16 | 16 | 16 | 0 | 0 | 100.0% | 100.0% | 80.6 to 100.0% |
| `duplicate_invoice` | 12 | 12 | 12 | 0 | 0 | 100.0% | 100.0% | 75.7 to 100.0% |
| `unknown_po` | 8 | 8 | 8 | 0 | 0 | 100.0% | 100.0% | 67.6 to 100.0% |
| `missing_po` | 6 | 6 | 6 | 0 | 0 | 100.0% | 100.0% | 61.0 to 100.0% |
| `gst_miscalculated` | 14 | 13 | 13 | 0 | 1 | 100.0% | 92.9% | 68.5 to 98.7% |

Exact reason-code set correct on 195 of 196 invoices; on invoices with two or more true reasons, 4 of 5. Small counts (for example `missing_po`) make recall figures coarse: read the interval.

## Conditions that must not flag on their own

Invoices that carry one of these and no true discrepancy:

| Condition | Invoices | Wrongly flagged | needs_review | Correctly approved |
|---|---:|---:|---:|---:|
| `description_mismatch` | 17 | 0 | 0 | 17 |
| `partial_delivery` | 7 | 0 | 0 | 7 |
| `price_within_tolerance` | 6 | 0 | 0 | 6 |

## Line matching

Lines that truly belong to a PO line: 819; matched to the right one: 819 (100.0%).

| Method | Lines | Correct | Wrong match | Not matched |
|---|---:|---:|---:|---:|
| description | 175 | 175 | 0 | 0 |
| llm | 46 | 46 | 0 | 0 |
| sku | 598 | 598 | 0 | 0 |

## Mismatches (1 invoices with a different code set or needs_review; first 1)

- `inv_0162`: expected ['gst_miscalculated', 'missing_po'], got ['missing_po'], status `flag`
