# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""One bounded, read-only HTTP probe and the redaction of what it recorded.

The probe never follows a redirect (a 302 to another host would leave the
authorized scope), reads at most ``max_body`` bytes, and has a timeout. What
it returns is already redacted: tokens, cookies and password-like JSON values
never reach a finding.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from navin.security.verification.policy import Authorization, check_request

_SECRET_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "proxy-authorization", "x-api-key"})
_PATTERNS = (
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ****"),
    (re.compile(r'(?i)("(?:password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token)"\s*:\s*")[^"]*(")'), r"\1****\2"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)=([^&\s]+)"), r"\1=****"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "****.jwt"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "****aws"),
)


def redact(text: str) -> str:
    """Mask credentials in a recorded request or response."""
    out = str(text or "")
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class ProbeResult:
    method: str
    url: str
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    truncated: bool = False
    elapsed_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.status is not None

    def request_line(self, header_names: tuple[str, ...] = ()) -> str:
        """What was sent, with header names only: values are never recorded."""
        names = f" [headers: {', '.join(sorted(header_names))}]" if header_names else ""
        return redact(f"{self.method} {self.url}{names}")

    def observed(self) -> str:
        if not self.ok:
            return f"no response: {self.error}"
        shown = {k: v for k, v in self.headers.items() if k.lower() not in _SECRET_HEADERS}
        head = ", ".join(f"{k}: {v}" for k, v in list(shown.items())[:12])
        return redact(f"HTTP {self.status}\n{head}\n\n{self.body}")[:4000]


def send_probe(
    method: str,
    url: str,
    auth: Authorization,
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
    max_body: int = 65536,
    reproduction: bool = False,
) -> ProbeResult:
    """Send one read-only request to an authorized target. Blocking: call it off the loop."""
    verb = check_request(method, url, auth, reproduction=reproduction)
    request = urllib.request.Request(url, method=verb, headers={"User-Agent": "navin-security-verify", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect)
    started = time.monotonic()
    result = ProbeResult(method=verb, url=url)
    response = None
    try:
        try:
            response = opener.open(request, timeout=timeout_s)
        except urllib.error.HTTPError as err:  # 3xx (not followed), 4xx and 5xx still answer
            response = err
        result.status = response.getcode()
        result.headers = {k: v for k, v in response.headers.items()}
        raw = response.read(max_body + 1) if verb != "HEAD" else b""
        result.truncated = len(raw) > max_body
        result.body = raw[:max_body].decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result.error = str(getattr(exc, "reason", exc))[:300]
    finally:
        if response is not None:
            response.close()
        result.elapsed_s = round(time.monotonic() - started, 3)
    return result


async def send_probe_async(*args, **kwargs) -> ProbeResult:
    """Same as :func:`send_probe`, off the event loop."""
    return await asyncio.to_thread(send_probe, *args, **kwargs)
