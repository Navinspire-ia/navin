---
name: identity-access-review
description: Run identity and access reviews (IAM/PAM recertification, joiners-movers-leavers, dormant and orphan accounts, privileged access and MFA coverage, service account key age, break-glass accounts, separation-of-duties conflicts) across Microsoft Entra ID and PIM, Okta, SailPoint IdentityNow, Saviynt, CyberArk, HashiCorp Vault, AWS IAM (credential report, Access Analyzer), GCP IAM recommender, Kubernetes RBAC and GitHub organisations, then map findings to ISO 27001:2022, SOC 2, DORA, NIS2 and PCI DSS 4.0 and build an auditor evidence pack. Use when the user asks to "review user access", "find dormant accounts", "check SoD conflicts", "list privileged accounts without MFA", "prepare the access recertification", "who has cluster-admin", or sends an IAM export to analyse. In French "revue des habilitations", "comptes dormants et orphelins", "separation des taches", "recertification des acces".
metadata: {"navin":{"emoji":"🪪","category":"security"}}
---

# Identity and Access Review

## Overview

Review who holds what access, whether that access is still justified, and whether any combination of rights lets one person commit and conceal an error or a fraud. The review covers the identity lifecycle (joiners, movers, leavers), dormant and orphan accounts, privileged accounts and PAM vaults, MFA coverage, service accounts and credential age, break-glass accounts, separation of duties (SoD) and the recertification campaigns themselves. The default posture is read-only and evidence-based: every statement cites an export, an API response or a command output with its timestamp, and nothing is disabled, removed or re-assigned on any target without the user's explicit approval of a named change.

## When it applies

- Periodic recertification (quarterly for privileged access, semi-annual for application access) or a pre-audit dry run for ISO 27001, SOC 2, PCI DSS, DORA or NIS2.
- Post-incident questions: "who could have done this", "how many admins do we really have".
- Reorganisation, merger, offboarding wave or a new HR system: reconcile HR truth with IAM reality.
- Cloud and platform hygiene: AWS unused access, GCP over-granted roles, Kubernetes `cluster-admin` sprawl, GitHub owners and admin tokens.
- Any user request that contains an IAM export (CSV, JSON, credential report) and asks what is wrong with it.

## Engines and sources

| Source | How it is read | Key fields |
|---|---|---|
| HR system (Workday, SAP SuccessFactors, BambooHR) | API or CSV extract supplied by HR | employee id, status, termination date, manager |
| Microsoft Entra ID / PIM | Microsoft Graph `GET /v1.0/users?$select=signInActivity`, `/reports/authenticationMethods/userRegistrationDetails`, `/roleManagement/directory/roleEligibilityScheduleInstances`, `/identityGovernance/accessReviews/definitions` | `lastSignInDateTime`, `isMfaRegistered`, eligible vs active role |
| Okta | `GET /api/v1/users?filter=status eq "ACTIVE"`, `/users/{id}/roles`, `/users/{id}/factors`, `/api-tokens` | `lastLogin`, `SUPER_ADMIN`, factor status |
| SailPoint IdentityNow / Saviynt | `GET /v3/campaigns`, `/v3/certifications/{id}/access-review-items`, `/v3/sod-policies`, `/v3/accounts?filters=uncorrelated eq true` | decision, reviewer, uncorrelated accounts |
| PAM: CyberArk, HashiCorp Vault, BeyondTrust, Delinea | Vault REST (`/PasswordVault/API/Accounts`, `/Safes/{id}/Members`), `vault policy read`, Password Safe and Secret Server APIs | safe membership, last rotation, session recordings |
| AWS IAM | `infra level=observe`: `aws iam generate-credential-report` then `aws iam get-credential-report`, `aws iam get-account-authorization-details`, `aws accessanalyzer list-findings-v2` (unused access) | `password_last_used`, `mfa_active`, `access_key_1_last_rotated`, `UnusedIAMRole` |
| GCP IAM | `infra level=observe`: `gcloud recommender recommendations list --recommender=google.iam.policy.Recommender`, `gcloud iam service-accounts keys list --managed-by=user` | `REMOVE_ROLE`, `validAfterTime` |
| Kubernetes | `infra level=observe`: `kubectl get clusterrolebindings -o json`, `kubectl auth can-i --list --as=<user>` | `cluster-admin` subjects, wildcard verbs |
| GitHub | `connectors` tool (GitHub): `GET /orgs/{org}/members?role=admin`, `?filter=2fa_disabled`, `/orgs/{org}/personal-access-tokens` | owners, 2FA, PAT scopes |
| Ticketing | `connectors` tool (Jira, ServiceNow) for access requests, change approvals and remediation tickets | approver, requester, dates |

Tokens (`OKTA_API_TOKEN`, `GITHUB_TOKEN`, `SAILPOINT_CLIENT_SECRET`, `AZURE_ACCESS_TOKEN`) are read from environment variables and never printed or written to the report. Install a missing CLI (`aws`, `gcloud`, `kubectl`, `az`, `vault`) with the `toolbelt` skill. Exact calls per platform, including pagination and the exact filters, are in `references/platform-commands.md`.

## Workflow

1. **Fix the scope and the population.** Agree with the user on the systems, the account types (human, service, break-glass, guest) and the reference date. Record the extraction time of every export; a review without a dated population is not auditable. Hash each export (`sha256sum`) for the evidence pack.
2. **Collect read-only.** Pull the sources listed above through `infra level=observe` or the `connectors` tool. For exports larger than a few thousand lines, run `grep` first (`"PolicyName": "AdministratorAccess"`, `cluster-admin`, `"mfa_active","false"`) and read only the matching sections.
3. **Normalise into the review schema.** Produce one CSV with the columns `user,type,privileged,mfa,last_login,manager,status,key_age_days,hr_status,source` (see `examples/accounts.csv`). Join to HR by employee id, not by email: renamed or re-hired people break email joins. Mark `hr_status=leaver` from the HR extract, or supply a one-column leaver list.
4. **Run the account review.**
   ```bash
   python3 scripts/access_review_report.py examples/accounts.csv --as-of 2026-04-01 --leavers examples/leavers.txt
   ```
   Default thresholds: 90 days dormant, 90 days service key age. Exit code 1 means findings exist. Add `--json` for machine output.
5. **Run the SoD check.** Build the matrix from `references/sod-matrix-catalog.md` with the real role names of the organisation, export one row per `user,entitlement` grant, then:
   ```bash
   python3 scripts/sod_conflicts.py examples/entitlements.csv examples/sod-matrix.json
   ```
   Exit code 1 when a high-severity conflict exists; `--fail-on medium` tightens the gate.
6. **Review privileged access by hand.** For each admin population, confirm: named individual accounts only, MFA enforced (phishing-resistant for Global Administrator, AWS root and `cluster-admin`), PIM or just-in-time elevation rather than permanent assignment, PAM vaulting for shared credentials with session recording, and a break-glass procedure with sealed credentials, alerting on use and a quarterly test.
7. **Check the recertification campaign itself.** A campaign counts as evidence only if: every item has a decision (no `NotReviewed`), the reviewer is the manager or the application owner and not the user, denied items were actually removed (compare with the post-campaign export), and auto-approve on timeout is disabled.
8. **Classify and map.** Label each finding with severity and the control references from `references/frameworks-mapping.md`. A privileged leaver still active, an admin without MFA, a high SoD conflict without mitigating control and a root key older than a year are always high.
9. **Propose, never execute.** Draft the remediation list (disable, remove entitlement, rotate key, split role, accept risk with owner and expiry). Execution of a change on a target requires the user's approval of that exact change and goes through `infra level=execute` or the platform's own workflow with a ticket reference.
10. **Assemble the evidence pack** following the checklist in `references/frameworks-mapping.md`, and deliver the report.

## Rules

- Entra `signInActivity` requires an Entra ID P1 licence and the `AuditLog.Read.All` permission; a null `lastSignInDateTime` means "no sign-in since the attribute started being recorded", not "never". Say so in the report.
- PIM: an eligible assignment is not a finding; a permanent active assignment to Global Administrator, Privileged Role Administrator or Security Administrator is. Count both and report the ratio.
- AWS credential report: `password_last_used` equals `no_information` for users created before the field existed and `N/A` when no console password exists. Treat `access_key_1_last_used_date = N/A` with `access_key_1_active = true` as an unused active key.
- AWS root: `aws iam get-account-summary` must show `AccountMFAEnabled = 1` and the credential report's `<root_account>` row must show no active access keys. One missing item is a high finding in every account of the organisation.
- GCP IAM recommender needs 90 days of usage data before it recommends; a project with no recommendation may simply be too young.
- Kubernetes: `kubectl auth can-i --list` shows effective rights for one subject only; iterate over the subjects of `cluster-admin` and `admin` bindings, and over service accounts with `automountServiceAccountToken` in `kube-system`.
- GitHub `?filter=2fa_disabled` only works with an owner token; when the organisation enforces 2FA the list is empty by construction, record the enforcement setting instead.
- Service accounts are never "dormant by MFA": the script skips MFA for `type=service`. Their findings are owner, key age and scope.
- Break-glass accounts are excluded from the dormant check (they are supposed to be unused) but any login on them must match an incident ticket.
- Orphan means no owner recorded, not "manager left the company": handle the second case through the mover process and flag it separately.
- Never paste a full credential report, a Vault token accessor list or a Graph response with `passwordCredentials` into chat or the report. Quote the row of the finding only.
- A SoD conflict with a documented mitigating control (dual approval, independent reconciliation) is reported as an accepted risk with its expiry date, not silently dropped.
- Deprovisioning deadlines: PCI DSS 4.0 req. 8.2.5 expects revocation immediately on termination; most policies set 24 hours. Measure the gap between HR termination date and account disable date for every leaver found.

## Deliverable

Produce a Markdown report with these sections and tables; use `studio-html-report` when the user wants an HTML version.

1. **Scope and population**: table `Source | Extract time | Accounts | Hash`.
2. **Key metrics**: table `Metric | Value | Target` covering total accounts, active, privileged, privileged MFA coverage (%), dormant, orphaned, leavers still active, service keys over threshold, SoD conflicts (high/medium/low), recertification completion (%).
3. **Findings**: table `ID | Check | Severity | User | System | Detail | Control refs (ISO/SOC 2/DORA/NIS2/PCI) | Proposed action | Owner`.
4. **SoD conflicts**: table `User | Severity | Domain | Entitlement A | Entitlement B | Mitigating control | Decision` (straight from `scripts/sod_conflicts.py`).
5. **Privileged access posture**: table `Population | Members | Permanent vs eligible | MFA | PAM vaulted | Session recording | Last review`.
6. **Leaver reconciliation**: table `User | HR termination date | Account disabled date | Gap (days) | Systems still active`.
7. **Exceptions register**: table `User | Entitlement | Reason | Approver | Expiry`.
8. **Evidence pack index**: table `Item | File | Hash | Covers control`.

Close with the remediation list ordered by severity and the next review date per population.

## Resources

- `scripts/access_review_report.py` - dormant, orphan, privileged-without-MFA, stale key, leaver-still-active and break-glass-used checks on a normalised CSV or JSON export; Markdown or `--json`; exit 1 on findings.
- `scripts/sod_conflicts.py` - SoD conflicts per user from an entitlement CSV and a matrix JSON (glob patterns allowed); exit 1 on high conflicts, `--fail-on` to tighten.
- `examples/accounts.csv` - sample normalised population with every account type; the model for step 3.
- `examples/leavers.txt` - sample HR leaver list, one user id per line.
- `examples/entitlements.csv` and `examples/sod-matrix.json` - sample grants and matrix used in step 5.
- `references/sod-matrix-catalog.md` - conflicting pairs by domain (finance, HR, IT, cloud, software delivery) with severity and mitigating controls; open when building the matrix.
- `references/platform-commands.md` - exact Graph, Okta, SailPoint, Saviynt, AWS, GCP, Kubernetes, GitHub and PAM calls with field names and pagination; open during collection.
- `references/frameworks-mapping.md` - control references (ISO 27001:2022 A.5.15 to A.5.18, A.8.2; SOC 2 CC6.1 to CC6.3; DORA art. 9; NIS2 art. 21; PCI DSS 4.0 req. 7 and 8), review cadence and the evidence pack checklist; open at steps 8 and 10.
