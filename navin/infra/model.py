# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Data model of the infrastructure registry.

An :class:`Operation` is one thing Navin can do on infrastructure: a CLI
command, an API call or an IaC resource. Every record answers the same
questions (what, where, with which parameters and permissions, with which
risk, how to verify it, how to undo it, where it works). Four tiers are kept
apart on purpose:

- known: the operation is in the registry (curated or discovered);
- available: its CLI or API is present on this machine;
- authorized: the policy lets it run at the current autonomy level (directly
  or after the user's approval) and no provider permission check denied it;
- tested: it ran through the engine here and its verification passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LEVELS = ("observe", "prepare", "execute")
RISKS = ("read", "plan", "change", "critical")
TIERS = ("known", "available", "authorized", "tested")
RISK_ORDER = {risk: index for index, risk in enumerate(RISKS)}

#: Provider id -> the binaries that make it available, most important first.
PROVIDER_BINARIES: dict[str, tuple[str, ...]] = {
    "aws": ("aws",),
    "gcp": ("gcloud", "gsutil", "bq"),
    "azure": ("az",),
    "kubernetes": ("kubectl", "kustomize", "eksctl", "k3s", "rke2", "microk8s", "kind", "k3d", "minikube", "velero"),
    "openshift": ("oc",),
    "docker": ("docker", "docker-compose"),
    "podman": ("podman", "podman-compose"),
    "containerd": ("nerdctl", "ctr", "crictl"),
    "terraform": ("terraform",),
    "opentofu": ("tofu",),
    "pulumi": ("pulumi",),
    "cdk": ("cdk",),
    "helm": ("helm",),
    "ansible": ("ansible", "ansible-playbook", "ansible-doc", "ansible-inventory", "ansible-galaxy", "ansible-vault", "ansible-config", "ansible-lint"),
    "argocd": ("argocd",),
    "flux": ("flux",),
    "github": ("gh",),
    "gitlab": ("glab",),
    "jenkins": ("jenkins-cli",),
    "forgejo": ("tea", "forgejo-runner", "act_runner"),
    "openstack": ("openstack",),
    "vsphere": ("govc",),
    "proxmox": ("pvesh", "qm", "pct", "qmrestore", "vzdump"),
    "linux": ("systemctl", "journalctl", "ip", "ss", "nft", "iptables", "ip6tables", "ufw", "firewall-cmd", "dig", "tcpdump",
              "openssl", "ssh", "scp", "sftp", "rsync", "cloud-init", "reboot", "shutdown", "poweroff", "halt", "sysctl",
              "hostnamectl", "exportfs", "showmount", "nfsstat", "nmap", "mtr", "traceroute", "nc", "wg", "wg-quick", "tc"),
    "webservers": ("nginx", "haproxy", "traefik"),
    "security": ("trivy", "falco", "falcoctl", "kube-bench", "checkov", "tfsec", "vault", "opa", "conftest", "gator", "kyverno", "grype", "cmctl"),
    "observability": ("promtool", "amtool", "logcli", "grafana-cli", "otelcol", "otelcol-contrib"),
    "databases": ("psql", "pg_isready", "pg_dump", "pg_dumpall", "pg_restore", "mysql", "mysqldump", "redis-cli", "mongosh", "mongodump", "mongorestore", "kafka-topics"),
    "storage": ("mc", "rclone", "ceph", "rbd", "rados", "radosgw-admin", "gsutil"),
    "testing": ("k6", "hey", "vegeta", "ab", "wrk", "curl", "testssl.sh"),
    "finops": ("infracost", "kubecost"),
    "windows": ("powershell", "pwsh", "sc", "net"),
}

#: Providers ``discover`` knows how to read from the machine.
DISCOVERABLE = (
    "aws", "gcp", "azure", "kubernetes", "openshift", "docker", "podman", "containerd", "terraform",
    "opentofu", "pulumi", "cdk", "helm", "ansible", "argocd", "flux", "github", "gitlab", "jenkins",
    "forgejo", "openstack", "vsphere", "proxmox", "velero", "security",
)


def provider_of_binary(binary: str) -> str:
    """The provider a binary belongs to, '' when it is not an infrastructure tool."""
    for provider, binaries in PROVIDER_BINARIES.items():
        if binary in binaries:
            return provider
    if binary.startswith("kafka-"):
        return "databases"
    if binary.startswith("ansible-"):
        return "ansible"
    return ""


@dataclass
class Operation:
    """One entry of the Universal Command & API Registry."""

    id: str
    provider: str
    service: str
    name: str
    #: The exact CLI form, without the variable arguments.
    command: str
    #: The API behind it (HTTP method and path, SDK call, IaC address).
    endpoint: str = ""
    version: str = ""
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    effects: str = ""
    risk: str = "read"
    categories: list[str] = field(default_factory=list)
    verify: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)
    compat: list[str] = field(default_factory=list)
    #: Where the record comes from: curated, botocore, cloudcontrol, kubectl, ...
    source: str = ""
    doc: str = ""

    def to_row(self) -> dict[str, Any]:
        """The compact form stored in the catalog files (empty fields dropped)."""
        return {key: value for key, value in asdict(self).items() if value not in ("", [], None)}

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Operation:
        known = {name: row[name] for name in cls.__dataclass_fields__ if name in row}
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def effects_for(risk: str, categories: list[str] | tuple[str, ...] = ()) -> str:
    """A plain sentence on what an operation of this risk does."""
    base = {
        "read": "Reads state only; nothing changes.",
        "plan": "Computes or simulates a change (plan, diff, dry run, validation); nothing is applied.",
        "change": "Creates or changes resources; the previous state is captured first when possible.",
        "critical": "Deletes, replaces or exposes resources, or touches identity, network, data or cost: it can break service or lose data, and it always needs explicit approval.",
    }.get(risk, "")
    extra = {
        "iam": "Changes who can do what (IAM / RBAC).",
        "network": "Changes reachability (routing, firewalls, DNS, load balancers).",
        "data": "Can lose or overwrite stored data.",
        "costly": "Starts billable capacity.",
        "secrets": "Reveals or changes secret values.",
        "remote-exec": "Runs arbitrary commands on a remote machine or container.",
        "availability": "Can stop a running service.",
        "load": "Sends test traffic that can degrade the target.",
    }
    notes = [extra[name] for name in categories if name in extra]
    return " ".join([base, *notes]).strip()
