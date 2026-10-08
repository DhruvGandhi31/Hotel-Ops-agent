"""The line matcher's Code nodes (workflows/reconcile_invoice.json) versus the Python reference.

Same idea as test_workflow_code.py: run the workflow's real JavaScript under Node and require it to
agree with evals/hotel_evals/match.py on prompts and on the verdict for valid and invalid model output.
"""

import copy
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hotel_evals.match import (
    build_match_system_prompt,
    load_match_schema,
    match_errors,
    match_retry_message,
    match_user_message,
    match_validator,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "workflows"
RECONCILE = WORKFLOWS / "reconcile_invoice.json"
HARNESS = ROOT / "evals" / "js" / "run_code_node.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(node: str, cases: list[dict], workflow: Path = RECONCILE, root: Path = ROOT) -> list[dict]:
    request = {"workflow": str(workflow), "node": node, "root": str(root), "cases": cases}
    proc = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(request), capture_output=True, text=True, timeout=180, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def node_code(workflow: Path, name: str) -> str:
    wf = json.loads(workflow.read_text(encoding="utf-8"))
    return next(n for n in wf["nodes"] if n["name"] == name)["parameters"]["jsCode"]


CTX = {
    "invoice_id": 7,
    "invoice_number": "INV-00123",
    "pending_lines": [
        {
            "line_no": 2,
            "supplier_code": None,
            "description": "Roma Toms 10kg",
            "quantity": 10.0,
            "unit": "box",
            "unit_price_cents": 3200,
            "line_total_cents": 32000,
        },
        {
            "line_no": 4,
            "supplier_code": None,
            "description": "Olive oil, XV 4L",
            "quantity": 2.5,
            "unit": None,
            "unit_price_cents": 4590,
            "line_total_cents": 11475,
        },
    ],
    "candidates": [
        {
            "po_line_no": 1,
            "supplier_sku": "P-TOM10",
            "description": "Tomatoes Roma 10kg Box",
            "unit": "box",
            "quantity": 10,
            "unit_price_cents": 3200,
        },
        {
            "po_line_no": 3,
            "supplier_sku": None,
            "description": "Olive Oil Extra Virgin 4L",
            "unit": "tin",
            "quantity": 3,
            "unit_price_cents": 4590,
        },
        {
            "po_line_no": 5,
            "supplier_sku": "X-1",
            "description": 'Item with "quotes"',
            "unit": "each",
            "quantity": 0.5,
            "unit_price_cents": 5,
        },
    ],
}


def valid_answer() -> dict:
    return {
        "matches": [
            {"invoice_line_no": 2, "po_line_no": 1, "confidence": 0.95},
            {"invoice_line_no": 4, "po_line_no": 3, "confidence": 0.9},
        ]
    }


# ---- prompts -----------------------------------------------------------------------------------


def test_system_prompt_is_identical_to_the_python_one():
    out = run_node("Build Match Prompt", [{"input": {"result": CTX}}])
    assert out[0]["output"][0]["json"]["system"] == build_match_system_prompt()


def test_user_message_is_identical_including_number_and_money_formatting():
    item = run_node("Build Match Prompt", [{"input": {"result": CTX}}])[0]["output"][0]["json"]
    assert item["user"] == match_user_message(CTX)
    assert item["attempt"] == 1 and item["invoice_id"] == 7
    assert "quantity 2.5" in item["user"] and "quantity 10," in item["user"]  # 10.0 -> "10", 2.5 -> "2.5"
    assert "unit no unit printed" in item["user"] and "$0.05" in item["user"] and "SKU none" in item["user"]


@pytest.mark.parametrize("quantity", [0.001, 0.5, 1, 2.25, 10, 100, 1234.567, 0.1])
def test_number_formatting_agrees_for_awkward_quantities(quantity):
    ctx = copy.deepcopy(CTX)
    ctx["pending_lines"][0]["quantity"] = quantity
    js = run_node("Build Match Prompt", [{"input": {"result": ctx}}])[0]["output"][0]["json"]["user"]
    assert js == match_user_message(ctx)


def test_retry_prompt_is_identical_to_the_python_one():
    first = run_node("Build Match Prompt", [{"input": {"result": CTX}}])[0]["output"][0]["json"]
    failed = {
        "valid": False,
        "attempt": 1,
        "raw": '{"matches": []}',
        "errors": ["(root)/matches: fewer than 1 items", "x"],
    }
    got = run_node("Build Match Retry Prompt", [{"input": failed, "nodes": {"Build Match Prompt": {"json": first}}}])[
        0
    ]["output"][0]["json"]
    assert got["user"] == match_retry_message(first["user"], failed["raw"], failed["errors"])
    assert got["system"] == first["system"] and got["attempt"] == 2


# ---- validation --------------------------------------------------------------------------------


def validate(outputs: list, ctx: dict = CTX, run_index: int = 0) -> list[dict]:
    first = {"json": {"context": ctx}}
    cases = [
        {
            "input": o
            if isinstance(o, dict) and "text" not in o and "error" not in o
            else o
            if isinstance(o, dict)
            else {"text": o},
            "runIndex": run_index,
            "nodes": {"Build Match Prompt": first},
        }
        for o in outputs
    ]
    return run_node("Validate Matches", cases)


def _mutations(rng: random.Random):
    base = valid_answer()

    def with_(path, value):
        d = copy.deepcopy(base)
        t = d
        for k in path[:-1]:
            t = t[k]
        t[path[-1]] = value
        return d

    bad = [None, "", 0, 1, -1, 2, 1.5, 0.5, True, "abc", [], {}, 0.84, 1.01, -0.01]
    for key in ("invoice_line_no", "po_line_no", "confidence"):
        for value in bad:
            yield f"{key}={value!r}", with_(["matches", 0, key], value)
        d = copy.deepcopy(base)
        del d["matches"][0][key]
        yield f"missing {key}", d
    yield "extra key", {**base, "notes": "x"}
    yield "extra entry key", with_(["matches", 0], {**base["matches"][0], "why": "same product"})
    yield "empty matches", {"matches": []}
    yield "matches not a list", {"matches": {"a": 1}}
    yield "no matches key", {}
    yield "answered line twice", {"matches": [*base["matches"], base["matches"][0]]}
    yield "missed a line", {"matches": base["matches"][:1]}
    yield "unknown invoice line", with_(["matches", 0, "invoice_line_no"], 9)
    yield "po line not offered", with_(["matches", 0, "po_line_no"], 2)
    yield "po line used twice", with_(["matches", 1, "po_line_no"], 1)
    yield "both null is fine", {"matches": [{**m, "po_line_no": None} for m in base["matches"]]}
    yield (
        "confidence 0 and 1 are fine",
        {"matches": [{**base["matches"][0], "confidence": 0}, {**base["matches"][1], "confidence": 1}]},
    )
    yield (
        "swap is fine",
        {"matches": [{**base["matches"][0], "po_line_no": 3}, {**base["matches"][1], "po_line_no": 1}]},
    )
    yield "po line 5 offered", with_(["matches", 0, "po_line_no"], 5)
    yield "po line as float 1.0", with_(["matches", 0, "po_line_no"], 1.0)
    for _ in range(40):  # random type confusion on a random field
        field = rng.choice(["invoice_line_no", "po_line_no", "confidence"])
        yield (
            f"random {field}",
            with_(["matches", rng.randrange(2), field], rng.choice([None, "x", 3.7, -5, 99, True, [], {}])),
        )


def test_verdicts_match_python_on_generated_outputs():
    rng = random.Random(11)
    cases = [("valid", valid_answer()), *_mutations(rng)]
    cases += [
        ("not json", "I think line 2 is tomatoes."),
        ("truncated", '{"matches": [{"invoice_line_no": 2'),
        ("empty", ""),
        ("array", "[1]"),
    ]
    validator = match_validator()

    def python_valid(output) -> bool:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return False
        return match_errors(output, CTX, validator) == []

    js = validate([o for _, o in cases])
    mismatches = []
    for (label, output), result in zip(cases, js, strict=True):
        assert "error" not in result, (label, result)
        js_valid = result["output"][0]["json"]["valid"]
        if js_valid != python_valid(output):
            mismatches.append((label, f"js={js_valid}", f"python={python_valid(output)}"))
    assert not mismatches, mismatches[:10]
    verdicts = [r["output"][0]["json"]["valid"] for r in js]
    assert 5 < sum(verdicts) < len(verdicts) - 20, "the corpus must contain plenty of both verdicts"
    assert js[0]["output"][0]["json"]["matches"] == valid_answer()["matches"]


def test_chain_output_shapes_and_the_outage_rule():
    answer = valid_answer()
    got = validate(
        [
            answer,
            {"text": json.dumps(answer)},
            {"text": "oops"},
            {"error": "Expected ',' or '}' after property value in JSON at position 47"},
            {"error": "fetch failed: connect ECONNREFUSED 127.0.0.1:11434"},
        ]
    )
    assert [g["output"][0]["json"]["valid"] if "output" in g else None for g in got[:4]] == [True, True, False, False]
    assert "not valid JSON" in got[3]["output"][0]["json"]["errors"][0]
    assert "model server failure" in got[4]["error"]  # an outage fails the execution, it is not the model's fault


def test_attempt_number_comes_from_the_run_index():
    assert [validate([valid_answer()], run_index=i)[0]["output"][0]["json"]["attempt"] for i in (0, 1)] == [1, 2]


def test_a_confidence_of_exactly_zero_and_one_are_allowed_but_integers_are_checked():
    ok = validate(
        [
            {
                "matches": [
                    {"invoice_line_no": 2, "po_line_no": None, "confidence": 0},
                    {"invoice_line_no": 4, "po_line_no": None, "confidence": 1},
                ]
            }
        ]
    )
    assert ok[0]["output"][0]["json"]["valid"] is True
    bad = validate(
        [
            {
                "matches": [
                    {"invoice_line_no": 2.5, "po_line_no": None, "confidence": 0.5},
                    {"invoice_line_no": 4, "po_line_no": None, "confidence": 0.5},
                ]
            }
        ]
    )
    assert bad[0]["output"][0]["json"]["valid"] is False


# ---- the shared interpreter and the nodes around it --------------------------------------------


def _interpreter(code: str) -> str:
    return re.search(r"// >>> schema-interpreter.*?// <<< schema-interpreter", code, re.S).group(0)


def test_both_validators_carry_the_identical_schema_interpreter():
    extraction = _interpreter(node_code(WORKFLOWS / "ingest_invoice.json", "Validate Extraction"))
    matches = _interpreter(node_code(RECONCILE, "Validate Matches"))
    assert extraction == matches and len(extraction) > 2000


def test_unsupported_schema_keyword_still_fails_loudly_in_the_matcher_validator(tmp_path):
    root = tmp_path / "repo"
    (root / "schemas").mkdir(parents=True)
    schema = load_match_schema()
    schema["properties"]["matches"]["uniqueItems"] = True
    (root / "schemas" / "line_match.json").write_text(json.dumps(schema), encoding="utf-8")
    got = run_node(
        "Validate Matches",
        [{"input": valid_answer(), "nodes": {"Build Match Prompt": {"json": {"context": CTX}}}}],
        root=root,
    )
    assert "uniqueItems" in got[0]["error"] and "not supported" in got[0]["error"]


def test_prepare_nodes_shape_what_reconcile_invoice_reads():
    prompt = {"json": {"invoice_id": 7}}
    matched = run_node(
        "Prepare Matched",
        [
            {
                "input": {"valid": True, "attempt": 2, "matches": valid_answer()["matches"]},
                "nodes": {"Build Match Prompt": prompt},
            }
        ],
    )[0]["output"][0]["json"]
    assert matched["invoice_id"] == 7 and matched["matches"] == valid_answer()["matches"]
    assert matched["matcher"] == {"status": "matched", "attempts": 2, "lines": 2}
    assert matched["context"]["workflow_name"] == "Test Workflow"

    failed = run_node(
        "Prepare Failed Match",
        [{"input": {"valid": False, "attempt": 2, "errors": ["a", "b"]}, "nodes": {"Build Match Prompt": prompt}}],
    )[0]["output"][0]["json"]
    assert failed["matches"] == [] and failed["matcher"] == {"status": "failed", "attempts": 2, "errors": ["a", "b"]}

    unneeded = run_node(
        "Prepare Unneeded", [{"input": {}, "nodes": {"Get Candidates": {"json": {"result": {"invoice_id": 9}}}}}]
    )
    unneeded = unneeded[0]["output"][0]["json"]
    assert unneeded["invoice_id"] == 9 and unneeded["matches"] == [] and unneeded["matcher"] == {"status": "not_needed"}


def test_api_request_validation():
    cases = [
        {"input": {"body": b}}
        for b in ({"invoice_id": 5}, {"invoice_id": "5"}, {"invoice_id": 0}, {"invoice_id": 1.5}, {}, None)
    ]
    got = [
        r["output"][0]["json"]
        for r in run_node("Read Request", cases, workflow=WORKFLOWS / "reconcile_invoice_api.json")
    ]
    assert got[0] == {"valid": True, "invoice_id": 5}
    assert all(g["valid"] is False and g["status_code"] == 400 for g in got[1:])


def test_every_keyword_in_every_schema_is_handled_by_the_shared_interpreter():
    interpreter = _interpreter(node_code(RECONCILE, "Validate Matches"))
    for schema_file in sorted((ROOT / "schemas").glob("*.json")):
        keywords: set[str] = set()

        def walk(node, in_properties=False, keywords=keywords):
            if isinstance(node, dict):
                for key, value in node.items():
                    if not in_properties:
                        keywords.add(key)
                    walk(value, in_properties=(key == "properties" and not in_properties))
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(schema_file.read_text(encoding="utf-8")))
        for keyword in keywords:
            assert f"'{keyword}'" in interpreter, f"{schema_file.name} uses '{keyword}' which the interpreter lacks"
