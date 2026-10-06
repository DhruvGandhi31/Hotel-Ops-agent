"""Invoice extraction against a local Ollama model, mirroring the n8n workflow's contract.

Same prompt file, same schema, same rule as the workflow (principle 2): validate every
output; on failure retry once with the validation errors appended; if it fails again the
invoice goes to needs_review and nothing unvalidated is passed on.
"""

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "invoice_extraction.json"
PROMPT_PATH = ROOT / "prompts" / "invoice_extraction.md"
RETRY_PROMPT_PATH = ROOT / "prompts" / "invoice_extraction_retry.md"

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def anchor_patterns(node):
    """Make every `pattern` end-anchored the way ECMA-262 (and the workflow) reads it.

    In Python's `re`, `$` also matches just before a trailing newline, so "1430.74\n" would pass
    ^[0-9]+\\.[0-9]{2}$ here while the workflow rejects it. \\Z means end of string only.
    """
    if isinstance(node, dict):
        return {
            k: (v[:-1] + "\\Z" if k == "pattern" and isinstance(v, str) and v.endswith("$") else anchor_patterns(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [anchor_patterns(v) for v in node]
    return node


def load_validator(schema: dict | None = None) -> Draft202012Validator:
    return Draft202012Validator(anchor_patterns(schema or load_schema()))


ANNOTATION_KEYS = ("description", "$schema", "$id", "title")


def strip_descriptions(node):
    """Drop schema annotations (prose) but keep structure; the rules already say it in the prompt.

    Only string-valued annotation keys go. A property that is itself *named* "description" has a
    dict value and must stay (an earlier version dropped it, so the line-item `description`
    field was missing from the schema the model saw).
    """
    if isinstance(node, dict):
        return {k: strip_descriptions(v) for k, v in node.items() if not (k in ANNOTATION_KEYS and isinstance(v, str))}
    if isinstance(node, list):
        return [strip_descriptions(v) for v in node]
    return node


def build_system_prompt(schema: dict | None = None) -> str:
    schema = schema or load_schema()
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert "{{SCHEMA}}" in text, "prompt file lost its {{SCHEMA}} placeholder"
    return text.replace("{{SCHEMA}}", json.dumps(strip_descriptions(schema), separators=(",", ":")))


def first_user_message(invoice_text: str) -> str:
    return "Invoice text:\n\n" + invoice_text


def retry_user_message(invoice_text: str, previous_output: str, errors: list[str]) -> str:
    """The one retry (CLAUDE.md: the validation error is appended to the prompt).

    A single user message rather than extra chat turns, so the n8n workflow, whose chain node
    takes one prompt, sends exactly what the eval measured.
    """
    text = RETRY_PROMPT_PATH.read_text(encoding="utf-8")
    for key, value in (
        ("{{INVOICE_TEXT}}", invoice_text),
        ("{{PREVIOUS_OUTPUT}}", previous_output),
        ("{{ERRORS}}", "\n".join(f"- {e}" for e in errors)),
    ):
        text = text.replace(key, value)
    return text


def abn_checksum_ok(abn: str) -> bool:
    digits = re.sub(r"\s+", "", abn)
    if len(digits) != 11 or not digits.isdigit():
        return False
    nums = [int(c) for c in digits]
    nums[0] -= 1
    return sum(w * d for w, d in zip(_ABN_WEIGHTS, nums, strict=True)) % 89 == 0


def validation_errors(output, validator: Draft202012Validator) -> list[str]:
    """Schema errors plus the checks a schema can't express. Empty list means valid."""
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(output), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors[:12]
    for key in ("invoice_date", "due_date"):
        value = output.get(key)
        if value is not None:
            try:
                real = date.fromisoformat(value).year >= 1900
            except ValueError:
                real = False
            if not real:
                errors.append(f"{key}: {value!r} is not a real calendar date")
    for i, line in enumerate(output["lines"], start=1):
        if round(line["quantity"], 3) != line["quantity"]:
            errors.append(f"lines/{i}/quantity: {line['quantity']} has more than 3 decimal places")
    if not abn_checksum_ok(output["supplier_abn"]):
        errors.append(f"supplier_abn: {output['supplier_abn']!r} fails the ABN checksum; re-read it from the document")
    return errors


@dataclass
class Extraction:
    status: str  # 'extracted' | 'needs_review'
    output: dict | None
    attempts: int
    errors: list[str] = field(default_factory=list)
    raw: str = ""
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempt_errors: list[list[str]] = field(default_factory=list)
    max_context_used: int = 0  # prompt + completion tokens of the largest attempt


ChatFn = Callable[[list[dict]], dict]


class TransportError(RuntimeError):
    """The model server was unreachable or kept failing. An infrastructure fault, not a
    wrong answer: the run stops instead of scoring it as an extraction failure."""


FORMAT_MODES = ("schema", "json", "none")


def ollama_chat_fn(
    model: str,
    host: str,
    schema: dict | None,
    num_ctx: int = 4096,
    timeout: float = 600,
    format_mode: str = "schema",
    backoff: tuple[float, ...] = (5, 20, 60),
) -> ChatFn:
    """Returns chat(messages) -> {'content', 'prompt_tokens', 'completion_tokens'}.

    format_mode: 'schema' constrains decoding to the JSON schema; 'json' is Ollama's generic
    JSON mode (all that n8n's Ollama chat model node can request); 'none' is prompt only.
    Validation runs in every mode.
    """
    if format_mode not in FORMAT_MODES:
        raise ValueError(f"format_mode must be one of {FORMAT_MODES}")

    def chat(messages: list[dict]) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,  # reasoning models: thinking only adds latency on a transcription task
            "options": {"temperature": 0, "seed": 42, "num_ctx": num_ctx, "num_predict": 4096},
        }
        if format_mode == "schema":
            body["format"] = schema
        elif format_mode == "json":
            body["format"] = "json"
        req = urllib.request.Request(
            f"{host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        last: Exception | None = None
        for wait in (*backoff, None):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                if exc.code < 500:  # a 4xx is our bug (bad model name, bad request); retrying won't help
                    raise TransportError(f"Ollama rejected the request: HTTP {exc.code} {exc.read()[:300]!r}") from exc
                last = exc
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as exc:
                last = exc
            if wait is None:
                raise TransportError(f"Ollama unreachable after {len(backoff) + 1} attempts: {last!r}") from last
            time.sleep(wait)
        return {
            "content": data["message"]["content"],
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }

    return chat


class Extractor:
    def __init__(self, chat: ChatFn, schema: dict | None = None):
        self.schema = schema or load_schema()
        self.validator = load_validator(self.schema)
        self.system = build_system_prompt(self.schema)
        self.chat = chat

    def extract(self, invoice_text: str) -> Extraction:
        user = first_user_message(invoice_text)
        result = Extraction(status="needs_review", output=None, attempts=0)
        started = time.perf_counter()
        for attempt in (1, 2):
            reply = self.chat([{"role": "system", "content": self.system}, {"role": "user", "content": user}])
            result.attempts = attempt
            result.raw = reply["content"]
            result.prompt_tokens += reply["prompt_tokens"]
            result.completion_tokens += reply["completion_tokens"]
            result.max_context_used = max(result.max_context_used, reply["prompt_tokens"] + reply["completion_tokens"])
            errors, parsed = self._check(reply["content"])
            result.attempt_errors.append(errors)
            if not errors:
                result.status, result.output, result.errors = "extracted", parsed, []
                break
            result.errors = errors
            user = retry_user_message(invoice_text, reply["content"], errors)
        result.latency_s = time.perf_counter() - started
        return result

    def _check(self, content: str) -> tuple[list[str], dict | None]:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return [f"output is not valid JSON ({exc.msg} at char {exc.pos})"], None
        errors = validation_errors(parsed, self.validator)
        return errors, None if errors else parsed
