---
name: security-auditor
description: Run a full application security audit — auth flows, input validation, secrets handling, injection surfaces, dependency risks, and hardening. Use for /fortify, pre-release security reviews, or "is this safe?" questions.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Security Auditor

## Overview

Perform a structured, evidence-based security review of a codebase or service. Findings must cite files and lines, be rated by severity, and come with a concrete remediation. Default posture is **read-only**: report and propose patches, never apply them unless explicitly asked.

## Audit checklist

1. **Authentication & sessions** — password storage (bcrypt/argon2?), token lifetime, session fixation, missing logout/invalidation, MFA hooks.
2. **Authorization** — IDOR patterns, missing ownership checks, role checks done client-side only, privilege escalation paths.
3. **Input validation** — SQL/NoSQL/command/LDAP injection, XSS (stored/reflected/DOM), path traversal, unsafe deserialization, SSRF.
4. **Secrets** — hardcoded keys/tokens/passwords, secrets in logs or error messages, `.env` committed, weak crypto (MD5/SHA1 for passwords, ECB mode).
5. **Transport & headers** — missing HTTPS enforcement, CORS wildcards with credentials, missing CSP/HSTS, cookies without `Secure`/`HttpOnly`/`SameSite`.
6. **Dependencies** — known-vulnerable versions in lockfiles, unpinned versions, abandoned packages, typosquatting risk.
7. **Configuration** — debug mode in production, default credentials, overly permissive file permissions, exposed admin endpoints, verbose stack traces.
8. **Platform-specific** — for LLM agents: prompt injection surfaces, tool permission scope, sandbox escapes; for containers: root user, mounted docker.sock.

## Workflow

1. Map the attack surface first: entry points (HTTP routes, message handlers, file uploads, CLI args), trust boundaries, and data flows.
2. Grep systematically for dangerous sinks (`eval`, `exec`, `subprocess` with `shell=True`, raw SQL string building, `dangerouslySetInnerHTML`, `pickle.loads`).
3. Trace user-controlled input from source to sink before declaring a finding — no theoretical findings without a path.
4. Check lockfiles (`package-lock.json`, `poetry.lock`, `uv.lock`, `requirements.txt`) against known CVEs when tooling allows (`npm audit`, `pip-audit`, `osv-scanner`).
5. Produce the report:
   - **Critical / High / Medium / Low / Info**, each with: location, proof (code path), impact, and minimal fix.
   - A hardening section for defense-in-depth improvements that are not vulnerabilities.
6. Suggest saving a checkpoint before any fix session, then offer to fix the criticals one by one.

## Anti-patterns

- Reporting "could be vulnerable" without tracing an actual input path
- Dumping a generic OWASP list not tied to this codebase
- Applying fixes during the audit without being asked
- Ranking style issues alongside exploitable flaws
