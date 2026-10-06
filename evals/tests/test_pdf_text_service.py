"""The PDF text service, and the guarantee that the eval harness and the workflow see the same text."""

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader

from hotel_datagen.build import build_dataset
from hotel_datagen.output import write_dataset

SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "pdf-text"
ROOT = SERVICE_DIR.parents[1]

import sys  # noqa: E402

sys.path.insert(0, str(SERVICE_DIR))
import app as pdf_app  # noqa: E402
from pdf_text import extract_text  # noqa: E402


@pytest.fixture(scope="module")
def invoices(tmp_path_factory) -> list[Path]:
    out = tmp_path_factory.mktemp("pdfs")
    write_dataset(build_dataset(24, 5), out)
    return sorted((out / "invoices").glob("*.pdf"))


@pytest.fixture()
def service():
    server = pdf_app.serve("127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def post(base: str, path: str, body: bytes) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, data=body, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_extract_text_is_what_the_eval_harness_computed_before_the_service_existed(invoices):
    for path in invoices[:6]:
        text, pages = extract_text(path.read_bytes())
        expected = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        assert text == expected and pages == len(PdfReader(path).pages)


def test_eval_harness_and_service_share_one_implementation():
    """run.py must import the service's function rather than keep a copy that can drift."""
    run_py = (ROOT / "evals" / "run.py").read_text(encoding="utf-8")
    assert "from pdf_text import extract_text" in run_py
    assert "PdfReader" not in run_py


def test_service_pin_matches_the_eval_pin():
    dockerfile = (SERVICE_DIR / "Dockerfile").read_text(encoding="utf-8")
    service_pin = re.search(r"pip install pypdf==([0-9.]+)", dockerfile)
    dev_pin = re.search(r"^pypdf==([0-9.]+)$", (ROOT / "requirements-dev.txt").read_text(encoding="utf-8"), re.M)
    assert service_pin and dev_pin and service_pin.group(1) == dev_pin.group(1)


def test_service_returns_text_and_page_count(invoices, service):
    code, body = post(service, "/extract", invoices[0].read_bytes())
    assert code == 200 and body["pages"] >= 1
    assert body["text"] == extract_text(invoices[0].read_bytes())[0] and "error" not in body


def test_table_cells_stay_on_separate_lines(invoices, service):
    """The reason the service exists: one cell per line, not a space-joined row."""
    template_d = next(
        p
        for p in invoices
        if "Delivery Docket" in extract_text(p.read_bytes())[0] or "UOM" in extract_text(p.read_bytes())[0]
    )
    text = extract_text(template_d.read_bytes())[0]
    assert re.search(r"^[0-9]+\.[0-9]{2}$", text, re.M), "amounts should each sit on their own line"


@pytest.mark.parametrize(
    "garbage",
    [b"%PDF-1.4 this is not really a pdf", b"", b"%PDF-" + b"\x00" * 200],
    ids=["truncated", "empty", "binary junk"],
)
def test_unreadable_pdf_is_a_200_with_an_error_not_a_crash(service, garbage):
    code, body = post(service, "/extract", garbage)
    assert code == 200 and body["text"] == "" and body["pages"] == 0 and body["error"]
    assert post(service, "/extract", b"%PDF-1.4 still alive?")[0] == 200  # and it keeps serving


def test_encrypted_pdf_is_reported_not_read(service):
    import io

    from reportlab.lib.pdfencrypt import StandardEncryption
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt=StandardEncryption("user-pass", canPrint=0), invariant=1)
    c.drawString(72, 700, "secret invoice text")
    c.save()
    code, body = post(service, "/extract", buf.getvalue())
    assert code == 200 and body["text"] == "" and "encrypted" in body["error"].lower()


def test_unknown_path_and_health(service):
    assert post(service, "/nope", b"x")[0] == 404
    with urllib.request.urlopen(service + "/healthz") as resp:
        assert resp.status == 200 and resp.read() == b"ok"


def test_oversize_body_is_refused_with_a_status_not_a_reset(service, monkeypatch):
    monkeypatch.setattr(pdf_app, "MAX_BYTES", 10_000)
    for _ in range(5):  # a connection reset would show up as an exception here
        assert post(service, "/extract", b"x" * 200_000)[0] == 413
