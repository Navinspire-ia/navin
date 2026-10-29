---
name: security-api
description: Test REST, GraphQL, WebSocket and webhooks. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Api (Navin Security)

## Overview

Test REST, GraphQL, WebSocket and webhook surfaces for exposure, abuse and server-side request flaws.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: api, graphql, websocket.
- Families: api, availability, network.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C3.01 | L2 | Mass assignment and excessive data exposure |
| C3.02 | L2 | API rate limiting and quotas |
| C3.03 | L2 | GraphQL: introspection, depth, batching |
| C3.06 | L2 | SSRF, host header, request smuggling, cache poisoning |
| C3.10 | L2 | Webhooks and WebSocket: signature, origin, authentication |
| C7.01 | L2 | Application DoS, expensive queries, pagination |
| C7.04 | L1 | Timeouts, quotas, circuit breakers, resource exhaustion |
| C7.10 | L2 | Error handling: no stack traces, fail closed |

## Engines

- Other engines named by the catalog: k6, navin_dataflow, navin_rules, nuclei, schemathesis, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Inventory routes from the OpenAPI file, the router code and the running target; flag undocumented and legacy versions.
2. Mass assignment: compare accepted fields with the model; try a role or owner field on update, read-only.
3. Excessive exposure: responses carrying fields the client does not need (hashes, internal ids, emails of others).
4. GraphQL: introspection in production, query depth and cost limits, batching used to bypass rate limits.
5. SSRF: every feature that fetches a URL must block loopback, link-local and metadata addresses after DNS resolution.
6. Webhooks and sockets: signature verified on the raw body, origin checked, authentication on connect, replay window.
7. Rate limits and pagination caps exist per identity; error responses carry no stack traces.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Load testing a shared environment to prove a missing rate limit: show the missing control in code and send a few requests.
- Reporting introspection alone as high severity.
- Treating a clean scan as proof of security: say which controls were not assessed.
