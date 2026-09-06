---
name: api-security-auditor
description: Audit the web/API attack surface - authn/authz on endpoints, CORS, CSRF, SSRF, security headers, rate limiting, mass assignment, and OWASP API Top 10. Use for /perimeter, API reviews, or exposed-surface hardening.
metadata: {"navin":{"emoji":"📡","category":"security"}}
---

# API & Web Surface Auditor

## Overview

Review everything exposed over the network: HTTP routes, GraphQL resolvers, websockets, and webhooks. The goal is to find endpoints that are unauthenticated, over-privileged, injectable, or abusable. Cite the exact route handler for each finding.

## Checklist (OWASP API Top 10 aligned)

1. **Broken object-level authorization (BOLA/IDOR)** - object IDs accepted from the client without an ownership check.
2. **Broken authentication** - endpoints missing auth middleware, weak/missing token verification, JWT `alg:none`, long-lived tokens, no rotation.
3. **Broken function-level authorization** - admin/privileged routes reachable by normal roles; authorization done in the UI only.
4. **Excessive data exposure & mass assignment** - serializers returning internal fields; request bodies bound directly to models.
5. **Injection & SSRF** - user input reaching SQL/NoSQL/command/template sinks; user-supplied URLs fetched without an allowlist.
6. **Resource abuse** - no rate limiting/pagination caps, unbounded uploads, expensive GraphQL queries (no depth/complexity limit).
7. **Transport & headers** - HTTPS enforced, HSTS, CSP, `X-Content-Type-Options`, secure/HttpOnly/SameSite cookies.
8. **CORS & CSRF** - reflected `Origin`, wildcard `Access-Control-Allow-Origin` with credentials, state-changing GETs, missing CSRF tokens on cookie-auth forms.

## Workflow

1. Enumerate every route/handler (router files, decorators, OpenAPI/GraphQL schema). Build a table: method, path, auth required?, roles, input sources.
2. For each, verify the auth + authz check actually runs before the handler logic, and that object access is scoped to the caller.
3. Test injection/SSRF paths by tracing input to sink.
4. Inspect middleware/config for headers, CORS, CSRF, and rate limits.
5. Report per finding: `[SEVERITY] route` - issue, proof (handler code), impact, and the minimal fix (middleware, scoping, header, limit).

## Anti-patterns

- Assuming a global auth middleware covers a route without confirming it is applied
- Reporting CORS wildcards as critical when no credentials are allowed (rate correctly)
- Listing generic header advice without checking what the server already sets
