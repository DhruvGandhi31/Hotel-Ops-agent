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
"""

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from hotel_datagen.abn import format_abn

from .oracle import truth_to_extraction

MODES = ("oracle", "garbage_once", "garbage_always", "server_error")
RETRY_MARKER = "Your previous answer was:"
GARBAGE = '{"supplier_name": "Not an invoice", "total": 12'


class MockState:
    def __init__(self, data_dir: Path):
        self.docs = [
            json.loads(p.read_text(encoding="utf-8"))["document"]
            for p in sorted((data_dir / "ground_truth").glob("*.json"))
        ]
        self.mode = "oracle"
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "retries": 0, "unmatched": 0, "formats": {}}

    def find(self, text: str) -> dict | None:
        squashed = re.sub(r"\s+", " ", text)
        for doc in self.docs:
            if doc["invoice_number"] in squashed and format_abn(doc["supplier_abn"]) in squashed:
                return doc
        return None

    def reply(self, user_text: str, fmt) -> str:
        with self.lock:
            self.stats["requests"] += 1
            key = json.dumps(fmt) if not isinstance(fmt, str) else fmt
            self.stats["formats"][key] = self.stats["formats"].get(key, 0) + 1
            is_retry = RETRY_MARKER in user_text
            self.stats["retries"] += is_retry
            mode = self.mode
        if mode == "garbage_always" or (mode == "garbage_once" and not is_retry):
            return GARBAGE
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
            if state.mode == "server_error":
                with state.lock:
                    state.stats["requests"] += 1
                return self._send(500, b'{"error": "mock server error"}')
            content = state.reply(user, body.get("format"))
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
