# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The curated base of the registry: high-value operations of every domain.

They are always ``known``, even before any discovery and on machines without
the CLI, so Navin can plan for a platform it has not met yet. Discovery adds
the thousands of operations of what is really installed; these records add
what discovery cannot read from a help page: the exact verification, the
rollback path and the permissions an expert would check.

Row: (provider, service, name, command, risk, categories, verify, rollback,
permissions, endpoint, example). Risk is re-derived from the command by the
classifier when the record is used; the value here is the documented one.
"""

from __future__ import annotations

from navin.infra.model import Operation, effects_for

_R, _P, _C, _X = "read", "plan", "change", "critical"

CURATED: list[tuple] = [
    # -- AWS --------------------------------------------------------------------
    ("aws", "sts", "Who am I", "aws sts get-caller-identity", _R, [], [], [], ["sts:GetCallerIdentity"], "POST https://sts.amazonaws.com/ Action=GetCallerIdentity", "aws sts get-caller-identity --profile <profile>"),
    ("aws", "sso", "Sign in with IAM Identity Center", "aws sso login", _C, ["local"], ["aws sts get-caller-identity --profile <profile>"], ["aws sso logout"], ["sso:GetRoleCredentials (on the assigned permission set)"], "IAM Identity Center OIDC device flow", "aws sso login --profile <profile>"),
    ("aws", "sso", "Configure an Identity Center profile", "aws configure sso", _C, ["local"], ["aws sts get-caller-identity --profile <profile>"], ["remove the [profile] block from ~/.aws/config"], ["sso:ListAccounts", "sso:ListAccountRoles"], "", "aws configure sso --profile <profile>"),
    ("aws", "sts", "Assume a temporary role", "aws sts assume-role", _R, [], [], [], ["sts:AssumeRole"], "POST https://sts.amazonaws.com/ Action=AssumeRole", "aws sts assume-role --role-arn arn:aws:iam::<account>:role/<role> --role-session-name navin"),
    ("aws", "eks", "List EKS clusters", "aws eks list-clusters", _R, [], [], [], ["eks:ListClusters"], "GET /clusters", "aws eks list-clusters --region <region>"),
    ("aws", "eks", "Describe an EKS cluster", "aws eks describe-cluster", _R, [], [], [], ["eks:DescribeCluster"], "GET /clusters/{name}", "aws eks describe-cluster --name <cluster> --region <region>"),
    ("aws", "eks", "Write kubeconfig for EKS", "aws eks update-kubeconfig", _C, ["local"], ["kubectl get nodes"], ["kubectl config delete-context <context>"], ["eks:DescribeCluster"], "GET /clusters/{name}", "aws eks update-kubeconfig --name <cluster> --region <region> --alias <alias>"),
    ("aws", "eks", "Create an EKS cluster", "aws eks create-cluster", _X, ["costly"], ["aws eks wait cluster-active --name <cluster>", "aws eks describe-cluster --name <cluster>"], ["aws eks delete-cluster --name <cluster>"], ["eks:CreateCluster", "iam:PassRole"], "POST /clusters", "aws eks create-cluster --name <cluster> --role-arn <role> --resources-vpc-config subnetIds=<a>,<b>"),
    ("aws", "eks", "Create a managed node group", "aws eks create-nodegroup", _X, ["costly"], ["aws eks wait nodegroup-active --cluster-name <cluster> --nodegroup-name <ng>"], ["aws eks delete-nodegroup --cluster-name <cluster> --nodegroup-name <ng>"], ["eks:CreateNodegroup", "iam:PassRole"], "POST /clusters/{name}/node-groups", "aws eks create-nodegroup --cluster-name <cluster> --nodegroup-name <ng> --subnets <a> <b> --node-role <role>"),
    ("aws", "ec2", "List instances", "aws ec2 describe-instances", _R, [], [], [], ["ec2:DescribeInstances"], "POST https://ec2.{region}.amazonaws.com/ Action=DescribeInstances", "aws ec2 describe-instances --filters Name=instance-state-name,Values=running"),
    ("aws", "ec2", "List VPCs", "aws ec2 describe-vpcs", _R, [], [], [], ["ec2:DescribeVpcs"], "Action=DescribeVpcs", "aws ec2 describe-vpcs"),
    ("aws", "ec2", "List subnets", "aws ec2 describe-subnets", _R, [], [], [], ["ec2:DescribeSubnets"], "Action=DescribeSubnets", "aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc>"),
    ("aws", "ec2", "List route tables", "aws ec2 describe-route-tables", _R, [], [], [], ["ec2:DescribeRouteTables"], "Action=DescribeRouteTables", "aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc>"),
    ("aws", "ec2", "List security groups", "aws ec2 describe-security-groups", _R, [], [], [], ["ec2:DescribeSecurityGroups"], "Action=DescribeSecurityGroups", "aws ec2 describe-security-groups --filters Name=vpc-id,Values=<vpc>"),
    ("aws", "ec2", "Open a port in a security group", "aws ec2 authorize-security-group-ingress", _X, ["network"], ["aws ec2 describe-security-groups --group-ids <sg>"], ["aws ec2 revoke-security-group-ingress --group-id <sg> --ip-permissions <same>"], ["ec2:AuthorizeSecurityGroupIngress"], "Action=AuthorizeSecurityGroupIngress", "aws ec2 authorize-security-group-ingress --group-id <sg> --protocol tcp --port 443 --cidr <cidr>"),
    ("aws", "ec2", "Network reachability analysis", "aws ec2 start-network-insights-analysis", _C, [], ["aws ec2 describe-network-insights-analyses --network-insights-analysis-ids <id>"], ["aws ec2 delete-network-insights-analysis --network-insights-analysis-id <id>"], ["ec2:StartNetworkInsightsAnalysis"], "Action=StartNetworkInsightsAnalysis", "aws ec2 start-network-insights-analysis --network-insights-path-id <path>"),
    ("aws", "ec2", "Snapshot an EBS volume", "aws ec2 create-snapshot", _C, ["backup"], ["aws ec2 wait snapshot-completed --snapshot-ids <snap>"], ["aws ec2 delete-snapshot --snapshot-id <snap>"], ["ec2:CreateSnapshot"], "Action=CreateSnapshot", "aws ec2 create-snapshot --volume-id <vol> --description navin-backup"),
    ("aws", "s3api", "List buckets", "aws s3api list-buckets", _R, [], [], [], ["s3:ListAllMyBuckets"], "GET https://s3.amazonaws.com/", "aws s3api list-buckets"),
    ("aws", "s3api", "Block public access on a bucket", "aws s3api put-public-access-block", _C, ["data"], ["aws s3api get-public-access-block --bucket <bucket>"], ["aws s3api delete-public-access-block --bucket <bucket>"], ["s3:PutBucketPublicAccessBlock"], "PUT /?publicAccessBlock", "aws s3api put-public-access-block --bucket <bucket> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"),
    ("aws", "s3api", "Enable bucket versioning", "aws s3api put-bucket-versioning", _C, ["data"], ["aws s3api get-bucket-versioning --bucket <bucket>"], ["aws s3api put-bucket-versioning --bucket <bucket> --versioning-configuration Status=Suspended"], ["s3:PutBucketVersioning"], "PUT /?versioning", "aws s3api put-bucket-versioning --bucket <bucket> --versioning-configuration Status=Enabled"),
    ("aws", "s3", "Sync files to S3", "aws s3 sync", _C, [], ["aws s3 ls s3://<bucket>/<prefix> --recursive --summarize"], ["restore the previous object versions (versioning) or sync back from the source"], ["s3:PutObject", "s3:ListBucket"], "PUT /{key}", "aws s3 sync ./dist s3://<bucket>/<prefix> --dryrun"),
    ("aws", "iam", "Simulate a principal policy", "aws iam simulate-principal-policy", _P, [], [], [], ["iam:SimulatePrincipalPolicy"], "Action=SimulatePrincipalPolicy", "aws iam simulate-principal-policy --policy-source-arn <arn> --action-names eks:DescribeCluster"),
    ("aws", "iam", "Credential report", "aws iam generate-credential-report", _R, [], ["aws iam get-credential-report"], [], ["iam:GenerateCredentialReport"], "Action=GenerateCredentialReport", "aws iam generate-credential-report && aws iam get-credential-report --query Content --output text | base64 -d"),
    ("aws", "iam", "Attach a policy to a role", "aws iam attach-role-policy", _X, ["iam"], ["aws iam list-attached-role-policies --role-name <role>"], ["aws iam detach-role-policy --role-name <role> --policy-arn <arn>"], ["iam:AttachRolePolicy"], "Action=AttachRolePolicy", "aws iam attach-role-policy --role-name <role> --policy-arn <arn>"),
    ("aws", "accessanalyzer", "List access analyzer findings", "aws accessanalyzer list-findings", _R, [], [], [], ["access-analyzer:ListFindings"], "POST /finding", "aws accessanalyzer list-findings --analyzer-arn <arn>"),
    ("aws", "rds", "List databases", "aws rds describe-db-instances", _R, [], [], [], ["rds:DescribeDBInstances"], "Action=DescribeDBInstances", "aws rds describe-db-instances"),
    ("aws", "rds", "Create a PostgreSQL instance", "aws rds create-db-instance", _X, ["costly", "data"], ["aws rds wait db-instance-available --db-instance-identifier <db>", "aws rds describe-db-instances --db-instance-identifier <db>"], ["aws rds delete-db-instance --db-instance-identifier <db> --final-db-snapshot-identifier <db>-final"], ["rds:CreateDBInstance"], "Action=CreateDBInstance", "aws rds create-db-instance --db-instance-identifier <db> --engine postgres --db-instance-class db.t4g.medium --allocated-storage 50 --multi-az --storage-encrypted --manage-master-user-password --master-username app"),
    ("aws", "rds", "Snapshot a database", "aws rds create-db-snapshot", _C, ["backup"], ["aws rds wait db-snapshot-available --db-snapshot-identifier <snap>"], ["aws rds delete-db-snapshot --db-snapshot-identifier <snap>"], ["rds:CreateDBSnapshot"], "Action=CreateDBSnapshot", "aws rds create-db-snapshot --db-instance-identifier <db> --db-snapshot-identifier <db>-navin"),
    ("aws", "rds", "Point-in-time restore", "aws rds restore-db-instance-to-point-in-time", _X, ["data", "costly"], ["aws rds wait db-instance-available --db-instance-identifier <target>"], ["aws rds delete-db-instance --db-instance-identifier <target> --skip-final-snapshot"], ["rds:RestoreDBInstanceToPointInTime"], "Action=RestoreDBInstanceToPointInTime", "aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier <db> --target-db-instance-identifier <db>-restore --restore-time <iso>"),
    ("aws", "elasticache", "Create a Redis replication group", "aws elasticache create-replication-group", _X, ["costly"], ["aws elasticache wait replication-group-available --replication-group-id <id>"], ["aws elasticache delete-replication-group --replication-group-id <id> --final-snapshot-identifier <id>-final"], ["elasticache:CreateReplicationGroup"], "Action=CreateReplicationGroup", "aws elasticache create-replication-group --replication-group-id <id> --replication-group-description app --engine redis --cache-node-type cache.t4g.small --num-node-groups 1 --replicas-per-node-group 1 --automatic-failover-enabled --multi-az-enabled --transit-encryption-enabled --at-rest-encryption-enabled"),
    ("aws", "elbv2", "List load balancers", "aws elbv2 describe-load-balancers", _R, [], [], [], ["elasticloadbalancing:DescribeLoadBalancers"], "Action=DescribeLoadBalancers", "aws elbv2 describe-load-balancers"),
    ("aws", "elbv2", "Target health", "aws elbv2 describe-target-health", _R, [], [], [], ["elasticloadbalancing:DescribeTargetHealth"], "Action=DescribeTargetHealth", "aws elbv2 describe-target-health --target-group-arn <arn>"),
    ("aws", "route53", "List DNS records", "aws route53 list-resource-record-sets", _R, [], [], [], ["route53:ListResourceRecordSets"], "GET /2013-04-01/hostedzone/{Id}/rrset", "aws route53 list-resource-record-sets --hosted-zone-id <zone>"),
    ("aws", "route53", "Change DNS records", "aws route53 change-resource-record-sets", _X, ["network"], ["aws route53 get-change --id <change>", "dig +short <name> @<authoritative>"], ["apply the inverse change batch (UPSERT the previous values, captured first)"], ["route53:ChangeResourceRecordSets"], "POST /2013-04-01/hostedzone/{Id}/rrset", "aws route53 change-resource-record-sets --hosted-zone-id <zone> --change-batch file://change.json"),
    ("aws", "acm", "Request a TLS certificate", "aws acm request-certificate", _C, [], ["aws acm describe-certificate --certificate-arn <arn> --query Certificate.Status"], ["aws acm delete-certificate --certificate-arn <arn>"], ["acm:RequestCertificate"], "POST / X-Amz-Target=CertificateManager.RequestCertificate", "aws acm request-certificate --domain-name <domain> --validation-method DNS"),
    ("aws", "cloudformation", "Create a change set", "aws cloudformation create-change-set", _P, [], ["aws cloudformation describe-change-set --stack-name <stack> --change-set-name <cs>"], ["aws cloudformation delete-change-set --stack-name <stack> --change-set-name <cs>"], ["cloudformation:CreateChangeSet"], "Action=CreateChangeSet", "aws cloudformation create-change-set --stack-name <stack> --change-set-name navin --template-body file://template.yaml"),
    ("aws", "cloudformation", "Execute a reviewed change set", "aws cloudformation execute-change-set", _C, [], ["aws cloudformation wait stack-update-complete --stack-name <stack>"], ["aws cloudformation cancel-update-stack --stack-name <stack> (during update); CloudFormation rolls back failed updates itself"], ["cloudformation:ExecuteChangeSet"], "Action=ExecuteChangeSet", "aws cloudformation execute-change-set --stack-name <stack> --change-set-name navin"),
    ("aws", "cloudformation", "Detect stack drift", "aws cloudformation detect-stack-drift", _R, [], ["aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id <id>"], [], ["cloudformation:DetectStackDrift"], "Action=DetectStackDrift", "aws cloudformation detect-stack-drift --stack-name <stack>"),
    ("aws", "cloudcontrol", "Read any resource (Cloud Control)", "aws cloudcontrol get-resource", _R, [], [], [], ["cloudformation:GetResource"], "POST / X-Amz-Target=CloudApiService.GetResource", "aws cloudcontrol get-resource --type-name AWS::EC2::VPC --identifier <vpc>"),
    ("aws", "cloudcontrol", "Create any resource (Cloud Control)", "aws cloudcontrol create-resource", _C, [], ["aws cloudcontrol get-resource-request-status --request-token <token>"], ["aws cloudcontrol delete-resource --type-name <type> --identifier <id>"], ["cloudformation:CreateResource"], "X-Amz-Target=CloudApiService.CreateResource", "aws cloudcontrol create-resource --type-name AWS::Logs::LogGroup --desired-state '{\"LogGroupName\":\"app\"}'"),
    ("aws", "cloudwatch", "Read metrics", "aws cloudwatch get-metric-data", _R, [], [], [], ["cloudwatch:GetMetricData"], "Action=GetMetricData", "aws cloudwatch get-metric-data --metric-data-queries file://queries.json --start-time <t0> --end-time <t1>"),
    ("aws", "cloudwatch", "List alarms in alarm", "aws cloudwatch describe-alarms", _R, [], [], [], ["cloudwatch:DescribeAlarms"], "Action=DescribeAlarms", "aws cloudwatch describe-alarms --state-value ALARM"),
    ("aws", "logs", "Tail CloudWatch logs", "aws logs tail", _R, [], [], [], ["logs:FilterLogEvents"], "X-Amz-Target=Logs_20140328.FilterLogEvents", "aws logs tail /aws/eks/<cluster>/cluster --since 30m --follow"),
    ("aws", "ce", "Cost and usage", "aws ce get-cost-and-usage", _R, [], [], [], ["ce:GetCostAndUsage"], "X-Amz-Target=AWSInsightsIndexService.GetCostAndUsage", "aws ce get-cost-and-usage --time-period Start=<yyyy-mm-01>,End=<yyyy-mm-dd> --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE"),
    ("aws", "compute-optimizer", "Rightsizing recommendations", "aws compute-optimizer get-ec2-instance-recommendations", _R, [], [], [], ["compute-optimizer:GetEC2InstanceRecommendations"], "", "aws compute-optimizer get-ec2-instance-recommendations"),
    ("aws", "budgets", "Create a budget", "aws budgets create-budget", _C, [], ["aws budgets describe-budgets --account-id <account>"], ["aws budgets delete-budget --account-id <account> --budget-name <name>"], ["budgets:ModifyBudget"], "", "aws budgets create-budget --account-id <account> --budget file://budget.json --notifications-with-subscribers file://notify.json"),
    ("aws", "backup", "Start an on-demand backup", "aws backup start-backup-job", _C, ["backup"], ["aws backup describe-backup-job --backup-job-id <job>"], ["aws backup delete-recovery-point --backup-vault-name <vault> --recovery-point-arn <rp>"], ["backup:StartBackupJob", "iam:PassRole"], "PUT /backup-jobs", "aws backup start-backup-job --backup-vault-name <vault> --resource-arn <arn> --iam-role-arn <role>"),
    ("aws", "resourcegroupstaggingapi", "Inventory every tagged resource", "aws resourcegroupstaggingapi get-resources", _R, [], [], [], ["tag:GetResources"], "", "aws resourcegroupstaggingapi get-resources --region <region>"),
    ("aws", "securityhub", "Security findings", "aws securityhub get-findings", _R, [], [], [], ["securityhub:GetFindings"], "POST /findings", "aws securityhub get-findings --filters '{\"SeverityLabel\":[{\"Value\":\"CRITICAL\",\"Comparison\":\"EQUALS\"}]}'"),
    # -- Google Cloud -------------------------------------------------------------
    ("gcp", "auth", "Sign in", "gcloud auth login", _C, ["local"], ["gcloud auth list"], ["gcloud auth revoke"], ["none (local credentials only)"], "", "gcloud auth login --update-adc"),
    ("gcp", "config", "Impersonate a service account", "gcloud config set auth/impersonate_service_account", _C, ["local"], ["gcloud auth print-access-token --impersonate-service-account <sa>"], ["gcloud config unset auth/impersonate_service_account"], ["iam.serviceAccounts.getAccessToken"], "", "gcloud config set auth/impersonate_service_account <sa>@<project>.iam.gserviceaccount.com"),
    ("gcp", "asset", "Inventory every resource (Cloud Asset)", "gcloud asset search-all-resources", _R, [], [], [], ["cloudasset.assets.searchAllResources"], "GET https://cloudasset.googleapis.com/v1/{scope}:searchAllResources", "gcloud asset search-all-resources --scope=projects/<project>"),
    ("gcp", "compute", "List instances", "gcloud compute instances list", _R, [], [], [], ["compute.instances.list"], "GET https://compute.googleapis.com/compute/v1/projects/{project}/aggregated/instances", "gcloud compute instances list --project <project>"),
    ("gcp", "compute", "Create a firewall rule", "gcloud compute firewall-rules create", _X, ["network"], ["gcloud compute firewall-rules describe <rule>"], ["gcloud compute firewall-rules delete <rule>"], ["compute.firewalls.create"], "POST /compute/v1/projects/{project}/global/firewalls", "gcloud compute firewall-rules create <rule> --network <net> --allow tcp:443 --source-ranges <cidr>"),
    ("gcp", "container", "List GKE clusters", "gcloud container clusters list", _R, [], [], [], ["container.clusters.list"], "GET https://container.googleapis.com/v1/projects/{project}/locations/-/clusters", "gcloud container clusters list"),
    ("gcp", "container", "Get GKE credentials", "gcloud container clusters get-credentials", _C, ["local"], ["kubectl get nodes"], ["kubectl config delete-context <context>"], ["container.clusters.get"], "", "gcloud container clusters get-credentials <cluster> --region <region>"),
    ("gcp", "container", "Create a GKE Autopilot cluster", "gcloud container clusters create-auto", _X, ["costly"], ["gcloud container clusters describe <cluster> --region <region> --format=value(status)"], ["gcloud container clusters delete <cluster> --region <region>"], ["container.clusters.create"], "POST /v1/projects/{project}/locations/{location}/clusters", "gcloud container clusters create-auto <cluster> --region <region>"),
    ("gcp", "run", "Deploy to Cloud Run", "gcloud run deploy", _C, [], ["gcloud run services describe <svc> --region <region> --format=value(status.url)"], ["gcloud run services update-traffic <svc> --to-revisions <previous>=100"], ["run.services.create", "run.services.update", "iam.serviceAccounts.actAs"], "POST https://run.googleapis.com/v2/projects/{project}/locations/{location}/services", "gcloud run deploy <svc> --image <image> --region <region>"),
    ("gcp", "sql", "List Cloud SQL instances", "gcloud sql instances list", _R, [], [], [], ["cloudsql.instances.list"], "GET https://sqladmin.googleapis.com/v1/projects/{project}/instances", "gcloud sql instances list"),
    ("gcp", "sql", "Back up Cloud SQL", "gcloud sql backups create", _C, ["backup"], ["gcloud sql backups list --instance <db>"], ["gcloud sql backups delete <backup-id> --instance <db>"], ["cloudsql.backupRuns.create"], "POST /v1/projects/{project}/instances/{instance}/backupRuns", "gcloud sql backups create --instance <db>"),
    ("gcp", "dns", "Change DNS records", "gcloud dns record-sets update", _X, ["network"], ["gcloud dns record-sets describe <name> --type <type> --zone <zone>"], ["gcloud dns record-sets update <name> --type <type> --zone <zone> --rrdatas <previous>"], ["dns.resourceRecordSets.update"], "PATCH /dns/v1/projects/{project}/managedZones/{zone}/rrsets/{name}/{type}", "gcloud dns record-sets update <name> --type A --zone <zone> --rrdatas <ip> --ttl 300"),
    ("gcp", "projects", "Read the IAM policy", "gcloud projects get-iam-policy", _R, [], [], [], ["resourcemanager.projects.getIamPolicy"], "POST /v1/projects/{project}:getIamPolicy", "gcloud projects get-iam-policy <project> --format=json"),
    ("gcp", "projects", "Grant a role", "gcloud projects add-iam-policy-binding", _X, ["iam"], ["gcloud projects get-iam-policy <project> --flatten=bindings --filter=bindings.role:<role>"], ["gcloud projects remove-iam-policy-binding <project> --member <member> --role <role>"], ["resourcemanager.projects.setIamPolicy"], "POST /v1/projects/{project}:setIamPolicy", "gcloud projects add-iam-policy-binding <project> --member serviceAccount:<sa> --role roles/<role>"),
    ("gcp", "billing", "Billing account of a project", "gcloud billing projects describe", _R, [], [], [], ["billing.resourceAssociations.list"], "", "gcloud billing projects describe <project>"),
    ("gcp", "recommender", "Rightsizing recommendations", "gcloud recommender recommendations list", _R, [], [], [], ["recommender.computeInstanceMachineTypeRecommendations.list"], "", "gcloud recommender recommendations list --recommender=google.compute.instance.MachineTypeRecommender --location <zone>"),
    # -- Azure -----------------------------------------------------------------------
    ("azure", "account", "Current subscription", "az account show", _R, [], [], [], ["Reader (any role on the subscription)"], "", "az account show -o table"),
    ("azure", "resource", "Inventory resources (Resource Graph)", "az graph query", _R, [], [], [], ["Microsoft.ResourceGraph/resources/read"], "POST https://management.azure.com/providers/Microsoft.ResourceGraph/resources", "az graph query -q \"Resources | summarize count() by type\""),
    ("azure", "deployment", "Preview a deployment (what-if)", "az deployment group what-if", _P, [], [], [], ["Microsoft.Resources/deployments/whatIf/action"], "POST .../deployments/{name}/whatIf", "az deployment group what-if -g <rg> --template-file main.bicep"),
    ("azure", "deployment", "Deploy a template", "az deployment group create", _C, [], ["az deployment group show -g <rg> -n <name> --query properties.provisioningState"], ["redeploy the previous template (az deployment group create --rollback-on-error)"], ["Microsoft.Resources/deployments/write"], "PUT .../deployments/{name}", "az deployment group create -g <rg> --template-file main.bicep --rollback-on-error"),
    ("azure", "aks", "Get AKS credentials", "az aks get-credentials", _C, ["local"], ["kubectl get nodes"], ["kubectl config delete-context <context>"], ["Microsoft.ContainerService/managedClusters/listClusterUserCredential/action"], "", "az aks get-credentials -g <rg> -n <cluster>"),
    ("azure", "aks", "Create an AKS cluster", "az aks create", _X, ["costly"], ["az aks show -g <rg> -n <cluster> --query provisioningState"], ["az aks delete -g <rg> -n <cluster>"], ["Microsoft.ContainerService/managedClusters/write"], "PUT .../managedClusters/{name}", "az aks create -g <rg> -n <cluster> --node-count 3 --zones 1 2 3 --enable-managed-identity"),
    ("azure", "network", "Add an NSG rule", "az network nsg rule create", _X, ["network"], ["az network nsg rule show -g <rg> --nsg-name <nsg> -n <rule>"], ["az network nsg rule delete -g <rg> --nsg-name <nsg> -n <rule>"], ["Microsoft.Network/networkSecurityGroups/securityRules/write"], "PUT .../securityRules/{name}", "az network nsg rule create -g <rg> --nsg-name <nsg> -n allow-https --priority 200 --destination-port-ranges 443"),
    ("azure", "role", "Assign a role", "az role assignment create", _X, ["iam"], ["az role assignment list --assignee <principal> --scope <scope>"], ["az role assignment delete --assignee <principal> --role <role> --scope <scope>"], ["Microsoft.Authorization/roleAssignments/write"], "PUT .../roleAssignments/{id}", "az role assignment create --assignee <principal> --role Reader --scope <scope>"),
    ("azure", "monitor", "Query logs (KQL)", "az monitor log-analytics query", _R, [], [], [], ["Microsoft.OperationalInsights/workspaces/query/read"], "", "az monitor log-analytics query -w <workspace-id> --analytics-query \"AzureActivity | take 20\""),
    ("azure", "consumption", "Usage and cost", "az consumption usage list", _R, [], [], [], ["Microsoft.Consumption/usageDetails/read"], "", "az consumption usage list --start-date <d0> --end-date <d1>"),
    # -- Private cloud --------------------------------------------------------------
    ("openstack", "compute", "List servers", "openstack server list", _R, [], [], [], ["compute:get_all"], "GET /servers/detail", "openstack server list --long"),
    ("openstack", "compute", "Create a server", "openstack server create", _X, ["costly"], ["openstack server show <name> -c status"], ["openstack server delete <name>"], ["compute:create"], "POST /servers", "openstack server create --flavor <flavor> --image <image> --network <net> <name>"),
    ("openstack", "network", "List networks", "openstack network list", _R, [], [], [], ["get_network"], "GET /v2.0/networks", "openstack network list"),
    ("vsphere", "vm", "List virtual machines", "govc find / -type m", _R, [], [], [], ["System.Read"], "vSphere API PropertyCollector", "govc find / -type m"),
    ("vsphere", "vm", "VM details", "govc vm.info", _R, [], [], [], ["System.Read"], "", "govc vm.info -r <vm>"),
    ("vsphere", "vm", "Snapshot a VM", "govc snapshot.create", _C, ["backup"], ["govc snapshot.tree -vm <vm>"], ["govc snapshot.remove -vm <vm> <snapshot>"], ["VirtualMachine.State.CreateSnapshot"], "CreateSnapshot_Task", "govc snapshot.create -vm <vm> navin-before-change"),
    ("vsphere", "vm", "Revert a VM to a snapshot", "govc snapshot.revert", _X, ["data"], ["govc vm.info <vm>", "govc snapshot.tree -vm <vm> -C"], ["not reversible: take govc snapshot.create -vm <vm> pre-revert first, then revert to pre-revert"], ["VirtualMachine.State.RevertToSnapshot"], "RevertToSnapshot_Task", "govc snapshot.revert -vm <vm> <snapshot>"),
    ("proxmox", "qemu", "List VMs", "qm list", _R, [], [], [], ["VM.Audit"], "GET /api2/json/nodes/{node}/qemu", "qm list"),
    ("proxmox", "qemu", "Snapshot a VM", "qm snapshot", _C, ["backup"], ["qm listsnapshot <vmid>"], ["qm delsnapshot <vmid> <name>"], ["VM.Snapshot"], "POST /api2/json/nodes/{node}/qemu/{vmid}/snapshot", "qm snapshot <vmid> navin-before-change"),
    ("proxmox", "lxc", "List containers", "pct list", _R, [], [], [], ["VM.Audit"], "GET /api2/json/nodes/{node}/lxc", "pct list"),
    ("proxmox", "api", "Read the cluster", "pvesh get /cluster/resources", _R, [], [], [], ["Sys.Audit"], "GET /api2/json/cluster/resources", "pvesh get /cluster/resources --output-format json"),
    # -- Containers -----------------------------------------------------------------
    ("docker", "engine", "List containers", "docker ps", _R, [], [], [], [], "GET /containers/json", "docker ps -a --format '{{.Names}} {{.Status}}'"),
    ("docker", "engine", "Inspect a container", "docker inspect", _R, [], [], [], [], "GET /containers/{id}/json", "docker inspect <container>"),
    ("docker", "engine", "Build an image", "docker build", _C, [], ["docker image inspect <image>"], ["docker rmi <image>"], [], "POST /build", "docker build -t <image>:<tag> ."),
    ("docker", "engine", "Run a container", "docker run", _C, [], ["docker ps --filter name=<name>"], ["docker rm -f <name>"], [], "POST /containers/create + /start", "docker run -d --name <name> -p 8080:80 <image>"),
    ("docker", "compose", "Start a stack", "docker compose up", _C, [], ["docker compose ps"], ["docker compose down (keep volumes)"], [], "Compose spec", "docker compose up -d --wait"),
    ("docker", "compose", "Render the stack", "docker compose config", _P, [], [], [], [], "", "docker compose config"),
    ("docker", "context", "Docker contexts", "docker context ls", _R, [], [], [], [], "", "docker context ls"),
    ("podman", "engine", "List containers", "podman ps", _R, [], [], [], [], "GET /libpod/containers/json", "podman ps -a"),
    ("podman", "kube", "Generate Kubernetes YAML", "podman kube generate", _P, [], [], [], [], "", "podman kube generate <pod> > pod.yaml"),
    ("containerd", "cri", "CRI containers on a node", "crictl ps", _R, [], [], [], [], "CRI ListContainers", "crictl ps -a"),
    ("containerd", "nerdctl", "containerd containers", "nerdctl ps", _R, [], [], [], [], "", "nerdctl --namespace k8s.io ps -a"),
    # -- Kubernetes ------------------------------------------------------------------
    ("kubernetes", "discovery", "API resources (CRDs included)", "kubectl api-resources -o wide", _R, [], [], [], ["get on /apis"], "GET /apis + GET /api/v1", "kubectl api-resources -o wide"),
    ("kubernetes", "discovery", "Schema of a resource", "kubectl explain", _R, [], [], [], ["get on /openapi/v3"], "GET /openapi/v3", "kubectl explain deployment.spec.strategy --recursive"),
    ("kubernetes", "core", "Everything in a namespace", "kubectl get all", _R, [], [], [], ["list on pods, services, deployments..."], "", "kubectl get all -n <ns> -o wide"),
    ("kubernetes", "core", "Events", "kubectl get events", _R, [], [], [], ["list events"], "GET /api/v1/namespaces/{ns}/events", "kubectl get events -n <ns> --sort-by=.lastTimestamp"),
    ("kubernetes", "core", "Logs", "kubectl logs", _R, [], [], [], ["get pods/log"], "GET /api/v1/namespaces/{ns}/pods/{pod}/log", "kubectl logs deploy/<app> -n <ns> --since=30m --all-containers"),
    ("kubernetes", "core", "Server-side diff", "kubectl diff", _P, [], [], [], ["patch (dry run)"], "PATCH ?dryRun=All", "kubectl diff -f k8s/"),
    ("kubernetes", "core", "Apply manifests", "kubectl apply", _C, [], ["kubectl rollout status <kind>/<name> -n <ns>", "kubectl get -f <file>"], ["kubectl apply -f <snapshot.yaml>", "kubectl rollout undo <kind>/<name> -n <ns>"], ["patch / create on the resources"], "PATCH (server-side apply)", "kubectl apply -f k8s/ --server-side"),
    ("kubernetes", "core", "Restart a workload", "kubectl rollout restart", _C, [], ["kubectl rollout status deploy/<app> -n <ns> --timeout=300s"], ["kubectl rollout undo deploy/<app> -n <ns>"], ["patch deployments"], "PATCH /apis/apps/v1/namespaces/{ns}/deployments/{name}", "kubectl rollout restart deploy/<app> -n <ns>"),
    ("kubernetes", "core", "Roll back a workload", "kubectl rollout undo", _C, [], ["kubectl rollout status deploy/<app> -n <ns>"], ["kubectl rollout undo deploy/<app> -n <ns> --to-revision=<previous>"], ["patch deployments"], "", "kubectl rollout undo deploy/<app> -n <ns>"),
    ("kubernetes", "core", "Scale a workload", "kubectl scale", _C, [], ["kubectl rollout status deploy/<app> -n <ns>"], ["kubectl scale deploy/<app> -n <ns> --replicas=<previous>"], ["patch deployments/scale"], "PATCH .../scale", "kubectl scale deploy/<app> -n <ns> --replicas=3"),
    ("kubernetes", "core", "Debug a pod", "kubectl debug", _C, ["remote-exec"], ["kubectl get pod <pod> -n <ns> -o jsonpath={.spec.ephemeralContainers[*].name}"], ["kubectl delete pod <debug-pod> -n <ns>"], ["create pods/ephemeralcontainers"], "", "kubectl debug -it <pod> -n <ns> --image=busybox --target=<container>"),
    ("kubernetes", "core", "Delete resources", "kubectl delete", _X, ["data"], ["kubectl get <kind> <name> -n <ns> (expect NotFound)"], ["kubectl apply -f <snapshot.yaml> (taken before the delete)"], ["delete on the resources"], "DELETE", "kubectl delete deploy/<app> -n <ns>"),
    ("kubernetes", "core", "Drain a node", "kubectl drain", _X, ["availability"], ["kubectl get node <node>"], ["kubectl uncordon <node>"], ["create pods/eviction", "patch nodes"], "POST .../pods/{pod}/eviction", "kubectl drain <node> --ignore-daemonsets --delete-emptydir-data"),
    ("kubernetes", "rbac", "Check a permission", "kubectl auth can-i", _R, [], [], [], ["create selfsubjectaccessreviews"], "POST /apis/authorization.k8s.io/v1/selfsubjectaccessreviews", "kubectl auth can-i --list -n <ns>"),
    ("kubernetes", "cert-manager", "Certificates", "kubectl get certificates -A", _R, [], [], [], ["list certificates.cert-manager.io"], "GET /apis/cert-manager.io/v1/certificates", "kubectl get certificates,certificaterequests -A"),
    ("kubernetes", "gatekeeper", "Policy violations (Gatekeeper)", "kubectl get constraints", _R, [], [], [], ["list constraints.gatekeeper.sh"], "", "kubectl get constraints -o json"),
    ("kubernetes", "kustomize", "Render a kustomization", "kubectl kustomize", _P, [], [], [], ["none (local render)"], "", "kubectl kustomize overlays/prod"),
    ("kubernetes", "k3s", "K3s cluster", "k3s kubectl get nodes", _R, [], [], [], ["get nodes", "root on the K3s server"], "", "k3s kubectl get nodes -o wide"),
    ("kubernetes", "microk8s", "MicroK8s status", "microk8s status", _R, [], [], [], ["root or microk8s group"], "", "microk8s status --wait-ready"),
    ("kubernetes", "eksctl", "EKS clusters (eksctl)", "eksctl get cluster", _R, [], [], [], ["eks:ListClusters"], "", "eksctl get cluster --region <region>"),
    ("openshift", "core", "Projects", "oc get projects", _R, [], [], [], ["list projects"], "GET /apis/project.openshift.io/v1/projects", "oc get projects"),
    ("openshift", "core", "New application", "oc new-app", _C, [], ["oc rollout status deployment/<app>"], ["oc delete all -l app=<app>"], ["create deployments, services, buildconfigs"], "", "oc new-app <image-or-repo> --name <app>"),
    ("openshift", "core", "Cluster operators", "oc get clusteroperators", _R, [], [], [], ["list clusteroperators"], "", "oc get clusteroperators"),
    ("openshift", "adm", "Who can", "oc adm policy who-can", _R, [], [], [], ["create resourceaccessreviews"], "", "oc adm policy who-can delete pods -n <ns>"),
    # -- IaC --------------------------------------------------------------------------
    ("terraform", "cli", "Initialize", "terraform init", _P, [], ["terraform validate"], [], [], "", "terraform init -input=false"),
    ("terraform", "cli", "Plan to a file", "terraform plan", _P, [], ["terraform show -json tfplan"], [], ["read on every managed resource"], "", "terraform plan -input=false -out=tfplan"),
    ("terraform", "cli", "Apply a saved plan", "terraform apply", _C, [], ["terraform plan -detailed-exitcode -input=false (exit 0: no drift)"], ["restore the state snapshot taken before (terraform state push) only if the state is damaged; otherwise revert the code in git and plan/apply again"], ["create/update/delete on the planned resources"], "", "terraform apply -input=false tfplan"),
    ("terraform", "cli", "Provider schemas", "terraform providers schema -json", _R, [], [], [], [], "", "terraform providers schema -json"),
    ("terraform", "cli", "Import an existing resource", "terraform import", _C, [], ["terraform plan -detailed-exitcode"], ["terraform state rm <address>"], [], "", "terraform import <address> <id>"),
    ("terraform", "cli", "Destroy", "terraform destroy", _X, ["data"], ["terraform state list (expect empty)"], ["recreate from code: terraform apply (data is gone unless backed up)"], [], "", "terraform plan -destroy -out=destroy.tfplan"),
    ("opentofu", "cli", "Plan to a file", "tofu plan", _P, [], [], [], [], "", "tofu plan -out=tfplan"),
    ("pulumi", "cli", "Preview", "pulumi preview", _P, [], [], [], [], "", "pulumi preview --diff -s <stack>"),
    ("pulumi", "cli", "Update", "pulumi up", _C, [], ["pulumi preview --expect-no-changes -s <stack>"], ["pulumi stack export --version <previous> | pulumi stack import, then pulumi up"], [], "", "pulumi up -s <stack> --yes"),
    ("cdk", "cli", "Diff", "cdk diff", _P, [], [], [], [], "", "cdk diff <stack>"),
    ("cdk", "cli", "Deploy", "cdk deploy", _C, [], ["cdk diff <stack> (expect no differences)"], ["deploy the previous commit"], ["cloudformation:*ChangeSet"], "", "cdk deploy <stack> --require-approval broadening"),
    # -- Configuration -----------------------------------------------------------------
    ("ansible", "playbook", "Dry run a playbook", "ansible-playbook --check --diff", _P, [], [], [], [], "", "ansible-playbook -i inventory site.yml --check --diff --limit <hosts>"),
    ("ansible", "playbook", "Run a playbook", "ansible-playbook", _C, ["remote-exec"], ["ansible-playbook -i inventory site.yml --check (expect changed=0)"], ["run the previous version of the playbook / role"], ["ssh + become on the hosts"], "", "ansible-playbook -i inventory site.yml --limit <hosts>"),
    ("ansible", "inventory", "Inventory graph", "ansible-inventory --graph", _R, [], [], [], [], "", "ansible-inventory -i inventory --graph"),
    ("ansible", "doc", "Modules catalog", "ansible-doc -l", _R, [], [], [], [], "", "ansible-doc -l -j"),
    ("linux", "ssh", "Run a remote command", "ssh", _C, ["remote-exec"], ["ssh <user>@<host> 'echo $?'"], ["run the inverse command over ssh (no automatic undo)"], ["ssh key on the host"], "", "ssh <user>@<host> 'systemctl is-active nginx'"),
    ("linux", "cloud-init", "cloud-init status", "cloud-init status", _R, [], [], [], [], "", "cloud-init status --long"),
    ("helm", "release", "Releases", "helm list -A", _R, [], [], [], ["list secrets (helm storage)"], "", "helm list -A"),
    ("helm", "release", "Render a chart", "helm template", _P, [], [], [], [], "", "helm template <release> <chart> -f values.yaml"),
    ("helm", "release", "Diff an upgrade", "helm diff upgrade", _P, [], [], [], [], "", "helm diff upgrade <release> <chart> -n <ns> -f values.yaml"),
    ("helm", "release", "Install or upgrade", "helm upgrade --install", _C, [], ["helm status <release> -n <ns>", "kubectl rollout status deploy/<app> -n <ns>"], ["helm rollback <release> <previous-revision> -n <ns>"], ["create/patch on the chart's resources", "secrets (release storage)"], "", "helm upgrade --install <release> <chart> -n <ns> -f values.yaml --atomic --wait --timeout 10m"),
    ("helm", "release", "Roll back", "helm rollback", _C, [], ["helm status <release> -n <ns>"], ["helm rollback <release> <revision-before> -n <ns>"], [], "", "helm rollback <release> <revision> -n <ns> --wait"),
    ("helm", "release", "Uninstall", "helm uninstall", _X, ["data"], ["helm status <release> -n <ns> (expect not found)"], ["helm install <release> <chart> -f <saved-values.yaml>"], [], "", "helm uninstall <release> -n <ns> --keep-history"),
    # -- CI/CD and GitOps -----------------------------------------------------------------
    ("github", "actions", "Workflow runs", "gh run list", _R, [], [], [], ["actions:read"], "GET /repos/{owner}/{repo}/actions/runs", "gh run list -R <owner>/<repo> --limit 10"),
    ("github", "actions", "Dispatch a workflow", "gh workflow run", _C, [], ["gh run watch <run-id> --exit-status"], ["gh run cancel <run-id>"], ["actions:write"], "POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches", "gh workflow run deploy.yml -R <owner>/<repo> -f environment=staging"),
    ("github", "actions", "Set a repository secret", "gh secret set", _X, ["secrets"], ["gh secret list -R <owner>/<repo>"], ["gh secret set <name> with the previous value, or gh secret delete <name>"], ["secrets:write"], "PUT /repos/{owner}/{repo}/actions/secrets/{name}", "gh secret set <NAME> -R <owner>/<repo> < value.txt"),
    ("gitlab", "ci", "Pipelines", "glab ci list", _R, [], [], [], ["read_api"], "GET /projects/{id}/pipelines", "glab ci list -R <group>/<project>"),
    ("gitlab", "ci", "Lint .gitlab-ci.yml", "glab ci lint", _P, [], [], [], ["api"], "POST /projects/{id}/ci/lint", "glab ci lint .gitlab-ci.yml"),
    ("gitlab", "ci", "Run a pipeline", "glab ci run", _C, [], ["glab ci status --live"], ["glab ci cancel"], ["api"], "POST /projects/{id}/pipeline", "glab ci run -b main"),
    ("jenkins", "api", "Job status", "curl $JENKINS_URL/job/<job>/lastBuild/api/json", _R, [], [], [], ["Job/Read"], "GET /job/{job}/lastBuild/api/json", "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/lastBuild/api/json"),
    ("jenkins", "api", "Trigger a job", "curl -X POST $JENKINS_URL/job/<job>/build", _C, [], ["curl $JENKINS_URL/job/<job>/lastBuild/api/json (result SUCCESS)"], ["Stop the build: POST /job/<job>/<n>/stop"], ["Job/Build"], "POST /job/{job}/build", "curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/build"),
    ("forgejo", "actions", "Forgejo Actions runs", "curl $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/tasks", _R, [], [], [], ["read:repository"], "GET /api/v1/repos/{owner}/{repo}/actions/tasks", "curl -fsS -H \"Authorization: token $FORGEJO_TOKEN\" $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/tasks"),
    ("argocd", "app", "Application status", "argocd app get", _R, [], [], [], ["applications, get"], "GET /api/v1/applications/{name}", "argocd app get <app> -o json"),
    ("argocd", "app", "Diff against Git", "argocd app diff", _P, [], [], [], ["applications, get"], "", "argocd app diff <app>"),
    ("argocd", "app", "Sync", "argocd app sync", _C, [], ["argocd app wait <app> --health --sync --timeout 600"], ["argocd app rollback <app> <history-id>"], ["applications, sync"], "POST /api/v1/applications/{name}/sync", "argocd app sync <app> --prune=false"),
    ("argocd", "app", "Roll back", "argocd app rollback", _C, [], ["argocd app wait <app> --health"], ["argocd app sync <app>"], ["applications, sync"], "PUT /api/v1/applications/{name}/rollback", "argocd app rollback <app> <history-id>"),
    ("flux", "gitops", "Flux health", "flux check", _R, [], [], [], [], "", "flux check && flux get all -A"),
    ("flux", "gitops", "Reconcile", "flux reconcile kustomization", _C, [], ["flux get kustomizations <name> (Ready True)"], ["git revert the commit, then flux reconcile source git <repo>"], ["patch kustomizations.kustomize.toolkit.fluxcd.io"], "", "flux reconcile kustomization <name> --with-source"),
    ("flux", "gitops", "Suspend reconciliation", "flux suspend kustomization", _C, [], ["flux get kustomizations <name> (SUSPENDED True)"], ["flux resume kustomization <name>"], [], "", "flux suspend kustomization <name>"),
    # -- Network --------------------------------------------------------------------------
    ("linux", "network", "Addresses and routes", "ip -br addr", _R, [], [], [], [], "netlink", "ip -br addr; ip route"),
    ("linux", "network", "Listening sockets", "ss -tulpn", _R, [], [], [], ["root for process names"], "", "ss -tulpn"),
    ("linux", "network", "Firewall ruleset", "nft list ruleset", _R, [], [], [], ["root"], "", "nft list ruleset"),
    ("linux", "network", "DNS lookup", "dig", _R, [], [], [], [], "", "dig +short <name> A @1.1.1.1"),
    ("linux", "network", "Capture packets", "tcpdump", _R, ["capture"], [], [], ["root / CAP_NET_RAW"], "", "tcpdump -i any -nn port 443 -c 100"),
    ("linux", "network", "TLS certificate of an endpoint", "openssl s_client", _R, [], [], [], [], "", "openssl s_client -connect <host>:443 -servername <host> </dev/null | openssl x509 -noout -dates -subject -issuer"),
    # -- Security ---------------------------------------------------------------------------
    ("security", "trivy", "Scan an image", "trivy image", _R, [], [], [], [], "", "trivy image --severity HIGH,CRITICAL <image>"),
    ("security", "trivy", "Scan a cluster", "trivy k8s", _R, [], [], [], ["cluster read"], "", "trivy k8s --report summary cluster"),
    ("security", "trivy", "Scan IaC", "trivy config", _R, [], [], [], [], "", "trivy config ."),
    ("security", "kube-bench", "CIS benchmark", "kube-bench run", _R, [], [], [], [], "", "kube-bench run --targets node"),
    ("security", "checkov", "IaC policy scan", "checkov -d", _R, [], [], [], [], "", "checkov -d . --compact"),
    ("security", "conftest", "OPA policies on manifests", "conftest test", _R, [], [], [], [], "", "conftest test k8s/ -p policy/"),
    ("security", "falco", "Runtime alerts", "kubectl logs -l app.kubernetes.io/name=falco", _R, [], [], [], [], "", "kubectl logs -n falco -l app.kubernetes.io/name=falco --since=1h"),
    ("security", "vault", "Vault status", "vault status", _R, [], [], [], [], "GET /v1/sys/seal-status", "vault status"),
    ("security", "vault", "Read a secret", "vault kv get", _R, ["secrets"], [], [], ["read on the path"], "GET /v1/{mount}/data/{path}", "vault kv get -field=<key> secret/<path>"),
    ("aws", "kms", "List keys and rotation", "aws kms get-key-rotation-status", _R, [], [], [], ["kms:GetKeyRotationStatus"], "", "aws kms get-key-rotation-status --key-id <key>"),
    # -- Observability ------------------------------------------------------------------------
    ("observability", "prometheus", "Instant query", "curl $PROM/api/v1/query", _R, [], [], [], [], "GET /api/v1/query", "curl -fsS \"$PROM/api/v1/query?query=up==0\""),
    ("observability", "prometheus", "Check rules", "promtool check rules", _P, [], [], [], [], "", "promtool check rules alerts/*.yaml"),
    ("observability", "alertmanager", "Active alerts", "amtool alert query", _R, [], [], [], [], "GET /api/v2/alerts", "amtool --alertmanager.url=$AM alert query"),
    ("observability", "loki", "Query logs", "logcli query", _R, [], [], [], [], "GET /loki/api/v1/query_range", "logcli query '{namespace=\"<ns>\"} |= \"error\"' --since=1h"),
    ("observability", "grafana", "Grafana health", "curl $GRAFANA/api/health", _R, [], [], [], [], "GET /api/health", "curl -fsS $GRAFANA/api/health"),
    ("observability", "tempo", "Find a trace", "curl $TEMPO/api/search", _R, [], [], [], [], "GET /api/search", "curl -fsS \"$TEMPO/api/search?tags=service.name%3D<svc>&limit=20\""),
    ("observability", "otel", "Validate a collector config", "otelcol validate", _P, [], [], [], [], "", "otelcol validate --config otel-collector.yaml"),
    # -- Data ------------------------------------------------------------------------------------
    ("databases", "postgresql", "Connection and replication", "psql -c 'select ...'", _R, [], [], [], ["CONNECT"], "", "psql \"$DATABASE_URL\" -c \"select now(), pg_is_in_recovery()\""),
    ("databases", "postgresql", "Logical backup", "pg_dump", _R, ["backup"], ["pg_restore --list <file> | head"], [], ["SELECT on the schema"], "", "pg_dump \"$DATABASE_URL\" -Fc -f backup-$(date +%F).dump"),
    ("databases", "postgresql", "Restore a backup", "pg_restore", _X, ["data"], ["psql -c 'select count(*) from <table>'"], ["restore the backup taken just before"], ["CREATE on the database"], "", "pg_restore -d \"$TARGET_URL\" --clean --if-exists backup.dump"),
    ("databases", "mysql", "Status", "mysql -e 'show status'", _R, [], [], [], [], "", "mysql -e \"show global status like 'Threads_connected'\""),
    ("databases", "redis", "Info", "redis-cli info", _R, [], [], [], [], "", "redis-cli -u \"$REDIS_URL\" info replication"),
    ("databases", "mongodb", "Replica set status", "mongosh --eval rs.status()", _R, [], [], [], [], "", "mongosh \"$MONGO_URL\" --quiet --eval \"rs.status().ok\""),
    ("databases", "kafka", "Topics", "kafka-topics --list", _R, [], [], [], [], "", "kafka-topics --bootstrap-server <broker> --describe --topic <topic>"),
    # -- Storage ------------------------------------------------------------------------------------
    ("storage", "minio", "Buckets", "mc ls", _R, [], [], [], [], "", "mc ls <alias>"),
    ("storage", "minio", "Mirror a bucket", "mc mirror", _C, [], ["mc diff <src> <dst>"], ["with versioning on: mc undo <dst> --recursive --force; otherwise mc mirror back from the source"], ["s3:PutObject", "s3:ListBucket"], "", "mc mirror --overwrite <src> <dst>"),
    ("storage", "ceph", "Cluster health", "ceph status", _R, [], [], [], [], "", "ceph -s && ceph osd df"),
    ("storage", "gcs", "Buckets", "gsutil ls", _R, [], [], [], ["storage.buckets.list"], "", "gsutil ls -p <project>"),
    ("storage", "nfs", "NFS exports", "showmount -e", _R, [], [], [], [], "", "showmount -e <server>"),
    ("aws", "efs", "File systems", "aws efs describe-file-systems", _R, [], [], [], ["elasticfilesystem:DescribeFileSystems"], "", "aws efs describe-file-systems"),
    # -- Servers ---------------------------------------------------------------------------------------
    ("linux", "systemd", "Service status", "systemctl status", _R, [], [], [], [], "D-Bus org.freedesktop.systemd1", "systemctl status <unit> --no-pager"),
    ("linux", "systemd", "Restart a service", "systemctl restart", _C, [], ["systemctl is-active <unit>"], ["systemctl restart <unit> after restoring the previous config"], ["root"], "", "systemctl restart <unit>"),
    ("linux", "systemd", "Failed units", "systemctl --failed", _R, [], [], [], [], "", "systemctl --failed --no-pager"),
    ("linux", "journald", "Logs of a unit", "journalctl -u", _R, [], [], [], [], "", "journalctl -u <unit> --since -1h --no-pager"),
    ("webservers", "nginx", "Test the configuration", "nginx -t", _P, [], [], [], ["root"], "", "nginx -t"),
    ("webservers", "nginx", "Reload", "nginx -s reload", _C, [], ["nginx -t", "curl -fsSI http://localhost/"], ["restore the previous config file, nginx -t, nginx -s reload"], ["root"], "", "nginx -t && nginx -s reload"),
    ("webservers", "haproxy", "Check the configuration", "haproxy -c -f", _P, [], [], [], [], "", "haproxy -c -f /etc/haproxy/haproxy.cfg"),
    ("webservers", "traefik", "Health", "traefik healthcheck", _R, [], [], [], [], "", "traefik healthcheck --ping"),
    ("windows", "services", "Windows services", "Get-Service", _R, [], [], [], [], "", "pwsh -Command \"Get-Service | Where-Object Status -eq 'Stopped'\""),
    ("windows", "services", "Restart a Windows service", "Restart-Service", _C, [], ["Get-Service <name>"], ["Restart-Service <name> after restoring the previous config"], ["Administrator"], "", "pwsh -Command \"Restart-Service <name>\""),
    # -- Tests -------------------------------------------------------------------------------------------
    ("testing", "http", "Health check", "curl -fsS", _R, [], [], [], [], "", "curl -fsS -o /dev/null -w '%{http_code} %{time_total}\\n' https://<host>/healthz"),
    ("testing", "load", "Load test", "k6 run", _C, ["load"], ["check k6 thresholds exit code 0 and the service error rate in monitoring"], ["stop the run (Ctrl-C); a load test leaves no persistent change, scale back autoscaled replicas if needed"], ["approval of the target owner"], "", "k6 run --vus 20 --duration 2m load.js"),
    ("testing", "load", "HTTP benchmark", "hey", _C, ["load"], ["read the hey summary: status code distribution all 2xx"], ["stop the run (Ctrl-C); no persistent change"], ["approval of the target owner"], "", "hey -z 30s -c 20 https://<host>/"),
    ("testing", "network", "TCP reachability", "nc -vz", _R, [], [], [], [], "", "nc -vz <host> 5432"),
    # -- FinOps --------------------------------------------------------------------------------------------
    ("finops", "infracost", "Cost of a Terraform plan", "infracost breakdown", _P, [], [], [], [], "", "infracost breakdown --path tfplan.json"),
    ("finops", "kubecost", "Kubernetes cost allocation", "kubectl cost namespace", _R, [], [], [], [], "", "kubectl cost namespace --window 7d"),
]


def curated_operations() -> list[Operation]:
    out = []
    for provider, service, name, command, risk, categories, verify, rollback, permissions, endpoint, example in CURATED:
        slug = "-".join(command.replace("$", "").split()[1:4]).strip("-") or command.split()[0]
        slug = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in slug).strip("-")
        out.append(Operation(
            id=f"curated:{provider}:{service}:{slug}",
            provider=provider,
            service=service,
            name=name,
            command=command,
            endpoint=endpoint,
            risk=risk,
            categories=list(categories),
            verify=list(verify),
            rollback=list(rollback),
            permissions=list(permissions),
            examples=[example] if example else [],
            effects=effects_for(risk, categories),
            source="curated",
        ))
    return out


# -- Extended catalog ----------------------------------------------------------------
# The rows below are built with a compact helper so every platform the operator
# names has expert records: a read, a plan where one exists, the changes with
# their verification and rollback, the real permission and API call.

_AWS_PREFIX = {"elbv2": "elasticloadbalancing", "efs": "elasticfilesystem", "s3api": "s3",
               "resourcegroupstaggingapi": "tag", "logs": "logs", "ce": "ce"}


def _split(text: str) -> list[str]:
    return [part.strip() for part in text.split(" ;; ") if part.strip()] if text else []


def _aws_action(command: str) -> tuple[str, str]:
    words = command.split()
    service, op = words[1], words[2]
    return _AWS_PREFIX.get(service, service), "".join(x.capitalize() for x in op.split("-"))


def _add(provider: str, service: str, name: str, command: str, risk: str, example: str = "",
         verify: str = "", rollback: str = "", perms: str = "", endpoint: str = "", cats: str = "") -> None:
    permissions = _split(perms)
    if provider == "aws" and command.startswith("aws ") and not permissions:
        prefix, action = _aws_action(command)
        permissions = [f"{prefix}:{action}"]
        endpoint = endpoint or f"{prefix} API action {action}"
    CURATED.append((provider, service, name, command, risk, [c for c in cats.split(",") if c],
                    _split(verify), _split(rollback), permissions, endpoint, example or command))


# AWS: network
_add("aws", "ec2", "List NAT gateways", "aws ec2 describe-nat-gateways", _R, "aws ec2 describe-nat-gateways --filter Name=vpc-id,Values=<vpc>")
_add("aws", "ec2", "Create a NAT gateway", "aws ec2 create-nat-gateway", _X, "aws ec2 create-nat-gateway --subnet-id <public-subnet> --allocation-id <eipalloc>", "aws ec2 wait nat-gateway-available --nat-gateway-ids <nat>", "aws ec2 delete-nat-gateway --nat-gateway-id <nat> ;; aws ec2 release-address --allocation-id <eipalloc>", cats="costly,network")
_add("aws", "ec2", "List VPN connections", "aws ec2 describe-vpn-connections", _R, "aws ec2 describe-vpn-connections --query 'VpnConnections[].{id:VpnConnectionId,state:State,tunnels:VgwTelemetry[].Status}'")
_add("aws", "ec2", "Create a site-to-site VPN connection", "aws ec2 create-vpn-connection", _X, "aws ec2 create-vpn-connection --type ipsec.1 --customer-gateway-id <cgw> --transit-gateway-id <tgw>", "aws ec2 wait vpn-connection-available --vpn-connection-ids <vpn>", "aws ec2 delete-vpn-connection --vpn-connection-id <vpn>", cats="network,costly")
_add("aws", "ec2", "Create a route in a route table", "aws ec2 create-route", _X, "aws ec2 create-route --route-table-id <rtb> --destination-cidr-block 0.0.0.0/0 --nat-gateway-id <nat>", "aws ec2 describe-route-tables --route-table-ids <rtb> --query 'RouteTables[].Routes'", "aws ec2 delete-route --route-table-id <rtb> --destination-cidr-block 0.0.0.0/0", cats="network")
_add("aws", "ec2", "Create a VPC", "aws ec2 create-vpc", _C, "aws ec2 create-vpc --cidr-block 10.20.0.0/16 --tag-specifications 'ResourceType=vpc,Tags=[{Key=Name,Value=<name>}]'", "aws ec2 wait vpc-available --vpc-ids <vpc>", "aws ec2 delete-vpc --vpc-id <vpc>", cats="network")
_add("aws", "ec2", "Create a subnet", "aws ec2 create-subnet", _C, "aws ec2 create-subnet --vpc-id <vpc> --cidr-block 10.20.1.0/24 --availability-zone <az>", "aws ec2 wait subnet-available --subnet-ids <subnet>", "aws ec2 delete-subnet --subnet-id <subnet>", cats="network")
_add("aws", "ec2", "Revoke a firewall rule from a security group", "aws ec2 revoke-security-group-ingress", _X, "aws ec2 revoke-security-group-ingress --group-id <sg> --protocol tcp --port 22 --cidr 0.0.0.0/0", "aws ec2 describe-security-group-rules --filters Name=group-id,Values=<sg>", "aws ec2 authorize-security-group-ingress --group-id <sg> --protocol tcp --port 22 --cidr <cidr>", cats="network")
_add("aws", "elbv2", "Describe load balancer listeners", "aws elbv2 describe-listeners", _R, "aws elbv2 describe-listeners --load-balancer-arn <alb-arn>")
_add("aws", "elbv2", "Deregister a target from a load balancer", "aws elbv2 deregister-targets", _X, "aws elbv2 deregister-targets --target-group-arn <tg> --targets Id=<instance>", "aws elbv2 describe-target-health --target-group-arn <tg>", "aws elbv2 register-targets --target-group-arn <tg> --targets Id=<instance>", cats="availability")
_add("aws", "elbv2", "Delete a load balancer", "aws elbv2 delete-load-balancer", _X, "aws elbv2 delete-load-balancer --load-balancer-arn <arn>", "aws elbv2 describe-load-balancers --load-balancer-arns <arn> (LoadBalancerNotFound)", "not reversible: recreate it with aws elbv2 create-load-balancer and the saved listeners and DNS alias", cats="availability,network")
_add("aws", "route53", "List DNS hosted zones", "aws route53 list-hosted-zones", _R, "aws route53 list-hosted-zones", endpoint="GET /2013-04-01/hostedzone")
# AWS: compute, containers, serverless
_add("aws", "ec2", "List EBS volumes", "aws ec2 describe-volumes", _R, "aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=<instance>")
_add("aws", "ec2", "Create an EBS volume", "aws ec2 create-volume", _C, "aws ec2 create-volume --availability-zone <az> --size 100 --volume-type gp3 --encrypted", "aws ec2 wait volume-available --volume-ids <vol>", "aws ec2 delete-volume --volume-id <vol>", cats="costly")
_add("aws", "ec2", "Modify an EBS volume", "aws ec2 modify-volume", _C, "aws ec2 modify-volume --volume-id <vol> --size 200 --volume-type gp3", "aws ec2 describe-volumes-modifications --volume-ids <vol>", "a volume cannot shrink: restore from a snapshot taken before (aws ec2 create-snapshot first); type and IOPS can be modified back after 6 hours", cats="costly")
_add("aws", "ec2", "Stop an instance", "aws ec2 stop-instances", _X, "aws ec2 stop-instances --instance-ids <instance>", "aws ec2 wait instance-stopped --instance-ids <instance>", "aws ec2 start-instances --instance-ids <instance>", cats="availability")
_add("aws", "efs", "Create an EFS file system", "aws efs create-file-system", _C, "aws efs create-file-system --performance-mode generalPurpose --encrypted --creation-token <token>", "aws efs describe-file-systems --file-system-id <fs> --query 'FileSystems[].LifeCycleState'", "aws efs delete-file-system --file-system-id <fs>", cats="costly")
_add("aws", "ecs", "Describe ECS services", "aws ecs describe-services", _R, "aws ecs describe-services --cluster <cluster> --services <service>")
_add("aws", "ecs", "Update an ECS service", "aws ecs update-service", _C, "aws ecs update-service --cluster <cluster> --service <service> --task-definition <family>:<rev> --force-new-deployment", "aws ecs wait services-stable --cluster <cluster> --services <service>", "aws ecs update-service --cluster <cluster> --service <service> --task-definition <family>:<previous-rev>")
_add("aws", "ecr", "List ECR images", "aws ecr describe-images", _R, "aws ecr describe-images --repository-name <repo> --query 'sort_by(imageDetails,&imagePushedAt)[-5:]'")
_add("aws", "lambda", "Get a Lambda function", "aws lambda get-function", _R, "aws lambda get-function --function-name <fn>")
_add("aws", "lambda", "Update Lambda function code", "aws lambda update-function-code", _C, "aws lambda update-function-code --function-name <fn> --zip-file fileb://function.zip", "aws lambda wait function-updated --function-name <fn>", "aws lambda update-alias --function-name <fn> --name live --function-version <previous-version>")
_add("aws", "lambda", "Publish a Lambda version", "aws lambda publish-version", _C, "aws lambda publish-version --function-name <fn> --description <release>", "aws lambda list-versions-by-function --function-name <fn>", "aws lambda delete-function --function-name <fn> --qualifier <version>")
_add("aws", "lambda", "Point a Lambda alias (rollback)", "aws lambda update-alias", _C, "aws lambda update-alias --function-name <fn> --name live --function-version <version>", "aws lambda get-alias --function-name <fn> --name live", "aws lambda update-alias --function-name <fn> --name live --function-version <previous-version>")
_add("aws", "autoscaling", "Describe Auto Scaling groups", "aws autoscaling describe-auto-scaling-groups", _R, "aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <asg>")
_add("aws", "autoscaling", "Update an Auto Scaling group", "aws autoscaling update-auto-scaling-group", _C, "aws autoscaling update-auto-scaling-group --auto-scaling-group-name <asg> --min-size 2 --max-size 6 --desired-capacity 3", "aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names <asg> --query 'AutoScalingGroups[].[MinSize,MaxSize,DesiredCapacity]'", "aws autoscaling update-auto-scaling-group --auto-scaling-group-name <asg> --min-size <old-min> --max-size <old-max> --desired-capacity <old-desired>", cats="costly")
_add("aws", "ssm", "Run a command with SSM", "aws ssm send-command", _C, "aws ssm send-command --document-name AWS-RunShellScript --instance-ids <instance> --parameters commands='systemctl is-active nginx'", "aws ssm get-command-invocation --command-id <cmd> --instance-id <instance>", "aws ssm cancel-command --command-id <cmd> ;; run the inverse command through send-command", cats="remote-exec")
_add("aws", "ssm", "Open an SSM session", "aws ssm start-session", _C, "aws ssm start-session --target <instance>", "aws ssm describe-sessions --state Active", "aws ssm terminate-session --session-id <session>", cats="remote-exec")
_add("aws", "ssm", "List managed instances", "aws ssm describe-instance-information", _R, "aws ssm describe-instance-information --query 'InstanceInformationList[].[InstanceId,PingStatus]'")
# AWS: CloudFormation
_add("aws", "cloudformation", "Describe CloudFormation stacks", "aws cloudformation describe-stacks", _R, "aws cloudformation describe-stacks --stack-name <stack>")
_add("aws", "cloudformation", "CloudFormation stack events", "aws cloudformation describe-stack-events", _R, "aws cloudformation describe-stack-events --stack-name <stack> --max-items 20")
_add("aws", "cloudformation", "List CloudFormation stack resources", "aws cloudformation list-stack-resources", _R, "aws cloudformation list-stack-resources --stack-name <stack>")
_add("aws", "cloudformation", "Create a CloudFormation stack", "aws cloudformation create-stack", _C, "aws cloudformation create-stack --stack-name <stack> --template-body file://template.yaml --capabilities CAPABILITY_NAMED_IAM", "aws cloudformation wait stack-create-complete --stack-name <stack>", "aws cloudformation delete-stack --stack-name <stack>")
_add("aws", "cloudformation", "Update a stack with a change set", "aws cloudformation create-change-set --change-set-type UPDATE", _P, "aws cloudformation create-change-set --change-set-type UPDATE --stack-name <stack> --change-set-name <cs> --template-body file://template.yaml", "aws cloudformation describe-change-set --stack-name <stack> --change-set-name <cs>", "aws cloudformation delete-change-set --stack-name <stack> --change-set-name <cs>", perms="cloudformation:CreateChangeSet")
_add("aws", "cloudformation", "Deploy a template as a plan (no execute)", "aws cloudformation deploy --no-execute-changeset", _P, "aws cloudformation deploy --no-execute-changeset --stack-name <stack> --template-file template.yaml", perms="cloudformation:CreateChangeSet ;; cloudformation:DescribeChangeSet")
_add("aws", "cloudformation", "Delete a CloudFormation stack", "aws cloudformation delete-stack", _X, "aws cloudformation delete-stack --stack-name <stack>", "aws cloudformation wait stack-delete-complete --stack-name <stack>", "not reversible: recreate with create-stack from the template in Git; data in retained resources (DeletionPolicy Retain/Snapshot) survives", cats="data")
_add("aws", "cloudformation", "Roll back a failed CloudFormation stack", "aws cloudformation rollback-stack", _C, "aws cloudformation rollback-stack --stack-name <stack>", "aws cloudformation wait stack-rollback-complete --stack-name <stack>", "re-apply the change with a new change set once fixed")
# AWS: IAM, KMS
_add("aws", "iam", "List IAM roles", "aws iam list-roles", _R, "aws iam list-roles --query 'Roles[].RoleName'")
_add("aws", "iam", "Get an IAM role", "aws iam get-role", _R, "aws iam get-role --role-name <role>")
_add("aws", "iam", "List IAM role attached policies", "aws iam list-attached-role-policies", _R, "aws iam list-attached-role-policies --role-name <role>")
_add("aws", "iam", "IAM access key last used", "aws iam get-access-key-last-used", _R, "aws iam get-access-key-last-used --access-key-id <AKIA...>")
_add("aws", "iam", "Deactivate an IAM access key", "aws iam update-access-key", _X, "aws iam update-access-key --user-name <user> --access-key-id <key> --status Inactive", "aws iam list-access-keys --user-name <user>", "aws iam update-access-key --user-name <user> --access-key-id <key> --status Active", cats="iam")
_add("aws", "kms", "List KMS keys", "aws kms list-keys", _R, "aws kms list-keys")
_add("aws", "kms", "Enable KMS key rotation", "aws kms enable-key-rotation", _C, "aws kms enable-key-rotation --key-id <key>", "aws kms get-key-rotation-status --key-id <key>", "aws kms disable-key-rotation --key-id <key>", cats="secrets")
# AWS: CloudWatch
_add("aws", "cloudwatch", "Create a CloudWatch alarm", "aws cloudwatch put-metric-alarm", _C, "aws cloudwatch put-metric-alarm --alarm-name <name> --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=<id> --statistic Average --period 300 --threshold 80 --comparison-operator GreaterThanThreshold --evaluation-periods 2 --alarm-actions <sns-arn>", "aws cloudwatch describe-alarms --alarm-names <name>", "aws cloudwatch delete-alarms --alarm-names <name>")
_add("aws", "logs", "CloudWatch Logs Insights query", "aws logs start-query", _R, "aws logs start-query --log-group-name <group> --start-time $(date -d -1hour +%s) --end-time $(date +%s) --query-string 'fields @timestamp, @message | filter @message like /ERROR/ | limit 50'")
_add("aws", "logs", "CloudWatch Logs Insights results", "aws logs get-query-results", _R, "aws logs get-query-results --query-id <query-id>")
# AWS: RDS, S3, EBS
_add("aws", "rds", "List RDS snapshots", "aws rds describe-db-snapshots", _R, "aws rds describe-db-snapshots --db-instance-identifier <db>")
_add("aws", "rds", "Modify an RDS instance", "aws rds modify-db-instance", _X, "aws rds modify-db-instance --db-instance-identifier <db> --db-instance-class db.r6g.large --apply-immediately", "aws rds wait db-instance-available --db-instance-identifier <db> ;; aws rds describe-db-instances --db-instance-identifier <db> --query 'DBInstances[].DBInstanceClass'", "aws rds modify-db-instance --db-instance-identifier <db> --db-instance-class <previous-class> --apply-immediately", cats="availability,costly")
_add("aws", "rds", "Reboot an RDS instance", "aws rds reboot-db-instance", _X, "aws rds reboot-db-instance --db-instance-identifier <db>", "aws rds wait db-instance-available --db-instance-identifier <db>", "not reversible (a restart); the instance returns to available", cats="availability")
_add("aws", "rds", "Fail over an RDS cluster", "aws rds failover-db-cluster", _X, "aws rds failover-db-cluster --db-cluster-identifier <cluster> --target-db-instance-identifier <replica>", "aws rds describe-db-clusters --db-cluster-identifier <cluster> --query 'DBClusters[].DBClusterMembers'", "aws rds failover-db-cluster --db-cluster-identifier <cluster> --target-db-instance-identifier <old-writer>", cats="availability")
_add("aws", "s3api", "Get S3 bucket versioning", "aws s3api get-bucket-versioning", _R, "aws s3api get-bucket-versioning --bucket <bucket>")
_add("aws", "s3api", "Get S3 lifecycle rules", "aws s3api get-bucket-lifecycle-configuration", _R, "aws s3api get-bucket-lifecycle-configuration --bucket <bucket>", perms="s3:GetLifecycleConfiguration")
_add("aws", "s3api", "Put S3 lifecycle rules", "aws s3api put-bucket-lifecycle-configuration", _C, "aws s3api put-bucket-lifecycle-configuration --bucket <bucket> --lifecycle-configuration file://lifecycle.json", "aws s3api get-bucket-lifecycle-configuration --bucket <bucket>", "aws s3api put-bucket-lifecycle-configuration --bucket <bucket> --lifecycle-configuration file://previous-lifecycle.json ;; or aws s3api delete-bucket-lifecycle --bucket <bucket>", perms="s3:PutLifecycleConfiguration", cats="data")
_add("aws", "s3api", "Get S3 bucket encryption", "aws s3api get-bucket-encryption", _R, "aws s3api get-bucket-encryption --bucket <bucket>", perms="s3:GetEncryptionConfiguration")
_add("aws", "s3api", "Put S3 bucket encryption", "aws s3api put-bucket-encryption", _C, "aws s3api put-bucket-encryption --bucket <bucket> --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"<key>\"}}]}'", "aws s3api get-bucket-encryption --bucket <bucket>", "aws s3api put-bucket-encryption --bucket <bucket> --server-side-encryption-configuration file://previous-encryption.json", perms="s3:PutEncryptionConfiguration ;; kms:GenerateDataKey")
_add("aws", "s3api", "Get S3 bucket replication", "aws s3api get-bucket-replication", _R, "aws s3api get-bucket-replication --bucket <bucket>", perms="s3:GetReplicationConfiguration")
_add("aws", "s3", "List S3 objects", "aws s3 ls", _R, "aws s3 ls s3://<bucket>/<prefix>/ --recursive --human-readable --summarize", perms="s3:ListBucket", endpoint="GET /?list-type=2")
_add("aws", "s3", "Preview an S3 sync (dry run)", "aws s3 sync --dryrun", _P, "aws s3 sync ./dist s3://<bucket>/<prefix> --delete --dryrun", perms="s3:ListBucket")
_add("aws", "s3", "Remove S3 objects", "aws s3 rm", _X, "aws s3 rm s3://<bucket>/<prefix>/ --recursive", "aws s3 ls s3://<bucket>/<prefix>/", "with versioning on: delete the delete markers (aws s3api delete-object --version-id <marker>); otherwise not reversible", perms="s3:DeleteObject", cats="data")
_add("aws", "ebs", "Snapshot an EBS volume before change", "aws ec2 create-snapshot --volume-id", _C, "aws ec2 create-snapshot --volume-id <vol> --description pre-change", "aws ec2 wait snapshot-completed --snapshot-ids <snap>", "aws ec2 delete-snapshot --snapshot-id <snap>", perms="ec2:CreateSnapshot", cats="backup")
_add("aws", "ebs", "List EBS volumes", "aws ec2 describe-volumes", _R, "aws ec2 describe-volumes --query 'Volumes[].[VolumeId,Size,VolumeType,State]'", perms="ec2:DescribeVolumes")
_add("aws", "ebs", "Grow an EBS volume", "aws ec2 modify-volume", _C, "aws ec2 modify-volume --volume-id <vol> --size 200", "aws ec2 describe-volumes-modifications --volume-ids <vol> ;; lsblk on the host then growpart/resize2fs", "cannot shrink: restore from the pre-change snapshot", perms="ec2:ModifyVolume")
_add("aws", "ebs", "Delete an EBS volume", "aws ec2 delete-volume", _X, "aws ec2 delete-volume --volume-id <vol>", "aws ec2 describe-volumes --volume-ids <vol> (InvalidVolume.NotFound)", "not reversible: restore from snapshot with aws ec2 create-volume --snapshot-id <snap>", perms="ec2:DeleteVolume", cats="data")
_add("aws", "efs", "List EFS mount targets", "aws efs describe-mount-targets", _R, "aws efs describe-mount-targets --file-system-id <fs>")
_add("aws", "efs", "Create an EFS access point", "aws efs create-access-point", _C, "aws efs create-access-point --file-system-id <fs> --posix-user Uid=1000,Gid=1000 --root-directory 'Path=/app,CreationInfo={OwnerUid=1000,OwnerGid=1000,Permissions=750}'", "aws efs describe-access-points --file-system-id <fs>", "aws efs delete-access-point --access-point-id <ap>")
# AWS: EKS
_add("aws", "eks", "Describe an EKS node group", "aws eks describe-nodegroup", _R, "aws eks describe-nodegroup --cluster-name <cluster> --nodegroup-name <ng>")
_add("aws", "eks", "Upgrade an EKS node group version", "aws eks update-nodegroup-version", _X, "aws eks update-nodegroup-version --cluster-name <cluster> --nodegroup-name <ng> --kubernetes-version <version>", "aws eks wait nodegroup-active --cluster-name <cluster> --nodegroup-name <ng> ;; kubectl get nodes -o wide", "launch template rollback: aws eks update-nodegroup-version --launch-template version=<previous>; the Kubernetes minor version cannot be downgraded", cats="availability")
_add("aws", "eks", "List EKS add-ons", "aws eks list-addons", _R, "aws eks list-addons --cluster-name <cluster>")
_add("aws", "eks", "Upgrade an EKS control plane", "aws eks update-cluster-version", _X, "aws eks update-cluster-version --name <cluster> --kubernetes-version <version>", "aws eks describe-update --name <cluster> --update-id <id>", "not reversible: EKS cannot downgrade; restore workloads to a new cluster from backups (velero)", cats="availability")
_add("aws", "eks", "Create an EKS node group with eksctl", "eksctl create nodegroup", _X, "eksctl create nodegroup --cluster <cluster> --name <ng> --node-type m6i.large --nodes 3", "eksctl get nodegroup --cluster <cluster> ;; kubectl get nodes", "eksctl delete nodegroup --cluster <cluster> --name <ng> --drain", perms="eks:CreateNodegroup ;; cloudformation:CreateStack ;; iam:PassRole", cats="costly")
_add("aws", "eks", "Upgrade an EKS cluster with eksctl", "eksctl upgrade cluster", _X, "eksctl upgrade cluster --name <cluster> --version <version> --approve", "eksctl get cluster --name <cluster> ;; kubectl version", "not reversible: EKS cannot downgrade; restore workloads to a new cluster from backups", perms="eks:UpdateClusterVersion", cats="availability")
_add("aws", "eks", "Plan an EKS upgrade with eksctl", "eksctl upgrade cluster --name", _P, "eksctl upgrade cluster --name <cluster> --version <version>", perms="eks:DescribeCluster")
_add("aws", "eks", "Write kubeconfig for an EKS cluster", "aws eks update-kubeconfig --name", _C, "aws eks update-kubeconfig --name <cluster> --region <region>", "kubectl get nodes", "kubectl config delete-context <context>", perms="eks:DescribeCluster", cats="local")

# Google Cloud
_add("gcp", "compute", "List VPC networks", "gcloud compute networks list", _R, "gcloud compute networks list --project <project>", perms="compute.networks.list", endpoint="GET /compute/v1/projects/{project}/global/networks")
_add("gcp", "compute", "List VPC subnets", "gcloud compute networks subnets list", _R, "gcloud compute networks subnets list --network <vpc> --project <project>", perms="compute.subnetworks.list", endpoint="GET /compute/v1/projects/{project}/aggregated/subnetworks")
_add("gcp", "vpc", "Create a VPC subnet", "gcloud compute networks subnets create", _C, "gcloud compute networks subnets create <subnet> --network <vpc> --region <region> --range 10.30.0.0/24", "gcloud compute networks subnets describe <subnet> --region <region>", "gcloud compute networks subnets delete <subnet> --region <region>", perms="compute.subnetworks.create", endpoint="POST /compute/v1/projects/{project}/regions/{region}/subnetworks", cats="network")
_add("gcp", "compute", "List routes", "gcloud compute routes list", _R, "gcloud compute routes list --filter network=<vpc>", perms="compute.routes.list", endpoint="GET /compute/v1/projects/{project}/global/routes")
_add("gcp", "compute", "Create a route", "gcloud compute routes create", _X, "gcloud compute routes create <route> --network <vpc> --destination-range 10.50.0.0/16 --next-hop-vpn-tunnel <tunnel> --next-hop-vpn-tunnel-region <region>", "gcloud compute routes describe <route>", "gcloud compute routes delete <route>", perms="compute.routes.create", endpoint="POST /compute/v1/projects/{project}/global/routes", cats="network")
_add("gcp", "compute", "List Cloud NAT gateways", "gcloud compute routers nats list", _R, "gcloud compute routers nats list --router <router> --region <region>", perms="compute.routers.get")
_add("gcp", "nat", "Create a Cloud NAT gateway", "gcloud compute routers nats create", _X, "gcloud compute routers nats create <nat> --router <router> --region <region> --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges", "gcloud compute routers get-status <router> --region <region>", "gcloud compute routers nats delete <nat> --router <router> --region <region>", perms="compute.routers.update", endpoint="PATCH /compute/v1/projects/{project}/regions/{region}/routers/{router}", cats="network,costly")
_add("gcp", "compute", "List VPN tunnels", "gcloud compute vpn-tunnels list", _R, "gcloud compute vpn-tunnels list --format 'table(name,region,status,detailedStatus)'", perms="compute.vpnTunnels.list")
_add("gcp", "compute", "Delete a VPN tunnel", "gcloud compute vpn-tunnels delete", _X, "gcloud compute vpn-tunnels delete <tunnel> --region <region>", "gcloud compute vpn-tunnels list --filter name=<tunnel>", "recreate with gcloud compute vpn-tunnels create <tunnel> and the saved shared secret and peer", perms="compute.vpnTunnels.delete", cats="network")
_add("gcp", "compute", "List firewall rules", "gcloud compute firewall-rules list", _R, "gcloud compute firewall-rules list --filter network=<vpc>", perms="compute.firewalls.list")
_add("gcp", "compute", "List load balancer forwarding rules", "gcloud compute forwarding-rules list", _R, "gcloud compute forwarding-rules list", perms="compute.forwardingRules.list")
_add("gcp", "compute", "Load balancer backend service health", "gcloud compute backend-services get-health", _R, "gcloud compute backend-services get-health <backend> --global", perms="compute.backendServices.get")
_add("gcp", "compute", "Remove a backend from a load balancer", "gcloud compute backend-services remove-backend", _X, "gcloud compute backend-services remove-backend <backend> --instance-group <ig> --instance-group-zone <zone> --global", "gcloud compute backend-services get-health <backend> --global", "gcloud compute backend-services add-backend <backend> --instance-group <ig> --instance-group-zone <zone> --global", perms="compute.backendServices.update", cats="availability")
_add("gcp", "compute", "Start an instance", "gcloud compute instances start", _C, "gcloud compute instances start <vm> --zone <zone>", "gcloud compute instances describe <vm> --zone <zone> --format 'value(status)'", "gcloud compute instances stop <vm> --zone <zone>", perms="compute.instances.start")
_add("gcp", "compute", "Stop an instance", "gcloud compute instances stop", _X, "gcloud compute instances stop <vm> --zone <zone>", "gcloud compute instances describe <vm> --zone <zone> --format 'value(status)'", "gcloud compute instances start <vm> --zone <zone>", perms="compute.instances.stop", cats="availability")
_add("gcp", "dns", "List Cloud DNS records", "gcloud dns record-sets list", _R, "gcloud dns record-sets list --zone <zone>", perms="dns.resourceRecordSets.list")
_add("gcp", "container", "Describe a GKE cluster", "gcloud container clusters describe", _R, "gcloud container clusters describe <cluster> --region <region>", perms="container.clusters.get")
_add("gcp", "container", "Upgrade a GKE cluster", "gcloud container clusters upgrade", _X, "gcloud container clusters upgrade <cluster> --region <region> --master --cluster-version <version>", "gcloud container operations list --filter targetLink~<cluster> ;; kubectl get nodes", "not reversible for the control plane: GKE cannot downgrade a minor; node pools can be recreated at the old patch", perms="container.clusters.update", cats="availability")
_add("gcp", "container", "List GKE node pools", "gcloud container node-pools list", _R, "gcloud container node-pools list --cluster <cluster> --region <region>", perms="container.clusters.get")
_add("gcp", "node-pools", "Upgrade a GKE node pool", "gcloud container clusters upgrade --node-pool", _X, "gcloud container clusters upgrade <cluster> --node-pool <pool> --region <region>", "gcloud container node-pools describe <pool> --cluster <cluster> --region <region> --format 'value(version)'", "gcloud container node-pools rollback <pool> --cluster <cluster> --region <region>", perms="container.clusters.update", cats="availability")
_add("gcp", "sql", "Describe a Cloud SQL instance", "gcloud sql instances describe", _R, "gcloud sql instances describe <db>", perms="cloudsql.instances.get", endpoint="GET /v1/projects/{project}/instances/{instance}")
_add("gcp", "sql", "Patch a Cloud SQL instance", "gcloud sql instances patch", _X, "gcloud sql instances patch <db> --tier db-custom-4-15360", "gcloud sql instances describe <db> --format 'value(settings.tier,state)'", "gcloud sql instances patch <db> --tier <previous-tier>", perms="cloudsql.instances.update", endpoint="PATCH /v1/projects/{project}/instances/{instance}", cats="availability")
_add("gcp", "sql", "Restart a Cloud SQL instance", "gcloud sql instances restart", _X, "gcloud sql instances restart <db>", "gcloud sql operations list --instance <db> --limit 1", "not reversible (a restart); the instance returns to RUNNABLE", perms="cloudsql.instances.restart", cats="availability")
_add("gcp", "sql", "Fail over a Cloud SQL instance", "gcloud sql instances failover", _X, "gcloud sql instances failover <db>", "gcloud sql instances describe <db> --format 'value(gceZone,state)'", "fail over again to return to the original zone: gcloud sql instances failover <db>", perms="cloudsql.instances.failover", cats="availability")
_add("gcp", "sql", "List Cloud SQL backups", "gcloud sql backups list", _R, "gcloud sql backups list --instance <db>", perms="cloudsql.backupRuns.list")
_add("gcp", "sql", "Export Cloud SQL to GCS", "gcloud sql export sql", _C, "gcloud sql export sql <db> gs://<bucket>/<db>-$(date +%F).sql.gz --database <name>", "gcloud storage ls -l gs://<bucket>/<db>-*.sql.gz", "gcloud storage rm gs://<bucket>/<file>", perms="cloudsql.instances.export ;; storage.objects.create", cats="backup")
_add("gcp", "run", "List Cloud Run services", "gcloud run services list", _R, "gcloud run services list --region <region>", perms="run.services.list")
_add("gcp", "run", "Shift Cloud Run traffic (rollback)", "gcloud run services update-traffic", _C, "gcloud run services update-traffic <service> --region <region> --to-revisions <previous-revision>=100", "gcloud run services describe <service> --region <region> --format 'value(status.traffic)'", "gcloud run services update-traffic <service> --region <region> --to-latest", perms="run.services.update")
_add("gcp", "logging", "Read Cloud Logging", "gcloud logging read", _R, "gcloud logging read 'severity>=ERROR AND resource.type=k8s_container' --freshness 1h --limit 50", perms="logging.logEntries.list")
_add("gcp", "monitoring", "List alerting policies", "gcloud monitoring policies list", _R, "gcloud alpha monitoring policies list --format 'table(displayName,enabled)'", perms="monitoring.alertPolicies.list")
_add("gcp", "iam", "List IAM service accounts", "gcloud iam service-accounts list", _R, "gcloud iam service-accounts list --project <project>", perms="iam.serviceAccounts.list")
_add("gcp", "iam", "List IAM service account keys", "gcloud iam service-accounts keys list", _R, "gcloud iam service-accounts keys list --iam-account <sa>@<project>.iam.gserviceaccount.com --managed-by user", perms="iam.serviceAccountKeys.list")
_add("gcp", "iam-keys", "Disable an IAM service account key", "gcloud iam service-accounts keys disable", _X, "gcloud iam service-accounts keys disable <key-id> --iam-account <sa>@<project>.iam.gserviceaccount.com", "gcloud iam service-accounts keys list --iam-account <sa>@<project>.iam.gserviceaccount.com", "gcloud iam service-accounts keys enable <key-id> --iam-account <sa>@<project>.iam.gserviceaccount.com", perms="iam.serviceAccountKeys.disable", cats="iam")
_add("gcp", "kms", "List KMS keys", "gcloud kms keys list", _R, "gcloud kms keys list --keyring <ring> --location <location>", perms="cloudkms.cryptoKeys.list")
_add("gcp", "kms", "Set KMS key rotation", "gcloud kms keys update --rotation-period", _C, "gcloud kms keys update <key> --keyring <ring> --location <location> --rotation-period 90d --next-rotation-time <rfc3339>", "gcloud kms keys describe <key> --keyring <ring> --location <location> --format 'value(rotationPeriod)'", "gcloud kms keys update <key> --keyring <ring> --location <location> --remove-rotation-schedule", perms="cloudkms.cryptoKeys.update", cats="secrets")

# Azure
_add("azure", "network", "List VNets", "az network vnet list", _R, "az network vnet list -o table", perms="Microsoft.Network/virtualNetworks/read")
_add("azure", "network", "List VNet subnets", "az network vnet subnet list", _R, "az network vnet subnet list -g <rg> --vnet-name <vnet> -o table", perms="Microsoft.Network/virtualNetworks/subnets/read")
_add("azure", "vnet", "Create a VNet subnet", "az network vnet subnet create", _C, "az network vnet subnet create -g <rg> --vnet-name <vnet> -n <subnet> --address-prefixes 10.40.1.0/24", "az network vnet subnet show -g <rg> --vnet-name <vnet> -n <subnet>", "az network vnet subnet delete -g <rg> --vnet-name <vnet> -n <subnet>", perms="Microsoft.Network/virtualNetworks/subnets/write", cats="network")
_add("azure", "network", "List route tables", "az network route-table list", _R, "az network route-table list -o table", perms="Microsoft.Network/routeTables/read")
_add("azure", "network", "Create a route in a route table", "az network route-table route create", _X, "az network route-table route create -g <rg> --route-table-name <rt> -n <route> --address-prefix 0.0.0.0/0 --next-hop-type VirtualAppliance --next-hop-ip-address <nva-ip>", "az network route-table route show -g <rg> --route-table-name <rt> -n <route>", "az network route-table route delete -g <rg> --route-table-name <rt> -n <route>", perms="Microsoft.Network/routeTables/routes/write", cats="network")
_add("azure", "network", "List NAT gateways", "az network nat gateway list", _R, "az network nat gateway list -o table", perms="Microsoft.Network/natGateways/read")
_add("azure", "nat", "Create a NAT gateway", "az network nat gateway create", _X, "az network nat gateway create -g <rg> -n <nat> --public-ip-addresses <pip> --idle-timeout 10", "az network nat gateway show -g <rg> -n <nat> --query provisioningState", "az network nat gateway delete -g <rg> -n <nat>", perms="Microsoft.Network/natGateways/write", cats="network,costly")
_add("azure", "network", "List VPN connections", "az network vpn-connection list", _R, "az network vpn-connection list -g <rg> -o table", perms="Microsoft.Network/connections/read")
_add("azure", "network", "List load balancers", "az network lb list", _R, "az network lb list -o table", perms="Microsoft.Network/loadBalancers/read")
_add("azure", "network", "List Application Gateway load balancers", "az network application-gateway list", _R, "az network application-gateway list -o table", perms="Microsoft.Network/applicationGateways/read")
_add("azure", "firewall", "List NSG firewall rules", "az network nsg rule list", _R, "az network nsg rule list -g <rg> --nsg-name <nsg> -o table", perms="Microsoft.Network/networkSecurityGroups/securityRules/read")
_add("azure", "dns", "List Azure DNS records", "az network dns record-set list", _R, "az network dns record-set list -g <rg> -z <zone> -o table", perms="Microsoft.Network/dnsZones/recordsets/read")
_add("azure", "dns-records", "Add an Azure DNS A record", "az network dns record-set a add-record", _X, "az network dns record-set a add-record -g <rg> -z <zone> -n <name> -a <ip>", "dig +short <name>.<zone> @<azure-ns>", "az network dns record-set a remove-record -g <rg> -z <zone> -n <name> -a <ip>", perms="Microsoft.Network/dnsZones/A/write", cats="network")
_add("azure", "compute", "List VMs", "az vm list", _R, "az vm list -d -o table", perms="Microsoft.Compute/virtualMachines/read")
_add("azure", "compute", "Start a VM", "az vm start", _C, "az vm start -g <rg> -n <vm>", "az vm get-instance-view -g <rg> -n <vm> --query instanceView.statuses[1].code", "az vm deallocate -g <rg> -n <vm>", perms="Microsoft.Compute/virtualMachines/start/action")
_add("azure", "compute", "Stop a VM", "az vm stop", _X, "az vm stop -g <rg> -n <vm>", "az vm get-instance-view -g <rg> -n <vm> --query instanceView.statuses[1].code", "az vm start -g <rg> -n <vm>", perms="Microsoft.Compute/virtualMachines/powerOff/action", cats="availability")
_add("azure", "compute", "Restart a VM", "az vm restart", _X, "az vm restart -g <rg> -n <vm>", "az vm get-instance-view -g <rg> -n <vm> --query instanceView.statuses[1].code", "not reversible (a restart); the VM returns to running", perms="Microsoft.Compute/virtualMachines/restart/action", cats="availability")
_add("azure", "aks", "List AKS clusters", "az aks list", _R, "az aks list -o table", perms="Microsoft.ContainerService/managedClusters/read")
_add("azure", "aks", "Show an AKS cluster", "az aks show", _R, "az aks show -g <rg> -n <cluster>", perms="Microsoft.ContainerService/managedClusters/read")
_add("azure", "aks", "Available AKS upgrades", "az aks get-upgrades", _P, "az aks get-upgrades -g <rg> -n <cluster> -o table", perms="Microsoft.ContainerService/managedClusters/read")
_add("azure", "aks", "Upgrade an AKS cluster", "az aks upgrade", _X, "az aks upgrade -g <rg> -n <cluster> --kubernetes-version <version> --yes", "az aks show -g <rg> -n <cluster> --query '[kubernetesVersion,provisioningState]' ;; kubectl get nodes", "not reversible: AKS cannot downgrade; restore workloads to a new cluster from backups", perms="Microsoft.ContainerService/managedClusters/write", cats="availability")
_add("azure", "aks", "List AKS node pools", "az aks nodepool list", _R, "az aks nodepool list -g <rg> --cluster-name <cluster> -o table", perms="Microsoft.ContainerService/managedClusters/agentPools/read")
_add("azure", "aks", "Scale an AKS node pool", "az aks nodepool scale", _C, "az aks nodepool scale -g <rg> --cluster-name <cluster> -n <pool> --node-count 4", "az aks nodepool show -g <rg> --cluster-name <cluster> -n <pool> --query count ;; kubectl get nodes", "az aks nodepool scale -g <rg> --cluster-name <cluster> -n <pool> --node-count <previous>", perms="Microsoft.ContainerService/managedClusters/agentPools/write", cats="costly")
_add("azure", "aks", "Get AKS credentials", "az aks get-credentials -g", _C, "az aks get-credentials -g <rg> -n <cluster>", "kubectl get nodes", "kubectl config delete-context <cluster>", perms="Microsoft.ContainerService/managedClusters/listClusterUserCredential/action", cats="local")
_add("azure", "postgres", "List PostgreSQL flexible servers", "az postgres flexible-server list", _R, "az postgres flexible-server list -o table", perms="Microsoft.DBforPostgreSQL/flexibleServers/read")
_add("azure", "postgres", "Restart a PostgreSQL flexible server", "az postgres flexible-server restart", _X, "az postgres flexible-server restart -g <rg> -n <server>", "az postgres flexible-server show -g <rg> -n <server> --query state", "not reversible (a restart); the server returns to Ready", perms="Microsoft.DBforPostgreSQL/flexibleServers/restart/action", cats="availability")
_add("azure", "sql", "List Azure SQL databases", "az sql db list", _R, "az sql db list -g <rg> -s <server> -o table", perms="Microsoft.Sql/servers/databases/read")
_add("azure", "storage", "List storage accounts", "az storage account list", _R, "az storage account list -o table", perms="Microsoft.Storage/storageAccounts/read")
_add("azure", "storage", "List blobs", "az storage blob list", _R, "az storage blob list --account-name <account> -c <container> --auth-mode login -o table", perms="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read")
_add("azure", "storage", "Delete a blob", "az storage blob delete", _X, "az storage blob delete --account-name <account> -c <container> -n <blob> --auth-mode login", "az storage blob exists --account-name <account> -c <container> -n <blob> --auth-mode login", "with soft delete on: az storage blob undelete --account-name <account> -c <container> -n <blob>; otherwise not reversible", perms="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete", cats="data")
_add("azure", "keyvault", "List key vaults", "az keyvault list", _R, "az keyvault list -o table", perms="Microsoft.KeyVault/vaults/read")
_add("azure", "keyvault", "Read a Key Vault secret", "az keyvault secret show", _R, "az keyvault secret show --vault-name <vault> -n <secret> --query id", perms="Microsoft.KeyVault/vaults/secrets/getSecret/action", cats="secrets")
_add("azure", "keyvault", "List Key Vault KMS keys", "az keyvault key list", _R, "az keyvault key list --vault-name <vault> -o table", perms="Microsoft.KeyVault/vaults/keys/read")
_add("azure", "keyvault", "Set a Key Vault KMS key rotation policy", "az keyvault key rotation-policy update", _C, "az keyvault key rotation-policy update --vault-name <vault> -n <key> --value @rotation-policy.json", "az keyvault key rotation-policy show --vault-name <vault> -n <key>", "az keyvault key rotation-policy update --vault-name <vault> -n <key> --value @previous-policy.json", perms="Microsoft.KeyVault/vaults/keys/rotationpolicy/write", cats="secrets")
_add("azure", "monitor", "Azure Monitor metrics", "az monitor metrics list", _R, "az monitor metrics list --resource <resource-id> --metric 'Percentage CPU' --interval PT5M", perms="Microsoft.Insights/metrics/read")
_add("azure", "monitor", "Activity log", "az monitor activity-log list", _R, "az monitor activity-log list --offset 24h --status Failed -o table", perms="Microsoft.Insights/eventtypes/values/read")
_add("azure", "advisor", "Advisor recommendations", "az advisor recommendation list", _R, "az advisor recommendation list -o table", perms="Microsoft.Advisor/recommendations/read")
_add("azure", "costmanagement", "Cost Management query", "az costmanagement query", _R, "az costmanagement query --type ActualCost --timeframe MonthToDate --scope /subscriptions/<sub> --dataset-grouping name=ServiceName type=Dimension", perms="Microsoft.CostManagement/query/action")
_add("azure", "policy", "List policy assignments", "az policy assignment list", _R, "az policy assignment list -o table", perms="Microsoft.Authorization/policyAssignments/read")
_add("azure", "iam", "List IAM service principals", "az ad sp list", _R, "az ad sp list --all --query '[].{name:displayName,appId:appId}' -o table", perms="Microsoft Graph Application.Read.All")
_add("azure", "iam", "Remove an IAM role assignment", "az role assignment delete", _X, "az role assignment delete --assignee <principal> --role <role> --scope <scope>", "az role assignment list --assignee <principal> --scope <scope>", "az role assignment create --assignee <principal> --role <role> --scope <scope>", perms="Microsoft.Authorization/roleAssignments/delete", cats="iam")

# OpenStack
_add("openstack", "compute", "Show a server", "openstack server show", _R, "openstack server show <server> -f json", perms="os_compute_api:servers:show")
_add("openstack", "compute", "Reboot a server", "openstack server reboot", _X, "openstack server reboot --soft <server>", "openstack server show <server> -c status -f value", "not reversible (a reboot); the server returns to ACTIVE", perms="os_compute_api:servers:reboot", cats="availability")
_add("openstack", "compute", "Stop a server", "openstack server stop", _X, "openstack server stop <server>", "openstack server show <server> -c status -f value (SHUTOFF)", "openstack server start <server>", perms="os_compute_api:servers:stop", cats="availability")
_add("openstack", "compute", "Delete a server", "openstack server delete", _X, "openstack server delete --wait <server>", "openstack server show <server> (No server found)", "not reversible: rebuild from an image snapshot (openstack server image create beforehand)", perms="os_compute_api:servers:delete", cats="data")
_add("openstack", "network", "List subnets", "openstack subnet list", _R, "openstack subnet list --network <net>", perms="get_subnet")
_add("openstack", "network", "List routers (routing)", "openstack router list", _R, "openstack router list --long", perms="get_router")
_add("openstack", "network", "List ports", "openstack port list", _R, "openstack port list --server <server>", perms="get_port")
_add("openstack", "network", "Create a security group firewall rule", "openstack security group rule create", _X, "openstack security group rule create --ingress --protocol tcp --dst-port 443 --remote-ip <cidr> <sg>", "openstack security group rule list <sg>", "openstack security group rule delete <rule-id>", perms="create_security_group_rule", cats="network")
_add("openstack", "volume", "List volumes", "openstack volume list", _R, "openstack volume list --long", perms="volume:get_all")
_add("openstack", "volume", "Create a volume", "openstack volume create", _C, "openstack volume create --size 50 --type <type> <name>", "openstack volume show <name> -c status -f value (available)", "openstack volume delete <name>", perms="volume:create", cats="costly")
_add("openstack", "volume", "Snapshot a volume", "openstack volume snapshot create", _C, "openstack volume snapshot create --volume <volume> --force <snap>", "openstack volume snapshot show <snap> -c status -f value", "openstack volume snapshot delete <snap>", perms="volume:create_snapshot", cats="backup")
_add("openstack", "image", "List images", "openstack image list", _R, "openstack image list --status active", perms="get_images")
_add("openstack", "orchestration", "List Heat stacks", "openstack stack list", _R, "openstack stack list", perms="stacks:index")
_add("openstack", "orchestration", "Create a Heat stack", "openstack stack create", _C, "openstack stack create -t stack.yaml --wait <stack>", "openstack stack show <stack> -c stack_status -f value", "openstack stack delete --yes --wait <stack>", perms="stacks:create")
_add("openstack", "orchestration", "Update a Heat stack", "openstack stack update", _C, "openstack stack update -t stack.yaml --wait <stack>", "openstack stack show <stack> -c stack_status -f value", "openstack stack update -t previous-stack.yaml --wait <stack> ;; or openstack stack cancel <stack>", perms="stacks:update")
_add("openstack", "orchestration", "Preview a Heat stack update", "openstack stack update --dry-run", _P, "openstack stack update --dry-run -t stack.yaml <stack>", perms="stacks:preview_update")
_add("openstack", "orchestration", "Delete a Heat stack", "openstack stack delete", _X, "openstack stack delete --yes --wait <stack>", "openstack stack show <stack> (Stack not found)", "not reversible: recreate with openstack stack create from the template in Git", perms="stacks:delete", cats="data")
_add("openstack", "identity", "Show project quota", "openstack quota show", _R, "openstack quota show <project>", perms="os_compute_api:os-quota-sets:show")

# VMware vSphere (govc)
_add("vsphere", "vm", "Power on a VM", "govc vm.power -on", _C, "govc vm.power -on <vm>", "govc vm.info <vm> (Power state: poweredOn)", "govc vm.power -s <vm>", perms="VirtualMachine.Interact.PowerOn")
_add("vsphere", "vm", "Power off a VM", "govc vm.power -off", _X, "govc vm.power -off <vm>", "govc vm.info <vm> (Power state: poweredOff)", "govc vm.power -on <vm>", perms="VirtualMachine.Interact.PowerOff", cats="availability")
_add("vsphere", "vm", "Clone a VM", "govc vm.clone", _C, "govc vm.clone -vm <template> -on=false -folder <folder> -ds <datastore> <new-vm>", "govc vm.info <new-vm>", "govc vm.destroy <new-vm>", perms="VirtualMachine.Provisioning.Clone", cats="costly")
_add("vsphere", "vm", "Remove a VM snapshot", "govc snapshot.remove", _X, "govc snapshot.remove -vm <vm> <snapshot>", "govc snapshot.tree -vm <vm>", "not reversible: the snapshot delta is merged into the base disk", perms="VirtualMachine.State.RemoveSnapshot", cats="data")
_add("vsphere", "host", "ESXi host info", "govc host.info", _R, "govc host.info -host <esxi-host>", perms="System.Read")
_add("vsphere", "datastore", "Datastore info", "govc datastore.info", _R, "govc datastore.info <datastore>", perms="System.Read")
_add("vsphere", "cluster", "Cluster usage", "govc cluster.usage", _R, "govc cluster.usage <cluster>", perms="System.Read")

# Proxmox
_add("proxmox", "qemu", "VM status", "qm status", _R, "qm status <vmid> --verbose", perms="VM.Audit")
_add("proxmox", "qemu", "Start a VM", "qm start", _C, "qm start <vmid>", "qm status <vmid> (running)", "qm shutdown <vmid>", perms="VM.PowerMgmt")
_add("proxmox", "qemu", "Shut down a VM", "qm shutdown", _X, "qm shutdown <vmid> --timeout 120", "qm status <vmid> (stopped)", "qm start <vmid>", perms="VM.PowerMgmt", cats="availability")
_add("proxmox", "qemu", "Hard stop a VM", "qm stop", _X, "qm stop <vmid>", "qm status <vmid> (stopped)", "qm start <vmid>", perms="VM.PowerMgmt", cats="availability")
_add("proxmox", "qemu", "Reboot a VM", "qm reboot", _X, "qm reboot <vmid>", "qm status <vmid> (running)", "not reversible (a reboot)", perms="VM.PowerMgmt", cats="availability")
_add("proxmox", "qemu", "Roll back a VM to a snapshot", "qm rollback", _X, "qm rollback <vmid> <snapshot>", "qm listsnapshot <vmid> ;; qm status <vmid>", "not reversible: take qm snapshot <vmid> pre-rollback first and roll back to it", perms="VM.Snapshot.Rollback", cats="data")
_add("proxmox", "qemu", "Clone a VM", "qm clone", _C, "qm clone <vmid> <newid> --name <name> --full", "qm config <newid>", "qm destroy <newid> --purge", perms="VM.Clone ;; Datastore.AllocateSpace")
_add("proxmox", "qemu", "Migrate a VM", "qm migrate", _C, "qm migrate <vmid> <target-node> --online", "pvesh get /cluster/resources --type vm --output-format json", "qm migrate <vmid> <source-node> --online", perms="VM.Migrate")
_add("proxmox", "lxc", "Start a container", "pct start", _C, "pct start <ctid>", "pct status <ctid> (running)", "pct shutdown <ctid>", perms="VM.PowerMgmt")
_add("proxmox", "lxc", "Stop a container", "pct stop", _X, "pct stop <ctid>", "pct status <ctid> (stopped)", "pct start <ctid>", perms="VM.PowerMgmt", cats="availability")
_add("proxmox", "lxc", "Snapshot a container", "pct snapshot", _C, "pct snapshot <ctid> pre-change", "pct listsnapshot <ctid>", "pct delsnapshot <ctid> pre-change", perms="VM.Snapshot", cats="backup")
_add("proxmox", "api", "List nodes", "pvesh get /nodes", _R, "pvesh get /nodes --output-format json", perms="Sys.Audit")
_add("proxmox", "api", "List storage", "pvesh get /storage", _R, "pvesh get /storage --output-format json", perms="Datastore.Audit")
_add("proxmox", "backup", "Back up with vzdump", "vzdump", _C, "vzdump <vmid> --storage <storage> --mode snapshot --compress zstd", "pvesh get /nodes/<node>/storage/<storage>/content --content backup", "pvesm free <storage>:backup/<archive>", perms="VM.Backup ;; Datastore.AllocateSpace", cats="backup")
_add("proxmox", "backup", "Restore a VM backup", "qmrestore", _X, "qmrestore <storage>:backup/<archive> <vmid> --force", "qm config <vmid> ;; qm start <vmid>", "not reversible: the previous disks of <vmid> are overwritten; restore an older archive", perms="VM.Allocate ;; Datastore.AllocateSpace", cats="data")

# Docker, Compose, Podman, containerd
_add("docker", "engine", "List networks", "docker network ls", _R, "docker network ls")
_add("docker", "engine", "List volumes", "docker volume ls", _R, "docker volume ls --filter dangling=true")
_add("docker", "engine", "Remove a volume", "docker volume rm", _X, "docker volume rm <volume>", "docker volume ls --filter name=<volume>", "not reversible: restore the data from a backup (docker run --rm -v <volume>:/data -v $PWD:/b alpine tar xzf /b/<volume>.tgz -C /data)", perms="docker group or root", cats="data")
_add("docker", "engine", "Prune images", "docker image prune", _X, "docker image prune -a --filter until=168h -f", "docker system df", "pull the images again (docker pull <image>)", perms="docker group or root", cats="data")
_add("docker", "compose", "Compose services status", "docker compose ps", _R, "docker compose -f compose.yaml ps")
_add("docker", "compose", "Compose logs", "docker compose logs", _R, "docker compose logs --since 30m --tail 200 <service>")
_add("docker", "compose", "Pull Compose images", "docker compose pull", _C, "docker compose pull", "docker compose images", "pin the previous tags in compose.yaml", perms="docker group or root")
_add("docker", "compose", "Stop and remove a Compose stack", "docker compose down", _X, "docker compose down", "docker compose ps -a", "docker compose up -d (named volumes are kept unless -v)", perms="docker group or root", cats="availability")
_add("docker", "compose", "Restart Compose services", "docker compose restart", _C, "docker compose restart <service>", "docker compose ps <service>", "not reversible (a restart)", perms="docker group or root")
_add("podman", "engine", "List images", "podman images", _R, "podman images")
_add("podman", "engine", "Run a container", "podman run", _C, "podman run -d --name <name> -p 8080:80 <image>", "podman ps --filter name=<name>", "podman rm -f <name>")
_add("podman", "pod", "Create a pod", "podman pod create", _C, "podman pod create --name <pod> -p 8080:80", "podman pod ps --filter name=<pod>", "podman pod rm -f <pod>")
_add("podman", "pod", "List pods", "podman pod ps", _R, "podman pod ps")
_add("podman", "pod", "Remove a pod", "podman pod rm", _X, "podman pod rm -f <pod>", "podman pod ps --filter name=<pod>", "podman kube play <pod>.yaml (from podman kube generate saved before)", cats="availability")
_add("podman", "kube", "Play a Kubernetes YAML", "podman kube play", _C, "podman kube play app.yaml", "podman pod ps ;; podman ps", "podman kube down app.yaml")
_add("podman", "kube", "Tear down a Kubernetes YAML", "podman kube down", _X, "podman kube down app.yaml", "podman pod ps", "podman kube play app.yaml", cats="availability")
_add("podman", "systemd", "Generate a systemd unit", "podman generate systemd", _P, "podman generate systemd --new --name <container> --files")
_add("podman", "auto-update", "Auto-update containers", "podman auto-update", _C, "podman auto-update", "podman auto-update --dry-run ;; podman ps", "podman auto-update rolls back automatically on failure (--rollback default true); otherwise podman run the previous image tag")
_add("podman", "auto-update", "Preview auto-updates", "podman auto-update --dry-run", _P, "podman auto-update --dry-run")
_add("containerd", "ctr", "List images", "ctr images ls", _R, "ctr -n k8s.io images ls -q", perms="root")
_add("containerd", "ctr", "Pull an image", "ctr images pull", _C, "ctr -n k8s.io images pull docker.io/library/<image>:<tag>", "ctr -n k8s.io images ls name==docker.io/library/<image>:<tag>", "ctr -n k8s.io images rm docker.io/library/<image>:<tag>", perms="root")
_add("containerd", "ctr", "Remove an image", "ctr images rm", _X, "ctr -n k8s.io images rm <ref>", "ctr -n k8s.io images ls name==<ref>", "ctr -n k8s.io images pull <ref>", perms="root", cats="data")
_add("containerd", "ctr", "List containers", "ctr containers ls", _R, "ctr -n k8s.io containers ls", perms="root")
_add("containerd", "cri", "List pods", "crictl pods", _R, "crictl pods --namespace <ns>", perms="root")
_add("containerd", "cri", "Container logs", "crictl logs", _R, "crictl logs --tail 200 <container-id>", perms="root")
_add("containerd", "cri", "Inspect a container", "crictl inspect", _R, "crictl inspect <container-id>", perms="root")
_add("containerd", "cri", "Remove an image", "crictl rmi", _X, "crictl rmi <image>", "crictl images | grep <image>", "crictl pull <image>", perms="root", cats="data")
_add("containerd", "cri", "Stop a pod sandbox", "crictl stopp", _X, "crictl stopp <pod-id>", "crictl pods --id <pod-id>", "the kubelet recreates the pod sandbox; or delete the pod with kubectl", perms="root", cats="availability")
_add("containerd", "nerdctl", "Compose up", "nerdctl compose up", _C, "nerdctl compose up -d", "nerdctl compose ps", "nerdctl compose down")

# Kubernetes
_K = "kubernetes"
_add(_K, "core", "Nodes with addresses", "kubectl get nodes -o wide", _R, "kubectl get nodes -o wide", perms="list nodes")
_add(_K, "metrics", "Node resource usage", "kubectl top nodes", _R, "kubectl top nodes", perms="get nodes.metrics.k8s.io")
_add(_K, "metrics", "Pod resource usage", "kubectl top pods", _R, "kubectl top pods -A --sort-by=memory", perms="get pods.metrics.k8s.io")
_add(_K, "core", "Cordon a node", "kubectl cordon", _C, "kubectl cordon <node>", "kubectl get node <node> (SchedulingDisabled)", "kubectl uncordon <node>", perms="patch nodes")
_add(_K, "core", "Uncordon a node", "kubectl uncordon", _C, "kubectl uncordon <node>", "kubectl get node <node> (Ready, no SchedulingDisabled)", "kubectl cordon <node>", perms="patch nodes")
_add(_K, "core", "Taint a node", "kubectl taint nodes", _X, "kubectl taint nodes <node> dedicated=<team>:NoExecute", "kubectl describe node <node> | grep Taints", "kubectl taint nodes <node> dedicated=<team>:NoExecute-", perms="patch nodes", cats="availability")
_add(_K, "core", "Set a container image", "kubectl set image", _C, "kubectl set image deployment/<app> <container>=<image>:<tag> -n <ns>", "kubectl rollout status deployment/<app> -n <ns> --timeout 300s", "kubectl rollout undo deployment/<app> -n <ns>", perms="patch deployments")
_add(_K, "autoscaling", "Autoscale a deployment", "kubectl autoscale deployment", _C, "kubectl autoscale deployment <app> -n <ns> --min 2 --max 10 --cpu-percent 70", "kubectl get hpa <app> -n <ns>", "kubectl delete hpa <app> -n <ns>", perms="create horizontalpodautoscalers")
_add(_K, "autoscaling", "Horizontal pod autoscalers", "kubectl get hpa", _R, "kubectl get hpa -A", perms="list horizontalpodautoscalers")
_add(_K, "core", "Port-forward to a service", "kubectl port-forward", _C, "kubectl port-forward svc/<service> 8080:80 -n <ns>", "curl -fsS http://127.0.0.1:8080/healthz", "stop the port-forward process (Ctrl-C)", perms="create pods/portforward", cats="local")
_add(_K, "policy", "Pod disruption budgets", "kubectl get pdb", _R, "kubectl get pdb -A", perms="list poddisruptionbudgets")
_add(_K, "network", "Network policies (firewall)", "kubectl get networkpolicies", _R, "kubectl get networkpolicies -A", perms="list networkpolicies")
_add(_K, "network", "Apply a network policy (firewall)", "kubectl apply -f networkpolicy.yaml", _X, "kubectl apply -f networkpolicy.yaml -n <ns>", "kubectl describe networkpolicy <name> -n <ns> ;; kubectl exec <client-pod> -n <ns> -- nc -vz <service> <port>", "kubectl delete networkpolicy <name> -n <ns>", perms="create networkpolicies ;; patch networkpolicies", cats="network")
_add(_K, "rbac", "RBAC role bindings", "kubectl get rolebindings", _R, "kubectl get rolebindings -A -o wide", perms="list rolebindings")
_add(_K, "rbac", "RBAC cluster role bindings", "kubectl get clusterrolebindings", _R, "kubectl get clusterrolebindings -o wide", perms="list clusterrolebindings")
_add(_K, "rbac", "RBAC permissions of a subject", "kubectl auth can-i --list", _R, "kubectl auth can-i --list --as system:serviceaccount:<ns>:<sa> -n <ns>", perms="create selfsubjectrulesreviews ;; impersonate serviceaccounts")
_add(_K, "rbac", "Create an RBAC role binding", "kubectl create rolebinding", _X, "kubectl create rolebinding <name> --clusterrole view --serviceaccount <ns>:<sa> -n <ns>", "kubectl auth can-i list pods --as system:serviceaccount:<ns>:<sa> -n <ns>", "kubectl delete rolebinding <name> -n <ns>", perms="create rolebindings ;; bind clusterroles", cats="iam")
_add(_K, "secrets", "Create a secret", "kubectl create secret generic", _X, "kubectl create secret generic <name> -n <ns> --from-file=<key>=./<file>", "kubectl get secret <name> -n <ns> -o jsonpath='{.data}' | jq 'keys'", "kubectl delete secret <name> -n <ns> ;; or re-apply the previous secret from the secret store", perms="create secrets", cats="secrets")
_add(_K, "discovery", "API versions", "kubectl api-versions", _R, "kubectl api-versions", perms="get /apis (discovery, any authenticated user)")
_add(_K, "kustomize", "Kustomize build", "kustomize build", _P, "kustomize build overlays/prod", perms="none (local render)")
_add(_K, "kustomize", "Kustomize diff", "kubectl diff -k", _P, "kubectl diff -k overlays/prod", perms="get, patch (dry-run) on the rendered kinds")
_add(_K, "kustomize", "Kustomize apply", "kubectl apply -k", _C, "kubectl apply -k overlays/prod", "kubectl diff -k overlays/prod (empty) ;; kubectl rollout status deployment/<app> -n <ns>", "git revert the overlay change then kubectl apply -k overlays/prod ;; kubectl rollout undo deployment/<app> -n <ns>", perms="create, patch on the rendered kinds")
_add(_K, "cert-manager", "cert-manager issuers", "kubectl get issuers,clusterissuers -A", _R, "kubectl get issuers,clusterissuers -A", perms="list issuers.cert-manager.io ;; list clusterissuers.cert-manager.io")
_add(_K, "cert-manager", "cert-manager API check", "cmctl check api", _R, "cmctl check api --wait 2m", perms="create certificaterequests.cert-manager.io (dry-run)")
_add(_K, "cert-manager", "cert-manager certificate status", "cmctl status certificate", _R, "cmctl status certificate <cert> -n <ns>", perms="get certificates.cert-manager.io")
_add(_K, "cert-manager", "cert-manager renew a certificate", "cmctl renew", _C, "cmctl renew <cert> -n <ns>", "cmctl status certificate <cert> -n <ns> ;; kubectl get certificate <cert> -n <ns>", "not needed: the previous secret stays valid until its expiry; restore the old TLS secret from backup if the new one is wrong", perms="patch certificates.cert-manager.io/status")
_add(_K, "opa", "Gatekeeper constraint templates", "kubectl get constrainttemplates", _R, "kubectl get constrainttemplates", perms="list constrainttemplates.templates.gatekeeper.sh")
_add(_K, "opa", "Apply an OPA Gatekeeper constraint", "kubectl apply -f constraint.yaml", _X, "kubectl apply -f constraint.yaml", "kubectl get <kind> <name> -o jsonpath='{.status.totalViolations}'", "kubectl delete -f constraint.yaml", perms="create constraints.gatekeeper.sh", cats="iam")
_add(_K, "kyverno", "Kyverno policy reports", "kubectl get policyreports -A", _R, "kubectl get policyreports -A", perms="list policyreports.wgpolicyk8s.io")
_add(_K, "falco", "Upgrade Falco rules with Helm", "helm upgrade falco falcosecurity/falco", _C, "helm upgrade falco falcosecurity/falco -n falco --reuse-values -f custom-rules.yaml", "kubectl rollout status ds/falco -n falco ;; kubectl logs -n falco -l app.kubernetes.io/name=falco --tail 20", "helm rollback falco -n falco", perms="patch daemonsets ;; patch configmaps")
# Kubernetes managed and distributions
_add(_K, "eks", "EKS kubeconfig and nodes", "kubectl get nodes -L eks.amazonaws.com/nodegroup", _R, "kubectl get nodes -L eks.amazonaws.com/nodegroup", perms="list nodes")
_add(_K, "k3s", "K3s configuration check", "k3s check-config", _R, "k3s check-config", perms="root on the node")
_add(_K, "k3s", "K3s etcd snapshot", "k3s etcd-snapshot save", _C, "k3s etcd-snapshot save --name pre-change", "k3s etcd-snapshot list", "k3s etcd-snapshot delete pre-change-<node>-<ts>", perms="root on the K3s server", cats="backup")
_add(_K, "k3s", "K3s etcd snapshots", "k3s etcd-snapshot list", _R, "k3s etcd-snapshot list", perms="root on the K3s server")
_add(_K, "k3s", "Install a K3s server", "curl -sfL https://get.k3s.io | sh -", _X, "curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=<version> sh -s - server --cluster-init", "systemctl is-active k3s ;; k3s kubectl get nodes", "/usr/local/bin/k3s-uninstall.sh", perms="root on the node", cats="availability")
_add(_K, "rke2", "RKE2 nodes", "KUBECONFIG=/etc/rancher/rke2/rke2.yaml kubectl get nodes", _R, "KUBECONFIG=/etc/rancher/rke2/rke2.yaml /var/lib/rancher/rke2/bin/kubectl get nodes -o wide", perms="root on the RKE2 server")
_add(_K, "rke2", "RKE2 server status", "systemctl status rke2-server", _R, "systemctl status rke2-server --no-pager", perms="root on the node")
_add(_K, "rke2", "RKE2 etcd snapshot", "rke2 etcd-snapshot save", _C, "rke2 etcd-snapshot save --name pre-change", "rke2 etcd-snapshot list", "rke2 etcd-snapshot delete pre-change-<node>-<ts>", perms="root on the RKE2 server", cats="backup")
_add(_K, "rke2", "RKE2 etcd snapshots", "rke2 etcd-snapshot list", _R, "rke2 etcd-snapshot list", perms="root on the RKE2 server")
_add(_K, "microk8s", "MicroK8s kubectl", "microk8s kubectl get", _R, "microk8s kubectl get pods -A", perms="root or microk8s group")
_add(_K, "microk8s", "Enable a MicroK8s addon", "microk8s enable", _C, "microk8s enable dns ingress", "microk8s status --addon <addon>", "microk8s disable <addon>", perms="root or microk8s group")
_add(_K, "microk8s", "Disable a MicroK8s addon", "microk8s disable", _X, "microk8s disable <addon>", "microk8s status --addon <addon>", "microk8s enable <addon>", perms="root or microk8s group", cats="availability")
_add(_K, "microk8s", "MicroK8s inspection report", "microk8s inspect", _R, "microk8s inspect", perms="root")
_add(_K, "microk8s", "Refresh MicroK8s certificates", "microk8s refresh-certs", _C, "microk8s refresh-certs --cert server.crt", "microk8s refresh-certs --check", "restore /var/snap/microk8s/current/certs from the backup taken by refresh-certs", perms="root")
_add(_K, "kind", "Create a kind cluster", "kind create cluster", _C, "kind create cluster --name <name> --config kind.yaml", "kubectl cluster-info --context kind-<name>", "kind delete cluster --name <name>", perms="docker group", cats="local")
_add(_K, "kind", "List kind clusters", "kind get clusters", _R, "kind get clusters", perms="docker group")
_add(_K, "kind", "Delete a kind cluster", "kind delete cluster", _X, "kind delete cluster --name <name>", "kind get clusters", "kind create cluster --name <name> --config kind.yaml", perms="docker group", cats="local")
_add(_K, "minikube", "Start a minikube cluster", "minikube start", _C, "minikube start -p <profile> --kubernetes-version <version>", "minikube status -p <profile>", "minikube delete -p <profile>", perms="docker group or hypervisor access", cats="local")
_add(_K, "minikube", "Delete a minikube cluster", "minikube delete", _X, "minikube delete -p <profile>", "minikube profile list", "minikube start -p <profile>", perms="docker group or hypervisor access", cats="local")
# OpenShift
_O = "openshift"
_add(_O, "route", "List routes", "oc get routes", _R, "oc get routes -n <project>", perms="list routes.route.openshift.io")
_add(_O, "route", "Expose a service as a route", "oc expose service", _C, "oc expose service <svc> -n <project> --hostname <host>", "oc get route <svc> -n <project> ;; curl -fsS https://<host>/", "oc delete route <svc> -n <project>", perms="create routes.route.openshift.io", cats="network")
_add(_O, "rollout", "Rollout status", "oc rollout status", _R, "oc rollout status deployment/<app> -n <project>", perms="get deployments")
_add(_O, "rollout", "Roll back a rollout", "oc rollout undo", _C, "oc rollout undo deployment/<app> -n <project>", "oc rollout status deployment/<app> -n <project>", "oc rollout undo deployment/<app> -n <project> --to-revision <revision>", perms="patch deployments")
_add(_O, "rollout", "Start a new rollout", "oc rollout latest", _C, "oc rollout latest dc/<app> -n <project>", "oc rollout status dc/<app> -n <project>", "oc rollout undo dc/<app> -n <project>", perms="create deploymentconfigs/instantiate")
_add(_O, "adm", "Node usage", "oc adm top nodes", _R, "oc adm top nodes", perms="get nodes.metrics.k8s.io")
_add(_O, "adm", "Grant a role to a user", "oc adm policy add-role-to-user", _X, "oc adm policy add-role-to-user edit <user> -n <project>", "oc adm policy who-can update deployments -n <project>", "oc adm policy remove-role-from-user edit <user> -n <project>", perms="create rolebindings ;; bind roles", cats="iam")
_add(_O, "adm", "Cluster version", "oc get clusterversion", _R, "oc get clusterversion -o jsonpath='{.items[0].status.desired.version}'", perms="get clusterversions.config.openshift.io")
_add(_O, "adm", "Available cluster upgrades", "oc adm upgrade", _P, "oc adm upgrade", perms="get clusterversions.config.openshift.io")
_add(_O, "adm", "Upgrade the cluster", "oc adm upgrade --to", _X, "oc adm upgrade --to <version>", "oc get clusterversion ;; oc get clusteroperators", "not reversible: OpenShift does not support downgrades; restore from an etcd backup (cluster-backup.sh) taken before", perms="patch clusterversions.config.openshift.io (cluster-admin)", cats="availability")
_add(_O, "core", "Create a project", "oc new-project", _C, "oc new-project <project> --display-name '<name>'", "oc get project <project>", "oc delete project <project>", perms="create projectrequests")
_add(_O, "core", "Explain a resource", "oc explain", _R, "oc explain route.spec.tls", perms="get /openapi (discovery)")
_add(_O, "olm", "Installed operators (OLM)", "oc get csv -A", _R, "oc get csv -A", perms="list clusterserviceversions.operators.coreos.com")
_add(_O, "security", "Security context constraints", "oc get scc", _R, "oc get scc", perms="list securitycontextconstraints.security.openshift.io")

# IaC
_add("terraform", "state", "List state resources", "terraform state list", _R, "terraform state list")
_add("terraform", "state", "Show a state resource", "terraform state show", _R, "terraform state show '<address>'")
_add("terraform", "state", "Move a state resource", "terraform state mv", _X, "terraform state mv '<old-address>' '<new-address>'", "terraform state list | grep '<new-address>' ;; terraform plan (no changes)", "terraform state mv '<new-address>' '<old-address>' ;; or terraform state push the backup terraform.tfstate.backup", cats="data")
_add("terraform", "workspace", "List workspaces", "terraform workspace list", _R, "terraform workspace list")
_add("terraform", "workspace", "Select a workspace", "terraform workspace select", _C, "terraform workspace select <env>", "terraform workspace show", "terraform workspace select <previous>", cats="local")
_add("terraform", "cli", "Lock providers", "terraform providers lock", _C, "terraform providers lock -platform=linux_amd64 -platform=darwin_arm64", "git diff .terraform.lock.hcl", "git checkout .terraform.lock.hcl", cats="local")
_add("terraform", "state", "Force-unlock the state", "terraform force-unlock", _X, "terraform force-unlock <lock-id>", "terraform plan -lock-timeout=30s (acquires the lock)", "not reversible: only run it when the lock holder is dead, a concurrent apply can corrupt the state", cats="data")
_add("terraform", "cli", "Outputs as JSON", "terraform output -json", _R, "terraform output -json")
_add("terraform", "cli", "Run Terraform tests", "terraform test", _P, "terraform test -verbose")
_add("terraform", "cli", "Dependency graph", "terraform graph", _R, "terraform graph | dot -Tsvg > graph.svg")
_add("terraform", "cli", "Refresh-only plan (drift)", "terraform plan -refresh-only", _P, "terraform plan -refresh-only -out drift.tfplan")
_add("opentofu", "cli", "Initialize", "tofu init", _P, "tofu init -upgrade")
_add("opentofu", "cli", "Apply a saved plan", "tofu apply tfplan", _C, "tofu plan -out tfplan && tofu apply tfplan", "tofu plan -detailed-exitcode (exit 0)", "git revert the change, tofu plan -out tfplan, tofu apply tfplan")
_add("opentofu", "state", "List state resources", "tofu state list", _R, "tofu state list")
_add("pulumi", "stack", "List stacks", "pulumi stack ls", _R, "pulumi stack ls")
_add("pulumi", "stack", "Stack outputs", "pulumi stack output", _R, "pulumi stack output --json -s <stack>")
_add("pulumi", "stack", "Refresh the state", "pulumi refresh", _C, "pulumi refresh --yes -s <stack>", "pulumi preview -s <stack> --expect-no-changes", "pulumi stack import --file pre-refresh.json -s <stack>", cats="data")
_add("pulumi", "stack", "Destroy a stack", "pulumi destroy", _X, "pulumi destroy --yes -s <stack>", "pulumi stack -s <stack> (0 resources)", "not reversible for data: pulumi up -s <stack> recreates the resources empty; restore data from backups", cats="data")
_add("pulumi", "stack", "Export stack state (backup)", "pulumi stack export", _R, "pulumi stack export -s <stack> --file pre-change.json")
_add("pulumi", "stack", "Import stack state (rollback)", "pulumi stack import", _X, "pulumi stack import -s <stack> --file pre-change.json", "pulumi preview -s <stack>", "pulumi stack import -s <stack> --file <the export taken just before>", cats="data")
_add("pulumi", "config", "Set stack config", "pulumi config set", _C, "pulumi config set <key> <value> -s <stack>", "pulumi config get <key> -s <stack>", "pulumi config set <key> <previous> -s <stack> ;; or pulumi config rm <key>", cats="local")
_add("cdk", "cli", "Synthesize", "cdk synth", _P, "cdk synth <stack> > template.yaml", perms="none (local synthesis; lookups need cloudformation:DescribeStacks)")
_add("cdk", "cli", "List stacks", "cdk ls", _R, "cdk ls --long")
_add("cdk", "cli", "Bootstrap an environment", "cdk bootstrap", _C, "cdk bootstrap aws://<account>/<region>", "aws cloudformation describe-stacks --stack-name CDKToolkit", "aws cloudformation delete-stack --stack-name CDKToolkit (empty the staging bucket first)", perms="cloudformation:* ;; iam:CreateRole ;; s3:CreateBucket")
_add("cdk", "cli", "Destroy a stack", "cdk destroy", _X, "cdk destroy <stack> --force", "aws cloudformation describe-stacks --stack-name <stack> (does not exist)", "not reversible for data: cdk deploy <stack> recreates it; restore data from backups", perms="cloudformation:DeleteStack", cats="data")

# Configuration
_add("ansible", "adhoc", "Ping hosts", "ansible -m ping", _R, "ansible all -i inventory.ini -m ping")
_add("ansible", "adhoc", "Gather facts", "ansible -m setup", _R, "ansible <host> -i inventory.ini -m setup -a 'filter=ansible_distribution*'")
_add("ansible", "playbook", "Syntax check", "ansible-playbook --syntax-check", _P, "ansible-playbook -i inventory.ini site.yml --syntax-check")
_add("ansible", "playbook", "List target hosts", "ansible-playbook --list-hosts", _P, "ansible-playbook -i inventory.ini site.yml --list-hosts")
_add("ansible", "playbook", "Run on a subset of hosts", "ansible-playbook --limit", _C, "ansible-playbook -i inventory.ini site.yml --limit <host> --diff", "ansible-playbook -i inventory.ini site.yml --limit <host> --check --diff (changed=0)", "run the playbook from the previous Git tag with --limit <host>", cats="remote-exec")
_add("ansible", "inventory", "Inventory as JSON", "ansible-inventory --list", _R, "ansible-inventory -i inventory.ini --list")
_add("ansible", "galaxy", "Install roles and collections", "ansible-galaxy install", _C, "ansible-galaxy install -r requirements.yml", "ansible-galaxy collection list", "pin the previous versions in requirements.yml and reinstall with --force", cats="local")
_add("ansible", "vault", "View a vault file", "ansible-vault view", _R, "ansible-vault view group_vars/prod/vault.yml", cats="secrets")
_add("ansible", "vault", "Encrypt a file with vault", "ansible-vault encrypt", _C, "ansible-vault encrypt group_vars/prod/vault.yml", "head -1 group_vars/prod/vault.yml ($ANSIBLE_VAULT;1.1;AES256)", "ansible-vault decrypt group_vars/prod/vault.yml", cats="secrets")
_add("ssh", "client", "Remote uptime", "ssh <host> uptime", _R, "ssh <user>@<host> uptime", perms="ssh key on the host")
_add("ssh", "client", "Check a control master", "ssh -O check", _R, "ssh -O check <host>")
_add("ssh", "client", "Collect host keys", "ssh-keyscan", _R, "ssh-keyscan -t ed25519 <host>")
_add("ssh", "client", "Copy a file with scp", "scp", _C, "scp ./app.conf <user>@<host>:/etc/app/app.conf", "ssh <user>@<host> sha256sum /etc/app/app.conf", "scp the previous copy back (keep /etc/app/app.conf.bak)", perms="write access on the target path")
_add("ssh", "client", "Preview an rsync", "rsync --dry-run", _P, "rsync -avz --delete --dry-run ./site/ <user>@<host>:/var/www/site/")
_add("ssh", "client", "Sync files with rsync", "rsync -avz", _C, "rsync -avz --delete --backup --backup-dir=/var/backups/site-$(date +%F) ./site/ <user>@<host>:/var/www/site/", "rsync -avzn --delete ./site/ <user>@<host>:/var/www/site/ (nothing to transfer)", "rsync -avz <user>@<host>:/var/backups/site-<date>/ back into place", perms="write access on the target path")
_add("ssh", "client", "Restart a remote service", "ssh <host> sudo systemctl restart", _C, "ssh <user>@<host> sudo systemctl restart <unit>", "ssh <user>@<host> systemctl is-active <unit>", "restore the previous config over ssh then restart again", perms="sudo on the host", cats="remote-exec")
_add("cloud-init", "cli", "Query instance data", "cloud-init query", _R, "cloud-init query --list-keys", perms="root for sensitive keys")
_add("cloud-init", "cli", "Validate user-data", "cloud-init schema --config-file", _P, "cloud-init schema --config-file user-data.yaml --annotate")
_add("cloud-init", "cli", "Boot timing analysis", "cloud-init analyze show", _R, "cloud-init analyze show")
_add("cloud-init", "cli", "Clean cloud-init state", "cloud-init clean", _X, "cloud-init clean --logs --reboot", "cloud-init status --wait after reboot", "not reversible: modules run again on next boot as on a first boot; snapshot the VM first", perms="root", cats="availability")
_add("helm", "repo", "Add a chart repository", "helm repo add", _C, "helm repo add <name> <url>", "helm repo list", "helm repo remove <name>", cats="local")
_add("helm", "repo", "Update chart repositories", "helm repo update", _C, "helm repo update", "helm search repo <chart>", "not needed: only the local index cache changes", cats="local")
_add("helm", "repo", "Search charts", "helm search repo", _R, "helm search repo <chart> --versions")
_add("helm", "chart", "Show chart default values", "helm show values", _R, "helm show values <repo>/<chart> --version <version>")
_add("helm", "release", "Release values", "helm get values", _R, "helm get values <release> -n <ns> -a", perms="get secrets (Helm release storage)")
_add("helm", "release", "Release history", "helm history", _R, "helm history <release> -n <ns>", perms="list secrets")
_add("helm", "release", "Run release tests", "helm test", _C, "helm test <release> -n <ns> --logs", "helm status <release> -n <ns>", "kubectl delete pod -l helm.sh/hook=test -n <ns> (test pods only)", perms="create pods")
_add("helm", "chart", "Update chart dependencies", "helm dependency update", _C, "helm dependency update ./chart", "helm dependency list ./chart", "git checkout chart/Chart.lock chart/charts/", cats="local")
_add("helm", "plugin", "List plugins", "helm plugin list", _R, "helm plugin list")
_add("helm", "release", "Upgrade reusing values", "helm upgrade --reuse-values", _C, "helm upgrade <release> <repo>/<chart> -n <ns> --reuse-values --version <version> --atomic --wait", "helm status <release> -n <ns> ;; kubectl rollout status deployment/<app> -n <ns>", "helm rollback <release> <previous-revision> -n <ns>", perms="get, update secrets ;; patch on the chart kinds")

# CI/CD
_add("github", "actions", "Failed run logs", "gh run view --log-failed", _R, "gh run view <run-id> --log-failed", perms="actions:read")
_add("github", "actions", "Re-run failed jobs", "gh run rerun --failed", _C, "gh run rerun <run-id> --failed", "gh run watch <run-id> --exit-status", "gh run cancel <run-id>", perms="actions:write")
_add("github", "actions", "List workflows", "gh workflow list", _R, "gh workflow list --all", perms="actions:read")
_add("github", "actions", "Enable a workflow", "gh workflow enable", _C, "gh workflow enable <workflow>", "gh workflow view <workflow>", "gh workflow disable <workflow>", perms="actions:write")
_add("github", "actions", "Disable a workflow", "gh workflow disable", _C, "gh workflow disable <workflow>", "gh workflow list --all (disabled_manually)", "gh workflow enable <workflow>", perms="actions:write")
_add("github", "actions", "Set a repository variable", "gh variable set", _C, "gh variable set <NAME> --body <value> --repo <owner>/<repo>", "gh variable list --repo <owner>/<repo>", "gh variable set <NAME> --body <previous> ;; or gh variable delete <NAME>", perms="variables:write")
_add("github", "actions", "List repository secrets", "gh secret list", _R, "gh secret list --repo <owner>/<repo>", perms="secrets:read (names only)")
_add("gitlab", "ci", "Pipeline status", "glab ci status", _R, "glab ci status --branch <branch>", perms="Reporter")
_add("gitlab", "ci", "View a pipeline", "glab ci view", _R, "glab ci view <branch>", perms="Reporter")
_add("gitlab", "ci", "Trace a job log", "glab ci trace", _R, "glab ci trace <job-id>", perms="Reporter")
_add("gitlab", "ci", "Retry a job", "glab ci retry", _C, "glab ci retry <job-id>", "glab ci status", "glab ci cancel job <job-id>", perms="Developer")
_add("gitlab", "ci", "Cancel a pipeline", "glab ci cancel pipeline", _C, "glab ci cancel pipeline <pipeline-id>", "glab ci list --status canceled", "glab ci retry or glab ci run -b <branch>", perms="Developer")
_add("gitlab", "ci", "Delete a pipeline", "glab ci delete", _X, "glab ci delete <pipeline-id>", "glab ci list", "not reversible: pipeline logs and artifacts are lost; run a new pipeline with glab ci run", perms="Owner", cats="data")
_add("gitlab", "ci", "List CI variables", "glab variable list", _R, "glab variable list -R <group>/<project>", perms="Maintainer")
_add("gitlab", "ci", "Set a CI variable", "glab variable set", _X, "glab variable set <KEY> <value> --masked --protected -R <group>/<project>", "glab variable get <KEY> -R <group>/<project>", "glab variable update <KEY> <previous> ;; or glab variable delete <KEY>", perms="Maintainer", cats="secrets")
_add("gitlab", "api", "GitLab API call", "glab api projects/:id/pipelines", _R, "glab api projects/:id/pipelines?status=failed", perms="read_api", endpoint="GET /api/v4/projects/{id}/pipelines")
_add("jenkins", "api", "List jobs", "curl $JENKINS_URL/api/json?tree=jobs[name,color]", _R, "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN \"$JENKINS_URL/api/json?tree=jobs[name,color]\"", perms="Overall/Read", endpoint="GET /api/json?tree=jobs[name,color]")
_add("jenkins", "api", "Build with parameters", "curl -X POST $JENKINS_URL/job/<job>/buildWithParameters", _C, "curl -fsS -X POST -u $JENKINS_USER:$JENKINS_TOKEN \"$JENKINS_URL/job/<job>/buildWithParameters?ENV=staging\"", "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/lastBuild/api/json?tree=result,building", "curl -X POST -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/<n>/stop", perms="Job/Build", endpoint="POST /job/{job}/buildWithParameters")
_add("jenkins", "api", "Console output", "curl $JENKINS_URL/job/<job>/lastBuild/consoleText", _R, "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/lastBuild/consoleText | tail -100", perms="Job/Read", endpoint="GET /job/{job}/lastBuild/consoleText")
_add("jenkins", "api", "Stop a build", "curl -X POST $JENKINS_URL/job/<job>/<n>/stop", _C, "curl -fsS -X POST -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/<n>/stop", "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN $JENKINS_URL/job/<job>/<n>/api/json?tree=result (ABORTED)", "start it again: POST /job/<job>/build", perms="Job/Cancel", endpoint="POST /job/{job}/{n}/stop")
_add("jenkins", "cli", "Build with the Jenkins CLI", "java -jar jenkins-cli.jar", _C, "java -jar jenkins-cli.jar -s $JENKINS_URL -auth @creds build <job> -s -v", "exit code of the -s (sync) build ;; curl $JENKINS_URL/job/<job>/lastBuild/api/json", "java -jar jenkins-cli.jar -s $JENKINS_URL -auth @creds stop-builds <job>", perms="Job/Build")
_add("forgejo", "actions", "List workflow runs", "curl $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/runs", _R, "curl -fsS -H \"Authorization: token $FORGEJO_TOKEN\" $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/runs", perms="read:repository", endpoint="GET /api/v1/repos/{owner}/{repo}/actions/runs")
_add("forgejo", "actions", "Dispatch a workflow", "curl -X POST $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/workflows/<workflow>/dispatches", _C, "curl -fsS -X POST -H \"Authorization: token $FORGEJO_TOKEN\" -H 'Content-Type: application/json' -d '{\"ref\":\"main\"}' $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/workflows/<workflow>/dispatches", "curl -fsS -H \"Authorization: token $FORGEJO_TOKEN\" $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/tasks?limit=1", "cancel the run in the web UI (Actions, run, Cancel); the run itself does not modify the repository", perms="write:repository", endpoint="POST /api/v1/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches")
_add("forgejo", "actions", "List runners", "curl $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/runners", _R, "curl -fsS -H \"Authorization: token $FORGEJO_TOKEN\" $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/runners", perms="read:repository (repo admin)", endpoint="GET /api/v1/repos/{owner}/{repo}/actions/runners")
_add("forgejo", "actions", "Set a repository secret", "curl -X PUT $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/secrets/<name>", _X, "curl -fsS -X PUT -H \"Authorization: token $FORGEJO_TOKEN\" -H 'Content-Type: application/json' -d '{\"data\":\"<value>\"}' $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/secrets/<name>", "curl -fsS -H \"Authorization: token $FORGEJO_TOKEN\" $FORGEJO_URL/api/v1/repos/<owner>/<repo>/actions/secrets (name listed)", "PUT the previous value again from the secret store, or DELETE /api/v1/repos/{owner}/{repo}/actions/secrets/{name}", perms="write:repository (repo admin)", endpoint="PUT /api/v1/repos/{owner}/{repo}/actions/secrets/{secretname}", cats="secrets")
_add("forgejo", "tea", "List pull requests with tea", "tea pulls list", _R, "tea pulls list --repo <owner>/<repo> --state open", perms="read:repository")
_add("forgejo", "tea", "Create a release with tea", "tea releases create", _C, "tea releases create --repo <owner>/<repo> --tag <tag> --title <title>", "tea releases list --repo <owner>/<repo>", "tea releases delete --repo <owner>/<repo> <tag>", perms="write:repository")
_add("argocd", "app", "List applications", "argocd app list", _R, "argocd app list -o wide", perms="applications, get")
_add("argocd", "app", "Application history", "argocd app history", _R, "argocd app history <app>", perms="applications, get")
_add("argocd", "app", "Set application parameters", "argocd app set", _C, "argocd app set <app> --revision <git-ref>", "argocd app get <app> ;; argocd app wait <app> --health --sync", "argocd app set <app> --revision <previous-ref>", perms="applications, update")
_add("argocd", "app", "Delete an application", "argocd app delete", _X, "argocd app delete <app> --cascade=false", "argocd app list | grep <app>", "argocd app create from the manifest in Git (with --cascade=false the workloads were kept)", perms="applications, delete", cats="data")
_add("argocd", "app", "Wait for an application", "argocd app wait", _R, "argocd app wait <app> --health --sync --timeout 600", perms="applications, get")
_add("argocd", "proj", "List projects", "argocd proj list", _R, "argocd proj list", perms="projects, get")
_add("argocd", "cluster", "List clusters", "argocd cluster list", _R, "argocd cluster list", perms="clusters, get")
_add("argocd", "repo", "List repositories", "argocd repo list", _R, "argocd repo list", perms="repositories, get")
_add("argocd", "account", "Check a permission", "argocd account can-i", _R, "argocd account can-i sync applications '<project>/<app>'", perms="any authenticated user")
_add("flux", "gitops", "All Flux objects", "flux get all -A", _R, "flux get all -A --status-selector ready=false")
_add("flux", "gitops", "Git sources", "flux get sources git", _R, "flux get sources git -A")
_add("flux", "gitops", "Reconcile a Helm release", "flux reconcile helmrelease", _C, "flux reconcile helmrelease <name> -n <ns> --with-source", "flux get helmreleases <name> -n <ns> (Ready True)", "git revert the values change then flux reconcile source git <repo>", perms="patch helmreleases.helm.toolkit.fluxcd.io")
_add("flux", "gitops", "Reconcile a Git source", "flux reconcile source git", _C, "flux reconcile source git <repo> -n flux-system", "flux get sources git <repo> -n flux-system", "git revert the commit, then reconcile again", perms="patch gitrepositories.source.toolkit.fluxcd.io")
_add("flux", "gitops", "Resume reconciliation", "flux resume kustomization", _C, "flux resume kustomization <name> -n flux-system", "flux get kustomizations <name> (SUSPENDED False, Ready True)", "flux suspend kustomization <name>", perms="patch kustomizations.kustomize.toolkit.fluxcd.io")
_add("flux", "gitops", "Controller logs", "flux logs", _R, "flux logs --level error --since 1h")
_add("flux", "gitops", "Events", "flux events", _R, "flux events -A --types Warning")
_add("flux", "gitops", "Kustomization tree", "flux tree kustomization", _R, "flux tree kustomization flux-system")
_add("flux", "gitops", "Diff a kustomization", "flux diff kustomization", _P, "flux diff kustomization <name> --path ./clusters/prod")
_add("flux", "gitops", "Delete a kustomization", "flux delete kustomization", _X, "flux delete kustomization <name> --silent", "flux get kustomizations", "re-add it to Git (flux create kustomization --export) and flux reconcile source git flux-system; pruned workloads are recreated", perms="delete kustomizations.kustomize.toolkit.fluxcd.io", cats="data")
_add("flux", "gitops", "Bootstrap Flux", "flux bootstrap github", _X, "flux bootstrap github --owner <org> --repository <repo> --branch main --path clusters/<cluster>", "flux check ;; flux get kustomizations", "flux uninstall --keep-namespace (the Git repo commits stay)", perms="cluster-admin ;; GitHub repo admin token", cats="iam")

# Network (each concept named so a search finds it)
_add("network", "firewall", "ufw firewall status", "ufw status verbose", _R, "ufw status numbered", perms="root")
_add("network", "firewall", "ufw allow a port (firewall)", "ufw allow", _X, "ufw allow from <cidr> to any port 22 proto tcp", "ufw status numbered", "ufw delete allow from <cidr> to any port 22 proto tcp", perms="root", cats="network")
_add("network", "firewall", "firewalld zones (firewall)", "firewall-cmd --list-all", _R, "firewall-cmd --list-all --zone public", perms="root")
_add("network", "firewall", "firewalld add a service (firewall)", "firewall-cmd --add-service", _X, "firewall-cmd --zone public --add-service https --permanent && firewall-cmd --reload", "firewall-cmd --zone public --list-services", "firewall-cmd --zone public --remove-service https --permanent && firewall-cmd --reload", perms="root", cats="network")
_add("network", "firewall", "nftables add a rule (firewall)", "nft add rule", _X, "nft add rule inet filter input tcp dport 443 accept", "nft list chain inet filter input", "nft delete rule inet filter input handle <handle> ;; or nft -f /etc/nftables.conf", perms="root", cats="network")
_add("network", "dns", "DNS lookup with trace", "dig +trace", _R, "dig +trace <name>")
_add("network", "route", "Add a static route", "ip route add", _X, "ip route add 10.60.0.0/16 via <gateway> dev <iface>", "ip route get 10.60.0.1", "ip route del 10.60.0.0/16 via <gateway>", perms="root / CAP_NET_ADMIN", cats="network")
_add("network", "vpn", "WireGuard VPN status", "wg show", _R, "wg show all", perms="root")
_add("network", "vpn", "Bring up a WireGuard VPN", "wg-quick up", _X, "wg-quick up wg0", "wg show wg0 latest-handshakes ;; ping -c3 <peer-ip>", "wg-quick down wg0", perms="root", cats="network")
_add("network", "load balancer", "HAProxy load balancer stats", "echo 'show stat' | socat stdio /run/haproxy/admin.sock", _R, "echo 'show stat' | socat stdio /run/haproxy/admin.sock | cut -d, -f1,2,18", perms="root or haproxy group")

# Security
_add("security", "vault", "List secret engines", "vault secrets list", _R, "vault secrets list -detailed", perms="read sys/mounts")
_add("security", "vault", "Write a KV secret", "vault kv put", _X, "vault kv put -mount=secret <path> <key>=<value>", "vault kv metadata get -mount=secret <path>", "vault kv rollback -mount=secret -version=<previous> <path>", perms="create, update secret/data/<path>", cats="secrets")
_add("security", "vault", "Read a policy", "vault policy read", _R, "vault policy read <policy>", perms="read sys/policies/acl/<policy>")
_add("security", "vault", "Write a policy", "vault policy write", _X, "vault policy write <policy> policy.hcl", "vault policy read <policy> ;; vault token capabilities <token> <path>", "vault policy write <policy> previous-policy.hcl (vault policy read <policy> > previous-policy.hcl first)", perms="update sys/policies/acl/<policy>", cats="iam")
_add("security", "vault", "Look up a token", "vault token lookup", _R, "vault token lookup", perms="read auth/token/lookup-self")
_add("security", "vault", "Raft peers", "vault operator raft list-peers", _R, "vault operator raft list-peers", perms="read sys/storage/raft/configuration")
_add("security", "opa", "Evaluate a policy", "opa eval", _P, "opa eval -d policy.rego -i input.json 'data.authz.allow'")
_add("security", "opa", "Test policies", "opa test", _P, "opa test ./policies -v")
_add("security", "opa", "Gatekeeper verify", "gator verify", _P, "gator verify ./suite.yaml")
_add("security", "kyverno", "Apply Kyverno policies offline", "kyverno apply", _P, "kyverno apply ./policies --resource deployment.yaml")
_add("security", "trivy", "Scan a filesystem", "trivy fs", _R, "trivy fs --scanners vuln,secret,misconfig --severity HIGH,CRITICAL .")
_add("security", "trivy", "Generate an SBOM", "trivy image --format cyclonedx", _R, "trivy image --format cyclonedx --output sbom.json <image>")
_add("security", "trivy", "Scan an SBOM", "trivy sbom", _R, "trivy sbom sbom.json")
_add("security", "trivy", "Install the Trivy Operator", "helm upgrade --install trivy-operator", _C, "helm upgrade --install trivy-operator aqua/trivy-operator -n trivy-system --create-namespace --version <version>", "kubectl rollout status deployment/trivy-operator -n trivy-system ;; kubectl get vulnerabilityreports -A", "helm rollback trivy-operator -n trivy-system ;; or helm uninstall trivy-operator -n trivy-system", perms="cluster-admin (installs CRDs and ClusterRoles)")
_add("security", "falco", "Falco artifacts", "falcoctl artifact list", _R, "falcoctl artifact list")
_add("security", "falco", "Install Falco rules artifact", "falcoctl artifact install", _C, "falcoctl artifact install falco-rules:3", "falcoctl artifact list ;; falco --list | head", "falcoctl artifact install falco-rules:<previous>", perms="root")

# Observability
_add("observability", "prometheus", "Scrape targets", "curl $PROM/api/v1/targets", _R, "curl -fsS $PROM/api/v1/targets?state=active | jq '.data.activeTargets[] | select(.health!=\"up\") | .scrapeUrl'", endpoint="GET /api/v1/targets")
_add("observability", "prometheus", "Rules", "curl $PROM/api/v1/rules", _R, "curl -fsS $PROM/api/v1/rules | jq '.data.groups[].name'", endpoint="GET /api/v1/rules")
_add("observability", "prometheus", "Check the configuration", "promtool check config", _P, "promtool check config /etc/prometheus/prometheus.yml")
_add("observability", "prometheus", "Reload the configuration", "curl -X POST $PROM/-/reload", _C, "promtool check config prometheus.yml && curl -fsS -X POST $PROM/-/reload", "curl -fsS $PROM/api/v1/status/config | jq -r .data.yaml | head ;; prometheus_config_last_reload_successful == 1", "restore the previous prometheus.yml then POST /-/reload again", perms="--web.enable-lifecycle", endpoint="POST /-/reload")
_add("observability", "grafana", "Search dashboards", "curl $GRAFANA/api/search", _R, "curl -fsS -H \"Authorization: Bearer $GRAFANA_TOKEN\" \"$GRAFANA/api/search?type=dash-db&query=<name>\"", perms="dashboards:read", endpoint="GET /api/search")
_add("observability", "grafana", "List datasources", "curl $GRAFANA/api/datasources", _R, "curl -fsS -H \"Authorization: Bearer $GRAFANA_TOKEN\" $GRAFANA/api/datasources", perms="datasources:read", endpoint="GET /api/datasources")
_add("observability", "grafana", "Import a dashboard", "curl -X POST $GRAFANA/api/dashboards/db", _C, "curl -fsS -X POST -H \"Authorization: Bearer $GRAFANA_TOKEN\" -H 'Content-Type: application/json' -d @dashboard.json $GRAFANA/api/dashboards/db", "curl -fsS -H \"Authorization: Bearer $GRAFANA_TOKEN\" $GRAFANA/api/dashboards/uid/<uid>", "curl -X POST -H \"Authorization: Bearer $GRAFANA_TOKEN\" $GRAFANA/api/dashboards/uid/<uid>/restore -d '{\"version\":<previous>}'", perms="dashboards:write", endpoint="POST /api/dashboards/db")
_add("observability", "loki", "Labels", "logcli labels", _R, "logcli labels app")
_add("observability", "loki", "Series", "logcli series", _R, "logcli series '{namespace=\"<ns>\"}'")
_add("observability", "loki", "Ready", "curl $LOKI/ready", _R, "curl -fsS $LOKI/ready", endpoint="GET /ready")
_add("observability", "loki", "Delete log streams", "logcli delete create", _X, "logcli delete create --query '{app=\"<app>\"}' --from <start> --to <end>", "logcli delete list", "logcli delete cancel --request-id <id> (before the cancel period ends); after that not reversible", perms="Loki compactor deletion API", cats="data")
_add("observability", "tempo", "Trace by id", "curl $TEMPO/api/traces/<id>", _R, "curl -fsS $TEMPO/api/traces/<trace-id>", endpoint="GET /api/traces/{traceID}")
_add("observability", "tempo", "Ready", "curl $TEMPO/ready", _R, "curl -fsS $TEMPO/ready && curl -fsS $TEMPO/api/echo", endpoint="GET /ready")
_add("observability", "tempo", "Apply Tempo overrides", "kubectl apply -f tempo-overrides.yaml", _C, "kubectl apply -f tempo-overrides.yaml -n <ns>", "curl -fsS $TEMPO/status/runtime_config", "kubectl apply -f previous-tempo-overrides.yaml -n <ns>", perms="patch configmaps")
_add("observability", "otel", "Collector health", "curl http://<collector>:13133/", _R, "curl -fsS http://<collector>:13133/", endpoint="GET :13133/ (health_check extension)")
_add("observability", "otel", "OpenTelemetry collectors", "kubectl get opentelemetrycollectors -A", _R, "kubectl get opentelemetrycollectors -A", perms="list opentelemetrycollectors.opentelemetry.io")
_add("observability", "otel", "Apply an OpenTelemetry collector", "kubectl apply -f otel-collector.yaml", _C, "otelcol validate --config collector.yaml && kubectl apply -f otel-collector.yaml -n <ns>", "kubectl rollout status deployment/<collector>-collector -n <ns> ;; curl -fsS http://<collector>:13133/", "kubectl apply -f previous-otel-collector.yaml -n <ns>", perms="patch opentelemetrycollectors.opentelemetry.io")

# Data
_add("databases", "postgresql", "Check a connection", "pg_isready", _R, "pg_isready -h <host> -p 5432 -d <db>")
_add("databases", "postgresql", "Active sessions", "psql -c pg_stat_activity", _R, "psql \"$DATABASE_URL\" -c \"select pid, state, wait_event_type, now()-query_start as age, left(query,80) from pg_stat_activity where state <> 'idle' order by age desc\"", perms="pg_read_all_stats")
_add("databases", "postgresql", "Replication lag", "psql -c pg_stat_replication", _R, "psql \"$DATABASE_URL\" -c \"select client_addr, state, replay_lag from pg_stat_replication\"", perms="pg_monitor")
_add("databases", "postgresql", "Vacuum analyze", "psql -c 'vacuum analyze'", _C, "psql \"$DATABASE_URL\" -c 'vacuum (verbose, analyze) <table>'", "psql \"$DATABASE_URL\" -c \"select last_vacuum, last_analyze from pg_stat_user_tables where relname='<table>'\"", "not needed: vacuum only reclaims dead tuples and refreshes statistics", perms="table owner")
_add("databases", "postgresql", "Cancel a query", "psql -c pg_cancel_backend", _C, "psql \"$DATABASE_URL\" -c 'select pg_cancel_backend(<pid>)'", "psql \"$DATABASE_URL\" -c 'select state from pg_stat_activity where pid=<pid>'", "not reversible: the client must run the query again", perms="pg_signal_backend")
_add("databases", "mysql", "Process list", "mysql -e 'show processlist'", _R, "mysql -h <host> -u <user> -p -e 'show full processlist'", perms="PROCESS")
_add("databases", "mysql", "Replica status", "mysql -e 'show replica status'", _R, "mysql -h <host> -u <user> -p -e 'show replica status\\G'", perms="REPLICATION CLIENT")
_add("databases", "mysql", "Dump a database", "mysqldump", _R, "mysqldump --single-transaction --routines --triggers -h <host> -u <user> -p <db> | gzip > <db>-$(date +%F).sql.gz", perms="SELECT ;; LOCK TABLES ;; SHOW VIEW ;; TRIGGER", cats="backup")
_add("databases", "mysql", "Restore a dump", "mysql < dump.sql", _X, "gunzip -c <db>.sql.gz | mysql -h <host> -u <user> -p <db>", "mysql -e 'select count(*) from <db>.<table>'", "not reversible: take a mysqldump of the target first and restore it", perms="ALL on <db>", cats="data")
_add("databases", "redis", "Ping", "redis-cli ping", _R, "redis-cli -u \"$REDIS_URL\" ping")
_add("databases", "redis", "Latency", "redis-cli --latency", _R, "redis-cli -u \"$REDIS_URL\" --latency-history -i 5")
_add("databases", "redis", "Background save (backup)", "redis-cli bgsave", _C, "redis-cli -u \"$REDIS_URL\" bgsave", "redis-cli -u \"$REDIS_URL\" lastsave ;; redis-cli info persistence (rdb_last_bgsave_status:ok)", "not needed: only writes a new dump.rdb", perms="+bgsave ACL", cats="backup")
_add("databases", "redis", "Read a config value", "redis-cli config get", _R, "redis-cli -u \"$REDIS_URL\" config get maxmemory*", perms="+config|get ACL")
_add("databases", "redis", "Set a config value", "redis-cli config set", _C, "redis-cli -u \"$REDIS_URL\" config set maxmemory-policy allkeys-lru", "redis-cli -u \"$REDIS_URL\" config get maxmemory-policy", "redis-cli -u \"$REDIS_URL\" config set maxmemory-policy <previous>", perms="+config|set ACL")
_add("databases", "redis", "Flush all keys", "redis-cli flushall", _X, "redis-cli -u \"$REDIS_URL\" flushall", "redis-cli -u \"$REDIS_URL\" dbsize (0)", "not reversible: restore dump.rdb taken by bgsave before", perms="+flushall ACL", cats="data")
_add("databases", "mongodb", "Database stats", "mongosh --eval db.stats()", _R, "mongosh \"$MONGO_URL\" --quiet --eval 'db.stats()'", perms="dbStats action")
_add("databases", "mongodb", "Dump with mongodump", "mongodump", _R, "mongodump --uri \"$MONGO_URL\" --gzip --archive=<db>-$(date +%F).gz", perms="backup role", cats="backup")
_add("databases", "mongodb", "Restore with mongorestore", "mongorestore", _X, "mongorestore --uri \"$MONGO_URL\" --gzip --archive=<db>.gz --drop", "mongosh \"$MONGO_URL\" --quiet --eval 'db.<coll>.countDocuments()'", "not reversible with --drop: mongodump the target first and restore it", perms="restore role", cats="data")
_add("databases", "kafka", "Describe a topic", "kafka-topics --describe --topic", _R, "kafka-topics --bootstrap-server <broker> --describe --topic <topic>", perms="DESCRIBE Topic")
_add("databases", "kafka", "Create a topic", "kafka-topics --create", _C, "kafka-topics --bootstrap-server <broker> --create --topic <topic> --partitions 6 --replication-factor 3", "kafka-topics --bootstrap-server <broker> --describe --topic <topic>", "kafka-topics --bootstrap-server <broker> --delete --topic <topic>", perms="CREATE Cluster or Topic")
_add("databases", "kafka", "Delete a topic", "kafka-topics --delete", _X, "kafka-topics --bootstrap-server <broker> --delete --topic <topic>", "kafka-topics --bootstrap-server <broker> --list | grep -x <topic> (absent)", "not reversible: messages are lost; recreate the topic and replay from the source or MirrorMaker copy", perms="DELETE Topic", cats="data")
_add("databases", "kafka", "Consumer group lag", "kafka-consumer-groups --describe --group", _R, "kafka-consumer-groups --bootstrap-server <broker> --describe --group <group>", perms="DESCRIBE Group ;; DESCRIBE Topic")
_add("databases", "kafka", "Describe configs", "kafka-configs --describe --entity-type", _R, "kafka-configs --bootstrap-server <broker> --describe --entity-type topics --entity-name <topic>", perms="DESCRIBE_CONFIGS")
_add("databases", "kafka", "Alter configs", "kafka-configs --alter", _C, "kafka-configs --bootstrap-server <broker> --alter --entity-type topics --entity-name <topic> --add-config retention.ms=604800000", "kafka-configs --bootstrap-server <broker> --describe --entity-type topics --entity-name <topic>", "kafka-configs --bootstrap-server <broker> --alter --entity-type topics --entity-name <topic> --delete-config retention.ms", perms="ALTER_CONFIGS")

# Storage
_add("storage", "gcs", "List GCS buckets", "gcloud storage buckets list", _R, "gcloud storage buckets list --project <project>", perms="storage.buckets.list")
_add("storage", "gcs", "Describe a GCS bucket", "gcloud storage buckets describe", _R, "gcloud storage buckets describe gs://<bucket>", perms="storage.buckets.get")
_add("storage", "gcs", "List GCS objects", "gcloud storage ls", _R, "gcloud storage ls -l gs://<bucket>/<prefix>/", perms="storage.objects.list")
_add("storage", "gcs", "Copy to GCS", "gcloud storage cp", _C, "gcloud storage cp ./backup.tgz gs://<bucket>/backups/", "gcloud storage ls -l gs://<bucket>/backups/backup.tgz", "gcloud storage rm gs://<bucket>/backups/backup.tgz", perms="storage.objects.create")
_add("storage", "gcs", "Remove GCS objects", "gcloud storage rm", _X, "gcloud storage rm gs://<bucket>/<prefix>/**", "gcloud storage ls gs://<bucket>/<prefix>/", "with versioning on: gcloud storage cp gs://<bucket>/<object>#<generation> gs://<bucket>/<object>; otherwise not reversible", perms="storage.objects.delete", cats="data")
_add("storage", "gcs", "Enable GCS bucket versioning", "gcloud storage buckets update --versioning", _C, "gcloud storage buckets update gs://<bucket> --versioning", "gcloud storage buckets describe gs://<bucket> --format 'value(versioning_enabled)'", "gcloud storage buckets update gs://<bucket> --no-versioning", perms="storage.buckets.update", cats="data")
_add("storage", "minio", "Server info", "mc admin info", _R, "mc admin info <alias>", perms="admin:ServerInfo")
_add("storage", "minio", "Lifecycle rules", "mc ilm rule ls", _R, "mc ilm rule ls <alias>/<bucket>", perms="s3:GetLifecycleConfiguration")
_add("storage", "minio", "Enable versioning", "mc version enable", _C, "mc version enable <alias>/<bucket>", "mc version info <alias>/<bucket>", "mc version suspend <alias>/<bucket>", perms="s3:PutBucketVersioning")
_add("storage", "minio", "Remove a bucket", "mc rb", _X, "mc rb --force <alias>/<bucket>", "mc ls <alias> | grep <bucket> (absent)", "not reversible: mc mirror back from a replica or backup", perms="s3:DeleteBucket", cats="data")
_add("storage", "minio", "Add a user", "mc admin user add", _X, "mc admin user add <alias> <access-key> <secret-key>", "mc admin user info <alias> <access-key>", "mc admin user remove <alias> <access-key>", perms="admin:CreateUser", cats="iam")
_add("storage", "ceph", "Health detail", "ceph health detail", _R, "ceph health detail")
_add("storage", "ceph", "OSD tree", "ceph osd tree", _R, "ceph osd tree")
_add("storage", "ceph", "Usage", "ceph df", _R, "ceph df detail")
_add("storage", "ceph", "List RBD images", "rbd ls", _R, "rbd ls -l <pool>")
_add("storage", "ceph", "Snapshot an RBD image", "rbd snap create", _C, "rbd snap create <pool>/<image>@pre-change", "rbd snap ls <pool>/<image>", "rbd snap rm <pool>/<image>@pre-change", cats="backup")
_add("storage", "ceph", "Remove an RBD image", "rbd rm", _X, "rbd rm <pool>/<image>", "rbd ls <pool> | grep <image> (absent)", "with rbd trash: use rbd trash mv then rbd trash restore; after rbd rm not reversible", cats="data")
_add("storage", "ceph", "Mark an OSD out", "ceph osd out", _X, "ceph osd out <id>", "ceph -s (rebalancing) ;; ceph osd tree", "ceph osd in <id>", cats="availability")
_add("storage", "nfs", "Exported filesystems", "exportfs -v", _R, "exportfs -v", perms="root")
_add("storage", "nfs", "Re-export NFS shares", "exportfs -ra", _C, "exportfs -ra", "exportfs -v ;; showmount -e localhost", "restore /etc/exports from backup then exportfs -ra", perms="root")
_add("storage", "nfs", "NFS statistics", "nfsstat", _R, "nfsstat -c")
_add("storage", "nfs", "Mount an NFS share", "mount -t nfs", _C, "mount -t nfs4 -o rw,hard,noatime <server>:/<export> /mnt/<dir>", "findmnt /mnt/<dir>", "umount /mnt/<dir>", perms="root")

# Servers
_add("linux", "system", "Uptime and load", "uptime", _R, "uptime")
_add("linux", "system", "Disk usage", "df -h", _R, "df -hT -x tmpfs")
_add("linux", "system", "Memory usage", "free -m", _R, "free -m")
_add("linux", "system", "Top processes", "ps aux --sort=-%cpu", _R, "ps aux --sort=-%cpu | head -15")
_add("linux", "system", "Kernel messages", "dmesg", _R, "dmesg -T --level=err,warn | tail -50", perms="root (kernel.dmesg_restrict)")
_add("linux", "packages", "List available updates (apt)", "apt list --upgradable", _R, "apt list --upgradable")
_add("linux", "packages", "List available updates (dnf)", "dnf check-update", _R, "dnf check-update --security")
_add("linux", "packages", "Upgrade packages", "apt-get upgrade", _C, "apt-get update && apt-get -y upgrade", "apt list --upgradable (empty) ;; systemctl --failed", "apt-get install <pkg>=<previous-version> (dnf history undo <id> on RHEL)", perms="root")
_add("linux", "system", "Reboot", "systemctl reboot", _X, "systemctl reboot", "ssh <host> uptime (small uptime) ;; systemctl --failed", "not reversible (a reboot)", perms="root", cats="availability")
_add("linux", "system", "Host name", "hostnamectl", _R, "hostnamectl")
_add("linux", "system", "Time and NTP", "timedatectl", _R, "timedatectl show-timesync --all")
_add("linux", "system", "Kernel parameters", "sysctl -a", _R, "sysctl -a 2>/dev/null | grep <key>")
_add("linux", "system", "Set a kernel parameter", "sysctl -w", _C, "sysctl -w net.core.somaxconn=4096", "sysctl net.core.somaxconn", "sysctl -w net.core.somaxconn=<previous>", perms="root")
_add("linux", "systemd", "Show a unit file", "systemctl cat", _R, "systemctl cat <unit>")
_add("linux", "systemd", "Reload unit files", "systemctl daemon-reload", _C, "systemctl daemon-reload", "systemctl show <unit> -p NeedDaemonReload (no)", "restore the previous unit file then daemon-reload", perms="root")
_add("linux", "systemd", "Enable and start a unit", "systemctl enable --now", _C, "systemctl enable --now <unit>", "systemctl is-enabled <unit> ;; systemctl is-active <unit>", "systemctl disable --now <unit>", perms="root")
_add("linux", "systemd", "Stop a service", "systemctl stop", _X, "systemctl stop <unit>", "systemctl is-active <unit> (inactive)", "systemctl start <unit>", perms="root", cats="availability")
_add("linux", "journald", "Errors of a unit", "journalctl -u -p err", _R, "journalctl -u <unit> -p err --since today --no-pager")
_add("windows", "events", "Event log", "Get-EventLog", _R, "pwsh -Command \"Get-EventLog -LogName System -EntryType Error -Newest 50\"")
_add("windows", "events", "Windows events", "Get-WinEvent", _R, "pwsh -Command \"Get-WinEvent -FilterHashtable @{LogName='Application';Level=2} -MaxEvents 50\"")
_add("windows", "updates", "Installed hotfixes", "Get-HotFix", _R, "pwsh -Command \"Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10\"")
_add("windows", "features", "Windows features", "Get-WindowsFeature", _R, "pwsh -Command \"Get-WindowsFeature | Where-Object Installed\"")
_add("windows", "features", "Install a Windows feature", "Install-WindowsFeature", _C, "pwsh -Command \"Install-WindowsFeature -Name Web-Server -IncludeManagementTools\"", "Get-WindowsFeature Web-Server (Installed)", "Uninstall-WindowsFeature -Name Web-Server", perms="Administrator")
_add("windows", "services", "Stop a Windows service", "Stop-Service", _X, "pwsh -Command \"Stop-Service -Name <name>\"", "Get-Service <name> (Stopped)", "Start-Service -Name <name>", perms="Administrator", cats="availability")
_add("windows", "remoting", "Run a remote command", "Invoke-Command -ComputerName", _C, "pwsh -Command \"Invoke-Command -ComputerName <host> -ScriptBlock { Get-Service W3SVC }\"", "check the returned output and $LASTEXITCODE", "run the inverse script block (no automatic undo)", perms="Remote Management Users or Administrator", cats="remote-exec")
_add("windows", "services", "Query a service with sc.exe", "sc.exe query", _R, "sc.exe query <name>")
_add("windows", "services", "Stop a service with sc.exe", "sc.exe stop", _X, "sc.exe stop <name>", "sc.exe query <name> (STOPPED)", "sc.exe start <name>", perms="Administrator", cats="availability")
_add("windows", "system", "Restart the computer", "Restart-Computer", _X, "pwsh -Command \"Restart-Computer -ComputerName <host> -Wait -For PowerShell -Timeout 600\"", "Test-Connection <host> ;; (Get-CimInstance Win32_OperatingSystem).LastBootUpTime", "not reversible (a reboot)", perms="Administrator", cats="availability")
_add("webservers", "nginx", "Dump the full configuration", "nginx -T", _R, "nginx -T 2>/dev/null | less", perms="root")
_add("webservers", "nginx", "Tail the access log", "tail -f /var/log/nginx/access.log", _R, "tail -n 200 /var/log/nginx/access.log", perms="adm group")
_add("webservers", "traefik", "Raw configuration", "curl $TRAEFIK/api/rawdata", _R, "curl -fsS $TRAEFIK/api/rawdata | jq '.routers | keys'", endpoint="GET /api/rawdata")
_add("webservers", "traefik", "HTTP routers", "curl $TRAEFIK/api/http/routers", _R, "curl -fsS $TRAEFIK/api/http/routers | jq '.[] | {name, status, rule}'", endpoint="GET /api/http/routers")
_add("webservers", "traefik", "IngressRoutes", "kubectl get ingressroutes -A", _R, "kubectl get ingressroutes.traefik.io -A")
_add("webservers", "traefik", "Apply an IngressRoute", "kubectl apply -f ingressroute.yaml", _C, "kubectl apply -f ingressroute.yaml -n <ns>", "curl -fsS $TRAEFIK/api/http/routers/<ns>-<name>@kubernetescrd ;; curl -fsSI https://<host>/", "kubectl apply -f previous-ingressroute.yaml -n <ns> ;; or kubectl delete -f ingressroute.yaml", perms="patch ingressroutes.traefik.io")
_add("webservers", "haproxy", "Reload HAProxy", "systemctl reload haproxy", _C, "haproxy -c -f /etc/haproxy/haproxy.cfg && systemctl reload haproxy", "systemctl is-active haproxy ;; echo 'show info' | socat stdio /run/haproxy/admin.sock", "restore the previous haproxy.cfg, haproxy -c, systemctl reload haproxy", perms="root")
_add("webservers", "haproxy", "Disable a load balancer server", "echo 'disable server' | socat stdio /run/haproxy/admin.sock", _X, "echo 'disable server <backend>/<server>' | socat stdio /run/haproxy/admin.sock", "echo 'show servers state <backend>' | socat stdio /run/haproxy/admin.sock", "echo 'enable server <backend>/<server>' | socat stdio /run/haproxy/admin.sock", perms="admin level on the stats socket", cats="availability")

# Tests
_add("testing", "network", "Path report", "mtr --report", _R, "mtr --report --report-cycles 20 <host>")
_add("testing", "network", "Traceroute", "traceroute", _R, "traceroute -T -p 443 <host>")
_add("testing", "network", "DNS resolution test", "dig +short", _R, "dig +short <name> @<resolver>")
_add("testing", "network", "Inject latency (network chaos)", "tc qdisc add dev", _X, "tc qdisc add dev <iface> root netem delay 200ms", "tc qdisc show dev <iface> ;; ping -c5 <peer>", "tc qdisc del dev <iface> root", perms="root / CAP_NET_ADMIN", cats="availability")
_add("testing", "api", "API status and timing", "curl -fsS -o /dev/null -w", _R, "curl -fsS -o /dev/null -w '%{http_code} %{time_total}s\\n' https://<host>/api/health")
_add("testing", "api", "JSON API assertion", "curl -fsS | jq", _R, "curl -fsS https://<host>/api/v1/status | jq -e '.status == \"ok\"'")
_add("testing", "api", "API write smoke test", "curl -fsS -X POST", _C, "curl -fsS -X POST -H 'Content-Type: application/json' -d '{\"name\":\"navin-smoke\"}' https://<host>/api/v1/items", "curl -fsS https://<host>/api/v1/items?name=navin-smoke | jq length", "curl -fsS -X DELETE https://<host>/api/v1/items/<id>")
_add("testing", "performance", "Request timing breakdown", "curl -w timings", _R, "curl -o /dev/null -s -w 'dns %{time_namelookup} connect %{time_connect} tls %{time_appconnect} ttfb %{time_starttransfer} total %{time_total}\\n' https://<host>/")
_add("testing", "performance", "Latency benchmark", "hey -n", _C, "hey -n 2000 -c 50 https://<host>/", "read the latency distribution and the status code distribution", "stop the run (Ctrl-C); no persistent change", perms="approval of the target owner", cats="load")
_add("testing", "load", "Constant rate attack", "vegeta attack", _C, "echo 'GET https://<host>/' | vegeta attack -rate 100 -duration 60s | vegeta report", "vegeta report shows success ratio 100% and p99 within the SLO", "stop the run (Ctrl-C); no persistent change", perms="approval of the target owner", cats="load")
_add("testing", "load", "ApacheBench", "ab -n", _C, "ab -n 5000 -c 50 https://<host>/", "Failed requests: 0 in the ab report", "stop the run (Ctrl-C); no persistent change", perms="approval of the target owner", cats="load")
_add("testing", "load", "k6 dry run (inspect)", "k6 inspect", _P, "k6 inspect load.js")
_add("testing", "security", "Image vulnerability test", "trivy image --exit-code 1", _R, "trivy image --exit-code 1 --severity CRITICAL <image>")
_add("testing", "security", "Service scan of own hosts", "nmap -sV", _C, "nmap -sV -p 1-1024 <own-host>", "compare the open ports against the expected list", "stop the scan; no persistent change (notify the SOC before scanning)", perms="written authorization for the target", cats="network")
_add("testing", "security", "TLS configuration test", "testssl.sh", _R, "testssl.sh --severity HIGH https://<host>")
_add("testing", "security", "OWASP ZAP baseline", "docker run zaproxy/zap-stable zap-baseline.py", _C, "docker run --rm -t zaproxy/zap-stable zap-baseline.py -t https://<host>", "exit code 0 (no FAIL alerts)", "stop the container; a baseline scan is passive, no persistent change", perms="written authorization for the target")
_add("testing", "availability", "Blackbox probe loop", "while curl -fsS", _R, "while true; do curl -fsS -o /dev/null -w '%{http_code} %{time_total}\\n' https://<host>/healthz || echo DOWN; sleep 5; done")
_add("testing", "availability", "Watch pods during a change", "kubectl get pods --watch", _R, "kubectl get pods -n <ns> -l app=<app> --watch")
_add("testing", "availability", "Synthetic check (blackbox exporter)", "curl $BLACKBOX/probe", _R, "curl -fsS \"$BLACKBOX/probe?target=https://<host>&module=http_2xx\" | grep probe_success")
_add("testing", "availability", "Kill one replica (chaos)", "kubectl delete pod", _X, "kubectl delete pod <one-pod-of-the-deployment> -n <ns>", "kubectl rollout status deployment/<app> -n <ns> ;; kubectl get pods -n <ns> -l app=<app>", "the ReplicaSet recreates the pod; if not, kubectl rollout restart deployment/<app> -n <ns>", perms="delete pods", cats="availability")
_add("testing", "recovery", "Velero backup", "velero backup create", _C, "velero backup create <name> --include-namespaces <ns> --wait", "velero backup describe <name> --details (Phase: Completed)", "velero backup delete <name> --confirm", perms="create backups.velero.io", cats="backup")
_add("testing", "recovery", "Velero backups", "velero backup get", _R, "velero backup get")
_add("testing", "recovery", "Velero restore drill", "velero restore create", _X, "velero restore create <restore> --from-backup <name> --namespace-mappings <ns>:<ns>-drill --wait", "velero restore describe <restore> --details (Phase: Completed) ;; kubectl get all -n <ns>-drill", "kubectl delete namespace <ns>-drill", perms="create restores.velero.io", cats="data")
_add("testing", "recovery", "RDS point-in-time restore drill", "aws rds restore-db-instance-to-point-in-time --target-db-instance-identifier", _X, "aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier <db> --target-db-instance-identifier <db>-drill --restore-time <iso8601>", "aws rds wait db-instance-available --db-instance-identifier <db>-drill", "aws rds delete-db-instance --db-instance-identifier <db>-drill --skip-final-snapshot", perms="rds:RestoreDBInstanceToPointInTime", cats="costly,data")
_add("testing", "recovery", "Drift check after restore", "terraform plan -refresh-only -detailed-exitcode", _P, "terraform plan -refresh-only -detailed-exitcode")

# FinOps
_add("finops", "inventory", "AWS tagged resources inventory", "aws resourcegroupstaggingapi get-resources", _R, "aws resourcegroupstaggingapi get-resources --tag-filters Key=team --query 'ResourceTagMappingList[].ResourceARN'", perms="tag:GetResources")
_add("finops", "inventory", "Tag resources for cost allocation", "aws resourcegroupstaggingapi tag-resources", _C, "aws resourcegroupstaggingapi tag-resources --resource-arn-list <arn> --tags team=<team>,cost-center=<cc>", "aws resourcegroupstaggingapi get-resources --resource-arn-list <arn>", "aws resourcegroupstaggingapi untag-resources --resource-arn-list <arn> --tag-keys team cost-center", perms="tag:TagResources")
_add("finops", "inventory", "GCP asset inventory", "gcloud asset search-all-resources", _R, "gcloud asset search-all-resources --scope projects/<project> --format 'table(assetType,name)'", perms="cloudasset.assets.searchAllResources")
_add("finops", "inventory", "Azure resource inventory", "az resource list", _R, "az resource list --query '[].{name:name,type:type,rg:resourceGroup}' -o table", perms="Microsoft.Resources/subscriptions/resources/read")
_add("finops", "cost", "AWS cost by service", "aws ce get-cost-and-usage --group-by SERVICE", _R, "aws ce get-cost-and-usage --time-period Start=<yyyy-mm-01>,End=<yyyy-mm-dd> --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE", perms="ce:GetCostAndUsage")
_add("finops", "cost-allocation", "AWS cost by tag", "aws ce get-cost-and-usage --group-by TAG", _R, "aws ce get-cost-and-usage --time-period Start=<start>,End=<end> --granularity MONTHLY --metrics UnblendedCost --group-by Type=TAG,Key=team", perms="ce:GetCostAndUsage")
_add("finops", "cost", "AWS cost forecast", "aws ce get-cost-forecast", _R, "aws ce get-cost-forecast --time-period Start=<today>,End=<month-end> --metric UNBLENDED_COST --granularity MONTHLY", perms="ce:GetCostForecast")
_add("finops", "cost", "GCP billing export query", "bq query", _R, "bq query --use_legacy_sql=false 'select service.description, sum(cost) c from `<project>.<dataset>.gcp_billing_export_v1_*` where invoice.month=\"<yyyymm>\" group by 1 order by c desc'", perms="bigquery.jobs.create ;; bigquery.tables.getData")
_add("finops", "cost", "GCP billing accounts", "gcloud billing accounts list", _R, "gcloud billing accounts list", perms="billing.accounts.list")
_add("finops", "cost", "Azure cost analysis", "az costmanagement query --type ActualCost", _R, "az costmanagement query --type ActualCost --timeframe MonthToDate --scope /subscriptions/<sub> --dataset-grouping name=ResourceGroup type=Dimension", perms="Microsoft.CostManagement/query/action")
_add("finops", "cost", "Activate a cost allocation tag", "aws ce update-cost-allocation-tags-status", _C, "aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=team,Status=Active", "aws ce list-cost-allocation-tags --tag-keys team", "aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=team,Status=Inactive", perms="ce:UpdateCostAllocationTagsStatus")
_add("finops", "rightsizing", "AWS EC2 rightsizing", "aws compute-optimizer get-ec2-instance-recommendations", _R, "aws compute-optimizer get-ec2-instance-recommendations --filters name=Finding,values=Overprovisioned", perms="compute-optimizer:GetEC2InstanceRecommendations")
_add("finops", "rightsizing", "AWS EBS rightsizing", "aws compute-optimizer get-ebs-volume-recommendations", _R, "aws compute-optimizer get-ebs-volume-recommendations", perms="compute-optimizer:GetEBSVolumeRecommendations")
_add("finops", "rightsizing", "AWS Lambda rightsizing", "aws compute-optimizer get-lambda-function-recommendations", _R, "aws compute-optimizer get-lambda-function-recommendations", perms="compute-optimizer:GetLambdaFunctionRecommendations")
_add("finops", "rightsizing", "GCP machine type rightsizing", "gcloud recommender recommendations list --recommender=google.compute.instance.MachineTypeRecommender", _R, "gcloud recommender recommendations list --project <project> --location <zone> --recommender google.compute.instance.MachineTypeRecommender", perms="recommender.computeInstanceMachineTypeRecommendations.list")
_add("finops", "rightsizing", "Azure cost recommendations", "az advisor recommendation list --category Cost", _R, "az advisor recommendation list --category Cost -o table", perms="Microsoft.Advisor/recommendations/read")
_add("finops", "rightsizing", "Kubernetes requests vs usage", "kubectl top pods --containers", _R, "kubectl top pods -A --containers ;; kubectl get pods -A -o custom-columns=NS:.metadata.namespace,POD:.metadata.name,CPU:.spec.containers[*].resources.requests.cpu,MEM:.spec.containers[*].resources.requests.memory", perms="get pods.metrics.k8s.io ;; list pods")
_add("finops", "rightsizing", "Apply Kubernetes rightsized requests", "kubectl set resources", _C, "kubectl set resources deployment/<app> -n <ns> --requests=cpu=250m,memory=256Mi --limits=memory=512Mi", "kubectl rollout status deployment/<app> -n <ns> ;; kubectl top pods -n <ns> -l app=<app>", "kubectl rollout undo deployment/<app> -n <ns>", perms="patch deployments")
_add("finops", "rightsizing", "Resize an EC2 instance", "aws ec2 modify-instance-attribute --instance-type", _X, "aws ec2 stop-instances --instance-ids <id> && aws ec2 wait instance-stopped --instance-ids <id> && aws ec2 modify-instance-attribute --instance-id <id> --instance-type Value=<type> && aws ec2 start-instances --instance-ids <id>", "aws ec2 describe-instances --instance-ids <id> --query 'Reservations[].Instances[].[InstanceType,State.Name]'", "repeat with --instance-type Value=<previous-type>", perms="ec2:ModifyInstanceAttribute ;; ec2:StopInstances ;; ec2:StartInstances", cats="availability")
_add("finops", "budgets", "AWS budgets", "aws budgets describe-budgets", _R, "aws budgets describe-budgets --account-id <account>", perms="budgets:ViewBudget")
_add("finops", "budgets", "Create an AWS budget", "aws budgets create-budget", _C, "aws budgets create-budget --account-id <account> --budget file://budget.json --notifications-with-subscribers file://notifications.json", "aws budgets describe-budget --account-id <account> --budget-name <name>", "aws budgets delete-budget --account-id <account> --budget-name <name>", perms="budgets:ModifyBudget")
_add("finops", "budgets", "GCP budgets", "gcloud billing budgets list", _R, "gcloud billing budgets list --billing-account <billing-account>", perms="billing.budgets.list")
_add("finops", "budgets", "Create a GCP budget", "gcloud billing budgets create", _C, "gcloud billing budgets create --billing-account <billing-account> --display-name <name> --budget-amount 1000EUR --threshold-rule percent=0.8", "gcloud billing budgets list --billing-account <billing-account>", "gcloud billing budgets delete <budget-id> --billing-account <billing-account>", perms="billing.budgets.create")
_add("finops", "budgets", "Azure budgets", "az consumption budget list", _R, "az consumption budget list -o table", perms="Microsoft.Consumption/budgets/read")
_add("finops", "budgets", "Create an Azure budget", "az consumption budget create", _C, "az consumption budget create --budget-name <name> --amount 1000 --category cost --time-grain monthly --start-date <yyyy-mm-01> --end-date <yyyy-mm-01>", "az consumption budget show --budget-name <name>", "az consumption budget delete --budget-name <name>", perms="Microsoft.Consumption/budgets/write")
_add("finops", "optimisation", "Savings Plans recommendation", "aws ce get-savings-plans-purchase-recommendation", _R, "aws ce get-savings-plans-purchase-recommendation --savings-plans-type COMPUTE_SP --term-in-years ONE_YEAR --payment-option NO_UPFRONT --lookback-period-in-days THIRTY_DAYS", perms="ce:GetSavingsPlansPurchaseRecommendation")
_add("finops", "optimisation", "Reserved instances recommendation", "aws ce get-reservation-purchase-recommendation", _R, "aws ce get-reservation-purchase-recommendation --service 'Amazon Relational Database Service'", perms="ce:GetReservationPurchaseRecommendation")
_add("finops", "optimisation", "Unattached EBS volumes", "aws ec2 describe-volumes --filters Name=status,Values=available", _R, "aws ec2 describe-volumes --filters Name=status,Values=available --query 'Volumes[].[VolumeId,Size,CreateTime]'", perms="ec2:DescribeVolumes")
_add("finops", "optimisation", "Delete an unattached EBS volume", "aws ec2 delete-volume --volume-id", _X, "aws ec2 create-snapshot --volume-id <vol> --description pre-delete && aws ec2 delete-volume --volume-id <vol>", "aws ec2 describe-volumes --volume-ids <vol> (InvalidVolume.NotFound)", "aws ec2 create-volume --snapshot-id <pre-delete-snap> --availability-zone <az>", perms="ec2:DeleteVolume ;; ec2:CreateSnapshot", cats="data")
_add("finops", "optimisation", "Idle load balancers", "aws cloudwatch get-metric-statistics --metric-name RequestCount", _R, "aws cloudwatch get-metric-statistics --namespace AWS/ApplicationELB --metric-name RequestCount --dimensions Name=LoadBalancer,Value=<app/name/id> --start-time <14-days-ago> --end-time <now> --period 86400 --statistics Sum", perms="cloudwatch:GetMetricStatistics")
_add("finops", "optimisation", "Old snapshots", "aws ec2 describe-snapshots --owner-ids self", _R, "aws ec2 describe-snapshots --owner-ids self --query 'Snapshots[?StartTime<=`<yyyy-mm-dd>`].[SnapshotId,VolumeSize,StartTime]'", perms="ec2:DescribeSnapshots")
_add("finops", "optimisation", "Infracost diff", "infracost diff", _P, "infracost diff --path . --compare-to infracost-base.json")
_add("finops", "optimisation", "Kubecost allocation API", "curl $KUBECOST/model/allocation", _R, "curl -fsS \"$KUBECOST/model/allocation?window=7d&aggregate=namespace\"", endpoint="GET /model/allocation")
