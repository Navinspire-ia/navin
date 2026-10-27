# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The curated infrastructure catalog covers every platform the operator cites."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from navin.infra.curated import CURATED, curated_operations
from navin.infra.model import Operation

OPS: list[Operation] = curated_operations()


def _text(op: Operation) -> str:
    return f"{op.provider} {op.service} {op.name} {op.command}".lower()


def _label(op: Operation) -> str:
    return f"{op.service} {op.name}".lower()


def _prov(*names):
    return lambda op: op.provider in names


def _svc(*names):
    return lambda op: op.service.lower() in names


def _has(*needles):
    return lambda op: any(n in _text(op) for n in needles)


def _word(pattern):
    rx = re.compile(pattern)
    return lambda op: bool(rx.search(_label(op)))


def _first(*binaries):
    return lambda op: _command_words(op.command)[:1] and _command_words(op.command)[0] in binaries


PLATFORMS = {
    "AWS": _prov("aws"),
    "Google Cloud": _prov("gcp"),
    "Azure": _prov("azure"),
    "OpenStack": _prov("openstack"),
    "vSphere": _prov("vsphere"),
    "Proxmox": _prov("proxmox"),
    "Docker": _prov("docker"),
    "Docker Compose": lambda op: op.provider == "docker" and op.service == "compose",
    "Podman": _prov("podman"),
    "containerd": _prov("containerd"),
    "Kubernetes": _prov("kubernetes"),
    "EKS": _has("aws eks", "eksctl"),
    "GKE": _has("gcloud container"),
    "AKS": _has("az aks"),
    "OpenShift": _prov("openshift"),
    "K3s": _has("k3s"),
    "RKE2": _has("rke2"),
    "MicroK8s": _has("microk8s"),
    "Terraform": _prov("terraform"),
    "OpenTofu": _prov("opentofu"),
    "Pulumi": _prov("pulumi"),
    "CloudFormation": _has("cloudformation"),
    "CDK": _prov("cdk"),
    "Ansible": _prov("ansible"),
    "SSH": _first("ssh", "scp", "rsync", "ssh-keyscan"),
    "cloud-init": _has("cloud-init"),
    "Helm": _prov("helm"),
    "Kustomize": _has("kustomize", "apply -k", "diff -k"),
    "GitHub Actions": _prov("github"),
    "GitLab CI": _prov("gitlab"),
    "Jenkins": _prov("jenkins"),
    "Forgejo Actions": _prov("forgejo"),
    "Argo CD": _prov("argocd"),
    "Flux": _prov("flux"),
    "VPC": _word(r"\bvpcs?\b"),
    "VNet": _word(r"\bvnets?\b"),
    "subnet": _word(r"subnet"),
    "routing": _word(r"\brout(e|es|ing)\b"),
    "VPN": _word(r"\bvpn\b"),
    "DNS": _word(r"\bdns\b"),
    "NAT": _word(r"\bnat\b"),
    "firewall": _word(r"firewall"),
    "load balancer": _word(r"load balancer"),
    "IAM": _word(r"\biam\b"),
    "RBAC": _word(r"\brbac\b"),
    "KMS": _word(r"\bkms\b"),
    "Vault": lambda op: op.service == "vault" and op.provider == "security",
    "cert-manager": _svc("cert-manager"),
    "OPA": _svc("opa", "conftest", "gatekeeper", "kyverno"),
    "Trivy": _svc("trivy"),
    "Falco": _svc("falco"),
    "Prometheus": _svc("prometheus"),
    "Grafana": _svc("grafana"),
    "Loki": _svc("loki"),
    "Tempo": _svc("tempo"),
    "OpenTelemetry": _svc("otel"),
    "CloudWatch": lambda op: op.provider == "aws" and op.service in {"cloudwatch", "logs"},
    "PostgreSQL": _svc("postgresql", "postgres"),
    "MySQL": _svc("mysql"),
    "Redis": _svc("redis"),
    "MongoDB": _svc("mongodb"),
    "Kafka": _svc("kafka"),
    "RDS": lambda op: op.provider == "aws" and op.service == "rds",
    "Cloud SQL": lambda op: op.provider == "gcp" and op.service == "sql",
    "S3": lambda op: op.provider == "aws" and op.service in {"s3", "s3api"},
    "GCS": _svc("gcs"),
    "MinIO": _svc("minio"),
    "EBS": _svc("ebs"),
    "EFS": _svc("efs"),
    "Ceph": _svc("ceph"),
    "NFS": _svc("nfs"),
    "Linux": _prov("linux"),
    "Windows Server": _prov("windows"),
    "systemd": _svc("systemd"),
    "Nginx": _svc("nginx"),
    "Traefik": _svc("traefik"),
    "HAProxy": _svc("haproxy"),
    "network tests": lambda op: op.provider == "testing" and op.service == "network",
    "API tests": lambda op: op.provider == "testing" and op.service == "api",
    "performance tests": lambda op: op.provider == "testing" and op.service == "performance",
    "load tests": lambda op: op.provider == "testing" and op.service == "load",
    "security tests": lambda op: op.provider == "testing" and op.service == "security",
    "availability tests": lambda op: op.provider == "testing" and op.service == "availability",
    "recovery tests": lambda op: op.provider == "testing" and op.service == "recovery",
    "FinOps inventory": lambda op: op.provider == "finops" and op.service == "inventory",
    "cost analysis": lambda op: op.provider == "finops" and op.service.startswith("cost"),
    "rightsizing": lambda op: op.provider == "finops" and op.service == "rightsizing",
    "budgets": lambda op: op.provider == "finops" and op.service == "budgets",
    "optimisation": lambda op: op.provider == "finops" and op.service == "optimisation",
}

KNOWN_BINARIES = {
    "aws", "gcloud", "gsutil", "bq", "az", "openstack", "govc", "qm", "pct", "pvesh", "pvesm", "vzdump", "qmrestore",
    "docker", "podman", "ctr", "crictl", "nerdctl", "kubectl", "eksctl", "oc", "k3s", "rke2", "microk8s", "kind",
    "minikube", "terraform", "tofu", "pulumi", "cdk", "ansible", "ansible-playbook", "ansible-inventory",
    "ansible-doc", "ansible-galaxy", "ansible-vault", "ssh", "scp", "rsync", "ssh-keyscan", "cloud-init", "helm",
    "kustomize", "gh", "glab", "curl", "java", "tea", "argocd", "flux", "ip", "ss", "nft", "dig", "tcpdump",
    "openssl", "ufw", "firewall-cmd", "wg", "wg-quick", "echo", "socat", "trivy", "kube-bench", "checkov",
    "conftest", "falcoctl", "vault", "opa", "gator", "kyverno", "cmctl", "promtool", "amtool", "logcli", "otelcol",
    "psql", "pg_dump", "pg_restore", "pg_isready", "mysql", "mysqldump", "redis-cli", "mongosh", "mongodump",
    "mongorestore", "kafka-topics", "kafka-consumer-groups", "kafka-configs", "mc", "ceph", "rbd", "showmount",
    "exportfs", "nfsstat", "mount", "systemctl", "journalctl", "uptime", "df", "free", "ps", "dmesg", "apt",
    "apt-get", "dnf", "hostnamectl", "timedatectl", "sysctl", "nginx", "haproxy", "traefik", "tail", "sc.exe",
    "pwsh", "nc", "mtr", "traceroute", "tc", "hey", "k6", "vegeta", "ab", "nmap", "testssl.sh", "while",
    "velero", "infracost",
}
POWERSHELL_CMDLET = re.compile(r"^[A-Z][a-z]+-[A-Z][A-Za-z]+$")
CLOUD_PROVIDERS = {"aws", "gcp", "azure", "kubernetes", "openshift"}


def _command_words(command: str) -> list[str]:
    words = command.split()
    while words and re.match(r"^[A-Z_][A-Z0-9_]*=", words[0]):
        words = words[1:]
    return words


def test_ids_are_unique():
    counts = Counter(op.id for op in OPS)
    assert [i for i, n in counts.items() if n > 1] == []
    assert len(OPS) == len(CURATED)


@pytest.mark.parametrize("platform", sorted(PLATFORMS))
def test_every_cited_platform_has_a_read_and_a_change(platform):
    selected = [op for op in OPS if PLATFORMS[platform](op)]
    assert selected, f"{platform}: no record"
    assert any(op.risk in {"read", "plan"} for op in selected), f"{platform}: no read or plan record"
    assert any(op.risk in {"change", "critical"} for op in selected), f"{platform}: no change or critical record"


def test_every_record_is_complete():
    for op in OPS:
        assert op.risk in {"read", "plan", "change", "critical"}, op.id
        assert op.examples and op.examples[0].strip(), f"{op.id}: no example"
        if op.risk in {"change", "critical"} and "local" not in op.categories:
            assert op.verify and all(v.strip() for v in op.verify), f"{op.id}: no verify"
            assert op.rollback and all(r.strip() for r in op.rollback), f"{op.id}: no rollback"
        if op.provider in CLOUD_PROVIDERS:
            assert op.permissions and all(p.strip() for p in op.permissions), f"{op.id}: no permissions"


def test_every_command_starts_with_a_known_binary():
    for op in OPS:
        words = _command_words(op.command)
        assert words, op.id
        first = words[0]
        assert first in KNOWN_BINARIES or POWERSHELL_CMDLET.match(first), f"{op.id}: unknown binary {first!r}"


def test_cloud_commands_target_their_provider():
    expected = {"aws": {"aws", "eksctl"}, "gcp": {"gcloud"}, "azure": {"az"}, "openshift": {"oc"}}
    for op in OPS:
        if op.provider in expected:
            assert _command_words(op.command)[0] in expected[op.provider], op.id


def test_no_typographic_dashes():
    for row in CURATED:
        for value in row:
            text = " ".join(value) if isinstance(value, list) else str(value)
            assert "\u2013" not in text and "\u2014" not in text, row[3]
