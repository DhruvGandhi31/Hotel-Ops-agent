# Synthetic dataset report

Generator v1, seed `43`, n `200`, price tolerance 2.0%, GST 10%

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
| `description_mismatch` | 12% | 24 | 26 | 13.3% |
| `partial_delivery` | 6% | 12 | 13 | 6.6% |
| `price_within_tolerance` | 6% | 12 | 12 | 6.1% |
| `exact_resend` | 2% | 4 | not scored | |

## Expected outcomes

- `recommend_approve`: 128
- `flag`: 68 (8 with two or more reason codes)
- ingestion no-op (exact re-send): 4

GST error variants: arithmetic_error 6, gst_on_gst_free 7, wrong_rate 3

## Coverage

| Template | Files |
|---|---:|
| A | 39 |
| B | 53 |
| C | 55 |
| D | 53 |

| Supplier | Category | Template | Files |
|---|---|---|---:|
| Greenleaf Fresh Produce Pty Ltd | produce | A | 11 |
| Southern Cross Butchery Pty Ltd | meat | D | 19 |
| Coastal Creamery Co | dairy | B | 19 |
| Tidewater Seafood Traders | seafood | C | 16 |
| Wattle Street Bakehouse | bakery | C | 17 |
| Ironbark Pantry Wholesale Pty Ltd | dry_goods | D | 15 |
| Copper Still Liquor Merchants | liquor | A | 12 |
| Brightwash Hygiene Supplies | cleaning | B | 22 |
| Bayside Linen & Laundry Services | linen | A | 16 |
| Lilly Pilly Guest Amenities | amenities | B | 12 |
| Steadfast Maintenance Supplies | maintenance | D | 19 |
| Morning Ridge Coffee Roasters | coffee | C | 22 |

- Lines per invoice: min 1, max 10, mean 4.6
- Invoices mixing GST-free and taxable lines: 68
- Invoices with per-line GST rounding (template D): 48

## Master data (seed.sql)

- Suppliers: 12
- Purchase orders: 194 (18 never invoiced)
- PO lines: 878
- Receipts: 194
