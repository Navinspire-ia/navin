---
name: data-dlp-monitor
description: Classify data and monitor for leaks and exfiltration. Finds personal, financial and secret data with validated detectors (IBAN mod 97, French NIR key, SIREN/SIRET and payment card Luhn, e-mail, French phone, AWS keys, JWT, private keys, RGPD article 9 vocabulary), proposes Public/Internal/Confidential/Restricted tiers, reviews Microsoft Purview DLP policies and labels, Google Cloud DLP infoTypes, AWS Macie, Nightfall and gitleaks results, and detects exfiltration signals (volume baseline per user, off-hours, new destination, archive then upload, DNS tunneling, personal cloud, LLM prompts) with a response playbook aligned on RGPD, CNIL, NIS2 and DORA. Use when the user asks to "scan this folder for personal data", "classify these files", "check our DLP policies", "find exfiltration in these transfer logs", "is there a secret in this repo", or shares an export that may contain sensitive data. In French "classifier les donnees", "detecter une fuite de donnees", "verifier les politiques DLP".
metadata: {"navin":{"emoji":"🔏","category":"security"}}
---

# Data Classification and DLP Monitor

## Overview

Find where sensitive data lives, say how sensitive it is, verify that the controls on each egress channel match that sensitivity, and detect the behaviours that precede a leak. The default posture is read-only and evidence-based: scan copies, read policies and logs, compute baselines, and report counts and redacted samples. Nothing is blocked, quarantined, labeled, deleted or rotated, and no DLP policy is created or changed, without the user's approval of the exact action; raw sensitive values never appear in the deliverable.

## When it applies

- A folder, bucket, share, database export or repository must be inventoried for personal data (RGPD art. 4), special categories (art. 9), banking and card data, or secrets before a migration, an audit or a records-of-processing update (art. 30).
- A DLP program must be reviewed: Purview labels and policies, Cloud DLP jobs, Macie findings, CASB upload rules, gitleaks in CI.
- Transfer logs (proxy, CASB, mail trace, EDR file events, CloudTrail) must be analysed for exfiltration by an insider, a compromised account or malware.
- An incident needs the data qualified: which types, how many subjects, which tier, so the DPO can decide on CNIL notification (art. 33, 72 hours) and NIS2 or DORA reporting.
- A team wants to know what may be pasted into a public LLM, committed to GitHub or sent to a personal address, and what control enforces it.

Out of scope: legal advice on the lawful basis of a processing (the DPO decides; the skill supplies the facts) and endpoint forensics beyond the file events.

## Engines and sources

| Engine | Interface | Use |
|---|---|---|
| Bundled detectors | `scripts/classify_scan.py` (regex plus checksum validators, stdlib) | first pass on any text-like copy: CSV, JSON, logs, SQL dumps, Markdown, code, `.env` |
| Bundled egress analysis | `scripts/egress_baseline.py` on a CSV `timestamp,user,destination,bytes,channel[,filename]` | z-score per user, new destination, off-hours, personal cloud, archive, DNS volume, bursts |
| Microsoft Purview | Security & Compliance PowerShell `Get-Label`, `Get-LabelPolicy`, `Get-DlpCompliancePolicy`, `Get-DlpComplianceRule`, `Get-DlpDetailReport`; Advanced Hunting `CloudAppEvents` (`DlpRuleMatch`) and `DeviceEvents` (`Dlp*` actions) | labels, SITs, policy modes, Endpoint DLP restrictions, match volumes |
| Google Cloud Sensitive Data Protection | `POST https://dlp.googleapis.com/v2/projects/{project}/locations/{location}/content:inspect` with `inspectConfig.infoTypes` (`FRANCE_NIR`, `IBAN_CODE`, `CREDIT_CARD_NUMBER`, `EMAIL_ADDRESS`, `AWS_CREDENTIALS`, `JSON_WEB_TOKEN`), `dlpJobs`, `jobTriggers`, discovery profiles; `gcloud dlp` | inspection of GCS and BigQuery, sampling, de-identification |
| AWS Macie | `aws macie2 list-findings`, `get-findings`, `describe-buckets`, `list-classification-jobs`; managed identifiers `FRANCE_NATIONAL_IDENTIFICATION_NUMBER`, `CREDIT_CARD_NUMBER`, `AWS_CREDENTIALS`, `PRIVATE_KEY` | S3 discovery and bucket exposure |
| Nightfall | `POST https://api.nightfall.ai/v3/scan` with `detectionRules`, `Authorization: Bearer $NIGHTFALL_API_KEY` | SaaS DLP (Slack, Drive, GitHub, Jira) and ad-hoc scans |
| gitleaks | `gitleaks detect --source . --redact --report-format json --report-path gitleaks.json`, `gitleaks protect --staged` | secrets across git history and in pre-commit |
| Telemetry for exfiltration | Defender for Cloud Apps `CloudAppEvents` (`FileUploaded`), Exchange `MessageTrace` and Unified Audit Log (`New-InboxRule`), Zscaler/Netskope proxy logs, Sysmon 11/22, CloudTrail `GetObject`, DNS resolver logs, GitHub audit log `git.push` | inputs to the egress CSV |

Navin integration:

- `aws macie2`, `gcloud dlp`, `az` and `kubectl` commands run through the `infra` tool at level `observe`; creating a Macie job, a Cloud DLP job trigger or a bucket policy is `prepare` then `execute` after approval.
- Purview and Exchange cmdlets run in the user's own PowerShell session; the skill supplies the exact command and reads the pasted output.
- Slack, GitHub, Jira, Notion and Gmail are reached with the `connectors` tool (for example GitHub `security_and_analysis.secret_scanning_push_protection.status`, Slack delivery of the report).
- API keys (`NIGHTFALL_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `AWS_PROFILE`) come from environment variables and are never printed.
- Missing CLIs (`gitleaks`, `aws`, `gcloud`, `jq`) are installed with the `toolbelt` skill.
- Large exports (a 2 GB proxy log, a SQL dump) are searched with the `grep` tool (`pattern="FR76|AKIA|BEGIN .*PRIVATE KEY"`) and sampled before any `read_file`; the scanner's `--max-size-kb` skips files above the limit so the agent decides how to sample them.

## Workflow

1. Define the scope and the copies. List the locations (paths, buckets, repositories, mailboxes, log sources), the period and the legal context (which processing, which DPO, which contract). Work on read-only copies or exports; never scan production databases directly without the owner's agreement, and never move Restricted data out of the customer's environment.
2. Scan for data types. Run `python3 scripts/classify_scan.py examples/sample_data --redact --output /tmp/classification.md` on the copies (`--json` for machine output, `--include` to pick globs). The script reports counts per type and file, the legal basis of each type, a proposed tier and one masked sample per type when `--redact` is passed; without `--redact` no value at all is printed. Exit 1 means Confidential or Restricted data is present.
3. Confirm the tier with the owner. Apply the tier matrix below; a bulk dataset (more than 50 distinct NIR, IBAN or PAN, or more than 1 000 data subjects) is Restricted regardless of individual types. Record the data owner, retention and the processing in the records (art. 30) for each Restricted or Confidential location.
4. Map channels and controls. For each location, list the channels through which the data can leave (`references/channels-controls.md`) and check the control that exists on each: Purview policy mode and Endpoint DLP restrictions, CASB upload rules, USB device control, auto-forward settings, push protection, LLM domain blocks. Read the policies with the cmdlets and queries in `references/purview-gcp-dlp.md`. A control in `TestWithNotifications` mode or a policy without the relevant SIT is "not enforced".
5. Compare detectors. Where a vendor engine is in place, run the same sample through it (Cloud DLP `content:inspect`, Macie job on a sample prefix, Purview SIT test in the compliance portal) and compare with `references/detectors.md`; list the types the vendor misses (SIRET, SIREN, special-category vocabulary) and the custom SIT or infoType that closes the gap.
6. Build the egress baseline. Export transfers for the period to a CSV with `timestamp,user,destination,bytes,channel[,filename]` (channel values: `email`, `saas_upload`, `usb`, `cloud_storage`, `llm_prompt`, `git_push`, `print`, `dns`, `other`) and run `python3 scripts/egress_baseline.py examples/transfers.csv --output /tmp/egress.md` (`--json`, `--z-threshold 3.0`, `--min-history 5`, `--work-hours 7-20`, `--personal-domains` for customer-specific additions). Correlate the top anomalies by hand with HR events (leavers, movers) and the change calendar (migrations, backups).
7. Qualify each anomaly: sanctioned job, policy violation without malice, insider exfiltration, compromised account or malware. For the last three, follow the response playbook in `references/channels-controls.md`: preserve evidence, qualify the data with the scanner, propose containment actions for approval, and give the DPO the facts for the art. 33 decision.
8. Write the deliverable; hand detection gaps (new destination alerting, DNS volume rule, inbox-rule alert) to the `siem-edr-connector` skill as proposed rules, and the secrets found in repositories to rotation by their owners.

Tier matrix:

| Tier | Content | Handling |
|---|---|---|
| Public | published material, no personal data | no restriction |
| Internal | business data without personal data, public-register identifiers (SIREN, SIRET), internal documentation | inside the organisation; no personal cloud |
| Confidential | directly identifying personal data (name with e-mail, phone, address, date of birth), IBAN, contracts, source code | need-to-know, encryption in transit, label applied, DLP policy in audit or block |
| Restricted | secrets and keys, payment card PAN, NIR, health, union, religion, sexual orientation, biometric data (art. 9), criminal data (art. 10), bulk personal datasets | named access, encryption at rest and in transit, DLP block on every channel, logging of access, no copy outside the controlled environment |

## Rules

- Checksum validation is mandatory before counting a numeric identifier: a 14-digit SIRET passes the payment-card regex and often Luhn; the scanner resolves this by matching SIRET first and never reusing a span. Apply the same order when writing custom SITs or infoTypes (hotword rules, exclusion rules).
- Count distinct values and data subjects, not matches; the notification decision depends on subjects.
- Test values from documentation (`4111 1111 1111 1111`, `AKIAIOSFODNN7EXAMPLE`, `FR76 3000 6000 0112 3456 7890 189`) are reported in code repositories because they show a pattern, and marked as known samples in the table.
- A secret found anywhere in git history is compromised even when a later commit removed it: the fix is rotation, then history rewrite only if the repository is private and every fork is controlled.
- Special-category vocabulary alone (a health policy document) is not art. 9 data; the same words next to an identifier in a record are. Report the context, not the word count.
- Purview `TestWithNotifications` and Cloud DLP `inspect` without a job trigger do not prevent anything; the deliverable says "detects" or "blocks", never "protects".
- Encryption by a label breaks downstream inspection by CASBs and mail gateways; when a label encrypts, verify that the egress inspection is done before encryption (Exchange transport) or by Purview itself.
- An egress baseline needs at least 5 days of history per user; the first days of a new user are learning days, not anomalies. A z-score computed on a user with a constant daily volume is infinite on the first change: the script reports it as `z=inf` with the ratio, read the volume column.
- Off-hours are evaluated in UTC by the script; convert the `--work-hours` window from the users' local timezone (France: 06-19 UTC in winter, 05-18 UTC in summer for a 07:00 to 20:00 day).
- Personal data in the transfer logs themselves (user names, destinations) is a processing: keep the CSV in the customer's environment and delete it at the end of the engagement per the agreed retention.
- Notification deadlines: RGPD art. 33 within 72 hours of awareness to the CNIL (teleservice on cnil.fr), art. 34 to the persons when the risk is high; NIS2 art. 23 early warning in 24 hours, incident notification in 72 hours, final report in one month; DORA art. 19 initial notification within 4 hours of classification as major and 24 hours of awareness. The report gives the timestamps that start these clocks.

## Deliverable

A Markdown report with these sections:

1. Scope: locations, copies used, period, owners and DPO, tools queried, locations not assessed.
2. Classification table:

| Location | Proposed tier | Data types (type x distinct count) | Data subjects (estimate) | Legal basis / regulation | Owner | Existing label | Action proposed |
|---|---|---|---|---|---|---|---|

3. Channel control matrix:

| Channel | Tier required | Control in place | Mode (block / audit / none) | Evidence (policy name, cmdlet output) | Gap |
|---|---|---|---|---|---|

4. Exfiltration anomalies:

| Score | User | Day | Destination | Channel | Volume | Signals | Qualification (sanctioned / violation / insider / compromise) | Evidence (CSV lines, log ids) | Next step |
|---|---|---|---|---|---|---|---|---|---|

5. Secrets found: rule id, file, commit, author, age, rotation owner, push-protection status.
6. Notification assessment: facts for the DPO (types, subjects, encryption state, destination, awareness timestamp) and the applicable deadlines.
7. Proposed changes awaiting approval: policy payloads, blocks, rotations, each with owner and rollback.

When the user wants an HTML report, hand the same sections to the `studio-html-report` skill. Samples in the report are redacted (`FR76****89`), never full values.

## Resources

- `scripts/classify_scan.py` - scan a file or folder with the validated detectors, propose a tier per file, `--redact` masked samples, `--json`, exit 1 on Confidential or Restricted. Run at step 2 and during incident qualification.
- `scripts/egress_baseline.py` - transfers CSV to scored anomalies with reasons (volume z-score per user, new destination, off-hours, personal cloud or LLM destination, archive or encrypted container, USB size, DNS volume, bursts); Markdown or `--json`, exit 1 when anomalies exist. Run at step 6.
- `examples/sample_data/` - four files for the scanner: `examples/sample_data/clients_export.csv` (IBAN, NIR including a Corsican 2B number, e-mails, phones, art. 9 mentions), `examples/sample_data/fournisseurs.md` (SIREN and SIRET), `examples/sample_data/deploy_notes.txt` (AWS key pair, JWT, GitHub token, test card, private key) and `examples/sample_data/release_notes.md` (public, no detection).
- `examples/transfers.csv` - 140 transfers for three users over two weeks with a planted off-hours upload of archives to Google Drive and Mega, a DNS tunneling day, a large LLM prompt and a chunked WeTransfer burst.
- `references/detectors.md` - regex and validation for every type, checksum code, mapping to Purview SITs, Cloud DLP infoTypes, Macie identifiers and gitleaks rules, false-positive tuning. Open at steps 2 and 5.
- `references/channels-controls.md` - channel by channel telemetry, preventive and detective controls, behavioural signals with thresholds, response playbook with notification duties. Open at steps 4 and 7.
- `references/purview-gcp-dlp.md` - Purview cmdlets and KQL, Endpoint DLP actions and policy design, Cloud DLP `content:inspect` body with custom infoTypes and hotwords, Macie CLI, Nightfall scan API, gitleaks commands and report fields. Open at steps 4 and 5.
