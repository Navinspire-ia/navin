# Microsoft Purview, Google Cloud DLP, AWS Macie, Nightfall, gitleaks

## Microsoft Purview (Microsoft 365 E5 / E5 Compliance)

Concepts: sensitivity labels (classification applied to files, mails, sites,
Teams; can encrypt and watermark), sensitive information types (SIT, the
detectors), trainable classifiers, DLP policies (locations Exchange,
SharePoint, OneDrive, Teams, Devices for Endpoint DLP, Power BI, on-premises
scanner, Defender for Cloud Apps), Insider Risk Management (sequence and
cumulative exfiltration detections), Data Security Posture Management for AI.

Read configuration with the Security & Compliance PowerShell module (read-only
cmdlets, run through the user's session; `Connect-IPPSSession` first):

```powershell
Get-Label | Select DisplayName, Priority, ContentType, EncryptionEnabled
Get-LabelPolicy | Select Name, Labels, ExchangeLocation, SharePointLocation
Get-DlpCompliancePolicy | Select Name, Mode, Workload, Enabled
Get-DlpComplianceRule -Policy "Confidential - block external" | Select Name, ContentContainsSensitiveInformation, BlockAccess, NotifyUser, GenerateIncidentReport
Get-DlpSensitiveInformationType | Where-Object { $_.Publisher -ne "Microsoft Corporation" }   # custom SITs
Get-DlpDetailReport -StartDate 2026-03-01 -EndDate 2026-03-13 -Source Exchange,SharePoint,OneDrive,EndpointDevices
```

DLP events in the Unified Audit Log and in Defender XDR Advanced Hunting:

```kusto
CloudAppEvents
| where ActionType in ("DlpRuleMatch", "DLPRuleMatch")
| extend Policy = tostring(RawEventData.PolicyDetails[0].PolicyName), Rule = tostring(RawEventData.PolicyDetails[0].Rules[0].RuleName)
| extend SIT = tostring(RawEventData.PolicyDetails[0].Rules[0].ConditionsMatched.SensitiveInformation[0].SensitiveInformationTypeName)
| summarize Matches = count() by AccountDisplayName, Application, Policy, Rule, SIT
| order by Matches desc
```

Endpoint DLP (devices onboarded to Defender for Endpoint) activities, with
the label and SIT of the file: `DeviceEvents | where ActionType startswith "Dlp"`
(e.g. `DlpFileCopiedToRemovableMedia`, `DlpFileUploadedToCloud`,
`DlpFilePrinted`, `DlpSensitiveDataPastedToBrowser`). Restricted actions in
policy: `Copy to clipboard`, `Copy to USB removable media`, `Copy to network
share`, `Print`, `Upload to a restricted cloud service domain`, `Paste to
supported browsers`, `Copy to a remote desktop session`, `Access by restricted
apps`.

Policy design rules:

- Start in `TestWithNotifications` mode (`-Mode TestWithNotifications`) for
  two weeks, read `Get-DlpDetailReport`, then switch to `Enable`.
- One rule per severity: `low` on 1 to 9 instances of a SIT (`-ContentContainsSensitiveInformation @{Name="Credit Card Number"; minCount="1"; maxCount="9"}`), `high` above 10 with `BlockAccess $true` and `GenerateIncidentReport`.
- Labels encrypt (`EncryptionEnabled`) only when the recipients list is
  stable; encryption breaks downstream DLP inspection in third-party CASBs.
- Policy changes are made by the user in the compliance portal or with
  `New-DlpCompliancePolicy`/`Set-DlpComplianceRule` after approval; never from
  this skill without a written payload review.

## Google Cloud Sensitive Data Protection (Cloud DLP)

API `dlp.googleapis.com`, enabled with `gcloud services enable dlp.googleapis.com`
(through `infra`). Scopes `https://www.googleapis.com/auth/cloud-platform`.

Inspect content inline:

```
POST https://dlp.googleapis.com/v2/projects/{project}/locations/{location}/content:inspect
{
  "inspectConfig": {
    "infoTypes": [{"name": "FRANCE_NIR"}, {"name": "IBAN_CODE"}, {"name": "CREDIT_CARD_NUMBER"}, {"name": "EMAIL_ADDRESS"}, {"name": "PHONE_NUMBER"}, {"name": "AWS_CREDENTIALS"}, {"name": "JSON_WEB_TOKEN"}],
    "minLikelihood": "POSSIBLE",
    "includeQuote": false,
    "limits": {"maxFindingsPerRequest": 1000},
    "customInfoTypes": [{
      "infoType": {"name": "SIRET"},
      "regex": {"pattern": "\\b\\d{3} ?\\d{3} ?\\d{3} ?\\d{5}\\b"},
      "likelihood": "POSSIBLE",
      "detectionRules": [{"hotwordRule": {"hotwordRegex": {"pattern": "(?i)siret"}, "proximity": {"windowBefore": 50}, "likelihoodAdjustment": {"fixedLikelihood": "VERY_LIKELY"}}}]
    }],
    "ruleSet": [{"infoTypes": [{"name": "EMAIL_ADDRESS"}], "rules": [{"exclusionRule": {"regex": {"pattern": ".+@exemple-client\\.fr"}, "matchingType": "MATCHING_TYPE_FULL_MATCH"}}]}]
  },
  "item": {"value": "<text up to 0.5 MB>"}
}
```

Response: `result.findings[]` with `infoType.name`, `likelihood`
(`VERY_UNLIKELY` to `VERY_LIKELY`), `location.byteRange`, `quote` when
`includeQuote` is true (keep it false for Restricted data). Related calls:
`content:deidentify` (masking, `cryptoReplaceFfxFpeConfig` tokenisation),
`dlpJobs` with `storageConfig` for Cloud Storage buckets and BigQuery tables
(`inspectJob.storageConfig.cloudStorageOptions.fileSet.url`), `jobTriggers` for
scheduled scans, and the discovery service (`projects.locations.discoveryConfigs`)
that profiles every BigQuery table and bucket with a sensitivity score.
Built-in French infoTypes: `FRANCE_CNI`, `FRANCE_NIR`, `FRANCE_PASSPORT`,
`FRANCE_TAX_IDENTIFICATION_NUMBER`, `FRANCE_DRIVERS_LICENSE_NUMBER`. Pricing is
per byte inspected: scan a 1 % sample (`bytesLimitPerFilePercent`) before a
full job.

gcloud equivalents (through `infra`): `gcloud dlp info-types list --filter="name~FRANCE"`,
`gcloud dlp jobs list --filter="state=DONE" --location=europe-west1`.

## AWS Macie

Macie discovers sensitive data in S3 (managed data identifiers: `CREDIT_CARD_NUMBER`,
`AWS_CREDENTIALS`, `PRIVATE_KEY`, `JSON_WEB_TOKEN`, `FRANCE_NATIONAL_IDENTIFICATION_NUMBER`,
`FRANCE_PASSPORT_NUMBER`, `FRANCE_TAX_IDENTIFICATION_NUMBER`, `PHONE_NUMBER`,
`EMAIL_ADDRESS`...; custom identifiers are regex with keywords and ignore words)
and reports findings (`SensitiveData:S3Object/Personal`, `/Financial`,
`/Credentials`, `/Multiple`; `Policy:IAMUser/S3BucketPublic`,
`/S3BucketSharedExternally`, `/S3BucketEncryptionDisabled`). Through `infra` at
`observe`:

```
aws macie2 get-macie-session
aws macie2 list-findings --finding-criteria '{"criterion":{"severity.description":{"eq":["High"]},"updatedAt":{"gte":1772323200000}}}'
aws macie2 get-findings --finding-ids <id>
aws macie2 describe-buckets --criteria '{"publicAccess.effectivePermission":{"eq":["PUBLIC"]}}'
aws macie2 list-classification-jobs --filter-criteria '{"includes":{"and":[{"simpleCriterion":{"comparator":"EQ","key":"jobStatus","values":["COMPLETE"]}}]}}'
```

Creating a job (`create-classification-job`) or enabling automated discovery
is `prepare` then `execute` after approval; Macie bills per GB scanned.
Findings also arrive in Security Hub and EventBridge (`aws.macie` source) for
routing to the SIEM.

## Nightfall

SaaS DLP for Slack, Google Drive, GitHub, Jira, Confluence, Salesforce and a
scan API: `POST https://api.nightfall.ai/v3/scan` with
`{"policy": {"detectionRules": [{"detectors": [{"detectorType": "NIGHTFALL_DETECTOR", "nightfallDetector": "CREDIT_CARD_NUMBER", "minConfidence": "LIKELY", "minNumFindings": 1}], "logicalOp": "ANY"}]}, "payload": ["<text>"]}`,
header `Authorization: Bearer $NIGHTFALL_API_KEY`. Findings return
`detector.name`, `confidence`, `location.byteRange`, `redactedFinding` when a
`redactionConfig` is set. Built-in detectors include `IBAN_CODE`,
`FRANCE_NATIONAL_IDENTIFICATION_NUMBER`, `API_KEY`, `CRYPTOGRAPHIC_KEY`,
`PASSWORD_IN_CODE`. Rate limit 10 requests/second on the scan API. The
file-scan API is asynchronous (`/v3/upload`, webhook result).

## gitleaks (secrets in repositories)

Install with the `toolbelt` skill. Commands, all read-only:

```bash
gitleaks detect --source . --redact --report-format json --report-path gitleaks.json   # full history
gitleaks detect --source . --no-git --redact                                           # working tree only
gitleaks protect --staged --redact                                                     # pre-commit
gitleaks detect --source . --log-opts="--since=2026-03-01" --redact                   # recent commits
```

Report fields: `RuleID` (`aws-access-token`, `github-pat`, `private-key`,
`jwt`, `slack-bot-token`, `stripe-access-token`, `generic-api-key`), `File`,
`StartLine`, `Commit`, `Author`, `Date`, `Secret` (redacted with `--redact`),
`Entropy`, `Fingerprint` (for `.gitleaksignore`). A secret in any commit of the
history is compromised even if a later commit removed it; the remediation is
rotation, then history rewrite (`git filter-repo`) only when the repository is
private and all forks are controlled. GitHub secret scanning push protection
(`Settings > Code security > Push protection`) blocks the same patterns at
push time; check its status with the `connectors` GitHub tool
(`GET /repos/{owner}/{repo}` field `security_and_analysis.secret_scanning_push_protection.status`).
