# STIX 2.1 cheat sheet

Specification: OASIS STIX Version 2.1 (Committee Specification 03, 2021).
Every object carries `type`, `spec_version: "2.1"`, `id` (`<type>--<uuid4>`),
`created`, `modified` (RFC 3339 with milliseconds, UTC `Z`). SDOs and SROs
also accept `created_by_ref`, `revoked`, `labels`, `confidence` (0 to 100),
`lang`, `external_references`, `object_marking_refs`, `granular_markings`.

## Domain objects (SDO) used in CTI exchange

| Type | Required fields | Notes |
|---|---|---|
| `indicator` | `pattern`, `pattern_type`, `valid_from` | `pattern_type`: `stix`, `pcre`, `sigma`, `snort`, `suricata`, `yara`; `indicator_types`: `anomalous-activity`, `anonymization`, `benign`, `compromised`, `malicious-activity`, `attribution`, `unknown`; `valid_until` optional but required for aging |
| `malware` | `is_family` | `malware_types` open vocabulary (`ransomware`, `downloader`, `backdoor`, `remote-access-trojan`...); `sample_refs` point to `file` SCOs |
| `threat-actor` | `name` | `threat_actor_types`, `aliases`, `sophistication`, `resource_level`, `primary_motivation` |
| `intrusion-set` | `name` | the cluster analysts track over time (APT28, FIN7); an actor `attributed-to` it |
| `campaign` | `name` | `first_seen`, `last_seen`, `objective` |
| `attack-pattern` | `name` | ATT&CK techniques: `external_references[{"source_name": "mitre-attack", "external_id": "T1059.001"}]`, `kill_chain_phases[{"kill_chain_name": "mitre-attack", "phase_name": "execution"}]` |
| `course-of-action` | `name` | mitigations (ATT&CK `M1038`) |
| `identity` | `name`, `identity_class` | `individual`, `group`, `organization`, `class`, `system`, `unknown`; `sectors` (`financial-services`, `healthcare`...) |
| `infrastructure` | `name` | `infrastructure_types`: `command-and-control`, `botnet`, `exfiltration`, `phishing`, `hosting-malware` |
| `tool` | `name` | legitimate software used offensively (PsExec, Mimikatz) |
| `vulnerability` | `name` | `external_references[{"source_name": "cve", "external_id": "CVE-2023-36884"}]` |
| `report` | `name`, `published`, `object_refs` | the wrapper for a finished intelligence product |
| `note`, `opinion` | `content` / `opinion` | analyst commentary; `opinion` values `strongly-disagree` to `strongly-agree` |
| `grouping` | `context`, `object_refs` | unfinished set, `context`: `suspicious-activity`, `malware-analysis`, `unspecified` |
| `location` | one of `region`, `country`, `latitude`+`longitude` | `country` is ISO 3166-1 alpha-2 |

## Relationship objects (SRO)

| Object | Fields | Common `relationship_type` values |
|---|---|---|
| `relationship` | `source_ref`, `target_ref`, `relationship_type`, `start_time`, `stop_time` | `indicates` (indicator -> malware/tool/infrastructure/attack-pattern/campaign/intrusion-set/threat-actor), `uses` (actor/malware/campaign -> attack-pattern/tool/malware/infrastructure), `attributed-to` (campaign/intrusion-set -> threat-actor/intrusion-set), `targets` (-> identity/location/vulnerability), `mitigates` (course-of-action -> attack-pattern/malware), `communicates-with` (malware/infrastructure -> ipv4-addr/domain-name/url), `based-on` (indicator -> observed-data), `derived-from`, `duplicate-of`, `related-to` (generic, last resort) |
| `sighting` | `sighting_of_ref` (indicator, malware, actor...), `where_sighted_refs` (identity/location), `observed_data_refs`, `first_seen`, `last_seen`, `count`, `summary` | a sighting is evidence that the thing was seen; never use a `relationship` for that |

## Cyber observable objects (SCO)

`ipv4-addr`, `ipv6-addr`, `domain-name`, `url`, `email-addr`, `email-message`,
`file` (`hashes` with keys `MD5`, `SHA-1`, `SHA-256`, `SHA-512`, `SSDEEP`),
`directory`, `windows-registry-key`, `process`, `mutex`, `network-traffic`,
`user-account`, `x509-certificate`, `autonomous-system`, `mac-addr`,
`software`, `artifact`. SCO ids are deterministic UUIDv5 in 2.1 (namespace
`00abedb4-aa42-466c-9c01-fed23315a9b7` over the id-contributing properties),
which is how MISP and OpenCTI deduplicate observables.

## Pattern language (pattern_type `stix`)

```
[file:hashes.'SHA-256' = '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08']
[ipv4-addr:value = '185.220.101.45'] OR [domain-name:value = 'api.telemetry-sync.org']
[url:value MATCHES '^https?://cdn-update-service\\.net/assets/.*\\.(ps1|zip)$']
[network-traffic:dst_ref.type = 'ipv4-addr' AND network-traffic:dst_ref.value ISSUBSET '45.153.160.0/24' AND network-traffic:dst_port = 443]
[email-message:from_ref.value = 'billing@invoice-portal-secure.com' AND email-message:subject LIKE '%Facture%']
[windows-registry-key:key = 'HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' AND windows-registry-key:values[*].data LIKE '%svchost32%']
[process:command_line MATCHES '(?i)powershell.* -enc '] FOLLOWEDBY [network-traffic:dst_port = 443] WITHIN 300 SECONDS
[file:name = 'svchost32.exe'] REPEATS 3 TIMES
```

Operators: `=`, `!=`, `>`, `<`, `>=`, `<=`, `IN (...)`, `LIKE` (SQL `%`, `_`),
`MATCHES` (PCRE), `ISSUBSET`, `ISSUPERSET`. Observation operators `AND`, `OR`,
`FOLLOWEDBY`; qualifiers `WITHIN n SECONDS`, `START t STOP t`, `REPEATS n TIMES`.
String constants use single quotes; backslashes are doubled. Hash keys with a
dash must be quoted: `hashes.'SHA-256'`.

## Markings

TLP 1.0 marking-definition ids are fixed by the specification (section 7.2.1.4):

| TLP 1.0 | Id |
|---|---|
| WHITE | `marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9` |
| GREEN | `marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da` |
| AMBER | `marking-definition--f88d31f6-486f-44da-b317-01333bde0b82` |
| RED | `marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed` |

TLP 2.0 (FIRST, 2022) adds CLEAR (replaces WHITE) and AMBER+STRICT. OASIS
publishes them as an extension (`extension-definition--60a3c5c5-0d10-413e-aab3-9e08dde9e88d`,
`tlp_2_0` values `clear`, `green`, `amber`, `amber+strict`, `red`); OpenCTI
ships them with the ids listed in `scripts/stix_summary.py`. A `statement`
marking carries copyright or handling text. `granular_markings` restrict a
marking to selected properties (`selectors: ["pattern"]`).

Sharing rules (TLP 2.0 definitions):

| Label | May be shared with |
|---|---|
| TLP:CLEAR | anyone, no restriction (copyright still applies) |
| TLP:GREEN | the community (peers, partner organisations) but not public channels |
| TLP:AMBER | the recipient organisation and its clients, need-to-know |
| TLP:AMBER+STRICT | the recipient organisation only |
| TLP:RED | the named recipients only, often verbal; never into a shared platform |

## Bundle and TAXII 2.1

A bundle is `{"type": "bundle", "id": "bundle--<uuid4>", "objects": [...]}`
with no `spec_version` at bundle level. TAXII 2.1 serves bundles-like envelopes
(`{"more": bool, "next": "...", "objects": [...]}`):

```
GET /taxii2/                                  discovery: api_roots
GET /<api-root>/collections/                  list collections (id, title, can_read, can_write)
GET /<api-root>/collections/<id>/objects/?added_after=2026-03-01T00:00:00Z&limit=100&match[type]=indicator
GET /<api-root>/collections/<id>/objects/?next=<cursor>
GET /<api-root>/collections/<id>/manifest/    ids, versions, media types without the objects
POST /<api-root>/collections/<id>/objects/    push (body: envelope), returns a status resource
Accept: application/taxii+json;version=2.1
```

Polling rule: keep the `X-TAXII-Date-Added-Last` header of the last page and
use it as the next `added_after`; `match[version]=last` avoids receiving every
historical version. MITRE ATT&CK is served at
`https://attack-taxii.mitre.org/api/v21/` (collection `x-mitre-collection`
ids per domain) and as static bundles in the `mitre-attack/attack-stix-data`
GitHub repository (`enterprise-attack/enterprise-attack.json`).

## Validation

`stix2validator bundle.json --strict` (Python package `stix2-validator`,
install with the `toolbelt` skill) checks ids, timestamps, vocabularies and
pattern syntax. `scripts/stix_summary.py` is the stdlib pre-check for
structure, validity windows, ATT&CK references and markings.
