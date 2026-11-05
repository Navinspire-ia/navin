# MITRE ATT&CK mapping and navigator layers

## Identifiers

| Object | Id format | Example |
|---|---|---|
| Tactic | `TA` + 4 digits | `TA0002` Execution |
| Technique | `T` + 4 digits | `T1059` Command and Scripting Interpreter |
| Sub-technique | technique + `.` + 3 digits | `T1059.001` PowerShell |
| Mitigation | `M` + 4 digits | `M1038` Execution Prevention |
| Group | `G` + 4 digits | `G0016` APT29 |
| Software | `S` + 4 digits | `S0154` Cobalt Strike |
| Campaign | `C` + 4 digits | `C0024` SolarWinds Compromise |
| Data source / component | `DS` + 4 digits | `DS0009` Process, component Process Creation |

Enterprise tactics in kill-chain order (STIX `x-mitre-tactic`, `phase_name` is the shortname):

| Id | Name | `phase_name` |
|---|---|---|
| TA0043 | Reconnaissance | `reconnaissance` |
| TA0042 | Resource Development | `resource-development` |
| TA0001 | Initial Access | `initial-access` |
| TA0002 | Execution | `execution` |
| TA0003 | Persistence | `persistence` |
| TA0004 | Privilege Escalation | `privilege-escalation` |
| TA0005 | Defense Evasion | `defense-evasion` |
| TA0006 | Credential Access | `credential-access` |
| TA0007 | Discovery | `discovery` |
| TA0008 | Lateral Movement | `lateral-movement` |
| TA0009 | Collection | `collection` |
| TA0011 | Command and Control | `command-and-control` |
| TA0010 | Exfiltration | `exfiltration` |
| TA0040 | Impact | `impact` |

ATT&CK versions change yearly (v16 October 2024, v17 April 2025, v18 October 2025); a report must state the version used because techniques are deprecated or merged (`x_mitre_deprecated: true`, `revoked: true` with a `revoked-by` relationship).

## STIX representation of ATT&CK

In `enterprise-attack.json` (repository `mitre-attack/attack-stix-data`):

- technique = `attack-pattern` with `external_references[0] = {"source_name": "mitre-attack", "external_id": "T1059.001", "url": "https://attack.mitre.org/techniques/T1059/001"}`, `kill_chain_phases[].kill_chain_name = "mitre-attack"`, `x_mitre_is_subtechnique`, `x_mitre_platforms` (`Windows`, `Linux`, `macOS`, `Office Suite`, `Identity Provider`, `IaaS`, `SaaS`, `Containers`, `ESXi`), `x_mitre_data_sources`, `x_mitre_detection`.
- tactic = `x-mitre-tactic` with `x_mitre_shortname`.
- group = `intrusion-set`; software = `malware` or `tool`; mitigation = `course-of-action`; data component = `x-mitre-data-component`.
- relationships: `intrusion-set uses attack-pattern`, `malware uses attack-pattern`, `course-of-action mitigates attack-pattern`, `x-mitre-data-component detects attack-pattern`, `attack-pattern subtechnique-of attack-pattern`.

Resolve a technique id from any STIX object with
`[r["external_id"] for r in obj["external_references"] if r["source_name"] == "mitre-attack"]`;
this is what `scripts/stix_summary.py` does. Mobile and ICS use
`mitre-mobile-attack` and `mitre-ics-attack` as `source_name`.

## Mapping observations to techniques

Map behaviours, not tools. A tool maps to several techniques; a technique
needs the observed procedure. Decision rules:

| Observation | Technique | Not this |
|---|---|---|
| Office document with macro delivered by e-mail | T1566.001 Spearphishing Attachment, then T1204.002 User Execution: Malicious File | T1566 alone (too coarse when the attachment is known) |
| `powershell -enc` | T1059.001 PowerShell plus T1027.010 Command Obfuscation | T1059 (parent) when the interpreter is known |
| `rundll32 comsvcs.dll MiniDump` on lsass | T1003.001 LSASS Memory plus T1218.011 Rundll32 | T1003 parent |
| NTLM logon type 9 with explicit credentials from a workstation | T1550.002 Pass the Hash | T1078 Valid Accounts unless the account itself was stolen |
| HTTPS beacon to a rare IP every 60 s | T1071.001 Web Protocols plus T1573.002 Asymmetric Cryptography when TLS is attacker-controlled | T1095 (non-application layer) |
| `vssadmin delete shadows` | T1490 Inhibit System Recovery | T1485 Data Destruction |
| Archive created then uploaded to a personal cloud | T1560.001 Archive via Utility, T1567.002 Exfiltration to Cloud Storage | T1041 (that is over the C2 channel) |
| Long DNS TXT queries to one domain | T1071.004 DNS or T1048.003 Exfiltration Over Unencrypted Non-C2 Protocol depending on direction and volume | T1568 (that is resolution of C2, not tunnelling) |
| New scheduled task running from `%TEMP%` | T1053.005 Scheduled Task | T1543 (service) |
| Registry Run key | T1547.001 Registry Run Keys / Startup Folder | T1112 Modify Registry (too generic) |

Confidence per mapping: `high` when the procedure is observed in telemetry
(command line, event), `medium` when inferred from an alert title, `low` when
inferred from a report's prose. Write the confidence next to each technique
in the deliverable.

## Navigator layer (ATT&CK Navigator 4.x / 5.x)

```json
{
  "name": "FIN-Facture intrusion 2026-03",
  "versions": {"attack": "17", "navigator": "5.1.0", "layer": "4.5"},
  "domain": "enterprise-attack",
  "description": "Techniques observed in incident INC-2026-0312, confidence in comments",
  "techniques": [
    {"techniqueID": "T1566.001", "tactic": "initial-access", "score": 100, "color": "#ff6666", "comment": "high: macro in Facture_2026-03.docm", "enabled": true, "showSubtechniques": true},
    {"techniqueID": "T1059.001", "tactic": "execution", "score": 100, "comment": "high: encoded command observed"},
    {"techniqueID": "T1003.001", "tactic": "credential-access", "score": 80, "comment": "medium: Defender title only"},
    {"techniqueID": "T1071.001", "tactic": "command-and-control", "score": 60, "comment": "low: from vendor report"}
  ],
  "gradient": {"colors": ["#ffffff", "#ff6666"], "minValue": 0, "maxValue": 100},
  "legendItems": [{"label": "observed", "color": "#ff6666"}],
  "hideDisabled": false,
  "layout": {"layout": "side", "showID": true, "showName": true}
}
```

Score convention: 100 observed in telemetry, 80 alert title, 60 report prose,
20 assessed likely. Build the layer from the technique list of the deliverable
and upload it in the Navigator (`Open Existing Layer` > `Upload from local`).
MISP produces the same layer with `returnFormat: "attack"` on
`/events/restSearch`; OpenCTI exports it from an intrusion set's `Attack
patterns` tab (`Export` > `ATT&CK navigator`).

## Coverage comparison

To compare the intrusion's techniques to the SOC's detection coverage, build a
second layer from the detection rules (Sigma `attack.t*` tags, Elastic rule
`threat[].technique[].id`, Sentinel `techniques`, Defender custom detection
`mitreTechniques`), then in Navigator `Create Layer from other layers` with
expression `a - b` (observed minus covered) to list the gaps. Each gap becomes
a proposed detection handed to the `siem-edr-connector` skill.
