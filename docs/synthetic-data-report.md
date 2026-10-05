# Synthetic dataset report

Generator v1, seed `42`, n `200`, price tolerance 2.0%, GST 10%

## Files

- Invoice PDFs: **200**
- Distinct invoices: 184
- Duplicates (same supplier + invoice number + total, different file): 12
- Exact re-sends (byte-identical file; ingestion must be a no-op): 4
- Scored files (all except exact re-sends): 196

Rates are targets turned into exact counts (`Injected` = round(rate x n)). A duplicate
carries the conditions of the invoice it copies, so a condition can appear on more files
than it was injected into.

## Flag reason codes

| Code | Target rate | Injected | Scored files carrying it | Share of scored files |
|---|---:|---:|---:|---:|
| `price_variance` | 10% | 20 | 20 | 10.2% |
| `short_delivery` | 8% | 16 | 16 | 8.2% |
| `duplicate_invoice` | 6% | 12 | 12 | 6.1% |
| `unknown_po` | 4% | 8 | 8 | 4.1% |
| `missing_po` | 3% | 6 | 6 | 3.1% |
| `gst_miscalculated` | 7% | 14 | 14 | 7.1% |

## Conditions (must not flag on their own)

| Code | Target rate | Injected | Scored files carrying it | Share of scored files |
|---|---:|---:|---:|---:|
| `description_mismatch` | 12% | 24 | 24 | 12.2% |
| `partial_delivery` | 6% | 12 | 14 | 7.1% |
| `price_within_tolerance` | 6% | 12 | 16 | 8.2% |
| `exact_resend` | 2% | 4 | not scored | |

## Expected outcomes

- `recommend_approve`: 125
- `flag`: 71 (5 with two or more reason codes)
- ingestion no-op (exact re-send): 4

GST error variants: arithmetic_error 3, gst_on_gst_free 5, wrong_rate 6

## Coverage

| Template | Files |
|---|---:|
| A | 49 |
| B | 47 |
| C | 51 |
| D | 53 |

| Supplier | Category | Template | Files |
|---|---|---|---:|
| Greenleaf Fresh Produce Pty Ltd | produce | A | 17 |
| Southern Cross Butchery Pty Ltd | meat | D | 25 |
| Coastal Creamery Co | dairy | B | 12 |
| Tidewater Seafood Traders | seafood | C | 23 |
| Wattle Street Bakehouse | bakery | C | 16 |
| Ironbark Pantry Wholesale Pty Ltd | dry_goods | D | 10 |
| Copper Still Liquor Merchants | liquor | A | 14 |
| Brightwash Hygiene Supplies | cleaning | B | 16 |
| Bayside Linen & Laundry Services | linen | A | 18 |
| Lilly Pilly Guest Amenities | amenities | B | 19 |
| Steadfast Maintenance Supplies | maintenance | D | 18 |
| Morning Ridge Coffee Roasters | coffee | C | 12 |

- Lines per invoice: min 1, max 10, mean 4.5
- Invoices mixing GST-free and taxable lines: 62
- Invoices with per-line GST rounding (template D): 47

## Master data (seed.sql)

- Suppliers: 12
- Purchase orders: 194 (18 never invoiced)
- PO lines: 869
- Receipts: 194
