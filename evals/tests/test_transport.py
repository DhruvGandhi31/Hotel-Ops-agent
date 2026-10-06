"""The model server failing must stop a run loudly, never be scored as 'extraction failed'."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hotel_evals.extract import TransportError, ollama_chat_fn, retry_user_message

MESSAGES = [{"role": "user", "content": "x"}]


class _Server:
    def __init__(self, responder):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                # Drain the request first: replying while the client is still sending can reset the
                # connection, which a retrying client then (correctly) retries and the counts drift.
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append(1)
                status, body = responder(len(calls))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.calls = calls
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.chat = ollama_chat_fn(
            "m", f"http://127.0.0.1:{self.server.server_port}", None, backoff=(0, 0), format_mode="none"
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()


OK_BODY = json.dumps({"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2}).encode()


def test_recovers_after_transient_server_errors():
    with _Server(lambda n: (500, b"") if n < 3 else (200, OK_BODY)) as s:
        reply = s.chat(MESSAGES)
    assert reply == {"content": "{}", "prompt_tokens": 3, "completion_tokens": 2}
    assert len(s.calls) == 3


def test_gives_up_with_transport_error():
    with _Server(lambda n: (500, b"boom")) as s, pytest.raises(TransportError, match="after 3 attempts"):
        s.chat(MESSAGES)
    assert len(s.calls) == 3


def test_client_error_is_not_retried():
    with _Server(lambda n: (404, b"model not found")) as s, pytest.raises(TransportError, match="404"):
        s.chat(MESSAGES)
    assert len(s.calls) == 1


def test_unreachable_server_is_a_transport_error():
    chat = ollama_chat_fn("m", "http://127.0.0.1:1", None, backoff=(0,), format_mode="none")
    with pytest.raises(TransportError, match="unreachable"):
        chat(MESSAGES)


def test_retry_prompt_has_no_unfilled_placeholders():
    text = retry_user_message("THE TEXT", "PREV", ["total: bad", "gst: bad"])
    assert "{{" not in text
    assert "THE TEXT" in text and "PREV" in text and "- total: bad\n- gst: bad" in text


def test_each_format_mode_sends_the_matching_format_to_ollama():
    """schema -> the JSON schema itself; json -> the string "json"; none -> no format at all."""
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(OK_BODY)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for mode in ("schema", "json", "none"):
            ollama_chat_fn("m", f"http://127.0.0.1:{server.server_port}", schema, format_mode=mode)(MESSAGES)
    finally:
        server.shutdown()
    assert seen[0]["format"] == schema
    assert seen[1]["format"] == "json"
    assert "format" not in seen[2]
    assert all(body["options"]["temperature"] == 0 and body["think"] is False for body in seen)
