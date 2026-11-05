# Field mapping: vendor events to ECS and OCSF

Use this table when writing a custom mapping for `scripts/normalize_events.py`
or when a query must be translated between consoles. ECS names follow
Elastic Common Schema 8.x; OCSF names follow the OCSF 1.x `Detection Finding`
(class_uid 2004) and `Process Activity` (class_uid 1007) classes.

## Core entity fields

| Concept | ECS | OCSF | Splunk CIM | CrowdStrike Falcon (detects v1 / alerts v2) | Microsoft Defender (Advanced Hunting) | Wazuh (wazuh-alerts-*) | SentinelOne (threats API) | Sentinel SecurityAlert |
|---|---|---|---|---|---|---|---|---|
| Event time | `@timestamp` | `time` (epoch ms) | `_time` | `created_timestamp` / `timestamp` | `Timestamp` | `timestamp` | `threatInfo.createdAt` | `TimeGenerated` |
| Host name | `host.name` | `device.hostname` | `dest` / `host` | `device.hostname` | `DeviceName` | `agent.name` | `agentRealtimeInfo.agentComputerName` | `CompromisedEntity` |
| Host id | `host.id` | `device.uid` | `dest_nt_host` | `device.device_id` | `DeviceId` | `agent.id` | `agentRealtimeInfo.agentUuid` | (in `Entities` JSON) |
| Host IP | `host.ip` | `device.ip` | `dest_ip` | `device.local_ip` | `LocalIP` | `agent.ip` | `agentRealtimeInfo.agentIpV4` | (Entities) |
| User | `user.name` | `actor.user.name` | `user` | `behaviors[].user_name` | `AccountName` | `data.win.eventdata.targetUserName` | `threatInfo.processUser` | `AccountName` (parsed) |
| User domain | `user.domain` | `actor.user.domain` | `user_domain` | `behaviors[].user_id` (SID) | `AccountDomain` | `data.win.eventdata.targetDomainName` | - | - |
| Process name | `process.name` | `actor.process.name` | `process_name` | `behaviors[].filename` | `FileName` | `data.win.eventdata.image` | `threatInfo.originatorProcess` | - |
| Command line | `process.command_line` | `actor.process.cmd_line` | `process` | `behaviors[].cmdline` | `ProcessCommandLine` | `data.win.eventdata.commandLine` | - | - |
| Parent process | `process.parent.name` | `actor.process.parent_process.name` | `parent_process_name` | `behaviors[].parent_details.parent_process_graph_id` | `InitiatingProcessFileName` | `data.win.eventdata.parentImage` | - | - |
| SHA-256 | `process.hash.sha256` / `file.hash.sha256` | `actor.process.file.hashes[].value` (algorithm_id 3) | `file_hash` | `behaviors[].sha256` | `SHA256` | `data.win.eventdata.hashes` (parse `SHA256=`) | `threatInfo.sha256` | - |
| Source IP | `source.ip` | `src_endpoint.ip` | `src_ip` | `behaviors[].ioc_value` when `ioc_type=ipv4` | `LocalIP` | `data.srcip` | - | - |
| Destination IP | `destination.ip` | `dst_endpoint.ip` | `dest_ip` | - | `RemoteIP` | `data.dstip` | - | - |
| Destination port | `destination.port` | `dst_endpoint.port` | `dest_port` | - | `RemotePort` | `data.dstport` | - | - |
| Rule name | `rule.name` | `finding_info.title` | `signature` | `behaviors[].display_name` | `Title` (AlertInfo) | `rule.description` | `threatInfo.threatName` | `AlertName` |
| Rule id | `rule.id` | `finding_info.uid` | `signature_id` | `behaviors[].behavior_id` | `AlertId` | `rule.id` | `threatInfo.classification` | `SystemAlertId` |
| Severity | `event.severity` (int) | `severity_id` (1 info, 2 low, 3 medium, 4 high, 5 critical, 6 fatal) | `severity` | `max_severity` (0-100), `max_severity_displayname` | `Severity` (Informational/Low/Medium/High) | `rule.level` (0-15) | `threatInfo.confidenceLevel` (malicious/suspicious) | `AlertSeverity` |
| Tactic | `threat.tactic.name` | `attacks[].tactic.name` | - | `behaviors[].tactic` | `Category` | `rule.mitre.tactic[]` | `indicators[].tactics[].name` | `Tactics` |
| Technique id | `threat.technique.id` | `attacks[].technique.uid` | - | `behaviors[].technique_id` | `AttackTechniques` (JSON array string) | `rule.mitre.id[]` | `indicators[].tactics[].techniques[].name` | `Techniques` |
| Status | `event.outcome` | `status` / `status_id` | `action` | `status` (new, in_progress, true_positive, false_positive, ignored, closed) | `Status` (New, InProgress, Resolved) | - | `threatInfo.incidentStatus` | `Status` |
| Vendor | `observer.vendor` | `metadata.product.vendor_name` | `vendor_product` | constant `CrowdStrike` | constant `Microsoft` | constant `Wazuh` | constant `SentinelOne` | `VendorName` |

## Severity normalisation

| Normalized label | ECS `event.severity` | OCSF `severity_id` | Wazuh level | Falcon `max_severity` | Defender | Elastic `kibana.alert.severity` |
|---|---|---|---|---|---|---|
| informational | 1 | 1 | 0 to 2 | - | Informational | - |
| low | 2 | 2 | 3 to 6 | 1 to 39 | Low | low |
| medium | 3 | 3 | 7 to 9 | 40 to 69 | Medium | medium |
| high | 4 | 4 | 10 to 12 | 70 to 89 | High | high |
| critical | 5 | 5 | 13 to 15 | 90 to 100 | - | critical |

## OCSF class selection

| Source record | OCSF class | class_uid | Notes |
|---|---|---|---|
| EDR detection, SIEM alert | Detection Finding | 2004 | `finding_info`, `evidences[]`, `attacks[]` |
| Process start (Sysmon 1, Defender `ProcessCreated`) | Process Activity | 1007 | `activity_id` 1 = Launch |
| Network connection (Sysmon 3, `DeviceNetworkEvents`) | Network Activity | 4001 | `activity_id` 6 = Traffic |
| Authentication (4624/4625, `LogonSuccess`) | Authentication | 3002 | `activity_id` 1 = Logon, `status_id` 1 success 2 failure |
| File write (Sysmon 11, `FileCreated`) | File System Activity | 1001 | `activity_id` 1 = Create |
| DNS query (Sysmon 22, `DnsConnectionInspected`) | DNS Activity | 4003 | `query.hostname`, `answers[]` |

## Gotchas

- Falcon `device.hostname` is the NetBIOS short name; Defender `DeviceName` is
  the FQDN. Correlate on the lower-cased short label (the script does this).
- Defender `AttackTechniques` is a JSON array serialised as a string in the
  Advanced Hunting export (`["T1003.001"]`): `parse_json()` it in KQL or
  `json.loads` it after export.
- Wazuh `data.win.eventdata.hashes` carries `SHA1=...,MD5=...,SHA256=...,IMPHASH=...`
  in one string; split on `,` then `=`.
- Splunk `_time` is epoch seconds as a string when exported with
  `output_mode=json`; `_raw` is kept and should be dropped before sharing.
- Elastic alert documents live in `.alerts-security.alerts-<space>` and carry
  the original event fields at the root plus `kibana.alert.*`; the original
  index is in `kibana.alert.ancestors[].index`.
- SentinelOne threat `confidenceLevel` is a verdict, not a severity: map
  `malicious` to high and `suspicious` to medium, then raise with the
  `threatInfo.analystVerdict` when present.
