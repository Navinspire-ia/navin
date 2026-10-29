---
name: security-remediation
description: Generate and validate fixes. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Remediation (Navin Security)

## Overview

Write the fix for each finding, prove it works, and leave a regression test behind.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: findings.
- Families: injection, authentication, authorization, multitenancy, code_execution, files.

## Method

1. Fix the cause named in the finding, with the smallest change; keep behavior otherwise identical.
2. Add a test that fails before the fix and passes after it; record its path in the finding's validation.
3. Re-run the scanner or probe that found the issue; mark the finding `clean` only when the same check no longer fires.
4. For dependencies upgrade to the first fixed version and run the test suite; note major version changes.
5. For secrets, rotation is part of the fix and is done by the owner; say so.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Suppressing the scanner rule instead of fixing the code.
- Marking a finding fixed without re-running the check.
- Treating a clean scan as proof of security: say which controls were not assessed.
