---
name: security-secrets
description: Identify exposed secrets and their propagation. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Secrets (Navin Security)

## Overview

Find exposed credentials, how far they spread and what each one unlocks, without ever copying the value.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: secrets.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C1.10 | L1 | Hardcoded secrets and sensitive logs |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): gitleaks, trivy.
- Other engines named by the catalog: navin_rules, trufflehog. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Run the registered scanners (gitleaks, trivy) and review `.env` files, CI variables, mobile resources and logs.
2. Never print or store a secret value: report rule, file, line and a masked match only.
3. For each hit decide live or placeholder from the format and the surrounding code; do not call the provider to test a key.
4. Trace propagation: git history, build artifacts, container layers, client bundles, prompts and logs.
5. Remediation always has three parts: revoke and rotate, remove from code and history, load from a secret manager.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Pasting a found secret into the report or the chat.
- Treating a test key in a fixture as a leak without checking it is a placeholder.
- Treating a clean scan as proof of security: say which controls were not assessed.
