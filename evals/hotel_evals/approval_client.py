"""A scripted client for the n8n Approval Form, for tests and for the demo.

The form signs people in with their n8n account (Form Trigger `n8nUserAuth`), so a script has to do what a
browser does: log in, follow the OAuth-style redirect to the form (accepting the one-time consent step), read the
short-lived token out of the page, and send it back as `x-auth-token` with each submission. Pages after the first
are fetched from the `formWaitingUrl` each submission returns.

The address matters: n8n builds its sign-in redirects from `N8N_WEBHOOK_URL`, not from the address a client used,
and cookies belong to one host name. A client must therefore reach n8n at the very address `N8N_WEBHOOK_URL`
names (`localhost` and `127.0.0.1` are different hosts), or sign-in loops back to the wrong place.

Workarounds, each found by experiment (docs/p4-engineering-log.md, section 2):
  * n8n marks its cookies Secure; Python's cookie jar will not send those over plain http, browsers and curl do
    for localhost. The flag is cleared after every response (local test instances only).
  * `/rest/login` is rate limited (HTTP 429 after a handful of attempts): log in once and reuse the session.
  * The first request for the next page after a submission can hang while a second one answers at once. Not
    understood; requests for waiting pages use a short timeout and are retried.
  * A completion page ends the run by POSTing an empty body (with the page's token) back to its own URL as soon as
    a browser loads it; without that the execution stays `waiting`. `finish()` does it, and the helpers below call
    it, so a scripted session leaves nothing behind. A page that was only read is closed the way a person closes
    it without choosing: submitting it with no decision, which the workflow treats as abandoned.
"""

import http.client
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
import uuid

# The Approval Form's webhookId, pinned in workflows/approval_form.json: the form lives at /form/<this>.
APPROVAL_FORM_ID = "6f0a3c1e-7a52-4b6e-9d0f-2f4c1d5a8b01"


class FormError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url: str, timeout: float = 15) -> tuple[int, str]:
    """GET without following redirects and without any session: (status, Location header or ''). Used to ask
    what a stranger is told, e.g. that the form sends them to n8n's sign-in."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={"Accept": "text/html,*/*"}), timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as exc:
        raise FormError(f"{url}: {exc!r}") from exc


class FormClient:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _request(self, url, data=None, headers=None, method=None, timeout=60):
        req = urllib.request.Request(
            url, data=data, headers={"Accept": "text/html,*/*", **(headers or {})}, method=method
        )
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                result = resp.status, resp.read().decode("utf-8", "replace"), resp.geturl()
        except urllib.error.HTTPError as exc:
            result = exc.code, exc.read().decode("utf-8", "replace"), url
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError, OSError) as exc:
            raise FormError(f"{url}: {exc!r}") from exc
        for cookie in self.jar:
            cookie.secure = False
        return result

    def login(self, email: str, password: str, attempts: int = 6) -> None:
        body = json.dumps({"emailOrLdapLoginId": email, "password": password}).encode()
        for attempt in range(attempts):
            code, text, _ = self._request(f"{self.base}/rest/login", body, {"Content-Type": "application/json"})
            if code == 200:
                return
            if code == 429 and attempt + 1 < attempts:  # rate limited: wait and try again
                time.sleep(15)
                continue
            raise FormError(f"login failed (HTTP {code}): {text[:200]}")

    def open_form(self, url: str) -> tuple[str, str]:
        """GET a form URL as the logged-in user, completing the consent step if asked. Returns (html, url)."""
        code, html, final = self._request(url)
        if "/oauth/consent" in final:
            code, text, _ = self._request(f"{self.base}/rest/consent/details")
            data = json.loads(text).get("data", {})
            redirect = data.get("redirectUrl") if data.get("autoApproved") else None
            if not redirect:
                code, text, _ = self._request(
                    f"{self.base}/rest/consent/approve",
                    json.dumps({"approved": True}).encode(),
                    {"Content-Type": "application/json"},
                )
                redirect = json.loads(text)["data"]["redirectUrl"]
            code, html, final = self._request(redirect)
        if code != 200:
            raise FormError(f"form not available (HTTP {code}) at {final}")
        return html, final

    @staticmethod
    def auth_token(html: str) -> str | None:
        m = re.search(r"authToken = '([^']*)'", html)
        return m.group(1) if m else None

    def submit(self, url: str, html: str, fields: dict[str, str]) -> dict:
        """POST fields (multipart, as the page does) to the page `html` came from; returns the JSON reply."""
        boundary = "----hoa" + uuid.uuid4().hex
        body = "".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()
        )
        body = (body + f"--{boundary}--\r\n").encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        token = self.auth_token(html)
        if token:
            headers["x-auth-token"] = token
        code, text, _ = self._request(url, body, headers, "POST")
        try:
            reply = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FormError(f"unexpected reply to a form submission (HTTP {code}): {text[:200]}") from exc
        if code != 200:
            raise FormError(f"form submission refused (HTTP {code}): {text[:200]}")
        return reply

    def waiting_page(self, url: str, attempts: int = 6, timeout: float = 3) -> tuple[str, str]:
        """Fetch the next page of a running form (see the module note on the first-request hang).
        Returns (html, url): a submission from this page goes back to the same URL."""
        last: Exception | None = None
        for _ in range(attempts):
            try:
                code, html, _ = self._request(url, timeout=timeout)
            except FormError as exc:
                last = exc
                continue
            if code == 200:
                return html, url
            last = FormError(f"HTTP {code}")
        raise FormError(f"the next page never arrived: {last}")


def page_text(html: str) -> str:
    """The visible text of a form page: scripts, styles and tags removed, entities decoded, whitespace squeezed."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    for entity, char in (
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&#x3D;", "="),
    ):
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


class ApprovalClient(FormClient):
    """The Approval Form: the pending list, and deciding an approval."""

    def __init__(self, base: str, email: str, password: str, form_id: str = APPROVAL_FORM_ID):
        super().__init__(base)
        self.form_id = form_id
        self.login(email, password)

    def form_url(self, approval_id: int | str | None = None) -> str:
        url = f"{self.base}/form/{self.form_id}"
        return url if approval_id is None else f"{url}?approval_id={approval_id}"

    @staticmethod
    def is_completion(html: str) -> bool:
        """A completion page is the one whose script POSTs an empty body to finish the run."""
        return "body: {}" in html and "x-auth-token" in html

    def finish(self, html: str, url: str) -> None:
        """What a browser does on loading a completion page: POST an empty body to it with the page's token."""
        token = self.auth_token(html)
        headers = {"x-auth-token": token} if token else {}
        self._request(url, b"{}", {"Content-Type": "application/json", **headers}, "POST")

    def _next_page(self, reply: dict) -> tuple[str, str]:
        """The page a submission leads to; a completion page is finished like a browser would."""
        html, url = self.waiting_page(reply["formWaitingUrl"])
        if self.is_completion(html):
            self.finish(html, url)
        return html, url

    def _first_page(self, approval_id=None) -> tuple[str, str]:
        """Open the form and press Continue: returns (html, url) of the page the workflow shows next."""
        html, url = self.open_form(self.form_url(approval_id))
        return self._next_page(self.submit(url, html, {"field-0": "" if approval_id is None else str(approval_id)}))

    def _close(self, html: str, url: str, fields: dict[str, str]) -> None:
        """End a page that was only read: submit it with nothing chosen. Completion pages are already finished."""
        if not self.is_completion(html):
            self._next_page(self.submit(url, html, fields))

    def pending_page(self) -> str:
        """The HTML of the list of pending approvals (the page is then closed)."""
        html, url = self._first_page()
        self._close(html, url, {})
        return html

    def decision_page(self, approval_id) -> str:
        """The HTML of the page for one approval: a decision form, or a note that it is closed. A decision form
        is closed without a decision, so reading a page records nothing and leaves nothing waiting."""
        html, url = self._first_page(approval_id)
        self._close(html, url, self._blank_answers(html))
        return html

    @staticmethod
    def _blank_answers(html: str) -> dict[str, str]:
        fields = {}
        for tag in ("select", "textarea"):
            m = re.search(rf"<{tag}[^>]*name=['\"](field-\d+)['\"]", html)
            if m:
                fields[m.group(1)] = ""
        return fields

    def decide(self, approval_id, decision: str, comment: str = "") -> str:
        """Approve or reject (`decision` is 'Approve' or 'Reject'). Returns the visible text of the page that
        follows, which says what was recorded. If the approval is closed there is no decision form, and the
        text of that page (why it is closed) is returned instead."""
        page, url = self._first_page(approval_id)
        select = re.search(r"<select[^>]*name=['\"](field-\d+)['\"]", page)
        if not select:
            return page_text(page)
        fields = {select.group(1): decision}
        area = re.search(r"<textarea[^>]*name=['\"](field-\d+)['\"]", page)
        if area:
            fields[area.group(1)] = comment
        return page_text(self._next_page(self.submit(url, page, fields))[0])
