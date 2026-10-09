"""The end-to-end demo: an invoice dropped on the upload webhook, through extraction, reconciliation and an
approval request, to a recorded human decision, with the invoice's full audit trail at the end.

    set -a; . ./.env; set +a
    python evals/demo.py                                   # you open the form in a browser and decide
    python evals/demo.py --decide approve --comment "..."  # the script decides through the same form

Needs the stack running, the workflows imported and published, an owner account (scripts/setup-owner.sh), master
data seeded (scripts/seed.sh) and a model for extraction (Ollama, or the mock model for a dry run). The invoice is
chosen from the synthetic dataset in data-gen/out; nothing here touches real data.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from hotel_evals.approval_client import APPROVAL_FORM_ID, ApprovalClient, FormError
from hotel_evals.n8n_client import TransportError, upload_invoice
from hotel_evals.ops_db import DatabaseError, dataset_fingerprint, loaded_fingerprint, psql

ROOT = Path(__file__).resolve().parents[1]


def heading(text: str) -> None:
    print(f"\n== {text}")


def candidates(data_dir: Path, kind: str) -> list[Path]:
    """Dataset invoices of the wanted kind: one price variance for 'flagged', no findings for 'clean'."""
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for entry in manifest["files"]:
        if entry["expected_ingestion"] != "insert":
            continue
        labels = json.loads((data_dir / "ground_truth" / f"{entry['file_id']}.json").read_text(encoding="utf-8"))[
            "labels"
        ]
        wanted = labels["reason_codes"] == ["price_variance"] if kind == "flagged" else not labels["reason_codes"]
        if wanted and not labels["conditions"]:
            out.append(data_dir / entry["file"])
    return out


def money(cents) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


def trail_summary(action: str, d: dict) -> str:
    if action == "approval.decided":
        comment = f' - "{d["comment"]}"' if d.get("comment") else ""
        return f"{d.get('decision')} (the approver saw: {d.get('recommended_status')}){comment}"
    if action in ("approval.requested", "approval.refreshed", "invoice.reconciled"):
        codes = ", ".join([*d.get("reason_codes", []), *d.get("review_reasons", [])]) or "no findings"
        return f"{d.get('status') or d.get('recommended_status') or ''} {codes}".strip()
    return ""


def print_trail(invoice_id: int) -> None:
    rows = json.loads(
        psql(f"SELECT coalesce(json_agg(t ORDER BY t.audit_id), '[]') FROM invoice_audit_trail({invoice_id}) t")
    )
    print(f"{'time (UTC)':<10} {'actor':<28} {'action':<20} detail")
    for r in rows:
        when, who, action = r["occurred_at"][11:19], r["actor"][:27], r["action"]
        print(f"{when:<10} {who:<28} {action:<20} {trail_summary(action, r['details'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, help="an invoice PDF to upload (default: pick one from the dataset)")
    ap.add_argument(
        "--kind", choices=["flagged", "clean"], default="flagged", help="which kind of dataset invoice to pick"
    )
    ap.add_argument("--data", type=Path, default=ROOT / "data-gen" / "out", help="dataset directory")
    ap.add_argument("--decide", choices=["approve", "reject"], help="decide through the form from this script")
    ap.add_argument("--comment", default="", help="the reason, with --decide")
    ap.add_argument(
        "--base-url",
        default=os.environ.get("N8N_WEBHOOK_URL") or f"http://localhost:{os.environ.get('N8N_HOST_PORT', '5678')}",
        help="n8n's address; must be the address N8N_WEBHOOK_URL names, because sign-in redirects are built from it",
    )
    args = ap.parse_args(argv)

    token = os.environ.get("INGEST_WEBHOOK_TOKEN")
    email, password = os.environ.get("N8N_OWNER_EMAIL"), os.environ.get("N8N_OWNER_PASSWORD")
    if not (token and email and password):
        print(
            "set INGEST_WEBHOOK_TOKEN, N8N_OWNER_EMAIL and N8N_OWNER_PASSWORD (set -a; . ./.env; set +a)",
            file=sys.stderr,
        )
        return 2
    base = args.base_url.rstrip("/")

    try:
        if not args.file:
            # The invoices come from this dataset; its suppliers and purchase orders must be the ones in the
            # database, or every invoice is "supplier unknown" and nothing is matched (as in a first real run).
            want, have = dataset_fingerprint(args.data / "seed.sql"), loaded_fingerprint()
            if have != want:
                print(
                    f"the database holds master data for a different dataset than {args.data}: the demo's invoices "
                    "would all come out as 'supplier unknown'.\n"
                    "Load this dataset's master data first: bash scripts/reset-ops-data.sh && "
                    f"bash scripts/seed.sh {args.data / 'seed.sql'}",
                    file=sys.stderr,
                )
                return 2
        heading("1. Drop an invoice on the upload webhook")
        files = [args.file] if args.file else candidates(args.data, args.kind)
        body: dict = {}
        for pdf in files:
            print(f"uploading {pdf.name} ...")
            code, body = upload_invoice(
                f"{base}/webhook/invoice-upload", token, pdf.name, pdf.read_bytes(), timeout=900
            )
            if body.get("status") == "extracted":
                break
            print(f"  not new ({body.get('status')}), trying the next one")
        else:
            print("no new invoice to upload; run scripts/reset-ingestion.sh or pass --file", file=sys.stderr)
            return 1
        inv = body["extraction"]
        print(
            f"  extracted by {body['llm_model']}: invoice {inv['invoice_number']} from {inv['supplier_name']}, "
            f"{len(inv['lines'])} lines, total ${inv['total']}"
        )

        heading("2. Reconciliation (deterministic rules, in SQL)")
        rec = body.get("reconciliation") or {}
        if "lines" not in rec:
            print(f"  reconciliation is {rec.get('status')}: {rec.get('error')}", file=sys.stderr)
            return 1
        print(f"  recommendation: {rec['status']}")
        for code in [*rec["reason_codes"], *rec["review_reasons"]]:
            print(f"    - {code}")
        matcher = (rec.get("matcher") or {}).get("status")
        print(f"  line matching by the model: {matcher}")

        heading("3. Approval requested (nothing is approved yet)")
        approval = rec.get("approval") or {}
        if approval.get("result") not in ("created", "unchanged", "refreshed", "decided"):
            print(f"  no approval request: {approval}", file=sys.stderr)
            return 1
        approval_id, invoice_id = approval["approval_id"], rec["invoice_id"]
        print(
            f"  approval {approval_id} is {approval['approval_status']}, recommended: {approval['recommended_status']}"
        )
        print(f"  pending list : {base}/form/{APPROVAL_FORM_ID}")
        print(f"  this invoice : {base}/form/{APPROVAL_FORM_ID}?approval_id={approval_id}")

        heading("4. A person decides")
        if args.decide:
            print(f"  deciding as {email} through the form: {args.decide}, reason: {args.comment or '(none)'}")
            client = ApprovalClient(base, email, password)
            print(f"  result page: {client.decide(approval_id, args.decide.capitalize(), args.comment)}")
        else:
            print(f"  open the invoice link above and sign in as {email} (the password is N8N_OWNER_PASSWORD in .env).")
            input("  decide in the browser, then press Enter here ... ")

        heading("5. What is on record")
        row = (
            psql(
                "SELECT status, coalesce(decided_by, '-'), "
                "coalesce(to_char(decided_at, 'YYYY-MM-DD HH24:MI:SS'), '-'), "
                f"coalesce(comment, '-') FROM approvals WHERE id = {int(approval_id)}"
            )
            .strip()
            .split("|")
        )
        print(f"  approval {approval_id}: {row[0]}; decided by {row[1]} at {row[2]} UTC; reason: {row[3]}")
        print("\n  audit trail of this invoice:")
        print_trail(int(invoice_id))
        if row[0] == "pending":
            print("\n  (still pending: nothing was decided)")
    except (TransportError, FormError, DatabaseError) as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
