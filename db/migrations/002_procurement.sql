-- Master data the reconciler checks invoices against: suppliers, purchase orders and
-- goods receiving. Money is integer cents ex GST; quantities allow 3 decimals (weighed goods).

CREATE TABLE suppliers (
  id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  abn                text NOT NULL UNIQUE CHECK (abn ~ '^[0-9]{11}$'),
  name               text NOT NULL,
  category           text NOT NULL,
  email              text,
  phone              text,
  address            text,
  payment_terms_days integer NOT NULL CHECK (payment_terms_days >= 0),
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE purchase_orders (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  po_number              text NOT NULL UNIQUE,
  supplier_id            bigint NOT NULL REFERENCES suppliers (id),
  order_date             date NOT NULL,
  expected_delivery_date date,
  status                 text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
  created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX purchase_orders_supplier_idx ON purchase_orders (supplier_id);

CREATE TABLE purchase_order_lines (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  purchase_order_id bigint NOT NULL REFERENCES purchase_orders (id) ON DELETE CASCADE,
  line_no           integer NOT NULL CHECK (line_no > 0),
  supplier_sku      text,
  description       text NOT NULL,
  unit              text NOT NULL,
  quantity          numeric(12, 3) NOT NULL CHECK (quantity > 0),
  unit_price_cents  bigint NOT NULL CHECK (unit_price_cents >= 0),
  gst_applicable    boolean NOT NULL,
  UNIQUE (purchase_order_id, line_no)
);

CREATE INDEX purchase_order_lines_sku_idx ON purchase_order_lines (supplier_sku);

CREATE TABLE receipts (
  id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  receipt_number    text NOT NULL UNIQUE,
  purchase_order_id bigint NOT NULL REFERENCES purchase_orders (id),
  received_date     date NOT NULL,
  received_by       text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX receipts_purchase_order_idx ON receipts (purchase_order_id);

-- A receipt line should reference a line of the receipt's own PO. Not enforced by a
-- constraint (it would need a composite FK); the seed and ingestion paths guarantee it.
CREATE TABLE receipt_lines (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  receipt_id             bigint NOT NULL REFERENCES receipts (id) ON DELETE CASCADE,
  purchase_order_line_id bigint NOT NULL REFERENCES purchase_order_lines (id),
  quantity_received      numeric(12, 3) NOT NULL CHECK (quantity_received >= 0),
  UNIQUE (receipt_id, purchase_order_line_id)
);

-- Which generated dataset the master data came from (scripts/seed.sh). Lets a re-seed
-- refuse to mix rows from a different seed or generator version.
CREATE TABLE seed_metadata (
  key   text PRIMARY KEY,
  value text NOT NULL
);
