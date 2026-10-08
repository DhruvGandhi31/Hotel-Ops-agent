import json
from pathlib import Path

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import ground_truth
from hotel_evals.ops_db import dataset_fingerprint, dollar_quote
from hotel_evals.reconcile_report import render
from hotel_evals.reconcile_scoring import (
    FLAG_CODES,
    case_from_ground_truth,
    got_from_result,
    score_reconciliation,
)


def case(
    file_id="inv_1",
    codes=(),
    status=None,
    got_codes=(),
    got_status=None,
    review=(),
    conditions=(),
    po_lines=None,
    got_lines=None,
):
    truth_status = status or ("flag" if codes else "recommend_approve")
    return {
        "file_id": file_id,
        "truth": {
            "codes": set(codes),
            "status": truth_status,
            "conditions": list(conditions),
            "po_lines": po_lines or {},
        },
        "got": {
            "status": got_status or ("flag" if got_codes else "recommend_approve"),
            "codes": set(got_codes),
            "review": set(review),
            "lines": got_lines or {},
        },
    }


def test_per_code_counts_and_ratios():
    cases = [
        case("a", codes={"price_variance"}, got_codes={"price_variance"}),  # TP
        case("b", codes={"price_variance"}, got_codes=set()),  # FN
        case("c", codes=set(), got_codes={"price_variance"}),  # FP
        case("d", codes=set(), got_codes=set()),  # TN
    ]
    m = score_reconciliation(cases)["per_code"]["price_variance"]
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_undefined_ratios_are_none_not_zero_or_one():
    m = score_reconciliation([case("a")])["per_code"]["unknown_po"]
    assert m["precision"] is None and m["recall"] is None  # nothing predicted, nothing expected


def test_needs_review_is_a_miss_for_recall_but_counts_as_escalated():
    cases = [
        case("a", codes={"short_delivery"}, got_codes={"short_delivery"}),
        case("b", codes={"short_delivery"}, got_codes=set(), got_status="needs_review", review={"unmatched_line"}),
    ]
    s = score_reconciliation(cases)
    assert s["per_code"]["short_delivery"]["recall"] == 0.5  # b is a miss
    assert s["flag_class"]["recall"] == 0.5
    assert s["flag_class"]["caught_or_escalated"] == 1.0  # but it was not silently approved
    assert s["flag_class"]["wrongly_approved"] == 0
    assert s["needs_review_count"] == 1 and s["review_reasons"] == {"unmatched_line": 1}


def test_a_flagged_invoice_that_was_approved_is_counted():
    s = score_reconciliation([case("a", codes={"gst_miscalculated"}, got_codes=set())])
    assert s["flag_class"]["wrongly_approved"] == 1 and s["flag_class"]["caught_or_escalated"] == 0.0


def test_flag_class_precision_counts_clean_invoices_that_were_flagged():
    cases = [
        case("a", codes={"duplicate_invoice"}, got_codes={"duplicate_invoice"}),
        case("b", got_codes={"price_variance"}),
    ]
    fc = score_reconciliation(cases)["flag_class"]
    assert fc["precision"] == 0.5 and fc["recall"] == 1.0 and fc["approved_correctly"] == 0.0


def test_status_confusion_matrix():
    cases = [
        case("a", codes={"price_variance"}, got_codes={"price_variance"}),
        case("b", got_status="needs_review", review={"supplier_unknown"}),
        case("c"),
    ]
    assert score_reconciliation(cases)["confusion"] == {
        "flag": {"flag": 1},
        "recommend_approve": {"needs_review": 1, "recommend_approve": 1},
    }


def test_conditions_only_count_invoices_with_no_true_discrepancy():
    cases = [
        case("a", conditions=["description_mismatch"], got_codes={"price_variance"}),  # wrongly flagged
        case("b", conditions=["description_mismatch"]),  # correctly approved
        case("c", conditions=["description_mismatch"], got_status="needs_review", review={"unmatched_line"}),
        case(
            "d", codes={"price_variance"}, conditions=["description_mismatch"], got_codes={"price_variance"}
        ),  # carries a real code: excluded
    ]
    c = score_reconciliation(cases)["condition_false_positives"]["description_mismatch"]
    assert c == {"invoices": 3, "flagged": 1, "needs_review": 1, "approved": 1}


def test_exact_code_set_and_multi_code_rates():
    cases = [
        case("a", codes={"price_variance", "short_delivery"}, got_codes={"price_variance", "short_delivery"}),
        case("b", codes={"price_variance", "short_delivery"}, got_codes={"price_variance"}),
        case("c"),
    ]
    s = score_reconciliation(cases)
    assert s["exact_code_match"] == {"correct": 2, "of": 3} and s["multi_code"] == {"correct": 1, "of": 2}
    assert [m["file_id"] for m in s["mismatches"]] == ["b"]


def test_matcher_accuracy_by_method_and_unmatched_lines():
    cases = [
        case(
            "a",
            po_lines={1: 1, 2: 2, 3: 3, 4: None},
            got_lines={1: (1, "sku"), 2: (2, "llm"), 3: (None, "none"), 4: (None, "none")},
        ),
        case("b", po_lines={1: 5}, got_lines={1: (7, "llm")}),
    ]
    mt = score_reconciliation(cases)["matcher"]
    assert mt["lines"] == 4 and mt["correct"] == 2 and mt["accuracy"] == 0.5  # the no-PO line is not scored
    assert mt["by_method"]["llm"] == {"lines": 2, "correct": 1, "wrong": 1}
    assert mt["by_method"]["none"]["lines"] == 1 and mt["by_method"]["none"]["correct"] == 0


def test_case_from_ground_truth_no_po_lines_cannot_be_matched():
    ds = build_dataset(200, 42)
    by_case = {c.case_id: c for c in ds.cases}
    suppliers = {s.key: s for s in ds.suppliers}
    seen = set()
    for c in ds.cases:
        gt = json.loads(json.dumps(ground_truth(c, suppliers[c.doc.supplier_key], by_case, "x")))
        if gt["labels"]["exact_resend_of"]:
            continue
        built = case_from_ground_truth("f", gt)
        codes = built["truth"]["codes"]
        if {"unknown_po", "missing_po"} & codes:
            seen.add("no-po")
            assert all(v is None for v in built["truth"]["po_lines"].values()), "nothing to match against"
        else:
            seen.add("po")
            assert all(isinstance(v, int) for v in built["truth"]["po_lines"].values())
        assert set(built["truth"]["codes"]) <= set(FLAG_CODES)
    assert seen == {"no-po", "po"}


def test_got_from_result_reads_what_reconcile_invoice_returns():
    result = {
        "status": "flag",
        "reason_codes": ["price_variance"],
        "review_reasons": ["unmatched_line"],
        "lines": [
            {"line_no": 1, "po_line_no": 3, "match_method": "sku"},
            {"line_no": 2, "po_line_no": None, "match_method": "none"},
        ],
    }
    assert got_from_result(result) == {
        "status": "flag",
        "codes": {"price_variance"},
        "review": {"unmatched_line"},
        "lines": {1: (3, "sku"), 2: (None, "none")},
    }


def test_report_renders_every_section_and_states_the_caveat():
    score = score_reconciliation([case("a", codes={"price_variance"}, got_codes={"price_variance"}), case("b")])
    md = render({"mode": "rules", "dataset": "data-gen/out", "seed": 42}, score, {"price_tolerance_pct": 2.0})
    for heading in (
        "## The `flag` class",
        "## Precision and recall per discrepancy type",
        "## Conditions that must not flag",
        "## Line matching",
        "## Mismatches",
    ):
        assert heading in md
    assert "share rules" in md and "price_tolerance_pct = 2.0" in md and "MOCK" not in md
    assert "MOCK MODEL" in render({"mode": "rules", "dataset": "d", "seed": 1, "mock": True}, score, {})


def test_dollar_quote_never_collides_with_its_content():
    assert dollar_quote("abc") == "$j$abc$j$"
    assert dollar_quote("has $j$ inside") == "$jx$has $j$ inside$jx$"


def test_dataset_fingerprint_is_read_from_seed_sql(tmp_path: Path):
    f = tmp_path / "seed.sql"
    f.write_text(
        "INSERT INTO seed_metadata (key, value) VALUES ('dataset_fingerprint', '"
        + "ab" * 32
        + "') ON CONFLICT DO NOTHING;"
    )
    assert dataset_fingerprint(f) == "ab" * 32
    f.write_text("nothing here")
    with pytest.raises(ValueError):
        dataset_fingerprint(f)
