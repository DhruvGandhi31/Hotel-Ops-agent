"""End-to-end checks of the ingestion pipeline's *behaviour*, with the mock model (no GPU).

    python evals/mock_ollama.py --data data-gen/out --port 11500 &      # the model
    OLLAMA_BASE_URL=http://host.docker.internal:11500 bash scripts/setup-credentials.sh
    python evals/pipeline_check.py

Exercises what the accuracy eval cannot: the retry, the needs_review path, outages, bad uploads,
idempotency and what lands in the database. Needs a running stack, a seeded `ops` database (for
supplier resolution) and the dataset in data-gen/out. It resets ingestion state first.
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from hotel_evals.n8n_client import upload_invoice

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data-gen" / "out"
WEBHOOK = os.environ.get("WEBHOOK", "http://127.0.0.1:5678/webhook/invoice-upload")
MOCK = os.environ.get("MOCK", "http://127.0.0.1:11500")
TOKEN = os.environ.get("INGEST_WEBHOOK_TOKEN", "")

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def sql(query: str) -> str:
    out = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", 'psql -X -tA -U "${OPS_DB_USER:-postgres}" -d ops'],
        input=query,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    return out.stdout.strip()


def mode(name: str) -> None:
    req = urllib.request.Request(
        f"{MOCK}/__mode", json.dumps({"mode": name}).encode(), {"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req).read()


def mock_stats() -> dict:
    return json.loads(urllib.request.urlopen(f"{MOCK}/__stats").read())


def pdf(file_id: str) -> bytes:
    return (DATA / "invoices" / f"{file_id}.pdf").read_bytes()


def upload(content: bytes, name: str = "x.pdf", token: str | None = None) -> tuple[int, dict]:
    return upload_invoice(WEBHOOK, TOKEN if token is None else token, name, content, timeout=120, backoff=(1,))


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def blank_pdf() -> bytes:
    """A valid PDF with no text layer: what a scan looks like to a text extractor."""
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    c.rect(50, 50, 200, 100)  # a drawing, no text
    c.save()
    return buf.getvalue()


def reconciliation_checks(manifest: dict, exclude: set) -> None:
    """Reconciliation behaviour, with the mock model. Needs the dataset's master data to be seeded."""
    from hotel_evals.n8n_client import post_json

    reconcile_url = WEBHOOK.rsplit("/", 1)[0] + "/reconcile"
    by_id = {}
    for f in manifest["files"]:
        if f["expected_ingestion"] == "insert" and f["file_id"] not in exclude:  # already ingested above
            by_id[f["file_id"]] = json.loads(
                (DATA / "ground_truth" / f"{f['file_id']}.json").read_text(encoding="utf-8")
            )

    def aliased(skip: set) -> str:
        return next(
            fid
            for fid, gt in by_id.items()
            if fid not in skip
            and not gt["labels"]["reason_codes"]
            and any("description_mismatch" in ln["issues"] for ln in gt["labels"]["lines"])
        )

    plain = next(
        fid for fid, gt in by_id.items() if not gt["labels"]["reason_codes"] and not gt["labels"]["conditions"]
    )
    used: set = set()
    clear_cases = [aliased(used)]
    used.add(clear_cases[0])
    for _ in range(5):
        clear_cases.append(aliased(used))
        used.add(clear_cases[-1])
    a1, a2, a3, a4, a5, a6 = clear_cases

    print("reconciliation after ingest")
    mode("oracle")
    code, body = upload(pdf(plain), f"{plain}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "a clean invoice is reconciled in the same response", rec.get("status") == "recommend_approve", str(rec)[:200]
    )
    check("without needing the matcher", (rec.get("matcher") or {}).get("status") == "not_needed")
    check(
        "and the result is stored once, with an audit row",
        sql(f"SELECT count(*) FROM reconciliations WHERE invoice_id = {body.get('invoice_id')}") == "1"
        and int(
            sql(
                f"SELECT count(*) FROM audit_log WHERE entity_id = '{body.get('invoice_id')}' "
                "AND action = 'invoice.reconciled'"
            )
        )
        >= 1,
    )

    print("reworded lines go to the matcher")
    before = mock_stats().get("matcher_requests", 0)
    code, body = upload(pdf(a1), f"{a1}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "a reworded line is matched by the model and the invoice approves",
        rec.get("status") == "recommend_approve",
        str(rec)[:240],
    )
    check(
        "one model call, reported as matched",
        (rec.get("matcher") or {}).get("status") == "matched" and mock_stats()["matcher_requests"] == before + 1,
    )
    check(
        "the line is recorded as matched by llm, with its confidence",
        any(ln.get("match_method") == "llm" and ln.get("match_confidence") for ln in rec.get("lines", [])),
    )

    print("the matcher returns bad JSON once")
    mode("garbage_once")
    code, body = upload(pdf(a2), f"{a2}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "the one retry rescues it",
        (rec.get("matcher") or {}).get("attempts") == 2 and rec.get("status") == "recommend_approve",
        str(rec)[:240],
    )

    print("the matcher returns bad JSON every time")
    mode("matcher_garbage_always")
    code, body = upload(pdf(a3), f"{a3}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "the invoice is stored and goes to needs_review",
        body.get("status") == "extracted" and rec.get("status") == "needs_review",
        f"HTTP {code} {str(body)[:300]}",
    )
    check(
        "the reason is the unmatched line, never a guess",
        "unmatched_line" in rec.get("review_reasons", []) and (rec.get("matcher") or {}).get("status") == "failed",
    )

    print("the matcher is unsure, or says the line is not on the PO")
    mode("matcher_unsure")
    code, body = upload(pdf(a4), f"{a4}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "low confidence is not trusted",
        rec.get("status") == "needs_review" and "low_confidence_match" in rec.get("review_reasons", []),
        str(rec)[:240],
    )
    mode("matcher_none")
    code, body = upload(pdf(a5), f"{a5}.pdf")
    rec = body.get("reconciliation") or {}
    check(
        "'not on the PO' leaves the line unmatched",
        rec.get("status") == "needs_review" and "unmatched_line" in rec.get("review_reasons", []),
        str(rec)[:240],
    )

    print("the matcher's model call fails after the invoice was stored")
    mode("matcher_server_error")
    code, body = upload(pdf(a6), f"{a6}.pdf")
    rec = body.get("reconciliation") or {}
    invoice_id = body.get("invoice_id")
    check(
        "the invoice is still ingested",
        code == 200 and body.get("status") == "extracted" and invoice_id is not None,
        f"HTTP {code} {str(body)[:200]}",
    )
    check(
        "reconciliation reports itself pending, with the error",
        rec.get("status") == "pending" and bool(rec.get("error")),
        str(rec)[:200],
    )
    check(
        "no result is stored for it yet",
        sql(f"SELECT count(*) FROM reconciliations WHERE invoice_id = {invoice_id}") == "0",
    )
    mode("oracle")
    code, again = post_json(reconcile_url, TOKEN, {"invoice_id": invoice_id})
    check(
        "running it again once the server is back completes it",
        code == 200 and again.get("status") == "recommend_approve",
        f"HTTP {code} {str(again)[:200]}",
    )
    check("and it is stored", sql(f"SELECT count(*) FROM reconciliations WHERE invoice_id = {invoice_id}") == "1")

    print("the reconcile API")
    code, first = post_json(reconcile_url, TOKEN, {"invoice_id": invoice_id})
    code2, second = post_json(reconcile_url, TOKEN, {"invoice_id": invoice_id})
    check(
        "is idempotent: the same invoice gives the same result and one row",
        first.get("status") == second.get("status")
        and first.get("reason_codes") == second.get("reason_codes")
        and sql(f"SELECT count(*) FROM reconciliations WHERE invoice_id = {invoice_id}") == "1",
    )
    check("rejects a body without an invoice id (400)", post_json(reconcile_url, TOKEN, {})[0] == 400)
    check("rejects a non-integer id (400)", post_json(reconcile_url, TOKEN, {"invoice_id": "7"})[0] == 400)
    check(
        "answers 404 for an invoice that does not exist",
        post_json(reconcile_url, TOKEN, {"invoice_id": 99999999})[0] == 404,
    )
    check("refuses a wrong token (403)", post_json(reconcile_url, "wrong", {"invoice_id": invoice_id})[0] == 403)


def main() -> int:
    if not TOKEN:
        print("set INGEST_WEBHOOK_TOKEN (the X-Ingest-Token value from .env)", file=sys.stderr)
        return 2
    sql("TRUNCATE reconciliation_lines, reconciliations, invoice_lines, invoices, invoice_files RESTART IDENTITY")
    mode("oracle")
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    originals = [f["file_id"] for f in manifest["files"] if f["expected_ingestion"] == "insert"]
    a, b, c, d, e = originals[:5]

    print("happy path")
    audit_before = int(sql("SELECT count(*) FROM audit_log"))  # append-only: survives the reset above
    ingested_before = int(sql("SELECT count(*) FROM audit_log WHERE action = 'invoice.ingested'"))
    code, body = upload(pdf(a), f"{a}.pdf")
    check("a new invoice is extracted", code == 200 and body.get("status") == "extracted", str(body)[:200])
    check("it was valid on the first attempt", body.get("attempts") == 1)
    check("the response carries the extraction", isinstance(body.get("extraction"), dict))
    check(
        "the invoice is in the database",
        sql(f"SELECT count(*) FROM invoices WHERE id = {body.get('invoice_id')}") == "1",
    )
    check(
        "with its lines",
        int(sql(f"SELECT count(*) FROM invoice_lines WHERE invoice_id = {body.get('invoice_id')}")) >= 1,
    )
    check(
        "and exactly one ingestion audit row, plus one for the reconciliation that follows it",
        int(sql("SELECT count(*) FROM audit_log")) == audit_before + 2
        and int(sql("SELECT count(*) FROM audit_log WHERE action = 'invoice.ingested'")) == ingested_before + 1,
    )
    first_id = body.get("invoice_id")

    print("idempotency: the same bytes again")
    calls_before = mock_stats()["requests"]
    code, body = upload(pdf(a), f"{a}.pdf")
    check(
        "is a no-op",
        code == 200 and body.get("status") == "noop" and body.get("invoice_id") == first_id,
        str(body)[:200],
    )
    check("without calling the model", mock_stats()["requests"] == calls_before)
    check("and without a second row", sql("SELECT count(*) FROM invoices") == "1")

    print("the model returns bad JSON once")
    mode("garbage_once")
    code, body = upload(pdf(b), f"{b}.pdf")
    check("the retry rescues it", body.get("status") == "extracted" and body.get("attempts") == 2, str(body)[:200])
    stats = mock_stats()
    check("exactly one retry was sent, carrying the errors", stats["requests"] == 2 and stats["retries"] == 1)

    print("the model returns bad JSON every time")
    mode("garbage_always")
    code, body = upload(pdf(c), f"{c}.pdf")
    check(
        "it ends in needs_review after two attempts",
        body.get("status") == "needs_review" and body.get("attempts") == 2,
        str(body)[:200],
    )
    check("with the reason", bool(body.get("reason")))
    check(
        "no invoice is created",
        sql(f"SELECT count(*) FROM invoices WHERE file_sha256 = '{body.get('file_sha256')}'") == "0",
    )
    check(
        "the file and the model's last output are kept",
        sql(f"SELECT status || '/' || attempts FROM invoice_files WHERE file_sha256 = '{body.get('file_sha256')}'")
        == "needs_review/2",
    )
    mode("oracle")
    code, again = upload(pdf(c), f"{c}.pdf")
    check(
        "uploading it again does not silently retry",
        again.get("status") == "noop" and again.get("existing_status") == "needs_review",
        str(again)[:200],
    )

    print("the model server is down")
    mode("server_error")
    files_before = sql("SELECT count(*) FROM invoice_files")
    errors_before = int(sql("SELECT count(*) FROM audit_log WHERE action = 'workflow.error'"))
    code, body = upload(pdf(d), f"{d}.pdf")
    check("the caller gets a 5xx, not a verdict", code >= 500, f"HTTP {code} {body}")
    check(
        "the file is NOT recorded, so it can be uploaded again",
        sql("SELECT count(*) FROM invoice_files") == files_before,
    )
    check(
        "the failure is audited",
        int(sql("SELECT count(*) FROM audit_log WHERE action = 'workflow.error'")) == errors_before + 1,
    )
    mode("oracle")
    code, body = upload(pdf(d), f"{d}.pdf")
    check("once the server is back the same file succeeds", body.get("status") == "extracted", str(body)[:200])

    print("a PDF with no text layer")
    calls_before = mock_stats()["requests"]
    code, body = upload(blank_pdf(), "scan.pdf")
    check(
        "goes to needs_review without a model call",
        body.get("status") == "needs_review" and body.get("attempts") == 0 and mock_stats()["requests"] == calls_before,
        str(body)[:200],
    )

    print("a PDF the extractor cannot read")
    calls_before = mock_stats()["requests"]
    code, body = upload(b"%PDF-1.4 this is not really a pdf, just a header", "corrupt.pdf")
    check(
        "goes to needs_review with the reason, not a 5xx",
        code == 200 and body.get("status") == "needs_review" and "could not be read" in body.get("reason", ""),
        str(body)[:200],
    )
    check("without a model call", mock_stats()["requests"] == calls_before and body.get("attempts") == 0)

    print("the PDF text service is down")
    compose("stop", "pdf-text")
    try:
        files_before = sql("SELECT count(*) FROM invoice_files")
        errors_before = int(sql("SELECT count(*) FROM audit_log WHERE action = 'workflow.error'"))
        code, body = upload(pdf(e), f"{e}.pdf")
        check("the caller gets a 5xx, not a verdict", code >= 500, f"HTTP {code} {body}")
        check("the file is NOT recorded", sql("SELECT count(*) FROM invoice_files") == files_before)
        check(
            "the failure is audited",
            int(sql("SELECT count(*) FROM audit_log WHERE action = 'workflow.error'")) == errors_before + 1,
        )
    finally:
        compose("up", "-d", "--wait", "pdf-text")
    code, body = upload(pdf(e), f"{e}.pdf")
    check("once it is back the same file succeeds", body.get("status") == "extracted", str(body)[:200])

    reconciliation_checks(manifest, set(originals[:5]))

    print("bad uploads")
    code, body = upload(pdf(e), "x.pdf", token="wrong")
    check("a wrong token is refused", code == 403, f"HTTP {code}")
    code, body = upload(b"this is not a pdf", "x.pdf")
    check("a non-PDF is rejected with 415", code == 415 and body.get("status") == "rejected", f"HTTP {code} {body}")
    check(
        "and nothing is recorded for it",
        sql("SELECT count(*) FROM invoice_files WHERE original_filename = 'x.pdf'") == "0",
    )

    print("a known supplier is resolved by ABN")
    known = sql("SELECT count(*) FROM invoices WHERE supplier_id IS NOT NULL")
    check("invoices from seeded suppliers are linked", int(known) >= 1, f"{known} linked")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): " + "; ".join(failures))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
