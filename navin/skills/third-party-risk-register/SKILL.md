---
name: third-party-risk-register
description: Build and maintain the ICT third-party risk register (DORA register of information templates B_01 to B_07 with LEI, contract reference, function criticality, substitutability, exit strategy, sub-contracting, data location), tier vendors (critical, important, standard), judge due diligence evidence (SOC 2 Type II, ISO 27001 scope, pentest, RGPD art. 28 DPA, BCP/DR, cyber insurance), check contract clauses (audit rights, 24h incident notification, SLA, termination assistance), measure concentration risk, track provider incidents and ratings (SecurityScorecard, BitSight), compute review dates. Use when the user asks to "build the DORA register", "assess this vendor", "which contracts expire soon", "check our supplier concentration", "review the vendor contract clauses", or shares a supplier list or SOC 2 report. In French "registre des prestataires", "risque fournisseur", "plan de sortie".
metadata: {"navin":{"emoji":"🤝","category":"security"}}
---

# Third-Party Risk Register

## Overview

Keep one authoritative register of ICT third-party arrangements and use it to decide where the organisation is exposed: which providers support critical or important functions, which contracts and certificates are about to lapse, which providers cannot be replaced, where spend and data concentrate, and which vendors are overdue for a re-assessment. The register follows the DORA register of information structure (Commission Implementing Regulation (EU) 2024/2956) so that a regulated financial entity can report it, and the same structure serves NIS2 supply chain security (art. 21(2)(d)), ISO/IEC 27036 and the EBA outsourcing guidelines for everyone else. The default posture is read-only and evidence-based: every verdict cites a document with its date and scope, the register is the user's data and is never changed without the user's approval of the exact edit, and no provider or authority is contacted on the user's behalf.

## When it applies

- A DORA register of information must be created, completed or validated before the yearly submission to the competent authority (ACPR, AMF, BaFin, CSSF, DNB).
- Onboarding a new vendor: tiering, due diligence, contract clause review.
- Periodic review: contract and certificate expiries, stale assessments, exit plan tests.
- Board or audit questions: concentration on a hyperscaler, dependence on a non-substitutable core system, providers hosting personal data outside the EEA.
- A provider incident, breach news, rating drop or sub-processor change notice arrives and the user asks what is affected.
- Any supplier list (CSV, XLSX export from Coupa, SAP Ariba, ServiceNow VRM, OneTrust, Prevalent) or a SOC 2 report is shared for analysis.

## Engines and sources

| Need | Source | Access |
|---|---|---|
| Provider legal identity | GLEIF `GET https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=<name>` or `?filter[lei]=<lei>` | public, no token |
| Spend and contracts | Procurement or contract tool export (Coupa, Ariba, DocuSign CLM, ServiceNow Vendor Risk `GET /api/now/table/sn_vdr_risk_asmt_assessment`) | user export or API token in `SNOW_TOKEN` |
| Existing register | CSV or JSON in the DORA vocabulary; large exports searched with `grep` (`grep "critical"`, `grep "not substitutable"`) before reading | file |
| Due diligence documents | Trust centers (AWS Artifact, Microsoft Service Trust Portal, Google Compliance Reports Manager), vendor portals, SharePoint or Google Drive through the `connectors` tool | user access |
| External ratings | SecurityScorecard `GET https://api.securityscorecard.io/companies/{domain}` (`SSC_API_KEY`), BitSight `GET https://api.bitsighttech.com/ratings/v1/companies/{guid}` (`BITSIGHT_API_TOKEN`) | tokens from environment, never printed |
| Vulnerability and breach watch | NVD `GET https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch=<product>`, CISA KEV JSON feed, provider status pages | public |
| Cloud footprint check | `infra level=observe`: `aws organizations list-accounts`, `aws ce get-cost-and-usage --group-by Type=DIMENSION,Key=SERVICE`, `az account list`, `gcloud billing accounts list` | read-only cloud CLIs |
| Incident and ticket records | `connectors` tool: Jira, ServiceNow, Slack incident channels | user workspace |
| Register validation and scoring | `scripts/register_check.py`, `scripts/vendor_risk_score.py` | local, standard library |

Install a missing CLI (`aws`, `az`, `gcloud`) with the `toolbelt` skill. Never run cloud CLIs through raw exec.

## Workflow

1. **Establish the inventory.** Start from procurement spend, not from the IT list: every supplier invoiced for an ICT service (hosting, SaaS, software licence, telecom, security service, outsourced development, data feeds) is a candidate. Reconcile with cloud billing (`infra level=observe`) and with the DPA list from the DPO. Record the contracting legal entity and look up its LEI; a hyperscaler contracted through AWS EMEA SARL (LU) or Microsoft Ireland Operations Ltd (IE) has a different LEI and country than the parent.
2. **Map functions and criticality.** For each arrangement, name the business function it supports and whether that function is critical or important (DORA art. 3(22): a disruption would materially impair financial performance, continuity of services or regulatory compliance). The function owner decides, not IT. Attach RTO and RPO. One function may rely on several arrangements; one arrangement may serve several functions.
3. **Tier and score vendors.** Fill the attribute JSON (see `examples/vendors.json`) and run:
   ```bash
   python3 scripts/vendor_risk_score.py examples/vendors.json --as-of 2026-04-01
   ```
   The script weights data classification, function criticality, access level, substitutability, data volume and sub-contracting depth into an inherent score (1 to 4), rates control effectiveness 0 to 100 from the evidence held, computes the residual score and assigns a tier (critical: 12-month cadence, important: 24, standard: 36). Exit code 1 when a critical vendor keeps a residual score of 3.0 or more.
4. **Collect due diligence by tier.** Request the evidence listed in `references/due-diligence-evidence.md` and judge each document on scope, date and findings. Record the verdict and the document date in the register; a SOC 2 report whose system description does not name the service bought is not evidence.
5. **Review the contract clauses.** Score the DORA art. 30 clauses and the RGPD art. 28 clauses with `references/contract-clauses.md`. Note notice periods, incident notification delay, audit rights, data location commitments, sub-contracting conditions and termination assistance, and copy the values into the register fields.
6. **Check the register.**
   ```bash
   python3 scripts/register_check.py examples/register.csv --as-of 2026-04-01
   ```
   The script reports missing fields per tier, invalid LEIs, contracts and SOC 2 or ISO certificates expiring within 90 days (`--expiry-days`), critical or important vendors without exit plan, with untested exit plan or without an assessment in the last 365 days (`--assessment-max-age-days`), providers declared not substitutable, incident notification above 24h, data outside the EEA and adequate countries, and concentration above 30% of spend by provider and by country (`--concentration-pct`). Exit code 1 means at least one blocking gap.
7. **Analyse concentration beyond spend.** Count critical functions per provider and per sub-contractor (several SaaS vendors on the same hyperscaler region is a hidden concentration), list providers with no identified alternative, and record the result in template B_07.01 (alternative providers identified).
8. **Build or test exit plans.** Every critical or important arrangement needs an exit strategy (DORA art. 28(8)): data export format, termination assistance duration, alternative provider or in-house option, estimated duration and cost. Critical exit plans are tested yearly, at least as a tabletop.
9. **Link incidents.** Maintain a provider incident register: provider, date detected, date notified by the provider, clause deadline, impact on functions, regulatory report filed (DORA art. 19 major incident, NIS2 art. 23, RGPD art. 33), remediation. A notification later than the contract clause is a contract finding.
10. **Set monitoring.** Register rating sources, CVE watch keywords and status pages per critical vendor; define the triggers from `references/due-diligence-evidence.md` and the next review date per tier.
11. **Deliver and propose.** Produce the deliverable below. Edits to the register, contract renegotiation requests or termination notices are proposed as a list; the user approves each one before anything is written to the register file or sent through a connector.

## Rules

- The register row is the contractual arrangement, not the vendor: one provider with three contracts yields three rows in B_02.01 and one row in B_05.01.
- Criticality attaches to the function (B_06.01), and the arrangement inherits it; never tier a vendor as "standard" because its invoice is small when it supports a critical function (DNS, certificate authority, identity provider).
- Annual cost goes in the reporting currency with the last financial year's invoiced amount; for a new contract, use the contractual annual value and flag it as an estimate.
- Sub-contractors are listed only when they effectively underpin a critical or important function (RTS on sub-contracting); a cleaning company at the provider's data centre is out of scope, the provider's cloud host is in.
- An ISO 27001:2013 certificate is void since 31 October 2025; accept only ISO/IEC 27001:2022 and verify the scope statement names the product and sites.
- A SOC 2 report older than 12 months plus a bridge letter of more than 3 months is a finding, not evidence.
- Data location means every region used including backups and support access; "EU" is not a country code, record each ISO 3166-1 alpha-2 code.
- A US provider certified under the EU-US Data Privacy Framework is adequate for the transfer; the certification must be checked on the DPF list and recorded with the date.
- The 24-hour incident notification expectation applies to the provider notifying the entity; it is distinct from the entity's own 4-hour (DORA art. 19 initial notification) and 24-hour (NIS2 early warning) obligations, which depend on that provider notice.
- A provider refusing audit rights for a critical function is a blocking finding (DORA art. 30(3)(e)); pooled audits and certifications can reduce, not replace, the right.
- Register validity is continuous: a signature, renewal, termination or criticality change updates the register within the month, not at the yearly submission.
- Do not store or quote API tokens, full SOC 2 reports or contract texts in the deliverable; cite document title, date, section and the finding only.

## Deliverable

Produce a Markdown report with these sections; use `studio-html-report` when the user wants an HTML report.

1. **Register summary**: table `Metric | Value` with arrangements, providers, critical / important / standard counts, total annual cost, blocking / major / minor gaps.
2. **Gaps**: table `Check | Severity | Vendor | Tier | Contract ref | Detail | Register field | Proposed action | Owner | Due date` (from `scripts/register_check.py`).
3. **Vendor scores**: table `Vendor | Tier | Inherent | Controls | Residual | Last assessment | Next review | Control gaps` (from `scripts/vendor_risk_score.py`).
4. **Concentration**: tables `Provider | Annual cost | Share | Critical functions supported | Alternative identified` and `Country | Annual cost | Share | Arrangements`.
5. **Expiry calendar**: table `Date | Vendor | Item (contract, SOC 2, ISO 27001, DPA) | Days left | Action`.
6. **Exit plans**: table `Vendor | Function | Substitutability | Exit plan | Last test | Termination assistance (months) | Alternative`.
7. **Contract clause review**: table `Vendor | Clause (art. 30 ref) | Score (0-2) | Gap | Renegotiation date`.
8. **Provider incidents**: table `Date | Vendor | Impacted function | Notified after (h) | Clause (h) | Regulatory report | Status`.
9. **Monitoring plan**: table `Vendor | Tier | Rating source | CVE keywords | Status page | Next review`.

Finish with the proposed register edits as a list the user can approve line by line.

## Resources

- `scripts/register_check.py` - completeness per tier, LEI format, expiries, exit plans, assessment age, substitutability, notification delay, data location and concentration checks on a CSV or JSON register; exit 1 on blocking gaps; `--json` output.
- `scripts/vendor_risk_score.py` - inherent risk x control effectiveness scoring, tier assignment, cadence and next review date from vendor attribute JSON; exit 1 on a critical vendor with residual 3.0 or more.
- `examples/register.csv` - sample register in the DORA vocabulary with critical, important and standard arrangements and deliberate gaps.
- `examples/vendors.json` - sample vendor attribute file for the scoring script.
- `references/dora-register-fields.md` - templates B_01 to B_07, closed lists (service types S01 to S19, substitutability, reliance), field gotchas, reporting calendar and the NIS2 and EBA correspondences; open at steps 1, 2 and 6.
- `references/due-diligence-evidence.md` - evidence to request per tier, how to judge SOC 2 and ISO 27001 documents, continuous monitoring signals with API endpoints and thresholds, re-assessment cadence; open at steps 4 and 10.
- `references/contract-clauses.md` - DORA art. 30(2) and 30(3) clauses, art. 28(7) termination rights, RGPD art. 28 DPA clauses and the 0-2 scoring; open at step 5.
