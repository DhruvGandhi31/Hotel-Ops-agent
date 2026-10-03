#!/bin/sh
# Runs once, on an empty data volume (Postgres entrypoint behaviour).
# Creates one role per database so n8n internals and app data stay separated.
# No `set -eu`: the entrypoint sources non-executable init scripts, so shell
# options would leak into it. ON_ERROR_STOP makes psql failures fatal instead.

psql -v ON_ERROR_STOP=1 -X --username "$POSTGRES_USER" --dbname postgres \
  -v n8n_user="$N8N_DB_USER" -v n8n_pass="$N8N_DB_PASSWORD" \
  -v ops_user="$OPS_DB_USER" -v ops_pass="$OPS_DB_PASSWORD" <<'SQL'
CREATE ROLE :"n8n_user" LOGIN PASSWORD :'n8n_pass';
CREATE DATABASE n8n OWNER :"n8n_user";
REVOKE ALL ON DATABASE n8n FROM PUBLIC;

CREATE ROLE :"ops_user" LOGIN PASSWORD :'ops_pass';
CREATE DATABASE ops OWNER :"ops_user";
REVOKE ALL ON DATABASE ops FROM PUBLIC;
SQL
