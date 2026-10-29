---
name: security-threat-modeling
description: Identify sensitive assets, trust boundaries and attack scenarios. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Threat Modeling (Navin Security)

## Overview

Turn the discovered stack into assets, trust boundaries and attack scenarios before any scanner runs.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: injection, authentication, authorization, multitenancy, code_execution, files.

## Method

1. List the assets worth stealing or breaking: identities, tenant data, money, secrets, infrastructure, the agent's own tools.
2. Draw the trust boundaries: internet to app, user to user, tenant to tenant, app to database, app to cloud, model to tools.
3. For each boundary write the attack scenario in one line (who, what they control, what they reach) and tag the controls that would stop it.
4. Rank families by asset value times exposure; this order decides what the audit reads first.
5. State what the model could not see (no running target, no second tenant) so the report can say it was not covered.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Listing every OWASP category without tying it to an asset of this project.
- Treating a framework default as a mitigation without reading the config.
- Treating a clean scan as proof of security: say which controls were not assessed.
