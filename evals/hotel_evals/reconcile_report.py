"""Markdown report for a reconciliation eval run."""

from datetime import UTC, datetime

from .reconcile_scoring import CONDITIONS, FLAG_CODES
from .report import wilson

MODE_TEXT = {
    "rules": "rules only: ground-truth extraction and ground-truth line matches, SQL only",
    "matcher": "ground-truth extraction; the model matches the lines that need it (through n8n)",
    "e2e": "end to end: PDFs through the ingestion webhook, extraction by the model, matching by the model",
}


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _ci(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{lo * 100:.1f} to {hi * 100:.1f}%"


def render(meta: dict, score: dict, settings: dict) -> str:
    n = score["n"]
    fc = score["flag_class"]
    exp_flag = fc["tp"] + fc["fn"]
    lines = [
        "# Reconciliation eval" + (" (MOCK MODEL: pipeline test, not an accuracy result)" if meta.get("mock") else ""),
        "",
        f"- Run: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Mode: **{meta['mode']}**, {MODE_TEXT[meta['mode']]}",
        f"- Dataset: `{meta['dataset']}` (seed {meta['seed']}); invoices reconciled: **{n}** "
        "(exact re-sends are ingestion no-ops and are not reconciled)",
        f"- Rules and tolerances: {', '.join(f'{k} = {v}' for k, v in settings.items())}",
    ]
    if meta.get("model"):
        lines.append(f"- Model for line matching and extraction: `{meta['model']}`")
    lines += [
        "",
        "> **Read this first.** The synthetic labels and the reconciler share rules (the generator and the",
        "> reconciler both encode the 2% price tolerance and the half-cent-per-line GST allowance), so a",
        "> near-perfect *rules-only* score shows the rules are implemented as specified, not that they suit a",
        "> real hotel. The honest signal is the gap between the modes: what extraction and line matching errors",
        "> cost on top of the rules.",
        "",
        "## The `flag` class",
        "",
        "| Measure | Result |",
        "|---|---:|",
        "| Precision (flagged invoices that truly had a discrepancy) | "
        f"{_pct(fc['precision'])} ({fc['tp']}/{fc['tp'] + fc['fp']}) |",
        "| Recall (truly flagged invoices that were flagged) | "
        f"{_pct(fc['recall'])} ({fc['tp']}/{exp_flag}), 95% CI {_ci(fc['tp'], exp_flag)} |",
        f"| Caught or escalated to a human (flag or needs_review) | {_pct(fc['caught_or_escalated'])} |",
        f"| Truly flagged but **approved** | {fc['wrongly_approved']} |",
        f"| Clean invoices correctly recommended for approval | {_pct(fc['approved_correctly'])} |",
        f"| Invoices sent to `needs_review` | {score['needs_review_count']} of {n} |",
        "",
        "Status, expected (rows) against recommended (columns):",
        "",
        "| Expected \\ got | recommend_approve | flag | needs_review |",
        "|---|---:|---:|---:|",
    ]
    for expected in ("flag", "recommend_approve"):
        c = score["confusion"].get(expected, {})
        lines.append(
            f"| {expected} | {c.get('recommend_approve', 0)} | {c.get('flag', 0)} | {c.get('needs_review', 0)} |"
        )

    lines += [
        "",
        "## Precision and recall per discrepancy type",
        "",
        "| Reason code | Expected | Predicted | TP | FP | FN | Precision | Recall | Recall 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for code in FLAG_CODES:
        m = score["per_code"][code]
        lines.append(
            f"| `{code}` | {m['tp'] + m['fn']} | {m['tp'] + m['fp']} | {m['tp']} | {m['fp']} | {m['fn']} "
            f"| {_pct(m['precision'])} | {_pct(m['recall'])} | {_ci(m['tp'], m['tp'] + m['fn'])} |"
        )
    ec, mc = score["exact_code_match"], score["multi_code"]
    lines += [
        "",
        f"Exact reason-code set correct on {ec['correct']} of {ec['of']} invoices; on invoices with two or more "
        f"true reasons, {mc['correct']} of {mc['of']}. Small counts (for example `missing_po`) make recall "
        "figures coarse: read the interval.",
        "",
        "## Conditions that must not flag on their own",
        "",
        "Invoices that carry one of these and no true discrepancy:",
        "",
        "| Condition | Invoices | Wrongly flagged | needs_review | Correctly approved |",
        "|---|---:|---:|---:|---:|",
    ]
    for cond in CONDITIONS:
        c = score["condition_false_positives"][cond]
        lines.append(f"| `{cond}` | {c['invoices']} | {c['flagged']} | {c['needs_review']} | {c['approved']} |")

    mt = score["matcher"]
    lines += [
        "",
        "## Line matching",
        "",
        f"Lines that truly belong to a PO line: {mt['lines']}; matched to the right one: {mt['correct']} "
        f"({_pct(mt['accuracy'])}).",
        "",
        "| Method | Lines | Correct | Wrong match | Not matched |",
        "|---|---:|---:|---:|---:|",
    ]
    for method, b in mt["by_method"].items():
        lines.append(
            f"| {method} | {b['lines']} | {b['correct']} | {b['wrong']} | {b['lines'] - b['correct'] - b['wrong']} |"
        )
    if score["review_reasons"]:
        lines += [
            "",
            "Review reasons raised: " + ", ".join(f"`{k}` x {v}" for k, v in sorted(score["review_reasons"].items())),
        ]

    shown = score["mismatches"][:25]
    lines += [
        "",
        f"## Mismatches ({len(score['mismatches'])} invoices with a different code set or needs_review; "
        f"first {len(shown)})",
        "",
    ]
    for m in shown:
        lines.append(
            f"- `{m['file_id']}`: expected {m['expected'] or '[]'}, got {m['got'] or '[]'}, status `{m['status']}`"
            + (f", review {m['review']}" if m["review"] else "")
        )
    lines.append("")
    return "\n".join(lines)
