"""Upload an invoice PDF to the n8n ingestion webhook, the same call a demo or a customer makes."""

import http.client
import json
import time
import urllib.error
import urllib.request
import uuid

from .extract import TransportError


def multipart(field: str, filename: str, content: bytes, content_type: str = "application/pdf") -> tuple[bytes, str]:
    boundary = f"----hotelops{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return head + content + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def upload_invoice(
    url: str,
    token: str,
    filename: str,
    content: bytes,
    timeout: float = 900,
    backoff: tuple[float, ...] = (5, 20, 60),
) -> tuple[int, dict]:
    """POST the PDF. Returns (http_status, json_body). 4xx/5xx are returned, not raised: a workflow
    error is a finding about the pipeline. Only an unreachable server raises TransportError."""
    body, content_type = multipart("file", filename, content)
    last: Exception | None = None
    for wait in (*backoff, None):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": content_type, "X-Ingest-Token": token}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"message": raw[:300].decode("utf-8", "replace")}
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
        if wait is None:
            raise TransportError(f"n8n webhook unreachable after {len(backoff) + 1} attempts: {last!r}") from last
        time.sleep(wait)
    raise AssertionError("unreachable")
