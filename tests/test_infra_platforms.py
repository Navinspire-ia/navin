# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Conformance of the infrastructure classifier on every platform the user named.

Each row is a real command and the risk the Policy & Approval Engine must
give it at the ``execute`` level on a non-production context: ``read`` and
``plan`` run freely, ``change`` runs inside the scope, ``critical`` always asks.
"""

from __future__ import annotations

import pytest

from navin.infra.model import PROVIDER_BINARIES
from navin.infra.parse import (
    _UNGATED,
    classify_command,
    classify_segment,
    mentions_infra,
    parse_segment,
    tokenize,
    worst,
)
from navin.infra.policy import InfraPolicy, decide
from navin.infra.verify import verification_plan

DEV = {"kubernetes": {"current": "dev", "contexts": []}, "aws": {"profile": "dev"}, "gcp": {"project": "dev"},
       "terraform": {"workspace": ""}, "cwd": "."}

R, P, C, X = "read", "plan", "change", "critical"

MATRIX: dict[str, list[tuple[str, str]]] = {
    "aws": [
        ("aws ec2 describe-instances", R), ("aws sts get-caller-identity", R), ("aws ce get-cost-and-usage --granularity MONTHLY", R),
        ("aws ec2 run-instances --image-id ami-1 --dry-run", P), ("aws cloudformation validate-template --template-body file://t.yaml", P),
        ("aws ec2 create-tags --resources i-1 --tags Key=a,Value=b", C), ("aws lambda update-function-code --function-name f --zip-file fileb://f.zip", C),
        ("aws ecs update-service --cluster c --service s --force-new-deployment", C), ("aws ssm send-command --document-name AWS-RunShellScript", C),
        ("aws ec2 stop-instances --instance-ids i-1", X), ("aws ec2 reboot-instances --instance-ids i-1", X),
        ("aws ec2 terminate-instances --instance-ids i-1", X), ("aws ec2 run-instances --image-id ami-1", X),
        ("aws eks update-cluster-version --name x --kubernetes-version 1.31", X), ("aws eks create-cluster --name x", X),
        ("aws rds modify-db-instance --db-instance-identifier db --apply-immediately", X), ("aws rds delete-db-instance --db-instance-identifier db", X),
        ("aws s3api put-bucket-policy --bucket b --policy file://p.json", X), ("aws s3 rm s3://b --recursive", X), ("aws s3 sync . s3://b --delete", X),
        ("aws iam attach-role-policy --role-name r --policy-arn arn", X), ("aws kms schedule-key-deletion --key-id k", X),
        ("aws route53 change-resource-record-sets --hosted-zone-id z --change-batch file://c.json", X),
        ("aws ec2 authorize-security-group-ingress --group-id sg-1 --port 22 --cidr 0.0.0.0/0", X),
        ("aws ec2 create-nat-gateway --subnet-id s", X), ("aws ec2 create-route --route-table-id r --destination-cidr-block 0.0.0.0/0", X),
        ("aws secretsmanager get-secret-value --secret-id s", R), ("aws logs stop-query --query-id q", C),
        ("aws eks update-kubeconfig --name x", C), ("aws configure sso", C),
    ],
    "gcp": [
        ("gcloud compute instances list", R), ("gcloud asset search-all-resources --scope=projects/p", R), ("gcloud logging read severity>=ERROR", R),
        ("gcloud compute backend-services get-health be --global", R), ("gcloud storage ls gs://b", R), ("gcloud config get-value project", R),
        ("gcloud run deploy svc --image img --region r", C), ("gcloud compute instances add-labels vm --labels a=b", C),
        ("gcloud container clusters get-credentials c --region r", C), ("gcloud deployment-manager deployments create d --config c.yaml --preview", P),
        ("gcloud compute instances create vm1 --zone z", X), ("gcloud compute instances stop vm1 --zone z", X),
        ("gcloud container clusters upgrade c1 --master", X), ("gcloud sql instances restart db1", X), ("gcloud sql instances patch db1 --tier db-custom-2-7680", X),
        ("gcloud storage rm gs://b/x", X), ("gsutil rm gs://b/x", X), ("gcloud compute firewall-rules create allow-ssh --allow tcp:22", X),
        ("gcloud dns record-sets update www --type A --zone z --rrdatas 1.2.3.4", X), ("gcloud projects add-iam-policy-binding p --member u --role roles/owner", X),
        ("gcloud iam service-accounts keys create k.json --iam-account sa", X), ("bq rm -r -f dataset", X), ("bq query 'select 1'", R),
        ("gcloud container clusters create-auto c1 --region r", X), ("gcloud compute routers nats create nat1 --router r --region x", X),
        ("gcloud compute backend-services remove-backend be --instance-group ig", X),
    ],
    "azure": [
        ("az vm list -o table", R), ("az monitor log-analytics query -w x --analytics-query q", R), ("az graph query -q Resources", R),
        ("az deployment group what-if -g rg --template-file main.bicep", P), ("az aks get-credentials -g rg -n c", C),
        ("az webapp config appsettings set -g rg -n app --settings A=B", C), ("az deployment group create -g rg --template-file main.bicep", C),
        ("az vm create -g rg -n vm --image Ubuntu2204", X), ("az vm restart -g rg -n vm", X), ("az vm stop -g rg -n vm", X),
        ("az aks upgrade -g rg -n c --kubernetes-version 1.31", X), ("az group delete -n rg", X),
        ("az network nsg rule create -g rg --nsg-name n -n r --priority 100", X), ("az role assignment create --assignee x --role Owner", X),
        ("az keyvault secret show --vault-name v -n s", R), ("az storage blob delete --account-name a -c c -n b", X),
        ("az postgres flexible-server restart -g rg -n db", X),
    ],
    "private cloud": [
        ("openstack server list", R), ("openstack server create --flavor f --image i vm1", X), ("openstack server reboot vm1", X),
        ("openstack server delete vm1", X), ("openstack volume delete v1", X), ("openstack security group rule create sg --dst-port 22", X),
        ("openstack stack create -t heat.yaml app", C), ("openstack quota show", R),
        ("govc vm.info vm1", R), ("govc find / -type m", R), ("govc vm.power -on vm1", C), ("govc vm.power -off vm1", X),
        ("govc snapshot.create -vm vm1 before", C), ("govc snapshot.revert -vm vm1 before", X), ("govc vm.destroy vm1", X),
        ("qm list", R), ("qm start 100", C), ("qm shutdown 100", X), ("qm reboot 100", X), ("qm snapshot 100 before", C),
        ("qm rollback 100 before", X), ("qm destroy 100", X), ("pct exec 101 -- uptime", C), ("pvesh get /cluster/resources", R),
        ("pvesh delete /nodes/pve/qemu/100", X), ("vzdump 100 --storage backup", C), ("qmrestore /backup/vzdump-100.vma 100", X),
    ],
    "containers": [
        ("docker ps -a", R), ("docker build -t app .", C), ("docker run -d --name web nginx", C), ("docker exec web ls", C),
        ("docker rm -f web", X), ("docker system prune -af", X), ("docker volume rm data", X), ("docker compose up -d", C),
        ("docker compose down", C), ("docker compose down -v", X), ("docker-compose config", R),
        ("podman ps", R), ("podman generate kube web", P), ("podman kube play pod.yaml", C), ("podman pod rm -f web", X),
        ("nerdctl --namespace k8s.io ps -a", R), ("ctr -n k8s.io images ls", R), ("ctr images rm docker.io/x", X),
        ("crictl ps", R), ("crictl rmi abc", X), ("crictl stopp pod1", X),
    ],
    "orchestration": [
        ("kubectl get pods -A", R), ("kubectl api-resources -o wide", R), ("kubectl auth can-i --list", R), ("kubectl cost namespace --window 7d", R),
        ("kubectl diff -f k8s/", P), ("kubectl apply -f k8s/ --dry-run=server", P), ("kubectl apply -f k8s/", C),
        ("kubectl rollout restart deploy/web", C), ("kubectl scale deploy/web --replicas=3", C), ("kubectl set image deploy/web web=img:2", C),
        ("kubectl delete deploy/web", X), ("kubectl drain node1 --ignore-daemonsets", X), ("kubectl create rolebinding rb --clusterrole=admin --user=u", X),
        ("kubectl create secret generic db --from-literal=p=x", X), ("kubectl apply -f netpol.yaml", X), ("kubectl get secret db -o yaml", R),
        ("kubectl view-secret db", R), ("eksctl get cluster", R), ("eksctl create cluster -f cluster.yaml", X), ("eksctl delete cluster --name x", X),
        ("oc get projects", R), ("oc adm release info", R), ("oc adm top nodes", R), ("oc new-app nginx", C),
        ("oc adm policy add-role-to-user admin u", X), ("oc adm upgrade --to=4.16.3", X),
        ("k3s kubectl get nodes", R), ("k3s check-config", R), ("k3s etcd-snapshot save", C), ("k3s etcd-snapshot ls", R),
        ("rke2 etcd-snapshot save --name before", C), ("microk8s status", R), ("microk8s enable dns", C), ("microk8s kubectl delete pod x", X),
        ("microk8s reset", X), ("kind delete cluster", X), ("velero backup get", R), ("velero backup create before-upgrade", C),
        ("velero restore create --from-backup b1", X),
        ("eksctl upgrade cluster --name x --version 1.31", P), ("eksctl upgrade cluster --name x --version 1.31 --approve", X),
        ("kubectl apply -f rbac/rolebinding.yaml", X),
    ],
    "iac": [
        ("terraform init", P), ("terraform plan -out=tfplan", P), ("terraform validate", R), ("terraform apply tfplan", C),
        ("terraform apply -auto-approve", C), ("terraform destroy", X), ("terraform state rm aws_instance.x", X), ("terraform import aws_s3_bucket.b b", C),
        ("tofu plan -out=tfplan", P), ("tofu apply tfplan", C), ("pulumi preview", P), ("pulumi up --yes", C), ("pulumi destroy", X),
        ("pulumi stack ls", R), ("aws cloudformation create-change-set --stack-name s --change-set-name c", P),
        ("aws cloudformation execute-change-set --stack-name s --change-set-name c", C), ("aws cloudformation delete-stack --stack-name s", X),
        ("aws cloudformation deploy --template-file t.yaml --stack-name s --no-execute-changeset", P),
        ("cdk synth", P), ("cdk diff", P), ("cdk deploy", C), ("cdk destroy", X),
    ],
    "configuration": [
        ("ansible all -m ping", R), ("ansible-playbook site.yml --syntax-check", R), ("ansible-playbook site.yml --check --diff", P),
        ("ansible-playbook site.yml --limit web", C), ("ansible all -m shell -a uptime", C), ("ansible-inventory --graph", R),
        ("ansible-vault view secrets.yml", R), ("ssh web-1 uptime", R), ("ssh web-1 'sudo systemctl restart nginx'", C),
        ("ssh web-1 'sudo systemctl stop nginx'", X), ("ssh web-1 'cd /srv && rm -rf releases/old'", C), ("ssh web-1 'sudo reboot'", X),
        ("scp app.tar web-1:/srv/", C), ("rsync -av --dry-run ./dist/ web-1:/srv/", P), ("rsync -av --delete ./dist/ web-1:/srv/", X),
        ("cloud-init status --long", R), ("cloud-init schema --config-file user-data.yaml", P), ("cloud-init clean --reboot", X),
        ("helm list -A", R), ("helm template app ./chart", P), ("helm upgrade --install app ./chart --dry-run", P),
        ("helm upgrade --install app ./chart", C), ("helm rollback app 3", C), ("helm uninstall app", X),
        ("kustomize build overlays/prod", P), ("kubectl kustomize overlays/prod", R), ("kubectl apply -k overlays/dev", C),
    ],
    "ci/cd": [
        ("gh run list", R), ("gh workflow run deploy.yml -f env=staging", C), ("gh run rerun 123 --failed", C), ("gh secret set TOKEN", X),
        ("gh api -X DELETE repos/o/r/actions/runs/1", X), ("glab ci list", R), ("glab ci lint .gitlab-ci.yml", R), ("glab ci run -b main", C),
        ("glab variable set TOKEN x", X), ("java -jar jenkins-cli.jar -s http://j -auth u:t build deploy", C),
        ("java -jar jenkins-cli.jar -s http://j -auth u:t delete-job old", X), ("tea repos list", R), ("tea actions secrets create X", X),
        ("argocd app list", R), ("argocd app diff shop", R), ("argocd app sync shop", C), ("argocd app rollback shop 4", C),
        ("argocd app delete shop", X), ("argocd cluster add prod-ctx", X), ("flux get all -A", R), ("flux reconcile kustomization apps", C),
        ("flux suspend kustomization apps", C), ("flux delete kustomization apps", X), ("flux bootstrap github --owner x", X),
    ],
    "network": [
        ("ip -br addr", R), ("ip route add 10.0.0.0/8 via 10.1.1.1", X), ("ss -tulpn", R), ("nft list ruleset", R),
        ("nft add rule inet filter input tcp dport 22 accept", X), ("iptables -L -n", R), ("iptables -A INPUT -p tcp --dport 22 -j ACCEPT", X),
        ("ufw status", R), ("ufw allow 22", X), ("firewall-cmd --list-all", R), ("firewall-cmd --add-port=80/tcp", X),
        ("dig +short example.com", R), ("tcpdump -i any port 443 -c 10", R), ("openssl s_client -connect example.com:443", R),
        ("aws ec2 describe-vpcs", R), ("aws ec2 describe-vpn-connections", R), ("gcloud compute routers nats list --router r", R),
        ("gcloud compute networks subnets list", R), ("az network vnet list", R), ("az network lb list", R), ("aws elbv2 describe-load-balancers", R),
        ("wg show", R), ("wg-quick up wg0", X), ("tc qdisc show dev eth0", R), ("tc qdisc add dev eth0 root netem delay 200ms", X),
    ],
    "security": [
        ("trivy image nginx", R), ("trivy k8s --report summary cluster", R), ("falcoctl artifact list", R), ("kube-bench run", R),
        ("opa eval -d policy.rego data.main.deny", R), ("conftest test k8s/", R), ("kyverno apply policy.yaml --resource r.yaml", R),
        ("vault status", R), ("vault kv get secret/db", R), ("vault kv put secret/db password=x", X), ("vault policy write app app.hcl", X),
        ("cmctl status certificate web", R), ("cmctl check api", R), ("cmctl renew web", C), ("aws iam list-roles", R),
        ("aws kms enable-key-rotation --key-id k", X), ("kubectl get clusterrolebindings", R),
    ],
    "observability": [
        ("promtool check config prometheus.yml", P), ("promtool query instant http://prom:9090 up", R), ("amtool alert query", R),
        ("amtool silence add alertname=X", C), ("logcli query '{app=\"web\"}'", R), ("otelcol validate --config c.yaml", P),
        ("aws cloudwatch describe-alarms --state-value ALARM", R), ("aws logs tail /aws/eks/c/cluster", R),
        ("aws cloudwatch put-metric-alarm --alarm-name a", C), ("aws cloudwatch delete-alarms --alarm-names a", X),
    ],
    "data": [
        ("psql -c 'select 1'", R), ("psql -c 'drop table orders'", X), ("psql -f migrate.sql", X), ("pg_dump db -Fc -f db.dump", R),
        ("pg_restore -d db --clean db.dump", X), ("mysql -e 'show databases'", R), ("mysqldump shop", R), ("redis-cli info", R),
        ("redis-cli flushall", X), ("mongosh --eval 'db.stats()'", R), ("mongosh --eval 'db.orders.drop()'", X), ("mongodump --out d", R),
        ("mongorestore --drop dump/", X), ("kafka-topics --bootstrap-server b --list", R), ("kafka-topics --bootstrap-server b --delete --topic t", X),
        ("kafka-consumer-groups --bootstrap-server b --describe --group g", R), ("kafka-configs --alter --entity-type topics", C),
        ("aws rds describe-db-instances", R), ("aws rds create-db-snapshot --db-instance-identifier db --db-snapshot-identifier s", C),
        ("aws rds reboot-db-instance --db-instance-identifier db", X), ("gcloud sql backups create --instance db", C),
        ("gcloud sql instances failover db", X),
        ("pg_isready -h db -p 5432", R),
    ],
    "storage": [
        ("aws s3 ls s3://b", R), ("aws s3 cp f s3://b/f", C), ("aws s3api put-bucket-versioning --bucket b --versioning-configuration Status=Enabled", C),
        ("gcloud storage cp f gs://b/f", C), ("mc ls local", R), ("mc mirror ./d local/b", C), ("mc rb local/b --force", X),
        ("mc admin user add local u p", X), ("aws ec2 describe-volumes", R), ("aws ec2 delete-volume --volume-id v", X),
        ("aws efs describe-file-systems", R), ("ceph -s", R), ("ceph osd tree", R), ("ceph osd out 3", X), ("rbd ls pool", R),
        ("rbd rm pool/img", X), ("rbd snap create pool/img@s", C), ("showmount -e nfs1", R), ("exportfs -v", R), ("exportfs -ra", C),
        ("exportfs -u host:/data", X),
    ],
    "servers": [
        ("systemctl status nginx", R), ("systemctl restart nginx", C), ("systemctl stop nginx", X), ("journalctl -u nginx --since -1h", R),
        ("reboot", X), ("shutdown -h now", X), ("sysctl net.ipv4.ip_forward", R), ("sysctl -w net.ipv4.ip_forward=1", C),
        ("nginx -t", P), ("nginx -s reload", C), ("nginx -s stop", X), ("haproxy -c -f /etc/haproxy/haproxy.cfg", P),
        ("traefik healthcheck --ping", R), ("pwsh -Command Get-Service", R), ("pwsh -Command 'Restart-Service W3SVC'", C),
        ("pwsh -Command 'Stop-Service W3SVC'", X), ("pwsh -Command 'Restart-Computer -Force'", X), ("sc.exe query W3SVC", R),
        ("sc.exe stop W3SVC", X), ("net stop W3SVC", X), ("net start W3SVC", C), ("net localgroup administrators bob /add", X),
    ],
    "tests": [
        ("nc -vz db 5432", R), ("nmap -sV 10.0.0.0/24", R), ("mtr --report example.com", R), ("k6 run load.js", C),
        ("hey -z 30s https://staging.example.com/", C), ("hey -z 30s https://shop-prod.example.com/", X), ("vegeta attack -rate=50 -duration=30s", C),
        ("testssl.sh --severity HIGH https://staging.example.com", R),
    ],
    "finops": [
        ("infracost breakdown --path .", R), ("infracost diff --path .", R), ("aws ce get-cost-forecast --granularity MONTHLY", R),
        ("aws compute-optimizer get-ec2-instance-recommendations", R), ("aws budgets create-budget --account-id 1", C),
        ("gcloud recommender recommendations list --recommender google.compute.instance.MachineTypeRecommender", R),
        ("az advisor recommendation list --category Cost", R),
    ],
}

CASES = [(domain, command, risk) for domain, rows in MATRIX.items() for command, risk in rows]


def test_the_matrix_covers_every_domain_the_user_named():
    assert len(CASES) >= 300
    assert set(MATRIX) == {"aws", "gcp", "azure", "private cloud", "containers", "orchestration", "iac", "configuration",
                           "ci/cd", "network", "security", "observability", "data", "storage", "servers", "tests", "finops"}


@pytest.mark.parametrize(("domain", "command", "expected"), CASES, ids=[f"{d}: {c}" for d, c, _ in CASES])
def test_risk_of_each_command(domain, command, expected):
    classified = worst(classify_command(command))
    assert classified is not None, f"{command!r} is not recognised as an infrastructure command"
    decision = decide(classified, InfraPolicy(), DEV)
    assert decision.risk == expected, f"{command!r}: {decision.risk} ({decision.reasons}), expected {expected}"
    if expected == "critical":
        assert decision.action == "ask"
    if expected in {"read", "plan"} and "secrets" not in decision.categories:
        assert decision.action == "allow"


@pytest.mark.parametrize("binary", sorted({name for names in PROVIDER_BINARIES.values() for name in names} - _UNGATED))
def test_every_known_tool_reaches_the_exec_gate_and_the_classifier(binary):
    command = f"{binary} --help"
    assert mentions_infra(command), f"the exec gate would let {binary} through unchecked"
    assert mentions_infra(f"sudo /usr/local/bin/{binary} status")
    assert classify_segment(parse_segment(tokenize(command))) is not None


def test_the_gate_does_not_fire_on_lookalike_words():
    for command in ("pip install requests", "echo dotnet", "git commit -m 'kubectl-style'", "npm run docker-build", "cat ./notes/aws.md"):
        segments = classify_command(command)
        assert not segments, f"{command!r} was classified as infrastructure: {[s.binary for s in segments]}"


def test_production_and_remote_targets_raise_the_risk():
    prod = InfraPolicy()
    for command in ("ssh web-prod-1 'sudo systemctl restart nginx'", "ansible-playbook -i inventories/prod/hosts site.yml",
                    "helm upgrade shop ./chart --kube-context shop-production", "pulumi up -s prod", "scp app.tar web-prod-2:/srv/"):
        decision = decide(worst(classify_command(command)), prod, DEV)
        assert decision.risk == "critical" and decision.production, command


VERIFIED = {
    "aws ec2 stop-instances --instance-ids i-1": "aws ec2 describe-instances",
    "gcloud compute instances stop vm1 --zone europe-west1-b": "TERMINATED",
    "gcloud sql instances restart db1": "RUNNABLE",
    "az vm restart -g rg -n vm1": "running",
    "az aks upgrade -g rg -n c1 --kubernetes-version 1.31": "Succeeded",
    "az vm create -g rg -n vm1 --image Ubuntu2204": "az vm show",
    "openstack server reboot vm1": "ACTIVE",
    "openstack server delete vm1": "notfound",
    "govc vm.power -off vm1": "poweredOff",
    "govc snapshot.create -vm vm1 before": "snapshot.tree",
    "qm shutdown 100": "stopped",
    "pct start 101": "running",
    "qm snapshot 100 before": "before",
    "podman run -d --name web nginx": "Up",
    "ansible-playbook -i inventory site.yml --limit web": "changed=0",
    "pulumi up --yes -s dev": "--expect-no-changes",
    "cdk deploy app": "--fail",
    "velero backup create before-upgrade": "velero backup get before-upgrade",
    "vault kv put -mount=secret db password=x": "metadata",
    "net stop W3SVC": "STOPPED",
    "argocd app sync shop": "--health",
    "flux reconcile kustomization apps": "True",
    "helm upgrade --install shop ./chart -n shop": "deployed",
    "kubectl rollout restart deploy/web -n shop": "rollout status",
    "terraform apply tfplan": "-detailed-exitcode",
    "systemctl restart nginx": "active",
}


@pytest.mark.parametrize(("command", "marker"), list(VERIFIED.items()), ids=list(VERIFIED))
def test_a_change_on_each_platform_gets_an_automatic_verification(command, marker, tmp_path):
    checks = verification_plan(worst(classify_command(command)), tmp_path)
    assert checks, f"{command!r} has no automatic verification"
    described = " ".join(f"{' '.join(check.argv)} {check.expect}" for check in checks)
    assert marker in described, f"{command!r}: {described}"
    assert "password=x" not in described  # a check never echoes a secret value
