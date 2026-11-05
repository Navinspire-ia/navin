# DORA register of information: templates and fields

Source: Commission Implementing Regulation (EU) 2024/2956 (ITS on the register
of information, DORA art. 28(3)), applicable from 17 January 2025. The register
is reported to the competent authority (ACPR, AMF, BaFin, CSSF...) in xBRL-CSV
using the EBA/EIOPA data point model; financial entities keep it at entity,
sub-consolidated and consolidated level.

## Templates

| Template | Content | Granularity |
|---|---|---|
| B_01.01 | Entity maintaining the register | one row per register |
| B_01.02 | Financial entities in the scope of consolidation | one row per entity (LEI, type, competent authority, country) |
| B_01.03 | Branches | one row per branch |
| B_02.01 | Contractual arrangements: general information | one row per arrangement (reference number, type: standalone / overarching / subsequent, overarching reference, currency, annual expense) |
| B_02.02 | Contractual arrangements: specific information | one row per arrangement and ICT service (provider, service type, function, storage of data, data location, sensitiveness, reliance level, notice periods, governing law, termination reasons) |
| B_02.03 | Intra-group contractual arrangements link | links subsequent arrangements to the overarching one |
| B_03.01 | Entities signing the arrangement | which entity signed (LEI) |
| B_03.02 | Providers signing the arrangement | provider identifier |
| B_03.03 | Entities making use of the ICT services | beneficiaries |
| B_04.01 | Entities signing for third-party providers (group level) | mapping |
| B_05.01 | ICT third-party service providers | one row per provider: identifier (LEI or EUID), name, type of person, country, currency, total annual expense, ultimate parent identifier |
| B_05.02 | Sub-contracting chain | rank, provider identifier, rank-1 provider, recipient |
| B_06.01 | Functions | one row per function: identifier, licensed activity, name, criticality or importance assessment, reasons, date of last assessment, RTO, RPO, impact of discontinuing |
| B_07.01 | Assessment of ICT services | one row per arrangement and function: substitutability, reason, date of last audit, exit plan existence, reintegration possibility, impact of discontinuing, alternative providers identified |
| B_99.01 | Definitions | closed lists and codes |

## Key fields and how to fill them

| Field (template) | Closed list or format | Gotcha |
|---|---|---|
| Provider identifier (B_05.01.0010) | LEI (20 chars, ISO 17442), EUID for EU companies without LEI, or national code | Look up the LEI on `https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=<name>`; cloud providers often contract through a local entity (AWS EMEA SARL LU, Microsoft Ireland Operations Ltd IE) whose LEI differs from the parent |
| Type of ICT service (B_02.02.0060) | S01 ICT project management, S02 ICT development, S03 ICT help desk, S04 security management, S05 provision of data, S06 data analysis, S07 ICT facilities and hosting (excluding cloud), S08 computation, S09 non-cloud data storage, S10 telecom carrier, S11 network infrastructure, S12 hardware, S13 software licensing, S14 ICT operation management, S15 ICT consulting, S16 ICT risk management, S17 cloud IaaS, S18 cloud PaaS, S19 cloud SaaS | Cloud is always S17 to S19; a managed Kubernetes service is S18 |
| Function identifier (B_06.01.0010) | internal code F + number | One business function may rely on several arrangements |
| Criticality or importance assessment (B_06.01.0050) | Yes / No / Assessment not performed | "Critical or important function" has the DORA art. 3(22) meaning: disruption would materially impair financial performance, soundness or continuity of services or compliance |
| Reliance level (B_02.02.0170) | Not significant / Low / Material / Full | Full reliance plus not substitutable is what supervisors look at first |
| Substitutability (B_07.01.0030) | Not substitutable / Highly complex substitutability / Medium complexity / Easily substitutable | Must match the exit plan effort |
| Storage of data (B_02.02.0110) | Yes / No | Yes as soon as the provider stores or processes data, including backups |
| Location of data at rest (B_02.02.0120) and processing (0130) | ISO 3166-1 alpha-2 | Record every region used, not the contracting country |
| Sensitiveness of data (B_02.02.0140) | Low / Medium / High | High for personal data of clients or payment data |
| Notice period for the financial entity (B_02.02.0180) and for the provider (0190) | number of days | Count from the contract, not the SLA annex |
| Governing law (B_02.02.0200) | ISO country code | Non-EU governing law on a critical function is a finding |
| Annual expense (B_02.01.0070, B_05.01.0070) | amount in the reporting currency | Take invoiced amounts of the last financial year; estimate for new contracts |
| Date of last audit (B_07.01.0050) | ISO date | Audit means the financial entity's or a pooled audit, not the provider's SOC 2 |
| Exit plan (B_07.01.0070) | Yes / No | Must exist for every critical or important function (art. 28(8)) |
| Reintegration possibility (B_07.01.0080) | Easy / Difficult / Highly complex | Supports the exit strategy |
| Alternative providers identified (B_07.01.0100) | Yes / No, plus identification | Concentration analysis input |
| Sub-contracting rank (B_05.02.0020) | 1 for the direct provider, 2 and more for sub-contractors | Only sub-contractors that effectively underpin critical or important functions (RTS on sub-contracting, art. 30(2)(a)) |

## Reporting calendar and consistency checks

- First reporting: 2025 (reference date set by each competent authority, usually 31 March, in April).
- Yearly thereafter; the register must be available on request at any time, so update it on every contract signature, renewal, termination or criticality reassessment.
- Validation rules used by authorities: every B_02.02 row links to an existing B_05.01 provider and B_06.01 function; every critical function has at least one B_07.01 row with a substitutability value; annual expenses in B_02.01 sum to the provider total in B_05.01; LEI format valid; country codes in the closed list; dates not in the future.
- `scripts/register_check.py` covers the structural subset of these checks (required fields per tier, LEI format, exit plan presence, assessment age); run it before the xBRL conversion.

## NIS2 and EBA correspondences

| Topic | DORA | NIS2 (Dir. 2022/2555) | EBA Guidelines on outsourcing (EBA/GL/2019/02) |
|---|---|---|---|
| Register of arrangements | Art. 28(3) and ITS | Art. 21(2)(d) supply chain security policies (no fixed template) | Section 11: register of all outsourcing arrangements with the same core fields |
| Criticality assessment | Art. 3(22), art. 28(3) | Art. 21(3) assess vulnerabilities specific to each supplier | Section 4: critical or important functions |
| Pre-contract due diligence | Art. 28(4) | Art. 21(2)(d) | Section 12.2 |
| Contractual provisions | Art. 30 | Art. 21(2)(d) | Section 13 |
| Exit strategies | Art. 28(8) | - | Section 15 |
| Notification to the authority | Art. 28(3), register submission | Art. 23 incident reporting (24h early warning) | Section 11.2: notify planned critical outsourcing |
