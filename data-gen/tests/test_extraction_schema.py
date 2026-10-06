"""schemas/invoice_extraction.json against the generated ground truth.

The ground truth `document` is exactly what a perfect extractor would return, so converting it
to the schema's wire format (dollar strings instead of cents) must always validate. The
negative cases pin down what the validator has to reject before output reaches the database.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hotel_evals.oracle import truth_to_extraction

from .test_output import _text

SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "invoice_extraction.json"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def good(ground_truths) -> dict:
    return truth_to_extraction(next(g for g in ground_truths if len(g["document"]["lines"]) >= 2)["document"])


def test_every_ground_truth_document_validates(validator, ground_truths):
    bad = [
        (gt["file"], e.message)
        for gt in ground_truths
        for e in validator.iter_errors(truth_to_extraction(gt["document"]))
    ]
    assert not bad, bad[:5]


def test_schema_covers_every_template_variation(ground_truths):
    """Guard against a schema that only works because the sample never exercises the hard cases."""
    docs = [g["document"] for g in ground_truths]
    assert any(d["po_number"] is None for d in docs)
    assert any(d["po_number"] and not d["po_number"].startswith("PO-") for d in docs)  # PO004512 style
    assert any(ln["supplier_code"] is None for d in docs for ln in d["lines"])
    assert any(ln["unit"] is None for d in docs for ln in d["lines"])
    assert any(ln["gst_cents"] is not None for d in docs for ln in d["lines"])
    assert any(isinstance(ln["quantity"], float) for d in docs for ln in d["lines"])


def _mutations(good: dict):
    def drop(key):
        def f(d):
            del d[key]

        return f

    def set_(key, value):
        def f(d):
            d[key] = value

        return f

    def line(key, value):
        def f(d):
            d["lines"][0][key] = value

        return f

    yield "missing total", drop("total")
    yield "missing po_number key (must be explicit null)", drop("po_number")
    yield "extra top-level field", set_("notes", "looks fine")
    yield "extra line field", line("confidence", 0.9)
    yield "money as float", set_("total", 1430.74)
    yield "money with currency symbol", set_("total", "$1,430.74")
    yield "money with one decimal", set_("gst", "130.7")
    yield "money negative", set_("subtotal", "-5.00")
    yield "date in AU print format", set_("invoice_date", "20/07/2026")
    yield "date with month name", set_("due_date", "3 Oct 2026")
    yield "currency not AUD", set_("currency", "USD")
    yield "abn too short", set_("supplier_abn", "5182475355")
    yield "abn with letters", set_("supplier_abn", "51 824 753 55X")
    yield "empty invoice number", set_("invoice_number", "")
    yield "no lines", set_("lines", [])
    yield "zero quantity", line("quantity", 0)
    yield "quantity as string", line("quantity", "3")
    yield "gst_applicable as string", line("gst_applicable", "GST")
    yield "gst_applicable null", line("gst_applicable", None)
    yield "unit empty string", line("unit", "")
    yield "unit price as float", line("unit_price", 2.4)


def test_validator_rejects_malformed_output(validator, good):
    assert not list(validator.iter_errors(good))
    accepted = []
    for name, mutate in _mutations(good):
        doc = copy.deepcopy(good)
        mutate(doc)
        if not list(validator.iter_errors(doc)):
            accepted.append(name)
    assert not accepted, accepted


def test_schema_accepts_spaced_and_unspaced_abn(validator, good):
    for abn in ("96845146270", "96 845 146 270"):
        doc = {**good, "supplier_abn": abn}
        assert not list(validator.iter_errors(doc)), abn


def test_unit_labels_are_what_is_printed(gate_out, ground_truths):
    """A unit in the truth must be on the page; a null unit is only allowed where none is printed."""
    for gt in ground_truths:
        text = _text(gate_out / gt["file"])
        for ln in gt["document"]["lines"]:
            if ln["unit"] is None:
                assert gt["template"] == "C", (gt["file"], ln)
            else:
                assert re.search(rf"(?<![A-Za-z]){re.escape(ln['unit'])}(?![A-Za-z])", text, re.I), (gt["file"], ln)


def test_template_d_gst_flag_is_readable_from_the_line_gst_column(ground_truths):
    """On per-line-GST invoices the only on-page signal is the amount: > 0.00 means taxable."""
    checked = 0
    for gt in ground_truths:
        for ln in gt["document"]["lines"]:
            if ln["gst_cents"] is not None:
                checked += 1
                assert ln["gst_applicable"] == (ln["gst_cents"] > 0), (gt["file"], ln)
    assert checked > 100
