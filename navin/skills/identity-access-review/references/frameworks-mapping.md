# Framework mapping

Use this table to label every finding and every evidence item with the control
references the auditor expects. One finding often maps to several frameworks.

| Review check | ISO/IEC 27001:2022 Annex A | SOC 2 (2017 TSC) | DORA (Reg. 2022/2554) | NIS2 (Dir. 2022/2555) | PCI DSS 4.0 |
|---|---|---|---|---|---|
| Joiner/mover/leaver process, timely deprovisioning | A.5.16 Identity management, A.5.18 Access rights, A.6.5 Responsibilities after termination | CC6.2, CC6.3 | Art. 9(4)(c) strong authentication and access management policies | Art. 21(2)(i) access control policies | Req. 8.2.4, 8.2.5 (revoke within 24h for terminated users), 8.2.6 (inactive accounts after 90 days) |
| Dormant and orphan accounts | A.5.16, A.5.18 | CC6.1, CC6.2 | Art. 9(4)(c) | Art. 21(2)(i) | Req. 8.2.6, 8.2.2 (shared accounts) |
| Periodic access recertification | A.5.18 (review at planned intervals), A.5.15 Access control policy | CC6.3 (review of access) | Art. 9(4)(c); RTS on ICT risk management framework art. 20-21 | Art. 21(2)(i) | Req. 7.2.4 (review all user accounts at least every six months) |
| Privileged access, PAM, break-glass | A.8.2 Privileged access rights, A.8.5 Secure authentication | CC6.1, CC6.3 | Art. 9(4)(c), (d) | Art. 21(2)(i), (j) multi-factor authentication | Req. 7.2.5 (application and system accounts), 8.6 |
| MFA coverage | A.8.5 | CC6.1 | Art. 9(4)(d) | Art. 21(2)(j) | Req. 8.4.1 to 8.4.3, 8.5 |
| Service accounts and key rotation | A.8.2, A.5.17 Authentication information | CC6.1 | Art. 9(4)(d) | Art. 21(2)(h) cryptography and (i) | Req. 8.6.1 to 8.6.3 (system account credentials), 8.3.9 |
| Separation of duties | A.5.3 Segregation of duties | CC6.1, CC6.3, CC5.1 | Art. 5(2) and art. 6(4) independent control functions | Art. 21(2)(i) | Req. 7.2.1 (least privilege), 6.5.3 (separation of test and production roles) |
| Logging of privileged actions | A.8.15 Logging, A.8.16 Monitoring | CC7.2 | Art. 9(4)(e), art. 10 detection | Art. 21(2)(b), (d) | Req. 10.2.1.2 |
| Cloud and SaaS admin access (Entra, Okta, AWS, GCP) | A.5.23 Cloud services, A.8.2 | CC6.1 | Art. 9, art. 28 third-party ICT risk | Art. 21(2)(d) supply chain | Req. 7, 8, 12.8 |
| Source control and CI/CD admin access | A.8.2, A.8.4 Access to source code, A.8.32 Change management | CC8.1 | Art. 9(4)(a), (b) | Art. 21(2)(e) | Req. 6.5 |

## Cadence auditors expect

| Population | Minimum frequency | Source |
|---|---|---|
| Privileged accounts, PAM safes, cloud admins, GitHub owners | Quarterly | PCI DSS 7.2.4 and common SOC 2 CUECs; DORA RTS expects at least yearly with risk-based increases |
| Standard user access per application | Every six months | PCI DSS 4.0 req. 7.2.4 |
| Service accounts and API keys | Yearly review, 90-day rotation for secrets | PCI DSS 8.6.3 (frequency defined by targeted risk analysis), ISO A.5.17 |
| Leaver deprovisioning check | Monthly HR-to-IAM reconciliation | SOC 2 CC6.2, PCI DSS 8.2.5 |
| SoD matrix refresh | Yearly, and on every ERP role change | ISO A.5.3 |

## Evidence pack checklist

| Item | Format | Proves |
|---|---|---|
| Population extract with timestamp and hash | CSV plus `sha256sum` output | Completeness of the review scope |
| HR leaver list used for reconciliation | CSV from Workday/SuccessFactors | Leaver detection source |
| Script outputs (`access_review_report.py`, `sod_conflicts.py`) | Markdown and JSON | Findings and thresholds applied |
| Recertification decisions export | Entra access review decisions, SailPoint certification items | Reviewer, decision, date |
| Remediation tickets | Jira or ServiceNow keys with closure dates | Findings were acted upon |
| Exceptions register | Table: user, entitlement, reason, approver, expiry | Accepted risks are time-bound |
| Policy references | Access control policy version and approval date | Control design |
