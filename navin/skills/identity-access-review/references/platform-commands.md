# Platform commands and API calls

Every command is read-only. Cloud and Kubernetes CLIs go through the `infra`
tool at level `observe`; identity SaaS APIs are called with the token read from
an environment variable that is never echoed. Install missing CLIs with the
`toolbelt` skill.

## Microsoft Entra ID (Microsoft Graph)

Token: `AZURE_ACCESS_TOKEN` or `az account get-access-token --resource-type ms-graph` through `infra`.

| Need | Call |
|---|---|
| Users with sign-in activity | `GET https://graph.microsoft.com/v1.0/users?$select=id,userPrincipalName,accountEnabled,signInActivity,employeeLeaveDateTime&$top=999` (needs `AuditLog.Read.All`; `signInActivity.lastSignInDateTime`) |
| MFA registration | `GET https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails` (`isMfaRegistered`, `isMfaCapable`, `methodsRegistered`) |
| Directory role members (Global Administrator etc.) | `GET https://graph.microsoft.com/v1.0/directoryRoles?$expand=members` |
| PIM eligible assignments | `GET https://graph.microsoft.com/v1.0/roleManagement/directory/roleEligibilityScheduleInstances?$expand=principal,roleDefinition` |
| PIM active assignments | `GET https://graph.microsoft.com/v1.0/roleManagement/directory/roleAssignmentScheduleInstances?$expand=principal,roleDefinition` (`assignmentType=Activated` vs `Assigned` = permanent) |
| Access review definitions and decisions | `GET https://graph.microsoft.com/v1.0/identityGovernance/accessReviews/definitions` then `.../definitions/{id}/instances/{instanceId}/decisions` (`decision`: Approve, Deny, NotReviewed; `reviewedBy`) |
| Guest users | `GET https://graph.microsoft.com/v1.0/users?$filter=userType eq 'Guest'&$select=userPrincipalName,createdDateTime,signInActivity` |
| Service principals and credential age | `GET https://graph.microsoft.com/v1.0/applications?$select=displayName,passwordCredentials,keyCredentials` (`endDateTime`, `startDateTime`) |

CLI alternative: `az ad user list --query "[?accountEnabled==\`true\`]"`, `az role assignment list --all --include-inherited` for Azure RBAC.

## Okta

Token: `OKTA_API_TOKEN` (header `Authorization: SSWS ${OKTA_API_TOKEN}`), base `https://<org>.okta.com/api/v1`.

| Need | Call |
|---|---|
| Users with status and last login | `GET /users?limit=200&filter=status eq "ACTIVE"` (`lastLogin`, `passwordChanged`, `status`: STAGED, PROVISIONED, ACTIVE, SUSPENDED, DEPROVISIONED); paginate with the `Link: rel="next"` header |
| Admin role assignments | `GET /iam/assignees/users` then `GET /users/{id}/roles` (`SUPER_ADMIN`, `ORG_ADMIN`, `APP_ADMIN`) |
| MFA factors | `GET /users/{id}/factors` (`factorType`, `status: ACTIVE`) or `GET /api/v1/reports/...` is not available; use the Factors report in System Log |
| Group membership | `GET /groups/{id}/users` |
| API tokens | `GET /api-tokens` (`created`, `expiresAt`, `userId`) |
| Deprovisioning events | `GET /logs?filter=eventType eq "user.lifecycle.deactivate"&since=<ISO>` |

## SailPoint IdentityNow (Identity Security Cloud)

Token: OAuth2 client credentials from `SAILPOINT_CLIENT_ID` / `SAILPOINT_CLIENT_SECRET`, `POST https://<tenant>.api.identitynow.com/oauth/token`.

| Need | Call |
|---|---|
| Certification campaigns | `GET /v3/campaigns?filters=status eq "COMPLETED"` then `GET /v3/certifications/{id}/access-review-items` (`decision`, `completed`) |
| SoD policies and violations | `GET /v3/sod-policies`, `GET /v3/sod-violation-report/{reportResultId}/download` |
| Uncorrelated accounts (orphans) | `GET /v3/accounts?filters=uncorrelated eq true` |
| Access profiles and roles per identity | `GET /v3/search` with `{"indices":["identities"],"query":{"query":"@access(type:ROLE)"}}` |
| Identity attributes (manager, lifecycle state) | `GET /v3/public-identities?filters=...` or `/v2024/identities` (`lifecycleState.stateName`, `managerRef`) |

## Saviynt EIC

Token: `SAVIYNT_TOKEN` from `POST /ECM/api/login`. Campaign status: `POST /ECM/api/v5/fetchCampaignDetails`; SoD: `POST /ECM/api/v5/getSODReport`; accounts: `POST /ECM/api/v5/getAccounts` with `{"endpoint":"<name>","accountStatus":"1"}`.

## AWS IAM

Run through `infra level=observe`.

```bash
aws iam generate-credential-report
aws iam get-credential-report --query Content --output text | base64 -d > iam-credential-report.csv
# columns: user, password_enabled, password_last_used, mfa_active, access_key_1_active,
# access_key_1_last_rotated, access_key_1_last_used_date, access_key_2_*
aws iam get-account-authorization-details --output json > iam-auth-details.json
aws iam list-users --query 'Users[?PasswordLastUsed<`2025-10-01`].[UserName,PasswordLastUsed]'
aws accessanalyzer list-analyzers --type ACCOUNT_UNUSED_ACCESS
aws accessanalyzer list-findings-v2 --analyzer-arn <arn> --filter '{"findingType":{"eq":["UnusedIAMRole","UnusedIAMUserAccessKey","UnusedIAMUserPassword","UnusedPermission"]}}'
aws iam get-account-summary   # AccountMFAEnabled must be 1
aws iam list-virtual-mfa-devices --assignment-status Assigned
aws organizations list-accounts   # scope: run the above per account via AWS SSO / assume-role
aws sso-admin list-permission-sets --instance-arn <arn>
aws identitystore list-users --identity-store-id <id>
```

Search the authorization details with `grep` before reading: `"PolicyName": "AdministratorAccess"`, `"Effect": "Allow"` combined with `"Action": "*"`.

## GCP IAM

```bash
gcloud projects get-iam-policy <project> --format=json
gcloud asset search-all-iam-policies --scope=organizations/<org-id> --query='policy:roles/owner'
gcloud recommender recommendations list --project=<project> --location=global --recommender=google.iam.policy.Recommender --format=json
# recommenderSubtype REMOVE_ROLE or REPLACE_ROLE; content.overview.removedRole
gcloud iam service-accounts keys list --iam-account=<sa>@<project>.iam.gserviceaccount.com --managed-by=user
# validAfterTime gives the key age; user-managed keys older than 90 days are findings
gcloud recommender insights list --insight-type=google.iam.serviceAccount.Insight --project=<project> --location=global
```

## Kubernetes RBAC

```bash
kubectl get clusterrolebindings -o json | jq '.items[] | select(.roleRef.name=="cluster-admin") | {name:.metadata.name, subjects}'
kubectl auth can-i --list --as=<user> --as-group=<group> -n <namespace>
kubectl auth can-i '*' '*' --as=system:serviceaccount:<ns>:<sa>
kubectl get rolebindings,clusterrolebindings -A -o wide
kubectl get serviceaccounts -A -o json | jq '.items[] | select(.secrets != null)'   # long-lived tokens
```

Findings: `cluster-admin` bound to a group with more than a handful of members, `system:masters` in any kubeconfig, wildcard verbs on `secrets`, service accounts with `automountServiceAccountToken` and long-lived secret tokens.

## GitHub organisation

Use the `connectors` tool (GitHub connector) or `GITHUB_TOKEN` with `read:org`.

| Need | Call |
|---|---|
| Owners | `GET /orgs/{org}/members?role=admin` |
| Members without 2FA | `GET /orgs/{org}/members?filter=2fa_disabled` (owner token required) |
| Outside collaborators | `GET /orgs/{org}/outside_collaborators` |
| Repository admins | `GET /repos/{owner}/{repo}/collaborators?permission=admin` |
| Fine-grained PAT requests and grants | `GET /orgs/{org}/personal-access-tokens`, `.../personal-access-token-requests` |
| GitHub App installations | `GET /orgs/{org}/installations` (`permissions` object) |
| Audit log for admin actions | `GET /orgs/{org}/audit-log?phrase=action:org.add_member+action:repo.access` (Enterprise) |

## PAM platforms

| Platform | Read-only evidence |
|---|---|
| CyberArk PAS / Privilege Cloud | `GET /PasswordVault/API/Accounts?filter=safeName eq <safe>` (`secretManagement.lastModifiedTime`, `platformId`); `GET /PasswordVault/API/Recordings?FromTime=<epoch>`; `GET /PasswordVault/API/Users?filter=userType eq EPVUser` for vault admins; Safe membership report `GET /PasswordVault/API/Safes/{safeUrlId}/Members` |
| HashiCorp Vault | `vault list auth/approle/role`, `vault read auth/token/accessors` is sensitive; prefer `vault list -format=json identity/entity/id` and `vault read sys/internal/counters/activity` for active clients; root token presence: `vault read sys/internal/counters/tokens`; policies with `path "*"` and `capabilities = ["sudo"]` via `vault policy read <name>` |
| BeyondTrust Password Safe | `GET /BeyondTrust/api/public/v3/ManagedAccounts`, `GET .../Sessions?status=1`, `GET .../UserGroups/{id}/Users` |
| Delinea Secret Server | `GET /SecretServer/api/v1/secrets?filter.includeInactive=false`, `GET /api/v1/reports/execute` with the built-in "Secret Access" report, `GET /api/v1/users?filter.includeInactive=false` for admins |

## HR source of truth

Workday: `GET /ccx/api/v1/<tenant>/workers?Active=true` or the Report-as-a-Service RaaS URL agreed with HR; the join key is usually employee ID, not email. SAP SuccessFactors: `GET /odata/v2/EmpJob?$filter=emplStatus ne '...'`. Export a one-column leaver list for `scripts/access_review_report.py --leavers`.
