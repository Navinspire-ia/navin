# Due diligence evidence by tier

Ask only for what the tier needs, judge what arrives against the criteria
below, and record the verdict (accepted, accepted with finding, rejected) with
the document date in the register. A document is evidence only when its scope
covers the service actually bought.

## What to request

| Evidence | Critical | Important | Standard | How to judge it |
|---|---|---|---|---|
| SOC 2 Type II report (AICPA TSC) | Yes, yearly | Yes, yearly | Bridge letter or Type I accepted | Period covers at least 6 months and ended less than 12 months ago; system description names the service and the regions used; opinion unqualified; read section 4 exceptions and the CUECs (complementary user entity controls) which become obligations for the entity; sub-service organisations carved out must be covered by their own report |
| ISO/IEC 27001:2022 certificate | Yes | Yes | Optional | Certificate in validity (3-year cycle, yearly surveillance), issuing body accredited (COFRAC, UKAS, DAkkS, ANAB); the scope statement must include the data centres, product and legal entity in the contract; ask for the Statement of Applicability when the scope is generic |
| Penetration test summary | Yes, less than 12 months | Yes, less than 24 months | No | Independent tester, scope includes the production tenant or application in use, findings with CVSS and remediation status; refuse a one-page attestation without scope |
| DPA under RGPD art. 28 | Yes when personal data | Yes when personal data | Yes when personal data | Signed by both parties; sub-processor list with notification mechanism; transfer tool for non-EEA (SCC 2021/914 module 2 or 3, adequacy); audit right; deletion at end of service; breach notification to the controller without undue delay (CNIL expects 24 to 48h in practice) |
| Sub-processor and sub-contractor list | Yes, with countries | Yes | On request | Compare with the DPA; every sub-contractor supporting a critical function must appear in register template B_05.02 |
| BCP/DR evidence | Yes: test report less than 12 months, RTO/RPO | Summary | No | Declared RTO/RPO at or below the function's RTO/RPO in B_06.01; test report dated with scenario and results; multi-region or secondary site |
| Cyber insurance certificate | Yes | Optional | No | Coverage amount versus contract value and potential incident cost; check exclusions (acts of war, unpatched systems) |
| Financial health | Yes: last annual accounts or rating | Optional | No | Going concern, dependency on a single client, recent acquisitions |
| Security questionnaire (CAIQ v4, SIG Core, or the entity's own) | Yes | Lite version | No | Answers must reference the SOC 2 or ISO evidence; "not applicable" needs a reason |
| External security rating | Yes, continuous | Yes, quarterly | Optional | SecurityScorecard letter grade (A to F) or BitSight rating (250 to 900); use as a trigger for a question, never as the assessment itself |
| Incident history | Yes: incidents of the last 3 years affecting the service | Yes | No | Compare with public breach records and the provider's status page history |
| Exit and reversibility plan | Yes: documented, tested yearly | Documented | No | Data export format, duration of termination assistance, deletion attestation, alternative provider or in-house option, estimated cost and duration |

## Judging a SOC 2 report quickly

1. Section 1 (auditor's opinion): unqualified, period, trust services categories (Security is mandatory; Availability and Confidentiality matter for hosting).
2. Section 3 (system description): the product and infrastructure in scope; locate the regions and the sub-service organisations (AWS, Azure, GCP) and whether they are carved out (inclusive method is rare).
3. Section 4 (tests and results): list every exception; map each to the service bought; ask the provider for its management response when the exception touches access control, change management or backups.
4. CUECs: copy them into the entity's own control list; auditors of the entity will ask how each CUEC is met.
5. Bridge letter: acceptable to cover the gap between the report end date and today, up to 3 months beyond the report period.

## Judging an ISO 27001 certificate

- Verify the certificate on the certification body's public directory (for instance BSI Verifeye, LRQA client directory) or through the IAF CertSearch database.
- Scope wording such as "the information security management system supporting the delivery of <product> from <sites>" is good; "ISMS of the company" with no site or product is weak.
- ISO 27001:2013 certificates expired on 31 October 2025; a 2013 certificate after that date is a finding.
- ISO 27017 (cloud controls) and ISO 27018 (PII in the cloud) are useful complements; ISO 27701 for processors.

## Continuous monitoring signals

| Signal | Source | Action threshold |
|---|---|---|
| Rating drop | SecurityScorecard `GET https://api.securityscorecard.io/companies/{domain}` (token `SSC_API_KEY`), BitSight `GET https://api.bitsighttech.com/ratings/v1/companies/{guid}` (token `BITSIGHT_API_TOKEN`) | Drop of one letter grade or 50 BitSight points in a quarter: ask the provider for an explanation within 30 days |
| New critical CVE in a product the provider runs for the entity | `GET https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch=<product>`, CISA KEV `https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json` | KEV listing: ask for the patch date within 72h |
| Breach or incident news | Provider status page, trust center, CNIL and EDPB enforcement, press | Any confirmed incident touching the service: open a provider incident record, request the incident report, check the 24h notification clause was honoured |
| Certificate expiry | Register fields `soc2_expiry`, `iso27001_expiry` through `scripts/register_check.py --expiry-days 90` | Request the renewed document 90 days before |
| Ownership change, insolvency, sanction | Company registry (Infogreffe, Companies House), OFAC and EU sanctions lists | Re-run due diligence; check the change-of-control clause |
| Sub-processor change notice | DPA notification e-mail (route through the `connectors` tool to a Gmail or Slack search) | Update B_05.02 within 30 days; object when the new sub-processor sits outside the EEA without a transfer tool |

## Re-assessment cadence

| Tier | Full assessment | Evidence refresh | Scorecard review | Exit plan test |
|---|---|---|---|---|
| Critical | Every 12 months | SOC 2 and pentest yearly | Monthly | Yearly (tabletop at minimum) |
| Important | Every 24 months | SOC 2 or ISO yearly | Quarterly | Documented, not tested |
| Standard | Every 36 months or at renewal | At renewal | Not required | Not required |
