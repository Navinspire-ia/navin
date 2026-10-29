---
name: security-attack-chaining
description: Chain several weaknesses into compromise scenarios. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Attack Chaining (Navin Security)

## Overview

Combine confirmed findings into compromise scenarios and rate the chain, not the parts.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: findings.
- Families: injection, authentication, authorization, multitenancy, code_execution, files.

## Method

1. Start from the correlation result: each attack path lists its steps, the link between them and the impact.
2. Check each link in the code or on the target; an unverified link keeps the chain a hypothesis.
3. Rate the chain by the final impact and the weakest link's exploitability; never add severities.
4. Name the cheapest link to cut and what it breaks; that is the first fix.
5. Do not execute a chain end to end; demonstrate links separately and read-only.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Presenting a hypothesis as a demonstrated compromise.
- Chaining findings of different applications.
- Treating a clean scan as proof of security: say which controls were not assessed.
