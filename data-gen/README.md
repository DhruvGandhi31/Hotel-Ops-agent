# data-gen

Synthetic suppliers, purchase orders, goods receiving and invoice PDFs, with ground truth for
every invoice. No real data, ever: business names are invented, emails use the reserved
`.example` domain, phone numbers are in ACMA's fictitious `5550 xxxx` range, and ABNs have a
valid checksum but are random.

```bash
python data-gen/generate.py --n 200 --seed 42   # -> data-gen/out/ (gitignored)
bash scripts/seed.sh                            # load master data into the ops database
```

The same `(n, seed)` always produces byte-identical output, PDFs included. The gate dataset's
distribution is committed at [`docs/synthetic-data-report.md`](../docs/synthetic-data-report.md)
and a test fails if it goes stale.

## Output

| Path | What |
|---|---|
| `invoices/inv_NNNN.pdf` | invoice PDFs with a real text layer, numbered in arrival order |
| `ground_truth/inv_NNNN.json` | what is printed + what reconciliation should conclude |
| `master_data.json` | suppliers, POs, receipts (same content as `seed.sql`) |
| `seed.sql` | master data for the `ops` database; invoices are **not** seeded |
| `manifest.json` | per-file summary, seed, quotas |
| `REPORT.md` | label distribution and coverage |

## Ground truth contract

`document` is exactly what is printed on the PDF: the extraction target for P2. Money is integer
cents ex GST, dates are ISO, `po_number` is as printed (one supplier prints `PO004512` rather
than `PO-004512`) or `null` when absent, and `quantity` is a number.

`labels` is what P3 reconciliation should conclude:

| Field | Meaning |
|---|---|
| `reason_codes` | flag reasons; non-empty means `expected_status` is `flag` |
| `conditions` | labelled situations that must **not** flag on their own |
| `expected_status` | `flag`, `recommend_approve`, or `null` for exact re-sends |
| `expected_ingestion` | `insert`, or `noop` for a byte-identical re-send |
| `true_po_number` | canonical PO, including when it's missing from the document |
| `duplicate_of`, `exact_resend_of` | the file this one copies |
| `gst` | method (`invoice` or `line`), taxable subtotal, correct/stated GST, tolerance, error variant |
| `lines[]` | per line: matching PO line, issues, PO/received quantity, PO price, true GST status |

**Reason codes.** `price_variance` (unit price more than 2% above the PO), `short_delivery`
(invoiced more than was received), `duplicate_invoice` (same supplier ABN, invoice number and
total as an earlier file but different bytes), `unknown_po` (printed PO doesn't exist),
`missing_po` (no PO printed), `gst_miscalculated` (stated GST is off from 10% of the taxable
subtotal by more than the rounding tolerance).

**Conditions.** `description_mismatch` (line worded differently from the PO, no item code
printed: the fuzzy-matching case), `partial_delivery` (less received than ordered, invoice bills
what arrived), `price_within_tolerance` (price differs by at most 1%), `exact_resend`.

**Rules the labels follow**

- Ingestion happens in file order. A copy always arrives after its original, and only the copy
  is labelled `duplicate_invoice`.
- GST correctness is judged against the PO's GST status, not the invoice's own markers (one
  error variant marks a GST-free item as taxable). The tolerance is `ceil(taxable_lines / 2)`
  cents, because suppliers that round per line can drift half a cent per line.
- `unknown_po` and `missing_po` never co-occur with line-level codes, since there is no PO to
  check against.

`tests/test_labels.py` re-derives every label from the written files with plain rules, so the
ground truth is checked independently of the code that injected it.
