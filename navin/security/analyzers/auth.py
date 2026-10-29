# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Authentication and access analyzer.

Four checks, each a static suspicion to confirm on the running app:

- a state-changing route with no authentication or permission check
  (decorator, dependency, router dependency, or a check in its first lines),
  skipped when a global auth middleware is found;
- JWT verification switched off: ``verify=False``, ``verify_signature`` or
  expiry disabled, the ``none`` algorithm accepted, a decode without verify;
- a login route with no rate limiting anywhere in the project;
- a webhook that acts on its payload without checking a signature.
"""

from __future__ import annotations

import ast
import re

from navin.security.analyzers.common import (
    Route,
    SourceSet,
    all_args,
    const_str,
    dotted,
    functions,
    keyword,
    line_of,
    line_text,
    route_of,
    short,
)
from navin.security.model import SEVERITIES, Evidence, Finding
from navin.security.verification.probe import redact

ANALYZER_ID = "navin_auth"
CHECKED_CONTROLS = frozenset({"C2.01", "C2.03", "C2.08", "C3.10"})

_AUTH_DECORATOR = re.compile(
    r"login_required|auth|permission|requires|jwt_required|roles?_required|admin_required|protected|"
    r"authenticated|token_required|guard|secured|check_access|scope", re.I)
_AUTH_DEPENDENCY = re.compile(r"auth|user|token|permission|verify|security|session|guard|principal|claims|scope|api_?key|admin", re.I)
_AUTH_BODY = re.compile(
    r"is_authenticated|current_user|check_perm|has_perm|require_\w+\(|verify_\w+\(|authorize|authenticate|"
    r"get_user_from|decode_token|jwt\.decode|abort\(\s*40[13]|status_code\s*=\s*(?:40[13]|status\.HTTP_40[13])|"
    r"PermissionDenied|Unauthorized|Forbidden", re.I)
_IDENTITY_TYPES = re.compile(r"\b(?:User|Principal|Identity|Claims|TokenData|CurrentUser|AuthUser)\b")
_PUBLIC_PATH = re.compile(
    r"(?:^|/)(?:login|logout|signin|sign-in|signup|sign-up|register|token|refresh|health|healthz|ready|readyz|"
    r"live|livez|ping|status|webhooks?|hooks?|oauth2?|callback|auth|password|reset|forgot|verify|confirm|public|"
    r"docs|redoc|openapi|metrics|static|favicon)(?:[/.{_-]|$)", re.I)
_PROJECT_AUTH = re.compile(r"add_middleware\(\s*\w*(?:auth|session|jwt|token|login)\w*|FastAPI\([^)]*dependencies\s*=", re.I)
_ROUTER_AUTH = re.compile(r"APIRouter\([^)]*dependencies\s*=", re.I)
_RATE_LIMIT = re.compile(r"slowapi|flask_limiter|Limiter\(|fastapi_limiter|RateLimiter|ratelimit|throttl|rate_limit|express-rate-limit", re.I)
_LOGIN_PATH = re.compile(r"(?:^|/)(?:login|signin|sign-in|token|sessions?)(?:/|$)", re.I)
_SIGNATURE = re.compile(
    r"signature|hmac|compare_digest|timingSafeEqual|construct_?event|constructEvent|verify|svix|webhook_secret|signing_secret", re.I)
_JWT_OPTION_SEVERITY = {"verify_signature": "high", "verify_exp": "medium", "verify_aud": "medium", "verify_iss": "medium", "verify_nbf": "low"}
_JS_JWT = (
    (re.compile(r"algorithms\s*:\s*\[[^\]]*['\"]none['\"]", re.I), "high", "the 'none' algorithm is accepted"),
    (re.compile(r"ignoreExpiration\s*:\s*true"), "medium", "the expiry check is disabled (ignoreExpiration)"),
)
_JS_WEBHOOK = re.compile(r"\b(?:app|router)\.post\(\s*['\"`]([^'\"`]*webhook[^'\"`]*)['\"`]", re.I)
_JS_NEXT_ROUTE = re.compile(r"\b(?:app|router)\.(?:get|post|put|patch|delete|use)\(")


def _protected(func: ast.FunctionDef | ast.AsyncFunctionDef, route: Route) -> bool:
    if keyword(route.decorator, "dependencies") is not None:
        return True
    for deco in func.decorator_list:
        if deco is not route.decorator and _AUTH_DECORATOR.search(short(deco, 300)):
            return True
    for arg, default in all_args(func):
        if isinstance(default, ast.Call) and dotted(default.func).rsplit(".", 1)[-1] in {"Depends", "Security"}:
            if _AUTH_DEPENDENCY.search(short(default, 300)):
                return True
        if arg.annotation is not None:
            annotation = short(arg.annotation, 300)
            if ("Depends(" in annotation or "Security(" in annotation) and _AUTH_DEPENDENCY.search(annotation):
                return True
            if _IDENTITY_TYPES.search(annotation):
                return True
    head = " ".join(short(stmt, 400) for stmt in func.body[:8])
    return bool(_AUTH_BODY.search(head))


def _static(rel: str, line: int, text: str, what: str) -> Evidence:
    return Evidence(kind="static", description=what, tool=ANALYZER_ID, observed=redact(line_text(text, line)), location=f"{rel}:{line}")


def _routes(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    texts = [src.text for src in sources.python]
    project_auth = next((m.group(0) for m in (_PROJECT_AUTH.search(t) for t in texts) if m), "")
    rate_limited = any(_RATE_LIMIT.search(t) for t in texts) or any(_RATE_LIMIT.search(s.text) for s in sources.scripts)
    if project_auth:
        sources.notes.append(f"global authentication found ({project_auth.strip()}): routes are not checked one by one")
    for src, tree in sources.python_trees():
        router_auth = bool(_ROUTER_AUTH.search(src.text)) or ("before_request" in src.text and bool(_AUTH_BODY.search(src.text)))
        for func in functions(tree):
            route = route_of(func)
            if route is None or not route.mutating:
                continue
            body = short(func, 20000)
            if "webhook" in route.path.lower() or "webhook" in func.name.lower():
                if not _SIGNATURE.search(body):
                    findings.append(Finding(
                        id="navin-auth-webhook-unsigned", family="api", severity="high", cwe="CWE-345",
                        title=f"Webhook accepted without a signature check: {route.endpoint}",
                        file_path=src.rel, line=func.lineno, endpoint=route.endpoint, controls=["C3.10"], impact="fraud",
                        confidence=0.6, source=ANALYZER_ID,
                        description="The handler never verifies a signature or an HMAC: anyone who knows the URL can forge events.",
                        evidence=[_static(src.rel, func.lineno, src.text, "no signature, HMAC or constructEvent call in the handler")],
                    ))
                continue
            if _LOGIN_PATH.search(route.path) and not rate_limited:
                findings.append(Finding(
                    id="navin-auth-login-throttle", family="authentication", severity="medium", cwe="CWE-307",
                    title=f"Login without rate limiting: {route.endpoint}",
                    file_path=src.rel, line=func.lineno, endpoint=route.endpoint, controls=["C2.01"], impact="escalation",
                    confidence=0.5, source=ANALYZER_ID,
                    description="No rate limiter is used anywhere in the project: passwords can be guessed at full speed.",
                    evidence=[_static(src.rel, func.lineno, src.text, "no limiter, throttle or rate limit in the project")],
                ))
            if project_auth or router_auth or _PUBLIC_PATH.search(route.path) or _protected(func, route):
                continue
            findings.append(Finding(
                id="navin-auth-missing", family="authorization", severity="high", cwe="CWE-862",
                title=f"State-changing route without an authentication check: {route.endpoint}",
                file_path=src.rel, line=func.lineno, endpoint=route.endpoint, controls=["C2.08"], impact="escalation",
                confidence=0.45, source=ANALYZER_ID,
                description=("No auth decorator, dependency, router dependency or check in its first statements, and no "
                             "global auth middleware was found. Confirm with an anonymous request."),
                evidence=[_static(src.rel, func.lineno, src.text, f"{route.endpoint} handled by {func.name}() with no auth check")],
            ))
    return findings


def _jwt_python(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src, tree in sources.python_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            issues: list[tuple[str, str]] = []
            if name == "jwt.get_unverified_claims" or name.endswith(".jwt.get_unverified_claims"):
                issues.append(("medium", "claims are read without verifying the signature"))
            elif name == "jwt.decode" or name.endswith(".jwt.decode"):
                verify = keyword(node, "verify")
                if isinstance(verify, ast.Constant) and verify.value is False:
                    issues.append(("high", "signature verification is disabled (verify=False)"))
                options = keyword(node, "options")
                if isinstance(options, ast.Dict):
                    for key_node, value in zip(options.keys, options.values):
                        key = const_str(key_node) or ""
                        if key in _JWT_OPTION_SEVERITY and isinstance(value, ast.Constant) and value.value is False:
                            issues.append((_JWT_OPTION_SEVERITY[key], f"{key} is disabled"))
                algorithms = keyword(node, "algorithms")
                if isinstance(algorithms, (ast.List, ast.Tuple, ast.Set)) and any(
                    (const_str(e) or "").lower() == "none" for e in algorithms.elts
                ):
                    issues.append(("high", "the 'none' algorithm is accepted"))
            if issues:
                findings.append(_jwt_finding(src.rel, node.lineno, src.text, issues))
    for src in sources.scripts:
        issues_at: dict[int, list[tuple[str, str]]] = {}
        for pattern, severity, what in _JS_JWT:
            for match in pattern.finditer(src.text):
                issues_at.setdefault(line_of(src.text, match.start()), []).append((severity, what))
        if "jsonwebtoken" in src.text and "jwt.verify(" not in src.text:
            match = re.search(r"\bjwt\.decode\(", src.text)
            if match:
                issues_at.setdefault(line_of(src.text, match.start()), []).append(
                    ("medium", "jwt.decode() reads the token without verifying it and this file never calls jwt.verify()"))
        for line, issues in sorted(issues_at.items()):
            findings.append(_jwt_finding(src.rel, line, src.text, issues))
    return findings


def _jwt_finding(rel: str, line: int, text: str, issues: list[tuple[str, str]]) -> Finding:
    severity = min((s for s, _ in issues), key=SEVERITIES.index)
    return Finding(
        id="navin-auth-jwt", family="authentication", severity=severity, cwe="CWE-347",
        title=f"JWT check weakened: {issues[0][1]}", file_path=rel, line=line, controls=["C2.03"], impact="escalation",
        confidence=0.7, source=ANALYZER_ID, description="; ".join(what for _, what in issues) + ".",
        evidence=[_static(rel, line, text, "; ".join(what for _, what in issues))],
    )


def _webhooks_js(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src in sources.scripts:
        for match in _JS_WEBHOOK.finditer(src.text):
            window = src.text[match.end(): match.end() + 2500]
            following = _JS_NEXT_ROUTE.search(window)
            if following:
                window = window[: following.start()]
            if _SIGNATURE.search(window):
                continue
            line = line_of(src.text, match.start())
            findings.append(Finding(
                id="navin-auth-webhook-unsigned", family="api", severity="high", cwe="CWE-345",
                title=f"Webhook accepted without a signature check: POST {match.group(1)}",
                file_path=src.rel, line=line, endpoint=f"POST {match.group(1)}", controls=["C3.10"], impact="fraud",
                confidence=0.55, source=ANALYZER_ID,
                description="The handler never verifies a signature or an HMAC: anyone who knows the URL can forge events.",
                evidence=[_static(src.rel, line, src.text, "no signature, HMAC or constructEvent call before the next route")],
            ))
    return findings


def analyze(sources: SourceSet) -> list[Finding]:
    return [*_routes(sources), *_jwt_python(sources), *_webhooks_js(sources)]
