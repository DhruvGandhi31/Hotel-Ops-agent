-- Tests for the approval functions (migration 007). Plain SQL, no framework:
--   bash scripts/test-db.sh
-- One transaction, rolled back, so it leaves the database as it found it. A failed ASSERT aborts.
--
-- A decided approval can never be changed or deleted (that is a rule under test), so scenarios cannot clean up
-- after themselves. Each scenario gets its own purchase order instead, which also keeps the cumulative
-- quantity rule from coupling them.

BEGIN;

INSERT INTO suppliers (abn, name, category, payment_terms_days) VALUES ('99999999995', 'Fixture Foods', 'test', 14);

CREATE TEMP SEQUENCE file_seq;
CREATE TEMP SEQUENCE po_seq;

CREATE FUNCTION pg_temp.dollars(cents bigint) RETURNS text LANGUAGE sql AS $$
  SELECT (cents / 100)::text || '.' || lpad((cents % 100)::text, 2, '0') $$;

CREATE FUNCTION pg_temp.ln(code text, descr text, qty numeric, price bigint, gst boolean) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('supplier_code', code, 'description', descr, 'quantity', qty, 'unit', 'each',
           'unit_price', pg_temp.dollars(price), 'gst_applicable', gst,
           'line_total', pg_temp.dollars(round(qty * price)::bigint), 'line_gst', NULL) $$;

-- A fresh PO "PO-T<n>" with three lines, all fully received:
--   1 A1 Tomatoes Roma 10kg Box 10 x $32.00 GST-free; 2 B2 Olive Oil 4L 4 x $45.90 taxable; 3 D4 Mystery Tin 2 x $50.00 taxable
CREATE FUNCTION pg_temp.new_po() RETURNS text LANGUAGE plpgsql AS $$
DECLARE po text := 'PO-T' || nextval('po_seq')::text;
BEGIN
  INSERT INTO purchase_orders (po_number, supplier_id, order_date)
    SELECT po, id, '2026-07-01' FROM suppliers WHERE abn = '99999999995';
  INSERT INTO purchase_order_lines (purchase_order_id, line_no, supplier_sku, description, unit, quantity, unit_price_cents, gst_applicable)
    SELECT p.id, v.n, v.sku, v.descr, v.unit, v.qty, v.price, v.gst
      FROM purchase_orders p,
           (VALUES (1, 'A1', 'Tomatoes Roma 10kg Box', 'box', 10, 3200, false),
                   (2, 'B2', 'Olive Oil 4L', 'tin', 4, 4590, true),
                   (3, 'D4', 'Mystery Tin', 'tin', 2, 5000, true)) AS v (n, sku, descr, unit, qty, price, gst)
     WHERE p.po_number = po;
  INSERT INTO receipts (receipt_number, purchase_order_id, received_date, received_by)
    SELECT 'GRN-' || po, id, '2026-07-03', 'tester' FROM purchase_orders WHERE po_number = po;
  INSERT INTO receipt_lines (receipt_id, purchase_order_line_id, quantity_received)
    SELECT r.id, pol.id, pol.quantity
      FROM receipts r JOIN purchase_order_lines pol ON pol.purchase_order_id = r.purchase_order_id
     WHERE r.receipt_number = 'GRN-' || po;
  RETURN po;
END $$;

CREATE FUNCTION pg_temp.mk(inv_no text, po text, lines jsonb) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE sub bigint; tax bigint; g bigint; res jsonb;
BEGIN
  SELECT sum(round((l ->> 'line_total')::numeric * 100)::bigint),
         COALESCE(sum(round((l ->> 'line_total')::numeric * 100)::bigint) FILTER (WHERE (l ->> 'gst_applicable')::boolean), 0)
    INTO sub, tax FROM jsonb_array_elements(lines) l;
  g := round(tax * 0.1)::bigint;
  res := ingest_invoice(jsonb_build_object(
    'file_sha256', lpad(nextval('file_seq')::text, 64, '0'), 'filename', inv_no || '.pdf', 'attempts', 1, 'llm_model', 'test',
    'extraction', jsonb_build_object(
      'supplier_name', 'Fixture', 'supplier_abn', '99999999995', 'bill_to_name', 'Hotel', 'invoice_number', inv_no,
      'invoice_date', '2026-07-05', 'due_date', NULL, 'po_number', po, 'currency', 'AUD', 'lines', lines,
      'subtotal', pg_temp.dollars(sub), 'gst', pg_temp.dollars(g), 'total', pg_temp.dollars(sub + g))));
  RETURN (res ->> 'invoice_id')::bigint;
END $$;

-- invoice -> reconciled (and nothing else); returns the invoice id
CREATE FUNCTION pg_temp.reconciled(inv_no text, lines jsonb) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE v_id bigint := pg_temp.mk(inv_no, pg_temp.new_po(), lines);
BEGIN
  PERFORM reconcile_invoice(v_id);
  RETURN v_id;
END $$;

CREATE FUNCTION pg_temp.clean() RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, false), pg_temp.ln('B2', 'Olive Oil 4L', 4, 4590, true)) $$;
CREATE FUNCTION pg_temp.overpriced() RETURNS jsonb LANGUAGE sql AS $$   -- D4 at 5101 against 5000: price_variance
  SELECT jsonb_build_array(pg_temp.ln('D4', 'Mystery Tin', 2, 5101, true)) $$;
CREATE FUNCTION pg_temp.unmatched() RETURNS jsonb LANGUAGE sql AS $$   -- no code, no known description: needs_review
  SELECT jsonb_build_array(pg_temp.ln('A1', 'Tomatoes Roma 10kg Box', 10, 3200, false), pg_temp.ln(NULL, 'Something nobody ordered', 1, 1000, true)) $$;

CREATE FUNCTION pg_temp.ctx(uid text DEFAULT 'user-1') RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('user_id', uid, 'workflow_name', 'Approval Form', 'workflow_id', 'wf-1', 'execution_id', '42') $$;

CREATE FUNCTION pg_temp.audit_count(v_id bigint, act text) RETURNS bigint LANGUAGE sql AS $$
  SELECT count(*) FROM audit_log WHERE entity_type = 'invoice' AND entity_id = v_id::text AND action = act $$;

DO $$
DECLARE
  v_id bigint; v_flag bigint; v_rev bigint; v_other bigint;
  r jsonb; a bigint; caught boolean;
BEGIN
  -- ===== requesting ============================================================================
  v_id := pg_temp.mk('T-NOREC', pg_temp.new_po(), pg_temp.clean());
  r := request_approval(v_id);
  ASSERT r ->> 'result' = 'error' AND r ->> 'reason' = 'not_reconciled', 'no approval before reconciliation: ' || r::text;
  ASSERT NOT EXISTS (SELECT 1 FROM approvals WHERE invoice_id = v_id), 'and no row was created';

  v_id := pg_temp.reconciled('T-CLEAN', pg_temp.clean());
  r := request_approval(v_id, pg_temp.ctx());
  ASSERT r ->> 'result' = 'created' AND r ->> 'approval_status' = 'pending' AND r ->> 'recommended_status' = 'recommend_approve',
    'a clean invoice gets a pending approval: ' || r::text;
  a := (r ->> 'approval_id')::bigint;
  ASSERT pg_temp.audit_count(v_id, 'approval.requested') = 1, 'the request is audited';
  ASSERT (SELECT actor FROM audit_log WHERE entity_id = v_id::text AND action = 'approval.requested') = 'Approval Form',
    'with the workflow as actor';

  r := request_approval(v_id, pg_temp.ctx());
  ASSERT r ->> 'result' = 'unchanged' AND (r ->> 'approval_id')::bigint = a, 'requesting again changes nothing: ' || r::text;
  ASSERT (SELECT count(*) FROM approvals WHERE invoice_id = v_id) = 1, 'still one approval per invoice';
  ASSERT pg_temp.audit_count(v_id, 'approval.requested') = 1, 'and nothing more is audited';

  -- ===== a clean recommendation can be approved without a comment ================================
  r := record_approval_decision(a, 'approved', 'ada@example.com', NULL, pg_temp.ctx('user-7'));
  ASSERT r ->> 'result' = 'recorded' AND r ->> 'decision' = 'approved', 'approved: ' || r::text;
  ASSERT (SELECT status FROM approvals WHERE id = a) = 'approved', 'row is approved';
  ASSERT (SELECT decided_by FROM approvals WHERE id = a) = 'ada@example.com'
     AND (SELECT decided_by_user_id FROM approvals WHERE id = a) = 'user-7'
     AND (SELECT decided_at FROM approvals WHERE id = a) IS NOT NULL, 'who and when are stored';
  ASSERT pg_temp.audit_count(v_id, 'approval.decided') = 1, 'the decision is audited';
  ASSERT (SELECT actor FROM audit_log WHERE entity_id = v_id::text AND action = 'approval.decided') = 'ada@example.com',
    'with the approver as actor';
  ASSERT (SELECT details ->> 'recommended_status' FROM audit_log WHERE entity_id = v_id::text AND action = 'approval.decided')
         = 'recommend_approve', 'and what they were shown';
  ASSERT (SELECT details ->> 'invoice_number' FROM audit_log WHERE entity_id = v_id::text AND action = 'approval.decided')
         = 'T-CLEAN', 'and which invoice';

  -- ===== a decision is final =====================================================================
  r := record_approval_decision(a, 'rejected', 'bob@example.com', 'changed my mind', pg_temp.ctx('user-8'));
  ASSERT r ->> 'result' = 'already_decided' AND r ->> 'decision' = 'approved' AND r ->> 'decided_by' = 'ada@example.com',
    'a second decision is refused and reports the first: ' || r::text;
  ASSERT (SELECT status FROM approvals WHERE id = a) = 'approved', 'the first decision stands';
  ASSERT pg_temp.audit_count(v_id, 'approval.decided') = 1, 'the refused attempt writes no decision row';

  caught := false;
  BEGIN UPDATE approvals SET status = 'rejected', comment = 'x' WHERE id = a;
  EXCEPTION WHEN SQLSTATE '23000' THEN caught := true; END;
  ASSERT caught, 'a decided approval cannot be updated';
  caught := false;
  BEGIN DELETE FROM approvals WHERE id = a;
  EXCEPTION WHEN SQLSTATE '23000' THEN caught := true; END;
  ASSERT caught, 'a decided approval cannot be deleted';

  -- request_approval on a decided invoice neither reopens nor duplicates it
  r := request_approval(v_id);
  ASSERT r ->> 'result' = 'decided' AND (r ->> 'stale')::boolean = false AND r ->> 'approval_status' = 'approved',
    'requesting after a decision reports it: ' || r::text;

  -- ===== overriding a flag needs a reason ========================================================
  v_flag := pg_temp.reconciled('T-FLAG', pg_temp.overpriced());
  r := request_approval(v_flag);
  ASSERT r ->> 'recommended_status' = 'flag', 'a price variance is flagged: ' || r::text;
  a := (r ->> 'approval_id')::bigint;
  ASSERT (SELECT reason_codes FROM approvals WHERE id = a) = ARRAY['price_variance'], 'with its reason codes';

  r := record_approval_decision(a, 'approved', 'ada@example.com', NULL);
  ASSERT r ->> 'result' = 'comment_required', 'approving a flag without a reason is refused: ' || r::text;
  r := record_approval_decision(a, 'approved', 'ada@example.com', E'  \n ');
  ASSERT r ->> 'result' = 'comment_required', 'a blank reason does not count: ' || r::text;
  ASSERT (SELECT status FROM approvals WHERE id = a) = 'pending', 'still pending after refusals';
  ASSERT pg_temp.audit_count(v_flag, 'approval.decided') = 0, 'refusals are not decisions';
  r := record_approval_decision(a, 'approved', 'ada@example.com', '  Supplier agreed a surcharge by phone.  ');
  ASSERT r ->> 'result' = 'recorded', 'approving with a reason works: ' || r::text;
  ASSERT (SELECT comment FROM approvals WHERE id = a) = 'Supplier agreed a surcharge by phone.', 'the reason is trimmed and stored';
  ASSERT (SELECT details ->> 'comment' FROM audit_log WHERE entity_id = v_flag::text AND action = 'approval.decided')
         = 'Supplier agreed a surcharge by phone.', 'and audited';

  -- ===== rejection always needs a reason =========================================================
  v_id := pg_temp.reconciled('T-REJ', pg_temp.clean());
  a := (request_approval(v_id) ->> 'approval_id')::bigint;
  r := record_approval_decision(a, 'rejected', 'ada@example.com', NULL);
  ASSERT r ->> 'result' = 'comment_required', 'rejecting a clean invoice without a reason is refused: ' || r::text;
  r := record_approval_decision(a, 'rejected', 'ada@example.com', 'Not what we ordered.');
  ASSERT r ->> 'result' = 'recorded' AND (SELECT status FROM approvals WHERE id = a) = 'rejected', 'rejected with a reason: ' || r::text;

  -- ===== doubt (needs_review) also needs a reason to approve ======================================
  v_rev := pg_temp.reconciled('T-REV', pg_temp.unmatched());
  r := request_approval(v_rev);
  ASSERT r ->> 'recommended_status' = 'needs_review', 'an unmatched line is needs_review: ' || r::text;
  a := (r ->> 'approval_id')::bigint;
  ASSERT (SELECT review_reasons FROM approvals WHERE id = a) = ARRAY['unmatched_line'], 'with its review reason';
  ASSERT (record_approval_decision(a, 'approved', 'ada@example.com', NULL) ->> 'result') = 'comment_required',
    'approving a needs_review invoice without a reason is refused';
  ASSERT (record_approval_decision(a, 'approved', 'ada@example.com', 'Checked by phone.') ->> 'result') = 'recorded', 'and works with one';

  -- ===== bad input is refused, nothing is applied ===============================================
  v_other := pg_temp.reconciled('T-BAD', pg_temp.clean());
  a := (request_approval(v_other) ->> 'approval_id')::bigint;
  ASSERT (record_approval_decision(a, 'maybe', 'ada@example.com', 'x') ->> 'result') = 'invalid', 'unknown decision word';
  ASSERT (record_approval_decision(a, NULL, 'ada@example.com', 'x') ->> 'result') = 'invalid', 'missing decision';
  ASSERT (record_approval_decision(a, 'approved', NULL, 'x') ->> 'result') = 'invalid', 'missing approver';
  ASSERT (record_approval_decision(a, 'approved', '   ', 'x') ->> 'result') = 'invalid', 'blank approver';
  ASSERT (record_approval_decision(-5, 'approved', 'ada@example.com', 'x') ->> 'result') = 'not_found', 'unknown approval';
  ASSERT (SELECT status FROM approvals WHERE id = a) = 'pending', 'none of those changed anything';
  ASSERT pg_temp.audit_count(v_other, 'approval.decided') = 0, 'or audited anything';

  -- ===== table constraints hold even for direct writes ===========================================
  -- (each insert targets an invoice with no approval yet, so only the snapshot rule can refuse it)
  v_id := pg_temp.reconciled('T-SNAP', pg_temp.clean());
  caught := false;
  BEGIN INSERT INTO approvals (invoice_id, recommended_status, reason_codes) VALUES (v_id, 'flag', '{}');
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a flag snapshot without reason codes is rejected';
  caught := false;
  BEGIN INSERT INTO approvals (invoice_id, recommended_status, reason_codes) VALUES (v_id, 'recommend_approve', ARRAY['price_variance']);
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a clean snapshot that carries reason codes is rejected';
  caught := false;
  BEGIN INSERT INTO approvals (invoice_id, recommended_status) VALUES (v_id, 'needs_review');
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a needs_review snapshot without review reasons is rejected';
  INSERT INTO approvals (invoice_id, recommended_status) VALUES (v_id, 'recommend_approve');   -- the valid shape still works
  caught := false;
  BEGIN UPDATE approvals SET status = 'rejected', decided_at = now(), decided_by = 'x', comment = NULL WHERE id = a;
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a rejection with a NULL comment is rejected by the table itself (a NULL CHECK result would pass)';
  caught := false;
  BEGIN UPDATE approvals SET status = 'rejected', decided_at = now(), decided_by = 'x', comment = E'\n\t ' WHERE id = a;
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'and so is one that is only whitespace';
  caught := false;
  BEGIN UPDATE approvals SET status = 'rejected', decided_at = now(), decided_by = NULL, comment = 'reason' WHERE id = a;
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a decision with no approver is rejected by the table itself';
  caught := false;
  BEGIN UPDATE approvals SET status = 'approved', decided_at = NULL, decided_by = 'x' WHERE id = a;
  EXCEPTION WHEN check_violation THEN caught := true; END;
  ASSERT caught, 'a decision without a time is rejected by the table itself';

  -- ===== a pending request follows the latest reconciliation =====================================
  -- v_other is pending and clean. Make its price tolerance tighter so a re-run flags it.
  v_id := pg_temp.reconciled('T-REFRESH', jsonb_build_array(pg_temp.ln('D4', 'Mystery Tin', 2, 5060, true)));  -- 1.2% above
  a := (request_approval(v_id) ->> 'approval_id')::bigint;
  ASSERT (SELECT recommended_status FROM approvals WHERE id = a) = 'recommend_approve', 'within tolerance at first';
  UPDATE reconciliation_settings SET value = 1.0 WHERE key = 'price_tolerance_pct';
  PERFORM reconcile_invoice(v_id);
  r := request_approval(v_id);
  ASSERT r ->> 'result' = 'refreshed' AND r ->> 'recommended_status' = 'flag', 'a re-run that flags it refreshes the pending approval: ' || r::text;
  ASSERT (SELECT reason_codes FROM approvals WHERE id = a) = ARRAY['price_variance'], 'reason codes follow';
  ASSERT pg_temp.audit_count(v_id, 'approval.refreshed') = 1, 'the refresh is audited';
  ASSERT (SELECT details -> 'before' ->> 'recommended_status' FROM audit_log WHERE entity_id = v_id::text AND action = 'approval.refreshed')
         = 'recommend_approve', 'with what it was before';
  ASSERT (record_approval_decision(a, 'approved', 'ada@example.com', NULL) ->> 'result') = 'comment_required',
    'and the comment rule now applies, because the person is deciding on a flag';

  -- ===== a decided approval is flagged stale if the reconciliation later disagrees =================
  UPDATE reconciliation_settings SET value = 2.0 WHERE key = 'price_tolerance_pct';
  PERFORM reconcile_invoice(v_id);
  r := request_approval(v_id);
  ASSERT r ->> 'result' = 'refreshed' AND r ->> 'recommended_status' = 'recommend_approve', 'back to clean: ' || r::text;
  ASSERT (record_approval_decision(a, 'approved', 'ada@example.com', NULL) ->> 'result') = 'recorded', 'approved';
  UPDATE reconciliation_settings SET value = 1.0 WHERE key = 'price_tolerance_pct';
  PERFORM reconcile_invoice(v_id);
  r := request_approval(v_id);
  ASSERT r ->> 'result' = 'decided' AND (r ->> 'stale')::boolean AND r ->> 'approval_status' = 'approved',
    'the decision stands but is reported stale: ' || r::text;
  ASSERT pg_temp.audit_count(v_id, 'approval.stale') = 1, 'and the staleness is audited';
  ASSERT (SELECT status FROM approvals WHERE id = a) = 'approved', 'the decision was not touched';
  UPDATE reconciliation_settings SET value = 2.0 WHERE key = 'price_tolerance_pct';

  -- ===== what the approver reads =================================================================
  r := approval_detail((SELECT id FROM approvals WHERE invoice_id = v_flag));
  ASSERT r -> 'invoice' ->> 'invoice_number' = 'T-FLAG' AND r -> 'invoice' ->> 'supplier_name' = 'Fixture Foods', 'detail: invoice header';
  ASSERT r -> 'approval' ->> 'recommended_status' = 'flag', 'detail: verdict';
  ASSERT jsonb_array_length(r -> 'lines') = 1, 'detail: lines';
  ASSERT r -> 'lines' -> 0 ->> 'po_line_no' = '3' AND r -> 'lines' -> 0 ->> 'match_method' = 'sku', 'detail: the PO line it matched';
  ASSERT r -> 'lines' -> 0 -> 'issues' ? 'price_variance', 'detail: the issue on the line';
  ASSERT (r -> 'lines' -> 0 ->> 'po_unit_price_cents')::bigint = 5000 AND (r -> 'lines' -> 0 ->> 'unit_price_cents')::bigint = 5101,
    'detail: both prices';
  ASSERT r ->> 'purchase_order' LIKE 'PO-T%', 'detail: the purchase order';
  ASSERT approval_detail(-1) IS NULL, 'no detail for an unknown approval';

  -- pending_approvals: flags before doubts before clean, only pending ones
  v_id := pg_temp.reconciled('T-Q-CLEAN', pg_temp.clean());      PERFORM request_approval(v_id);
  v_id := pg_temp.reconciled('T-Q-FLAG', pg_temp.overpriced());  PERFORM request_approval(v_id);
  v_id := pg_temp.reconciled('T-Q-REV', pg_temp.unmatched());    PERFORM request_approval(v_id);
  r := pending_approvals(100000);
  ASSERT (r ->> 'total')::int = (r ->> 'shown')::int, 'everything pending is shown within a large limit';
  ASSERT (SELECT array_agg(i ->> 'invoice_number' ORDER BY ord)
            FROM jsonb_array_elements(r -> 'items') WITH ORDINALITY t (i, ord)
           WHERE i ->> 'invoice_number' LIKE 'T-Q-%') = ARRAY['T-Q-FLAG', 'T-Q-REV', 'T-Q-CLEAN'],
    'order: flag, then needs_review, then recommend_approve: ' || r::text;
  ASSERT NOT EXISTS (SELECT 1 FROM jsonb_array_elements(r -> 'items') i WHERE i ->> 'invoice_number' IN ('T-CLEAN', 'T-REJ', 'T-FLAG')),
    'decided approvals are not listed';
  r := pending_approvals(0);
  ASSERT (r ->> 'shown')::int = 0 AND (r ->> 'total')::int >= 3 AND jsonb_array_length(r -> 'items') = 0, 'the limit caps what is shown, not the total';

  -- ===== the audit trail of one invoice, in order =================================================
  v_id := pg_temp.reconciled('T-TRAIL', pg_temp.clean());
  a := (request_approval(v_id) ->> 'approval_id')::bigint;
  PERFORM record_approval_decision(a, 'approved', 'ada@example.com', NULL);
  -- a row left by an older, reset database that happened to use the same invoice id
  INSERT INTO audit_log (occurred_at, actor, action, entity_type, entity_id)
    VALUES (now() - interval '400 days', 'old', 'invoice.ingested', 'invoice', v_id::text);
  ASSERT (SELECT array_agg(action ORDER BY audit_id) FROM invoice_audit_trail(v_id))
         = ARRAY['invoice.ingested', 'invoice.reconciled', 'approval.requested', 'approval.decided'],
    'the trail reads ingest, reconcile, request, decision and ignores older rows: '
    || (SELECT array_agg(action ORDER BY audit_id)::text FROM invoice_audit_trail(v_id));
  ASSERT (SELECT actor FROM invoice_audit_trail(v_id) WHERE action = 'approval.decided') = 'ada@example.com', 'with the approver named';
END $$;

ROLLBACK;
