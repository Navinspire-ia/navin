# MISP and OpenCTI API reference

## MISP REST

Headers on every call: `Authorization: $MISP_KEY`, `Accept: application/json`,
`Content-Type: application/json`. The key is an auth key of a user whose role
decides what is visible (org-only vs. all sharing groups). Base URL
`https://<misp>/`. Never print the key; read it from the environment.

| Need | Call | Body / parameters |
|---|---|---|
| Search attributes (IOCs) | `POST /attributes/restSearch` | `{"returnFormat": "json", "value": "185.220.101.45"}`; filters `type` (`ip-dst`, `domain`, `sha256`, `url`, `email-src`...), `category`, `tags` (`["tlp:amber", "!misp-galaxy:mitre-attack-pattern=%"]`), `from`/`to` (dates), `last` (`7d`), `to_ids` (1), `enforceWarninglist` (1), `includeContext`, `includeDecayScore`, `page`/`limit` |
| Search events | `POST /events/restSearch` | `{"returnFormat": "json", "eventinfo": "%Facture%", "published": 1, "threat_level_id": [1, 2], "searchall": "FactureLoader"}`; `returnFormat` also `stix2`, `csv`, `text`, `suricata`, `snort`, `yara`, `openioc`, `attack` (navigator layer) |
| Export one event as STIX 2.1 | `GET /events/restSearch/returnFormat:stix2/eventid:<id>` or `POST /events/restSearch` with `{"returnFormat": "stix2", "eventid": "<id>"}` | feed the result to `scripts/stix_summary.py` |
| Read an event | `GET /events/view/<id>` | includes `Attribute[]`, `Object[]`, `Galaxy[]`, `Tag[]`, `RelatedEvent[]` |
| Create an event | `POST /events/add` | `{"Event": {"info": "...", "distribution": 0, "threat_level_id": 2, "analysis": 1, "date": "2026-03-12", "Attribute": [{"type": "sha256", "category": "Payload delivery", "value": "...", "to_ids": true, "comment": "..."}], "Tag": [{"name": "tlp:amber"}]}}`; `distribution` 0 org only, 1 community, 2 connected communities, 3 all, 4 sharing group; `analysis` 0 initial, 1 ongoing, 2 complete; `threat_level_id` 1 high, 2 medium, 3 low, 4 undefined |
| Add attributes to an event | `POST /attributes/add/<event_id>` | `{"type": "domain", "value": "api.telemetry-sync.org", "to_ids": true}` |
| Add a MISP object (file, url, email) | `POST /objects/add/<event_id>/<template_uuid>` | template names: `file`, `url`, `email`, `domain-ip`, `network-connection`, `process` |
| Tag an event or attribute | `POST /tags/attachTagToObject` | `{"uuid": "<event or attribute uuid>", "tag": "misp-galaxy:mitre-attack-pattern=\"PowerShell - T1059.001\""}` |
| Publish | `POST /events/publish/<id>` | triggers ZMQ and push to synced instances; this is a sharing action, ask first |
| Warninglists | `GET /warninglists/index`, `POST /warninglists/checkValue` with `{"value": ["8.8.8.8", "microsoft.com"]}` | returns the lists that match (e.g. `List of known google domains`, `Top 1000 website from Alexa`) |
| Sightings | `POST /sightings/add/<attribute_id>` with `{"source": "EDR", "type": 0}` (0 sighting, 1 false positive, 2 expiration) | `GET /sightings/restSearch/attribute` |
| Galaxies (ATT&CK) | `GET /galaxies/index`, `GET /galaxy_clusters/view/<id>`, `POST /galaxies/attachCluster/<event_id>/event` | cluster values look like `PowerShell - T1059.001`; the `attack` return format of restSearch builds a navigator layer |
| Feeds | `GET /feeds/index`, `POST /feeds/fetchFromFeed/<id>` | ingestion changes data, ask first |
| Decaying | `POST /attributes/restSearch` with `"includeDecayScore": 1, "decayingModel": <id>` | score below the model threshold means the IOC is aged out |

MISP attribute types to STIX 2.1 mapping (as produced by `misp-stix`):

| MISP type | STIX 2.1 |
|---|---|
| `ip-dst`, `ip-src` | `ipv4-addr` / `ipv6-addr`, pattern `[ipv4-addr:value = '...']`; with `ip-dst|port` -> `network-traffic` |
| `domain`, `hostname` | `domain-name` |
| `domain|ip` | `domain-name` with `resolves_to_refs` |
| `url`, `uri`, `link` | `url` (`link` becomes an external reference, not an indicator) |
| `md5`, `sha1`, `sha256`, `filename|sha256` | `file` with `hashes` |
| `email-src`, `email-dst` | `email-addr`; `email-subject`, `email-attachment` -> `email-message` |
| `mutex` | `mutex` |
| `regkey`, `regkey|value` | `windows-registry-key` |
| `yara`, `sigma`, `snort` | `indicator` with `pattern_type` `yara`/`sigma`/`snort` |
| `vulnerability` | `vulnerability` with a `cve` external reference |
| `threat-actor` | `threat-actor` |
| galaxy `mitre-attack-pattern` | `attack-pattern` with `mitre-attack` external reference |
| tag `tlp:*` | the standard TLP `marking-definition` |

`to_ids` is the MISP flag that says "this value is safe to feed detection";
attributes with `to_ids: 0` are context and must not be pushed to a blocklist.

## OpenCTI GraphQL

Endpoint `POST https://<opencti>/graphql`, headers `Authorization: Bearer $OPENCTI_TOKEN`,
`Content-Type: application/json`. Pagination is Relay-style (`first`, `after`,
`edges { node }`, `pageInfo { endCursor hasNextPage }`). The Python client
`pycti` (`from pycti import OpenCTIApiClient`) wraps these queries
(`client.indicator.list(filters=...)`, `client.stix_core_relationship.list(...)`,
`client.stix_cyber_observable.create(...)`, `client.stix2.import_bundle_from_json(...)`);
use it in an environment where it is installed, the bundled scripts stay
standard-library.

Indicators by pattern or validity:

```graphql
query Indicators($after: ID) {
  indicators(first: 100, after: $after,
    filters: {mode: and, filters: [
      {key: "pattern_type", values: ["stix"]},
      {key: "valid_until", operator: gt, values: ["2026-03-12T00:00:00.000Z"]},
      {key: "x_opencti_score", operator: gte, values: ["60"]}
    ], filterGroups: []}) {
    edges { node {
      id standard_id name pattern pattern_type valid_from valid_until revoked
      confidence x_opencti_score x_opencti_detection
      indicator_types killChainPhases { kill_chain_name phase_name }
      objectMarking { definition definition_type }
      objectLabel { value }
      createdBy { name }
    } }
    pageInfo { endCursor hasNextPage }
  }
}
```

Observables (SCOs) and their indicators:

```graphql
query Observables {
  stixCyberObservables(first: 50, search: "185.220.101.45") {
    edges { node {
      id standard_id entity_type observable_value x_opencti_score
      ... on IPv4Addr { value }
      ... on DomainName { value }
      ... on StixFile { hashes { algorithm hash } name }
      indicators { edges { node { id name pattern valid_until } } }
      objectMarking { definition }
    } }
  }
}
```

Relationships around an entity (what an intrusion set uses, what an indicator indicates):

```graphql
query Rels($id: String) {
  stixCoreRelationships(first: 200, fromId: $id,
    relationship_type: ["uses", "indicates", "targets", "attributed-to"]) {
    edges { node {
      id relationship_type start_time stop_time confidence
      from { ... on BasicObject { id entity_type } ... on StixDomainObject { ... on IntrusionSet { name } ... on Malware { name } ... on Indicator { name } } }
      to   { ... on BasicObject { id entity_type } ... on StixDomainObject { ... on AttackPattern { name x_mitre_id } ... on Malware { name } } }
    } }
  }
}
```

Sightings: `stixSightingRelationships(fromId: $indicatorId) { edges { node { attribute_count first_seen last_seen to { ... on Identity { name } } } } }`.

Export to STIX bundle: `query { stixDomainObject(id: "<id>") { toStix } }` returns
the object; a full bundle export goes through `mutation { stixDomainObjectsExportAsk(format: "application/json", exportType: "full", ...) }`
then the file list on the entity. Imports: `mutation { stixBundlePush(connectorId: "...", bundle: "<json string>") }`
or the pycti `import_bundle_from_json`. Both are writes: present the bundle
and wait for approval.

OpenCTI-specific fields: `x_opencti_score` (0 to 100, decays with the
indicator decay rules), `x_opencti_detection` (boolean, equivalent of MISP
`to_ids`), `x_opencti_main_observable_type`, `x_mitre_id` on attack patterns,
`x_opencti_workflow_id` (status). The score, not `confidence`, drives the
export to detection tools in OpenCTI.

## Enrichment sources and limits

| Source | Call | Key | Free-tier limits |
|---|---|---|---|
| VirusTotal v3 | `GET https://www.virustotal.com/api/v3/files/<sha256>`, `/ip_addresses/<ip>`, `/domains/<domain>`, `/urls/<base64url(url)>`; relationships `/files/<hash>/contacted_domains` | `x-apikey: $VT_API_KEY` | 4 requests/min, 500/day, 15.5k/month; no file download, no retro-hunt |
| Shodan | `GET https://api.shodan.io/shodan/host/<ip>?key=$SHODAN_API_KEY`, `GET /dns/domain/<domain>` | query parameter | 1 request/second; search credits per plan |
| abuse.ch URLhaus | `POST https://urlhaus-api.abuse.ch/v1/url/` (`url=`), `/host/` (`host=`), `/payload/` (`sha256_hash=`) | `Auth-Key: $ABUSECH_API_KEY` (required since 2025) | fair use, bulk feeds at `https://urlhaus.abuse.ch/downloads/csv_recent/` |
| abuse.ch MalwareBazaar | `POST https://mb-api.abuse.ch/api/v1/` (`query=get_info&hash=<sha256>`) | `Auth-Key` | same; sample download requires `query=get_file` |
| abuse.ch ThreatFox | `POST https://threatfox-api.abuse.ch/api/v1/` (`{"query": "search_ioc", "search_term": "..."}`) | `Auth-Key` | IOCs carry `confidence_level`, `malware`, `threat_type` |
| GreyNoise | `GET https://api.greynoise.io/v3/community/<ip>` (noise/riot verdict), `GET /v2/noise/context/<ip>` (enterprise) | `key: $GREYNOISE_API_KEY` | community: 50 lookups/day; says whether the IP is a mass scanner or a known benign service (RIOT) |
| AbuseIPDB | `GET https://api.abuseipdb.com/api/v2/check?ipAddress=<ip>&maxAgeInDays=90` | `Key: $ABUSEIPDB_API_KEY` | 1 000 checks/day free; `abuseConfidenceScore` 0 to 100 |
| MISP warninglists | `POST /warninglists/checkValue` | MISP key | local, no limit |
| MITRE ATT&CK | static bundle `enterprise-attack.json`, TAXII `https://attack-taxii.mitre.org/api/v21/` | none | none |

Rate-limit handling: respect `Retry-After` on HTTP 429, batch hashes before
domains before IPs (hashes are the most decisive and the cheapest), and cache
answers per IOC for the duration of the investigation.
