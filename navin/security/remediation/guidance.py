# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How to fix each weakness, keyed by CWE first and family second."""

from __future__ import annotations

from navin.security.model import Finding

BY_CWE: dict[str, str] = {
    "CWE-89": "Use parameterized queries (placeholders with bound values) or the ORM query builder; never build SQL with f-strings, + or %.",
    "CWE-78": "Pass an argument list without shell=True and check the input against an allowlist; never interpolate user input into a shell string.",
    "CWE-94": "Remove eval/exec on user input; parse data with json or ast.literal_eval, or map the input to an allowlist of operations.",
    "CWE-22": "Resolve the path and check it stays under the allowed base (Path.resolve() then is_relative_to); use secure_filename for uploads.",
    "CWE-502": "Never unpickle data from a client; use json or a schema-validated format, and yaml.safe_load instead of yaml.load.",
    "CWE-918": "Fetch only URLs whose scheme and host are on an allowlist; block private, loopback and metadata addresses after DNS resolution; do not follow redirects.",
    "CWE-1336": "Render fixed templates and pass user data as context variables; never hand user input to render_template_string or Template().",
    "CWE-862": "Require authentication and an explicit permission check on every state-changing route (dependency, decorator or middleware), and test it anonymously and with a low-privilege account.",
    "CWE-347": "Verify the signature with a pinned algorithm list (never 'none'), keep the exp, aud and iss checks on, and reject a kid or jku outside your key set.",
    "CWE-639": "Take the tenant from the authenticated session or token, never from the body, the query or a header; check membership when it appears in the URL.",
    "CWE-863": "Filter every query on tenant-scoped data by the caller's tenant, or enforce it in the database with Row Level Security.",
    "CWE-602": "Recompute prices, totals and discounts on the server from the catalog; treat amounts sent by the client as display only.",
    "CWE-345": "Verify the webhook signature (HMAC with the shared secret, constant-time comparison) and its timestamp before acting on the payload.",
    "CWE-307": "Rate-limit login attempts per account and per IP, slow down repeated failures and alert on bursts.",
    "CWE-798": "Remove the secret from the code and its history, rotate it, and load it from the environment or a secret manager.",
}

BY_FAMILY: dict[str, str] = {
    "injection": "Keep data and code apart: parameterize, encode for the output context, and validate input against an allowlist.",
    "authentication": "Rely on a vetted authentication library, keep every token check on, and test the flow with forged and expired credentials.",
    "authorization": "Check object and function permissions on the server for every request, deny by default.",
    "multitenancy": "Derive the tenant from the session and scope every query, cache key and file path by it.",
    "code_execution": "Never execute or deserialize what a client controls; isolate what must run.",
    "files": "Confine file access to a base directory and check type and size of uploads.",
    "api": "Validate the schema of every request and response, limit rates and verify webhook signatures.",
    "secrets": "Move secrets to a secret manager, rotate the exposed ones and scan history.",
    "cryptography": "Use current algorithms and libraries with safe defaults; never roll your own.",
    "supply_chain": "Upgrade to a fixed version, pin dependencies with a lockfile and verify integrity.",
    "infrastructure": "Apply the CIS benchmark for the platform: least privilege, no root, no public exposure by default.",
    "frontend": "Set CSP and the security headers, encode output and keep tokens out of script-readable storage.",
    "business_logic": "Enforce every business rule on the server, inside a transaction where money or stock is involved.",
    "availability": "Bound input sizes, timeouts and rates; avoid catastrophic regular expressions.",
    "network": "Restrict outbound requests to an allowlist and terminate TLS with valid certificates.",
    "mobile_desktop": "Store secrets in the OS keychain or keystore and restrict IPC and deep links.",
    "ai_agents": "Treat external content as data, confirm sensitive tool calls with a human and limit tool permissions.",
    "audit_logging": "Log security events without secrets, protect the logs from tampering and alert on anomalies.",
}


def remediation_for(finding: Finding) -> str:
    return BY_CWE.get(finding.cwe) or BY_FAMILY.get(finding.family, "")


def apply_guidance(findings: list[Finding]) -> list[Finding]:
    """Fill an empty remediation; a scanner's own advice is kept."""
    for item in findings:
        if not item.remediation:
            item.remediation = remediation_for(item)
    return findings
