"""Scoring reconciliation results against ground truth.

A *case* is one invoice file:

    {"file_id": "inv_0007",
     "truth": {"codes": {...}, "status": "flag", "conditions": [...], "po_lines": {1: 3, 2: None}},
     "got":   {"status": "...", "codes": {...}, "review": {...}, "lines": {1: (3, "sku"), ...}}}

`truth.po_lines` maps invoice line number to the true PO line number (None when the invoice has no
PO to match against). `got.lines` maps invoice line number to (PO line number or None, method).

Definitions, chosen to be strict and explicit:
  - A reason code is *predicted* when it is in the result's `reason_codes`. `needs_review` is not a
    prediction of anything: it counts as a miss for recall, and is reported separately as an
    abstention so that "escalated to a human" is visible rather than hidden in either direction.
  - The `flag` class is the status `flag`.
  - Precision is undefined (None) when nothing was predicted, recall when nothing was expected.
"""

from collections import Counter

FLAG_CODES = ("price_variance", "short_delivery", "duplicate_invoice", "unknown_po", "missing_po", "gst_miscalculated")
CONDITIONS = ("description_mismatch", "partial_delivery", "price_within_tolerance")


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _prf(tp: int, fp: int, fn: int) -> dict:
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn)}


def score_reconciliation(cases: list[dict]) -> dict:
    per_code = {}
    for code in FLAG_CODES:
        tp = sum(code in c["truth"]["codes"] and code in c["got"]["codes"] for c in cases)
        fp = sum(code not in c["truth"]["codes"] and code in c["got"]["codes"] for c in cases)
        fn = sum(code in c["truth"]["codes"] and code not in c["got"]["codes"] for c in cases)
        per_code[code] = _prf(tp, fp, fn)

    def status_is(case, status):
        return case["got"]["status"] == status

    exp_flag = [c for c in cases if c["truth"]["status"] == "flag"]
    exp_ok = [c for c in cases if c["truth"]["status"] == "recommend_approve"]
    flag_class = {
        **_prf(
            sum(status_is(c, "flag") for c in exp_flag),
            sum(status_is(c, "flag") for c in exp_ok),
            sum(not status_is(c, "flag") for c in exp_flag),
        ),
        # a truly flagged invoice that went to a human instead is not silently approved
        "caught_or_escalated": _ratio(sum(not status_is(c, "recommend_approve") for c in exp_flag), len(exp_flag)),
        "wrongly_approved": sum(status_is(c, "recommend_approve") for c in exp_flag),
        "approved_correctly": _ratio(sum(status_is(c, "recommend_approve") for c in exp_ok), len(exp_ok)),
    }

    confusion = {
        expected: dict(Counter(c["got"]["status"] for c in cases if c["truth"]["status"] == expected))
        for expected in ("flag", "recommend_approve")
    }

    # Conditions that must NOT flag on their own: how often do they?
    condition_fp = {}
    for cond in CONDITIONS:
        carriers = [c for c in cases if cond in c["truth"]["conditions"] and not c["truth"]["codes"]]
        condition_fp[cond] = {
            "invoices": len(carriers),
            "flagged": sum(status_is(c, "flag") for c in carriers),
            "needs_review": sum(status_is(c, "needs_review") for c in carriers),
            "approved": sum(status_is(c, "recommend_approve") for c in carriers),
        }

    exact_codes = sum(c["truth"]["codes"] == c["got"]["codes"] for c in cases)
    multi = [c for c in cases if len(c["truth"]["codes"]) >= 2]

    # Line matching, over lines that have a true PO line
    matcher: dict[str, Counter] = {}
    for c in cases:
        for line_no, true_po in c["truth"]["po_lines"].items():
            if true_po is None:
                continue
            got_po, method = c["got"]["lines"].get(line_no, (None, "none"))
            bucket = matcher.setdefault(method, Counter())
            bucket["lines"] += 1
            bucket["correct"] += got_po == true_po
            bucket["wrong"] += got_po is not None and got_po != true_po
    total_lines = sum(b["lines"] for b in matcher.values())
    correct_lines = sum(b["correct"] for b in matcher.values())

    reviews = Counter(r for c in cases for r in c["got"]["review"])
    return {
        "n": len(cases),
        "per_code": per_code,
        "flag_class": flag_class,
        "confusion": confusion,
        "condition_false_positives": condition_fp,
        "exact_code_match": {"correct": exact_codes, "of": len(cases)},
        "multi_code": {"correct": sum(c["truth"]["codes"] == c["got"]["codes"] for c in multi), "of": len(multi)},
        "matcher": {
            "by_method": {m: dict(b) for m, b in sorted(matcher.items())},
            "lines": total_lines,
            "correct": correct_lines,
            "accuracy": _ratio(correct_lines, total_lines),
        },
        "review_reasons": dict(reviews),
        "needs_review_count": sum(status_is(c, "needs_review") for c in cases),
        "mismatches": [
            {
                "file_id": c["file_id"],
                "expected": sorted(c["truth"]["codes"]),
                "got": sorted(c["got"]["codes"]),
                "status": c["got"]["status"],
                "review": sorted(c["got"]["review"]),
            }
            for c in cases
            if c["truth"]["codes"] != c["got"]["codes"] or c["got"]["status"] == "needs_review"
        ],
    }


def case_from_ground_truth(file_id: str, gt: dict) -> dict:
    """Build the `truth` half of a case from a ground-truth file."""
    labels = gt["labels"]
    has_po = labels["true_po_number"] is not None and not {"unknown_po", "missing_po"} & set(labels["reason_codes"])
    return {
        "file_id": file_id,
        "truth": {
            "codes": set(labels["reason_codes"]),
            "status": labels["expected_status"],
            "conditions": list(labels["conditions"]),
            "po_lines": {ln["line_no"]: (ln["po_line_no"] if has_po else None) for ln in labels["lines"]},
        },
    }


def got_from_result(result: dict) -> dict:
    """The `got` half of a case from reconcile_invoice's JSON result."""
    return {
        "status": result["status"],
        "codes": set(result["reason_codes"]),
        "review": set(result["review_reasons"]),
        "lines": {ln["line_no"]: (ln["po_line_no"], ln["match_method"]) for ln in result["lines"]},
    }
