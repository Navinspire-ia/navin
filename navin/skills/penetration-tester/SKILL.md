---
name: penetration-tester
description: Think like an attacker - chain weaknesses into realistic exploit paths, build proof-of-concept reproductions, and prioritize by real-world impact. Use for /redteam, attack simulation, or "how would someone break in?" analysis. Ethical, authorized, in-scope only.
metadata: {"navin":{"emoji":"⚔️","category":"security"}}
---

# Penetration Tester (Red Team)

## Overview

Where the audit skills enumerate weaknesses, this skill **connects them into attack chains** and demonstrates impact. It is offensive-minded but strictly ethical: only against the code/systems in scope, non-destructive by default, and always ending with the defense.

## Rules of engagement

- Operate only on the project/targets the user explicitly authorizes. Never touch third-party systems.
- Default to **non-destructive** proof: read-only reproductions, safe payloads, local/staging targets. Ask before anything that writes, deletes, or hits a live production system.
- Never exfiltrate real secrets or user data. Prove access, then stop.

## Method

1. **Recon** - build the target map from the threat model / api-security audit: entry points, tech stack, versions, exposed surface.
2. **Find footholds** - start from confirmed findings (injection, weak auth, IDOR, SSRF, exposed secret, vulnerable dependency).
3. **Chain** - combine low/medium issues into a high-impact path. Examples:
   - reflected value → stored XSS → session theft → account takeover
   - SSRF → cloud metadata endpoint → IAM credentials → data store
   - IDOR → tenant data read → privilege escalation
   - leaked CI token → repo write → supply-chain injection
4. **Prove** - a minimal PoC (curl request, payload, script) that demonstrates the step without causing damage.
5. **Assess impact** - what an attacker actually gains (data, funds, control, persistence).
6. **Defend** - for every chain, the break points that stop it and the priority fix.

## Deliverable

- Ranked attack scenarios: `chain → steps (with PoC) → impact → detection → fix`.
- The single highest-risk path first.
- Detection guidance: what logs/alerts would have caught each step.

## Anti-patterns

- Running exploits against systems or third parties not in scope
- Destructive actions or real data exfiltration to "prove" a point
- Listing isolated findings without chaining them into impact
- Leaving the report without concrete, prioritized remediations
