-- Tests for the ingestion functions (migration 004). Plain SQL, no framework:
--   bash scripts/test-db.sh
-- Runs inside one transaction that is rolled back, so it leaves the database as it found it.
-- Any failed ASSERT aborts with a message.

BEGIN;

CREATE TEMP TABLE t_sample AS
SELECT jsonb_build_object(
  'supplier_name', 'Testco Pty Ltd', 'supplier_abn', '51 824 753 556', 'bill_to_name', 'The Wattle Lane Hotel',
  'invoice_number', 'T-100', 'invoice_date', '2026-07-20', 'due_date', NULL, 'po_number', 'PO-009999',
  'currency', 'AUD',
  'lines', jsonb_build_array(
    jsonb_build_object('supplier_code', 'X1', 'description', 'Thing', 'quantity', 2.5, 'unit', 'kg',
      'unit_price', '10.00', 'gst_applicable', true, 'line_total', '25.00', 'line_gst', NULL),
    jsonb_build_object('supplier_code', NULL, 'description', 'Other thing', 'quantity', 3, 'unit', NULL,
      'unit_price', '1.10', 'gst_applicable', false, 'line_total', '3.30', 'line_gst', NULL)),
  'subtotal', '28.30', 'gst', '2.50', 'total', '30.80') AS e;

CREATE FUNCTION pg_temp.payload(sha text, ext jsonb) RETURNS jsonb LANGUAGE sql AS $$
  SELECT jsonb_build_object('file_sha256', sha, 'filename', 'f.pdf', 'attempts', 1, 'llm_model', 'test-model',
                            'execution_id', 'ex-1', 'workflow_id', 'wf-1', 'workflow_name', 'Test', 'extraction', ext)
$$;

DO $$
DECLARE
  sample jsonb := (SELECT e FROM t_sample);
  r jsonb; r2 jsonb; r3 jsonb; inv bigint; n int;
  sha_a text := repeat('a', 64); sha_b text := repeat('b', 64); sha_c text := repeat('c', 64);
BEGIN
  -- dollars_to_cents: exact, strict
  ASSERT dollars_to_cents('1430.74') = 143074, 'cents';
  ASSERT dollars_to_cents('0.05') = 5, 'cents small';
  ASSERT dollars_to_cents('-5.00') = -500, 'cents negative';
  ASSERT dollars_to_cents('999999999999.99') = 99999999999999, 'cents max';
  FOR r IN SELECT to_jsonb(x) FROM unnest(ARRAY['1,430.74', '$5.00', '5', '5.0', '5.000', '', '1e3', '1000000000000.00']) x LOOP
    BEGIN
      PERFORM dollars_to_cents(r #>> '{}');
      RAISE EXCEPTION 'accepted bad amount %', r;
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
  END LOOP;

  -- first file: a normal ingest
  ASSERT invoice_file_status(sha_a) = '{"status": "new"}', 'unseen file is new';
  r := ingest_invoice(pg_temp.payload(sha_a, sample));
  ASSERT r ->> 'status' = 'extracted', 'status ' || r::text;
  ASSERT (r ->> 'duplicate_of') IS NULL, 'original has no duplicate_of';
  ASSERT (r ->> 'supplier_known')::boolean = false, 'unknown ABN has no supplier';
  inv := (r ->> 'invoice_id')::bigint;
  ASSERT (SELECT supplier_abn FROM invoices WHERE id = inv) = '51824753556', 'ABN stored as digits';
  ASSERT (SELECT total_cents FROM invoices WHERE id = inv) = 3080, 'total cents';
  ASSERT (SELECT invoice_key FROM invoices WHERE id = inv) = invoice_key_of('51824753556', 'T-100', 3080), 'key';
  ASSERT (SELECT count(*) FROM invoice_lines WHERE invoice_id = inv) = 2, 'two lines';
  ASSERT (SELECT unit FROM invoice_lines WHERE invoice_id = inv AND line_no = 2) IS NULL, 'null unit allowed';
  ASSERT (SELECT quantity FROM invoice_lines WHERE invoice_id = inv AND line_no = 1) = 2.5, 'decimal quantity';
  ASSERT (SELECT gst_applicable FROM invoice_lines WHERE invoice_id = inv AND line_no = 2) = false, 'gst flag';
  ASSERT (SELECT count(*) FROM audit_log WHERE entity_id = inv::text AND action = 'invoice.ingested') = 1, 'audit row';
  ASSERT invoice_file_status(sha_a) ->> 'existing_status' = 'extracted', 'file now known';

  -- same bytes again: no-op, nothing added
  r2 := ingest_invoice(pg_temp.payload(sha_a, sample));
  ASSERT r2 ->> 'status' = 'noop' AND (r2 ->> 'invoice_id')::bigint = inv, 'same file is a no-op: ' || r2::text;
  ASSERT (SELECT count(*) FROM invoices WHERE file_sha256 = sha_a) = 1, 'no second invoice';
  ASSERT (SELECT count(*) FROM audit_log WHERE entity_id = inv::text) = 1, 'no second audit row';

  -- same invoice, different file: stored, pointing at the original
  r3 := ingest_invoice(pg_temp.payload(sha_b, sample));
  ASSERT r3 ->> 'status' = 'extracted' AND (r3 ->> 'duplicate_of')::bigint = inv, 'duplicate points at original: ' || r3::text;
  ASSERT (SELECT count(*) FROM audit_log WHERE entity_id = (r3 ->> 'invoice_id') AND action = 'invoice.duplicate_detected') = 1,
    'duplicate audit action';

  -- a third copy still points at the ORIGINAL, not at the second
  r := ingest_invoice(pg_temp.payload(sha_c, sample));
  ASSERT (r ->> 'duplicate_of')::bigint = inv, 'third copy points at the first';

  -- key is case/space-insensitive on the number, sensitive to the total
  r := ingest_invoice(pg_temp.payload(repeat('d', 64), jsonb_set(sample, '{invoice_number}', '" t-100 "')));
  ASSERT (r ->> 'duplicate_of')::bigint = inv, 'invoice number is normalised for the key';
  r := ingest_invoice(pg_temp.payload(repeat('e', 64),
         jsonb_set(jsonb_set(sample, '{total}', '"30.81"'), '{subtotal}', '"28.31"')));
  ASSERT (r ->> 'duplicate_of') IS NULL, 'different total is a different invoice';

  -- known supplier is resolved by ABN
  INSERT INTO suppliers (abn, name, category, payment_terms_days) VALUES ('51824753556', 'Testco', 'test', 14);
  r := ingest_invoice(pg_temp.payload(repeat('f', 64), jsonb_set(sample, '{invoice_number}', '"T-200"')));
  ASSERT (r ->> 'supplier_known')::boolean AND (SELECT supplier_id FROM invoices WHERE id = (r ->> 'invoice_id')::bigint) IS NOT NULL,
    'supplier resolved';

  -- atomic: a bad amount deep in the payload rolls the whole call back
  BEGIN
    PERFORM ingest_invoice(pg_temp.payload(repeat('9', 64),
      jsonb_set(sample, '{lines,1,line_total}', '"3,30"')));
    RAISE EXCEPTION 'accepted a malformed line amount';
  EXCEPTION WHEN invalid_parameter_value THEN NULL;
  END;
  ASSERT NOT EXISTS (SELECT 1 FROM invoice_files WHERE file_sha256 = repeat('9', 64)), 'failed ingest leaves no file row';
  ASSERT NOT EXISTS (SELECT 1 FROM invoices WHERE file_sha256 = repeat('9', 64)), 'failed ingest leaves no invoice';

  -- needs_review: recorded once, never retried silently, never creates an invoice
  r := record_needs_review(jsonb_build_object('file_sha256', repeat('7', 64), 'filename', 'bad.pdf', 'attempts', 2,
         'reason', 'total: bad', 'last_output', '{broken', 'llm_model', 'test-model', 'workflow_name', 'Test'));
  ASSERT r ->> 'status' = 'needs_review', 'needs_review recorded';
  ASSERT (SELECT last_llm_output FROM invoice_files WHERE file_sha256 = repeat('7', 64)) = '{broken', 'raw output kept';
  ASSERT NOT EXISTS (SELECT 1 FROM invoices WHERE file_sha256 = repeat('7', 64)), 'no invoice for a failed file';
  ASSERT invoice_file_status(repeat('7', 64)) ->> 'existing_status' = 'needs_review', 'status visible';
  r := ingest_invoice(pg_temp.payload(repeat('7', 64), sample));
  ASSERT r ->> 'status' = 'noop' AND r ->> 'existing_status' = 'needs_review', 'a failed file is not silently reprocessed';
  ASSERT (record_needs_review(jsonb_build_object('file_sha256', repeat('7', 64), 'filename', 'bad.pdf', 'attempts', 2)) ->> 'status') = 'noop',
    'needs_review twice is a no-op';
  r := record_needs_review(jsonb_build_object('file_sha256', repeat('6', 64), 'filename', 'scan.pdf', 'attempts', 0,
         'reason', 'no extractable text layer'));
  ASSERT r ->> 'status' = 'needs_review' AND (SELECT attempts FROM invoice_files WHERE file_sha256 = repeat('6', 64)) = 0,
    'a file that never reached the model records zero attempts';
  SELECT count(*) INTO n FROM audit_log WHERE action = 'invoice.needs_review';
  ASSERT n = 2, 'one needs_review audit row per file';

  RAISE NOTICE 'ingest.sql: all assertions passed';
END;
$$;

ROLLBACK;
