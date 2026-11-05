# SoD matrix catalog

Conflicting entitlement pairs by domain. Copy the pairs that exist in the
organisation into a matrix JSON (see `examples/sod-matrix.json`) using the
exact role or group names of the source system (SAP roles, Entra groups, AWS
policies, GitHub teams). Severity is a default; lower it only with a documented
mitigating control (dual approval, independent reconciliation, monitored log).

## Finance (procure-to-pay, record-to-report)

| Activity A | Activity B | Severity | Fraud scenario | Typical mitigating control |
|---|---|---|---|---|
| Create or modify vendor master data | Approve payment run | high | Fictitious vendor paid | Vendor change report reviewed by a second person |
| Create or modify vendor bank details | Post vendor invoice | high | Redirect a real vendor's payment | Bank detail change callback |
| Post vendor invoice | Approve payment run | high | Pay an invoice nobody validated | Three-way match (PO, receipt, invoice) |
| Create purchase order | Record goods receipt | medium | Receive goods never delivered | PO above threshold needs manager approval |
| Post journal entries | Approve journal entries | high | Hide losses or inflate revenue | Journal approval workflow in the ERP |
| Maintain chart of accounts | Post journal entries | medium | Create accounts to park entries | Quarterly master data review |
| Bank reconciliation | Post cash receipts | high | Lapping of customer payments | Reconciliation by treasury, not AR |
| Create customer | Issue credit note | medium | Credit a friendly customer | Credit note above threshold approved |

## HR and payroll

| Activity A | Activity B | Severity | Fraud scenario |
|---|---|---|---|
| Create employee record | Run or approve payroll | high | Ghost employee |
| Modify employee bank details | Run payroll | high | Divert a salary |
| Approve timesheets | Modify own timesheet | medium | Overtime fraud |
| Set compensation | Approve compensation change | high | Self-granted raise |

## IT and identity

| Activity A | Activity B | Severity | Scenario |
|---|---|---|---|
| Request access (any system) | Approve access request | high | Self-approval |
| Administer the IAM platform (SailPoint, Entra, Okta admin) | Approve own role assignment | high | Unlogged privilege grant |
| Manage privileged vault (CyberArk Vault Admin, Vault root policy) | Review PAM session recordings | medium | Admin erases own session trail |
| Security monitoring (SIEM admin) | Production system administration | medium | Tamper with the logs of own actions |
| Manage MFA methods for users | Reset user passwords | medium | Full account takeover by one helpdesk agent |
| Database administrator | Application business role in the same system | high | Direct data modification outside workflow |

## Cloud and infrastructure

| Activity A | Activity B | Severity | Scenario |
|---|---|---|---|
| Approve change (ServiceNow / Jira change approver) | Deploy to production (AWS admin, kubectl prod, Argo CD sync) | high | Approve and push an unreviewed change |
| AWS `iam:*` or `AdministratorAccess` | CloudTrail or AWS Config administration | high | Disable audit logging after acting |
| GCP `roles/owner` on a project | `roles/logging.admin` on the same project | high | Same as above on GCP |
| Kubernetes `cluster-admin` | Audit log pipeline owner (Fluent Bit, Loki) | medium | Suppress audit events |
| Create IAM users or service accounts | Attach policies to them | medium | Grant self a second identity with more rights |
| Manage backup vault (AWS Backup, Velero) | Delete production data | high | Destroy data and its backups |
| Billing administrator | Create cloud resources | low | Hide spend; low direct fraud risk |

## Software delivery

| Activity A | Activity B | Severity | Scenario |
|---|---|---|---|
| Write to the default branch (bypass PR) | Trigger production release | high | Ship unreviewed code |
| Approve own pull request | Merge pull request | high | Review bypass; enforce `required_approving_review_count` and `dismiss_stale_reviews` |
| Manage CI secrets (GitHub Actions org secrets, GitLab CI variables) | Edit pipeline definitions | medium | Exfiltrate secrets through a modified workflow |
| Publish package to registry (npm, PyPI, Artifactory) | Approve dependency updates | medium | Inject a malicious dependency |
| GitHub organisation owner | Any developer role | low | Owner bypasses all branch protections; keep owner count at 2 to 3 |

## How to apply the matrix

1. Map business activities to technical entitlements with the system owner; one activity often maps to several roles (SAP: `F_BKPF_BUK` plus a transaction code).
2. Run `scripts/sod_conflicts.py` on the entitlement export.
3. For every conflict, the business owner picks: remove one side, split the role, or accept with a mitigating control and a review date. Record the decision; auditors ask for it.
4. Re-run the script after remediation and attach both outputs to the evidence pack.
