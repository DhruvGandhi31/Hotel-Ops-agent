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
import time
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


def n8n_sql(query: str) -> str:
    """Run SQL in n8n's own database (its executions), as the Postgres superuser."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "sh",
            "-c",
            'psql -X -tA -U "${POSTGRES_USER:-postgres}" -d n8n',
        ],
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


def reconciliation_checks(manifest: dict, exclude: set) -> set:
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
    return {plain, *clear_cases}


def approval_checks(manifest: dict, exclude: set) -> None:
    """Human approval: the request after reconciliation, the form, the rules, the audit trail. Mock model."""
    from urllib.parse import urlsplit

    from hotel_evals.approval_client import APPROVAL_FORM_ID, ApprovalClient, FormClient, FormError, probe
    from hotel_evals.n8n_client import post_json

    email = os.environ.get("N8N_OWNER_EMAIL", "")
    password = os.environ.get("N8N_OWNER_PASSWORD", "")
    if not email or not password:
        check("N8N_OWNER_EMAIL and N8N_OWNER_PASSWORD are set (run scripts/setup-owner.sh)", False)
        return
    # The form must be reached at the very address n8n is configured with (N8N_WEBHOOK_URL): its sign-in
    # redirects are built from that setting, and cookies belong to one host name.
    parts = urlsplit(WEBHOOK)
    base = os.environ.get("N8N_WEBHOOK_URL", "").rstrip("/") or f"{parts.scheme}://{parts.netloc}"
    reconcile_url = WEBHOOK.rsplit("/", 1)[0] + "/reconcile"

    truth = {}
    for f in manifest["files"]:
        if f["expected_ingestion"] == "insert" and f["file_id"] not in exclude:
            truth[f["file_id"]] = json.loads(
                (DATA / "ground_truth" / f"{f['file_id']}.json").read_text(encoding="utf-8")
            )

    def pick(predicate, taken):
        return next(fid for fid, gt in truth.items() if fid not in taken and predicate(gt["labels"]))

    taken: set = set()
    clean_a = pick(lambda lab: not lab["reason_codes"] and not lab["conditions"], taken)
    taken.add(clean_a)
    clean_b = pick(lambda lab: not lab["reason_codes"] and not lab["conditions"], taken)
    taken.add(clean_b)
    flagged = pick(lambda lab: lab["reason_codes"] == ["price_variance"], taken)
    taken.add(flagged)
    doubtful = pick(lambda lab: not lab["reason_codes"] and "description_mismatch" in lab["conditions"], taken)
    taken.add(doubtful)
    hostile = pick(lambda lab: not lab["reason_codes"] and not lab["conditions"], taken)

    def number_of(fid):
        return truth[fid]["document"]["invoice_number"]

    def ingest(fid):
        code, body = upload(pdf(fid), f"{fid}.pdf")
        rec = body.get("reconciliation") or {}
        return code, body, rec, (rec.get("approval") or {})

    def row(approval_id):
        out = sql(
            "SELECT status, recommended_status, coalesce(decided_by, ''), coalesce(decided_by_user_id, ''), "
            f"coalesce(comment, '') FROM approvals WHERE id = {int(approval_id)}"
        )
        return out.split("|") if out else None

    def audit(invoice_id, action):
        return int(sql(f"SELECT count(*) FROM invoice_audit_trail({int(invoice_id)}) WHERE action = '{action}'"))

    started = n8n_sql("SELECT now()")  # runs of the Approval Form that start after this are ours
    print("approval requests")
    mode("oracle")
    code, body, rec, ap_clean = ingest(clean_a)
    check(
        "a reconciled invoice gets a pending approval in the same response",
        ap_clean.get("result") == "created" and ap_clean.get("approval_status") == "pending",
        str(ap_clean)[:200],
    )
    check(
        "a clean invoice's approval records the recommendation",
        ap_clean.get("recommended_status") == "recommend_approve",
    )
    id_clean = ap_clean.get("approval_id")
    inv_clean = body.get("invoice_id")
    check("the request is audited with the workflow as actor", audit(inv_clean, "approval.requested") == 1)
    code, again = upload(pdf(clean_a), f"{clean_a}.pdf")
    check(
        "uploading the same file again creates nothing (a no-op)",
        again.get("status") == "noop" and sql(f"SELECT count(*) FROM approvals WHERE invoice_id = {inv_clean}") == "1",
    )

    code, body, rec, ap_flag = ingest(flagged)
    id_flag, inv_flag = ap_flag.get("approval_id"), body.get("invoice_id")
    check(
        "a flagged invoice's approval carries its reason codes",
        ap_flag.get("recommended_status") == "flag"
        and sql(f"SELECT reason_codes FROM approvals WHERE id = {id_flag}") == "{price_variance}",
        str(ap_flag)[:200],
    )

    mode("matcher_none")  # the model says the reworded line is not on the PO: a doubt, not a discrepancy
    code, body, rec, ap_doubt = ingest(doubtful)
    mode("oracle")
    id_doubt, inv_doubt = ap_doubt.get("approval_id"), body.get("invoice_id")
    check(
        "an invoice with a doubt gets a needs_review approval",
        ap_doubt.get("recommended_status") == "needs_review",
        str(ap_doubt)[:200],
    )

    code, body, rec, ap_clean_b = ingest(clean_b)
    id_clean_b = ap_clean_b.get("approval_id")
    code, body, rec, ap_hostile = ingest(hostile)
    id_hostile, inv_hostile = ap_hostile.get("approval_id"), body.get("invoice_id")

    print("the form: signing in")
    try:
        status, location = probe(f"{base}/form/{APPROVAL_FORM_ID}")
    except FormError as exc:
        status, location = 0, str(exc)
    check(
        "the form is not shown to someone who is not signed in: n8n redirects to its sign-in",
        status == 302 and "/oauth/authorize" in location,
        f"HTTP {status} {location[:120]}",
    )
    try:
        FormClient(base).login(email, "definitely-not-the-password", attempts=1)
        wrong_ok = True
    except FormError:
        wrong_ok = False
    check("a wrong password does not sign in", not wrong_ok)

    me = ApprovalClient(base, email, password)

    print("the form: the pending list and the decision page")
    listing = me.pending_page()
    check(
        "the pending list shows the waiting invoices",
        all(number_of(f) in listing for f in (clean_a, flagged, doubtful)),
    )
    check(
        "most urgent first: flagged, then needs review, then recommended",
        listing.index(number_of(flagged)) < listing.index(number_of(doubtful)) < listing.index(number_of(clean_a)),
    )
    check(
        "each row links to its approval", f"approval_id={id_flag}" in listing and f"approval_id={id_clean}" in listing
    )
    page = me.decision_page(id_flag)
    check("the decision page names the signed-in approver", f"deciding as <b>{email}</b>" in page, "")
    check("and shows the reason code and the line", "price_variance" in page and "price above PO" in page)
    check(
        "and requires a reason for a flagged invoice (the field is required on the page)",
        "form-required" in page.split("Comment")[1][:400] or "required" in page.split("Comment")[-1][:600].lower(),
    )
    unknown = me.decide(99999999, "Approve", "x")
    check("an unknown approval says so and records nothing", "no approval number 99999999" in unknown, unknown[:200])
    bad_link = me.decide("abc", "Approve", "x")
    check("a malformed link says so", "not valid" in bad_link or "Invalid link" in bad_link, bad_link[:200])

    print("the form: deciding")
    result = me.decide(id_clean, "Approve", "")
    check("a clean invoice can be approved without a reason", "Recorded: approved" in result, result[:240])
    r = row(id_clean)
    check(
        "the decision is stored with the approver's identity",
        r is not None and r[0] == "approved" and r[2] == email and r[3] != "",
        str(r),
    )
    check(
        "and audited, with the approver as actor",
        audit(inv_clean, "approval.decided") == 1
        and sql(f"SELECT actor FROM invoice_audit_trail({inv_clean}) WHERE action = 'approval.decided'") == email,
    )
    second = me.decide(id_clean, "Reject", "changed my mind")
    check(
        "a second decision is refused and names the first",
        "already approved" in second.lower() and email in second,
        second[:240],
    )
    check(
        "the first decision stands and nothing more is audited",
        (row(id_clean) or [""])[0] == "approved" and audit(inv_clean, "approval.decided") == 1,
    )
    reopened = me.decide(id_clean, "Reject", "x")
    check("an approval that is decided shows no decision form", "Already approved" in reopened, reopened[:200])

    no_reason = me.decide(id_flag, "Approve", "")
    check("approving a flagged invoice without a reason is refused", "reason is required" in no_reason, no_reason[:240])
    r = row(id_flag)
    check(
        "and nothing was recorded or audited",
        r is not None and r[0] == "pending" and audit(inv_flag, "approval.decided") == 0,
        str(r),
    )
    ok = me.decide(id_flag, "Approve", "Supplier agreed the surcharge by phone.")
    check("with a reason it is recorded", "Recorded: approved" in ok, ok[:240])
    check(
        "and the reason is stored",
        (row(id_flag) or ["", "", "", "", ""])[4] == "Supplier agreed the surcharge by phone.",
    )

    rej = me.decide(id_doubt, "Reject", "No such item on the order.")
    r = row(id_doubt)
    check(
        "a doubtful invoice can be rejected with a reason",
        "Recorded: rejected" in rej and r is not None and r[0] == "rejected",
        rej[:240],
    )
    rej2 = me.decide(id_clean_b, "Reject", "")
    r = row(id_clean_b)
    check(
        "rejecting any invoice without a reason is refused",
        "reason is required" in rej2 and r is not None and r[0] == "pending",
        rej2[:240],
    )

    print("the form: hostile text")
    evil = '<script>alert(1)</script> & "x"'
    me.decide(id_hostile, "Approve", evil)
    raw = me.decision_page(id_hostile)
    check("a comment is stored exactly as typed", (row(id_hostile) or ["", "", "", "", ""])[4] == evil)
    check(
        "and shown as text, never as markup",
        "<script>alert(1)" not in raw and "&lt;script&gt;alert(1)&lt;/script&gt;" in raw,
    )
    left = me.pending_page()
    check(
        "decided approvals leave the pending list",
        number_of(clean_a) not in left and number_of(flagged) not in left and number_of(clean_b) in left,
    )

    print("re-running reconciliation")
    code, again = post_json(reconcile_url, TOKEN, {"invoice_id": inv_clean})
    ap = again.get("approval") or {}
    check(
        "re-reconciling a decided invoice leaves its decision alone",
        ap.get("result") == "decided" and ap.get("approval_status") == "approved" and ap.get("stale") is False,
        str(ap)[:200],
    )
    code, again = post_json(reconcile_url, TOKEN, {"invoice_id": body.get("invoice_id")})
    check(
        "it is still one approval per invoice",
        sql("SELECT count(*) FROM approvals") == sql("SELECT count(DISTINCT invoice_id) FROM approvals"),
    )

    print("requesting the approval fails after the invoice is stored")
    sql("ALTER FUNCTION request_approval(bigint, jsonb) RENAME TO request_approval_off")
    try:
        victim = pick(lambda lab: not lab["reason_codes"] and not lab["conditions"], taken | {hostile, clean_b})
        code, body2, rec2, ap2 = ingest(victim)
        check(
            "the invoice is still ingested and reconciled",
            code == 200 and body2.get("status") == "extracted" and rec2.get("status") == "recommend_approve",
            f"HTTP {code} {str(rec2)[:200]}",
        )
        check(
            "the response says the approval is pending, with the error",
            ap2.get("result") == "pending" and bool(ap2.get("error")),
            str(ap2)[:200],
        )
        inv_victim = body2.get("invoice_id")
        check("no approval exists yet", sql(f"SELECT count(*) FROM approvals WHERE invoice_id = {inv_victim}") == "0")
    finally:
        sql("ALTER FUNCTION request_approval_off(bigint, jsonb) RENAME TO request_approval")
    code, fixed = post_json(reconcile_url, TOKEN, {"invoice_id": inv_victim})
    check(
        "running the reconciliation again creates it",
        (fixed.get("approval") or {}).get("result") == "created",
        str(fixed.get("approval"))[:200],
    )

    print("no session is left running")
    left = "?"
    for _ in range(15):  # the last completion pages finish asynchronously
        left = n8n_sql(
            "SELECT count(*) FROM execution_entity WHERE \"workflowId\" = 'hoaApprovalForm01' "
            f"AND status = 'waiting' AND \"startedAt\" >= '{started}'"
        )
        if left == "0":
            break
        time.sleep(1)
    check(
        "every form session ended: none of the runs started by these checks is still waiting",
        left == "0",
        f"{left} waiting",
    )

    print("the audit trail of one invoice")
    trail = sql(f"SELECT string_agg(action, ',' ORDER BY audit_id) FROM invoice_audit_trail({inv_clean})")
    check(
        "reads ingest, reconcile, request, decision in order, then the deliberate re-run (and no approval row)",
        trail == "invoice.ingested,invoice.reconciled,approval.requested,approval.decided,invoice.reconciled",
        trail,
    )
    inv_unused = sql(f"SELECT string_agg(action, ',' ORDER BY audit_id) FROM invoice_audit_trail({inv_hostile})")
    check(
        "and every approval has its request audited",
        all(audit(i, "approval.requested") >= 1 for i in (inv_clean, inv_flag, inv_doubt, inv_hostile)),
        inv_unused,
    )


def main() -> int:
    if not TOKEN:
        print("set INGEST_WEBHOOK_TOKEN (the X-Ingest-Token value from .env)", file=sys.stderr)
        return 2
    sql(
        "TRUNCATE approvals, reconciliation_lines, reconciliations, invoice_lines, invoices, "
        "invoice_files RESTART IDENTITY"
    )
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
        "and exactly one ingestion audit row, then one each for the reconciliation and the approval request",
        int(sql("SELECT count(*) FROM audit_log")) == audit_before + 3
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

    used = reconciliation_checks(manifest, set(originals[:5]))
    approval_checks(manifest, set(originals[:5]) | used)

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
