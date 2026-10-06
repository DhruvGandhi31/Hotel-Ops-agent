"""The workflow's Code nodes versus the Python the eval measured.

The eval harness (Python) and the n8n workflow (JavaScript in Code nodes) are two implementations
of the same contract. These tests run the *actual* jsCode from workflows/ingest_invoice.json under
Node and require it to agree with the Python: same prompts, same verdicts on valid and invalid model
output. If they drift, the accuracy numbers stop describing what runs.
"""

import base64
import copy
import hashlib
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from hotel_datagen.build import build_dataset
from hotel_datagen.output import ground_truth
from hotel_evals.extract import (
    build_system_prompt,
    first_user_message,
    load_validator,
    retry_user_message,
    validation_errors,
)
from hotel_evals.oracle import truth_to_extraction

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "workflows" / "ingest_invoice.json"
HARNESS = ROOT / "evals" / "js" / "run_code_node.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(node: str, cases: list[dict], root: Path = ROOT) -> list[dict]:
    request = {"workflow": str(WORKFLOW), "node": node, "root": str(root), "cases": cases}
    proc = subprocess.run(
        ["node", str(HARNESS)], input=json.dumps(request), capture_output=True, text=True, timeout=180, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def validate_cases(outputs: list, run_index: int = 0) -> list[dict]:
    cases = [{"input": {"text": o if isinstance(o, str) else json.dumps(o)}, "runIndex": run_index} for o in outputs]
    return run_node("Validate Extraction", cases)


@pytest.fixture(scope="module")
def good_documents() -> list[dict]:
    ds = build_dataset(200, 42)
    by_case = {c.case_id: c for c in ds.cases}
    suppliers = {s.key: s for s in ds.suppliers}
    docs = []
    for c in ds.cases:
        if c.exact_resend_of is None:
            gt = json.loads(json.dumps(ground_truth(c, suppliers[c.doc.supplier_key], by_case, "x")))
            docs.append(truth_to_extraction(gt["document"]))
    return docs


# ---- prompts ---------------------------------------------------------------------------------


def test_system_prompt_is_identical_to_the_python_one():
    out = run_node(
        "Build Prompt",
        [{"input": {"text": "x" * 50}, "nodes": {"Hash File": {"json": {"file_sha256": "a", "filename": "f"}}}}],
    )
    assert out[0]["output"][0]["json"]["system"] == build_system_prompt()


def test_first_user_message_and_text_gate():
    text = "Some invoice text\nwith lines, long enough to count"
    nodes = {"Hash File": {"json": {"file_sha256": "abc", "filename": "inv.pdf"}}}
    got = run_node(
        "Build Prompt",
        [
            {"input": {"text": text}, "nodes": nodes},
            {"input": {"text": " \n "}, "nodes": nodes},
            {"input": {}, "nodes": nodes},
        ],
    )
    first = got[0]["output"][0]["json"]
    assert first["user"] == first_user_message(text) and first["has_text"] is True and first["attempt"] == 1
    assert first["file_sha256"] == "abc" and first["filename"] == "inv.pdf" and first["invoice_text"] == text
    assert got[1]["output"][0]["json"]["has_text"] is False
    assert got[2]["output"][0]["json"]["has_text"] is False


def test_retry_prompt_is_identical_to_the_python_one():
    text = "INVOICE TEXT with {{ERRORS}} inside it"  # placeholder-like text in the invoice must behave the same
    prev = '{"total": "bad"}'
    errors = ["total: does not match", "gst: is a required property"]
    out = run_node(
        "Build Retry Prompt",
        [
            {
                "input": {"valid": False, "attempt": 1, "raw": prev, "errors": errors},
                "nodes": {"Build Prompt": {"json": {"invoice_text": text, "system": "SYS"}}},
            }
        ],
    )
    item = out[0]["output"][0]["json"]
    assert item["user"] == retry_user_message(text, prev, errors)
    assert item["system"] == "SYS" and item["attempt"] == 2


# ---- validation ------------------------------------------------------------------------------


def test_every_ground_truth_extraction_passes(good_documents):
    results = validate_cases(good_documents)
    bad = [(i, r) for i, r in enumerate(results) if r.get("error") or not r["output"][0]["json"]["valid"]]
    assert not bad, bad[:2]


def test_attempt_number_comes_from_the_run_index(good_documents):
    got = [validate_cases([good_documents[0]], run_index=i)[0]["output"][0]["json"]["attempt"] for i in (0, 1)]
    assert got == [1, 2]


def _mutations(doc: dict, rng: random.Random):
    """Yield (label, mutated copy). Covers each field with values of every JSON type, plus the
    near-misses a model actually produces (formatting, precision, impossible dates, bad checksums)."""
    bad_values = [
        None,
        "",
        " ",
        0,
        -1,
        1.5,
        True,
        [],
        {},
        "abc",
        "-1.00",
        "1.0",
        "1.000",
        "$5.00",
        "1,430.74",
        "1430.74\n",
        " 1.00",
    ]

    def with_(path, value):
        d = copy.deepcopy(doc)
        target = d
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return d

    top = [k for k in doc if k != "lines"]
    line_keys = list(doc["lines"][0])
    for key in top:
        for value in rng.sample(bad_values, 6):
            yield f"{key}={value!r}", with_([key], value)
        d = copy.deepcopy(doc)
        del d[key]
        yield f"missing {key}", d
    for key in line_keys:
        for value in rng.sample(bad_values, 6):
            yield f"line.{key}={value!r}", with_(["lines", 0, key], value)
        d = copy.deepcopy(doc)
        del d["lines"][0][key]
        yield f"line missing {key}", d
    yield "extra top-level key", {**doc, "notes": "x"}
    yield "extra line key", with_(["lines", 0], {**doc["lines"][0], "confidence": 0.9})
    yield "no lines", with_(["lines"], [])
    yield "lines not a list", with_(["lines"], {"a": 1})
    for date in (
        "2026-02-31",
        "2026-13-01",
        "2026-00-10",
        "0000-01-01",
        "1899-12-31",
        "2026-1-1",
        "20/07/2026",
        "2026-07-20T00:00:00",
    ):
        yield f"invoice_date={date}", with_(["invoice_date"], date)
        yield f"due_date={date}", with_(["due_date"], date)
    for abn in ("51824753557", "5182475355", "51 824 753 55X", "00000000000", "51 824 753 556", "51824753556"):
        yield f"abn={abn}", with_(["supplier_abn"], abn)
    for q in (0, -2, 1.2345, 2.42, 2.4200001, 1000000, 1000001, 0.001, 0.0005, "3", None):
        yield f"quantity={q!r}", with_(["lines", 0, "quantity"], q)
    for amount in ("0.00", "999999999999.99", "1000000000000.00", "12.5", "12.500", "-0.00", "00.10"):
        yield f"total={amount}", with_(["total"], amount)
        yield f"line_total={amount}", with_(["lines", 0, "line_total"], amount)
    yield "currency USD", with_(["currency"], "USD")
    yield "currency lowercase", with_(["currency"], "aud")


def test_verdicts_match_python_on_hundreds_of_mutated_outputs(good_documents):
    rng = random.Random(7)
    cases: list[tuple[str, object]] = []
    for doc in good_documents[:12]:
        cases.extend(_mutations(doc, rng))
    cases += [
        ("not json", "I could not read this invoice."),
        ("truncated json", '{"supplier_name": "Acme", "total": '),
        ("json array", "[1, 2, 3]"),
        ("json null", "null"),
        ("json number", "42"),
        ("empty", ""),
    ]
    validator = load_validator()

    def python_valid(output) -> bool:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return False
        return validation_errors(output, validator) == []

    js = validate_cases([o for _, o in cases])
    mismatches = []
    for (label, output), result in zip(cases, js, strict=True):
        assert "error" not in result, (label, result)
        js_valid = result["output"][0]["json"]["valid"]
        if js_valid != python_valid(output):
            mismatches.append((label, f"js={js_valid}", f"python={python_valid(output)}"))
    assert not mismatches, mismatches[:10]
    assert len(cases) > 600
    # the corpus must contain both verdicts in quantity or the comparison proves nothing
    verdicts = [r["output"][0]["json"]["valid"] for r in js]
    assert 40 < sum(verdicts) < len(verdicts) - 100


def test_chain_node_output_shapes(good_documents):
    """With JSON format n8n hands over the parsed object; without it, {text}; a failed call, {error}."""
    doc = good_documents[0]
    cases = [
        {"input": doc},  # parsed object
        {"input": {"text": json.dumps(doc)}},  # text
        {"input": {"text": "oops"}},
        {"input": {"error": "Expected ',' or '}' after property value in JSON at position 47"}},
        {"input": {"error": "fetch failed: connect ECONNREFUSED 127.0.0.1:11434"}},
        {"input": {"error": "Internal Server Error"}},
    ]
    got = run_node("Validate Extraction", cases)
    valid = [g["output"][0]["json"]["valid"] if "output" in g else None for g in got]
    assert valid[:4] == [True, True, False, False]
    assert got[0]["output"][0]["json"]["extraction"] == doc
    # a model that emitted bad JSON gets the retry; a down server must fail the execution instead
    assert "not valid JSON" in got[3]["output"][0]["json"]["errors"][0]
    assert "model server failure" in got[4]["error"] and "model server failure" in got[5]["error"]


def test_validator_refuses_a_schema_keyword_it_does_not_implement(tmp_path, good_documents):
    """The interpreter must fail loudly, never silently skip a constraint."""
    root = tmp_path / "repo"
    (root / "schemas").mkdir(parents=True)
    schema = json.loads((ROOT / "schemas" / "invoice_extraction.json").read_text(encoding="utf-8"))
    schema["properties"]["total"]["multipleOf"] = 0.01
    (root / "schemas" / "invoice_extraction.json").write_text(json.dumps(schema), encoding="utf-8")
    got = run_node("Validate Extraction", [{"input": good_documents[0]}], root=root)
    assert "multipleOf" in got[0]["error"] and "not supported" in got[0]["error"]


def test_every_keyword_in_every_schema_is_supported_by_the_interpreter():
    """Run the interpreter over each real schema's keywords: a future schema must not break it."""
    source = next(
        n for n in json.loads(WORKFLOW.read_text(encoding="utf-8"))["nodes"] if n["name"] == "Validate Extraction"
    )["parameters"]["jsCode"]
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
            assert f"'{keyword}'" in source, (
                f"{schema_file.name} uses '{keyword}', which Validate Extraction does not handle"
            )


# ---- hashing and payloads --------------------------------------------------------------------


def test_hash_file_matches_sha256_and_rejects_non_pdfs():
    pdf = b"%PDF-1.4\n" + b"x" * 1000
    cases = [
        {"input": {}, "binary": {"file": {"fileName": "a.pdf"}}, "buffer": base64.b64encode(pdf).decode()},
        {"input": {}, "binary": {"file": {"fileName": "a.pdf"}}, "buffer": base64.b64encode(b"hello").decode()},
        {
            "input": {},
            "binary": {"file": {"fileName": "a.pdf"}},
            "buffer": base64.b64encode(b"%PDF-" + b"x" * (10 * 1024 * 1024)).decode(),
        },
        {"input": {}},
    ]
    got = [r["output"][0]["json"] for r in run_node("Hash File", cases)]
    assert got[0] == {
        "accepted": True,
        "file_sha256": hashlib.sha256(pdf).hexdigest(),
        "filename": "a.pdf",
        "size_bytes": len(pdf),
    }
    assert (got[1]["accepted"], got[1]["status_code"]) == (False, 415)
    assert (got[2]["accepted"], got[2]["status_code"]) == (False, 413)
    assert (got[3]["accepted"], got[3]["status_code"]) == (False, 400)


def test_ingest_payload_shape_matches_what_the_database_function_reads():
    verdict = {"valid": True, "attempt": 2, "extraction": {"invoice_number": "X"}}
    nodes = {
        "Build Prompt": {"json": {"file_sha256": "f" * 64, "filename": "inv.pdf"}},
        "Ollama Chat Model": {"params": {"model": "qwen3.5:9b"}},
    }
    got = run_node(
        "Prepare Ingest Payload",
        [
            {
                "input": verdict,
                "nodes": nodes,
                "execution": {"id": "9"},
                "workflow": {"id": "wf", "name": "Ingest Invoice"},
            }
        ],
    )
    payload = got[0]["output"][0]["json"]["payload"]
    assert payload == {
        "file_sha256": "f" * 64,
        "filename": "inv.pdf",
        "attempts": 2,
        "llm_model": "qwen3.5:9b",
        "execution_id": "9",
        "workflow_id": "wf",
        "workflow_name": "Ingest Invoice",
        "extraction": {"invoice_number": "X"},
    }


def test_needs_review_payload_for_both_routes():
    nodes = {
        "Build Prompt": {"json": {"file_sha256": "a" * 64, "filename": "s.pdf"}},
        "Ollama Chat Model": {"params": {"model": "m"}},
    }
    no_text = run_node("Prepare Needs Review Payload", [{"input": {"has_text": False}, "nodes": nodes}])[0]["output"][
        0
    ]["json"]["payload"]
    assert no_text["attempts"] == 0 and no_text["llm_model"] is None and "text layer" in no_text["reason"]
    failed = run_node(
        "Prepare Needs Review Payload",
        [{"input": {"valid": False, "attempt": 2, "errors": ["a: b", "c: d"], "raw": "{oops"}, "nodes": nodes}],
    )[0]["output"][0]["json"]["payload"]
    assert failed["attempts"] == 2 and failed["llm_model"] == "m" and failed["reason"] == "a: b; c: d"
    assert failed["last_output"] == "{oops"


def test_unreadable_pdf_reason_names_the_extractor_error():
    nodes = {"Build Prompt": {"json": {"file_sha256": "a" * 64, "filename": "s.pdf"}}}
    payload = run_node(
        "Prepare Needs Review Payload",
        [
            {"input": {"has_text": False, "pdf_error": "PdfReadError: EOF marker not found"}, "nodes": nodes},
            {"input": {"has_text": False, "pdf_error": None}, "nodes": nodes},
        ],
    )
    assert (
        payload[0]["output"][0]["json"]["payload"]["reason"]
        == "the PDF could not be read: PdfReadError: EOF marker not found"
    )
    assert "no extractable text layer" in payload[1]["output"][0]["json"]["payload"]["reason"]


def test_build_prompt_reads_the_pdf_text_service_reply():
    nodes = {"Hash File": {"json": {"file_sha256": "abc", "filename": "inv.pdf"}}}
    ok, unreadable = run_node(
        "Build Prompt",
        [
            {"input": {"text": "Invoice text, long enough to count as real", "pages": 1}, "nodes": nodes},
            {"input": {"text": "", "pages": 0, "error": "ValueError: PDF is encrypted"}, "nodes": nodes},
        ],
    )
    assert ok["output"][0]["json"]["has_text"] is True and ok["output"][0]["json"]["pdf_error"] is None
    assert unreadable["output"][0]["json"]["has_text"] is False
    assert unreadable["output"][0]["json"]["pdf_error"] == "ValueError: PDF is encrypted"
