"""A stand-in for Ollama's /api/chat that answers from ground truth.

Lets CI (no GPU) and local runs exercise the whole n8n pipeline: webhook, PDF text, prompt,
validation, retry, database writes, response. It finds the invoice in the prompt by its invoice
number and supplier ABN and replies with the perfect extraction, so any difference downstream is
the pipeline's doing, not a model's. It says nothing about model accuracy.

    python -m hotel_evals.mock_ollama --data data-gen/out --port 11500

Failure modes, switched at run time with POST /__mode {"mode": "..."} (GET /__stats to inspect):
    oracle          valid extraction every time (default)
    garbage_once    invalid JSON for the first attempt, valid on the retry
    garbage_always  invalid every time (the invoice must end up needs_review)
    server_error    HTTP 500 for every request (an outage: nothing may be recorded)
    matcher_unsure  answers line-match prompts with confidence 0.5 (below the 0.85 threshold)
    matcher_none    answers line-match prompts with "not on the PO" (po_line_no null)
    matcher_wrong   answers line-match prompts with a PO line that is not the right one
    matcher_garbage_always  invalid for line-match prompts only (extraction still works)
    matcher_server_error  HTTP 500 for line-match prompts only (extraction still works)

It answers two kinds of prompt: invoice extraction (the perfect extraction) and line matching (the
true PO line for every line it is asked about, from the ground-truth labels).
"""

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hotel_datagen.abn import format_abn

from .oracle import truth_to_extraction

MODES = (
    "oracle",
    "garbage_once",
    "garbage_always",
    "server_error",
    "matcher_unsure",
    "matcher_none",
    "matcher_wrong",
    "matcher_server_error",
    "matcher_garbage_always",
)
MATCHER_MARKER = "You match lines on a supplier invoice"
RETRY_MARKER = "Your previous answer was:"
GARBAGE = '{"supplier_name": "Not an invoice", "total": 12'


class MockState:
    def __init__(self, data_dir: Path):
        truths = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((data_dir / "ground_truth").glob("*.json"))]
        self.docs = [t["document"] for t in truths]
        self.labels = [t["labels"] for t in truths]
        self.mode = "oracle"
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "retries": 0, "unmatched": 0, "formats": {}}

    def find(self, text: str) -> dict | None:
        squashed = re.sub(r"\s+", " ", text)
        for doc in self.docs:
            if doc["invoice_number"] in squashed and format_abn(doc["supplier_abn"]) in squashed:
                return doc
        return None

    def match_reply(self, user_text: str, mode: str) -> str:
        """Answer a line-matching prompt from the ground-truth labels."""
        number = re.match(r"Invoice (.+)", user_text).group(1).strip()
        asked = [(int(n), d) for n, d in re.findall(r'^- line (\d+): "(.*?)", quantity', user_text, re.M)]
        offered = [int(n) for n in re.findall(r"^- PO line (\d+):", user_text, re.M)]
        labels = None
        for doc, lab in zip(self.docs, self.labels, strict=True):
            lines = {ln["line_no"]: ln["description"] for ln in doc["lines"]}
            if doc["invoice_number"] == number and all(lines.get(n) == d for n, d in asked):
                labels = lab
                break
        if labels is None:
            with self.lock:
                self.stats["unmatched"] += 1
            return GARBAGE
        truth = {ln["line_no"]: ln["po_line_no"] for ln in labels["lines"]}
        matches = []
        for n, _ in asked:
            po = truth.get(n)
            if mode == "matcher_none":
                matches.append({"invoice_line_no": n, "po_line_no": None, "confidence": 0.9})
            elif mode == "matcher_wrong":
                wrong = next((c for c in offered if c != po), None)
                matches.append({"invoice_line_no": n, "po_line_no": wrong, "confidence": 0.95})
            else:
                matches.append(
                    {"invoice_line_no": n, "po_line_no": po, "confidence": 0.5 if mode == "matcher_unsure" else 0.95}
                )
        return json.dumps({"matches": matches})

    def reply(self, user_text: str, fmt, system_text: str = "") -> str:
        with self.lock:
            self.stats["requests"] += 1
            key = json.dumps(fmt) if not isinstance(fmt, str) else fmt
            self.stats["formats"][key] = self.stats["formats"].get(key, 0) + 1
            is_retry = RETRY_MARKER in user_text
            self.stats["retries"] += is_retry
            mode = self.mode
        if (
            mode == "garbage_always"
            or (mode == "garbage_once" and not is_retry)
            or (mode == "matcher_garbage_always" and MATCHER_MARKER in system_text)
        ):
            return GARBAGE
        if MATCHER_MARKER in system_text:
            with self.lock:
                self.stats["matcher_requests"] = self.stats.get("matcher_requests", 0) + 1
            return self.match_reply(user_text, mode)
        doc = self.find(user_text)
        if doc is None:
            with self.lock:
                self.stats["unmatched"] += 1
            return GARBAGE
        return json.dumps(truth_to_extraction(doc))


def make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path == "/__stats":
                self._send(200, json.dumps({**state.stats, "mode": state.mode}).encode())
            elif self.path in ("/", "/api/version"):
                self._send(200, b'{"version": "mock"}')
            elif self.path == "/api/tags":
                self._send(200, b'{"models": [{"name": "qwen3.5:9b", "model": "qwen3.5:9b"}]}')
            else:
                self._send(404, b"{}")

        def do_POST(self):
            body = self._json()
            if self.path == "/__mode":
                if body.get("mode") not in MODES:
                    return self._send(400, json.dumps({"error": f"mode must be one of {MODES}"}).encode())
                state.mode = body["mode"]
                state.stats.update(requests=0, retries=0, unmatched=0, formats={})
                return self._send(200, json.dumps({"mode": state.mode}).encode())
            if self.path != "/api/chat":
                return self._send(404, b"{}")

            user = next((m["content"] for m in reversed(body.get("messages", [])) if m["role"] == "user"), "")
            matching = MATCHER_MARKER in next(
                (m["content"] for m in body.get("messages", []) if m["role"] == "system"), ""
            )
            if state.mode == "server_error" or (state.mode == "matcher_server_error" and matching):
                with state.lock:
                    state.stats["requests"] += 1
                return self._send(500, b'{"error": "mock server error"}')
            system = next((m["content"] for m in body.get("messages", []) if m["role"] == "system"), "")
            content = state.reply(user, body.get("format"), system)
            model = body.get("model", "mock")
            done = {
                "model": model,
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": len(user) // 4,
                "eval_count": len(content) // 4,
            }
            if body.get("stream") is False:
                return self._send(
                    200, json.dumps({**done, "message": {"role": "assistant", "content": content}}).encode()
                )
            chunk = {**done, "done": False, "message": {"role": "assistant", "content": content}}
            del chunk["done_reason"]
            payload = (json.dumps(chunk) + "\n" + json.dumps(done) + "\n").encode()
            self._send(200, payload, "application/x-ndjson")

        def log_message(self, *args):
            pass

    return Handler


def serve(data_dir: Path, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(MockState(data_dir)))
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("data-gen/out"))
    ap.add_argument("--host", default="0.0.0.0")  # reachable from the n8n container via host.docker.internal
    ap.add_argument("--port", type=int, default=11500)
    args = ap.parse_args()
    server = serve(args.data, args.host, args.port)
    print(f"mock ollama on {args.host}:{args.port}, data {args.data}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
