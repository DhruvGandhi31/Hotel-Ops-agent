"""Line matching with an LLM: the Python reference for what the workflow's Code nodes do.

When an invoice line has no usable item code and no exact description match, the model chooses which
PO line it is. The workflow (`Reconcile Invoice`) builds the prompt, validates the reply and retries once
in JavaScript; this module is the same logic in Python so tests can hold the two to the same behaviour,
and so the mock model can read the prompt.

The context (`ctx`) is what `reconcile_candidates(invoice_id)` returns:
    {"invoice_number", "pending_lines": [...], "candidates": [...]}
"""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .extract import anchor_patterns, strip_descriptions

ROOT = Path(__file__).resolve().parents[2]
MATCH_SCHEMA_PATH = ROOT / "schemas" / "line_match.json"
MATCH_PROMPT_PATH = ROOT / "prompts" / "line_match.md"
MATCH_RETRY_PATH = ROOT / "prompts" / "line_match_retry.md"


def load_match_schema() -> dict:
    return json.loads(MATCH_SCHEMA_PATH.read_text(encoding="utf-8"))


def match_validator() -> Draft202012Validator:
    return Draft202012Validator(anchor_patterns(load_match_schema()))


def build_match_system_prompt() -> str:
    text = MATCH_PROMPT_PATH.read_text(encoding="utf-8")
    assert "{{SCHEMA}}" in text, "line_match.md lost its {{SCHEMA}} placeholder"
    return text.replace("{{SCHEMA}}", json.dumps(strip_descriptions(load_match_schema()), separators=(",", ":")))


def dollars(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def number(value) -> str:
    """A quantity as short text: 10.000 -> "10", 2.500 -> "2.5". Same as the workflow's formatting."""
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def match_user_message(ctx: dict) -> str:
    lines = [f"Invoice {ctx['invoice_number']}", "", "Invoice lines to match:"]
    for ln in ctx["pending_lines"]:
        unit = ln["unit"] or "no unit printed"
        lines.append(
            f'- line {ln["line_no"]}: "{ln["description"]}", quantity {number(ln["quantity"])}, unit {unit}, '
            f"unit price ${dollars(ln['unit_price_cents'])}, line total ${dollars(ln['line_total_cents'])}"
        )
    lines += ["", "Purchase order lines still unmatched (choose only among these):"]
    for c in ctx["candidates"]:
        lines.append(
            f'- PO line {c["po_line_no"]}: SKU {c["supplier_sku"] or "none"}, "{c["description"]}", '
            f"unit {c['unit']}, ordered {number(c['quantity'])}, price ${dollars(c['unit_price_cents'])}"
        )
    return "\n".join(lines)


def match_retry_message(context_text: str, previous_output: str, errors: list[str]) -> str:
    text = MATCH_RETRY_PATH.read_text(encoding="utf-8")
    for key, value in (
        ("{{CONTEXT}}", context_text),
        ("{{PREVIOUS_OUTPUT}}", previous_output),
        ("{{ERRORS}}", "\n".join(f"- {e}" for e in errors)),
    ):
        text = text.replace(key, value)
    return text


def match_errors(output, ctx: dict, validator: Draft202012Validator | None = None) -> list[str]:
    """Schema errors, then the checks a schema cannot express. Empty list means valid."""
    validator = validator or match_validator()
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors[:12]
    asked = [ln["line_no"] for ln in ctx["pending_lines"]]
    offered = {c["po_line_no"] for c in ctx["candidates"]}
    answered = [m["invoice_line_no"] for m in output["matches"]]
    for n in asked:
        times = answered.count(n)
        if times != 1:
            errors.append(f"invoice line {n} must be answered exactly once, it was answered {times} times")
    for n in sorted(set(answered) - set(asked)):
        errors.append(f"invoice line {n} was not asked about")
    used: dict[int, int] = {}
    for m in output["matches"]:
        po = m["po_line_no"]
        if po is None:
            continue
        if po not in offered:
            errors.append(f"PO line {po} is not one of the lines offered")
        elif po in used:
            errors.append(f"PO line {po} is used for invoice lines {used[po]} and {m['invoice_line_no']}; use it once")
        else:
            used[po] = m["invoice_line_no"]
    return errors
