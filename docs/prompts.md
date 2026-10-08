# Prompts

Every LLM prompt used by a workflow is mirrored here so changes diff cleanly in PRs.
`evals/tests/test_prompts_mirror.py` fails if a block below differs from its source file, so edit the
source file first, then copy it into the block between the BEGIN/END markers.

## Invoice extraction

- Source of truth: [`prompts/invoice_extraction.md`](../prompts/invoice_extraction.md)
- Used by: the extraction eval (`evals/run.py`) and, from P2 workflow onwards, the
  `Extract Invoice Fields` step of the ingestion workflow
- Output contract: [`schemas/invoice_extraction.json`](../schemas/invoice_extraction.json), validated in code
- `{{SCHEMA}}` is replaced at run time by the schema with its `description` fields removed
  (the prose is already in the rules above it)
- Retry (one only): see the next section; a single user message, not extra chat turns

<!-- BEGIN prompts/invoice_extraction.md -->
````markdown
You extract structured data from the text of one supplier tax invoice, issued to a hotel. The text was taken from a PDF, so columns and labels may appear in a different order from the page.

Return a single JSON object that follows the schema at the end of this message. Return JSON only: no commentary, no markdown fences.

## Transcribe; do not calculate

Copy every value exactly as it is printed. Never recalculate a total, correct a tax amount, or fill in something the document does not show. If the printed GST or total looks wrong, copy the wrong figure. A number that does not appear on the invoice must never appear in your output.

## Where things are

- The seller is the business that issued the invoice (its ABN is printed near its name and address). The customer is the hotel: it appears under "Bill To", "Bill to" or "Sold To". Never swap them.
- `invoice_number` is the number labelled "Invoice No", "Invoice #", "Inv #" or "Tax Invoice No." Do not use the customer's PO number, the "Account No", or the "Delivery Docket" number.
- `po_number` is the customer's purchase order number. The label varies: "Customer PO", "Your Order No.", "Order Ref", "Purchase Order". Copy it exactly as printed (for example `PO-004512` or `PO004512`). If no purchase order number is printed anywhere, use null. Never invent one.
- Words stamped across the page such as COPY, RE-ISSUED or REMINDER are not data. Ignore them.
- Dates: Australian documents write day first. `03/08/2026` is 3 August 2026. Output `YYYY-MM-DD`.

## Lines

- Output one object per line item, in the order printed. Do not output subtotal, GST, total, terms or payment rows as lines.
- `supplier_code` is the item code printed for the line, if any. Otherwise null.
- `description` is the item description only: leave out the item code and any tax marker symbol (`^` or `*`).
- `unit` is the unit printed for the line (each, kg, box, carton, bag...), in lowercase. A unit that is part of the quantity text counts (`2.420 kg` gives kg). If no unit is printed for the line, use null. Do not infer one from the product: a till-style line such as `4 x 9.94` has no unit, so its unit is null, never `each`.
- `quantity` is the number of units as printed (weighed goods keep their decimals).
- `unit_price` and `line_total` are dollar amounts ex GST with two decimals, as strings. No `$` and no thousands separator: `1,300.67` becomes `"1300.67"`.
- `gst_applicable` comes from the document's own marker: a Tax column showing GST (true) or FREE (false); a `^` or `*` next to the item (true), no marker (false); on invoices with a per-line GST column, true when that line's GST is above 0.00. Do not decide it from what the item is.
- `line_gst` is the GST amount printed for that line, only on invoices that have a per-line GST column. Copy it, including `"0.00"` when the column shows 0.00 (never null in that case). On invoices without such a column it is always null: never calculate it yourself.

## Totals

`subtotal` is the total ex GST, `gst` the total GST, `total` the amount payable including GST, all exactly as printed, as strings with two decimals.

## Schema

{{SCHEMA}}
````
<!-- END prompts/invoice_extraction.md -->

## Invoice extraction: retry message

- Source of truth: [`prompts/invoice_extraction_retry.md`](../prompts/invoice_extraction_retry.md)
- Sent as the user message of the second and last attempt, with the same system prompt. One message
  rather than extra chat turns, because the n8n chain node takes a single prompt and the workflow must
  send exactly what the eval measured.
- Placeholders: `{{INVOICE_TEXT}}`, `{{PREVIOUS_OUTPUT}}`, `{{ERRORS}}`

<!-- BEGIN prompts/invoice_extraction_retry.md -->
````markdown
Invoice text:

{{INVOICE_TEXT}}

Your previous answer was:

{{PREVIOUS_OUTPUT}}

It failed validation:
{{ERRORS}}

Return the corrected JSON object only. Re-read the invoice text above; change only what the errors require, and do not guess.
````
<!-- END prompts/invoice_extraction_retry.md -->

## Line matching (P3)

- Source of truth: [`prompts/line_match.md`](../prompts/line_match.md)
- Used by: the `Match Lines` step of the `Reconcile Invoice` workflow, only for invoice lines that have no
  usable item code and no exact description match (about 5% of lines)
- Output contract: [`schemas/line_match.json`](../schemas/line_match.json), validated in code, then checked that
  every asked line is answered exactly once and only offered PO lines are chosen
- `{{SCHEMA}}` is replaced at run time by the schema with its `description` fields removed
- The user message lists the invoice lines to match and the PO lines still unmatched; it is built by the
  workflow's `Build Match Prompt` node and by `evals/hotel_evals/match.py`, held identical by a test
- A match below the confidence threshold (0.85, `reconciliation_settings`) is not used: the invoice goes to
  `needs_review`

<!-- BEGIN prompts/line_match.md -->
````markdown
You match lines on a supplier invoice to the lines of the hotel's purchase order (PO). Each invoice line below could not be matched by its item code or by an exact description, so it is worded differently from the PO. Decide which PO line each one is.

Return a single JSON object that follows the schema at the end of this message. Return JSON only: no commentary, no markdown fences.

## Rules

- Give one entry for every invoice line listed, using its line number exactly as listed.
- `po_line_no` is the number of a PO line from the list of PO lines still unmatched, and only from that list. If none of them is the same product, use null. Do not force a match.
- Two lines are the same product when they describe the same item, even if the wording differs: different word order, abbreviations, plural or singular, or the pack size written another way. "Roma Toms 10kg" and "Tomatoes Roma 10kg Box" are the same product; "Roma Toms 10kg" and "Cherry Tomatoes 1kg Punnet" are not.
- Quantity and price are supporting evidence only. An invoice line can be the right product at a different price or quantity than the PO, because that is checked separately: do not reject a match merely because the price differs. But a candidate that is clearly a different product is wrong even if its price and quantity match.
- Each PO line can be used for at most one invoice line.
- `confidence` is a number from 0 to 1. Use 0.95 or more when the wording clearly describes the same product, 0.7 to 0.9 when it is probably the same, and below 0.5 when you are unsure. If two PO lines both fit, give a low confidence rather than guessing.
- Do not invent line numbers.

## Schema

{{SCHEMA}}
````
<!-- END prompts/line_match.md -->

## Line matching: retry message

- Source of truth: [`prompts/line_match_retry.md`](../prompts/line_match_retry.md)
- Sent as the user message of the second and last attempt, with the same system prompt: the original request,
  the failed answer and the numbered errors
- Placeholders: `{{CONTEXT}}`, `{{PREVIOUS_OUTPUT}}`, `{{ERRORS}}`

<!-- BEGIN prompts/line_match_retry.md -->
````markdown
{{CONTEXT}}

Your previous answer was:

{{PREVIOUS_OUTPUT}}

It failed validation:
{{ERRORS}}

Return the corrected JSON object only. Re-read the lines above; change only what the errors require, and do not guess.
````
<!-- END prompts/line_match_retry.md -->
