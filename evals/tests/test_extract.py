"""The validate -> retry once -> needs_review contract (CLAUDE.md principle 2), with a fake model."""

import json

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import ground_truth
from hotel_evals.extract import (
    Extractor,
    abn_checksum_ok,
    build_system_prompt,
    load_schema,
    load_validator,
    strip_descriptions,
    validation_errors,
)
from hotel_evals.oracle import truth_to_extraction


@pytest.fixture(scope="module")
def good() -> dict:
    ds = build_dataset(50, 1)
    c = next(c for c in ds.cases if c.exact_resend_of is None)
    gt = ground_truth(c, next(s for s in ds.suppliers if s.key == c.doc.supplier_key), {}, "x")
    return truth_to_extraction(json.loads(json.dumps(gt))["document"])


class FakeModel:
    """Replies with canned strings in order; records every message list it was sent."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        return {"content": self.replies.pop(0), "prompt_tokens": 100, "completion_tokens": 50}


def _extract(*replies: str):
    model = FakeModel(*replies)
    return Extractor(model).extract("invoice text"), model


def test_valid_first_time_is_one_call(good):
    result, model = _extract(json.dumps(good))
    assert result.status == "extracted" and result.attempts == 1 and result.output == good
    assert len(model.calls) == 1 and result.prompt_tokens == 100


def test_invalid_then_valid_retries_with_the_errors_in_the_prompt(good):
    broken = {**good, "total": "$1,430.74"}
    result, model = _extract(json.dumps(broken), json.dumps(good))
    assert result.status == "extracted" and result.attempts == 2 and result.output == good
    retry = model.calls[1]
    assert [m["role"] for m in retry] == ["system", "user"]  # one prompt, same shape as the n8n chain
    body = retry[-1]["content"]
    assert "invoice text" in body and json.dumps(broken) in body
    assert "total" in body and "failed validation" in body
    assert result.prompt_tokens == 200 and result.completion_tokens == 100  # both attempts counted


def test_invalid_twice_goes_to_needs_review_with_no_output(good):
    broken = {**good, "total": 5}
    result, model = _extract(json.dumps(broken), json.dumps(broken))
    assert result.status == "needs_review" and result.output is None and result.attempts == 2
    assert result.errors and len(model.calls) == 2  # never a third attempt


def test_not_json_is_a_validation_failure_not_a_crash(good):
    result, _ = _extract("Sure! Here is the invoice: {", json.dumps(good))
    assert result.status == "extracted" and result.attempts == 2
    assert "not valid JSON" in result.attempt_errors[0][0]


def test_json_that_is_not_an_object_is_rejected():
    result, _ = _extract("[1, 2, 3]", "null")
    assert result.status == "needs_review"


def test_unvalidated_output_is_never_exposed(good):
    result, _ = _extract(json.dumps({**good, "extra": 1}), json.dumps({**good, "extra": 1}))
    assert result.output is None


def test_impossible_calendar_date_triggers_retry(good):
    validator = load_validator()
    assert validation_errors(good, validator) == []
    errors = validation_errors({**good, "invoice_date": "2026-02-31"}, validator)
    assert errors == ["invoice_date: '2026-02-31' is not a real calendar date"]


def test_abn_checksum_is_enforced(good):
    validator = load_validator()
    bad = {**good, "supplier_abn": "51824753557"}
    assert any("ABN checksum" in e for e in validation_errors(bad, validator))
    assert abn_checksum_ok("51 824 753 556") and not abn_checksum_ok("51 824 753 557")
    assert not abn_checksum_ok("5182475355")


def test_prompt_embeds_the_schema_and_nothing_is_left_unfilled():
    prompt = build_system_prompt()
    schema = load_schema()
    assert "{{" not in prompt and json.dumps(strip_descriptions(schema), separators=(",", ":")) in prompt
    assert "Name of the business issuing" not in prompt  # schema prose is not embedded twice
    for name in schema["properties"]:
        assert f'"{name}"' in prompt


def test_prompt_covers_the_traps_the_dataset_contains():
    """If a template trap is added to data-gen, the prompt needs a line about it."""
    prompt = build_system_prompt()
    for phrase in ("Delivery Docket", "Account No", "Your Order No.", "COPY", "day first", "per-line GST", "null"):
        assert phrase in prompt, phrase


def test_quantity_precision_and_money_width_match_what_the_database_stores(good):
    validator = load_validator()
    too_precise = json.loads(json.dumps(good))
    too_precise["lines"][0]["quantity"] = 1.2345
    assert any("3 decimal places" in e for e in validation_errors(too_precise, validator))
    ok = json.loads(json.dumps(good))
    ok["lines"][0]["quantity"] = 2.42
    assert validation_errors(ok, validator) == []
    assert list(validator.iter_errors({**good, "total": "1" * 13 + ".00"}))  # would overflow bigint cents
    assert not list(validator.iter_errors({**good, "total": "9" * 12 + ".99"}))
    huge = json.loads(json.dumps(good))
    huge["lines"][0]["quantity"] = 2_000_000
    assert list(validator.iter_errors(huge))


def test_stripped_schema_keeps_a_property_named_description():
    """Regression: the embedded schema once lost the line item's `description` field."""
    embedded = json.loads(build_system_prompt().split("## Schema")[1].strip())
    assert set(embedded["properties"]["lines"]["items"]["properties"]) == set(
        load_schema()["properties"]["lines"]["items"]["properties"]
    )
    assert "description" in embedded["properties"]["lines"]["items"]["required"]
    assert "Name of the business issuing" not in json.dumps(embedded)  # prose still gone
