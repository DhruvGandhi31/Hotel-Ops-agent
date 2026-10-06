import copy
import json

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import ground_truth
from hotel_evals.oracle import truth_to_extraction
from hotel_evals.report import wilson
from hotel_evals.scoring import aggregate, score_extraction, to_cents


@pytest.fixture(scope="module")
def documents() -> list[dict]:
    ds = build_dataset(200, 42)
    by_case = {c.case_id: c for c in ds.cases}
    suppliers = {s.key: s for s in ds.suppliers}
    return [
        json.loads(json.dumps(ground_truth(c, suppliers[c.doc.supplier_key], by_case, "x")))["document"]
        for c in ds.cases
        if c.exact_resend_of is None
    ]


@pytest.fixture(scope="module")
def doc(documents) -> dict:
    return next(d for d in documents if len(d["lines"]) >= 3 and d["po_number"])


def _score(doc, mutate=None):
    predicted = truth_to_extraction(doc)
    if mutate:
        mutate(predicted)
    return score_extraction(predicted, doc)


def _wrong(score) -> set[str]:
    return {f for f, ok in score["header"].items() if not ok}


def test_perfect_extractor_scores_100_percent_everywhere(documents):
    cases = []
    for d in documents:
        s = score_extraction(truth_to_extraction(d), d)
        assert not s["failures"], (d["invoice_number"], s["failures"][:2])
        cases.append({"template": "A", "score": s})
    agg = aggregate(cases)["overall"]
    assert agg["line_recall"] == agg["line_precision"] == agg["invoices_fully_correct"] == 1.0
    assert all(v == 1.0 for v in agg["header"].values())
    assert all(v == 1.0 for v in agg["line_fields"].values())


def test_failed_extraction_scores_everything_wrong(doc):
    s = score_extraction(None, doc)
    assert not any(s["header"].values())
    assert s["lines"]["correct"] == 0 and s["lines"]["predicted"] == 0


def test_wrong_total_only_affects_total(doc):
    assert _wrong(_score(doc, lambda p: p.update(total="1.00"))) == {"total"}


def test_total_formatting_is_not_forgiven_by_the_scorer(doc):
    """The validator rejects these before scoring; if one slipped through it must not count as correct."""
    assert _wrong(_score(doc, lambda p: p.update(total="$" + p["total"]))) == {"total"}
    assert _wrong(_score(doc, lambda p: p.update(total=float(p["total"])))) == {"total"}


def test_po_null_vs_value_both_directions(documents):
    with_po = next(d for d in documents if d["po_number"])
    without = next(d for d in documents if d["po_number"] is None)
    assert _wrong(_score(with_po, lambda p: p.update(po_number=None))) == {"po_number"}
    assert _wrong(_score(without, lambda p: p.update(po_number="PO-004501"))) == {"po_number"}
    assert not _wrong(_score(without))


def test_po_number_style_matters(documents):
    compact = next(d for d in documents if d["po_number"] and "-" not in d["po_number"])
    hyphenated = compact["po_number"][:2] + "-" + compact["po_number"][2:]
    assert _wrong(_score(compact, lambda p: p.update(po_number=hyphenated))) == {"po_number"}


def test_abn_spacing_and_name_case_are_forgiven(doc):
    abn = doc["supplier_abn"]
    spaced = f"{abn[:2]} {abn[2:5]} {abn[5:8]} {abn[8:]}"
    s = _score(doc, lambda p: p.update(supplier_abn=spaced, supplier_name=doc["supplier_name"].upper()))
    assert not _wrong(s)


def test_invoice_number_is_exact(doc):
    assert _wrong(_score(doc, lambda p: p.update(invoice_number=p["invoice_number"] + "0"))) == {"invoice_number"}
    assert _wrong(_score(doc, lambda p: p.update(invoice_number=p["invoice_number"][:-1]))) == {"invoice_number"}


def test_line_order_does_not_matter(doc):
    s = _score(doc, lambda p: p["lines"].reverse())
    assert s["lines"]["correct"] == s["lines"]["truth"] == len(doc["lines"])


def test_missing_line_lowers_recall_not_precision(doc):
    s = _score(doc, lambda p: p["lines"].pop(1))
    assert s["lines"]["correct"] == len(doc["lines"]) - 1
    assert s["lines"]["predicted"] == len(doc["lines"]) - 1
    assert len(s["failures"]) == 1 and s["failures"][0][0] == "line 2"


def test_extra_line_lowers_precision_not_recall(doc):
    def add_phantom(p):
        p["lines"].append(copy.deepcopy(p["lines"][0]) | {"description": "Phantom item"})

    s = _score(doc, add_phantom)
    assert s["lines"]["correct"] == s["lines"]["truth"]
    assert s["lines"]["predicted"] == s["lines"]["truth"] + 1


@pytest.mark.parametrize(
    ("field", "bad"),
    [("description", "Something else"), ("quantity", 999), ("unit_price", "0.01"), ("line_total", "0.01")],
)
def test_a_line_is_only_correct_if_all_four_key_fields_match(doc, field, bad):
    s = _score(doc, lambda p: p["lines"][0].__setitem__(field, bad))
    assert s["lines"]["correct"] == len(doc["lines"]) - 1


def test_non_key_line_fields_do_not_break_a_line(doc):
    def tweak(p):
        first = p["lines"][0]
        first.update(unit="zzz", supplier_code="ZZZ", gst_applicable=not first["gst_applicable"])

    s = _score(doc, tweak)
    assert s["lines"]["correct"] == len(doc["lines"])
    assert s["lines"]["fields"]["unit"] == len(doc["lines"]) - 1
    assert s["lines"]["fields"]["gst_applicable"] == len(doc["lines"]) - 1


def test_quantity_compares_numerically_not_textually(documents):
    d = next(d for d in documents if any(isinstance(ln["quantity"], float) for ln in d["lines"]))
    i = next(i for i, ln in enumerate(d["lines"]) if isinstance(ln["quantity"], float))
    s = _score(d, lambda p: p["lines"][i].update(quantity=str(p["lines"][i]["quantity"]) + "0"))
    assert s["lines"]["correct"] == len(d["lines"])


def test_to_cents():
    assert to_cents("1430.74") == 143074
    assert to_cents("0.05") == 5
    for bad in ("1,430.74", "$5.00", "5", "5.0", "5.000", 5.0, None, ""):
        assert to_cents(bad) is None, bad


def test_wilson_interval_brackets_the_estimate():
    lo, hi = wilson(192, 196)
    assert lo < 192 / 196 < hi
    assert wilson(196, 196)[1] == 1.0 and wilson(0, 0) == (0.0, 0.0)
    assert 0.94 < lo < 0.96  # 98% observed on n=196 is only weak evidence of >= 98%
