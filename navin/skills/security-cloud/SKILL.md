---
name: security-cloud
description: Analyze AWS, Azure, GCP and IAM. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Cloud (Navin Security)

## Overview

Review cloud accounts as code and, when read access is granted, as deployed.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: cloud, terraform.
- Families: infrastructure.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C4.07 | L3 | Cloud IAM: excessive privileges, keys, roles |
| C4.08 | L3 | Public storage and exposed data |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): trivy.
- Other engines named by the catalog: checkov, prowler. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Read Terraform, CloudFormation and CDK first; run the registered misconfiguration scanner on the files.
2. IAM: wildcard actions or resources, admin policies on workloads, long-lived keys, roles any principal can assume.
3. Data: public buckets and snapshots, unencrypted volumes and databases, open security groups, public endpoints on private services.
4. Only with explicit read access, compare the code with the live account; never change anything in the account.
5. Mark every finding with the account, region and resource id so it can be fixed in code.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Running write or delete commands against an account during an audit.
- Calling an account clean from the code alone when the live state was not read.
- Treating a clean scan as proof of security: say which controls were not assessed.
