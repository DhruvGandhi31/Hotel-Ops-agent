"""A tiny HTTP wrapper around pdf_text.extract_text, for the n8n workflow.

    POST /extract   body: the raw PDF bytes   ->  200 {"text": "...", "pages": N}
    GET  /healthz                              ->  200 ok

A PDF that cannot be read still answers 200 with empty text and an "error" field: to the workflow
that is "no extractable text", the same as a scan, and ends in needs_review. Only the service
itself being down is an infrastructure failure. Not published to the host; only n8n reaches it.
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pdf_text import extract_text

MAX_BYTES = 15 * 1024 * 1024  # the workflow rejects uploads over 10 MB before calling this
DRAIN_LIMIT = 64 * 1024 * 1024  # never read more than this from a refused request


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _drain(self, length: int) -> None:
        """Read and discard a refused request's body. Replying while the client is still sending
        can reset its connection, and it would then see a network error instead of our status."""
        remaining = min(length, DRAIN_LIMIT)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        if length > DRAIN_LIMIT:
            self.close_connection = True

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b'{"error": "not found"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.path != "/extract":
            self._drain(length)
            return self._send(404, b'{"error": "not found"}')
        if length > MAX_BYTES:
            self._drain(length)
            return self._send(413, b'{"error": "file too large"}')
        content = self.rfile.read(length)
        try:
            text, pages = extract_text(content)
            reply = {"text": text, "pages": pages}
        except Exception as exc:  # corrupt, encrypted, truncated: report it, do not crash
            logging.warning("unreadable PDF (%d bytes): %s", len(content), exc)
            reply = {"text": "", "pages": 0, "error": f"{type(exc).__name__}: {exc}"[:300]}
        self._send(200, json.dumps(reply).encode())

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)


def serve(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(os.environ.get("PORT", "8000"))
    logging.info("pdf-text listening on :%d", port)
    serve("0.0.0.0", port).serve_forever()
