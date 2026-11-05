---
name: siem-edr-connector
description: Collect, normalize and correlate security alerts from SIEM and EDR platforms (Splunk SPL and REST search jobs, Elastic Security KQL/EQL/ES|QL and detection rules API, Microsoft Sentinel and Defender for Endpoint KQL, Wazuh API and indexer, CrowdStrike Falcon FQL detects and alerts, SentinelOne Deep Visibility PowerQuery) into ECS or OCSF events, convert Sigma rules per backend, group alerts into incidents per host and user with MITRE ATT&CK tactics and a severity score, and produce a triage report. Use when the user asks to "pull alerts from Splunk", "correlate EDR alerts", "convert this Sigma rule", "normalize Defender events to ECS", "hunt across SIEM and EDR", or shares exported alert JSON from a security console. In French "corréler les alertes SIEM", "récupérer les détections CrowdStrike", "convertir une règle Sigma".
metadata: {"navin":{"emoji":"🛰️","category":"security"}}
---

# SIEM / EDR Connector

## Overview

Collect alerts and telemetry from the SIEM and EDR consoles a customer runs, bring them to one schema (ECS or OCSF), correlate them into incidents per host and user, tag the MITRE ATT&CK tactics and deliver a triage table with a decision per incident. The default posture is read-only and evidence-based: queries, exports and conversions only. Nothing is written to a console (saved search, detection rule, containment, isolation, closing an alert) without the user's explicit approval of the exact call, and every statement in the report cites the query, the event id and the time window that support it.

## When it applies

- A security team shares exported alerts (Splunk `output_mode=json`, Elastic `.alerts-security.alerts-*` hits, Falcon `/detects` summaries, Defender Advanced Hunting CSV/JSON, Wazuh `wazuh-alerts-*`, SentinelOne threats) and asks what happened.
- A hunt must run on several consoles at once and the results must be compared on the same fields.
- A Sigma rule has to be deployed on Splunk, Elastic, Sentinel, Defender, SentinelOne or Wazuh and back-tested first.
- An incident needs a storyline: which host, which user, which tactics, in which order, from which sensor.
- Regulatory context: EU DORA art. 17 (ICT incident classification) and NIS2 art. 23 (24 h early warning, 72 h notification) require time-stamped evidence of detection and triage; the deliverable of this skill is that evidence.

Out of scope: writing detection content from scratch (use Sigma and `references/sigma-workflow.md` instead of ad-hoc rules), active response on endpoints, and threat intelligence enrichment (hand IOCs to the `threat-intelligence` skill).

## Engines and sources

| Platform | Query language | Read endpoints | Auth |
|---|---|---|---|
| Splunk Enterprise / Cloud | SPL | `POST /services/search/jobs` (returns `sid`), `GET /services/search/jobs/{sid}` (poll `dispatchState`), `GET /services/search/jobs/{sid}/results?output_mode=json&count=0`; `exec_mode=oneshot` for small searches | `Authorization: Bearer $SPLUNK_TOKEN` or `Authorization: Splunk <session key>` on port 8089 |
| Elastic Security | KQL (Discover, rules), EQL (`POST /_eql/search`), ES\|QL (`POST /_query`), Query DSL (`POST /<index>/_search`) | alerts in `.alerts-security.alerts-<space>`; rules `GET /api/detection_engine/rules/_find`, `GET /api/detection_engine/rules?rule_id=`; header `kbn-xsrf: true` | `Authorization: ApiKey $ELASTIC_API_KEY` |
| Microsoft Sentinel | KQL | `POST https://api.loganalytics.io/v1/workspaces/{workspaceId}/query` with `{"query","timespan"}`; tables `SecurityAlert`, `SecurityIncident`, `SecurityEvent`, `DeviceProcessEvents` (when Defender connector is on) | Entra ID bearer token, scope `https://api.loganalytics.io/.default`; `az` through `infra` |
| Microsoft Defender for Endpoint | KQL (Advanced Hunting) | `POST https://api.securitycenter.microsoft.com/api/advancedqueries/run` with `{"Query"}`; alerts `GET /api/alerts?$filter=alertCreationTime ge 2026-03-11`; limits 45 calls/min, 10 000 rows | bearer token for `https://api.securitycenter.microsoft.com` |
| Wazuh | rule ids and Lucene/Query DSL on the indexer | `POST /security/user/authenticate` (basic auth, returns JWT, 15 min), `GET /agents`, `GET /rules?search=`; alerts `GET https://<indexer>:9200/wazuh-alerts-*/_search` | `Authorization: Bearer <jwt>` for the manager API (port 55000); indexer basic auth |
| CrowdStrike Falcon | FQL | `POST /oauth2/token` (client credentials), `GET /detects/queries/detects/v1?filter=` then `POST /detects/entities/summaries/GET/v1`, `GET /alerts/queries/alerts/v2?filter=` then `POST /alerts/entities/alerts/v2`, `GET /sensors/entities/datafeed/v2?appId=` (Event Streams), `GET /incidents/queries/incidents/v1` | `$FALCON_CLIENT_ID` / `$FALCON_CLIENT_SECRET`; regional base URL (`api.crowdstrike.com`, `api.eu-1.crowdstrike.com`, `api.us-2.crowdstrike.com`) |
| SentinelOne | PowerQuery (Singularity Data Lake) and legacy Deep Visibility S1QL | `POST /web/api/v2.1/dv/events/pq` then `GET /web/api/v2.1/dv/events/pq-ping?queryId=`, `GET /web/api/v2.1/threats`, `GET /web/api/v2.1/activities` | `Authorization: ApiToken $S1_API_TOKEN` |

Navin integration:

- Cloud consoles (`az sentinel`, `az monitor log-analytics query`, `aws securityhub`, `gcloud scc`) run through the `infra` tool at level `observe` for reads and `prepare` for anything that creates a rule; never raw `exec`.
- When a Navin connector exists for the destination (Slack, Jira, Datadog, Grafana, GitHub), send the triage table or open the ticket with the `connectors` tool instead of the vendor API.
- Tokens come from environment variables (`SPLUNK_TOKEN`, `ELASTIC_API_KEY`, `FALCON_CLIENT_SECRET`, `S1_API_TOKEN`, `WAZUH_PASSWORD`) and never appear in output, logs or the report. Mask them in any copied `curl`.
- A missing CLI (`sigma`, `splunk`, `az`, `jq`) is installed with the `toolbelt` skill.
- Exports above a few megabytes are searched with the `grep` tool (`pattern="lsass|-enc "`) before any `read_file`; never read a raw alert dump into context in full.
- Sigma conversion: `sigma convert -t splunk -p splunk_windows rule.yml`, `-t esql -p ecs_windows`, `-t eql -p ecs_windows`, `-t kusto -p microsoft_xdr`, `-t sentinelone_pq`, `-t crowdstrike_fql`.

## Workflow

1. Frame the question. Record the time window in UTC (consoles differ: Splunk uses the search head timezone, Defender stores UTC, Sentinel displays local time in the portal), the entities of interest (host, user, hash, IP) and the consoles in scope. Confirm which tenant or Falcon CID applies when there are several.
2. Inventory the sources. For each console, verify reachability with a cheap read (`GET /services/server/info`, `GET /api/status`, `GET /sensors/queries/installers/v1?limit=1`, `GET /web/api/v2.1/system/status`, Wazuh `GET /`). A console that cannot be queried is reported as "not assessed", never as "no alerts".
3. Pull the alerts. Use the per-console call from the table above with the smallest field set that still carries host, user, process, hashes, network, rule name, severity and ATT&CK tags; paginate (`offset` for Falcon, `cursor` for SentinelOne, `search_after` for Elastic, `$skip` for Defender alerts, `count`/`offset` for Splunk results) until the API reports no more. Save each export as JSONL under a work folder, one file per console.
4. Normalize. Run `python3 scripts/normalize_events.py examples/mixed_vendor_events.jsonl --output /tmp/normalized.jsonl --json` on the exports (auto-detects CrowdStrike, Defender, Wazuh, Splunk, Elastic, Sentinel and SentinelOne records). For an unlisted product, write a mapping file like `examples/custom_mapping.json` and pass `--mapping`; field names come from `references/field-mapping.md`. Exit code 1 means some records lacked a timestamp or host: inspect `event.normalization_problems` before going on.
5. Correlate. Run `python3 scripts/correlate_alerts.py examples/normalized_alerts.jsonl --window-minutes 30` (on the normalized file of step 4 in a real engagement; `--output /tmp/incidents.md` to write the Markdown). The script deduplicates identical (host, user, rule, minute) alerts, chains alerts of the same host or user while the gap stays under the window, merges host and user chains that share an alert, orders tactics along the kill chain and proposes a decision. Choose the window from the data: 30 minutes for EDR behaviours, 24 hours for authentication abuse, and state the choice in the report.
6. Hunt for the missing context. For each incident whose decision is "needs data", run the matching hunt from `references/query-cheatsheet.md` on the console that holds the raw telemetry (process tree, network, logons) and attach the hit counts. Translate once per console; do not paraphrase a query from memory when the cheat sheet has it.
7. Decide per incident with the severity matrix below, then write the deliverable. When the user asks to deploy a detection that came out of the hunt, follow `references/sigma-workflow.md`: convert, back-test over 7 and 30 days, present the saved-search or rule payload, and wait for approval before the `POST`.

Severity matrix used for the final rating (the script score is an input, not the verdict):

| Condition | Rating |
|---|---|
| Two or more tactics on one entity, one of them credential-access, lateral-movement, command-and-control, exfiltration or impact, with high or critical sensor severity | Critical |
| Two tactics or two independent sensors on the same entity within the window | High |
| One high or critical alert with no corroboration | Medium, "needs data" |
| Single low or informational alert, known benign parent or signed updater | Low, close with note |

Triage decision vocabulary: `true positive` (corroborated by a second source or raw telemetry), `benign true positive` (the behaviour happened and is authorized, name the owner), `false positive` (the rule misfired, name the filter to add), `needs data` (list the query that would settle it).

## Rules

- Correlation keys are the lower-cased short host label and the lower-cased account without domain; Falcon reports NetBIOS names while Defender and Sentinel report FQDNs, and `CORP\j.martin`, `j.martin@corp.example.fr` and `j.martin` are the same user.
- Falcon `/detects/*` is the legacy API; `/alerts/queries/alerts/v2` returns the same detections plus identity and cloud alerts with `composite_id`. Query both only during a migration and dedup on `detection_id`/`composite_id`.
- Defender Advanced Hunting keeps 30 days; Sentinel keeps what the workspace retention says (default 90 days, interactive up to 2 years); Splunk and Elastic depend on index retention. Write the effective retention next to each "no hits" statement.
- A Splunk search job with `exec_mode=normal` must be polled until `dispatchState` is `DONE`; reading `/results` earlier returns a partial set without error. Use `exec_mode=oneshot` under 50 000 results.
- KQL `has` is token-based and fast; `contains` is substring and slow. In Defender, `has_any` on `ProcessCommandLine` is the right primitive for switch lists.
- EQL sequences are the only way to express ordered multi-event logic in Elastic without a correlation layer; `maxspan` is mandatory for a bounded result.
- Wazuh rule levels 12 to 15 are "high importance" and above; level alone is not a verdict, read `rule.mitre` and `data.win.eventdata` fields.
- Sigma `level` maps to the normalized severity (informational, low, medium, high, critical); the correlation script reads tactics from `threat.tactic.name`, so keep the `attack.<tactic>` tags on every rule.
- Never close, suppress, isolate or add an exclusion from this skill: these are state changes on the console and require the user's approval and their own change record (NIS2 art. 21 requires incident handling procedures to be traceable).
- OCSF is the target when the destination is Amazon Security Lake or a data lake; ECS when the destination is Elastic. The normalization script produces ECS-like JSONL; the OCSF column of `references/field-mapping.md` gives the rename for the lake.
- Personal data in alerts (user names, IP addresses, file paths containing names) falls under RGPD; keep exports in the customer's environment, do not paste them into tickets outside it, and redact user names in any report that leaves the SOC.

## Deliverable

A Markdown report with these sections, in this order:

1. Scope: consoles queried, UTC window, entities, retention per console, consoles not assessed.
2. Incident table, one row per correlated incident:

| # | Entity (host / user) | Window UTC | Alerts | Sensors | Max severity | ATT&CK tactics | Techniques | Score | Rating | Decision | Evidence (query, event ids) |
|---|---|---|---|---|---|---|---|---|---|---|---|

3. Hunt results table:

| Hunt | Console | Query | Window | Hits | Interpretation |
|---|---|---|---|---|---|

4. Proposed detections (only when asked): Sigma id, title, backend, pipeline, 7-day and 30-day back-test hits, payload ready for approval.
5. Next actions with an owner and a deadline, each marked read-only or change-requiring-approval.

When the user wants an HTML report, hand the same sections to the `studio-html-report` skill. Keep raw exports out of the report; cite file names and event ids.

## Resources

- `scripts/normalize_events.py` - vendor JSON/JSONL to ECS-like JSONL with built-in mappings for CrowdStrike, Defender, Wazuh, Splunk, Elastic, Sentinel and SentinelOne, or a custom `--mapping` file. Run first on every export. `--json` prints the per-vendor summary.
- `scripts/correlate_alerts.py` - normalized JSONL to incidents (sliding window per host/user, dedup, ATT&CK tactics in kill-chain order, score, decision). `--json` for machine output, `--min-score` to filter, exit 1 when incidents exist.
- `examples/mixed_vendor_events.jsonl` - seven alerts from five vendors describing one intrusion (macro, encoded PowerShell, LSASS dump, C2, pass-the-hash) plus two unrelated alerts; use it to check the pipeline end to end. `examples/normalized_alerts.jsonl` is its normalized output (without `raw`), the input of the correlation sample command.
- `examples/legacy_ids_events.json` and `examples/custom_mapping.json` - a product without a built-in mapping and the mapping file that handles it: `python3 scripts/normalize_events.py examples/legacy_ids_events.json --mapping examples/custom_mapping.json --drop-raw`.
- `references/field-mapping.md` - ECS and OCSF field names per vendor, severity scales, OCSF class selection, parsing gotchas. Open when writing a mapping or translating fields between consoles.
- `references/query-cheatsheet.md` - twelve hunts in SPL, Defender and Sentinel KQL, EQL/ES|QL, FQL, SentinelOne PowerQuery and Wazuh, plus the exact API calls that run them. Open at step 6.
- `references/sigma-workflow.md` - install backends, rule anatomy and modifiers, `sigma convert` commands per target, deployment endpoints, back-test procedure. Open when a detection must be converted or deployed.
