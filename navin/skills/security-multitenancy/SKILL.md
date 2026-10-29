---
name: security-multitenancy
description: Verify tenant isolation, RLS, cache and storage. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Multitenancy (Navin Security)

## Overview

Verify that tenant A can never read or write tenant B through the app, the database, caches or storage.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: multitenant, database.
- Families: multitenancy.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C2.09 | L3 | Multi-tenant isolation: tenant_id source, RLS, cache |

## Engines

- Other engines named by the catalog: navin_tenant. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Find where `tenant_id` comes from: it must come from the session, never from a header or body the client sets.
2. Database: row level security enabled and forced on every tenant table, policies using the session claim, no `service_role` key in a client.
3. Caches and queues: keys and topics include the tenant; background jobs set the tenant context explicitly.
4. Storage: object paths and signed URLs scoped per tenant; listing a prefix must not cross tenants.
5. With two test tenants, repeat each read and write across the boundary and record the denial or the leak.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Trusting an ORM default scope without reading where it is bypassed (raw queries, admin tools, exports).
- Testing with one tenant only and calling the result clean.
- Treating a clean scan as proof of security: say which controls were not assessed.
