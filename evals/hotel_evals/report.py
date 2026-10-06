"""Markdown report for an extraction eval run."""

import math
import statistics
from datetime import UTC, datetime

from .scoring import GATE_HEADER_FIELDS, HEADER_FIELDS, LINE_FIELDS

TARGETS = {"total": 0.98, "invoice_number": 0.98, "po_number": 0.98, "line_items": 0.90}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, math.ceil(q * len(s)) - 1)]


def render(meta: dict, agg: dict, runs: list[dict], ingestion: dict | None = None, failures_shown: int = 25) -> str:
    n = agg["overall"]["n"]
    header_rate = agg["overall"]["header"]
    k_correct, k_truth, k_pred = agg["overall"]["line_counts"]
    ok = {
        "total": header_rate["total"],
        "invoice_number": header_rate["invoice_number"],
        "po_number": header_rate["po_number"],
        "line_items": agg["overall"]["line_recall"],
    }
    counts = {f: round(header_rate[f] * n) for f in GATE_HEADER_FIELDS} | {"line_items": k_correct}
    denoms = {f: n for f in GATE_HEADER_FIELDS} | {"line_items": k_truth}

    mock_banner = [
        "> **MOCK MODEL. This is a pipeline test, not an accuracy result.** The model behind n8n was",
        "> `evals/mock_ollama.py`, which answers from ground truth. It shows the webhook, PDF text,",
        "> validation, retry and database steps work; it says nothing about how well any LLM reads invoices.",
        "",
    ]
    lines = [
        "# Pipeline test (mock model)" if meta.get("mock") else f"# Extraction eval: {meta['model']}",
        "",
        *(mock_banner if meta.get("mock") else []),
        f"- Run: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Target: `{meta['target']}` ({meta.get('host', '')}), model `{meta['model']}`, "
        f"format mode `{meta['format_mode']}`, num_ctx {meta['num_ctx']}, "
        f"{meta.get('sampling', 'temperature 0, seed 42')}",
        f"- Dataset: `{meta['dataset']}` (seed {meta['seed']}, n={meta['n_total']}); scored files: **{n}** "
        "(exact re-sends are excluded from extraction scoring: same bytes as an original)",
        f"- Input text: {meta['text_source']}",
        f"- Prompt sha256: `{meta['prompt_sha'][:12]}`, schema sha256: `{meta['schema_sha'][:12]}`",
        "",
        "## Gate targets",
        "",
        "| Metric | Target | Result | 95% CI | Pass |",
        "|---|---:|---:|---:|:---:|",
    ]
    for key, target in TARGETS.items():
        lo, hi = wilson(counts[key], denoms[key])
        label = (
            "line items (strict: description, quantity, unit price, line total)" if key == "line_items" else f"`{key}`"
        )
        lines.append(
            f"| {label} | >= {_pct(target)} | {_pct(ok[key])} ({counts[key]}/{denoms[key]}) "
            f"| {_pct(lo)} to {_pct(hi)} | {'PASS' if ok[key] >= target else 'FAIL'} |"
        )
    lines += [
        "",
        f"With n={n}, a 98% target allows at most {int(n * 0.02)} misses per field; one run is a weak signal near the "
        "threshold, so read the interval as well as the point estimate.",
        "",
        "## Pipeline health",
        "",
    ]
    extracted = [r for r in runs if r["status"] == "extracted"]
    first_try = [r for r in extracted if r["attempts"] == 1]
    errored = [r for r in runs if r["status"] == "error"]
    lat = [r["latency_s"] for r in runs]
    lines += [
        f"- Valid first attempt: {len(first_try)}/{len(runs)}",
        f"- Valid after one retry: {len(extracted) - len(first_try)}/{len(runs)}",
        f"- needs_review (invalid twice): {sum(r['status'] == 'needs_review' for r in runs)}/{len(runs)}",
    ]
    if errored:
        lines.append(f"- Workflow errors (HTTP 4xx/5xx, scored as failures): {len(errored)}/{len(runs)}")
    lines.append(
        f"- Latency per invoice (incl. retry): median {statistics.median(lat):.1f}s, "
        f"p95 {_percentile(lat, 0.95):.1f}s, total {sum(lat) / 60:.1f} min"
    )
    if any(r["prompt_tokens"] for r in runs):
        lines += [
            f"- Tokens per invoice: median prompt {int(statistics.median(r['prompt_tokens'] for r in runs))}, "
            f"median completion {int(statistics.median(r['completion_tokens'] for r in runs))}",
            f"- Context: max {max(r['context_used'] for r in runs)} of {meta['num_ctx']} tokens used; "
            f"{sum(r['near_context_limit'] for r in runs)} invoices within 5% of the limit "
            "(Ollama silently truncates beyond it)",
        ]
    else:
        lines.append("- Tokens and context use: not visible through the workflow (see `ops.llm_calls`, planned for P6)")
    lines += [
        f"- Invoices with every field and every line correct: {_pct(agg['overall']['invoices_fully_correct'])}",
        f"- Line items: {k_correct} correct of {k_truth} true lines; {k_pred} lines predicted "
        f"(precision {_pct(agg['overall']['line_precision'])}, recall {_pct(agg['overall']['line_recall'])})",
        "",
    ]
    if ingestion is not None:
        lines += ["## Ingestion behaviour", ""]
        lines += [
            "Whether the pipeline did the right thing with each file, apart from extraction accuracy: "
            "`original` stored as new; `duplicate` (same invoice, different file) stored and linked to the "
            "original; `noop` (identical file) changes nothing.",
            "",
            "| Expected | Files | Correct | Extraction failed (needs_review) | Indeterminate | Wrong |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for kind, c in ingestion["by_kind"].items():
            lines.append(
                f"| {kind} | {sum(c.values())} | {c.get('ok', 0)} | {c.get('model_failed', 0)} "
                f"| {c.get('indeterminate', 0)} | {c.get('wrong', 0)} |"
            )
        for j in ingestion["wrong"][:10]:
            lines.append(f"\n- `{j['file_id']}` ({j['expected']}): {j['note']}")
        lines.append("")
    lines += [
        "## Header fields",
        "",
        "| Field | Accuracy |" + "".join(f" {t} |" for t in agg["by_template"]),
        "|---|---:|" + "---:|" * len(agg["by_template"]),
    ]
    for f in HEADER_FIELDS:
        row = f"| `{f}`{' *' if f in GATE_HEADER_FIELDS else ''} | {_pct(header_rate[f])} |"
        row += "".join(f" {_pct(b['header'][f])} |" for b in agg["by_template"].values())
        lines.append(row)
    lines += [
        "",
        "`*` gate field. Template columns: "
        + ", ".join(f"{t} (n={b['n']})" for t, b in agg["by_template"].items())
        + ".",
        "",
        "## Line fields",
        "",
        "Per-field accuracy over true lines, paired by position, and only counted when the invoice has the right "
        "number of lines (otherwise all its lines count as wrong).",
        "",
        "| Field | Accuracy |",
        "|---|---:|",
    ]
    lines += [f"| `{f}` | {_pct(agg['overall']['line_fields'][f])} |" for f in LINE_FIELDS]
    lines += ["", "| Template | Line recall | Line precision |", "|---|---:|---:|"]
    lines += [
        f"| {t} | {_pct(b['line_recall'])} | {_pct(b['line_precision'])} |" for t, b in agg["by_template"].items()
    ]

    failing = [r for r in runs if r["score"]["failures"] or r["status"] != "extracted"]
    lines += ["", f"## Failures ({len(failing)} invoices; first {min(failures_shown, len(failing))})", ""]
    for r in failing[:failures_shown]:
        lines.append(f"- `{r['file_id']}` (template {r['template']}, {r['status']}, attempts {r['attempts']})")
        if r["status"] != "extracted":
            lines.append(f"  - validation: {'; '.join(r['errors'])[:300]}")
        for field_name, expected, got in r["score"]["failures"][:4]:
            lines.append(f"  - {field_name}: expected `{expected}`, got `{got}`")
    lines.append("")
    return "\n".join(lines)
