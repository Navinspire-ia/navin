---
name: security-business-logic
description: Identify business rule bypasses. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Business Logic (Navin Security)

## Overview

Find rule bypasses that no scanner can: price, quota, workflow order and concurrency.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: payments, workflow, api.
- Families: business_logic.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C1.08 | L2 | Race conditions and TOCTOU |
| C3.08 | L3 | Business logic: price, payment, double spend, workflow |
| C3.09 | L4 | Race conditions on transactions |

## Engines

- Other engines named by the catalog: navin_business_logic, navin_rules. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. List the invariants the business depends on: totals match items, a coupon applies once, a refund never exceeds a payment.
2. For each, find the server-side enforcement; a client check is not enforcement.
3. Try the abuse in reading order: negative or huge quantities, repeated redemption, skipped steps, replay of an old request.
4. Races: two concurrent requests on balance, stock, redemption; look for read-modify-write without a lock or constraint.
5. Only on an isolated environment with synthetic data may race tests send parallel requests.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Running race tests against production or real payment providers.
- Reporting a theoretical race without a state that can be corrupted.
- Treating a clean scan as proof of security: say which controls were not assessed.
