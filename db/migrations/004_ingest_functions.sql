-- Ingestion as database functions (P2).
--
-- The n8n workflow validates the model's output and then makes ONE call per file:
--   ingest_invoice(payload)      validated extraction -> invoice_files + invoices + invoice_lines + audit_log
--   record_needs_review(payload) extraction failed twice -> invoice_files + audit_log
-- Everything that is arithmetic or a lookup (dollars to cents, ABN digits, supplier by ABN,
-- duplicate detection) is plain SQL in one transaction, testable with psql alone
-- (db/tests/ingest.sql), and not scattered across workflow expressions.
--
-- Payload for both: {file_sha256, filename, attempts, llm_model, execution_id, workflow_id, ...}
-- ingest_invoice also takes `extraction`; record_needs_review takes `reason` and `last_output`.

-- A line may print no unit (the schema allows null); 003 declared it NOT NULL.
ALTER TABLE invoice_lines ALTER COLUMN unit DROP NOT NULL;

-- A PDF with no text layer goes to needs_review before any model call: zero attempts.
ALTER TABLE invoice_files DROP CONSTRAINT invoice_files_attempts_check;
ALTER TABLE invoice_files ADD CONSTRAINT invoice_files_attempts_check CHECK (attempts BETWEEN 0 AND 2);

-- "1430.74" -> 143074. Exact: no floating point, no rounding, no tolerance for other shapes.
CREATE FUNCTION dollars_to_cents(amount text) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
BEGIN
  IF amount !~ '^-?[0-9]{1,12}\.[0-9]{2}$' THEN
    RAISE EXCEPTION 'not a dollar amount with two decimals: %', amount USING ERRCODE = '22023';
  END IF;
  RETURN replace(amount, '.', '')::bigint;
END;
$$;

CREATE FUNCTION ingest_invoice(payload jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  e            jsonb := payload -> 'extraction';
  v_sha        text := payload ->> 'file_sha256';
  v_abn        text := regexp_replace(e ->> 'supplier_abn', '\s', '', 'g');
  v_number     text := e ->> 'invoice_number';
  v_total      bigint := dollars_to_cents(e ->> 'total');
  v_key        text;
  v_supplier   bigint;
  v_original   bigint;
  v_invoice_id bigint;
  v_existing   record;
BEGIN
  INSERT INTO invoice_files (file_sha256, original_filename, status, attempts)
  VALUES (v_sha, payload ->> 'filename', 'extracted', (payload ->> 'attempts')::smallint)
  ON CONFLICT (file_sha256) DO NOTHING;

  IF NOT FOUND THEN
    -- Same bytes already seen: nothing changes (principle 4).
    SELECT f.status, i.id AS invoice_id, i.duplicate_of INTO v_existing
      FROM invoice_files f LEFT JOIN invoices i USING (file_sha256) WHERE f.file_sha256 = v_sha;
    RETURN jsonb_build_object(
      'status', 'noop', 'existing_status', v_existing.status,
      'invoice_id', v_existing.invoice_id, 'duplicate_of', v_existing.duplicate_of
    );
  END IF;

  v_key := invoice_key_of(v_abn, v_number, v_total);
  SELECT id INTO v_supplier FROM suppliers WHERE abn = v_abn;
  -- Same business key, different file: a re-issued or re-sent invoice. Point at the first one
  -- (the original, never at another duplicate); reconciliation flags it duplicate_invoice.
  SELECT id INTO v_original FROM invoices WHERE invoice_key = v_key AND duplicate_of IS NULL ORDER BY id LIMIT 1;

  INSERT INTO invoices (
    file_sha256, supplier_id, supplier_abn, supplier_name, invoice_number, invoice_date, due_date,
    po_number, currency, subtotal_cents, gst_cents, total_cents, duplicate_of, extraction, llm_model
  ) VALUES (
    v_sha, v_supplier, v_abn, e ->> 'supplier_name', v_number, (e ->> 'invoice_date')::date,
    (e ->> 'due_date')::date, e ->> 'po_number', e ->> 'currency',
    dollars_to_cents(e ->> 'subtotal'), dollars_to_cents(e ->> 'gst'), v_total,
    v_original, e, payload ->> 'llm_model'
  ) RETURNING id INTO v_invoice_id;

  INSERT INTO invoice_lines (
    invoice_id, line_no, supplier_code, description, quantity, unit,
    unit_price_cents, gst_applicable, line_total_cents, gst_cents
  )
  SELECT v_invoice_id, t.ord, t.l ->> 'supplier_code', t.l ->> 'description',
         (t.l ->> 'quantity')::numeric, t.l ->> 'unit', dollars_to_cents(t.l ->> 'unit_price'),
         (t.l ->> 'gst_applicable')::boolean, dollars_to_cents(t.l ->> 'line_total'),
         dollars_to_cents(t.l ->> 'line_gst')
    FROM jsonb_array_elements(e -> 'lines') WITH ORDINALITY AS t (l, ord);

  INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
  VALUES (
    COALESCE(payload ->> 'workflow_name', 'system'),
    CASE WHEN v_original IS NULL THEN 'invoice.ingested' ELSE 'invoice.duplicate_detected' END,
    'invoice', v_invoice_id::text, payload ->> 'workflow_id', payload ->> 'execution_id',
    jsonb_build_object(
      'file_sha256', v_sha, 'filename', payload ->> 'filename', 'invoice_key', v_key,
      'duplicate_of', v_original, 'supplier_known', v_supplier IS NOT NULL,
      'attempts', (payload ->> 'attempts')::int, 'llm_model', payload ->> 'llm_model'
    )
  );

  RETURN jsonb_build_object(
    'status', 'extracted', 'invoice_id', v_invoice_id, 'duplicate_of', v_original,
    'invoice_key', v_key, 'supplier_known', v_supplier IS NOT NULL
  );
END;
$$;

CREATE FUNCTION record_needs_review(payload jsonb) RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
  v_sha text := payload ->> 'file_sha256';
BEGIN
  INSERT INTO invoice_files (file_sha256, original_filename, status, failure_reason, last_llm_output, attempts)
  VALUES (
    v_sha, payload ->> 'filename', 'needs_review', COALESCE(payload ->> 'reason', 'unspecified'),
    payload ->> 'last_output', (payload ->> 'attempts')::smallint
  ) ON CONFLICT (file_sha256) DO NOTHING;

  IF NOT FOUND THEN
    RETURN jsonb_build_object('status', 'noop');
  END IF;

  INSERT INTO audit_log (actor, action, entity_type, entity_id, workflow_id, execution_id, details)
  VALUES (
    COALESCE(payload ->> 'workflow_name', 'system'), 'invoice.needs_review', 'invoice_file', v_sha,
    payload ->> 'workflow_id', payload ->> 'execution_id',
    jsonb_build_object(
      'filename', payload ->> 'filename', 'reason', payload ->> 'reason',
      'attempts', (payload ->> 'attempts')::int, 'llm_model', payload ->> 'llm_model'
    )
  );
  RETURN jsonb_build_object('status', 'needs_review');
END;
$$;

-- What the workflow checks before spending an LLM call on a file it has already seen.
CREATE FUNCTION invoice_file_status(sha text) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    (SELECT jsonb_build_object(
        'status', 'noop', 'existing_status', f.status,
        'invoice_id', i.id, 'duplicate_of', i.duplicate_of)
       FROM invoice_files f LEFT JOIN invoices i USING (file_sha256)
      WHERE f.file_sha256 = sha),
    jsonb_build_object('status', 'new'))
$$;
