"""Eval runner.

    # the extraction step alone, against a local Ollama model (model selection, prompt tuning)
    python evals/run.py --suite extraction --target ollama --model qwen3.5:9b

    # the whole ingestion pipeline through the n8n webhook (the gate run)
    python evals/run.py --suite extraction --target n8n

Reads data-gen/out (python data-gen/generate.py --n 200 --seed 42), writes a summary to
evals/results/ (committed) and per-invoice raw output to evals/results/raw/ (gitignored).
Exit status is 0 when every gate target is met, 1 when any is missed, 2 on bad usage,
3 when the server failed (resume with --resume).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from hotel_evals import ingestion, report
from hotel_evals.extract import (
    FORMAT_MODES,
    SCHEMA_PATH,
    Extractor,
    TransportError,
    build_system_prompt,
    load_schema,
    ollama_chat_fn,
)
from hotel_evals.n8n_client import upload_invoice
from hotel_evals.scoring import aggregate, score_extraction

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "pdf-text"))  # the extraction the workflow's service runs

from pdf_text import extract_text  # noqa: E402

# 127.0.0.1, not localhost: on Windows the name resolves IPv6 first and every request pays a ~2s timeout.
DEFAULT_WEBHOOK = "http://127.0.0.1:5678/webhook/invoice-upload"


def pdf_text(path: Path) -> str:
    return extract_text(path.read_bytes())[0]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_prior(raw_path: Path, resume: bool) -> list[dict]:
    if not (resume and raw_path.exists()):
        return []
    prior = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    print(f"resuming: {len(prior)} invoices already in {raw_path.name}")
    return prior


def run_record(entry: dict, score: dict, **fields) -> dict:
    return {
        "file_id": entry["file_id"],
        "template": entry["template"],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "context_used": 0,
        "near_context_limit": False,
        "score": score,
        **fields,
    }


def run_ollama(args, manifest: dict, truth_of, raw_path: Path):
    files = [f for f in manifest["files"] if f["expected_ingestion"] == "insert"]
    if args.limit:
        files = files[: args.limit]
    schema = load_schema()
    extractor = Extractor(
        ollama_chat_fn(args.model, args.host, schema, args.num_ctx, format_mode=args.format_mode), schema
    )
    prior = load_prior(raw_path, args.resume)
    done = {p["file_id"] for p in prior}
    rows = list(prior)
    with raw_path.open("a" if args.resume else "w", encoding="utf-8") as raw_out:
        for i, entry in enumerate([e for e in files if e["file_id"] not in done], start=len(done) + 1):
            result = extractor.extract(pdf_text(args.data / entry["file"]))
            score = score_extraction(result.output, truth_of(entry)["document"])
            row = run_record(
                entry,
                score,
                status=result.status,
                attempts=result.attempts,
                errors=result.errors,
                latency_s=result.latency_s,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                context_used=result.max_context_used,
                near_context_limit=result.max_context_used >= args.num_ctx * 0.95,
            )
            rows.append(row)
            raw_out.write(json.dumps({**row, "output": result.output, "raw": result.raw}) + "\n")
            raw_out.flush()
            print(
                f"[{i}/{len(files)}] {entry['file_id']} {entry['template']} {result.status:<12} "
                f"try={result.attempts} {result.latency_s:5.1f}s  {_verdict(score)}",
                flush=True,
            )
    meta = {
        "model": args.model,
        "target": "ollama (extraction step only)",
        "host": args.host,
        "format_mode": args.format_mode,
        "num_ctx": args.num_ctx,
        "text_source": "pdf-text service (pypdf), the same code the workflow runs",
        "prompt_sha": hashlib.sha256(build_system_prompt(schema).encode()).hexdigest(),
        "schema_sha": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
    }
    return rows, None, meta


def run_n8n(args, manifest: dict, truth_of, raw_path: Path):
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"set {args.token_env} (the X-Ingest-Token value from .env)")
    files = manifest["files"][: args.limit] if args.limit else manifest["files"]  # arrival order matters
    prior = load_prior(raw_path, args.resume)
    done = {p["file_id"] for p in prior}
    rows = [{k: v for k, v in p.items() if k != "response"} for p in prior]
    responses = {p["file_id"]: p["response"] for p in prior}
    judgements = [ingestion.judge(p["file_id"], truth_of(p)["labels"], p["response"], responses) for p in prior]
    models = set()
    with raw_path.open("a" if args.resume else "w", encoding="utf-8") as raw_out:
        for i, entry in enumerate([e for e in files if e["file_id"] not in done], start=len(done) + 1):
            truth = truth_of(entry)
            started = time.perf_counter()
            code, body = upload_invoice(
                args.webhook, token, f"{entry['file_id']}.pdf", (args.data / entry["file"]).read_bytes()
            )
            elapsed = time.perf_counter() - started
            status = body.get("status") if code == 200 else "error"
            response = {**body, "status": status, "http_status": code}
            if body.get("llm_model"):
                models.add(body["llm_model"])
            if i == 1 and truth["labels"]["expected_ingestion"] == "insert" and status == "noop":
                raise SystemExit(
                    "the first file was already known to the pipeline: ingestion state is not clean.\n"
                    "Run: bash scripts/reset-ingestion.sh"
                )
            judgement = ingestion.judge(entry["file_id"], truth["labels"], response, responses)
            responses[entry["file_id"]] = response
            judgements.append(judgement)

            if truth["labels"]["expected_ingestion"] == "insert":  # exact re-sends are not extraction cases
                score = score_extraction(response.get("extraction"), truth["document"])
                row = run_record(
                    entry,
                    score,
                    status=status if status in ("extracted", "needs_review") else "error",
                    attempts=response.get("attempts", 0),
                    errors=[response.get("reason") or response.get("message") or ""] if status != "extracted" else [],
                    latency_s=elapsed,
                )
                rows.append(row)
            else:
                row = run_record(
                    entry,
                    {"header": {}, "lines": {}, "failures": []},
                    status=status,
                    attempts=0,
                    errors=[],
                    latency_s=elapsed,
                )
            raw_out.write(json.dumps({**row, "response": response}) + "\n")
            raw_out.flush()
            print(
                f"[{i}/{len(files)}] {entry['file_id']} {entry['template']} {status:<12} "
                f"{elapsed:5.1f}s  {judgement['outcome']}",
                flush=True,
            )

    schema = load_schema()
    meta = {
        "model": ", ".join(sorted(models)) or "(no model call made)",
        "target": "n8n workflow `Ingest Invoice` (webhook, PDF text, model, validation, database)",
        "host": args.webhook,
        "format_mode": "json (n8n Ollama node)",
        "num_ctx": 4096,
        "sampling": "temperature 0",
        "text_source": "`pdf-text` service (pypdf) called by the workflow, the same code the dev evals use",
        "prompt_sha": hashlib.sha256(build_system_prompt(schema).encode()).hexdigest(),
        "schema_sha": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
    }
    return (
        [r for r in rows if r["score"]["header"] or r["status"] in ("error", "needs_review")],
        ingestion.summarise(judgements),
        meta,
    )


def _verdict(score: dict) -> str:
    bad = len(score["failures"])
    return "ok" if not bad else f"{bad} wrong"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=["extraction", "reconciliation"], required=True)
    ap.add_argument("--target", choices=["ollama", "n8n"], default="ollama")
    ap.add_argument("--model", help="Ollama model tag, e.g. qwen3.5:9b (target ollama)")
    ap.add_argument("--host", default="http://localhost:11434", help="Ollama URL (target ollama)")
    ap.add_argument(
        "--webhook",
        default=os.environ.get("WEBHOOK", DEFAULT_WEBHOOK),
        help="ingestion webhook URL (target n8n; default: $WEBHOOK or the local stack)",
    )
    ap.add_argument(
        "--token-env", default="INGEST_WEBHOOK_TOKEN", help="env var holding the webhook token (target n8n)"
    )
    ap.add_argument("--data", type=Path, default=ROOT / "data-gen" / "out")
    ap.add_argument("--limit", type=int, help="first N files only (smoke run; no report is committed)")
    ap.add_argument(
        "--format-mode",
        choices=FORMAT_MODES,
        default="schema",
        help="schema: constrained to the JSON schema; json: generic JSON mode (what n8n's Ollama node offers); "
        "none: prompt only (target ollama)",
    )
    ap.add_argument("--num-ctx", type=int, default=4096, help="4096 keeps a 9B model fully on an 8 GB GPU")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "results")
    ap.add_argument("--resume", action="store_true", help="continue a run that stopped: skip files already done")
    ap.add_argument(
        "--mock",
        action="store_true",
        help="the model behind n8n is the mock (evals/mock_ollama.py): a pipeline test, stamped in the report "
        "and written to a separate file so it can never be mistaken for an accuracy result",
    )
    ap.add_argument("--tag", default="", help="suffix for the output files, e.g. v2 -> extraction-n8n-v2.md")
    ap.add_argument(
        "--overwrite", action="store_true", help="allow replacing an existing report or raw output (refused by default)"
    )
    ap.add_argument(
        "--source",
        choices=["truth", "pdf"],
        default="truth",
        help="reconciliation: ground-truth extraction or the PDFs",
    )
    ap.add_argument(
        "--matcher", choices=["oracle", "n8n"], default="oracle", help="reconciliation: who matches reworded lines"
    )
    ap.add_argument(
        "--reconcile-webhook",
        default=os.environ.get("RECONCILE_WEBHOOK", "http://127.0.0.1:5678/webhook/reconcile"),
        help="reconciliation webhook URL (suite reconciliation, --matcher n8n)",
    )
    ap.add_argument(
        "--expect-perfect", action="store_true", help="reconciliation: exit 1 unless every invoice is exactly right"
    )
    args = ap.parse_args(argv)

    if args.suite == "reconciliation":
        if not (args.data / "manifest.json").exists():
            print(f"{args.data / 'manifest.json'} not found; run: python data-gen/generate.py", file=sys.stderr)
            return 2
        from hotel_evals.reconcile_eval import run_suite

        return run_suite(args, ROOT)
    if args.target == "ollama" and not args.model:
        print("--model is required for --target ollama", file=sys.stderr)
        return 2
    manifest_path = args.data / "manifest.json"
    if not manifest_path.exists():
        print(f"{manifest_path} not found; run: python data-gen/generate.py --n 200 --seed 42", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def truth_of(entry: dict) -> dict:
        return json.loads((args.data / "ground_truth" / f"{entry['file_id']}.json").read_text(encoding="utf-8"))

    raw_dir = args.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if args.mock and args.target != "n8n":
        print("--mock only applies to --target n8n", file=sys.stderr)
        return 2
    tag = (
        "n8n-mock"
        if args.mock
        else "n8n"
        if args.target == "n8n"
        else slug(args.model) + ("" if args.format_mode == "schema" else f"-{args.format_mode}")
    )
    if args.tag:
        tag = f"{tag}-{args.tag}"
    raw_path = raw_dir / f"extraction-{tag}.jsonl"
    report_path = args.out / f"extraction-{tag}.md"
    if not (args.resume or args.limit or args.overwrite):
        # A gate run takes an hour and its report is evidence: never replace one by accident.
        existing = [path for path in (report_path, raw_path) if path.exists()]
        if existing:
            print("refusing to overwrite: " + ", ".join(str(path) for path in existing), file=sys.stderr)
            print("use --tag to name this run, --resume to continue it, or --overwrite to replace it", file=sys.stderr)
            return 2

    try:
        runner = run_n8n if args.target == "n8n" else run_ollama
        rows, ingest_summary, meta = runner(args, manifest, truth_of, raw_path)
    except TransportError as exc:
        print(f"\nSTOPPED: {exc}\nrerun with --resume to continue.", file=sys.stderr)
        return 3

    cases = [{"template": r["template"], "score": r["score"]} for r in rows]
    agg = aggregate(cases)
    meta |= {
        "mock": args.mock,
        "dataset": str(args.data.relative_to(ROOT)) if args.data.is_relative_to(ROOT) else str(args.data),
        "seed": manifest["seed"],
        "n_total": manifest["n"],
    }
    md = report.render(meta, agg, rows, ingest_summary)
    if args.limit:
        print("\n" + md)
        print(f"(--limit given: report not written; raw output in {raw_path})")
    else:
        out = report_path
        out.write_text(md, encoding="utf-8", newline="\n")
        print(f"\nwrote {out}")

    overall = agg["overall"]
    met = all(
        [
            overall["header"]["total"] >= report.TARGETS["total"],
            overall["header"]["invoice_number"] >= report.TARGETS["invoice_number"],
            overall["header"]["po_number"] >= report.TARGETS["po_number"],
            overall["line_recall"] >= report.TARGETS["line_items"],
        ]
    )
    ingestion_ok = ingest_summary is None or ingest_summary["all_ok"]
    print("gate targets:", "MET" if met else "NOT MET")
    if ingest_summary is not None:
        print("ingestion behaviour:", "all correct" if ingestion_ok else f"{len(ingest_summary['wrong'])} wrong")
    return 0 if met and ingestion_ok else 1


if __name__ == "__main__":
    sys.exit(main())
