-- Invoice ingestion (P2).
--
-- Two layers, because there are two kinds of idempotency:
--   invoice_files  one row per distinct file (sha256). Reprocessing the same bytes is a no-op.
--                  Records failures too, so a file that needed review is not silently retried.
--   invoices       one row per *validated* extraction. Several files may share an invoice_key
--                  (a re-issued or re-sent invoice); the later ones point at the first via
--                  duplicate_of and are flagged `duplicate_invoice` by reconciliation (P3).
--
-- Only schema-valid extractions reach `invoices`. Anything else stays in invoice_files as
-- needs_review, with the raw model output kept for diagnosis.

-- Idempotency key: sha256(abn | UPPER(TRIM(invoice_number)) | total_cents), hex.
-- One definition, here; tests check it against data-gen's invoice_key().
-- IMMUTABLE is asserted by hand: convert_to() is only STABLE because the source encoding
-- can vary, but the ops database is created UTF8, which makes it deterministic.
CREATE FUNCTION invoice_key_of(abn text, invoice_number text, total_cents bigint) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT encode(sha256(convert_to(abn || '|' || upper(btrim(invoice_number)) || '|' || total_cents::text, 'UTF8')), 'hex')
$$;

CREATE TABLE invoice_files (
  file_sha256       text PRIMARY KEY CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  original_filename text NOT NULL,
  status            text NOT NULL CHECK (status IN ('extracted', 'needs_review')),
  failure_reason    text,
  last_llm_output   text,  -- raw text of the final attempt; may not be valid JSON
  attempts          smallint NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 1 AND 2),
  received_at       timestamptz NOT NULL DEFAULT now(),
  CHECK ((status = 'needs_review') = (failure_reason IS NOT NULL))
);

CREATE TABLE invoices (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  file_sha256    text NOT NULL UNIQUE REFERENCES invoice_files (file_sha256),
  supplier_id    bigint REFERENCES suppliers (id),  -- resolved by ABN; NULL if the ABN is unknown
  supplier_abn   text NOT NULL CHECK (supplier_abn ~ '^[0-9]{11}$'),
  supplier_name  text NOT NULL,
  invoice_number text NOT NULL CHECK (btrim(invoice_number) <> ''),
  invoice_key    text GENERATED ALWAYS AS (invoice_key_of(supplier_abn, invoice_number, total_cents)) STORED,
  invoice_date   date NOT NULL,
  due_date       date,
  po_number      text,  -- exactly as printed; matching to purchase_orders happens in P3
  currency       text NOT NULL DEFAULT 'AUD',
  subtotal_cents bigint NOT NULL,
  gst_cents      bigint NOT NULL,
  total_cents    bigint NOT NULL,
  duplicate_of   bigint REFERENCES invoices (id) CHECK (duplicate_of IS DISTINCT FROM id),
  extraction     jsonb NOT NULL,  -- the validated model output, as received
  llm_model      text,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- Not unique: a duplicate invoice is stored, then flagged.
CREATE INDEX invoices_invoice_key_idx ON invoices (invoice_key);
CREATE INDEX invoices_supplier_idx ON invoices (supplier_id);
CREATE INDEX invoices_po_number_idx ON invoices (po_number);

CREATE TABLE invoice_lines (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  invoice_id       bigint NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
  line_no          integer NOT NULL CHECK (line_no > 0),
  supplier_code    text,
  description      text NOT NULL,
  quantity         numeric(12, 3) NOT NULL CHECK (quantity > 0),
  unit             text NOT NULL,
  unit_price_cents bigint NOT NULL,
  gst_applicable   boolean NOT NULL,  -- as printed (or implied by a per-line GST column)
  line_total_cents bigint NOT NULL,   -- ex GST
  gst_cents        bigint,            -- only when the invoice prints GST per line
  UNIQUE (invoice_id, line_no)
);
