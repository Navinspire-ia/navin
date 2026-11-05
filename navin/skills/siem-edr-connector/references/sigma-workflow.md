# Sigma workflow: write, convert, deploy, back-test

## Install

Use the `toolbelt` skill when `sigma` is missing: `pipx install sigma-cli`
then the backends needed for the consoles present:

```bash
sigma plugin install splunk        # SPL
sigma plugin install elasticsearch # Lucene, ES|QL, EQL (-t lucene / -t esql / -t eql)
sigma plugin install microsoft365defender  # Kusto for Defender (-t kusto)
sigma plugin install sentinelone   # PowerQuery (-t sentinelone_pq)
sigma plugin install crowdstrike   # Falcon (-t crowdstrike_fql)
sigma plugin install opensearch    # Wazuh indexer (-t opensearch_lucene)
sigma plugin list
```

Clone the public rule set once: `git clone --depth 1 https://github.com/SigmaHQ/sigma`
(rules in `rules/`, emerging threats in `rules-emerging-threats/`, threat
hunting in `rules-threat-hunting/`).

## Rule anatomy

```yaml
title: Suspicious Encoded PowerShell Launched By Office
id: 3e4f0b57-9d7f-4a4c-8d8e-1c2b3a4d5e6f   # uuid4, never reuse
status: experimental                       # experimental, test, stable, deprecated
description: Office process spawning powershell with an encoded command.
references:
  - https://attack.mitre.org/techniques/T1059/001/
author: SOC Team
date: 2026-03-12
tags:
  - attack.execution
  - attack.t1059.001
  - attack.initial-access
  - attack.t1566.001
logsource:
  category: process_creation
  product: windows
detection:
  selection_parent:
    ParentImage|endswith:
      - '\WINWORD.EXE'
      - '\EXCEL.EXE'
      - '\POWERPNT.EXE'
  selection_child:
    Image|endswith: '\powershell.exe'
    CommandLine|contains:
      - ' -enc '
      - ' -EncodedCommand '
  filter_updater:
    CommandLine|contains: 'OfficeClickToRun'
  condition: selection_parent and selection_child and not filter_updater
falsepositives:
  - Office add-ins that bootstrap through PowerShell
level: high
```

Rules of the format that matter in conversion:

- `logsource` drives field names and index selection through the pipeline;
  `category: process_creation` + `product: windows` is the most portable.
- Modifiers: `|contains`, `|startswith`, `|endswith`, `|re`, `|all`,
  `|cidr`, `|base64offset|contains` (matches base64-encoded substrings at the
  three alignments), `|windash` (matches `-` and `/` switches).
- `condition` supports `and`, `or`, `not`, `1 of selection_*`, `all of them`.
  Sigma correlation rules (`correlation.type: event_count | value_count |
  temporal | temporal_ordered`) exist since specification 2.0 and convert only
  on backends that support them (Splunk, ES|QL, Kusto).
- `tags` use lower-case `attack.<tactic>` and `attack.t<id>`; the correlation
  script reads the tactic from the converted alert, so keep the tag.

## Convert

```bash
# Splunk, with the Windows TA field names (Image -> process_name, etc.)
sigma convert -t splunk -p splunk_windows rule.yml
# Splunk data model (tstats) version
sigma convert -t splunk -p splunk_windows -p splunk_cim_dm -f data_model rule.yml
# Elastic
sigma convert -t esql -p ecs_windows rule.yml
sigma convert -t eql -p ecs_windows rule.yml
sigma convert -t lucene -p ecs_windows -f siem_rule_ndjson rule.yml > rule.ndjson
# Microsoft Defender for Endpoint / Sentinel
sigma convert -t kusto -p microsoft_xdr rule.yml
sigma convert -t kusto -p sentinel_asim rule.yml
# SentinelOne, CrowdStrike
sigma convert -t sentinelone_pq rule.yml
sigma convert -t crowdstrike_fql -p crowdstrike_falcon rule.yml
# Whole folder, skip rules the backend cannot express
sigma convert -t splunk -p splunk_windows --skip-unsupported rules/windows/process_creation/
```

Check the rule before converting: `sigma check rule.yml` (schema and
duplicate ids). `--without-pipeline` is a debugging aid only: never deploy a
query converted without the pipeline, the field names will not match.

## Deploy

| Platform | How the converted query is deployed |
|---|---|
| Splunk | `POST /servicesNS/nobody/search/saved/searches` with `name`, `search`, `cron_schedule`, `alert_type=number of events`, `alert_comparator=greater than`, `alert_threshold=0`, `actions=notable` when Enterprise Security is present |
| Elastic Security | `POST /api/detection_engine/rules` with the ndjson from `-f siem_rule_ndjson`, or `POST /api/detection_engine/rules/_import` (multipart) for bulk; `kbn-xsrf: true` header |
| Sentinel | `PUT /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.OperationalInsights/workspaces/{ws}/providers/Microsoft.SecurityInsights/alertRules/{ruleId}?api-version=2023-02-01` with `kind: Scheduled`, `query`, `queryFrequency`, `queryPeriod`, `tactics`, `techniques`; drive through `infra` (az CLI: `az sentinel alert-rule create`) |
| Defender XDR | custom detection: `POST https://graph.microsoft.com/beta/security/rules/detectionRules` with `queryCondition.queryText`, `schedule.period` (`1H`, `3H`, `12H`, `24H`, `0` for continuous) |
| SentinelOne | STAR rule: `POST /web/api/v2.1/cloud-detection/rules` with `data.s1ql`, `data.severity`, `data.treatAsThreat` |
| Wazuh | rules stay in Wazuh format: translate the logic into `<rule id="100xxx" level="12">` in `/var/ossec/etc/rules/local_rules.xml`; use `wazuh-logtest` to test before `systemctl restart wazuh-manager` |

Deployment is a change on the target: present the converted query, the schedule
and the expected volume, and wait for the user's approval before any `POST`,
`PUT` or restart.

## Back-test before enabling

1. Run the converted query over the last 7 and 30 days with the hit count only
   (`| stats count by host` in SPL, `| summarize count() by DeviceName` in KQL).
2. More than 50 hits/day on a `level: high` rule means a missing filter: list
   the top 10 command lines or parents and add `filter_*` selections instead
   of lowering the level.
3. Zero hits is not proof of silence: fire the matching Atomic Red Team test
   (`Invoke-AtomicTest T1059.001 -TestNumbers 1`) on a lab host, in an
   authorized environment only, and confirm the alert arrives.
4. Record the rule id, the Sigma id, the backend, the pipeline and the
   back-test numbers in the deliverable table so the next review can reproduce
   the measurement.

## Maintenance

- Pin the SigmaHQ commit used; re-run `sigma convert` on the diff of
  `git log --since` to pick up filter improvements.
- A rule marked `status: deprecated` names its replacement in `related:` with
  `type: obsoletes`; retire the old saved search in the same change.
- Keep local rules in a separate folder with their own `id` namespace and a
  `custom.` tag so the two sets never collide.
