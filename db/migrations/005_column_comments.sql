-- Say in the schema which extracted columns are not trustworthy. The model reads these off the PDF
-- unreliably (measured in evals/results), and they are not part of the P2 accuracy targets.

COMMENT ON COLUMN invoice_lines.gst_cents IS
  'Per-line GST as the model extracted it. UNRELIABLE: it sometimes invents a value on layouts that print no per-line GST, and returns null instead of 0.00 where they do. Do not use for checks; reconcile GST from line totals and the PO GST status.';

COMMENT ON COLUMN invoice_lines.unit IS
  'Unit of measure as the model extracted it. Unreliable on layouts that print no unit (it tends to say "each"). Do not use for matching; quantity and unit price are the checked fields.';

COMMENT ON COLUMN invoice_lines.gst_applicable IS
  'GST marker read from the document by the model, about 90% accurate in evals. Reconciliation must judge GST against the purchase order line, not this flag.';

COMMENT ON TABLE invoices IS
  'One row per validated extraction. Gate-checked fields: invoice_number, po_number, total (and the other header amounts). duplicate_of links a re-issued invoice to the first one.';
