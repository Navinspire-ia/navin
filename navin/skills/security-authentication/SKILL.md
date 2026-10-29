---
name: security-authentication
description: Audit JWT, OAuth, OIDC, MFA and sessions. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Authentication (Navin Security)

## Overview

Audit how identities are proven: passwords, MFA, sessions, JWT, OAuth and OIDC, reset flows.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: auth, web, api.
- Families: authentication.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C2.01 | L2 | Brute force and credential stuffing |
| C2.02 | L2 | MFA: bypass and enrolment |
| C2.03 | L1 | JWT: signature, alg none, expiry, kid/jku |
| C2.04 | L2 | Sessions: fixation, rotation, invalidation, cookies |
| C2.05 | L2 | Password reset and tokens |
| C2.06 | L2 | OAuth/OIDC: redirect_uri, state, PKCE, nonce |
| C2.10 | L1 | Password storage and default accounts |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): semgrep, bandit.
- Other engines named by the catalog: navin_auth, navin_rules, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Read how passwords are stored (argon2, bcrypt, scrypt) and whether default or seeded accounts exist.
2. JWT: signature checked, `alg` pinned, `none` refused, expiry and audience enforced, `kid` and `jku` not attacker-controlled.
3. Sessions: id rotated on login, invalidated on logout and password change, cookies `Secure`, `HttpOnly`, `SameSite`.
4. Reset and invitation tokens: random, single-use, short-lived, not echoed in logs or referrers.
5. OAuth and OIDC: exact `redirect_uri` match, `state`, PKCE, `nonce`; open redirects count as findings here.
6. On a running target, test rate limiting and lockout with a handful of requests, never a real credential stuffing run.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Assuming MFA is enforced because the UI asks for it: check the API.
- Testing with real user credentials.
- Treating a clean scan as proof of security: say which controls were not assessed.
