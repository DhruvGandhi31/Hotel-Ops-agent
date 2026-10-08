"""The mock is test infrastructure for the n8n pipeline, so it gets tests of its own."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest
from pypdf import PdfReader

from hotel_datagen.build import build_dataset
from hotel_datagen.output import write_dataset
from hotel_evals.extract import Extractor, ollama_chat_fn
from hotel_evals.mock_ollama import MODES, serve


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("mockdata")
    write_dataset(build_dataset(30, 3), out)
    return out


@pytest.fixture()
def mock(dataset):
    server = serve(dataset, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{server.server_port}"
    yield host
    server.shutdown()


def _post(host, path, body):
    req = urllib.request.Request(f"{host}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.read()


def _text(path: Path) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(path).pages)


def _first_invoices(dataset, n=5):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    return [f for f in manifest["files"] if f["expected_ingestion"] == "insert"][:n]


def test_oracle_mode_makes_every_invoice_extract_perfectly(dataset, mock):
    extractor = Extractor(ollama_chat_fn("m", mock, None, format_mode="json"))
    for entry in _first_invoices(dataset):
        result = extractor.extract(_text(dataset / entry["file"]))
        truth = json.loads((dataset / "ground_truth" / f"{entry['file_id']}.json").read_text(encoding="utf-8"))
        assert result.status == "extracted" and result.attempts == 1, entry["file_id"]
        assert result.output["invoice_number"] == truth["document"]["invoice_number"]


def test_garbage_once_succeeds_on_retry(dataset, mock):
    _post(mock, "/__mode", {"mode": "garbage_once"})
    entry = _first_invoices(dataset, 1)[0]
    result = Extractor(ollama_chat_fn("m", mock, None, format_mode="json")).extract(_text(dataset / entry["file"]))
    assert result.status == "extracted" and result.attempts == 2
    stats = json.loads(urllib.request.urlopen(f"{mock}/__stats").read())
    assert stats["requests"] == 2 and stats["retries"] == 1


def test_garbage_always_ends_in_needs_review(dataset, mock):
    _post(mock, "/__mode", {"mode": "garbage_always"})
    entry = _first_invoices(dataset, 1)[0]
    result = Extractor(ollama_chat_fn("m", mock, None, format_mode="json")).extract(_text(dataset / entry["file"]))
    assert result.status == "needs_review" and result.output is None and result.attempts == 2


def test_unknown_text_is_counted_not_guessed(mock):
    body = json.loads(
        _post(mock, "/api/chat", {"model": "m", "stream": False, "messages": [{"role": "user", "content": "hello"}]})
    )
    assert "Not an invoice" in body["message"]["content"]
    assert json.loads(urllib.request.urlopen(f"{mock}/__stats").read())["unmatched"] == 1


def test_streaming_reply_is_ndjson_ending_in_done(mock):
    raw = _post(mock, "/api/chat", {"model": "m", "messages": [{"role": "user", "content": "hello"}]}).decode()
    lines = [json.loads(line) for line in raw.strip().splitlines()]
    assert lines[-1]["done"] is True and lines[-1]["eval_count"] >= 0
    assert "".join(line["message"]["content"] for line in lines).startswith("{")


def test_bad_mode_is_rejected(mock):
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(mock, "/__mode", {"mode": "nonsense"})
    assert err.value.code == 400
    assert set(MODES) >= {
        "oracle",
        "garbage_once",
        "garbage_always",
        "server_error",
        "matcher_unsure",
        "matcher_none",
        "matcher_wrong",
        "matcher_server_error",
        "matcher_garbage_always",
    }


def test_server_error_mode_is_a_transport_failure_not_a_bad_answer(dataset, mock):
    from hotel_evals.extract import TransportError

    _post(mock, "/__mode", {"mode": "server_error"})
    entry = _first_invoices(dataset, 1)[0]
    extractor = Extractor(ollama_chat_fn("m", mock, None, backoff=(0,), format_mode="json"))
    with pytest.raises(TransportError):
        extractor.extract(_text(dataset / entry["file"]))
