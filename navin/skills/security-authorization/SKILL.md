---
name: security-authorization
description: Verify IDOR, BOLA, RBAC and ABAC. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Authorization (Navin Security)

## Overview

Prove or refute that one identity can reach another identity's data or a higher role's function.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: auth, api, web.
- Families: authorization.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C2.07 | L2 | IDOR / BOLA on every resource |
| C2.08 | L3 | BFLA, RBAC and ABAC: vertical escalation |

## Engines

- Other engines named by the catalog: navin_auth, schemathesis, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. List every route or resolver that takes an object id and the check that binds it to the caller.
2. Look for ownership checks done only in the client, or on list endpoints but not on read, update and delete.
3. Compare roles: for each privileged function find the server-side role check; a hidden button is not a control.
4. With two test accounts, replay a request of account A using account B's session and an id of A; compare status and body.
5. A confirmed IDOR needs the dynamic evidence (both requests, both responses redacted); read-only requests only.
6. Fix by deriving the owner from the session and filtering the query, not by checking after the fetch.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Using production accounts or real customer ids.
- Reporting a missing check without finding out whether a layer below enforces it (row level security).
- Treating a clean scan as proof of security: say which controls were not assessed.
