-- Reconciliation (P3): decide recommend_approve / flag / needs_review for an ingested invoice.
--
-- Everything that is a rule lives here, in SQL, tested by db/tests/reconcile.sql. The only judgement
-- call handed to a model is "which PO line is this reworded invoice line?", and that arrives as an
-- input (`p_matches`) with a confidence, validated and thresholded here. Nothing in this file
-- approves, pays or sends anything: the status is a recommendation for a human (principle 3).
--
-- Flow, used by the n8n workflow:
--   1. reconcile_candidates(invoice_id)          which lines still need a match, and the candidates
--   2. (the model matches them, validated in the workflow)
--   3. reconcile_invoice(invoice_id, matches)    one call, one transaction, one result row
-- reconcile_invoice is idempotent: running it again replaces the invoice's result.

-- ---------------------------------------------------------------------------------------------
-- Tolerances are data, not constants buried in code.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE reconciliation_settings (
  key         text PRIMARY KEY,
  value       numeric NOT NULL,
  description text NOT NULL
);

INSERT INTO reconciliation_settings (key, value, description) VALUES
  ('price_tolerance_pct', 2.0,
   'An invoiced unit price more than this percent ABOVE the PO price is flagged price_variance. Undercharges are not flagged.'),
  ('gst_rate_pct', 10,
   'GST rate (Australia).'),
  ('gst_rounding_cents_per_taxable_line', 0.5,
   'Allowed gap between stated GST and the rate times the taxable subtotal: ceil(taxable lines x this) cents, because suppliers that round GST per line drift by up to half a cent per line.'),
  ('line_total_tolerance_cents', 1,
   'Allowed gap between quantity x unit price and the printed line total.'),
  ('line_match_min_confidence', 0.85,
   'A model-proposed line match below this confidence is not used; the line goes to needs_review.'),
  ('quantity_epsilon', 0.0005,
   'Quantities are compared to three decimals; differences below this are rounding.');

CREATE FUNCTION reconciliation_setting(setting_key text) RETURNS numeric
LANGUAGE plpgsql STABLE AS $$
DECLARE v numeric;
BEGIN
  SELECT value INTO v FROM reconciliation_settings WHERE key = setting_key;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown reconciliation setting: %', setting_key USING ERRCODE = '22023';
  END IF;
  RETURN v;
END;
$$;

-- "PO-004512" and "PO004512" are the same purchase order; so are "po 004512" and "Po-004512".
CREATE FUNCTION po_key(po_number text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT upper(regexp_replace(po_number, '[^A-Za-z0-9]', '', 'g'))
$$;

CREATE FUNCTION description_key(description text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT lower(regexp_replace(description, '[^A-Za-z0-9]', '', 'g'))
$$;

-- ---------------------------------------------------------------------------------------------
-- Results
-- ---------------------------------------------------------------------------------------------
CREATE TABLE reconciliations (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  invoice_id        bigint NOT NULL UNIQUE REFERENCES invoices (id) ON DELETE CASCADE,
  status            text NOT NULL CHECK (status IN ('recommend_approve', 'flag', 'needs_review')),
  -- why the invoice is flagged: discrepancies a human can act on
  reason_codes      text[] NOT NULL DEFAULT '{}'
    CHECK (reason_codes <@ ARRAY['price_variance', 'short_delivery', 'duplicate_invoice', 'unknown_po',
                                 'missing_po', 'gst_miscalculated']),
  -- why a human has to look when the system cannot be sure (unmatched line, supplier mismatch, ...)
  review_reasons    text[] NOT NULL DEFAULT '{}'
    CHECK (review_reasons <@ ARRAY['unmatched_line', 'low_confidence_match', 'supplier_unknown',
                                   'po_supplier_mismatch', 'line_arithmetic', 'totals_arithmetic',
                                   'quantity_exceeds_po', 'no_receiving_record']),
  purchase_order_id bigint REFERENCES purchase_orders (id),
  gst               jsonb,
  settings          jsonb NOT NULL,
  details           jsonb NOT NULL DEFAULT '{}'::jsonb,
  reconciled_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'flag' OR cardinality(reason_codes) > 0),
  CHECK (status <> 'needs_review' OR (cardinality(reason_codes) = 0 AND cardinality(review_reasons) > 0)),
  CHECK (status <> 'recommend_approve' OR (cardinality(reason_codes) = 0 AND cardinality(review_reasons) = 0))
);

CREATE INDEX reconciliations_status_idx ON reconciliations (status);

CREATE TABLE reconciliation_lines (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  reconciliation_id      bigint NOT NULL REFERENCES reconciliations (id) ON DELETE CASCADE,
  invoice_line_id        bigint NOT NULL REFERENCES invoice_lines (id) ON DELETE CASCADE,
  purchase_order_line_id bigint REFERENCES purchase_order_lines (id),
  match_method           text NOT NULL CHECK (match_method IN ('sku', 'description', 'llm', 'none')),
  match_confidence       numeric CHECK (match_confidence BETWEEN 0 AND 1),
  issues                 text[] NOT NULL DEFAULT '{}',
  invoiced_quantity      numeric(12, 3) NOT NULL,
  po_quantity            numeric(12, 3),
  received_quantity      numeric(12, 3),  -- total received for the PO line, all receipts
  billed_before          numeric(12, 3),  -- billed on earlier non-duplicate invoices for this PO line
  invoiced_unit_price_cents bigint NOT NULL,
  po_unit_price_cents    bigint,
  price_variance_pct     numeric(9, 3),
  UNIQUE (reconciliation_id, invoice_line_id)
);

COMMENT ON COLUMN reconciliation_lines.billed_before IS
  'Quantity billed on earlier (lower id) non-duplicate invoices against the same PO line, from their reconciliation rows. Invoices must be reconciled in ingest order for this to be complete.';

-- ---------------------------------------------------------------------------------------------
-- Deterministic line matching: printed supplier SKU first, then normalised description.
-- Each PO line is used at most once, and invoice lines are taken in line order, so two invoice lines
-- carrying the same code cannot both claim one PO line.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION deterministic_line_matches(p_invoice_id bigint, p_po_id bigint)
RETURNS TABLE (invoice_line_id bigint, purchase_order_line_id bigint, method text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  used_po      bigint[] := '{}';
  matched_inv  bigint[] := '{}';
  r            record;
  m            bigint;
BEGIN
  FOR r IN SELECT il.id, il.supplier_code FROM invoice_lines il
            WHERE il.invoice_id = p_invoice_id ORDER BY il.line_no LOOP
    m := NULL;
    IF r.supplier_code IS NOT NULL AND btrim(r.supplier_code) <> '' THEN
      SELECT pol.id INTO m FROM purchase_order_lines pol
       WHERE pol.purchase_order_id = p_po_id
         AND lower(btrim(pol.supplier_sku)) = lower(btrim(r.supplier_code))
         AND NOT (pol.id = ANY (used_po))
       ORDER BY pol.line_no LIMIT 1;
    END IF;
    IF m IS NOT NULL THEN
      used_po := used_po || m;
      matched_inv := matched_inv || r.id;
      invoice_line_id := r.id; purchase_order_line_id := m; method := 'sku';
      RETURN NEXT;
    END IF;
  END LOOP;

  FOR r IN SELECT il.id, il.description FROM invoice_lines il
            WHERE il.invoice_id = p_invoice_id AND NOT (il.id = ANY (matched_inv))
            ORDER BY il.line_no LOOP
    SELECT pol.id INTO m FROM purchase_order_lines pol
     WHERE pol.purchase_order_id = p_po_id
       AND description_key(pol.description) = description_key(r.description)
       AND NOT (pol.id = ANY (used_po))
     ORDER BY pol.line_no LIMIT 1;
    IF m IS NOT NULL THEN
      used_po := used_po || m;
      invoice_line_id := r.id; purchase_order_line_id := m; method := 'description';
      RETURN NEXT;
    END IF;
  END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- Step 1: what still needs a match? Returned as the input for the model, if any is needed.
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION reconcile_candidates(p_invoice_id bigint) RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  inv invoices%ROWTYPE;
  v_po bigint;
BEGIN
  SELECT * INTO inv FROM invoices WHERE id = p_invoice_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invoice % not found', p_invoice_id USING ERRCODE = 'P0002';
  END IF;
  IF inv.po_number IS NOT NULL AND btrim(inv.po_number) <> '' THEN
    SELECT id INTO v_po FROM purchase_orders WHERE po_key(po_number) = po_key(inv.po_number);
  END IF;
  IF v_po IS NULL THEN
    RETURN jsonb_build_object('invoice_id', p_invoice_id, 'invoice_number', inv.invoice_number,
                              'pending_lines', '[]'::jsonb, 'candidates', '[]'::jsonb);
  END IF;

  RETURN jsonb_build_object(
    'invoice_id', p_invoice_id,
    'invoice_number', inv.invoice_number,
    'pending_lines', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'line_no', il.line_no, 'supplier_code', il.supplier_code, 'description', il.description,
               'quantity', il.quantity, 'unit', il.unit, 'unit_price_cents', il.unit_price_cents,
               'line_total_cents', il.line_total_cents) ORDER BY il.line_no)
        FROM invoice_lines il
       WHERE il.invoice_id = p_invoice_id
         AND il.id NOT IN (SELECT m.invoice_line_id FROM deterministic_line_matches(p_invoice_id, v_po) m)
    ), '[]'::jsonb),
    'candidates', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'po_line_no', pol.line_no, 'supplier_sku', pol.supplier_sku, 'description', pol.description,
               'unit', pol.unit, 'quantity', pol.quantity, 'unit_price_cents', pol.unit_price_cents)
             ORDER BY pol.line_no)
        FROM purchase_order_lines pol
       WHERE pol.purchase_order_id = v_po
         AND pol.id NOT IN (SELECT m.purchase_order_line_id FROM deterministic_line_matches(p_invoice_id, v_po) m)
    ), '[]'::jsonb)
  );
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- Step 3: the reconciliation itself.
--   p_matches  [{invoice_line_no, po_line_no (or null), confidence}] for lines the model matched
--   p_context  {execution_id, workflow_id, workflow_name} for the audit row
-- ---------------------------------------------------------------------------------------------
CREATE FUNCTION reconcile_invoice(
  p_invoice_id bigint,
  p_matches    jsonb DEFAULT '[]'::jsonb,
  p_context    jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  inv           invoices%ROWTYPE;
  po            purchase_orders%ROWTYPE;
  v_price_tol   numeric := reconciliation_setting('price_tolerance_pct');
  v_gst_rate    numeric := reconciliation_setting('gst_rate_pct');
  v_gst_per_ln  numeric := reconciliation_setting('gst_rounding_cents_per_taxable_line');
  v_line_tol    numeric := reconciliation_setting('line_total_tolerance_cents');
  v_min_conf    numeric := reconciliation_setting('line_match_min_confidence');
  v_eps         numeric := reconciliation_setting('quantity_epsilon');
  v_settings    jsonb;

  codes         text[] := '{}';
  review        text[] := '{}';
  v_map         jsonb := '[]'::jsonb;
  used_po       bigint[] := '{}';
  matched_inv   bigint[] := '{}';
  r             record;
  lm            jsonb;
  v_pol         bigint;
  v_conf        numeric;
  v_has_receipt boolean := false;

  v_rec_id      bigint;
  v_status      text;
  v_taxable     bigint;
  v_taxable_n   integer;
  v_expected    bigint;
  v_tolerance   integer;
  v_gst_basis   text;
  v_gst         jsonb;
  v_sum_lines   bigint;
  v_line_codes  text[];
  v_line_review text[];
BEGIN
  SELECT * INTO inv FROM invoices WHERE id = p_invoice_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'invoice % not found', p_invoice_id USING ERRCODE = 'P0002';
  END IF;
  v_settings := (SELECT jsonb_object_agg(key, value) FROM reconciliation_settings);

  -- ---- invoice level: duplicate, PO, supplier ------------------------------------------------
  IF inv.duplicate_of IS NOT NULL THEN
    codes := array_append(codes, 'duplicate_invoice');
  END IF;

  IF inv.po_number IS NULL OR btrim(inv.po_number) = '' THEN
    codes := array_append(codes, 'missing_po');
  ELSE
    SELECT * INTO po FROM purchase_orders WHERE po_key(purchase_orders.po_number) = po_key(inv.po_number);
    IF NOT FOUND THEN
      codes := array_append(codes, 'unknown_po');
    END IF;
  END IF;

  IF inv.supplier_id IS NULL THEN
    review := array_append(review, 'supplier_unknown');
  ELSIF po.id IS NOT NULL AND po.supplier_id <> inv.supplier_id THEN
    review := array_append(review, 'po_supplier_mismatch');
  END IF;

  -- ---- line matching -------------------------------------------------------------------------
  IF po.id IS NOT NULL THEN
    FOR r IN SELECT * FROM deterministic_line_matches(p_invoice_id, po.id) LOOP
      v_map := v_map || jsonb_build_object('invoice_line_id', r.invoice_line_id,
                 'po_line_id', r.purchase_order_line_id, 'method', r.method);
      used_po := used_po || r.purchase_order_line_id;
      matched_inv := matched_inv || r.invoice_line_id;
    END LOOP;
  END IF;

  FOR r IN SELECT il.id, il.line_no FROM invoice_lines il
            WHERE il.invoice_id = p_invoice_id AND NOT (il.id = ANY (matched_inv))
            ORDER BY il.line_no LOOP
    v_pol := NULL; v_conf := NULL;
    IF po.id IS NULL THEN
      -- no PO, nothing to match against; the PO codes already say why
      v_map := v_map || jsonb_build_object('invoice_line_id', r.id, 'method', 'none');
      CONTINUE;
    END IF;

    SELECT e INTO lm FROM jsonb_array_elements(COALESCE(p_matches, '[]'::jsonb)) e
     WHERE (e ->> 'invoice_line_no')::integer = r.line_no LIMIT 1;

    IF lm IS NOT NULL AND (lm ->> 'po_line_no') IS NOT NULL THEN
      v_conf := (lm ->> 'confidence')::numeric;
      SELECT pol.id INTO v_pol FROM purchase_order_lines pol
       WHERE pol.purchase_order_id = po.id AND pol.line_no = (lm ->> 'po_line_no')::integer
         AND NOT (pol.id = ANY (used_po));
    END IF;

    IF v_pol IS NOT NULL AND v_conf IS NOT NULL AND v_conf >= v_min_conf THEN
      used_po := used_po || v_pol;
      v_map := v_map || jsonb_build_object('invoice_line_id', r.id, 'po_line_id', v_pol,
                 'method', 'llm', 'confidence', v_conf);
    ELSIF v_pol IS NOT NULL THEN  -- proposed, but not confident enough to trust
      v_map := v_map || jsonb_build_object('invoice_line_id', r.id, 'method', 'none',
                 'confidence', v_conf, 'review', 'low_confidence_match');
    ELSE                          -- no proposal, "not on the PO", or an unusable one
      v_map := v_map || jsonb_build_object('invoice_line_id', r.id, 'method', 'none',
                 'review', 'unmatched_line');
    END IF;
  END LOOP;

  IF po.id IS NOT NULL THEN
    v_has_receipt := EXISTS (SELECT 1 FROM receipts WHERE purchase_order_id = po.id);
    IF NOT v_has_receipt THEN
      review := array_append(review, 'no_receiving_record');
    END IF;
  END IF;

  -- ---- replace any earlier result, then record the lines ----------------------------------------
  DELETE FROM reconciliations WHERE invoice_id = p_invoice_id;
  INSERT INTO reconciliations (invoice_id, status, purchase_order_id, settings)
  VALUES (p_invoice_id, 'recommend_approve', po.id, v_settings)
  RETURNING id INTO v_rec_id;

  INSERT INTO reconciliation_lines (
    reconciliation_id, invoice_line_id, purchase_order_line_id, match_method, match_confidence, issues,
    invoiced_quantity, po_quantity, received_quantity, billed_before,
    invoiced_unit_price_cents, po_unit_price_cents, price_variance_pct
  )
  WITH map AS (
    SELECT (e ->> 'invoice_line_id')::bigint AS il_id,
           NULLIF(e ->> 'po_line_id', '')::bigint AS pol_id,
           e ->> 'method' AS method,
           NULLIF(e ->> 'confidence', '')::numeric AS conf,
           e ->> 'review' AS review
      FROM jsonb_array_elements(v_map) e
  ), base AS (
    SELECT il.id AS il_id, il.quantity AS qty, il.unit_price_cents AS ipc, il.line_total_cents AS ltc,
           map.pol_id, map.method, map.conf, map.review,
           pol.quantity AS po_qty, pol.unit_price_cents AS ppc,
           CASE WHEN map.pol_id IS NOT NULL AND v_has_receipt THEN
             COALESCE((SELECT sum(rl.quantity_received) FROM receipt_lines rl
                        WHERE rl.purchase_order_line_id = map.pol_id), 0) END AS received,
           CASE WHEN map.pol_id IS NOT NULL THEN
             COALESCE((SELECT sum(il2.quantity)
                         FROM reconciliation_lines rl2
                         JOIN reconciliations r2 ON r2.id = rl2.reconciliation_id
                         JOIN invoices i2 ON i2.id = r2.invoice_id
                         JOIN invoice_lines il2 ON il2.id = rl2.invoice_line_id
                        WHERE rl2.purchase_order_line_id = map.pol_id
                          AND i2.id < p_invoice_id AND i2.duplicate_of IS NULL
                          -- a copy restates its original's billing, so it is judged as the original was
                          AND i2.id IS DISTINCT FROM inv.duplicate_of), 0) END AS billed_before
      FROM map
      JOIN invoice_lines il ON il.id = map.il_id
      LEFT JOIN purchase_order_lines pol ON pol.id = map.pol_id
  )
  SELECT v_rec_id, b.il_id, b.pol_id, b.method, b.conf,
         array_remove(ARRAY[
           CASE WHEN b.review IS NOT NULL THEN b.review END,
           CASE WHEN b.pol_id IS NOT NULL AND b.ipc > b.ppc
                 AND (b.ipc - b.ppc) * 100 > v_price_tol * b.ppc THEN 'price_variance' END,
           CASE WHEN b.pol_id IS NOT NULL AND b.received IS NOT NULL
                 AND b.billed_before + b.qty > b.received + v_eps THEN 'short_delivery' END,
           CASE WHEN b.pol_id IS NOT NULL AND b.billed_before + b.qty > b.po_qty + v_eps THEN 'quantity_exceeds_po' END,
           CASE WHEN abs(round(b.qty * b.ipc) - b.ltc) > v_line_tol THEN 'line_arithmetic' END
         ], NULL),
         b.qty, b.po_qty, b.received, b.billed_before, b.ipc, b.ppc,
         CASE WHEN b.pol_id IS NOT NULL AND b.ppc > 0
              THEN round((b.ipc - b.ppc) * 100.0 / b.ppc, 3) END
    FROM base b;

  -- ---- GST: the PO decides what is taxable, the invoice's own markers only where there is no PO line
  SELECT COALESCE(sum(il.line_total_cents) FILTER (WHERE COALESCE(pol.gst_applicable, il.gst_applicable)), 0),
         count(*) FILTER (WHERE COALESCE(pol.gst_applicable, il.gst_applicable)),
         CASE WHEN count(*) FILTER (WHERE rl.purchase_order_line_id IS NULL) = 0 THEN 'po'
              WHEN count(*) FILTER (WHERE rl.purchase_order_line_id IS NOT NULL) = 0 THEN 'invoice'
              ELSE 'mixed' END
    INTO v_taxable, v_taxable_n, v_gst_basis
    FROM invoice_lines il
    JOIN reconciliation_lines rl ON rl.invoice_line_id = il.id AND rl.reconciliation_id = v_rec_id
    LEFT JOIN purchase_order_lines pol ON pol.id = rl.purchase_order_line_id
   WHERE il.invoice_id = p_invoice_id;

  v_expected  := round(v_taxable * v_gst_rate / 100);
  v_tolerance := ceil(v_taxable_n * v_gst_per_ln);
  IF abs(inv.gst_cents - v_expected) > v_tolerance THEN
    codes := array_append(codes, 'gst_miscalculated');
  END IF;
  v_gst := jsonb_build_object('taxable_cents', v_taxable, 'taxable_lines', v_taxable_n,
             'expected_cents', v_expected, 'stated_cents', inv.gst_cents, 'tolerance_cents', v_tolerance,
             'difference_cents', inv.gst_cents - v_expected, 'basis', v_gst_basis);

  -- ---- totals arithmetic ------------------------------------------------------------------------
  SELECT COALESCE(sum(line_total_cents), 0) INTO v_sum_lines FROM invoice_lines WHERE invoice_id = p_invoice_id;
  IF v_sum_lines <> inv.subtotal_cents OR inv.subtotal_cents + inv.gst_cents <> inv.total_cents THEN
    review := array_append(review, 'totals_arithmetic');
  END IF;

  -- ---- roll the line findings up ------------------------------------------------------------
  SELECT COALESCE(array_agg(DISTINCT i) FILTER (WHERE i IN ('price_variance', 'short_delivery')), '{}'),
         COALESCE(array_agg(DISTINCT i) FILTER (WHERE i NOT IN ('price_variance', 'short_delivery')), '{}')
    INTO v_line_codes, v_line_review
    FROM reconciliation_lines rl, unnest(rl.issues) i
   WHERE rl.reconciliation_id = v_rec_id;
  codes  := codes || v_line_codes;
  review := review || v_line_review;

  -- canonical order, no repeats
  SELECT COALESCE(array_agg(c ORDER BY array_position(
           ARRAY['price_variance', 'short_delivery', 'duplicate_invoice', 'unknown_po', 'missing_po',
                 'gst_miscalculated'], c)), '{}')
    INTO codes FROM (SELECT DISTINCT unnest(codes) AS c) s;
  SELECT COALESCE(array_agg(DISTINCT x ORDER BY x), '{}') INTO review FROM unnest(review) x;

  v_status := CASE WHEN cardinality(codes) > 0 THEN 'flag'
                   WHEN cardinality(review) > 0 THEN 'needs_review'
                   ELSE 'recommend_approve' END;
  -- a flagged invoice keeps its review reasons too (the human should see both), but needs_review
  -- means "no discrepancy found, yet cannot be sure", which the constraint enforces
  UPDATE reconciliations
     SET status = v_status, reason_codes = codes, review_reasons = review, gst = v_gst,
         details = jsonb_build_object(
           'purchase_order', po.po_number, 'supplier_known', inv.supplier_id IS NOT NULL,
           'line_matches', (SELECT jsonb_object_agg(match_method, n) FROM (
                SELECT match_method, count(*) AS n FROM reconciliation_lines
                 WHERE reconciliation_id = v_rec_id GROUP BY match_method) t))
   WHERE id = v_rec_id;

  INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
  VALUES (COALESCE(p_context ->> 'workflow_name', 'system'), 'invoice.reconciled', 'invoice',
          p_invoice_id::text, p_context ->> 'workflow_id', p_context ->> 'execution_id',
          jsonb_build_object('status', v_status, 'reason_codes', codes, 'review_reasons', review,
                             'reconciliation_id', v_rec_id));

  RETURN jsonb_build_object(
    'reconciliation_id', v_rec_id, 'invoice_id', p_invoice_id, 'status', v_status,
    'reason_codes', codes, 'review_reasons', review, 'purchase_order', po.po_number, 'gst', v_gst,
    'lines', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                'line_no', il.line_no, 'po_line_no', pol.line_no, 'po_line_id', rl.purchase_order_line_id,
                'match_method', rl.match_method, 'match_confidence', rl.match_confidence,
                'issues', rl.issues) ORDER BY il.line_no), '[]'::jsonb)
                FROM reconciliation_lines rl JOIN invoice_lines il ON il.id = rl.invoice_line_id
                LEFT JOIN purchase_order_lines pol ON pol.id = rl.purchase_order_line_id
               WHERE rl.reconciliation_id = v_rec_id));
END;
$$;
