"""Did ingestion do the right thing with each file, separately from whether the extraction was right?

Expected behaviour comes from the ground-truth labels (the rule is in docs/architecture.md, "Idempotency"):
  original   first time this invoice is seen: stored, duplicate_of is empty
  duplicate  same supplier + invoice number + total, different file: stored, duplicate_of = the original
  noop       byte-identical file already seen: nothing changes, the original's invoice is returned

A file whose extraction failed (needs_review) cannot be judged on this; it is counted apart, as a
model failure. A duplicate or re-send of such a file is `indeterminate` for the same reason.
"""

from collections import Counter


def expected_kind(labels: dict) -> str:
    if labels["exact_resend_of"]:
        return "noop"
    if labels["duplicate_of"]:
        return "duplicate"
    return "original"


def judge(file_id: str, labels: dict, response: dict, by_file: dict[str, dict]) -> dict:
    """by_file: file_id -> response body of every file uploaded so far (in arrival order)."""
    kind = expected_kind(labels)
    status = response.get("status")
    result = {"file_id": file_id, "expected": kind, "observed": status, "outcome": "wrong", "note": ""}

    if status in (None, "rejected", "error"):
        result["note"] = f"workflow did not process the file (HTTP error or {status})"
        return result

    if kind == "original":
        if status == "needs_review":
            result["outcome"] = "model_failed"
        elif status == "extracted" and response.get("duplicate_of") is None:
            result["outcome"] = "ok"
        else:
            result["note"] = f"status={status} duplicate_of={response.get('duplicate_of')}"
        return result

    original = by_file.get(labels["duplicate_of"] or labels["exact_resend_of"], {})
    if original.get("status") != "extracted":
        result["outcome"] = "indeterminate"
        result["note"] = f"its original is {original.get('status')}"
        return result

    if kind == "duplicate":
        if status == "extracted" and response.get("duplicate_of") == original["invoice_id"]:
            result["outcome"] = "ok"
        else:
            result["note"] = f"duplicate_of={response.get('duplicate_of')}, expected {original['invoice_id']}"
    else:  # noop
        if status == "noop" and response.get("invoice_id") == original["invoice_id"]:
            result["outcome"] = "ok"
        else:
            result["note"] = (
                f"status={status} invoice_id={response.get('invoice_id')}, expected noop on {original['invoice_id']}"
            )
    return result


def summarise(judgements: list[dict]) -> dict:
    table: dict[str, Counter] = {}
    for j in judgements:
        table.setdefault(j["expected"], Counter())[j["outcome"]] += 1
    return {
        "by_kind": {k: dict(v) for k, v in sorted(table.items())},
        "wrong": [j for j in judgements if j["outcome"] == "wrong"],
        "all_ok": all(j["outcome"] in ("ok", "model_failed", "indeterminate") for j in judgements),
    }
