-- Human approval (P4): one approval per reconciled invoice, decided by a person, recorded in audit_log.
--
-- Nothing here approves, pays or sends anything. `recommend_approve` is still only a recommendation; an
-- `approvals` row going from pending to approved is the human step principle 3 requires, and downstream
-- systems (none yet) must read `approvals.status`, never `reconciliations.status`.
--
-- Rules live here, in SQL, tested by db/tests/approvals.sql:
--   * one approval per invoice; request_approval() is idempotent and keeps a pending request in step with
--     the latest reconciliation;
--   * a decision is final (a trigger refuses to change or delete a decided row);
--   * approving a `flag` or `needs_review` invoice, or rejecting any invoice, needs a written reason;
--   * a second decision on the same approval is refused and reported, never applied;
--   * every request and every decision is written to the append-only audit_log in the same transaction.
-- The approver is passed in by the workflow from the authenticated n8n user (Form Trigger n8nUserAuth).

-- Text with leading and trailing whitespace removed. Postgres btrim() alone removes only spaces, so a reason of
-- a tab or a line break would count as written; this also removes tabs, line breaks, form feeds, vertical
-- tabs, non-breaking and zero-width spaces.
CREATE FUNCTION approval_trim(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT btrim(t, ' ' || chr(9) || chr(10) || chr(11) || chr(12) || chr(13) || chr(160) || chr(8203))
$$;

CREATE TABLE approvals (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  -- No ON DELETE CASCADE: a decision must never disappear because an invoice row was deleted.
  invoice_id         bigint NOT NULL UNIQUE REFERENCES invoices (id),
  status             text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
  -- What the person is shown. Kept in step with the latest reconciliation while pending, frozen on decision.
  recommended_status text NOT NULL CHECK (recommended_status IN ('recommend_approve', 'flag', 'needs_review')),
  reason_codes       text[] NOT NULL DEFAULT '{}',
  review_reasons     text[] NOT NULL DEFAULT '{}',
  requested_at       timestamptz NOT NULL DEFAULT now(),
  decided_at         timestamptz,
  decided_by         text,       -- the approver's identity (n8n user email)
  decided_by_user_id text,       -- the approver's n8n user id
  comment            text,
  CONSTRAINT approvals_snapshot_consistent CHECK (
       (recommended_status = 'recommend_approve' AND cardinality(reason_codes) = 0 AND cardinality(review_reasons) = 0)
    OR (recommended_status = 'flag' AND cardinality(reason_codes) > 0)
    OR (recommended_status = 'needs_review' AND cardinality(reason_codes) = 0 AND cardinality(review_reasons) > 0)),
  CONSTRAINT approvals_decision_complete CHECK (
       (status = 'pending' AND decided_at IS NULL AND decided_by IS NULL AND decided_by_user_id IS NULL
          AND comment IS NULL)
    OR (status <> 'pending' AND decided_at IS NOT NULL AND COALESCE(length(approval_trim(decided_by)), 0) > 0)),
  -- Approving a clean recommendation needs no explanation; overriding a flag or a doubt, or any rejection, does.
  -- COALESCE matters: a CHECK that evaluates to NULL passes, so a NULL comment would otherwise slip through.
  CONSTRAINT approvals_comment_when_required CHECK (
       status = 'pending'
    OR (status = 'approved' AND recommended_status = 'recommend_approve')
    OR COALESCE(length(approval_trim(comment)), 0) > 0)
);

CREATE INDEX approvals_status_idx ON approvals (status, requested_at);

CREATE FUNCTION approvals_reject_change_when_decided() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status <> 'pending' THEN
    RAISE EXCEPTION 'approval % is already % and cannot be changed or deleted', OLD.id, OLD.status
      USING ERRCODE = '23000';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER approvals_decided_are_final
  BEFORE UPDATE OR DELETE ON approvals
  FOR EACH ROW EXECUTE FUNCTION approvals_reject_change_when_decided();

-- ---------------------------------------------------------------------------------------------
-- request_approval(invoice_id, context): called after reconcile_invoice.
-- Returns {result: created | refreshed | unchanged | decided | error, approval_id, approval_status,
-- recommended_status, stale}. `stale` is true when the invoice was re-reconciled after a decision and the
-- result now differs from what the person saw; the decision stands and an audit row says so.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION request_approval(p_invoice_id bigint, p_context jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  rec     reconciliations%ROWTYPE;
  ap      approvals%ROWTYPE;
  v_actor text := COALESCE(p_context ->> 'workflow_name', 'system');
  v_same  boolean;
BEGIN
  SELECT * INTO rec FROM reconciliations WHERE invoice_id = p_invoice_id;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('result', 'error', 'reason', 'not_reconciled', 'invoice_id', p_invoice_id);
  END IF;

  INSERT INTO approvals (invoice_id, recommended_status, reason_codes, review_reasons)
  VALUES (p_invoice_id, rec.status, rec.reason_codes, rec.review_reasons)
  ON CONFLICT (invoice_id) DO NOTHING
  RETURNING * INTO ap;

  IF FOUND THEN
    INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
    VALUES (v_actor, 'approval.requested', 'invoice', p_invoice_id::text,
            p_context ->> 'workflow_id', p_context ->> 'execution_id',
            jsonb_build_object('approval_id', ap.id, 'recommended_status', ap.recommended_status,
                               'reason_codes', ap.reason_codes, 'review_reasons', ap.review_reasons));
    RETURN jsonb_build_object('result', 'created', 'approval_id', ap.id, 'approval_status', ap.status,
                              'recommended_status', ap.recommended_status, 'stale', false);
  END IF;

  SELECT * INTO ap FROM approvals WHERE invoice_id = p_invoice_id FOR UPDATE;
  v_same := ap.recommended_status = rec.status
        AND ARRAY(SELECT unnest(ap.reason_codes) ORDER BY 1) = ARRAY(SELECT unnest(rec.reason_codes) ORDER BY 1)
        AND ARRAY(SELECT unnest(ap.review_reasons) ORDER BY 1) = ARRAY(SELECT unnest(rec.review_reasons) ORDER BY 1);

  IF ap.status = 'pending' THEN
    IF v_same THEN
      RETURN jsonb_build_object('result', 'unchanged', 'approval_id', ap.id, 'approval_status', ap.status,
                                'recommended_status', ap.recommended_status, 'stale', false);
    END IF;
    UPDATE approvals
       SET recommended_status = rec.status, reason_codes = rec.reason_codes, review_reasons = rec.review_reasons
     WHERE id = ap.id;
    INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
    VALUES (v_actor, 'approval.refreshed', 'invoice', p_invoice_id::text,
            p_context ->> 'workflow_id', p_context ->> 'execution_id',
            jsonb_build_object('approval_id', ap.id,
                               'before', jsonb_build_object('recommended_status', ap.recommended_status,
                                  'reason_codes', ap.reason_codes, 'review_reasons', ap.review_reasons),
                               'after', jsonb_build_object('recommended_status', rec.status,
                                  'reason_codes', rec.reason_codes, 'review_reasons', rec.review_reasons)));
    RETURN jsonb_build_object('result', 'refreshed', 'approval_id', ap.id, 'approval_status', 'pending',
                              'recommended_status', rec.status, 'stale', false);
  END IF;

  -- Already decided: the decision stands. Say so if the reconciliation has moved on since.
  IF NOT v_same THEN
    INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
    VALUES (v_actor, 'approval.stale', 'invoice', p_invoice_id::text,
            p_context ->> 'workflow_id', p_context ->> 'execution_id',
            jsonb_build_object('approval_id', ap.id, 'decision', ap.status,
                               'decided_saw', jsonb_build_object('recommended_status', ap.recommended_status,
                                  'reason_codes', ap.reason_codes, 'review_reasons', ap.review_reasons),
                               'now', jsonb_build_object('recommended_status', rec.status,
                                  'reason_codes', rec.reason_codes, 'review_reasons', rec.review_reasons)));
  END IF;
  RETURN jsonb_build_object('result', 'decided', 'approval_id', ap.id, 'approval_status', ap.status,
                            'recommended_status', ap.recommended_status, 'stale', NOT v_same);
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- record_approval_decision(approval_id, decision, approver, comment, context)
-- decision is 'approved' or 'rejected'. Returns {result: recorded | already_decided | comment_required |
-- invalid | not_found, ...}; nothing is applied unless the result is `recorded`.
-- context carries user_id, workflow_id, workflow_name and execution_id from the workflow.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION record_approval_decision(
  p_approval_id bigint, p_decision text, p_approver text, p_comment text DEFAULT NULL,
  p_context jsonb DEFAULT '{}'::jsonb)
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  ap        approvals%ROWTYPE;
  v_comment text := NULLIF(approval_trim(COALESCE(p_comment, '')), '');
  v_approver text := NULLIF(approval_trim(COALESCE(p_approver, '')), '');
  inv       RECORD;
BEGIN
  IF p_decision IS NULL OR p_decision NOT IN ('approved', 'rejected') THEN
    RETURN jsonb_build_object('result', 'invalid', 'reason', 'decision must be approved or rejected');
  END IF;
  IF v_approver IS NULL THEN
    RETURN jsonb_build_object('result', 'invalid', 'reason', 'the approver is required');
  END IF;

  SELECT * INTO ap FROM approvals WHERE id = p_approval_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('result', 'not_found', 'approval_id', p_approval_id);
  END IF;

  IF ap.status <> 'pending' THEN
    RETURN jsonb_build_object('result', 'already_decided', 'approval_id', ap.id, 'decision', ap.status,
                              'decided_by', ap.decided_by, 'decided_at', ap.decided_at);
  END IF;

  IF (p_decision = 'rejected' OR ap.recommended_status <> 'recommend_approve') AND v_comment IS NULL THEN
    RETURN jsonb_build_object('result', 'comment_required', 'approval_id', ap.id,
                              'recommended_status', ap.recommended_status);
  END IF;

  UPDATE approvals
     SET status = p_decision, decided_at = now(), decided_by = v_approver,
         decided_by_user_id = p_context ->> 'user_id', comment = v_comment
   WHERE id = ap.id
  RETURNING * INTO ap;

  SELECT i.invoice_number, i.total_cents, COALESCE(s.name, i.supplier_name) AS supplier_name
    INTO inv
    FROM invoices i LEFT JOIN suppliers s ON s.id = i.supplier_id
   WHERE i.id = ap.invoice_id;

  INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
  VALUES (v_approver, 'approval.decided', 'invoice', ap.invoice_id::text,
          p_context ->> 'workflow_id', p_context ->> 'execution_id',
          jsonb_build_object('approval_id', ap.id, 'decision', ap.status, 'comment', ap.comment,
                             'recommended_status', ap.recommended_status, 'reason_codes', ap.reason_codes,
                             'review_reasons', ap.review_reasons, 'approver_user_id', ap.decided_by_user_id,
                             'invoice_number', inv.invoice_number, 'supplier_name', inv.supplier_name,
                             'total_cents', inv.total_cents, 'workflow_name', p_context ->> 'workflow_name'));

  RETURN jsonb_build_object('result', 'recorded', 'approval_id', ap.id, 'invoice_id', ap.invoice_id,
                            'decision', ap.status, 'decided_by', ap.decided_by, 'decided_at', ap.decided_at,
                            'recommended_status', ap.recommended_status);
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- What the approver reads. Pure reads.
-- ---------------------------------------------------------------------------------------------

-- Pending approvals, most urgent first (flag, then needs_review, then recommend_approve; oldest first).
-- {total, shown, items: [...]}; the list page shows at most p_limit.
CREATE FUNCTION pending_approvals(p_limit integer DEFAULT 100) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  WITH pending AS (
    SELECT a.id AS approval_id, a.invoice_id, a.recommended_status, a.reason_codes, a.review_reasons,
           a.requested_at, i.invoice_number, i.po_number, i.total_cents,
           COALESCE(s.name, i.supplier_name) AS supplier_name,
           row_number() OVER (ORDER BY CASE a.recommended_status
                                         WHEN 'flag' THEN 0 WHEN 'needs_review' THEN 1 ELSE 2 END,
                                       a.requested_at, a.id) AS rn
      FROM approvals a
      JOIN invoices i ON i.id = a.invoice_id
      LEFT JOIN suppliers s ON s.id = i.supplier_id
     WHERE a.status = 'pending')
  SELECT jsonb_build_object(
           'total', (SELECT count(*) FROM pending),
           'shown', (SELECT count(*) FROM pending WHERE rn <= p_limit),
           'items', COALESCE((SELECT jsonb_agg(to_jsonb(p) - 'rn' ORDER BY p.rn) FROM pending p WHERE p.rn <= p_limit),
                             '[]'::jsonb))
$$;

-- One approval with everything needed to decide it: the invoice, the verdict, and each line next to the PO
-- line it was matched to. NULL if there is no such approval.
CREATE FUNCTION approval_detail(p_approval_id bigint) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object(
    'approval', jsonb_build_object(
        'id', a.id, 'status', a.status, 'recommended_status', a.recommended_status,
        'reason_codes', a.reason_codes, 'review_reasons', a.review_reasons, 'requested_at', a.requested_at,
        'decided_at', a.decided_at, 'decided_by', a.decided_by, 'comment', a.comment),
    'invoice', jsonb_build_object(
        'id', i.id, 'invoice_number', i.invoice_number, 'invoice_date', i.invoice_date, 'due_date', i.due_date,
        'po_number', i.po_number, 'supplier_name', COALESCE(s.name, i.supplier_name),
        'supplier_abn', i.supplier_abn, 'subtotal_cents', i.subtotal_cents, 'gst_cents', i.gst_cents,
        'total_cents', i.total_cents,
        'duplicate_of_invoice_number', (SELECT d.invoice_number FROM invoices d WHERE d.id = i.duplicate_of)),
    'purchase_order', (SELECT po.po_number FROM purchase_orders po WHERE po.id = r.purchase_order_id),
    'gst', r.gst,
    'lines', COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                 'line_no', il.line_no, 'description', il.description, 'quantity', il.quantity, 'unit', il.unit,
                 'unit_price_cents', il.unit_price_cents, 'line_total_cents', il.line_total_cents,
                 'match_method', rl.match_method, 'match_confidence', rl.match_confidence,
                 'po_line_no', pol.line_no, 'po_description', pol.description, 'po_quantity', rl.po_quantity,
                 'po_unit_price_cents', rl.po_unit_price_cents, 'received_quantity', rl.received_quantity,
                 'billed_before', rl.billed_before, 'price_variance_pct', rl.price_variance_pct,
                 'issues', COALESCE(rl.issues, '{}')) ORDER BY il.line_no)
          FROM invoice_lines il
          LEFT JOIN reconciliation_lines rl ON rl.invoice_line_id = il.id AND rl.reconciliation_id = r.id
          LEFT JOIN purchase_order_lines pol ON pol.id = rl.purchase_order_line_id
         WHERE il.invoice_id = i.id), '[]'::jsonb))
    FROM approvals a
    JOIN invoices i ON i.id = a.invoice_id
    LEFT JOIN suppliers s ON s.id = i.supplier_id
    LEFT JOIN reconciliations r ON r.invoice_id = i.id
   WHERE a.id = p_approval_id
$$;

-- The audit trail of one invoice, oldest first: ingestion, reconciliation, approval request, decision.
-- audit_log is append-only and invoice ids restart after a development reset, so rows older than the invoice
-- itself are not its history.
CREATE FUNCTION invoice_audit_trail(p_invoice_id bigint)
RETURNS TABLE (audit_id bigint, occurred_at timestamptz, actor text, action text, details jsonb)
LANGUAGE sql STABLE AS $$
  SELECT l.id, l.occurred_at, l.actor, l.action, l.details
    FROM audit_log l
   WHERE l.entity_type = 'invoice' AND l.entity_id = p_invoice_id::text
     AND l.occurred_at >= (SELECT i.created_at FROM invoices i WHERE i.id = p_invoice_id)
   ORDER BY l.id
$$;
