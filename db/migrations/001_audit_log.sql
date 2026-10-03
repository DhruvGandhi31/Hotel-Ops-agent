-- Append-only audit trail. Written by the Error Handler workflow, ingestion,
-- reconciliation and every human approval decision.

CREATE TABLE audit_log (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at  timestamptz NOT NULL DEFAULT now(),
  actor        text NOT NULL,          -- 'system', a workflow name, or the approver's identity
  action       text NOT NULL,          -- dotted verb, e.g. 'invoice.ingested', 'approval.decided', 'workflow.error'
  entity_type  text,                   -- e.g. 'invoice', 'reconciliation'
  entity_id    text,
  workflow_id  text,
  execution_id text,
  details      jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id);
CREATE INDEX audit_log_occurred_at_idx ON audit_log (occurred_at);

-- Guard against accidental edits. The table owner can still drop these
-- triggers, so this is a seatbelt, not a security boundary.
CREATE FUNCTION audit_log_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP;
END;
$$;

CREATE TRIGGER audit_log_no_update_delete
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change();

CREATE TRIGGER audit_log_no_truncate
  BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change();
