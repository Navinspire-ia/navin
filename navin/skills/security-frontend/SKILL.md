---
name: security-frontend
description: Verify DOM, CSP, CORS and browser storage. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Frontend (Navin Security)

## Overview

Check the browser side: DOM sinks, headers, CORS, framing and what is stored on the client.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: web, frontend.
- Families: frontend.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C3.04 | L2 | CORS, CSP, clickjacking and security headers |
| C3.05 | L1 | DOM XSS, prototype pollution and sensitive browser-side data |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): semgrep.
- Other engines named by the catalog: navin_dataflow, navin_headers, navin_rules, nuclei, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. DOM: sinks fed by URL, storage or messages; prototype pollution through merge helpers; sanitizer versions.
2. Headers: CSP without `unsafe-inline` and `unsafe-eval` where avoidable, `frame-ancestors`, `Referrer-Policy`, `X-Content-Type-Options`.
3. CORS: no wildcard with credentials, no reflected origin without an allow-list.
4. Storage: tokens in `localStorage`, secrets in bundles and source maps, sensitive data in URLs.
5. Third-party scripts: pinned, with integrity where static, and inventoried.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Reporting every missing header at the same severity.
- Calling a sanitizer safe without checking the version against known bypasses.
- Treating a clean scan as proof of security: say which controls were not assessed.
