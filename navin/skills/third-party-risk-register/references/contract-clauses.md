# Contract clauses for ICT third-party arrangements

DORA art. 30 lists the mandatory content for every ICT contract (art. 30(2))
and the additional content for critical or important functions (art. 30(3)).
EBA outsourcing guidelines section 13 and the CNIL model clauses for RGPD
art. 28 overlap with it. Use this table to review a contract or an MSA before
signature and to flag gaps in existing arrangements.

## Mandatory for every ICT arrangement (DORA art. 30(2))

| Clause | What must be written | Register field | Typical weakness |
|---|---|---|---|
| Description of functions and services | Clear, complete, including whether sub-contracting of critical functions is permitted and under which conditions | B_02.02 service type, function | "Services as described in the order form" with no function mapping |
| Data location | Regions where data is processed and stored, including backups; obligation to notify before any change | data_location | Provider reserves the right to move data "within its global infrastructure" |
| Availability, authenticity, integrity, confidentiality of data | Including personal data protection and access to data on insolvency, resolution or termination | - | Access to data on provider insolvency is missing |
| Service level descriptions | Quantitative and qualitative targets with updates | SLA | Only a credit scheme, no exit right on repeated breach |
| Incident assistance | Provider assists at no additional cost or at a pre-determined cost when an ICT incident relates to the service | incident_notification_hours | Assistance billed at time-and-materials |
| Cooperation with authorities | Provider cooperates fully with the competent and resolution authorities | - | Absent in US-law templates |
| Termination rights and notice periods | Minimum notice periods for the entity and the provider, in line with supervisory expectations | notice_period_days | Auto-renewal with 12-month lock-in and 30-day notice for the provider |
| Training | Participation in the entity's ICT security awareness and digital operational resilience training when relevant | - | Usually missing; low impact |

## Additional for critical or important functions (DORA art. 30(3))

| Clause | Expected content | Negotiation position |
|---|---|---|
| Full service level descriptions with quantitative and qualitative targets | Performance targets, monitoring, corrective actions without undue delay | Refuse SLAs without measurement method |
| Incident notification | Notice periods and reporting obligations, including notification of developments that could materially affect the service | 24 hours after detection for any security incident affecting the service; 72h is the RGPD ceiling for the controller to notify the CNIL, so the processor needs to be faster |
| Business contingency plans | Provider implements and tests BCP; security measures, tools and policies adequate for the entity | Test report yearly |
| Participation in TLPT | Provider participates in the entity's threat-led penetration testing (art. 26) | Cloud hyperscalers accept via their pentest rules of engagement |
| Monitoring rights | Unrestricted rights of access, inspection and audit by the entity, its appointed third party and the competent authority; pooled audits and third-party certifications accepted only with the entity's own assessment | Audit right with a notice period of 30 days and once a year is standard; no cap on authority access |
| Exit strategies | Mandatory transition period during which the provider continues the service, with support for migration to another provider or in-house; termination assistance duration and cost | 12 months of termination assistance for a core system; data returned in an open format with a deletion certificate |
| Sub-contracting conditions | Conditions under which sub-contracting of critical functions is allowed (RTS on sub-contracting): prior notification, right to object, flow-down of the same obligations, monitoring of the chain | Right to object within 30 to 90 days; list in B_05.02 kept current |

## Termination rights (DORA art. 28(7))

The entity must be able to terminate when: the provider significantly breaches
law or contract; circumstances in the monitoring of the ICT risk show the
provider's weaknesses in its ICT risk management; the competent authority can no
longer effectively supervise the entity because of the arrangement; or the
provider's situation (change of control, insolvency) materially affects the
service. Check that each case is a listed termination trigger without penalty.

## RGPD art. 28 processor clauses (DPA)

| Clause | Requirement | Check |
|---|---|---|
| Documented instructions | Processing only on the controller's instructions, including transfers | Instructions annex lists purposes and data categories |
| Confidentiality of personnel | Staff bound by confidentiality | Present in almost all DPAs |
| Security measures (art. 32) | Technical and organisational measures annex | Annex is specific (encryption at rest, MFA, logging retention), not generic |
| Sub-processors | Prior specific or general authorisation; notification of changes with right to object | Mechanism, delay and list URL identified |
| Data subject rights assistance | Help the controller answer requests | Response delay stated |
| Breach notification | Without undue delay after becoming aware | Fix a number of hours (24 to 48) rather than "without undue delay" |
| Deletion or return | At end of service, with attestation | Format and delay; backups deletion timeline |
| Audit | Information and audits, including inspections | Not limited to "questionnaire only" for critical providers |
| International transfers | SCC (Decision 2021/914) with transfer impact assessment, or adequacy (EU-US Data Privacy Framework for certified US companies) | Module correct (2 controller-to-processor, 3 processor-to-processor); TIA on file |

## Clause review scoring

Score each clause: 2 present and adequate, 1 present but weak, 0 absent. A critical arrangement with any mandatory art. 30(3) clause at 0 is a blocking finding in the deliverable; existing contracts signed before 17 January 2025 need a remediation plan with a renegotiation date agreed with the provider.
