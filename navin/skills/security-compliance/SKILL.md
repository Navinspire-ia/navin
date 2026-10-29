---
name: security-compliance
description: Check OWASP ASVS, CIS and NIST requirements. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Compliance (Navin Security)

## Overview

Map findings and gaps to ASVS, CIS and NIST requirements, and check logging and recovery controls.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: audit_logging.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C7.05 | L3 | Security event logging |
| C7.06 | L3 | Log integrity and tamper protection |
| C7.07 | L1 | Sensitive data in logs |
| C7.08 | L5 | Correlation and alerting |
| C7.09 | L3 | Backups, restore and recovery plan |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): gitleaks.
- Other engines named by the catalog: navin_audit, navin_rules. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Take the control status from the correlation: clean, potential, confirmed, exploitable and not assessed are different answers.
2. Logging: security events recorded with who, what, when and from where; logs protected from tampering; no secrets in logs.
3. Detection: alerts on repeated failures, privilege changes and unusual exports.
4. Recovery: backups exist, restores are tested, the plan names an owner.
5. Report which requirements were not assessed and why; absence of evidence is not compliance.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Declaring a standard met from a scan alone.
- Counting not assessed controls as passed.
- Treating a clean scan as proof of security: say which controls were not assessed.
