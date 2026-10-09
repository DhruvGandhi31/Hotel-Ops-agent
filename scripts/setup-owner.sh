#!/usr/bin/env bash
# Create the n8n owner account from .env, so the Approval Form has someone to sign in as.
#
# The approval form authenticates with n8n's own accounts (Form Trigger "n8nUserAuth"), and the signed-in
# account is recorded as the approver. A fresh n8n has no account until someone completes its setup page;
# this does the same thing from the command line. Idempotent:
#   - no account yet            -> create the owner from N8N_OWNER_EMAIL / N8N_OWNER_PASSWORD
#   - that owner already exists -> nothing to do
#   - a different owner exists  -> stop and say so (it never touches an account it did not create)
#
# If .env predates these variables they are added to it with a generated password (.env is not committed).
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "setup-owner: .env not found; run scripts/init-env.sh" >&2; exit 1; }
set -a; . ./.env; set +a

if [[ -z "${N8N_OWNER_EMAIL:-}" || -z "${N8N_OWNER_PASSWORD:-}" ]]; then
  # n8n wants 8 to 64 characters with a digit and an upper-case letter.
  password="Ho1-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  {
    echo ""
    echo "# --- n8n owner account (scripts/setup-owner.sh); the approver identity on the Approval Form ---"
    echo "N8N_OWNER_EMAIL=owner@example.com"
    echo "N8N_OWNER_PASSWORD=$password"
  } >> .env
  echo "setup-owner: added N8N_OWNER_EMAIL and N8N_OWNER_PASSWORD to .env"
  set -a; . ./.env; set +a
fi

base="http://127.0.0.1:${N8N_HOST_PORT:-5678}"

# POST JSON and set HTTP_CODE to the status. The body is captured instead of being written to /dev/null:
# curl.exe on Windows is a native program, so `-o /dev/null` only works through Git Bash's path conversion,
# which MSYS_NO_PATHCONV=1 (used by the other scripts here) switches off. The write then fails after n8n has
# already acted, and `set -e` ends the script halfway, with the account created and nothing reported.
post_json() {
  local out
  out=$(curl -sS -w '\n%{http_code}' -X POST "$1" -H 'Content-Type: application/json' -d "$2") || return 1
  HTTP_CODE=${out##*$'\n'}
}

for _ in $(seq 1 60); do
  curl -fsS "$base/healthz" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "$base/healthz" >/dev/null 2>&1 || { echo "setup-owner: n8n is not answering at $base" >&2; exit 1; }

body="{\"email\":\"$N8N_OWNER_EMAIL\",\"firstName\":\"Hotel\",\"lastName\":\"Owner\",\"password\":\"$N8N_OWNER_PASSWORD\"}"

# Captured first, not piped into `grep -q`: grep exits at the first match, curl then fails with a broken pipe,
# and pipefail would turn that into a failure.
settings=$(curl -fsS "$base/rest/settings")
if [[ "$settings" == *'"showSetupOnFirstLoad":true'* ]]; then
  post_json "$base/rest/owner/setup" "$body" || { echo "setup-owner: could not reach $base" >&2; exit 1; }
  [[ "$HTTP_CODE" == "200" ]] || { echo "setup-owner: owner setup failed (HTTP $HTTP_CODE)" >&2; exit 1; }
  echo "setup-owner: created the owner account $N8N_OWNER_EMAIL"
  exit 0
fi

login="{\"emailOrLdapLoginId\":\"$N8N_OWNER_EMAIL\",\"password\":\"$N8N_OWNER_PASSWORD\"}"
post_json "$base/rest/login" "$login" || { echo "setup-owner: could not reach $base" >&2; exit 1; }
if [[ "$HTTP_CODE" == "200" ]]; then
  echo "setup-owner: the owner account $N8N_OWNER_EMAIL already exists"
  exit 0
fi
echo "setup-owner: n8n already has an account, and it is not $N8N_OWNER_EMAIL with the password in .env" >&2
echo "             (login returned HTTP $HTTP_CODE). Use that account, or set N8N_OWNER_EMAIL/N8N_OWNER_PASSWORD to it." >&2
exit 1
