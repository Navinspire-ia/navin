# Regulatory and standards mapping: what to prove, article by article

Each row names the requirement, the evidence an auditor or supervisor accepts, and the exercise or artefact in this skill that produces it. References are to the published texts: Regulation (EU) 2022/2554 (DORA), Directive (EU) 2022/2555 (NIS2), ISO 22301:2019, ISO/IEC 27031:2011, ANSSI and EBA documents.

## DORA (Regulation (EU) 2022/2554), applicable since 17 January 2025

| Article | Requirement | Evidence to produce | Source in this skill |
|---|---|---|---|
| Art. 5(2) | the management body approves and oversees the ICT risk management framework, including business continuity | minutes of the management body approving the BC policy and the DR plans; yearly review date | BIA sign-off table |
| Art. 11(1) | an ICT business continuity policy as part of the ICT risk management framework | the policy document, version controlled, with scope, roles, RTO/RPO methodology | BIA, exercise programme |
| Art. 11(2) | the policy ensures continuity of critical or important functions, rapid response to ICT incidents, activation of containment and response, and estimation of preliminary impacts | incident response plan with activation criteria, link between incident classification and DR activation | exercise plan (decision inject) |
| Art. 11(3) | ICT response and recovery plans, reviewed at least yearly and after major incidents, subject to independent internal audit review | the plans, change history, audit report reference | after-action report (plan updates section) |
| Art. 11(4) | business impact analysis of exposure to severe business disruptions, with quantitative and qualitative criteria, including dependencies on ICT third parties | the BIA with MTPD, RTO, RPO, MBCO, third-party dependencies | `references/bia-template.md` |
| Art. 11(5) | crisis management function, internal and external crisis communication plans, in line with art. 14 | crisis communication plan, exercise record showing drafts produced | exercise plan communication section |
| Art. 11(6) | ICT business continuity plans tested at least yearly, and after substantive changes; tests include scenarios of cyber-attacks and switchover between primary and redundant ICT infrastructure; for credit institutions and CCPs, tests cover critical or important functions outsourced to third parties | exercise programme, plan, log, after-action report per test, with the switchover test explicit | exercise plan + `rpo_rto_check.py` output + after-action report |
| Art. 11(7) | analysis of the test results and of post-incident reviews feeds the ICT risk management framework | findings tracked to closure with owners and dates | after-action report findings table |
| Art. 11(8) | the ICT business continuity policy and response plans are communicated to all relevant staff and tested | training and awareness records, participants list per exercise | exercise plan roles table |
| Art. 11(10) | financial entities report to competent authorities, upon request, an estimation of aggregated annual costs and losses caused by major ICT-related incidents | incident cost register | after-action report (business impact section) |
| Art. 12(1) | backup policies and procedures specifying scope and frequency based on criticality and confidentiality; restoration and recovery procedures and methods | backup policy per system with RPO-derived frequency, retention, immutability, encryption, and the restore procedure | BIA system table |
| Art. 12(2) | backup systems activated without jeopardising security, availability, integrity of data; restoration tested periodically | restore test records with achieved restore time and data validation | backup restore exercise + `rpo_rto_check.py` |
| Art. 12(3) | redundant ICT capacities with resources, capabilities and functions adequate to ensure business needs | capacity evidence of the secondary site (scale-up proven during full failover) | full failover exercise |
| Art. 12(4) | CCPs maintain a secondary processing site with a different geographic risk profile, able to ensure continuity of critical functions identically or at the level needed | secondary site description, distance and risk profile, failover evidence | exercise plan scope |
| Art. 12(5) | when restoring backup data on own systems, use ICT systems physically and logically segregated from the source system, protected from unauthorised access or ICT corruption | restore target architecture (separate account or subscription, no shared credentials) | backup restore exercise |
| Art. 12(6) | recovery time and recovery point objectives for each function, determined by potential overall impact on market efficiency; recovery ensures that the agreed service levels are met | RTO/RPO per function, and exercise results against them | BIA + `rpo_rto_check.py` |
| Art. 12(7) | data integrity checks when recovering from an incident, including reconciliation with external data | reconciliation procedure and its execution record | exercise plan rollback inject |
| Art. 14 | communication policies for internal staff and external stakeholders, including the communication of major incidents to clients and counterparts | templates, spokesperson designation, exercise drafts | exercise plan communication |
| Art. 24 | digital operational resilience testing programme, risk-based, with tests performed by independent parties (internal or external), with a procedure to prioritise, classify and remedy issues | the testing programme, independence statement for testers, remediation tracker | exercise programme in the deliverable |
| Art. 25 | tests include vulnerability assessments, open source analyses, network security assessments, gap analyses, physical security reviews, questionnaires, source code reviews, scenario-based tests, compatibility testing, performance testing, end-to-end testing, penetration testing; carried out at least yearly on ICT systems supporting critical or important functions | scenario-based and end-to-end tests are the DR exercises; one per critical function per year | full failover or component failover per critical function |
| Art. 26 and 27 | threat-led penetration testing (TLPT) at least every 3 years for significant entities, by qualified testers, on live production systems | TLPT scope agreed with the authority; out of scope for this skill except that the DR plan must cover the TLPT scenarios | `penetration-tester` skill |
| Art. 17 to 19 | ICT incident management, classification and reporting to the competent authority (initial notification within 4 hours of classification as major and no later than 24 hours after detection, intermediate report within 72 hours, final report within one month) | incident reporting templates completed as drills during exercises | exercise plan communication inject |
| Art. 28 to 30 | ICT third-party risk: register of information, exit strategies, contractual provisions on service levels, business continuity and testing participation | contracts with RTO commitments, participation of providers in tests | BIA dependency questions |

Delegated regulation (EU) 2024/1774 (RTS on ICT risk management framework), chapter II section 6 (articles 24 to 26), details the backup, restoration and testing requirements: documented restoration procedures, offline or logically isolated backup copies, test of restoration from backups at least yearly for critical or important functions, and the scenario list for BC testing (cyber-attack, failure of a third party, loss of a site, pandemic).

## NIS2 (Directive (EU) 2022/2555), transposed by 17 October 2024

| Article | Requirement | Evidence |
|---|---|---|
| Art. 21(2)(c) | business continuity, such as backup management and disaster recovery, and crisis management | BC policy, backup policy, DR plans, exercise records, crisis organisation |
| Art. 21(2)(f) | policies and procedures to assess the effectiveness of cybersecurity risk-management measures | exercise programme and after-action reports |
| Art. 21(2)(g) | basic cyber hygiene practices and cybersecurity training | participants and training records |
| Art. 23 | incident notification: early warning within 24 hours, incident notification within 72 hours, final report within one month | notification templates tested in exercises |
| Implementing regulation (EU) 2024/2690, Annex section 4 | for digital infrastructure and digital providers: business continuity and disaster recovery plans with RTO/RPO, backup copies stored in a separate location, restoration tests, crisis management | the same artefacts as DORA art. 11 and 12 |

## ISO 22301:2019 (business continuity management systems)

| Clause | Requirement | Evidence |
|---|---|---|
| 8.2.2 | business impact analysis: impact types and criteria, prioritised activities, MTPD, RTO, MBCO, dependencies | the BIA |
| 8.2.3 | risk assessment of disruption to prioritised activities | risk register entries |
| 8.3 | business continuity strategies and solutions, selection based on BIA and risk assessment, resource requirements | strategy per process and system with cost justification |
| 8.4 | business continuity plans and procedures: response structure, warning and communication, plans with roles, activation criteria, resources, recovery steps | the plans and runbooks |
| 8.5 | exercise programme: exercises consistent with the BC objectives, based on scenarios, with defined aims, minimising risk of disruption, with formal post-exercise reports and review | exercise plan, log, after-action report; programme covering all plans over time |
| 8.6 | evaluation of BC documentation and capabilities: after exercises, incidents and changes | review records |
| 9.1, 9.2, 9.3 | monitoring and measurement, internal audit, management review | KPIs (exercises done versus planned, RTO achieved versus target), audit reports, management review minutes |
| 10 | nonconformity and corrective action, continual improvement | findings tracker with closure evidence |

## ISO/IEC 27031:2011 (ICT readiness for business continuity)

| Clause | Requirement | Evidence |
|---|---|---|
| 6 | ICT readiness lifecycle: plan, implement, monitor and review, improve; IRBC policy | ICT continuity policy linked to the BC policy |
| 7.3 | IRBC strategy: skills and knowledge, facilities, technology (resilience, redundancy), data (backup, replication, RPO), processes, suppliers | strategy per component |
| 8 | implementation: incident response, ICT plans, awareness, testing | plans and exercise records |
| 9 | monitoring and review: exercise and test programme, performance measurement, internal audit | KPIs and exercise programme |

ISO/IEC 27001:2022 Annex A control 5.30 (ICT readiness for business continuity) and 8.13 (information backup) point to the same evidence; 8.14 (redundancy of information processing facilities) covers the secondary site.

## ANSSI and EBA

| Source | Content | Use |
|---|---|---|
| ANSSI, "Guide pour realiser un plan de continuite d'activite" (SGDSN, 2013) | French methodology for the PCA: BIA, strategies, plan content, exercises | structure of the French-language deliverable; vocabulary PRA/PCA, DMIA (duree maximale d'interruption admissible = MTPD), PDMA (perte de donnees maximale admissible = RPO) |
| ANSSI, "Organiser un exercice de gestion de crise cyber" (2020) | tabletop design, injects, roles (animation cell, players, observers), evaluation grid | tabletop and simulation design |
| ANSSI, "Crise d'origine cyber, les cles d'une gestion operationnelle et strategique" (2021) | crisis organisation, decision cells, communication | crisis exercise objectives |
| ANSSI, "Maitriser le risque numerique" and the hygiene guide (rule on backups) | offline backups, restore tests | ransomware scenario |
| EBA Guidelines on ICT and security risk management (EBA/GL/2019/04), section 3.7 | BIA, business continuity planning, response and recovery plans, testing at least yearly including switchover and scenario tests, crisis communication | the same table as DORA art. 11 and 12 for institutions still referencing EBA guidelines; DORA supersedes for financial entities in scope |
| EBA Guidelines on outsourcing arrangements (EBA/GL/2019/02), section 13.4 | business continuity plans covering outsourced critical functions, exit plans | third-party dependency rows |

## Minimal evidence pack for one exercise

| Item | Satisfies |
|---|---|
| Signed exercise plan (objectives, scope, scenario, roles, success and abort criteria) | ISO 22301 8.5, DORA art. 11(6) |
| Timestamped exercise log | DORA art. 12(6), ISO 22301 8.5 |
| `rpo_rto_check.py` output with pass/fail per system | DORA art. 12(6), EBA 3.7 |
| Screenshots or command outputs of failover, restore and validation | DORA art. 12(2), ISO 27031 9 |
| Communication drafts produced during the exercise | DORA art. 14, NIS2 art. 23 |
| After-action report with findings, owners, due dates | DORA art. 11(7), ISO 22301 10 |
| Management review record acknowledging the report | DORA art. 5(2), ISO 22301 9.3 |
