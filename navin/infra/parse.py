# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What an infrastructure command line does: tool, verb, target, risk.

The classification is deliberately conservative: an unknown verb of a known
tool is a ``change``, never a ``read``. A compound line (``a && b | c``) takes
the worst risk of its parts. Risks are ``read`` < ``plan`` < ``change`` <
``critical``; categories (iam, network, data, costly, secrets, remote-exec,
availability, load) say why a change is sensitive.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from navin.infra.model import PROVIDER_BINARIES, RISK_ORDER, provider_of_binary

_CONTROL = {"|", "||", "&&", ";", "&", "|&", ";;", "(", ")"}
_WRAPPERS = {"sudo", "nohup", "time", "exec", "command", "env", "doas", "stdbuf", "nice", "ionice", "unbuffer"}
#: Flags that take a value as the next word (when written without ``=``).
_VALUE_FLAGS = {
    "-n", "--namespace", "--context", "--kube-context", "-f", "--filename", "-o", "--output", "-l",
    "--selector", "--kubeconfig", "-c", "--container", "--profile", "--region", "--project", "--zone",
    "--cluster", "--version", "--values", "--set", "--set-string", "--set-file", "-p", "--patch", "--type",
    "--replicas", "--timeout", "--query", "--cli-input-json", "--endpoint-url", "-g", "--resource-group",
    "--subscription", "--name", "-i", "--inventory", "--limit", "--tags", "-e", "--extra-vars", "-m",
    "--module-name", "-a", "--args", "-H", "--host", "--file", "--env-file", "--project-name", "-X",
    "--request", "--method", "-d", "--data", "--repo", "-R", "--app", "--server", "--image", "--target",
    "--impersonate-service-account", "--account", "--configuration", "--format", "--filter", "--revision",
    "-u", "--user", "--as", "--as-group", "--from", "--to", "--chdir", "-C", "--directory", "--stack",
    "-s", "--cwd", "--workspace", "--location", "-l", "--label", "--eval", "--execute", "--command",
    "--cluster-name", "--vault-name", "--nsg-name", "--vnet-name",
}
_SINGLE_DASH_VALUE = {"-var", "-var-file", "-target", "-out", "-state", "-backend-config", "-parallelism", "-lock-timeout", "-auth", "-vm"}


@dataclass
class ParsedCommand:
    """One simple command of a line, after its wrappers and env assignments."""

    raw: str
    binary: str
    argv: list[str]
    words: list[str]
    flags: dict[str, str | bool]
    env: dict[str, str] = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return provider_of_binary(self.binary)

    def flag(self, *names: str) -> str:
        """The value of the first flag present among ``names`` ('' if absent or boolean)."""
        for name in names:
            value = self.flags.get(name)
            if isinstance(value, str):
                return value
        return ""

    def has(self, *names: str) -> bool:
        return any(name in self.flags for name in names)


@dataclass
class Classification:
    """What one command does, as far as the line tells."""

    command: str
    binary: str
    provider: str
    service: str = ""
    verb: str = ""
    resource: str = ""
    risk: str = "change"
    categories: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    targets: dict[str, str] = field(default_factory=dict)
    parsed: ParsedCommand | None = None

    @property
    def mutation(self) -> bool:
        return RISK_ORDER.get(self.risk, 2) >= RISK_ORDER["change"]


def tokenize(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return command.split()


def split_commands(command: str) -> list[list[str]]:
    """The simple commands of a line, split on pipes, ``&&``, ``;`` and friends."""
    segments: list[list[str]] = [[]]
    for token in tokenize(command):
        if token in _CONTROL or set(token) <= set(";&|()"):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _binary_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def parse_segment(tokens: list[str]) -> ParsedCommand | None:
    env: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            name, _, value = token.partition("=")
            env[name] = value
            index += 1
            continue
        if _binary_name(token) in _WRAPPERS:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        if _binary_name(token) == "timeout":
            index += 1
            while index < len(tokens) and (tokens[index].startswith("-") or re.match(r"^\d+[smhd]?$", tokens[index])):
                index += 1
            continue
        break
    if index >= len(tokens):
        return None
    argv = tokens[index:]
    binary = _binary_name(argv[0])
    words: list[str] = []
    flags: dict[str, str | bool] = {}
    position = 1
    while position < len(argv):
        token = argv[position]
        if token == "--":
            words.extend(argv[position + 1:])
            break
        if token.startswith("-") and len(token) > 1 and not re.match(r"^-\d", token):
            name, eq, value = token.partition("=")
            if eq:
                flags[name] = value
            elif (name in _VALUE_FLAGS or name in _SINGLE_DASH_VALUE) and position + 1 < len(argv) and not argv[position + 1].startswith("-"):
                flags[name] = argv[position + 1]
                position += 1
            else:
                flags[name] = True
        else:
            words.append(token)
        position += 1
    return ParsedCommand(raw=" ".join(tokens), binary=binary, argv=argv, words=words, flags=flags, env=env)


def parse_command(command: str) -> list[ParsedCommand]:
    out = []
    for segment in split_commands(command):
        parsed = parse_segment(segment)
        if parsed is not None:
            out.append(parsed)
    return out


# -- verbs per tool -------------------------------------------------------------

_KUBE_READ = {
    "get", "describe", "logs", "top", "explain", "api-resources", "api-versions", "version", "cluster-info",
    "events", "wait", "completion", "plugin", "options", "kustomize", "proxy", "port-forward", "whoami",
    "status", "projects", "extract",
}
_KUBE_CHANGE = {
    "apply", "create", "patch", "scale", "set", "label", "annotate", "expose", "autoscale", "run", "cp",
    "edit", "replace", "cordon", "uncordon", "attach", "exec", "debug", "new-app", "new-project", "new-build",
    "start-build", "import-image", "tag", "process", "rsh", "rsync", "login", "project", "logout",
    "cancel-build", "rollback",
}
_KUBE_CRITICAL = {"delete", "drain", "taint", "certificate"}
_SECRET_KINDS = {"secret", "secrets"}
_RBAC_KINDS = {
    "role", "roles", "clusterrole", "clusterroles", "rolebinding", "rolebindings", "clusterrolebinding",
    "clusterrolebindings", "serviceaccount", "serviceaccounts", "sa", "securitycontextconstraints", "scc",
}
_DATA_KINDS = {
    "pvc", "persistentvolumeclaim", "persistentvolumeclaims", "pv", "persistentvolume", "persistentvolumes",
    "statefulset", "statefulsets", "sts", "namespace", "namespaces", "ns", "crd", "crds",
    "customresourcedefinition", "customresourcedefinitions", "volumesnapshot", "volumesnapshots",
}
_NETWORK_KINDS = {"networkpolicy", "networkpolicies", "netpol", "ingress", "ingresses", "ing", "gateway", "gateways", "httproute", "httproutes", "route", "routes"}
_WORKLOAD_KINDS = {"deployment", "deployments", "deploy", "statefulset", "statefulsets", "sts", "daemonset", "daemonsets", "ds"}


def _resource_of(word: str) -> tuple[str, str]:
    """``deploy/web`` -> (deploy, web); ``pods.metrics.k8s.io`` -> (pods, '')."""
    kind, _, name = word.partition("/")
    return kind.split(".", 1)[0].lower(), name


def _classify_kube(p: ParsedCommand, c: Classification) -> None:
    words = p.words
    if p.binary in {"microk8s", "k3s", "rke2"} and words[:1] == ["kubectl"]:
        p.words = words = words[1:]
    verb = words[0] if words else ""
    sub = words[1] if len(words) > 1 else ""
    c.verb = verb
    kind, name = _resource_of(sub) if sub else ("", "")
    c.resource = kind
    c.service = "core"
    c.targets["kube_context"] = p.flag("--context")
    namespace = p.flag("-n", "--namespace")
    if namespace:
        c.targets["namespace"] = namespace
    if p.binary in {"kind", "k3d", "minikube", "microk8s", "k3s", "rke2"} and verb not in _KUBE_READ | _KUBE_CHANGE | _KUBE_CRITICAL:
        local_read = {"status", "get", "version", "config", "inspect", "list", "ls", "kubeconfig", "ip", "logs", "profile", "check-config", "--version"}
        if verb == "etcd-snapshot":
            c.verb = f"etcd-snapshot {sub}".strip()
            c.risk = "read" if sub in {"ls", "list", ""} else ("critical" if sub in {"delete", "prune"} else "change")
            if c.risk == "critical":
                c.categories.add("data")
            elif c.risk == "change":
                c.categories.add("backup")
        elif verb in local_read:
            c.risk = "read"
        elif verb in {"delete", "stop", "reset", "uninstall", "destroy", "pause"}:
            c.risk, c.reasons = "critical", [f"{p.binary} {verb} stops or removes the cluster"]
            c.categories.add("data" if verb in {"delete", "reset", "uninstall", "destroy"} else "availability")
        else:
            c.risk = "change"
        return
    if verb in _KUBE_PLUGIN_READ or verb == "view-secret":
        c.service, c.risk = "plugin", "read"
        if verb == "view-secret":
            c.categories.add("secrets")
            c.reasons.append("prints secret values")
        return
    if verb in _KUBE_PLUGIN_LOCAL:
        c.service, c.risk = "plugin", ("read" if not sub or sub in {"list", "search", "info"} and verb == "krew" else "change")
        if c.risk == "change":
            c.categories.add("local")
        return
    if verb == "rollout":
        c.verb = f"rollout {sub}"
        c.resource, name = _resource_of(words[2]) if len(words) > 2 else ("", "")
        c.risk = "read" if sub in {"status", "history"} else "change"
    elif verb == "auth":
        c.verb = f"auth {sub}"
        c.risk = "read" if sub in {"can-i", "whoami"} else "critical"
        if c.risk == "critical":
            c.categories.add("iam")
    elif verb == "config":
        c.verb = f"config {sub}"
        c.risk = "read" if sub in {"view", "get-contexts", "current-context", "get-clusters", "get-users"} else "change"
        if c.risk == "change":
            c.categories.add("local")
    elif verb == "adm":
        c.verb = f"adm {sub}"
        reads = sub in {"top", "inspect", "must-gather", "node-logs"} or (sub == "policy" and "who-can" in words) or (sub == "release" and words[2:3] in (["info"], ["extract"]))
        c.risk = "read" if reads else "critical"
        if c.risk == "critical":
            c.categories.add("iam" if sub in {"policy", "groups", "create-bootstrap-project-template"} else "availability")
    elif verb == "policy":
        c.risk = "read" if sub in {"who-can", "scc-review", "scc-subject-review"} else "critical"
        if c.risk == "critical":
            c.categories.add("iam")
    elif verb == "certificate":
        c.risk = "critical"
        c.categories.add("iam")
    elif verb in _KUBE_READ:
        c.risk = "read"
        if kind in _SECRET_KINDS and (p.flag("-o", "--output") in {"yaml", "json", "jsonpath", "go-template"} or verb in {"describe", "extract"} or p.flag("-o", "--output").startswith(("jsonpath", "go-template"))):
            c.categories.add("secrets")
            c.reasons.append("reads secret values")
    elif verb in _KUBE_CRITICAL:
        c.risk = "critical"
        c.reasons.append(f"kubectl {verb} removes or evicts resources")
        if verb in {"delete"} and (kind in _DATA_KINDS or kind == "" and p.has("-f", "--filename")):
            c.categories.add("data")
        if verb == "drain":
            c.categories.add("availability")
    elif verb in _KUBE_CHANGE:
        c.risk = "change"
        if verb in {"exec", "attach", "debug", "rsh"}:
            c.categories.add("remote-exec")
        if verb == "scale" and str(p.flag("--replicas")) == "0":
            # Scaling to zero takes the workload down: as disruptive as a stop.
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append("scale --replicas=0 stops every pod of the workload")
        if verb in {"login", "project", "logout"}:
            c.categories.add("local")
        if verb == "replace" and p.has("--force"):
            c.risk = "critical"
            c.reasons.append("replace --force deletes and recreates")
    else:
        c.risk = "change"
    if c.mutation:
        if kind in _RBAC_KINDS:
            c.risk = "critical"
            c.categories.add("iam")
        if kind in _NETWORK_KINDS:
            c.categories.add("network")
            if kind.startswith(("networkpolic", "netpol")):
                c.risk = "critical"
        if kind in _SECRET_KINDS:
            c.categories.add("secrets")
        manifest = (p.flag("-f", "--filename") or "").lower()
        if not kind and manifest:
            # The file name is all the line says about what the manifest holds.
            if "networkpolic" in manifest or "netpol" in manifest:
                c.risk = "critical"
                c.categories.add("network")
            if any(token in manifest for token in ("rbac", "rolebinding", "clusterrole")):
                c.risk = "critical"
                c.categories.add("iam")
            if "secret" in manifest:
                c.categories.add("secrets")
    dry = p.flag("--dry-run") or ("--dry-run" in p.flags and "client")
    if dry and dry != "none" and c.risk in {"change", "critical"} and verb not in {"exec", "attach", "debug"}:
        c.risk = "plan"
        c.reasons.append("dry run")
    if verb == "diff":
        c.risk = "plan"
    if name:
        c.targets["name"] = name


_HELM_READ = {"list", "ls", "status", "get", "history", "hist", "show", "inspect", "search", "version", "env", "verify", "completion"}
_HELM_PLAN = {"template", "lint", "diff"}
_HELM_CRITICAL = {"uninstall", "delete", "del", "un"}


def _classify_helm(p: ParsedCommand, c: Classification) -> None:
    verb = p.words[0] if p.words else ""
    sub = p.words[1] if len(p.words) > 1 else ""
    c.verb, c.service = verb, "release"
    c.targets["kube_context"] = p.flag("--kube-context")
    if p.flag("-n", "--namespace"):
        c.targets["namespace"] = p.flag("-n", "--namespace")
    if verb in {"repo", "dependency", "dep", "plugin", "registry"}:
        c.verb = f"{verb} {sub}"
        c.risk = "read" if sub in {"list", "ls"} else "change"
        c.categories.add("local")
    elif verb in _HELM_READ:
        c.risk = "read"
    elif verb in _HELM_PLAN:
        c.risk = "plan"
    elif verb in _HELM_CRITICAL:
        c.risk = "critical"
        c.reasons.append("helm uninstall removes every resource of the release")
        c.categories.add("data")
    else:
        c.risk = "change"
    if verb in {"install", "upgrade", "rollback", "uninstall", "status", "history", "get"}:
        names = p.words[1:] if verb != "get" else p.words[2:]
        if names:
            c.targets["release"] = names[0]
    if p.has("--dry-run") and c.risk in {"change", "critical"}:
        c.risk = "plan"
        c.reasons.append("dry run")


_TF_READ = {"version", "providers", "show", "output", "graph", "validate", "console", "metadata", "login", "logout", "modules"}
_TF_PLAN = {"plan", "init", "fmt", "get"}
_TF_CRITICAL = {"destroy", "taint", "untaint", "force-unlock"}


def _classify_terraform(p: ParsedCommand, c: Classification) -> None:
    verb = p.words[0] if p.words else ""
    sub = p.words[1] if len(p.words) > 1 else ""
    c.verb, c.service = verb, "workspace"
    chdir = p.flag("-chdir")
    if chdir:
        c.targets["workdir"] = chdir
    if verb == "state":
        c.verb = f"state {sub}"
        c.risk = "read" if sub in {"list", "show", "pull"} else "critical"
        if c.risk == "critical":
            c.categories.add("data")
            c.reasons.append(f"terraform state {sub} rewrites the state")
    elif verb == "workspace":
        c.verb = f"workspace {sub}"
        c.risk = "read" if sub in {"list", "show"} else ("critical" if sub == "delete" else "change")
        if sub in {"select", "new"}:
            c.categories.add("local")
    elif verb in _TF_READ:
        c.risk = "read"
    elif verb in _TF_PLAN:
        c.risk = "plan"
    elif verb in _TF_CRITICAL:
        c.risk = "critical"
        c.categories.add("data")
        c.reasons.append(f"terraform {verb} can destroy resources")
    elif verb == "apply":
        c.risk = "change"
        if p.has("-destroy"):
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append("apply -destroy destroys resources")
        plan_file = next((word for word in p.words[1:] if not word.startswith("-")), "")
        if plan_file:
            c.targets["plan_file"] = plan_file
        else:
            c.categories.add("no-saved-plan")
        if p.has("-auto-approve"):
            c.reasons.append("-auto-approve skips Terraform's own confirmation")
    elif verb in {"import", "refresh", "test", "replace-provider"}:
        c.risk = "change"
    else:
        c.risk = "change"
    var_file = p.flag("-var-file")
    if var_file:
        c.targets["var_file"] = var_file


_AWS_READ_PREFIX = (
    "describe-", "list-", "get-", "head-", "lookup-", "search-", "batch-get-", "select-", "check-", "filter-",
    "view-", "query", "scan", "download-", "export-", "detect-", "discover-", "poll-", "preview-", "read-",
)
_AWS_PLAN_PREFIX = ("validate-", "estimate-", "simulate-", "test-", "create-change-set", "describe-change-set", "dry-run")
_AWS_CRITICAL_PREFIX = (
    "delete-", "terminate-", "remove-", "deregister-", "detach-", "disassociate-", "disable-", "revoke-",
    "release-", "purge-", "reset-", "deactivate-", "drop-", "reject-", "abort-", "force-", "cancel-",
    "batch-delete-", "empty-", "failover-",
)
_AWS_IAM_SERVICES = {"iam", "sso-admin", "identitystore", "organizations", "ram", "kms", "secretsmanager", "sso", "cognito-idp", "accessanalyzer", "acm-pca"}
_AWS_DATA_SERVICES = {"rds", "dynamodb", "s3api", "s3", "elasticache", "redshift", "docdb", "neptune", "kafka", "es", "opensearch", "efs", "backup", "glacier", "memorydb", "keyspaces", "timestream-write", "fsx", "ebs"}
_AWS_NETWORK_SERVICES = {"route53", "route53domains", "elbv2", "elb", "globalaccelerator", "network-firewall", "directconnect", "cloudfront", "networkmanager", "vpc-lattice", "route53resolver", "wafv2", "shield"}
_AWS_NETWORK_TOKENS = ("security-group", "network-acl", "route", "vpn", "peering", "transit-gateway", "firewall", "nat-gateway", "internet-gateway", "vpc-endpoint", "prefix-list", "customer-gateway", "network-insights")
_AWS_STOP_PREFIX = ("reboot-", "hibernate-")
#: Stopping running capacity (not stop-query, stop-build or other job controls).
_AWS_STOP = {"stop-instances", "stop-db-instance", "stop-db-cluster", "stop-task", "stop-environment", "stop-cluster", "stop-notebook-instance", "stop-replication", "stop-application"}
_AWS_UPGRADES = {"update-cluster-version", "update-nodegroup-version", "upgrade-elasticsearch-domain", "upgrade-domain", "modify-cluster"}
_AWS_REMOTE_EXEC = {("ssm", "send-command"), ("ssm", "start-session"), ("ecs", "execute-command"), ("ec2-instance-connect", "send-ssh-public-key")}
#: Who may reach a resource: bucket policies and ACLs, resource policies, Lambda permissions.
_AWS_ACCESS_TOKENS = ("bucket-policy", "-acl", "resource-policy", "repository-policy", "add-permission", "remove-permission", "public-access", "key-policy", "grant-")
#: Database services where modify / restore / failover / reboot touch live data.
_AWS_DB_SERVICES = {"rds", "docdb", "neptune", "redshift", "elasticache", "memorydb", "dynamodb", "es", "opensearch", "kafka", "keyspaces"}
_AWS_COSTLY = {
    "run-instances", "create-db-instance", "create-db-cluster", "create-cluster", "create-nat-gateway",
    "create-cache-cluster", "create-replication-group", "create-nodegroup", "create-capacity-reservation",
    "allocate-hosts", "create-fleet", "request-spot-fleet", "request-spot-instances", "create-domain",
    "create-load-balancer", "create-vpn-connection", "create-transit-gateway", "create-file-system",
    "create-auto-scaling-group", "create-environment", "create-serverless-cache", "create-workspaces",
}


def _classify_aws(p: ParsedCommand, c: Classification) -> None:
    service = p.words[0] if p.words else ""
    op = p.words[1] if len(p.words) > 1 else ""
    c.service, c.verb = service, op
    c.targets["aws_profile"] = p.flag("--profile") or p.env.get("AWS_PROFILE", "")
    if p.flag("--region"):
        c.targets["region"] = p.flag("--region")
    if service in {"configure", "sso", "login", "logout"} or (service == "sts" and op in {"get-caller-identity", "get-session-token", "assume-role", "get-access-key-info", "decode-authorization-message"}):
        c.risk = "read" if service == "sts" or op in {"list", "get", "list-profiles", "export-credentials"} else "change"
        if c.risk == "change":
            c.categories.add("local")
        return
    if (service, op) in {("eks", "update-kubeconfig"), ("ecr", "get-login-password"), ("codeartifact", "login")}:
        c.risk = "change" if op != "get-login-password" else "read"
        c.categories.add("local")
        return
    if service == "s3":
        if op in {"ls", "presign"}:
            c.risk = "read"
        elif op in {"rm", "rb", "mv"} or (op == "sync" and p.has("--delete")):
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append(f"aws s3 {op} deletes objects")
        else:
            c.risk = "change"
        if p.has("--dryrun"):
            c.risk = "plan"
        return
    if op in {"wait", "tail"} or op.startswith(_AWS_READ_PREFIX):
        c.risk = "read"
        if (service == "secretsmanager" and op == "get-secret-value") or (service == "ssm" and op.startswith("get-parameter") and p.has("--with-decryption")) or (service == "kms" and op == "decrypt"):
            c.categories.add("secrets")
            c.reasons.append("reads secret values")
    elif op.startswith(_AWS_PLAN_PREFIX):
        c.risk = "plan"
    elif op.startswith(_AWS_CRITICAL_PREFIX):
        c.risk = "critical"
        c.reasons.append(f"aws {service} {op} removes or disables resources")
    elif op.startswith(_AWS_STOP_PREFIX) or op in _AWS_STOP or op in _AWS_UPGRADES:
        c.risk = "critical"
        c.categories.add("availability")
        c.reasons.append(f"aws {service} {op} stops, restarts or upgrades running capacity")
    else:
        c.risk = "change"
    if (service, op) in _AWS_REMOTE_EXEC:
        c.categories.add("remote-exec")
    if c.mutation:
        if service in _AWS_IAM_SERVICES:
            c.risk = "critical"
            c.categories.add("iam" if service != "secretsmanager" else "secrets")
        if any(token in op for token in _AWS_ACCESS_TOKENS) and not op.startswith(_AWS_PLAN_PREFIX):
            c.risk = "critical"
            c.categories.add("iam")
        if (service in _AWS_DATA_SERVICES and c.risk == "critical") or (service in _AWS_DB_SERVICES and op.startswith(("modify-", "restore-", "failover-", "reboot-", "update-table", "promote-"))):
            c.risk = "critical"
            c.categories.add("data")
        if service in _AWS_NETWORK_SERVICES or (service == "ec2" and any(token in op for token in _AWS_NETWORK_TOKENS)):
            c.categories.add("network")
            c.risk = "critical"
        if op in _AWS_COSTLY or op.startswith("purchase-"):
            c.categories.add("costly")
            c.risk = "critical"
            c.reasons.append("starts billable capacity")
        if service == "cloudformation" and op in {"deploy"} and p.has("--no-execute-changeset"):
            c.risk = "plan"
    if p.has("--dry-run") and c.risk in {"change", "critical"}:
        c.risk = "plan"
        c.reasons.append("dry run")


_VERB_READ = {"list", "describe", "get", "show", "read", "info", "version", "help", "status", "tail", "exists", "wait", "check", "view", "search", "export", "browse", "print-access-token", "ls", "query", "analyze", "lookup", "get-health", "test-iam-permissions"}
_READ_PREFIX = ("list-", "describe-", "get-", "search-", "query-", "analyze-", "lookup-", "show-")
_VERB_CRITICAL = {"delete", "remove", "purge", "disable", "destroy", "abandon-instances", "revoke", "deallocate", "reset", "undelete", "cancel", "set-iam-policy", "add-iam-policy-binding", "remove-iam-policy-binding", "rollback"}
#: Stopping, restarting or upgrading running capacity (availability).
_VERB_DISRUPT = {"stop", "restart", "reboot", "suspend", "upgrade", "failover", "rolling-action", "power-off", "shutdown", "recreate-instances"}
_GCP_COSTLY = {("compute", "instances", "create"), ("container", "clusters", "create"), ("container", "node-pools", "create"), ("sql", "instances", "create"), ("redis", "instances", "create"), ("alloydb", "clusters", "create"), ("compute", "reservations", "create"), ("spanner", "instances", "create")}
_GCP_NETWORK = {"networks", "firewall-rules", "routers", "routes", "vpn-tunnels", "vpn-gateways", "target-vpn-gateways", "forwarding-rules", "target-http-proxies", "target-https-proxies", "url-maps", "backend-services", "addresses", "record-sets", "managed-zones", "security-policies", "interconnects", "nats"}
_GCP_DATA = {"sql", "spanner", "bigtable", "firestore", "redis", "storage", "filestore", "alloydb", "memcache", "bq"}


def _classify_gcloud(p: ParsedCommand, c: Classification) -> None:
    words = [word for word in p.words if word not in {"alpha", "beta"}]
    c.targets["gcp_project"] = p.flag("--project")
    if p.flag("--impersonate-service-account"):
        c.targets["impersonate"] = p.flag("--impersonate-service-account")
    if p.binary == "gsutil":
        verb = words[0] if words else ""
        c.service, c.verb = "storage", verb
        c.risk = "read" if verb in {"ls", "cat", "du", "stat", "hash", "version", "help"} else ("critical" if verb in {"rm", "rb"} or (verb == "rsync" and p.has("-d")) else "change")
        if c.risk == "critical":
            c.categories.add("data")
        return
    if words[:1] in (["config"], ["auth"], ["components"], ["info"], ["version"], ["topic"], ["help"]):
        c.service, c.verb = words[0], words[1] if len(words) > 1 else ""
        c.risk = "read" if c.verb in {"list", "get", "get-value", "describe", "print-access-token", "print-identity-token", ""} or words[0] in {"info", "version", "topic", "help"} else "change"
        if c.risk == "change":
            c.categories.add("local")
        return
    if words[:1] == ["storage"] and len(words) > 1 and words[1] in {"ls", "cat", "du", "hash", "cp", "mv", "rm", "rsync", "sign-url"}:
        verb = words[1]
        c.service, c.verb = "storage", verb
        if verb in {"ls", "cat", "du", "hash", "sign-url"}:
            c.risk = "read"
        elif verb == "rm" or verb == "mv" or (verb == "rsync" and p.has("--delete-unmatched-destination-objects")):
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append(f"gcloud storage {verb} deletes objects")
        else:
            c.risk = "change"
        if p.has("--dry-run"):
            c.risk = "plan"
        return
    verb_index = next((i for i, word in enumerate(words) if i > 0 and (word in _VERB_READ or word in _VERB_CRITICAL or word in _VERB_DISRUPT or word.startswith(_READ_PREFIX) or re.match(r"^(create|update|deploy|patch|add|remove|delete|set|resize|start|stop|restart|import|enable|apply|attach|detach|run|submit|ssh|scp|connect|execute|restore|clone|move|promote|failover|upgrade|replace|sign|invoke|call|publish|acknowledge|recreate|rolling-action|reboot|suspend|resume|reset|migrate)(-|$)", word))), None)
    if verb_index is None:
        verb_index = len(words) - 1 if words else 0
    verb = words[verb_index] if words else ""
    c.service = words[0] if words else ""
    c.resource = words[verb_index - 1] if verb_index > 0 else ""
    c.verb = verb
    if verb == "get-credentials":
        c.risk = "change"
        c.categories.add("local")
        return
    if verb in _VERB_READ or verb.startswith(_READ_PREFIX) and "iam" not in verb:
        c.risk = "read"
        if c.service == "secrets" and verb == "access":
            c.categories.add("secrets")
    elif verb in _VERB_CRITICAL or verb.startswith(("remove-", "delete-")):
        c.risk = "critical"
        c.reasons.append(f"gcloud {' '.join(words[:verb_index + 1])} removes or disables resources")
    elif verb in _VERB_DISRUPT:
        c.risk = "critical"
        c.categories.add("availability")
        c.reasons.append(f"gcloud {' '.join(words[:verb_index + 1])} stops, restarts or upgrades running capacity")
    elif c.service == "secrets" and verb == "versions":
        c.risk = "read"
    else:
        c.risk = "change"
        if verb in {"ssh", "scp", "connect"}:
            c.categories.add("remote-exec")
    if c.service == "secrets" and "access" in words:
        c.risk = "read"
        c.categories.add("secrets")
    if c.mutation:
        if "iam" in verb or c.service == "iam" or "iam" in words:
            c.risk = "critical"
            c.categories.add("iam")
        if c.resource in _GCP_NETWORK or c.service == "dns":
            c.risk = "critical"
            c.categories.add("network")
        if c.service in _GCP_DATA and (c.risk == "critical" or verb in {"patch", "restore", "import", "promote-replica"}):
            c.risk = "critical"
            c.categories.add("data")
        if tuple(words[:1] + [c.resource, "create" if verb.startswith("create") else verb]) in _GCP_COSTLY:
            c.risk = "critical"
            c.categories.add("costly")
            c.reasons.append("starts billable capacity")
    if (p.has("--dry-run") or p.has("--validate-only") or p.has("--preview")) and c.mutation:
        c.risk = "plan"
        c.reasons.append("dry run")
    if words[verb_index + 1:verb_index + 2]:
        c.targets["name"] = words[verb_index + 1]


_AZ_COSTLY = {("vm", "create"), ("aks", "create"), ("vmss", "create"), ("sql", "create"), ("postgres", "create"), ("mysql", "create"), ("redis", "create"), ("cosmosdb", "create"), ("aks", "nodepool")}


def _classify_az(p: ParsedCommand, c: Classification) -> None:
    # az groups and verbs come before the first flag; a value after an unknown
    # flag (--template-file main.bicep) is not a verb.
    lead: list[str] = []
    for token in p.argv[1:]:
        if token.startswith("-"):
            break
        lead.append(token)
    words = lead or p.words
    verb = words[-1] if words else ""
    c.service, c.verb = (words[0] if words else ""), verb
    c.targets["azure_subscription"] = p.flag("--subscription")
    if p.flag("-g", "--resource-group"):
        c.targets["resource_group"] = p.flag("-g", "--resource-group")
    if c.service in {"login", "logout", "account", "config", "version", "upgrade", "extension", "bicep", "find", "interactive", "cloud"}:
        c.risk = "read" if verb in {"show", "list", "version", "find", "get-access-token"} or c.service in {"version", "find"} else "change"
        if c.risk == "change":
            c.categories.add("local")
        return
    if verb == "get-credentials":
        c.risk = "change"
        c.categories.add("local")
        return
    if verb in _VERB_READ or verb.startswith(("list", "show", "get-", "check", "query")):
        c.risk = "read"
        if c.service == "keyvault" and "secret" in words and verb in {"show", "download"}:
            c.categories.add("secrets")
    elif verb in {"what-if", "validate"} or p.has("--dry-run"):
        c.risk = "plan"
    elif verb in _VERB_CRITICAL or verb.startswith(("delete", "remove", "purge")) or verb in {"deallocate", "revoke", "reset-credentials", "regenerate-key", "renew", "rotate-certs"}:
        c.risk = "critical"
        c.reasons.append(f"az {' '.join(words)} removes, stops or rotates resources")
    elif verb in _VERB_DISRUPT or verb in {"redeploy", "reimage", "upgrade-control-plane"}:
        c.risk = "critical"
        c.categories.add("availability")
        c.reasons.append(f"az {' '.join(words)} stops, restarts or upgrades running capacity")
    else:
        c.risk = "change"
        if verb in {"run-command", "ssh", "invoke"} or "run-command" in words:
            c.categories.add("remote-exec")
    if c.mutation:
        if c.service in {"role", "ad", "identity", "keyvault"} or "role" in words:
            c.risk = "critical"
            c.categories.add("iam" if c.service != "keyvault" else "secrets")
        if c.service == "network" or c.service in {"dns", "afd", "cdn"}:
            c.risk = "critical"
            c.categories.add("network")
        if c.service in {"sql", "postgres", "mysql", "cosmosdb", "storage", "redis"} and (c.risk == "critical" or verb in {"restore", "update"}):
            c.risk = "critical"
            c.categories.add("data")
        if (c.service, verb) in _AZ_COSTLY or (c.service, words[1] if len(words) > 1 else "") in _AZ_COSTLY:
            c.risk = "critical"
            c.categories.add("costly")


_DOCKER_READ = {"ps", "images", "inspect", "logs", "info", "version", "stats", "top", "events", "history", "port", "diff", "search", "ls", "config", "df", "show", "cves", "quickview", "manifest"}
_DOCKER_CRITICAL = {"rm", "rmi", "kill", "prune", "rmp", "remove", "rb"}


def _classify_container(p: ParsedCommand, c: Classification) -> None:
    words = p.words
    verb = words[0] if words else ""
    c.service = "engine"
    c.targets["docker_context"] = p.flag("--context") or p.env.get("DOCKER_CONTEXT", "")
    if verb in {"container", "image", "network", "volume", "system", "context", "builder", "buildx", "compose", "service", "stack", "secret", "node", "swarm", "plugin", "trust", "scout", "pod", "machine", "generate", "kube", "auto-update", "healthcheck"} and len(words) > 1:
        c.service, verb = verb, words[1]
    c.verb = verb
    if c.service == "generate" or (c.service == "kube" and verb == "generate"):
        c.risk = "plan"  # podman generate kube|systemd only writes a description
        return
    if c.service == "auto-update" and p.has("--dry-run"):
        c.risk = "plan"
        return
    if c.service == "compose":
        if verb in {"ps", "config", "logs", "images", "ls", "top", "version", "events", "port"}:
            c.risk = "read"
        elif verb in {"down", "rm"}:
            c.risk = "critical" if p.has("-v", "--volumes") or verb == "rm" else "change"
            if c.risk == "critical":
                c.categories.add("data")
        else:
            c.risk = "change"
        if p.has("--dry-run") and c.risk in {"change", "critical"}:
            # docker compose --dry-run (Compose v2.17+) only prints what it would do.
            c.risk = "plan"
            c.reasons.append("dry run")
        if p.flag("-p", "--project-name"):
            c.targets["compose_project"] = p.flag("-p", "--project-name")
        return
    if verb in _DOCKER_READ:
        c.risk = "read"
    elif verb in _DOCKER_CRITICAL or (c.service == "swarm" and verb == "leave") or (c.service == "kube" and verb == "down"):
        c.risk = "critical"
        what = f"{p.binary} {verb}" if c.service == "engine" else f"{p.binary} {c.service} {verb}"
        c.reasons.append(f"{what} removes containers, images or data")
        if c.service in {"volume", "system"} or verb == "prune":
            c.categories.add("data")
    else:
        c.risk = "change"
        if verb in {"exec", "attach"}:
            c.categories.add("remote-exec")
        if verb in {"login", "logout", "use", "create"} and c.service == "context":
            c.categories.add("local")
    if c.service == "secret" and c.mutation:
        c.categories.add("secrets")


def _classify_simple(p: ParsedCommand, c: Classification, read: set[str], critical: set[str], *, plan: set[str] = frozenset(), position: int = 0, joiner: int = 1) -> str:
    verb = p.words[position] if len(p.words) > position else ""
    if verb in {"app", "cluster", "proj", "repo", "account", "cert", "gpg", "context", "workflow", "run", "pr", "issue", "release", "secret", "variable", "cache", "ci", "mr", "pipeline", "job", "stack", "config", "state", "kv", "policy", "auth", "secrets", "token", "server", "network", "volume", "image", "user", "role", "project", "snapshot"} and len(p.words) > position + 1:
        verb = f"{verb} {p.words[position + 1]}"
    c.verb = verb
    last = verb.split(" ")[-1]
    if verb in read or last in read:
        c.risk = "read"
    elif verb in plan or last in plan:
        c.risk = "plan"
    elif verb in critical or last in critical:
        c.risk = "critical"
        c.reasons.append(f"{p.binary} {verb} removes or disables resources")
    else:
        c.risk = "change"
    return verb


def _classify_ansible(p: ParsedCommand, c: Classification) -> None:
    c.service = p.binary
    inventory = p.flag("-i", "--inventory")
    if inventory:
        c.targets["inventory"] = inventory
    if p.flag("--limit", "-l"):
        c.targets["limit"] = p.flag("--limit", "-l")
    if p.binary == "ansible-playbook":
        c.verb = "playbook"
        if p.has("--syntax-check", "--list-tasks", "--list-hosts", "--list-tags"):
            c.risk = "read"
        elif p.has("--check", "-C"):
            c.risk = "plan"
            c.reasons.append("check mode")
        else:
            c.risk = "change"
            c.categories.add("remote-exec")
        return
    if p.binary in {"ansible-doc", "ansible-inventory", "ansible-config", "ansible-lint"}:
        c.risk = "read"
        return
    if p.binary == "ansible-galaxy":
        c.risk = "read" if "list" in p.words or "info" in p.words else "change"
        c.categories.add("local")
        return
    if p.binary == "ansible-vault":
        c.risk = "read" if "view" in p.words else "change"
        c.categories.add("secrets")
        return
    module = p.flag("-m", "--module-name") or "command"
    c.verb = f"-m {module}"
    if module in {"ping", "setup", "gather_facts", "stat", "find", "debug", "service_facts", "package_facts", "ansible.builtin.ping", "ansible.builtin.setup"}:
        c.risk = "read"
    elif module in {"shell", "command", "raw", "script", "ansible.builtin.shell", "ansible.builtin.command"}:
        c.risk = "change"
        c.categories.add("remote-exec")
    else:
        c.risk = "change"
    if p.has("--check", "-C") and c.mutation:
        c.risk = "plan"


_SQL_DESTRUCTIVE = re.compile(r"\b(drop|truncate|delete|alter|grant|revoke|flushall|flushdb|del|unlink|dropdatabase|deletemany|deleteone|remove)\b", re.I)
_SQL_READ = re.compile(r"^\s*(select|show|explain|describe|\\d|\\l|\\dt|info|get|keys|scan|ping|dbsize|ttl|type|exists|db\.\w+\.find|db\.stats|rs\.status)\b", re.I)


def _classify_database(p: ParsedCommand, c: Classification) -> None:
    c.service = p.binary
    if p.binary == "pg_isready":
        c.risk = "read"
        return
    if p.binary in {"pg_dump", "pg_dumpall", "mysqldump", "mongodump"}:
        c.risk = "read"
        c.categories.add("backup")
        return
    if p.binary in {"pg_restore", "mongorestore"}:
        c.risk = "critical"
        c.categories.add("data")
        c.reasons.append("restores over existing data")
        return
    if p.binary.startswith("kafka-"):
        reads = p.has("--list", "--describe") or p.binary in {"kafka-console-consumer", "kafka-log-dirs", "kafka-broker-api-versions", "kafka-get-offsets"}
        c.risk = "read" if reads else ("critical" if p.has("--delete", "--reset-offsets") and p.has("--execute", "--delete") else "change")
        if c.risk == "critical":
            c.categories.add("data")
        return
    if p.flag("-f", "--file") or "<" in p.argv:
        c.risk = "critical"
        c.categories.add("data")
        c.reasons.append("runs a SQL file against the database")
        return
    statement = p.flag("-c", "--command", "-e", "--execute", "--eval") or (" ".join(p.words) if p.binary == "redis-cli" else "")
    c.verb = statement[:60]
    if not statement:
        c.risk = "change"
        c.categories.add("data")
        c.reasons.append("interactive database session")
    elif _SQL_DESTRUCTIVE.search(statement):
        c.risk = "critical"
        c.categories.add("data")
        c.reasons.append("destructive statement")
    elif _SQL_READ.match(statement):
        c.risk = "read"
    else:
        c.risk = "change"
        c.categories.add("data")


def _classify_linux(p: ParsedCommand, c: Classification) -> None:
    b = p.binary
    words = p.words
    c.service = b
    if b == "systemctl":
        verb = words[0] if words else "status"
        c.verb = verb
        if len(words) > 1:
            c.targets["unit"] = words[1]
        if verb in {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "show", "cat", "list-timers", "list-sockets", "list-dependencies", "--failed", "get-default"} or not words:
            c.risk = "read"
        elif verb in {"stop", "disable", "mask", "kill", "isolate", "poweroff", "reboot", "halt", "suspend", "hibernate"}:
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append(f"systemctl {verb} stops a service or the machine")
        else:
            c.risk = "change"
        return
    if b == "journalctl":
        c.risk = "critical" if any(flag.startswith("--vacuum") or flag == "--rotate" for flag in p.flags) else "read"
        return
    if b == "ip":
        mutating = {"add", "del", "delete", "flush", "change", "replace", "set", "append", "prepend", "up", "down"}
        c.verb = " ".join(words[:2])
        c.risk = "critical" if any(word in mutating for word in words[1:3]) else "read"
        if c.risk == "critical":
            c.categories.add("network")
        return
    if b in {"nft", "iptables", "ip6tables", "ufw", "firewall-cmd", "pfctl", "netsh"}:
        read_flags = {"-L", "-S", "--list", "--list-rules", "-n", "--state", "--list-all", "--get-zones", "--get-active-zones"}
        c.verb = words[0] if words else next(iter(p.flags), "")
        if (b == "nft" and words[:1] == ["list"]) or (b == "ufw" and words[:1] in (["status"], ["show"], ["app"])) or any(flag in read_flags or flag.startswith(("--list", "--get", "--query")) for flag in p.flags) and not any(flag in {"-A", "-I", "-D", "-F", "-X", "-P", "-R", "-N", "-Z", "--add-rule", "--remove-rule", "--add-port", "--remove-port", "--reload"} for flag in p.flags):
            c.risk = "read"
        else:
            c.risk = "critical"
            c.categories.add("network")
            c.reasons.append("changes firewall rules")
        return
    if b in {"reboot", "shutdown", "poweroff", "halt"}:
        c.verb = b
        if b == "shutdown" and p.has("-c"):
            c.risk = "change"  # cancels a pending shutdown
            return
        c.risk = "critical"
        c.categories.add("availability")
        c.reasons.append(f"{b} stops the machine")
        return
    if b == "cloud-init":
        verb = words[0] if words else ""
        c.verb = verb
        if verb in {"status", "query", "analyze", "collect-logs", "version"} or p.has("--version") or not verb:
            c.risk = "read"
        elif verb == "schema":
            c.risk = "plan"
        elif verb == "clean" and p.has("--reboot", "-r"):
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append("cloud-init clean --reboot re-runs the first boot")
        else:
            c.risk = "change"
        return
    if b == "sysctl":
        c.risk = "change" if p.has("-w", "-p", "--write", "--load", "--system") or any("=" in word for word in words) else "read"
        return
    if b == "hostnamectl":
        c.risk = "change" if words[:1] and words[0].startswith("set-") else "read"
        return
    if b == "exportfs":
        if p.has("-u"):
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append("exportfs -u stops sharing a folder that clients may use")
        else:
            c.risk = "change" if p.has("-a", "-r", "-ra", "-ar", "-o") or words else "read"
        return
    if b in {"wg", "wg-quick"}:
        verb = words[0] if words else "show"
        c.verb = verb
        if (b == "wg" and verb in {"show", "showconf", "genkey", "pubkey", "genpsk"}) or (b == "wg-quick" and verb == "strip"):
            c.risk = "read"
        else:
            c.risk = "critical"
            c.categories.add("network")
            c.reasons.append(f"{b} {verb} changes a VPN tunnel")
        return
    if b == "tc":
        c.verb = " ".join(words[:2])
        if len(words) <= 1 or words[1] in {"show", "list", "ls"}:
            c.risk = "read"
        else:
            c.risk = "critical"
            c.categories.add("network")
            c.reasons.append("tc changes traffic shaping on the interface")
        return
    if b in {"ss", "netstat", "dig", "nslookup", "host", "ping", "traceroute", "mtr", "tcpdump", "openssl", "whois", "arp", "nc", "ncat", "telnet", "lsof", "dmesg", "uptime", "free", "df", "top", "htop", "iostat", "vmstat", "nmap", "showmount", "nfsstat", "findmnt"}:
        c.risk = "read"
        if b == "tcpdump":
            c.categories.add("capture")
        if b == "nmap":
            c.categories.add("scan")
        return
    if b in {"curl", "wget", "http", "httpie"}:
        method = (p.flag("-X", "--request", "--method") or "").upper()
        c.verb = method or "GET"
        c.risk = "critical" if method == "DELETE" else ("change" if method in {"POST", "PUT", "PATCH"} or p.has("-d", "--data", "--data-raw", "--data-binary", "-F", "--form", "-T", "--upload-file") else "read")
        _url_target(p, c)
        return
    if b in {"ssh", "scp", "sftp", "rsync"}:
        c.categories.add("remote-exec" if b == "ssh" else "transfer")
        if b == "ssh":
            # The remote command is everything after the host, flags included.
            argv, index = p.argv, 1
            while index < len(argv) and argv[index].startswith("-"):
                index += 2 if argv[index] in {"-i", "-p", "-o", "-l", "-F", "-J", "-L", "-R", "-D", "-W", "-b", "-c", "-E", "-e", "-m", "-Q", "-S", "-w"} else 1
            if index < len(argv):
                c.targets["host"] = argv[index].rsplit("@", 1)[-1]
            inner = " ".join(argv[index + 1:])
            c.risk = "change"
            if inner:
                # One quoted argument ("sudo systemctl stop nginx", "cd x && rm y")
                # is a whole remote shell line: judge every command in it.
                nested = worst(classify_command(inner))
                if nested is not None:
                    c.risk = nested.risk
                    c.categories |= nested.categories | {"remote"}
                    c.reasons.extend(nested.reasons)
                    c.verb = nested.verb
                    for key, value in nested.targets.items():
                        c.targets.setdefault(key, value)
                elif all(segment and _binary_name(segment[0]) in _REMOTE_READS for segment in split_commands(inner)):
                    c.risk = "read"
            return
        for word in words:
            if ":" in word and not word.startswith(("/", ".")) and not re.match(r"^[a-z]+://", word):
                c.targets["host"] = word.split(":", 1)[0].rsplit("@", 1)[-1]
                break
        c.risk = "critical" if b == "rsync" and p.has("--delete", "--delete-after", "--delete-before", "--remove-source-files") else "change"
        if b == "rsync" and p.has("-n", "--dry-run"):
            c.risk = "plan"
        if c.risk == "critical":
            c.categories.add("data")
        return
    c.risk = "change"


_REMOTE_READS = {"uptime", "df", "free", "cat", "ls", "hostname", "whoami", "id", "uname", "ps", "tail", "head", "grep", "du", "stat", "date", "w", "last", "env", "echo", "pwd", "find", "lsblk", "mount", "top"}


def _url_target(p: ParsedCommand, c: Classification) -> None:
    """Record the host of the first URL, so a production endpoint is recognised."""
    for word in [*p.words, *(value for value in p.flags.values() if isinstance(value, str))]:
        match = re.match(r"^[a-z][a-z0-9+.-]*://([^/:?#]+)", word)
        if match:
            c.targets["url_host"] = match.group(1)
            return


def _classify_webserver(p: ParsedCommand, c: Classification) -> None:
    c.service = p.binary
    if p.has("-t", "-T", "-c", "-v", "-V", "--version", "-vv") and not p.has("-s"):
        c.risk = "plan" if p.has("-t", "-T", "-c") else "read"
        return
    signal = p.flag("-s")
    if signal in {"stop", "quit"}:
        c.risk = "critical"
        c.categories.add("availability")
    elif signal:
        c.risk = "change"
    elif p.binary == "traefik" and p.words[:1] in (["version"], ["healthcheck"]):
        c.risk = "read"
    else:
        c.risk = "change"
    c.verb = signal or (p.words[0] if p.words else "")


def _classify_security(p: ParsedCommand, c: Classification) -> None:
    c.service = p.binary
    if p.binary == "cmctl":
        verb = " ".join(p.words[:2]) if p.words[:1] in (["x"], ["experimental"]) else (p.words[0] if p.words else "")
        c.verb = verb
        last = verb.split(" ")[-1]
        c.risk = "read" if last in {"check", "status", "inspect", "version", "completion"} or verb.startswith("check") else ("critical" if last == "uninstall" else "change")
        return
    if p.binary == "falcoctl":
        verb = " ".join(p.words[:2])
        c.verb = verb
        c.risk = "read" if p.words[1:2] in (["list"], ["info"], ["search"]) or p.words[:1] == ["version"] else "change"
        return
    if p.binary == "vault":
        verb = _classify_simple(p, c, {"read", "list", "status", "version", "kv get", "kv list", "kv metadata", "token lookup", "policy read", "policy list", "secrets list", "auth list", "audit list", "operator raft"}, {"delete", "kv delete", "kv destroy", "kv metadata delete", "token revoke", "lease revoke", "operator seal", "secrets disable", "auth disable", "policy delete"})
        if verb in {"read", "kv get"} or verb.startswith("kv get"):
            c.categories.add("secrets")
        if c.mutation:
            c.categories.add("secrets" if verb.startswith(("kv", "write")) else "iam")
            if verb.startswith(("policy", "auth", "token", "secrets")) and c.risk == "change":
                c.risk = "critical"
        return
    c.risk = "read"
    c.verb = p.words[0] if p.words else ""
    if p.binary == "falco" and not p.words:
        c.risk = "change"


_ARGOCD_READ = {"list", "get", "history", "logs", "manifests", "resources", "version", "context", "get-user-info", "can-i", "wait", "diff", "ls", "show"}
_FLUX_READ = {"get", "logs", "check", "tree", "stats", "trace", "events", "export", "version", "diff", "build"}
_GH_READ = {"list", "view", "watch", "status", "checks", "diff", "browse", "download", "logs", "trace", "lint", "ls", "get"}
_PULUMI_READ = {"preview", "stack ls", "stack output", "stack history", "whoami", "about", "config get", "version", "logs", "stack"}
_GOVC_READ_SUFFIX = (".info", ".ls", ".find", "ls", "find", "about", "events", "logs", "tasks", ".usage", "tree", "object.collect", "env", "version")
_QM_READ = {"list", "status", "config", "pending", "listsnapshot", "showcmd", "help", "cloudinit", "agent", "terminal", "vncproxy", "mtunnel"}
_QM_DISRUPT = {"stop", "shutdown", "reboot", "reset", "suspend"}
_QM_DATA = {"destroy", "delsnapshot", "rollback", "unlock", "restore"}
_CTR_GROUPS = {"images", "image", "i", "containers", "container", "c", "tasks", "task", "t", "snapshots", "snapshot", "namespaces", "namespace", "ns", "content", "leases", "plugins", "install"}
#: kubectl plugins (krew) that only read.
_KUBE_PLUGIN_READ = {"cost", "tree", "neat", "who-can", "access-matrix", "resource-capacity", "view-allocations", "df-pv", "images", "view-utilization", "rbac-tool", "score", "popeye", "outdated", "deprecations"}
_KUBE_PLUGIN_LOCAL = {"ctx", "ns", "krew", "oidc-login", "konfig"}
_VELERO_READ = {"get", "describe", "logs", "version", "completion"}
_GENERIC_READ = {"list", "ls", "get", "show", "view", "status", "info", "describe", "logs", "version", "help", "whoami", "search", "inspect"}


def classify_segment(p: ParsedCommand | None) -> Classification | None:
    """Classify one simple command, ``None`` when it is not an infrastructure tool."""
    if p is None:
        return None
    if p.binary == "java" and p.words and p.words[0].endswith("jenkins-cli.jar"):
        # java -jar jenkins-cli.jar -s URL -auth @creds <command>
        p.binary, p.words = "jenkins-cli", p.words[1:]
    provider = p.provider
    if not provider:
        return None
    c = Classification(command=p.raw, binary=p.binary, provider=provider, parsed=p)
    b = p.binary
    if b in {"kubectl", "oc", "kind", "k3d", "minikube", "microk8s", "k3s", "rke2"}:
        _classify_kube(p, c)
    elif b == "velero":
        group = p.words[0] if p.words else ""
        verb = p.words[1] if len(p.words) > 1 else group
        c.service, c.verb = "velero", f"{group} {verb}".strip()
        if verb in _VELERO_READ or group in _VELERO_READ:
            c.risk = "read"
        elif group == "restore" and verb == "create":
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append("a restore writes resources and volumes back into the cluster")
        elif verb in {"delete"} or group == "uninstall":
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append(f"velero {c.verb} removes backups or the backup system")
        else:
            c.risk = "change"
    elif b == "kustomize":
        c.risk = "plan" if p.words[:1] == ["build"] else "change"
        c.verb = p.words[0] if p.words else ""
        if c.risk == "change":
            c.categories.add("local")
    elif b == "eksctl":
        verb = _classify_simple(p, c, {"get", "info", "version", "utils describe-stacks", "utils describe-addon-versions"}, {"delete", "drain"})
        if verb.startswith("create") and c.risk == "change":
            c.risk = "critical"
            c.categories.add("costly")
        if verb.startswith("upgrade"):
            # Without --approve eksctl only prints the upgrade plan.
            c.risk = "critical" if p.has("--approve") else "plan"
            if p.has("--approve"):
                c.categories.add("availability")
                c.reasons.append("eksctl upgrade --approve upgrades the running cluster")
        c.service = p.words[1] if len(p.words) > 1 else ""
    elif b == "helm":
        _classify_helm(p, c)
    elif b in {"terraform", "tofu"}:
        _classify_terraform(p, c)
    elif b == "pulumi":
        verb = _classify_simple(p, c, _PULUMI_READ, {"destroy", "stack rm", "state delete", "cancel"}, plan={"preview"})
        c.verb = verb
        if verb == "preview" or p.words[:1] == ["preview"]:
            c.risk = "plan"
        if p.flag("-s", "--stack"):
            c.targets["stack"] = p.flag("-s", "--stack")
    elif b == "cdk":
        verb = p.words[0] if p.words else ""
        c.verb = verb
        c.risk = {"list": "read", "ls": "read", "doctor": "read", "docs": "read", "diff": "plan", "synth": "plan", "synthesize": "plan", "destroy": "critical"}.get(verb, "change")
    elif b == "aws":
        _classify_aws(p, c)
    elif b in {"gcloud", "gsutil", "bq"}:
        if b == "bq":
            verb = p.words[0] if p.words else ""
            c.service, c.verb = "bigquery", verb
            c.risk = "read" if verb in {"ls", "show", "head", "query", "version", "help"} else ("critical" if verb in {"rm"} else "change")
            if verb == "query" and _SQL_DESTRUCTIVE.search(" ".join(p.words[1:])):
                c.risk = "critical"
            if c.risk == "critical":
                c.categories.add("data")
        else:
            _classify_gcloud(p, c)
    elif b == "az":
        _classify_az(p, c)
    elif b in {"docker", "podman", "nerdctl", "docker-compose", "podman-compose"}:
        if b.endswith("-compose"):
            p.words = ["compose", *p.words]
        _classify_container(p, c)
    elif b in {"ctr", "crictl"}:
        words = p.words
        if b == "ctr" and words[:1] and words[0] in _CTR_GROUPS and len(words) > 1:
            c.service, verb = words[0], words[1]
        else:
            verb = words[0] if words else ""
        c.verb = verb
        if verb in {"ps", "images", "inspect", "inspecti", "inspectp", "logs", "info", "version", "pods", "stats", "statsp", "ls", "list", "img", "imagefsinfo", "config", "check", "usage", "metrics"}:
            c.risk = "read"
        elif verb in {"rm", "rmi", "rmp", "stopp", "remove", "delete", "del", "prune", "kill"}:
            c.risk = "critical"
            c.reasons.append(f"{b} {verb} removes containers, pods or images on the node")
        else:
            c.risk = "change"
            if verb in {"exec", "attach"}:
                c.categories.add("remote-exec")
    elif b in {"ansible", "ansible-playbook", "ansible-doc", "ansible-inventory", "ansible-config", "ansible-galaxy", "ansible-vault", "ansible-lint"}:
        _classify_ansible(p, c)
    elif b == "argocd":
        verb = _classify_simple(p, c, _ARGOCD_READ, {"delete", "rm", "remove", "terminate-op"}, plan={"diff"})
        if "app" in p.words[:1] and len(p.words) > 2:
            c.targets["app"] = p.words[2]
        if verb.startswith(("account update-password", "proj role", "cert add", "account", "cluster add", "gpg add")) and c.mutation:
            c.risk = "critical"
            c.categories.add("iam")
            if verb.startswith("cluster add"):
                c.reasons.append("argocd cluster add installs a cluster-admin service account in the target cluster")
        if verb == "login" or p.words[:1] == ["login"]:
            c.risk = "change"
            c.categories.add("local")
    elif b == "flux":
        verb = _classify_simple(p, c, _FLUX_READ, {"delete", "uninstall"}, plan={"diff", "build"})
        if p.words[:1] == ["bootstrap"]:
            c.risk = "critical"
            c.categories.add("iam")
            c.reasons.append("flux bootstrap installs the controllers and writes a deploy key to the Git repository")
        elif p.words[:2] == ["create", "secret"]:
            c.risk = "critical"
            c.categories.add("secrets")
        del verb
    elif b == "tea":
        # tea <group> [<action>]: a group alone lists (tea issues, tea pulls).
        words = p.words
        group = words[0] if words else ""
        action = words[1] if len(words) > 1 else "list"
        c.verb, c.service = f"{group} {action}".strip(), group
        if group in {"login", "logout"}:
            c.risk = "change"
            c.categories.add("local")
        elif group in _GENERIC_READ or action in _GENERIC_READ:
            c.risk = "read"
        elif action in {"delete", "rm", "remove"}:
            c.risk = "critical"
            c.reasons.append(f"tea {group} {action} removes it from the forge")
        else:
            c.risk = "change"
        if group in {"actions", "action"} and len(words) > 2:
            # tea actions secrets|variables create|delete NAME
            kind, step = words[1], words[2]
            c.verb = f"{group} {kind} {step}"
            c.risk = "read" if step in _GENERIC_READ else ("critical" if kind in {"secrets", "secret", "variables", "variable"} else "change")
            if kind.startswith("secret") and c.mutation:
                c.categories.add("secrets")
        if p.flag("-r", "--repo"):
            c.targets["repo"] = p.flag("-r", "--repo")
    elif b in {"forgejo-runner", "act_runner"}:
        verb = p.words[0] if p.words else ""
        c.verb = verb
        c.risk = "read" if verb in {"--version", "version", "help", "generate-config"} or p.has("--version") else "change"
    elif b in {"infracost", "kubecost"}:
        verb = p.words[0] if p.words else ""
        c.verb, c.service = verb, b
        c.risk = "read" if verb in {"breakdown", "diff", "output", "--version", "version", ""} else "change"
    elif b in {"gh", "glab"}:
        verb = _classify_simple(p, c, _GH_READ, {"delete", "archive", "rm"})
        if p.words[:1] == ["api"]:
            method = (p.flag("-X", "--method") or ("POST" if p.has("-f", "-F", "--field", "--raw-field", "--input") else "GET")).upper()
            c.verb = f"api {method}"
            c.risk = "read" if method == "GET" else ("critical" if method == "DELETE" else "change")
        if verb.startswith(("secret", "variable")) and c.mutation:
            c.risk = "critical"
            c.categories.add("secrets")
        if p.words[:1] in (["auth"], ["config"], ["alias"], ["extension"]):
            c.risk = "read" if "status" in p.words or "list" in p.words else "change"
            c.categories.add("local")
        if p.flag("-R", "--repo"):
            c.targets["repo"] = p.flag("-R", "--repo")
    elif b == "jenkins-cli":
        _classify_simple(p, c, {"list-jobs", "get-job", "console", "who-am-i", "list-plugins", "version", "help", "list-views", "get-view"}, {"delete-job", "delete-builds", "delete-node", "delete-view", "safe-shutdown", "shutdown"})
    elif b == "openstack":
        words = p.words
        verb_index = next((i for i, word in enumerate(words) if word in {"list", "show", "create", "delete", "set", "unset", "add", "remove", "start", "stop", "reboot", "rebuild", "resize", "migrate", "evacuate", "lock", "unlock", "pause", "unpause", "suspend", "resume", "shelve", "unshelve", "restore", "backup", "attach", "detach", "save", "purge"}), len(words) - 1)
        verb = words[verb_index] if words else ""
        c.service, c.verb = (words[0] if words else ""), verb
        c.resource = " ".join(words[:verb_index])
        c.risk = "read" if verb in {"list", "show", "save"} else ("critical" if verb in {"delete", "remove", "evacuate", "rebuild", "unset", "purge"} else "change")
        if verb in {"stop", "reboot", "shelve", "pause", "suspend", "resize", "migrate"} and words[:1] == ["server"]:
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append(f"openstack server {verb} interrupts the instance")
        if verb == "create" and words[:1] == ["server"]:
            c.risk = "critical"
            c.categories.add("costly")
            c.reasons.append("starts billable capacity")
        if c.risk == "critical" and verb in {"delete", "remove", "purge", "rebuild"} and any(word in {"volume", "image", "snapshot", "object", "container", "backup", "share", "server", "stack"} for word in words[:verb_index]):
            c.categories.add("data")
        if c.mutation and any(word in {"network", "subnet", "router", "port", "security", "floating"} for word in words[:verb_index]):
            c.risk = "critical"
            c.categories.add("network")
        if c.mutation and c.service in {"role", "user", "project", "group", "application"}:
            c.risk = "critical"
            c.categories.add("iam")
    elif b == "govc":
        verb = p.words[0] if p.words else ""
        c.verb, c.service = verb, verb.split(".", 1)[0]
        if not verb or verb.endswith(_GOVC_READ_SUFFIX) or verb in {"ls", "find", "about", "tree", "env", "version", "events", "logs", "tasks"}:
            c.risk = "read"
        elif any(token in verb for token in ("destroy", ".rm", "remove", "unregister", "revert", "datastore.rm", ".delete")):
            c.risk = "critical"
            c.categories.add("data")
        elif (verb == "vm.power" and p.has("-off", "-s", "-r", "-reset", "-suspend")) or verb in {"host.shutdown", "host.maintenance.enter", "host.disconnect", "vm.migrate"}:
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append(f"govc {verb} interrupts running virtual machines")
        else:
            c.risk = "change"
    elif b in {"qm", "pct", "qmrestore", "vzdump", "pct-restore"}:
        verb = p.words[0] if p.words else ""
        c.verb, c.service = verb, b
        if b == "vzdump":
            c.risk, c.verb = "change", "backup"
            c.categories.add("backup")
        elif b == "qmrestore" or verb in _QM_DATA:
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append(f"{b} {verb} overwrites or removes a guest's disks".replace("  ", " "))
        elif verb in _QM_DISRUPT:
            c.risk = "critical"
            c.categories.add("availability")
            c.reasons.append(f"{b} {verb} interrupts the guest")
        elif verb in _QM_READ:
            c.risk = "read"
        else:
            c.risk = "change"
            if verb == "exec" or p.words[:2] == ["guest", "exec"] or verb == "enter":
                c.categories.add("remote-exec")
    elif b == "pvesh":
        verb = p.words[0] if p.words else ""
        c.verb = verb
        c.risk = "read" if verb in {"get", "ls", "usage", "help"} else ("critical" if verb == "delete" else "change")
    elif provider == "linux":
        _classify_linux(p, c)
    elif b in {"nginx", "haproxy", "traefik"}:
        _classify_webserver(p, c)
    elif provider == "security":
        _classify_security(p, c)
    elif b in {"promtool", "amtool", "logcli", "grafana-cli", "otelcol", "otelcol-contrib"}:
        verb = " ".join(p.words[:2])
        c.verb = verb
        if p.words[:1] in (["check"], ["validate"]) or p.words[:2] == ["check", "rules"]:
            c.risk = "plan"
        elif b == "logcli" and p.words[:2] == ["delete", "create"]:
            c.risk = "critical"
            c.categories.add("data")
            c.reasons.append("logcli delete create removes log lines")
        elif b == "logcli" or (p.words[:1] in (["query"], ["alert"], ["silence"], ["tsdb"], ["test"]) and not {"add", "expire", "import", "create-blocks-from"} & set(p.words)):
            c.risk = "read"
        else:
            c.risk = "change"
    elif provider == "databases":
        _classify_database(p, c)
    elif b in {"mc", "rclone", "ceph", "rbd", "rados", "radosgw-admin"}:
        _classify_storage(p, c)
    elif provider == "testing":
        if b == "curl":
            _classify_linux(p, c)
        elif b == "testssl.sh":
            c.risk = "read"
            c.categories.add("scan")
            _url_target(p, c)
        else:
            c.risk = "change" if b != "k6" or p.words[:1] in (["run"], ["cloud"]) else "read"
            c.categories.add("load")
            c.verb = p.words[0] if p.words else ""
            _url_target(p, c)
    elif b in {"sc", "net"}:
        _classify_windows_cli(p, c)
    elif b in {"powershell", "pwsh"}:
        script = p.flag("-Command", "-c", "-command") or " ".join(p.words)
        c.verb = script[:60]
        c.risk = "read" if re.match(r"^\s*(get-|test-|select-|measure-|resolve-dnsname|show-)", script, re.I) else ("critical" if re.search(r"\b(remove-|stop-|disable-|uninstall-|clear-|format-|restart-computer|stop-computer|suspend-)", script, re.I) else "change")
        if re.search(r"\b(invoke-command|enter-pssession|new-pssession)\b", script, re.I):
            c.categories.add("remote-exec")
        if re.search(r"\b(add-localgroupmember|new-localuser|set-acl|grant-|add-adgroupmember)", script, re.I) and c.risk != "read":
            c.risk = "critical"
            c.categories.add("iam")
    else:
        c.risk = "change"
    return c


def _classify_storage(p: ParsedCommand, c: Classification) -> None:
    b, words = p.binary, p.words
    verb = words[0] if words else ("status" if p.has("-s", "--status") else "")
    c.verb, c.service = verb, b
    if b == "ceph":
        destructive = {"rm", "purge", "destroy", "delete", "out", "down"}
        if any(word in destructive for word in words):
            c.risk = "critical"
        elif verb in {"status", "health", "df", "versions", "mon", "osd", "pg", "fs", "auth", "mgr", "log", ""} and not any(word in {"set", "unset", "reweight", "add", "create", "enable", "disable", "in", "caps", "import"} for word in words[1:]):
            c.risk = "read"
        else:
            c.risk = "change"
        if verb == "auth" and c.mutation:
            c.risk = "critical"
            c.categories.add("iam")
    elif b in {"rbd", "rados"}:
        if verb in {"rm", "remove", "purge", "rmpool", "cleanup"} or words[:2] in (["trash", "rm"], ["trash", "purge"], ["snap", "rollback"], ["snap", "purge"], ["snap", "rm"]):
            c.risk = "critical"
        elif verb in {"ls", "list", "info", "du", "status", "showmapped", "df", "lspools", "stat", "get", "listomapkeys"} or words[:2] in (["snap", "ls"], ["trash", "ls"]):
            c.risk = "read"
        else:
            c.risk = "change"
    elif b == "radosgw-admin":
        sub = " ".join(words[:2])
        c.verb = sub
        if words[1:2] and words[1] in {"rm", "remove", "purge"}:
            c.risk = "critical"
        elif words[1:2] and words[1] in {"info", "list", "stats", "show", "get", "check"}:
            c.risk = "read"
        else:
            c.risk = "change"
        if words[:1] in (["user"], ["caps"], ["key"], ["subuser"]) and c.mutation:
            c.risk = "critical"
            c.categories.add("iam")
    else:  # mc, rclone
        admin_change = verb == "admin" and any(word in {"add", "remove", "rm", "set", "attach", "detach", "enable", "disable", "create"} for word in words[1:])
        if verb in {"rm", "rb", "purge", "delete", "deletefile", "rmdir", "rmdirs", "cleanup", "dedupe"} or (verb == "sync" and b == "rclone") or (verb == "mirror" and p.has("--remove")):
            c.risk = "critical"
        elif admin_change:
            c.risk = "critical" if words[1:2] and words[1] in {"user", "policy", "group", "service-account", "accesskey", "idp"} else "change"
            if c.risk == "critical":
                c.categories.add("iam")
        elif verb in {"ls", "lsd", "lsl", "cat", "stat", "du", "size", "tree", "find", "admin", "version", "config", "about", "check", "md5sum", "sha1sum", "diff", "ilm", "head"} and not (verb == "ilm" and any(word in {"add", "rm", "edit", "import"} for word in words[1:])):
            c.risk = "read"
        else:
            c.risk = "change"
        if p.has("--dry-run") and c.risk in {"change", "critical"}:
            c.risk = "plan"
    if c.risk == "critical" and "iam" not in c.categories:
        c.categories.add("data")


def _classify_windows_cli(p: ParsedCommand, c: Classification) -> None:
    """``sc.exe`` and ``net`` on Windows Server."""
    words = [word.lower() for word in p.words]
    verb = words[0] if words else ""
    c.verb, c.service = verb, p.binary
    if len(words) > 1 and verb in {"stop", "start", "config", "delete", "query", "queryex", "qc", "failure"}:
        c.targets["unit"] = p.words[1]
    if p.binary == "sc":
        c.risk = "read" if verb in {"query", "queryex", "qc", "qdescription", "qfailure", "enumdepend", "getdisplayname", "getkeyname", "sdshow"} else ("critical" if verb in {"stop", "delete", "pause", "sdset"} else "change")
    else:
        if verb == "stop" or verb == "pause":
            c.risk = "critical"
        elif verb in {"user", "localgroup", "group", "accounts"}:
            mutating = any(word in {"/add", "/delete", "/del"} for word in words) or (verb == "user" and len(words) > 2)
            c.risk = "critical" if mutating else "read"
            if mutating:
                c.categories.add("iam")
        elif verb in {"start", "use", "share", "time", "config", "session", "view", "statistics"}:
            c.risk = "read" if (verb in {"use", "share", "session"} and len(words) == 1) or verb in {"view", "statistics", "config"} else "change"
        else:
            c.risk = "change"
    if c.risk == "critical" and verb in {"stop", "pause"}:
        c.categories.add("availability")
        c.reasons.append(f"{p.binary} {verb} stops a Windows service")


def classify_command(command: str) -> list[Classification]:
    """Every infrastructure command of a line (non-infra commands are skipped)."""
    out = []
    for parsed in parse_command(command):
        classified = classify_segment(parsed)
        if classified is not None:
            out.append(classified)
    return out


def worst(classifications: list[Classification]) -> Classification | None:
    if not classifications:
        return None
    return max(classifications, key=lambda c: RISK_ORDER.get(c.risk, 2))


def _hint_pattern() -> re.Pattern[str]:
    """Every binary the classifier knows, so the exec gate never misses one.

    ``curl`` and ``wget`` are left out: they are general web tools, judged as
    infrastructure only inside the broker or behind ssh.
    """
    names = {name for binaries in PROVIDER_BINARIES.values() for name in binaries} - _UNGATED
    alternatives = sorted((re.escape(name) for name in names), key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join([*alternatives, r"kafka-[\w-]+", r"ansible[\w-]*", r"jenkins-cli"]) + r")(\.exe)?(?![\w-])")


_UNGATED = {"curl", "wget"}
_INFRA_HINT = _hint_pattern()


def mentions_infra(command: str) -> bool:
    """Cheap pre-check before the full parse."""
    return bool(_INFRA_HINT.search(command or ""))
