# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Passive dynamic baseline of a web application or API.

Strictly non-destructive: only GET, HEAD and OPTIONS, never a body, never a
request outside the base origin (except the http:// variant of an https host
on the default port, to test the HTTPS redirect), redirects never followed,
a hard request budget, bounded body reads and redacted evidence. Every request
goes through :func:`send_probe` and therefore through the authorization policy.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit

from loguru import logger

from navin.security.model import Evidence, Finding
from navin.security.verification.policy import Authorization, VerificationRefusedError
from navin.security.verification.probe import ProbeResult, redact, send_probe

ENGINE_ID = "navin_web_baseline"

# check id -> (family, severity, cwe, controls, title, remediation)
_CHECKS: dict[str, tuple[str, str, str, list[str], str, str]] = {
    "web-hsts-missing": ("cryptography", "medium", "CWE-319", ["C4.09"], "Strict-Transport-Security header missing",
                         "Send Strict-Transport-Security: max-age=31536000; includeSubDomains on every HTTPS response."),
    "web-https-redirect-missing": ("cryptography", "medium", "CWE-319", ["C4.09"], "Plain HTTP is not redirected to HTTPS",
                                   "Answer every http:// request with a 301/308 redirect to the https:// URL."),
    "web-csp-missing": ("frontend", "medium", "CWE-693", ["C3.04"], "Content-Security-Policy header missing",
                        "Define a restrictive Content-Security-Policy (default-src 'self', no inline script)."),
    "web-csp-unsafe": ("frontend", "medium", "CWE-693", ["C3.04"], "Content-Security-Policy allows unsafe sources",
                       "Remove 'unsafe-inline', 'unsafe-eval' and * from script-src/default-src; use nonces or hashes."),
    "web-xcto-missing": ("frontend", "low", "CWE-693", ["C3.04"], "X-Content-Type-Options: nosniff missing",
                         "Send X-Content-Type-Options: nosniff on every response."),
    "web-framing-missing": ("frontend", "medium", "CWE-1021", ["C3.04"], "No clickjacking protection",
                            "Send Content-Security-Policy frame-ancestors 'self' (or X-Frame-Options: DENY)."),
    "web-cookie-flags": ("authentication", "medium", "CWE-614", ["C2.04"], "Cookie set without protective flags",
                         "Set Secure, HttpOnly (session cookies) and SameSite=Lax or Strict on every cookie."),
    "web-cors-reflect": ("frontend", "high", "CWE-942", ["C3.04"], "CORS reflects an arbitrary Origin",
                         "Validate the Origin against an explicit allow-list instead of echoing it back."),
    "web-cors-wildcard-credentials": ("frontend", "high", "CWE-942", ["C3.04"], "CORS wildcard combined with credentials",
                                      "Never combine Access-Control-Allow-Origin: * with Allow-Credentials: true."),
    "web-server-banner": ("infrastructure", "low", "CWE-200", ["C3.04"], "Server banner exposes a version",
                          "Strip version numbers from the Server and X-Powered-By headers."),
    "web-sourcemap-exposed": ("frontend", "low", "CWE-540", ["C3.04"], "JavaScript source map publicly served",
                              "Do not deploy .map files to production, or restrict them to internal networks."),
    "web-debug-exposed": ("infrastructure", "high", "CWE-215", ["C7.10"], "Debug or internal endpoint exposed",
                          "Remove or block debug endpoints, .env and .git from the public web root."),
    "web-error-leak": ("audit_logging", "medium", "CWE-209", ["C7.10"], "Error response leaks a stack trace",
                       "Disable debug mode in production and return a generic error page; log details server side."),
    "web-graphql-introspection": ("api", "medium", "CWE-200", ["C3.03"], "GraphQL introspection enabled",
                                  "Disable GraphQL introspection in production."),
    "web-openapi-exposed": ("api", "low", "CWE-200", ["C3.03"], "API description publicly served",
                            "Confirm the OpenAPI/Swagger document is meant to be public; otherwise require authentication."),
    "web-cache-private": ("frontend", "low", "CWE-525", ["C2.04"], "Sensitive response cacheable by shared caches",
                          "Send Cache-Control: no-store (or private) on responses that set cookies or serve authenticated pages."),
    "web-admin-exposed": ("authorization", "low", "CWE-284", ["C3.04"], "Admin page reachable without an auth challenge",
                          "Put the admin interface behind authentication and, ideally, a network restriction."),
    "web-security-txt-missing": ("infrastructure", "info", "", ["C3.04"], "security.txt missing",
                                 "Publish /.well-known/security.txt (RFC 9116) with a contact address."),
}
CHECK_IDS: tuple[str, ...] = tuple(_CHECKS)
_HEADER_CHECKS = frozenset({"web-hsts-missing", "web-csp-missing", "web-csp-unsafe", "web-xcto-missing",
                            "web-framing-missing", "web-cookie-flags", "web-cors-reflect",
                            "web-cors-wildcard-credentials", "web-server-banner", "web-cache-private"})

_PROBE_ORIGIN = "https://navin-probe.invalid"
_DEBUG_PATHS = ("/actuator", "/actuator/env", "/debug", "/server-status", "/phpinfo.php", "/.env", "/.git/HEAD")
_GRAPHQL_PATH = "/graphql?query={__schema{types{name}}}"
_MAX_BODY = 65536
_SESSION_NAME = re.compile(r"(?i)sess|sid|auth|token|jwt|login|remember")
_VERSION = re.compile(r"\d+\.\d+")
_SCRIPT_SRC = re.compile(r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_ENV_LINE = re.compile(r"(?m)^[A-Z][A-Z0-9_]{2,}=\S")
_ENV_VALUE = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_]{2,})=.*$")
_ERROR_MARKERS = (
    "Traceback (most recent call last)", "Werkzeug Debugger", "werkzeug.debug",
    "java.lang.", "at org.springframework", "Server Error in '/' Application", "[HttpException",
    "Whoops! There was an error", "Illuminate\\", "Symfony\\Component", "symfony-profiler",
)

Sender = Callable[..., ProbeResult]


@dataclass
class WebBaselineReport:
    engine: str = ENGINE_ID
    status: str = "ok"
    target: str = ""
    started_at: str = ""
    duration_s: float = 0.0
    requests: int = 0
    checks_run: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    observations: dict[str, dict[str, str]] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["findings"] = [f.to_dict() for f in self.findings]
        return out


def _lower(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


class _Runner:
    def __init__(self, origin: str, auth: Authorization, sender: Sender, timeout_s: float, max_requests: int):
        self.origin = origin
        self.auth = auth
        self.sender = sender
        self.timeout_s = timeout_s
        self.max_requests = max(1, int(max_requests))
        self.requests = 0
        self.findings: dict[str, Finding] = {}
        self.checks: list[str] = []
        self.observations: dict[str, dict[str, str]] = {}

    def probe(self, method: str, url: str, headers: dict[str, str] | None = None) -> ProbeResult | None:
        if self.requests >= self.max_requests:
            return None
        parts = urlsplit(url)
        same = f"{parts.scheme}://{parts.netloc}" == self.origin
        if not same and not self._is_http_variant(url):
            return None
        self.requests += 1
        try:
            result = self.sender(method, url, self.auth, headers=headers, timeout_s=self.timeout_s, max_body=_MAX_BODY)
        except VerificationRefusedError as exc:
            logger.info("web baseline: request refused by policy: {}", exc)
            return None
        if result.ok:
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            self.observations[f"{method} {path}"] = {
                k: redact(v) for k, v in result.headers.items() if k.lower() not in {"set-cookie", "cookie", "authorization"}
            }
        return result

    def _is_http_variant(self, url: str) -> bool:
        base = urlsplit(self.origin)
        parts = urlsplit(url)
        return base.scheme == "https" and parts.scheme == "http" and parts.hostname == base.hostname and parts.port in (None, 80)

    def ran(self, *ids: str) -> None:
        for cid in ids:
            if cid not in self.checks:
                self.checks.append(cid)

    def add(self, cid: str, result: ProbeResult, description: str, *, confidence: float = 0.8,
            headers: tuple[str, ...] = ()) -> None:
        if cid in self.findings:
            return
        family, severity, cwe, controls, title, remediation = _CHECKS[cid]
        path = urlsplit(result.url).path or "/"
        self.findings[cid] = Finding(
            id=cid, family=family, title=title, severity=severity, cwe=cwe, endpoint=path,
            description=description, remediation=remediation, confidence=confidence, source=ENGINE_ID,
            controls=list(controls), exposure="internet",
            evidence=[Evidence(kind="dynamic", description=redact(description), tool=ENGINE_ID,
                               request=result.request_line(headers),
                               observed=_ENV_VALUE.sub(r"\1=****", result.observed())[:1500],
                               location=path)],
        )


def _check_headers(run: _Runner, res: ProbeResult, https: bool) -> None:
    h = _lower(res.headers)
    run.ran("web-csp-missing", "web-csp-unsafe", "web-xcto-missing", "web-framing-missing", "web-server-banner")
    csp = h.get("content-security-policy", "")
    if https:
        run.ran("web-hsts-missing")
        if "strict-transport-security" not in h:
            run.add("web-hsts-missing", res, "HTTPS response without Strict-Transport-Security.")
    if not csp:
        run.add("web-csp-missing", res, "Response without Content-Security-Policy.")
    else:
        directives = {}
        for part in csp.split(";"):
            tokens = part.strip().split()
            if tokens:
                directives[tokens[0].lower()] = [t.lower() for t in tokens[1:]]
        script = directives.get("script-src", directives.get("default-src", []))
        bad = [t for t in script if t in {"'unsafe-inline'", "'unsafe-eval'", "*"}]
        if bad:
            run.add("web-csp-unsafe", res, f"CSP script sources allow {', '.join(bad)}.")
    if h.get("x-content-type-options", "").strip().lower() != "nosniff":
        run.add("web-xcto-missing", res, "Response without X-Content-Type-Options: nosniff.")
    if "x-frame-options" not in h and "frame-ancestors" not in csp.lower():
        run.add("web-framing-missing", res, "No X-Frame-Options and no CSP frame-ancestors.")
    for name in ("server", "x-powered-by"):
        if _VERSION.search(h.get(name, "")):
            run.add("web-server-banner", res, f"{name} header exposes a version: {h[name][:80]}")
            break


def _check_cookies(run: _Runner, res: ProbeResult, https: bool, auth_path: bool) -> None:
    h = _lower(res.headers)
    cookie = h.get("set-cookie", "")
    run.ran("web-cookie-flags", "web-cache-private")
    if cookie:
        problems = []
        for raw in re.split(r",\s*(?=[^;,=\s]+=)", cookie):
            name = raw.split("=", 1)[0].strip()
            attrs = raw.lower()
            if https and "secure" not in attrs:
                problems.append(f"{name}: no Secure")
            if _SESSION_NAME.search(name) and "httponly" not in attrs:
                problems.append(f"{name}: no HttpOnly")
            if "samesite" not in attrs:
                problems.append(f"{name}: no SameSite")
        if problems:
            run.add("web-cookie-flags", res, "Cookie flags missing: " + "; ".join(problems[:6]))
    if (cookie or auth_path) and res.status == 200:
        cc = h.get("cache-control", "").lower()
        if "no-store" not in cc and "private" not in cc:
            run.add("web-cache-private", res, "Response sets a cookie or is authenticated but is not Cache-Control private/no-store.",
                    confidence=0.6)


def _check_error(run: _Runner, res: ProbeResult) -> None:
    run.ran("web-error-leak")
    if res.status is not None and res.status >= 400:
        hit = next((m for m in _ERROR_MARKERS if m in res.body), "")
        if hit:
            run.add("web-error-leak", res, f"Error response contains a debug marker: {hit}")


def _check_debug(run: _Runner, path: str, res: ProbeResult) -> None:
    if res.status != 200 or not res.body:
        return
    body = res.body
    ctype = _lower(res.headers).get("content-type", "").lower()
    hit = ""
    if path.startswith("/actuator") and "json" in ctype and ("{" in body[:5]):
        hit = "Spring actuator JSON"
    elif path == "/.env" and _ENV_LINE.search(body) and "<html" not in body[:500].lower():
        hit = ".env file with KEY=VALUE lines"
    elif path == "/.git/HEAD" and body.lstrip().startswith("ref:"):
        hit = "Git repository metadata"
    elif path == "/phpinfo.php" and ("phpinfo()" in body or "PHP Version" in body):
        hit = "phpinfo page"
    elif path == "/server-status" and "Apache Server Status" in body:
        hit = "Apache server-status page"
    elif path == "/debug" and any(m in body for m in _ERROR_MARKERS):
        hit = "debug page"
    if hit:
        run.add("web-debug-exposed", res, f"{path} is publicly readable: {hit}.", confidence=0.9)


def run_web_baseline(base_url: str, *, timeout_s: float = 10.0, max_requests: int = 40,
                     auth: Authorization | None = None, sender: Sender | None = None) -> WebBaselineReport:
    """Run the passive web/API baseline against ``base_url``. Blocking: call it off the loop."""
    started = time.monotonic()
    report = WebBaselineReport(started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    url = str(base_url or "").strip()
    if not url:
        report.status = "not_applicable"
        report.detail = "no base URL"
        return report
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        report.status = "blocked"
        report.detail = f"unsupported base URL: {redact(url)[:200]}"
        return report
    origin = f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"
    report.target = origin
    https = parts.scheme == "https"
    if auth is None:
        targets = [origin]
        if https and parts.port in (None, 443):
            targets.append(f"http://{parts.hostname}")
        auth = Authorization(targets=tuple(targets))
    run = _Runner(origin, auth, sender or send_probe, timeout_s, max_requests)
    try:
        _run(run, origin, https)
        report.status = "ok" if run.requests else "blocked"
    except _UnreachableError as exc:
        report.status = "blocked"
        report.detail = str(exc)
    except Exception as exc:  # a broken target must not break the audit
        logger.exception("web baseline failed for {}", origin)
        report.status = "failed"
        report.detail = f"{type(exc).__name__}: {exc}"[:300]
    report.requests = run.requests
    report.checks_run = list(run.checks)
    report.findings = list(run.findings.values())
    report.observations = run.observations
    report.duration_s = round(time.monotonic() - started, 3)
    return report


class _UnreachableError(Exception):
    pass


def _run(run: _Runner, origin: str, https: bool) -> None:
    home = run.probe("GET", origin + "/", headers={"Origin": _PROBE_ORIGIN})
    if home is None or not home.ok:
        raise _UnreachableError(f"target unreachable or refused: {home.error if home else 'refused by policy'}")
    _check_headers(run, home, https)
    _check_cookies(run, home, https, auth_path=False)
    _check_error(run, home)

    run.ran("web-cors-reflect", "web-cors-wildcard-credentials")
    for res in (home, run.probe("OPTIONS", origin + "/", headers={
            "Origin": _PROBE_ORIGIN, "Access-Control-Request-Method": "GET"})):
        if res is None or not res.ok:
            continue
        h = _lower(res.headers)
        acao = h.get("access-control-allow-origin", "").strip()
        creds = h.get("access-control-allow-credentials", "").strip().lower() == "true"
        if acao == _PROBE_ORIGIN:
            run.add("web-cors-reflect", res, f"Origin {_PROBE_ORIGIN} reflected (credentials={creds}).", headers=("Origin",))
        elif acao == "*" and creds:
            run.add("web-cors-wildcard-credentials", res, "ACAO * with Allow-Credentials true.", headers=("Origin",))

    if https and urlsplit(origin).port in (None, 443):
        run.ran("web-https-redirect-missing")
        plain = run.probe("GET", f"http://{urlsplit(origin).hostname}/")
        if plain is not None and plain.ok:
            loc = _lower(plain.headers).get("location", "")
            if not (300 <= (plain.status or 0) < 400 and loc.startswith("https://")):
                run.add("web-https-redirect-missing", plain, f"http:// answered {plain.status} without an HTTPS redirect.")

    run.ran("web-security-txt-missing")
    sec = run.probe("GET", origin + "/.well-known/security.txt")
    if sec is not None and sec.ok and not (sec.status == 200 and "contact:" in sec.body.lower()):
        run.add("web-security-txt-missing", sec, "No /.well-known/security.txt with a Contact field.", confidence=0.9)
    run.probe("GET", origin + "/robots.txt")

    missing = run.probe("GET", f"{origin}/navin-{secrets.token_hex(6)}-not-found")
    if missing is not None and missing.ok:
        _check_error(run, missing)

    run.ran("web-debug-exposed")
    for path in _DEBUG_PATHS:
        res = run.probe("GET", origin + path)
        if res is not None and res.ok:
            _check_debug(run, path, res)
            _check_error(run, res)

    run.ran("web-admin-exposed")
    admin = run.probe("GET", origin + "/admin")
    if admin is not None and admin.ok:
        h = _lower(admin.headers)
        _check_cookies(run, admin, https, auth_path=True)
        if admin.status == 200 and "html" in h.get("content-type", "").lower() and "www-authenticate" not in h \
                and not re.search(r"(?i)type\s*=\s*[\"']?password", admin.body):
            run.add("web-admin-exposed", admin, "/admin returns an HTML page without an auth challenge.", confidence=0.3)

    run.ran("web-graphql-introspection")
    gql = run.probe("GET", origin + _GRAPHQL_PATH)
    if gql is not None and gql.ok and gql.status == 200 and '"__schema"' in gql.body:
        run.add("web-graphql-introspection", gql, "GET introspection query returned the schema.", confidence=0.9)

    run.ran("web-openapi-exposed")
    for path in ("/openapi.json", "/swagger.json"):
        res = run.probe("GET", origin + path)
        if res is not None and res.ok and res.status == 200 and re.search(r'"(openapi|swagger)"\s*:', res.body[:2000]):
            run.add("web-openapi-exposed", res, f"{path} serves an API description.", confidence=0.9)
            break

    run.ran("web-sourcemap-exposed")
    scripts: list[str] = []
    for src in _SCRIPT_SRC.findall(home.body):
        full = urljoin(origin + "/", src).split("#", 1)[0].split("?", 1)[0]
        p = urlsplit(full)
        if f"{p.scheme}://{p.netloc}" == origin and full not in scripts:
            scripts.append(full)
    for script in scripts[:5]:
        res = run.probe("HEAD", script + ".map")
        if res is not None and res.ok and res.status == 200:
            ctype = _lower(res.headers).get("content-type", "").lower()
            if "html" in ctype:
                continue
            if "json" not in ctype:
                res = run.probe("GET", script + ".map")
                if res is None or not res.ok or res.status != 200 or '"mappings"' not in res.body[:4000]:
                    continue
            run.add("web-sourcemap-exposed", res, f"{urlsplit(script).path}.map is served.", confidence=0.9)
            break
