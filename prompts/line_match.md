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
