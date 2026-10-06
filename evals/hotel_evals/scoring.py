"""Field-level scoring of extractions against ground truth.

`predicted` is the schema-shaped extraction (dollar strings); `truth` is a ground-truth
`document` (cents). Comparison happens in cents, so formatting differences ("1,430.74" vs
"1430.74") are the validator's problem, not the scorer's.

Matching rules, deliberately strict:
  - invoice number, PO number, ABN: exact match (ABN ignoring spaces); PO null must be null
  - money: exact cents
  - description: exact after collapsing whitespace and ignoring case
  - a line item counts as correct only if description, quantity, unit price and line total
    all match (the P2 gate definition); lines are matched as a bag, so one missed line does
    not shift every line after it
"""

import re
from collections import Counter
from decimal import Decimal, InvalidOperation

HEADER_FIELDS = (
    "supplier_name",
    "supplier_abn",
    "bill_to_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "subtotal",
    "gst",
    "total",
)
# The three fields the gate sets a 98% target on.
GATE_HEADER_FIELDS = ("total", "invoice_number", "po_number")
LINE_FIELDS = (
    "supplier_code",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "gst_applicable",
    "line_total",
    "line_gst",
)
# What makes a line item "correct" for the gate.
LINE_KEY_FIELDS = ("description", "quantity", "unit_price", "line_total")

_MONEY = re.compile(r"^-?[0-9]+\.[0-9]{2}$")


def to_cents(value) -> int | None:
    """Dollar string to integer cents; None if it isn't a plain two-decimal amount."""
    if not isinstance(value, str) or not _MONEY.match(value):
        return None
    return int(Decimal(value) * 100)


def _squash(value) -> str | None:
    return None if value is None else re.sub(r"\s+", " ", str(value)).strip()


def _num(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _digits(value) -> str | None:
    return None if value is None else re.sub(r"\s+", "", str(value))


def header_matches(field: str, predicted: dict, truth: dict) -> bool:
    got = predicted.get(field)
    if field in ("subtotal", "gst", "total"):
        return to_cents(got) == truth[f"{field}_cents"]
    if field == "supplier_abn":
        return _digits(got) == _digits(truth[field])
    if field in ("invoice_number", "po_number"):
        return _squash(got) == _squash(truth[field])
    if field == "supplier_name" or field == "bill_to_name":
        a, b = _squash(got), _squash(truth[field])
        return a is not None and a.casefold() == b.casefold()
    return got == truth[field]


def line_field_matches(field: str, predicted: dict, truth: dict) -> bool:
    got = predicted.get(field)
    if field == "quantity":
        a = _num(got)
        return a is not None and a == _num(truth["quantity"])
    if field in ("unit_price", "line_total", "line_gst"):
        return (
            to_cents(got)
            == truth[
                {"unit_price": "unit_price_cents", "line_total": "line_total_cents", "line_gst": "gst_cents"}[field]
            ]
        )
    if field == "gst_applicable":
        return got is truth["gst_applicable"]
    if field == "description":
        a, b = _squash(got), _squash(truth["description"])
        return a is not None and a.casefold() == b.casefold()
    return _squash(got) == _squash(truth[field])


def _line_key_ok(predicted: dict, truth: dict) -> bool:
    return all(line_field_matches(f, predicted, truth) for f in LINE_KEY_FIELDS)


def match_lines(predicted_lines: list[dict], truth_lines: list[dict]) -> list[tuple[int, int | None]]:
    """Pair each truth line with an unused predicted line that is correct on the key fields.

    Returns (truth_index, predicted_index or None). Prefers the predicted line at the same
    position, then any other unused one; greedy is fine because correct keys are near-unique.
    """
    used: set[int] = set()
    pairs: list[tuple[int, int | None]] = []
    for i, t in enumerate(truth_lines):
        order = [i, *[j for j in range(len(predicted_lines)) if j != i]]
        hit = next(
            (j for j in order if j < len(predicted_lines) and j not in used and _line_key_ok(predicted_lines[j], t)),
            None,
        )
        if hit is not None:
            used.add(hit)
        pairs.append((i, hit))
    return pairs


def score_extraction(predicted: dict | None, truth: dict) -> dict:
    """Score one invoice. `predicted=None` (extraction failed) scores every field wrong."""
    result = {"header": {}, "lines": {}, "failures": []}
    p = predicted or {}
    for field in HEADER_FIELDS:
        ok = predicted is not None and header_matches(field, p, truth)
        result["header"][field] = ok
        if not ok:
            result["failures"].append((field, _expected_header(field, truth), p.get(field)))

    p_lines = p.get("lines") if isinstance(p.get("lines"), list) else []
    t_lines = truth["lines"]
    pairs = match_lines(p_lines, t_lines)
    correct = sum(1 for _, j in pairs if j is not None)
    result["lines"] = {
        "truth": len(t_lines),
        "predicted": len(p_lines),
        "correct": correct,
        # Per-field accuracy needs a pairing; positional only when the counts agree.
        "fields": {
            f: sum(line_field_matches(f, pl, tl) for pl, tl in zip(p_lines, t_lines, strict=False))
            if len(p_lines) == len(t_lines)
            else 0
            for f in LINE_FIELDS
        },
    }
    for i, j in pairs:
        if j is None:
            best = p_lines[i] if i < len(p_lines) else None
            result["failures"].append((f"line {i + 1}", _line_brief(t_lines[i]), best))
    return result


def _expected_header(field: str, truth: dict):
    return truth[f"{field}_cents"] / 100 if field in ("subtotal", "gst", "total") else truth[field]


def _line_brief(line: dict) -> dict:
    return {k: line[k] for k in ("description", "quantity", "unit_price_cents", "line_total_cents")}


def aggregate(cases: list[dict]) -> dict:
    """cases: [{'template': 'A', 'score': score_extraction(...)}, ...] -> accuracy tables."""

    def rate(num: int, den: int) -> float:
        return num / den if den else 0.0

    def block(subset: list[dict]) -> dict:
        header = {f: rate(sum(c["score"]["header"][f] for c in subset), len(subset)) for f in HEADER_FIELDS}
        truth_lines = sum(c["score"]["lines"]["truth"] for c in subset)
        pred_lines = sum(c["score"]["lines"]["predicted"] for c in subset)
        correct = sum(c["score"]["lines"]["correct"] for c in subset)
        return {
            "n": len(subset),
            "header": header,
            "line_recall": rate(correct, truth_lines),
            "line_precision": rate(correct, pred_lines),
            "line_counts": (correct, truth_lines, pred_lines),
            "line_fields": {
                f: rate(sum(c["score"]["lines"]["fields"][f] for c in subset), truth_lines) for f in LINE_FIELDS
            },
            "invoices_fully_correct": rate(
                sum(
                    all(c["score"]["header"].values())
                    and c["score"]["lines"]["correct"] == c["score"]["lines"]["truth"]
                    and c["score"]["lines"]["predicted"] == c["score"]["lines"]["truth"]
                    for c in subset
                ),
                len(subset),
            ),
        }

    by_template = {t: block([c for c in cases if c["template"] == t]) for t in sorted({c["template"] for c in cases})}
    return {
        "overall": block(cases),
        "by_template": by_template,
        "error_fields": Counter(
            f.split(" ")[0] if f.startswith("line") else f for c in cases for f, _, _ in c["score"]["failures"]
        ),
    }
