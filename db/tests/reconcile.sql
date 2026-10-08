-- Tests for reconcile_candidates / reconcile_invoice (migration 006). Plain SQL, no framework:
--   bash scripts/test-db.sh
-- One transaction, rolled back, so it leaves the database as it found it. A failed ASSERT aborts.
--
-- Fixture: one supplier, one PO with five lines, one receipt.
--   line 1  A1  Tomatoes Roma 10kg Box   10 x $32.00  GST-free   received 10
--   line 2  B2  Olive Oil 4L              4 x $45.90  taxable    received 4
--   line 3  C3  Napkins Linen           100 x $0.45   taxable    received 80   (short)
--   line 4  D4  Mystery Tin               2 x $50.00  taxable    received 2
--   line 5  E5  Extra Item                3 x $10.00  taxable    never delivered (no receipt line)

BEGIN;

INSERT INTO suppliers (abn, name, category, payment_terms_days) VALUES ('99999999995', 'Fixture Foods', 'test', 14);
INSERT INTO suppliers (abn, name, category, payment_terms_days) VALUES ('88888888885', 'Other Supplier', 'test', 14);

INSERT INTO purchase_orders (po_number, supplier_id, order_date)
  SELECT 'PO-009001', id, '2026-07-01' FROM suppliers WHERE abn = '99999999995';
INSERT INTO purchase_orders (po_number, supplier_id, order_date)   -- nothing received for this one
  SELECT 'PO-009002', id, '2026-07-01' FROM suppliers WHERE abn = '99999999995';
INSERT INTO purchase_orders (po_number, supplier_id, order_date)   -- a different supplier's PO
  SELECT 'PO-009003', id, '2026-07-01' FROM suppliers WHERE abn = '88888888885';

INSERT INTO purchase_order_lines (purchase_order_id, line_no, supplier_sku, description, unit, quantity, unit_price_cents, gst_applicable)
  SELECT po.id, v.n, v.sku, v.descr, v.unit, v.qty, v.price, v.gst
    FROM purchase_orders po,
         (VALUES (1, 'A1', 'Tomatoes Roma 10kg Box', 'box', 10, 3200, false),
                 (2, 'B2', 'Olive Oil 4L', 'tin', 4, 4590, true),
                 (3, 'C3', 'Napkins Linen', 'each', 100, 45, true),
                 (4, 'D4', 'Mystery Tin', 'tin', 2, 5000, true),
                 (5, 'E5', 'Extra Item', 'each', 3, 1000, true)) AS v (n, sku, descr, unit, qty, price, gst)
   WHERE po.po_number = 'PO-009001';
INSERT INTO purchase_order_lines (purchase_order_id, line_no, supplier_sku, description, unit, quantity, unit_price_cents, gst_applicable)
  SELECT id, 1, 'A1', 'Tomatoes Roma 10kg Box', 'box', 10, 3200, false FROM purchase_orders WHERE po_number = 'PO-009002';
INSERT INTO purchase_order_lines (purchase_order_id, line_no, supplier_sku, description, unit, quantity, unit_price_cents, gst_applicable)
  SELECT id, 1, 'Z9', 'Other Goods', 'each', 5, 1000, true FROM purchase_orders WHERE po_number = 'PO-009003';

INSERT INTO receipts (receipt_number, purchase_order_id, received_date, received_by)
  SELECT 'GRN-T-1', id, '2026-07-03', 'tester' FROM purchase_orders WHERE po_number = 'PO-009001';
INSERT INTO receipt_lines (receipt_id, purchase_order_line_id, quantity_received)
  SELECT r.id, pol.id, v.q
    FROM receipts r JOIN purchase_order_lines pol ON pol.purchase_order_id = r.purchase_order_id
    JOIN (VALUES (1, 10), (2, 4), (3, 80), (4, 2)) AS v (n, q) ON v.n = pol.line_no
   WHERE r.receipt_number = 'GRN-T-1';

-- ---- helpers ---------------------------------------------------------------------------------
CREATE TEMP SEQUENCE file_seq;

CREATE FUNCTION pg_temp.dollars(cents bigint) RETURNS text LANGUAGE sql AS $$
  SELECT (cents / 100)::text || '.' || lpad((cents % 100)::text, 2, '0') $$;

-- one invoice line: code (or null), description, quantity, unit price in cents, GST marker printed
CREATE FUNCTION pg_temp.ln(code text, descr text, qty numeric, price bigint, gst boolean) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('supplier_code', code, 'description', descr, 'quantity', qty, 'unit', 'each',
           'unit_price', pg_temp.dollars(price), 'gst_applicable', gst,
           'line_total', pg_temp.dollars(round(qty * price)::bigint), 'line_gst', NULL) $$;

-- Ingest an invoice built from lines, as a fresh file each time. GST is 10% of the lines marked
-- taxable unless overridden; the subtotal is the sum of the printed line totals unless overridden.
CREATE FUNCTION pg_temp.mk(abn text, inv_no text, po text, lines jsonb,
                           gst_override bigint DEFAULT NULL, subtotal_override bigint DEFAULT NULL) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE sub bigint; tax bigint; g bigint; res jsonb;
BEGIN
  SELECT sum(round((l ->> 'line_total')::numeric * 100)::bigint),
         COALESCE(sum(round((l ->> 'line_total')::numeric * 100)::bigint) FILTER (WHERE (l ->> 'gst_applicable')::boolean), 0)
    INTO sub, tax FROM jsonb_array_elements(lines) l;
  g := COALESCE(gst_override, round(tax * 0.1)::bigint);
  sub := COALESCE(subtotal_override, sub);
  res := ingest_invoice(jsonb_build_object(
    'file_sha256', lpad(nextval('file_seq')::text, 64, '0'), 'filename', inv_no || '.pdf', 'attempts', 1, 'llm_model', 'test',
    'extraction', jsonb_build_object(
      'supplier_name', 'Fixture', 'supplier_abn', abn, 'bill_to_name', 'Hotel', 'invoice_number', inv_no,
      'invoice_date', '2026-07-05', 'due_date', NULL, 'po_number', po, 'currency', 'AUD', 'lines', lines,
      'subtotal', pg_temp.dollars(sub), 'gst', pg_temp.dollars(g), 'total', pg_temp.dollars(sub + g))));
  RETURN (res ->> 'invoice_id')::bigint;
END $$;

-- Invoices accumulate against PO lines (that is the cumulative rule), so each independent scenario
-- starts from none of this test's invoices.
CREATE FUNCTION pg_temp.reset() RETURNS void LANGUAGE sql AS $$
  DELETE FROM invoices WHERE invoice_number LIKE 'T-%' $$;

-- A scenario of one invoice, from nothing to the reconciliation result.
CREATE FUNCTION pg_temp.one(inv_no text, abn text, po text, lines jsonb, gst_override bigint DEFAULT NULL,
                            subtotal_override bigint DEFAULT NULL, matches jsonb DEFAULT '[]') RETURNS jsonb
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_temp.reset();
  RETURN reconcile_invoice(pg_temp.mk(abn, inv_no, po, lines, gst_override, subtotal_override), matches);
END $$;

CREATE FUNCTION pg_temp.codes(r jsonb) RETURNS text[] LANGUAGE sql AS $$
  SELECT COALESCE(array_agg(x ORDER BY x), '{}') FROM jsonb_array_elements_text(r -> 'reason_codes') x $$;
CREATE FUNCTION pg_temp.review(r jsonb) RETURNS text[] LANGUAGE sql AS $$
  SELECT COALESCE(array_agg(x ORDER BY x), '{}') FROM jsonb_array_elements_text(r -> 'review_reasons') x $$;
CREATE FUNCTION pg_temp.line_of(r jsonb, n int, field text) RETURNS text LANGUAGE sql AS $$
  SELECT x ->> field FROM jsonb_array_elements(r -> 'lines') x WHERE (x ->> 'line_no')::int = n $$;

DO $$
DECLARE
  abn text := '99999999995';
  v_id bigint; v_id2 bigint; r jsonb; c jsonb;
  clean jsonb := jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, false),
                                   pg_temp.ln('B2', 'Olive Oil 4L', 4, 4590, true));
BEGIN
  -- ===== a clean invoice ======================================================================
  PERFORM pg_temp.reset();
  v_id := pg_temp.mk(abn, 'T-CLEAN', 'PO-009001', clean);
  r := reconcile_invoice(v_id);
  ASSERT r ->> 'status' = 'recommend_approve', 'clean invoice approves: ' || r::text;
  ASSERT pg_temp.codes(r) = '{}' AND pg_temp.review(r) = '{}', 'no findings';
  ASSERT pg_temp.line_of(r, 1, 'match_method') = 'sku', 'matched by SKU';
  ASSERT (r -> 'gst' ->> 'basis') = 'po', 'GST judged against the PO';
  ASSERT (r -> 'gst' ->> 'expected_cents')::bigint = 1836, 'GST is 10% of the taxable line only';
  ASSERT (SELECT count(*) FROM audit_log WHERE entity_id = v_id::text AND action = 'invoice.reconciled') = 1, 'audit row';

  -- ===== idempotent: running again replaces the result ===========================================
  r := reconcile_invoice(v_id);
  ASSERT (SELECT count(*) FROM reconciliations WHERE invoice_id = v_id) = 1, 'one result row per invoice';
  ASSERT (SELECT count(*) FROM reconciliation_lines rl JOIN reconciliations x ON x.id = rl.reconciliation_id WHERE x.invoice_id = v_id) = 2,
    'lines replaced, not duplicated';
  ASSERT (SELECT count(*) FROM audit_log WHERE entity_id = v_id::text AND action = 'invoice.reconciled') = 2, 'every run is audited';

  -- ===== price: tolerance is "more than 2% above" ================================================
  -- PO line 4 costs 5000 cents: 2% = 100 cents. Exactly 5100 is allowed; 5101 is not.
  r := pg_temp.one('T-P-EQ', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('D4', 'Mystery Tin', 2, 5100, true)));
  ASSERT pg_temp.codes(r) = '{}', 'exactly 2% above is within tolerance: ' || r::text;
  r := pg_temp.one('T-P-OVER', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('D4', 'Mystery Tin', 2, 5101, true)));
  ASSERT pg_temp.codes(r) = ARRAY['price_variance'] AND r ->> 'status' = 'flag', 'one cent past 2% flags: ' || r::text;
  ASSERT (SELECT price_variance_pct FROM reconciliation_lines WHERE reconciliation_id = (r ->> 'reconciliation_id')::bigint) = 2.020,
    'the variance is recorded';
  r := pg_temp.one('T-P-LOW', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('D4', 'Mystery Tin', 2, 2500, true)));
  ASSERT pg_temp.codes(r) = '{}', 'a price far BELOW the PO is not flagged (only overcharges are)';
  r := pg_temp.one('T-P-WITHIN', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 4, 4659, true)));
  ASSERT pg_temp.codes(r) = '{}', '1.5% above is within tolerance';

  -- ===== quantity against receiving ===============================================================
  r := pg_temp.one('T-Q-SHORT', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('C3', 'Napkins Linen', 100, 45, true)));
  ASSERT pg_temp.codes(r) = ARRAY['short_delivery'] AND r ->> 'status' = 'flag', 'billing 100 when 80 arrived flags: ' || r::text;
  ASSERT (SELECT received_quantity FROM reconciliation_lines WHERE reconciliation_id = (r ->> 'reconciliation_id')::bigint) = 80, 'received recorded';
  r := pg_temp.one('T-Q-PARTIAL', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('C3', 'Napkins Linen', 80, 45, true)));
  ASSERT pg_temp.codes(r) = '{}' AND r ->> 'status' = 'recommend_approve', 'billing exactly what arrived is fine: ' || r::text;
  r := pg_temp.one('T-Q-NEVER', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('E5', 'Extra Item', 3, 1000, true)));
  ASSERT pg_temp.codes(r) = ARRAY['short_delivery'], 'billing a PO line that never arrived is short: ' || r::text;

  -- ===== quantity is cumulative across invoices on the same PO line ==============================
  PERFORM pg_temp.reset();
  v_id := pg_temp.mk(abn, 'T-C-1', 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 3, 4590, true)));
  r := reconcile_invoice(v_id);
  ASSERT pg_temp.codes(r) = '{}', 'first partial invoice (3 of 4 received) is fine';
  v_id2 := pg_temp.mk(abn, 'T-C-2', 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 2, 4590, true)));
  r := reconcile_invoice(v_id2);
  ASSERT pg_temp.codes(r) = ARRAY['short_delivery'], 'the second takes the PO line past what was received (3 + 2 > 4): ' || r::text;
  ASSERT (SELECT billed_before FROM reconciliation_lines WHERE reconciliation_id = (r ->> 'reconciliation_id')::bigint) = 3, 'billed_before recorded';

  -- ===== duplicates ==============================================================================
  PERFORM pg_temp.reset();
  v_id := pg_temp.mk(abn, 'T-DUP', 'PO-009001', clean);
  r := reconcile_invoice(v_id);
  ASSERT r ->> 'status' = 'recommend_approve', 'the original approves';
  v_id2 := pg_temp.mk(abn, 'T-DUP', 'PO-009001', clean);   -- same invoice, different file
  ASSERT (SELECT duplicate_of FROM invoices WHERE id = v_id2) = v_id, 'ingest linked the copy to the original';
  r := reconcile_invoice(v_id2);
  ASSERT pg_temp.codes(r) = ARRAY['duplicate_invoice'] AND r ->> 'status' = 'flag',
    'the copy is flagged as a duplicate and nothing else: ' || r::text;
  r := reconcile_invoice(pg_temp.mk(abn, 'T-DUP', 'PO-009001', clean));
  ASSERT pg_temp.codes(r) = ARRAY['duplicate_invoice'], 'a third copy too';
  -- duplicates must not count as billed quantity: line 1 had 10 received and 10 billed once
  r := reconcile_invoice(pg_temp.mk(abn, 'T-AFTER', 'PO-009001', jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 0.5, 3200, false))));
  ASSERT (SELECT billed_before FROM reconciliation_lines rl JOIN reconciliations x ON x.id = rl.reconciliation_id
           WHERE x.id = (r ->> 'reconciliation_id')::bigint) = 10, 'billed_before is 10, not 30: the copies are not counted';

  -- ===== PO resolution ============================================================================
  r := pg_temp.one('T-PO-NORM', abn, 'po 009001', clean);
  ASSERT r ->> 'purchase_order' = 'PO-009001' AND r ->> 'status' = 'recommend_approve', 'PO numbers compare ignoring case and punctuation: ' || r::text;
  r := pg_temp.one('T-PO-UNK', abn, 'PO-009999', clean);
  ASSERT pg_temp.codes(r) = ARRAY['unknown_po'] AND r ->> 'status' = 'flag', 'an unknown PO is flagged: ' || r::text;
  r := pg_temp.one('T-PO-NONE', abn, NULL, clean);
  ASSERT pg_temp.codes(r) = ARRAY['missing_po'] AND r ->> 'status' = 'flag', 'a missing PO is flagged: ' || r::text;
  r := pg_temp.one('T-PO-EMPTY', abn, '   ', clean);
  ASSERT pg_temp.codes(r) = ARRAY['missing_po'], 'a blank PO counts as missing';
  ASSERT (SELECT count(*) FROM reconciliation_lines WHERE reconciliation_id = (r ->> 'reconciliation_id')::bigint) = 2,
    'lines are still recorded without a PO';

  -- ===== GST ======================================================================================
  -- one B2 line: 45.90, correct GST 4.59
  r := pg_temp.one('T-G-OK1', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 1, 4590, true)), 460);
  ASSERT pg_temp.codes(r) = '{}', '1 cent off is within the rounding allowance for one taxable line: ' || r::text;
  r := pg_temp.one('T-G-OK2', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 1, 4590, true)), 461);
  ASSERT pg_temp.codes(r) = ARRAY['gst_miscalculated'], '2 cents off is flagged';
  r := pg_temp.one('T-G-RATE', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('B2', 'Olive Oil 4L', 4, 4590, true)), 2754);
  ASSERT pg_temp.codes(r) = ARRAY['gst_miscalculated'], 'a 15% rate is flagged';
  ASSERT (r -> 'gst' ->> 'expected_cents')::bigint = 1836 AND (r -> 'gst' ->> 'stated_cents')::bigint = 2754, 'expected and stated are reported';
  -- the invoice marks the GST-free tomatoes taxable and charges GST on them: only the PO knows better
  r := pg_temp.one('T-G-FREE', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, true)));
  ASSERT pg_temp.codes(r) = ARRAY['gst_miscalculated'], 'GST on a GST-free item is caught because the PO decides: ' || r::text;
  -- with no PO the invoice's own markers are all there is, and the basis says so
  r := pg_temp.one('T-G-NOPO', abn, NULL, jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, true)));
  ASSERT pg_temp.codes(r) = ARRAY['missing_po'] AND (r -> 'gst' ->> 'basis') = 'invoice', 'with no PO the GST basis is the invoice: ' || r::text;
  -- more taxable lines allow more drift: 5 lines, ceil(2.5) = 3 cents
  r := pg_temp.one('T-G-N5', abn, 'PO-009001', jsonb_build_array(
         pg_temp.ln('B2', 'Olive Oil 4L', 1, 4590, true), pg_temp.ln('C3', 'Napkins Linen', 1, 45, true),
         pg_temp.ln('D4', 'Mystery Tin', 1, 5000, true), pg_temp.ln('E5', 'Extra Item', 1, 1000, true),
         pg_temp.ln(NULL, 'something else', 1, 100, true)), 1077);   -- correct is round(10735 x 0.1) = 1074; stated 3 cents over
  ASSERT (r -> 'gst' ->> 'tolerance_cents')::int = 3 AND (r -> 'gst' ->> 'taxable_lines')::int = 5, 'tolerance scales with taxable lines: ' || r::text;

  -- ===== line matching ============================================================================
  r := pg_temp.one('T-M-DESC', abn, 'PO-009001', jsonb_build_array(pg_temp.ln(NULL, 'tomatoes ROMA, 10kg box!', 10, 3200, false)));
  ASSERT pg_temp.line_of(r, 1, 'match_method') = 'description' AND r ->> 'status' = 'recommend_approve',
    'a normalised description matches when no code is printed: ' || r::text;

  -- a reworded line with no code: unmatched, so a human looks, unless a match is supplied
  PERFORM pg_temp.reset();
  v_id := pg_temp.mk(abn, 'T-M-LLM', 'PO-009001', jsonb_build_array(
          pg_temp.ln('B2', 'Olive Oil 4L', 4, 4590, true), pg_temp.ln(NULL, 'Roma Toms 10kg', 10, 3200, false)));
  r := reconcile_invoice(v_id);
  ASSERT r ->> 'status' = 'needs_review' AND pg_temp.review(r) = ARRAY['unmatched_line'], 'a reworded line without a match needs review: ' || r::text;
  c := reconcile_candidates(v_id);
  ASSERT jsonb_array_length(c -> 'pending_lines') = 1 AND (c -> 'pending_lines' -> 0 ->> 'line_no') = '2', 'one line is pending: ' || c::text;
  ASSERT (SELECT array_agg((x ->> 'po_line_no')::int ORDER BY (x ->> 'po_line_no')::int) FROM jsonb_array_elements(c -> 'candidates') x) = ARRAY[1, 3, 4, 5],
    'candidates are the PO lines not already matched';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 1, "confidence": 0.9}]');
  ASSERT r ->> 'status' = 'recommend_approve' AND pg_temp.line_of(r, 2, 'match_method') = 'llm', 'a confident match is used: ' || r::text;
  ASSERT (SELECT match_confidence FROM reconciliation_lines WHERE reconciliation_id = (r ->> 'reconciliation_id')::bigint AND match_method = 'llm') = 0.9,
    'the confidence is kept';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 1, "confidence": 0.85}]');
  ASSERT r ->> 'status' = 'recommend_approve', 'a confidence exactly at the threshold is accepted';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 1, "confidence": 0.84}]');
  ASSERT r ->> 'status' = 'needs_review' AND pg_temp.review(r) = ARRAY['low_confidence_match'], 'just below the threshold is not trusted';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": null, "confidence": 0.99}]');
  ASSERT pg_temp.review(r) = ARRAY['unmatched_line'], 'the model saying "not on the PO" leaves the line unmatched';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 2, "confidence": 0.99}]');
  ASSERT pg_temp.review(r) = ARRAY['unmatched_line'], 'a PO line already matched by SKU cannot be claimed again';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 77, "confidence": 0.99}]');
  ASSERT pg_temp.review(r) = ARRAY['unmatched_line'], 'a PO line that does not exist is unusable';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 2, "po_line_no": 1}]');
  ASSERT pg_temp.review(r) = ARRAY['low_confidence_match'], 'a match without a confidence is not trusted';
  r := reconcile_invoice(v_id, '[{"invoice_line_no": 1, "po_line_no": 3, "confidence": 0.99}, {"invoice_line_no": 2, "po_line_no": 1, "confidence": 0.99}]');
  ASSERT r ->> 'status' = 'recommend_approve' AND pg_temp.line_of(r, 1, 'match_method') = 'sku',
    'a model proposal for a line already matched by SKU is ignored: ' || r::text;

  -- two invoice lines carrying the same code cannot both claim one PO line
  r := pg_temp.one('T-M-TWICE', abn, 'PO-009001', jsonb_build_array(
         pg_temp.ln('B2', 'Olive Oil 4L', 2, 4590, true), pg_temp.ln('B2', 'Olive Oil 4L', 2, 4590, true)));
  ASSERT pg_temp.review(r) = ARRAY['unmatched_line'], 'the second line with the same code is unmatched: ' || r::text;

  -- a wrongly matched line is still checked like any other: this one is priced at 4x the PO line it was matched to
  r := pg_temp.one('T-M-WRONG', abn, 'PO-009001', jsonb_build_array(pg_temp.ln(NULL, 'Mystery oil', 4, 20000, true)), NULL, NULL,
                   '[{"invoice_line_no": 1, "po_line_no": 2, "confidence": 0.95}]');
  ASSERT pg_temp.codes(r) = ARRAY['price_variance'], 'matched lines are checked: ' || r::text;

  -- ===== flag and review together =================================================================
  r := pg_temp.one('T-BOTH', abn, 'PO-009001', jsonb_build_array(
         pg_temp.ln('D4', 'Mystery Tin', 2, 9000, true), pg_temp.ln(NULL, 'Something reworded', 1, 100, true)));
  ASSERT r ->> 'status' = 'flag' AND pg_temp.codes(r) = ARRAY['price_variance'] AND pg_temp.review(r) = ARRAY['unmatched_line'],
    'a flagged invoice still reports what could not be matched: ' || r::text;

  -- ===== defensive findings: needs_review, never flag ===========================================
  r := pg_temp.one('T-SUP', '77777777775', 'PO-009001', clean);
  ASSERT r ->> 'status' = 'needs_review' AND pg_temp.review(r) = ARRAY['supplier_unknown'], 'an ABN that is not a known supplier needs review: ' || r::text;
  r := pg_temp.one('T-SUPMIS', '88888888885', 'PO-009001', clean);
  ASSERT pg_temp.review(r) = ARRAY['po_supplier_mismatch'], 'a PO that belongs to another supplier needs review: ' || r::text;
  r := pg_temp.one('T-NOGRN', abn, 'PO-009002', jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, false)));
  ASSERT r ->> 'status' = 'needs_review' AND pg_temp.review(r) = ARRAY['no_receiving_record'], 'nothing received yet, so quantity cannot be checked: ' || r::text;
  r := pg_temp.one('T-OVERPO', abn, 'PO-009001', jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 11, 3200, false)));
  ASSERT 'quantity_exceeds_po' = ANY (pg_temp.review(r)), 'billing more than was ordered needs review: ' || r::text;
  r := pg_temp.one('T-LINEARITH', abn, 'PO-009001',
         jsonb_build_array(jsonb_set(pg_temp.ln('B2', 'Olive Oil 4L', 4, 4590, true), '{line_total}', '"190.00"')));
  ASSERT 'line_arithmetic' = ANY (pg_temp.review(r)), 'a line total that is not quantity x price needs review: ' || r::text;
  r := pg_temp.one('T-TOTALS', abn, 'PO-009001', clean, NULL, 99999);
  ASSERT 'totals_arithmetic' = ANY (pg_temp.review(r)), 'a subtotal that is not the sum of the lines needs review: ' || r::text;

  -- ===== the result row obeys its own constraints; an unknown invoice fails loudly ==============
  BEGIN
    PERFORM reconcile_invoice(-1);
    RAISE EXCEPTION 'reconciled an invoice that does not exist';
  EXCEPTION WHEN no_data_found THEN NULL;
  END;
  ASSERT NOT EXISTS (SELECT 1 FROM reconciliations WHERE status = 'flag' AND cardinality(reason_codes) = 0), 'a flag always has a reason';
  ASSERT NOT EXISTS (SELECT 1 FROM reconciliations WHERE status = 'recommend_approve' AND (cardinality(reason_codes) + cardinality(review_reasons)) > 0),
    'an approval has no findings';

  RAISE NOTICE 'reconcile.sql: all assertions passed';
END;
$$;

ROLLBACK;
