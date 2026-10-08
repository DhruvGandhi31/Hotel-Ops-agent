"""The Python line-matching reference, and the mock model's answers to line-matching prompts."""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import write_dataset
from hotel_evals.match import (
    build_match_system_prompt,
    dollars,
    load_match_schema,
    match_errors,
    match_retry_message,
    match_user_message,
    number,
)
from hotel_evals.mock_ollama import serve

CTX = {
    "invoice_number": "INV-1",
    "pending_lines": [
        {
            "line_no": 1,
            "description": "Roma Toms",
            "quantity": 3,
            "unit": "box",
            "unit_price_cents": 3200,
            "line_total_cents": 9600,
        },
        {
            "line_no": 3,
            "description": "Evoo 4L",
            "quantity": 1,
            "unit": None,
            "unit_price_cents": 4590,
            "line_total_cents": 4590,
        },
    ],
    "candidates": [
        {
            "po_line_no": 2,
            "supplier_sku": "T1",
            "description": "Tomatoes Roma",
            "unit": "box",
            "quantity": 3,
            "unit_price_cents": 3200,
        },
        {
            "po_line_no": 4,
            "supplier_sku": None,
            "description": "Olive Oil 4L",
            "unit": "tin",
            "quantity": 1,
            "unit_price_cents": 4590,
        },
    ],
}


def answer(*pairs, conf=0.9):
    return {"matches": [{"invoice_line_no": a, "po_line_no": b, "confidence": conf} for a, b in pairs]}


def test_prompt_embeds_the_schema_structure_without_its_prose():
    prompt = build_match_system_prompt()
    assert "{{" not in prompt
    assert '"po_line_no"' in prompt and '"confidence"' in prompt and "same product" in prompt
    assert "The invoice line's number, exactly" not in prompt  # schema descriptions are stripped (the rules say it)
    schema = load_match_schema()
    assert schema["properties"]["matches"]["items"]["properties"]["confidence"]["maximum"] == 1


def test_user_message_lists_every_pending_line_and_every_candidate():
    text = match_user_message(CTX)
    assert text.startswith("Invoice INV-1")
    assert text.count("- line ") == 2 and text.count("- PO line ") == 2
    assert "unit no unit printed" in text and "SKU none" in text and "$45.90" in text


def test_number_and_dollar_formatting():
    assert [number(x) for x in (10, 10.0, 2.5, 0.001, 100.0, 1234.567)] == [
        "10",
        "10",
        "2.5",
        "0.001",
        "100",
        "1234.567",
    ]
    assert [dollars(c) for c in (0, 5, 100, 4590, 123456)] == ["0.00", "0.05", "1.00", "45.90", "1234.56"]


def test_retry_message_carries_the_request_the_answer_and_the_errors():
    text = match_retry_message("THE REQUEST", '{"bad": 1}', ["e1", "e2"])
    assert "THE REQUEST" in text and '{"bad": 1}' in text and "- e1\n- e2" in text and "{{" not in text


@pytest.mark.parametrize(
    ("output", "expected_fragment"),
    [
        (answer((1, 2), (3, 4)), None),
        (answer((1, None), (3, None)), None),
        (answer((1, 4), (3, 2)), None),  # a different assignment is still valid
        ({"matches": []}, ""),  # any error: jsonschema and the JS interpreter word this differently
        ({}, "required"),
        (answer((1, 2)), "line 3 must be answered exactly once"),
        (answer((1, 2), (3, 4), (1, 2)), "line 1 must be answered exactly once"),
        (answer((1, 2), (9, 4)), "was not asked about"),
        (answer((1, 2), (3, 7)), "not one of the lines offered"),
        (answer((1, 2), (3, 2)), "use it once"),
        (answer((1, 2), (3, 4), conf=1.5), "maximum"),
        (answer((1, 2), (3, 4), conf=-0.1), "minimum"),
        ({"matches": [{"invoice_line_no": 1, "po_line_no": 2}]}, "required"),
        ({"matches": [{"invoice_line_no": 0, "po_line_no": 2, "confidence": 0.9}]}, "minimum"),
    ],
)
def test_domain_checks(output, expected_fragment):
    errors = match_errors(output, CTX)
    if expected_fragment is None:
        assert errors == []
    else:
        assert any(expected_fragment in e for e in errors), errors


# ---- the mock model's answers ------------------------------------------------------------------


@pytest.fixture(scope="module")
def dataset(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("matchdata")
    write_dataset(build_dataset(120, 5), out)
    return out


@pytest.fixture()
def mock(dataset):
    server = serve(dataset, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _request_for_an_aliased_invoice(dataset: Path):
    """A matching request built the way the workflow builds it, for one invoice with reworded lines."""
    master = json.loads((dataset / "master_data.json").read_text(encoding="utf-8"))
    pos = {p["po_number"]: p for p in master["purchase_orders"]}
    for f in sorted((dataset / "ground_truth").glob("*.json")):
        gt = json.loads(f.read_text(encoding="utf-8"))
        aliased = [ln for ln in gt["labels"]["lines"] if "description_mismatch" in ln["issues"]]
        if not aliased or gt["labels"]["exact_resend_of"]:
            continue
        po = pos[gt["labels"]["true_po_number"]]
        asked = {ln["line_no"] for ln in aliased}
        pending = [
            {
                "line_no": ln["line_no"],
                "description": ln["description"],
                "quantity": ln["quantity"],
                "unit": ln["unit"],
                "unit_price_cents": ln["unit_price_cents"],
                "line_total_cents": ln["line_total_cents"],
            }
            for ln in gt["document"]["lines"]
            if ln["line_no"] in asked
        ]
        taken = {ln["po_line_no"] for ln in gt["labels"]["lines"] if ln["line_no"] not in asked}
        candidates = [
            {
                "po_line_no": p["line_no"],
                "supplier_sku": p["supplier_sku"],
                "description": p["description"],
                "unit": p["unit"],
                "quantity": p["quantity"],
                "unit_price_cents": p["unit_price_cents"],
            }
            for p in po["lines"]
            if p["line_no"] not in taken
        ]
        truth = {ln["line_no"]: ln["po_line_no"] for ln in gt["labels"]["lines"] if ln["line_no"] in asked}
        return {
            "invoice_number": gt["document"]["invoice_number"],
            "pending_lines": pending,
            "candidates": candidates,
        }, truth
    raise AssertionError("no invoice with reworded lines in the dataset")


def _ask(host: str, ctx: dict, retry: bool = False) -> dict:
    user = match_user_message(ctx)
    if retry:
        user = match_retry_message(user, "{}", ["x"])
    body = {
        "model": "m",
        "stream": False,
        "format": "json",
        "messages": [{"role": "system", "content": build_match_system_prompt()}, {"role": "user", "content": user}],
    }
    req = urllib.request.Request(f"{host}/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    content = json.load(urllib.request.urlopen(req))["message"]["content"]
    return json.loads(content)


def _set_mode(host: str, mode: str):
    req = urllib.request.Request(
        f"{host}/__mode", json.dumps({"mode": mode}).encode(), {"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req).read()


def test_mock_answers_with_the_true_po_lines_and_the_answer_validates(dataset, mock):
    ctx, truth = _request_for_an_aliased_invoice(dataset)
    got = _ask(mock, ctx)
    assert {m["invoice_line_no"]: m["po_line_no"] for m in got["matches"]} == truth
    assert all(m["confidence"] >= 0.9 for m in got["matches"])
    assert match_errors(got, ctx) == []


@pytest.mark.parametrize(
    ("mode", "check"),
    [
        ("matcher_unsure", lambda ms, truth: all(m["confidence"] < 0.85 for m in ms)),
        ("matcher_none", lambda ms, truth: all(m["po_line_no"] is None for m in ms)),
        ("matcher_wrong", lambda ms, truth: all(m["po_line_no"] != truth[m["invoice_line_no"]] for m in ms)),
    ],
)
def test_mock_failure_modes(dataset, mock, mode, check):
    ctx, truth = _request_for_an_aliased_invoice(dataset)
    _set_mode(mock, mode)
    assert check(_ask(mock, ctx)["matches"], truth)


def test_mock_garbage_applies_to_matching_too_and_retry_is_counted(dataset, mock):
    ctx, truth = _request_for_an_aliased_invoice(dataset)
    _set_mode(mock, "garbage_once")
    with pytest.raises(json.JSONDecodeError):
        _ask(mock, ctx)
    assert {m["invoice_line_no"] for m in _ask(mock, ctx, retry=True)["matches"]} == set(truth)
    stats = json.loads(urllib.request.urlopen(f"{mock}/__stats").read())
    assert stats["retries"] == 1 and stats["matcher_requests"] == 1
