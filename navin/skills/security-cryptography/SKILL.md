---
name: security-cryptography
description: Check TLS, encryption, signatures and keys. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Cryptography (Navin Security)

## Overview

Check that cryptography is the right primitive, correctly keyed and used over a verified channel.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: cryptography.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C4.09 | L2 | Network: exposed ports, TLS, certificates, insecure services |
| C4.10 | L1 | Encryption: algorithms, keys, rotation, at rest and in transit |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): semgrep, bandit.
- Other engines named by the catalog: navin_rules, nuclei, sslyze, testssl. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Search for weak or home-made primitives: MD5 or SHA-1 for passwords or signatures, ECB, static IVs, `random` for tokens.
2. Keys: not in the repo, rotated, sized correctly, separate per purpose; compare with the secrets findings.
3. TLS: minimum version, certificate verification never disabled (`verify=False`, `rejectUnauthorized: false`), HSTS on web.
4. Data at rest: which stores hold personal or secret data and whether they are encrypted with managed keys.
5. Signatures and MACs: constant-time comparison, signed content includes everything that matters.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Flagging a hash used for cache keys or checksums as weak crypto.
- Recommending a cipher change without naming the mode and key handling.
- Treating a clean scan as proof of security: say which controls were not assessed.
