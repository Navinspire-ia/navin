---
name: security-supply-chain
description: Audit dependencies, SBOM, artifacts and CI/CD. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Supply Chain (Navin Security)

## Overview

Audit dependencies, lockfiles, build pipelines and artifacts for known and structural weaknesses.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: always.
- Families: supply_chain.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C4.01 | L1 | Vulnerable dependencies (CVE/OSV, KEV, EPSS) |
| C4.02 | L1 | Dependency confusion, typosquatting, lockfile integrity |
| C4.03 | L5 | SBOM and artifact provenance |
| C4.04 | L1 | CI/CD: permissions, secrets, pull_request_target, pinned actions |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): gitleaks, trivy, osv_scanner, pip_audit, npm_audit, cargo_audit.
- Other engines named by the catalog: checkov, grype, navin_rules, syft. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Run the registered dependency scanners that fit the manifests present (osv-scanner, trivy, pip-audit, npm audit, cargo-audit) and merge duplicates by advisory.
2. Rank by reachability and exposure, not by count: a critical in a dev-only tool is lower than a high in a request path.
3. Check structure: lockfiles committed and honored, versions pinned, no install scripts from unknown packages, no confusable names.
4. CI/CD: workflow permissions minimal, `pull_request_target` never checks out untrusted code, actions pinned by digest, secrets not exposed to forks.
5. Artifacts: SBOM generated, images signed, provenance recorded.
6. Remediation names the exact upgrade; when none exists say so and propose removal or an accepted risk with an owner.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Reporting a scanner's total as the finding.
- Running `npm audit fix --force` as the remediation without checking breaking changes.
- Treating a clean scan as proof of security: say which controls were not assessed.
