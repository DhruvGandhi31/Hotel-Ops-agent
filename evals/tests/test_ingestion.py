import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hotel_evals.extract import TransportError
from hotel_evals.ingestion import expected_kind, judge, summarise
from hotel_evals.n8n_client import multipart, upload_invoice

ORIGINAL = {"duplicate_of": None, "exact_resend_of": None}
DUPLICATE = {"duplicate_of": "inv_0001", "exact_resend_of": None}
RESEND = {"duplicate_of": None, "exact_resend_of": "inv_0001"}


def extracted(invoice_id, duplicate_of=None):
    return {"status": "extracted", "invoice_id": invoice_id, "duplicate_of": duplicate_of}


def test_expected_kind_from_labels():
    assert [expected_kind(x) for x in (ORIGINAL, DUPLICATE, RESEND)] == ["original", "duplicate", "noop"]


def test_original_must_be_stored_without_a_link():
    assert judge("inv_0001", ORIGINAL, extracted(7), {})["outcome"] == "ok"
    assert judge("inv_0001", ORIGINAL, extracted(7, duplicate_of=3), {})["outcome"] == "wrong"
    assert judge("inv_0001", ORIGINAL, {"status": "noop"}, {})["outcome"] == "wrong"  # dirty state, not a pass


def test_a_model_failure_is_not_an_ingestion_error():
    assert judge("inv_0001", ORIGINAL, {"status": "needs_review"}, {})["outcome"] == "model_failed"


def test_workflow_errors_are_wrong_not_swallowed():
    for status in ("error", "rejected", None):
        assert judge("inv_0001", ORIGINAL, {"status": status}, {})["outcome"] == "wrong"


def test_duplicate_must_point_at_its_original():
    seen = {"inv_0001": extracted(7)}
    assert judge("inv_0009", DUPLICATE, extracted(12, duplicate_of=7), seen)["outcome"] == "ok"
    assert judge("inv_0009", DUPLICATE, extracted(12, duplicate_of=99), seen)["outcome"] == "wrong"
    assert judge("inv_0009", DUPLICATE, extracted(12), seen)["outcome"] == "wrong"  # stored but not linked


def test_resend_must_be_a_noop_on_the_original_invoice():
    seen = {"inv_0001": extracted(7)}
    assert judge("inv_0009", RESEND, {"status": "noop", "invoice_id": 7}, seen)["outcome"] == "ok"
    assert judge("inv_0009", RESEND, {"status": "noop", "invoice_id": 8}, seen)["outcome"] == "wrong"
    assert judge("inv_0009", RESEND, extracted(12), seen)["outcome"] == "wrong"  # ingested again: not idempotent


def test_copies_of_a_failed_original_are_indeterminate():
    seen = {"inv_0001": {"status": "needs_review"}}
    assert judge("inv_0009", DUPLICATE, extracted(12), seen)["outcome"] == "indeterminate"
    assert judge("inv_0009", RESEND, {"status": "noop"}, seen)["outcome"] == "indeterminate"


def test_summary_counts_and_verdict():
    seen = {"inv_0001": extracted(7)}
    judgements = [
        judge("inv_0001", ORIGINAL, extracted(7), {}),
        judge("inv_0002", ORIGINAL, {"status": "needs_review"}, {}),
        judge("inv_0009", DUPLICATE, extracted(12, duplicate_of=7), seen),
    ]
    summary = summarise(judgements)
    assert summary["by_kind"] == {"duplicate": {"ok": 1}, "original": {"ok": 1, "model_failed": 1}}
    assert summary["all_ok"] and summary["wrong"] == []
    bad = summarise([*judgements, judge("inv_0010", RESEND, extracted(13), seen)])
    assert not bad["all_ok"] and [j["file_id"] for j in bad["wrong"]] == ["inv_0010"]


# ---- the upload client -----------------------------------------------------------------------


def test_multipart_body_has_the_file_field_and_a_matching_boundary():
    body, content_type = multipart("file", "a.pdf", b"%PDF-1.4 data")
    boundary = content_type.split("boundary=")[1]
    assert body.startswith(f"--{boundary}\r\n".encode()) and body.endswith(f"--{boundary}--\r\n".encode())
    assert b'name="file"; filename="a.pdf"' in body and b"%PDF-1.4 data" in body


class _Webhook:
    def __init__(self, status, body):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["token"] = self.headers.get("X-Ingest-Token")
                seen["type"] = self.headers.get("Content-Type", "")
                seen["bytes"] = self.rfile.read(int(self.headers["Content-Length"]))
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.seen = seen
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


def test_upload_sends_token_and_pdf_and_returns_status_and_json():
    with _Webhook(200, {"status": "extracted"}) as hook:
        code, body = upload_invoice(hook.url, "secret", "x.pdf", b"%PDF-1.7 payload")
    assert (code, body) == (200, {"status": "extracted"})
    assert hook.seen["token"] == "secret" and hook.seen["type"].startswith("multipart/form-data")
    assert b"%PDF-1.7 payload" in hook.seen["bytes"]


def test_upload_returns_http_errors_instead_of_raising():
    with _Webhook(500, {"message": "Error in workflow"}) as hook:
        assert upload_invoice(hook.url, "t", "x.pdf", b"x") == (500, {"message": "Error in workflow"})
    with _Webhook(403, b"Forbidden, not json") as hook:
        code, body = upload_invoice(hook.url, "t", "x.pdf", b"x")
    assert code == 403 and "Forbidden" in body["message"]


def test_upload_raises_only_when_the_server_is_unreachable():
    with pytest.raises(TransportError, match="unreachable"):
        upload_invoice("http://127.0.0.1:1/hook", "t", "x.pdf", b"x", backoff=(0,))
