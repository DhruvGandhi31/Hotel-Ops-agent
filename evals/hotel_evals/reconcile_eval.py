"""The reconciliation eval: invoices through the system, scored against the ground-truth labels.

Modes (see docs/decisions.md for why there are several):
  --source truth --matcher oracle   rules only: ground-truth extraction, ground-truth line matches,
                                    SQL only. Proves the rules are implemented as specified.
  --source truth --matcher n8n      ground-truth extraction, the real model matching the lines that
                                    need it, through the `Reconcile Invoice API` webhook.
  --source pdf                      end to end from the PDFs through the ingestion webhook (which
                                    reconciles after ingesting). The number that counts.

The synthetic labels and the reconciler share rules, so a near-perfect rules-only score proves the
implementation, not that the rules suit a real hotel. The gap between the modes is what extraction
and matching errors cost.
"""

import json
import os
import sys
from pathlib import Path

from .ops_db import dataset_fingerprint, dollar_quote, loaded_fingerprint, psql
from .oracle import truth_to_extraction
from .reconcile_scoring import case_from_ground_truth, got_from_result


class DatasetMismatch(RuntimeError):
    pass


def check_database_matches(data_dir: Path) -> None:
    """Master data and invoice tables must belong to this dataset, or results mean nothing."""
    want = dataset_fingerprint(data_dir / "seed.sql")
    have = loaded_fingerprint()
    if have != want:
        raise DatasetMismatch(
            f"the ops database holds master data for a different dataset (have {have and have[:12]}, "
            f"want {want[:12]}).\nRun: bash scripts/reset-ops-data.sh && bash scripts/seed.sh {data_dir / 'seed.sql'}"
        )
    invoices = int(psql("SELECT count(*) FROM invoices").strip())
    if invoices:
        raise DatasetMismatch(
            f"{invoices} invoices are already in the database; reconciliation is cumulative across invoices on a PO.\n"
            "Run: bash scripts/reset-ingestion.sh"
        )


def load_ground_truth(data_dir: Path) -> list[dict]:
    """Ground-truth files in arrival order, exact re-sends dropped (they are ingestion no-ops)."""
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for entry in manifest["files"]:
        if entry["expected_ingestion"] != "insert":
            continue
        gt = json.loads((data_dir / "ground_truth" / f"{entry['file_id']}.json").read_text(encoding="utf-8"))
        out.append({"file_id": entry["file_id"], "template": entry["template"], "gt": gt})
    return out


def truth_matches(gt: dict) -> list[dict]:
    """The perfect line matches: every invoice line to the PO line it truly is (None when no PO)."""
    return [
        {"invoice_line_no": ln["line_no"], "po_line_no": ln["po_line_no"], "confidence": 1.0}
        for ln in gt["labels"]["lines"]
        if ln["po_line_no"] is not None
    ]


def ingest_sql(item: dict) -> str:
    gt = item["gt"]
    payload = {
        "file_sha256": gt["file_sha256"],
        "filename": f"{item['file_id']}.pdf",
        "attempts": 1,
        "llm_model": "ground-truth",
        "workflow_name": "reconcile-eval",
        "extraction": truth_to_extraction(gt["document"]),
    }
    return f"ingest_invoice({dollar_quote(json.dumps(payload))}::jsonb)"


def run_rules_only(data_dir: Path) -> list[dict]:
    """Ingest each ground-truth extraction and reconcile it with the oracle line matches, in one session."""
    items = load_ground_truth(data_dir)
    statements = []
    for item in items:
        matches = dollar_quote(json.dumps(truth_matches(item["gt"])), "m")
        ctx = dollar_quote(json.dumps({"workflow_name": "reconcile-eval"}), "c")
        statements.append(
            "SELECT json_build_object('file_id', " + dollar_quote(item["file_id"], "f") + ", 'result', "
            f"reconcile_invoice((SELECT ({ingest_sql(item)} ->> 'invoice_id')::bigint), "
            f"{matches}::jsonb, {ctx}::jsonb));"
        )
    out = psql("\n".join(statements))
    results = {}
    for line in out.splitlines():
        line = line.strip()
        if line:
            row = json.loads(line)
            results[row["file_id"]] = row["result"]
    cases = []
    for item in items:
        case = case_from_ground_truth(item["file_id"], item["gt"])
        case["template"] = item["template"]
        case["got"] = got_from_result(results[item["file_id"]])
        cases.append(case)
    return cases


def _unusable_result(reason: str) -> dict:
    """A result for an invoice that never got a reconciliation (extraction failed, or reconciliation pending)."""
    return {"status": "needs_review", "reason_codes": [], "review_reasons": [reason], "lines": []}


def run_matcher_mode(data_dir: Path, webhook: str, token: str) -> list[dict]:
    """Ground-truth extraction ingested straight into the database, then reconciled through n8n so the
    real model matches the lines that need it. Invoices are reconciled in ingest order (cumulative rule)."""
    from .n8n_client import post_json

    items = load_ground_truth(data_dir)
    statements = [
        "SELECT json_build_object('file_id', " + dollar_quote(item["file_id"], "f") + ", 'invoice_id', "
        f"({ingest_sql(item)} ->> 'invoice_id')::bigint);"
        for item in items
    ]
    ids = {}
    for line in psql("\n".join(statements)).splitlines():
        if line.strip():
            row = json.loads(line)
            ids[row["file_id"]] = row["invoice_id"]
    cases = []
    for n, item in enumerate(items, start=1):
        code, body = post_json(webhook, token, {"invoice_id": ids[item["file_id"]]})
        case = case_from_ground_truth(item["file_id"], item["gt"])
        case["template"] = item["template"]
        if code == 200 and "status" in body and "lines" in body:
            case["got"] = got_from_result(body)
            case["matcher"] = body.get("matcher", {})
        else:
            case["got"] = got_from_result(_unusable_result(f"reconcile_http_{code}"))
            case["error"] = body
        cases.append(case)
        print(f"[{n}/{len(items)}] {item['file_id']} {case['got']['status']}", flush=True)
    return cases


def run_e2e(data_dir: Path, ingest_url: str, token: str) -> list[dict]:
    """The PDFs through the ingestion webhook (which reconciles after ingesting): the whole pipeline."""
    from .n8n_client import upload_invoice

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = []
    total = len(manifest["files"])
    for n, entry in enumerate(manifest["files"], start=1):
        pdf = (data_dir / entry["file"]).read_bytes()
        code, body = upload_invoice(ingest_url, token, f"{entry['file_id']}.pdf", pdf)
        if entry["expected_ingestion"] != "insert":
            print(f"[{n}/{total}] {entry['file_id']} re-send -> {body.get('status')}", flush=True)
            continue
        gt = json.loads((data_dir / "ground_truth" / f"{entry['file_id']}.json").read_text(encoding="utf-8"))
        case = case_from_ground_truth(entry["file_id"], gt)
        case["template"] = entry["template"]
        rec = body.get("reconciliation") if code == 200 else None
        if rec and "lines" in rec and "status" in rec:
            case["got"] = got_from_result(rec)
            case["matcher"] = rec.get("matcher", {})
        elif code == 200 and body.get("status") == "needs_review":
            case["got"] = got_from_result(_unusable_result("extraction_failed"))
        elif code == 200:
            case["got"] = got_from_result(_unusable_result("reconciliation_pending"))
        else:
            case["got"] = got_from_result(_unusable_result(f"upload_http_{code}"))
            case["error"] = body
        cases.append(case)
        print(f"[{n}/{total}] {entry['file_id']} {case['got']['status']}", flush=True)
    return cases


def workflow_model(root: Path) -> str:
    """The model the reconciliation workflow's Ollama node is configured with."""
    wf = json.loads((root / "workflows" / "reconcile_invoice.json").read_text(encoding="utf-8"))
    node = next(n for n in wf["nodes"] if n["type"].endswith("lmChatOllama"))
    return node["parameters"]["model"]


def run_suite(args, root: Path) -> int:
    """CLI entry for `run.py --suite reconciliation`. Returns the process exit status."""
    from . import reconcile_report
    from .n8n_client import TransportError
    from .reconcile_scoring import score_reconciliation

    mode = {"truth": {"oracle": "rules", "n8n": "matcher"}, "pdf": {"oracle": "e2e", "n8n": "e2e"}}[args.source][
        args.matcher
    ]
    data_dir = args.data
    try:
        check_database_matches(data_dir)
        if mode == "rules":
            cases = run_rules_only(data_dir)
        else:
            token = os.environ.get(args.token_env)
            if not token:
                print(f"set {args.token_env} (the X-Ingest-Token value from .env)", file=sys.stderr)
                return 2
            cases = (
                run_matcher_mode(data_dir, args.reconcile_webhook, token)
                if mode == "matcher"
                else run_e2e(data_dir, args.webhook, token)
            )
    except DatasetMismatch as exc:
        print(exc, file=sys.stderr)
        return 2
    except TransportError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 3

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    settings = json.loads(psql("SELECT jsonb_object_agg(key, value) FROM reconciliation_settings").strip())
    score = score_reconciliation(cases)
    meta = {
        "mode": mode,
        "mock": args.mock,
        "seed": manifest["seed"],
        "model": None if mode == "rules" else workflow_model(root),
        "dataset": str(data_dir.relative_to(root)) if data_dir.is_relative_to(root) else str(data_dir),
    }
    md = reconcile_report.render(meta, score, settings)

    name = f"reconciliation-{mode}" + ("-mock" if args.mock else "") + (f"-{args.tag}" if args.tag else "")
    report_path = args.out / f"{name}.md"
    if args.limit:
        print(md)
    else:
        if report_path.exists() and not args.overwrite:
            print(f"refusing to overwrite {report_path}; use --tag or --overwrite", file=sys.stderr)
            return 2
        args.out.mkdir(parents=True, exist_ok=True)
        report_path.write_text(md, encoding="utf-8", newline="\n")
        print(f"wrote {report_path}")

    fc = score["flag_class"]
    print(
        f"flag class: precision {fc['precision']}, recall {fc['recall']}; "
        f"{len(score['mismatches'])} mismatching invoices; {score['needs_review_count']} needs_review"
    )
    if args.expect_perfect and (score["mismatches"] or fc["fp"] or fc["fn"]):
        print("expected a perfect result and did not get one", file=sys.stderr)
        return 1
    return 0
