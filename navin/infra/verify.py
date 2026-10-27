# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Verification & Recovery Engine.

- :func:`verification_plan`: the commands that prove a change worked (rollout
  status, helm status, a clean terraform plan, the describe call of what was
  created, a health check), run right after the change.
- :func:`snapshot_plan`: what to capture before a change so it can be undone
  (the object's YAML, the Helm revision, the Terraform state, the unit state).
- :func:`diagnose`: the known failure patterns of each platform, with the fix.
- :func:`recovery_plan`: the way back, from the snapshot and the registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from navin.infra.parse import Classification, ParsedCommand


@dataclass
class Check:
    argv: list[str]
    expect: str = "exit0"  # exit0 | notfound | contains:<text> | exit0or2-no-drift
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"command": " ".join(self.argv), "expect": self.expect, "why": self.why}


@dataclass
class SnapshotSpec:
    argv: list[str]
    kind: str  # yaml | helm | tfstate | json | text
    restore: list[str] = field(default_factory=list)
    note: str = ""


def _kube_scope(p: ParsedCommand) -> list[str]:
    out = []
    for name in ("--context", "--kubeconfig"):
        if p.flag(name):
            out += [name, p.flag(name)]
    namespace = p.flag("-n", "--namespace")
    if namespace:
        out += ["-n", namespace]
    return out


def _helm_scope(p: ParsedCommand) -> list[str]:
    out = []
    if p.flag("--kube-context"):
        out += ["--kube-context", p.flag("--kube-context")]
    namespace = p.flag("-n", "--namespace")
    if namespace:
        out += ["-n", namespace]
    return out


def _manifest_workloads(path: Path) -> list[tuple[str, str, str]]:
    """(kind, name, namespace) of the Deployments / StatefulSets / DaemonSets in a manifest file or folder."""
    files = [path] if path.is_file() else sorted(path.glob("*.y*ml")) if path.is_dir() else []
    found = []
    for file in files[:40]:
        try:
            documents = list(yaml.safe_load_all(file.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            # A Helm or Jinja template is not plain YAML: its workloads get no rollout check.
            logger.info("infra: no rollout check for {}: {}", file, exc.__class__.__name__)
            continue
        for doc in documents:
            if isinstance(doc, dict) and doc.get("kind") in {"Deployment", "StatefulSet", "DaemonSet"}:
                meta = doc.get("metadata") or {}
                found.append((doc["kind"].lower(), str(meta.get("name", "")), str(meta.get("namespace", ""))))
    return [item for item in found if item[1]]


def verification_plan(c: Classification, cwd: str | Path | None = None) -> list[Check]:
    """Commands that prove the change in ``c`` worked (empty: nothing reliable to run)."""
    p = c.parsed
    if p is None or not c.mutation:
        return []
    b, words = p.binary, p.words
    verb = words[0] if words else ""
    if b in {"kubectl", "oc"}:
        scope = _kube_scope(p)
        if verb in {"apply", "create", "replace"} and p.flag("-f", "--filename"):
            target = p.flag("-f", "--filename")
            checks = [Check([b, "get", "-f", target, *scope], why="the objects exist")]
            path = Path(cwd or ".") / target
            for kind, name, namespace in _manifest_workloads(path):
                ns = ["-n", namespace] if namespace and "-n" not in scope else []
                checks.append(Check([b, "rollout", "status", f"{kind}/{name}", "--timeout=300s", *scope, *ns], why=f"{kind} {name} rolled out"))
            return checks
        if verb == "rollout" and len(words) > 2 and words[1] in {"restart", "undo", "resume"}:
            return [Check([b, "rollout", "status", words[2], "--timeout=300s", *scope], why="the rollout finished")]
        if verb in {"scale", "set", "patch"} and len(words) > 1:
            target = words[1] if "/" in words[1] or len(words) < 3 else f"{words[1]}/{words[2]}"
            if verb == "set" and len(words) > 2:
                target = words[2] if "/" in words[2] else (f"{words[2]}/{words[3]}" if len(words) > 3 else words[2])
            kind = target.split("/", 1)[0]
            if kind.startswith(("deploy", "statefulset", "sts", "daemonset", "ds")):
                return [Check([b, "rollout", "status", target, "--timeout=300s", *scope], why="the rollout finished")]
            return [Check([b, "get", target, *scope], why="the object is readable")]
        if verb == "delete" and len(words) > 1:
            target = words[1:3] if "/" not in words[1] else [words[1]]
            return [Check([b, "get", *target, *scope], expect="notfound", why="the object is gone")]
        if verb in {"drain", "cordon"} and len(words) > 1:
            return [Check([b, "get", "node", words[1], *scope], expect="contains:SchedulingDisabled", why="the node is cordoned")]
        if verb == "uncordon" and len(words) > 1:
            return [Check([b, "get", "node", words[1], *scope], why="the node is back")]
        return []
    if b == "helm" and verb in {"install", "upgrade", "rollback"} and len(words) > 1:
        return [Check(["helm", "status", words[1], *_helm_scope(p)], expect="contains:STATUS: deployed", why="the release is deployed")]
    if b == "helm" and verb in {"uninstall", "delete", "un"} and len(words) > 1:
        return [Check(["helm", "status", words[1], *_helm_scope(p)], expect="notfound", why="the release is gone")]
    if b in {"terraform", "tofu"} and verb == "apply":
        chdir = [f"-chdir={p.flag('-chdir')}"] if p.flag("-chdir") else []
        return [Check([b, *chdir, "plan", "-detailed-exitcode", "-input=false", "-lock=false"], expect="exit0", why="no drift left after the apply")]
    if b in {"docker", "podman", "nerdctl"}:
        if verb == "run" and p.flag("--name"):
            return [Check([b, "ps", "--filter", f"name={p.flag('--name')}", "--format", "{{.Status}}"], expect="contains:Up", why="the container runs")]
        if verb == "compose" and len(words) > 1 and words[1] in {"up", "restart", "start"}:
            extra = [arg for name in ("-f", "--file", "-p", "--project-name") if p.flag(name) for arg in (name, p.flag(name))]
            return [Check([b, "compose", *extra, "ps", "--status", "running", "--quiet"], why="the services run")]
    if b == "systemctl" and verb in {"start", "restart", "reload", "try-restart", "enable"} and len(words) > 1:
        return [Check(["systemctl", "is-active", words[1]], expect="contains:active", why="the unit is active")]
    if b == "systemctl" and verb == "stop" and len(words) > 1:
        return [Check(["systemctl", "is-active", words[1]], expect="contains:inactive", why="the unit stopped")]
    if b == "nginx" and p.flag("-s") == "reload":
        return [Check(["nginx", "-t"], why="the configuration is valid")]
    if b == "argocd" and words[:2] == ["app", "sync"] and len(words) > 2:
        return [Check(["argocd", "app", "wait", words[2], "--health", "--sync", "--timeout", "600"], why="the application is synced and healthy")]
    if b == "flux" and verb == "reconcile" and len(words) > 2:
        return [Check(["flux", "get", words[1], words[2]], expect="contains:True", why="the object is ready")]
    if b == "openstack" and words[:1] == ["server"] and len(words) > 2:
        action, name = words[1], words[-1]
        if action == "delete":
            return [Check(["openstack", "server", "show", name], expect="notfound", why="the server is gone")]
        if action == "create" and not p.has("--wait"):
            return [Check(["openstack", "server", "show", name, "-f", "value", "-c", "status"], why="the server exists (still building without --wait)")]
        state = {"stop": "SHUTOFF", "shelve": "SHELVED", "suspend": "SUSPENDED", "pause": "PAUSED"}.get(action, "ACTIVE")
        return [Check(["openstack", "server", "show", name, "-f", "value", "-c", "status"], expect=f"contains:{state}", why=f"the server is {state}")]
    if b == "openstack" and words[:1] == ["stack"] and len(words) > 2 and words[1] in {"create", "update"} and p.has("--wait"):
        return [Check(["openstack", "stack", "show", words[-1], "-f", "value", "-c", "stack_status"], expect="contains:COMPLETE", why="the stack finished")]
    if b == "govc" and words[:1] == ["vm.power"] and len(words) > 1:
        state = "poweredOff" if p.has("-off", "-s") else ("suspended" if p.has("-suspend") else "poweredOn")
        return [Check(["govc", "vm.info", words[-1]], expect=f"contains:{state}", why=f"the VM is {state}")]
    if b == "govc" and words[:1] in (["snapshot.create"], ["snapshot.revert"], ["snapshot.remove"]) and p.flag("-vm"):
        return [Check(["govc", "snapshot.tree", "-vm", p.flag("-vm")], why="the snapshot tree is readable")]
    if b in {"qm", "pct"} and len(words) > 1:
        action, vmid = words[0], words[1]
        if action in {"start", "resume", "reboot"}:
            return [Check([b, "status", vmid], expect="contains:running", why="the guest runs")]
        if action in {"stop", "shutdown"}:
            return [Check([b, "status", vmid], expect="contains:stopped", why="the guest is stopped")]
        if action == "snapshot" and len(words) > 2:
            return [Check([b, "listsnapshot", vmid], expect=f"contains:{words[2]}", why="the snapshot is listed")]
        if action == "destroy":
            return [Check([b, "status", vmid], expect="notfound", why="the guest is gone")]
        if action in {"rollback", "set", "resize", "migrate", "clone"}:
            return [Check([b, "status", vmid], why="the guest answers")]
    if b == "ansible-playbook" and not p.has("--check", "-C", "--syntax-check", "--list-hosts", "--list-tasks"):
        return [Check([*p.argv, "--check"], expect="contains:changed=0", why="a second pass in check mode finds nothing left to change")]
    if b == "pulumi" and words[:1] in (["up"], ["update"]):
        stack = ["-s", p.flag("-s", "--stack")] if p.flag("-s", "--stack") else []
        return [Check(["pulumi", "preview", "--expect-no-changes", *stack], why="no change is left between the code and the stack")]
    if b == "cdk" and words[:1] == ["deploy"]:
        return [Check(["cdk", "diff", "--fail", *words[1:]], why="the deployed stacks match the code")]
    if b == "velero" and words[:2] == ["backup", "create"] and len(words) > 2:
        return [Check(["velero", "backup", "get", words[2]], why="the backup exists")]
    if b == "velero" and words[:2] in (["restore", "create"], ["schedule", "create"]):
        return [Check(["velero", words[0], "get"], why=f"the {words[0]} is listed")]
    if b == "vault" and words[:2] == ["kv", "put"] and len(words) > 2:
        mount = [f"-mount={p.flag('-mount')}"] if p.flag("-mount") else []
        # metadata, not the value: the check never prints the secret.
        return [Check(["vault", "kv", "metadata", "get", *mount, words[2]], why="the new version is recorded")]
    if b == "vault" and words[:2] == ["policy", "write"] and len(words) > 2:
        return [Check(["vault", "policy", "read", words[2]], why="the policy is stored")]
    if b in {"sc", "net"} and len(words) > 1 and words[0].lower() in {"start", "stop"}:
        if b == "sc":
            # sc returns before the service settles: only check that it answers.
            return [Check([p.argv[0], "query", words[1]], why="the service answers")]
        state = "RUNNING" if words[0].lower() == "start" else "STOPPED"
        return [Check(["sc.exe", "query", words[1]], expect=f"contains:{state}", why=f"the service is {state.lower()}")]
    if b == "gcloud":
        clean = [word for word in words if word not in {"alpha", "beta"}]
        for index, word in enumerate(clean):
            if word in {"start", "stop", "reset", "resume", "suspend", "restart", "upgrade", "failover"} and index + 1 < len(clean):
                where = [f"{name}={p.flag(name)}" for name in ("--zone", "--region", "--project") if p.flag(name)]
                target = clean[index + 1]
                if clean[:2] == ["compute", "instances"]:
                    state = {"stop": "TERMINATED", "suspend": "SUSPENDED"}.get(word, "RUNNING")
                    return [Check(["gcloud", "compute", "instances", "describe", target, *where, "--format=value(status)"], expect=f"contains:{state}", why=f"the instance is {state}")]
                if clean[:2] == ["sql", "instances"]:
                    return [Check(["gcloud", "sql", "instances", "describe", target, *where, "--format=value(state)"], expect="contains:RUNNABLE", why="the database is up")]
                return [Check(["gcloud", *clean[:index], "describe", target, *where], why="the resource answers")]
            if word in {"create", "update", "deploy", "patch", "resize", "create-auto"} and index + 1 < len(clean):
                location = [arg for name in ("--zone", "--region", "--project", "--location") if p.flag(name) for arg in (f"{name}={p.flag(name)}",)]
                return [Check(["gcloud", *clean[:index], "describe", clean[index + 1], *location], why="the resource exists")]
            if word == "delete" and index + 1 < len(clean):
                location = [f"{name}={p.flag(name)}" for name in ("--zone", "--region", "--project", "--location") if p.flag(name)]
                return [Check(["gcloud", *clean[:index], "describe", clean[index + 1], *location], expect="notfound", why="the resource is gone")]
    if b == "az":
        # Groups and verb are the words before the first flag.
        lead: list[str] = []
        for token in p.argv[1:]:
            if token.startswith("-"):
                break
            lead.append(token)
        names = ("-n", "--name", "-g", "--resource-group", "--cluster-name", "--vault-name", "--subscription")
        args = [arg for name in names if p.flag(name) for arg in (name, p.flag(name))]
        if len(lead) >= 2 and args:
            action, group = lead[-1], lead[:-1]
            if group == ["vm"] and action in {"start", "stop", "restart", "deallocate"}:
                state = {"start": "running", "restart": "running", "stop": "stopped", "deallocate": "deallocated"}[action]
                return [Check(["az", "vm", "show", "-d", *args, "--query", "powerState", "-o", "tsv"], expect=f"contains:{state}", why=f"the VM is {state}")]
            if group[:1] in (["aks"], ["vmss"]) and action in {"upgrade", "scale", "update"}:
                return [Check(["az", *group, "show", *args, "--query", "provisioningState", "-o", "tsv"], expect="contains:Succeeded", why="the update finished")]
            if action in {"create", "update"}:
                return [Check(["az", *group, "show", *args], why="the resource exists")]
    if b == "aws":
        return _aws_checks(p)
    return []


def _aws_checks(p: ParsedCommand) -> list[Check]:
    words = p.words
    if len(words) < 2:
        return []
    service, op = words[0], words[1]
    scope = [arg for name in ("--profile", "--region") if p.flag(name) for arg in (name, p.flag(name))]
    if service == "s3" and op in {"cp", "sync", "mv"} and len(words) > 3 and words[-1].startswith("s3://"):
        return [Check(["aws", "s3", "ls", words[-1], *scope], why="the objects are there")]
    try:
        from botocore.exceptions import BotoCoreError
    except ImportError:
        return []  # without the AWS models no check can be derived; the report says verified=None
    from navin.infra.registry import _botocore_session, aws_counterparts, kebab

    try:
        model = _botocore_session().get_service_model("s3" if service == "s3api" else service)
    except BotoCoreError as exc:
        logger.info("infra: no AWS model for {}: {}", service, exc)
        return []
    names = {kebab(name): name for name in model.operation_names}
    name = names.get(op)
    if name is None:
        return []
    counterpart = aws_counterparts(service, name, model.operation_names)["verify"]
    if not counterpart:
        return []
    check_op = counterpart[0].split()[2]
    check_name = names.get(check_op)
    if check_name is None:
        return []
    shape = model.operation_model(check_name).input_shape
    members = {f"--{kebab(member)}" for member in (shape.members if shape is not None else {})}
    carried = [arg for flag, value in p.flags.items() if flag in members and isinstance(value, str) for arg in (flag, value)]
    required = {f"--{kebab(member)}" for member in (getattr(shape, "required_members", None) or [])} if shape is not None else set()
    if required - {flag for flag in p.flags}:
        return []  # the check would need an identifier the command did not name
    return [Check(["aws", service, check_op, *carried, *scope], why=f"{check_op} answers")]


def check_passed(check: Check, exit_code: int, output: str) -> bool:
    text = output or ""
    if check.expect == "notfound":
        return exit_code != 0 and bool(re.search(r"not ?found|NotFound|does not exist|release: not found|was not found|No resources found|No \w+ with a name or ID|404", text, re.I))
    if check.expect.startswith("contains:"):
        return exit_code == 0 and check.expect.split(":", 1)[1] in text
    return exit_code == 0


# -- snapshots ------------------------------------------------------------------------------


def snapshot_plan(c: Classification) -> SnapshotSpec | None:
    """What to capture before the change in ``c``, and how to put it back."""
    p = c.parsed
    if p is None or not c.mutation:
        return None
    b, words = p.binary, p.words
    verb = words[0] if words else ""
    if b in {"kubectl", "oc"} and verb in {"apply", "create", "replace", "patch", "scale", "set", "label", "annotate", "delete", "edit", "rollout", "autoscale"}:
        scope = _kube_scope(p)
        if p.flag("-f", "--filename"):
            return SnapshotSpec([b, "get", "-f", p.flag("-f", "--filename"), "-o", "yaml", "--ignore-not-found", *scope], "yaml", [b, "apply", "-f", "{snapshot}", *scope])
        target = words[2] if verb in {"rollout", "set"} and len(words) > 2 else (words[1] if len(words) > 1 else "")
        if verb != "rollout" and verb != "set" and len(words) > 2 and "/" not in words[1]:
            target = f"{words[1]}/{words[2]}"
        if target:
            return SnapshotSpec([b, "get", target, "-o", "yaml", "--ignore-not-found", *scope], "yaml", [b, "apply", "-f", "{snapshot}", *scope])
        return None
    if b == "helm" and verb in {"upgrade", "rollback", "uninstall", "delete", "un"} and len(words) > 1:
        scope = _helm_scope(p)
        return SnapshotSpec(["helm", "history", words[1], "-o", "json", "--max", "5", *scope], "helm", ["helm", "rollback", words[1], "{revision}", "--wait", *scope],
                            note="helm uninstall without --keep-history drops the history: the values are captured too")
    if b in {"terraform", "tofu"} and (verb in {"apply", "destroy", "import", "taint", "untaint"} or (verb == "state" and len(words) > 1 and words[1] in {"rm", "mv", "push", "replace-provider"})):
        chdir = [f"-chdir={p.flag('-chdir')}"] if p.flag("-chdir") else []
        return SnapshotSpec([b, *chdir, "state", "pull"], "tfstate", [b, *chdir, "state", "push", "{snapshot}"],
                            note="pushing an old state is a last resort for a damaged state; the normal way back is reverting the code and applying again")
    if b in {"docker", "podman"} and verb in {"rm", "stop", "kill", "update", "rename"} and len(words) > 1:
        return SnapshotSpec([b, "inspect", words[1]], "json", [], note="recreate the container from the captured inspect output")
    if b == "systemctl" and verb in {"stop", "disable", "mask", "restart", "start", "enable"} and len(words) > 1:
        return SnapshotSpec(["systemctl", "show", words[1], "--property=ActiveState,UnitFileState"], "text",
                            ["systemctl", "start" if verb in {"stop"} else ("enable" if verb in {"disable"} else ("unmask" if verb == "mask" else "status")), words[1]])
    if b == "aws" and len(words) > 1:
        return None
    return None


_VOLATILE = ("resourceVersion", "uid", "creationTimestamp", "generation", "managedFields", "selfLink")


def clean_kube_yaml(text: str) -> str:
    """Drop server-owned fields so the snapshot can be applied back.

    Output that is not YAML is kept as captured: the snapshot stays usable by
    hand, it just cannot be cleaned.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.info("infra: snapshot kept raw, not YAML ({})", exc.__class__.__name__)
        return text
    if not isinstance(data, dict):
        return text
    items = data.get("items") if data.get("kind") == "List" else [data]
    for item in items or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        for name in _VOLATILE:
            meta.pop(name, None)
        annotations = meta.get("annotations") or {}
        annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)
        if not annotations:
            meta.pop("annotations", None)
        item.pop("status", None)
    return yaml.safe_dump(data, sort_keys=False)


# -- diagnosis -------------------------------------------------------------------------------

_PATTERNS: list[tuple[str, str, str]] = [
    (r"Token has expired and refresh failed|The SSO session associated with this profile has expired|Error loading SSO Token|sso.*expired", "AWS SSO session expired", "Run aws sso login --profile <profile> (IAM Identity Center), then retry."),
    (r"ExpiredToken|RequestExpired|security token included in the request is expired", "AWS credentials expired", "Refresh the temporary credentials: aws sso login --profile <profile>, or assume the role again."),
    (r"Unable to locate credentials|NoCredentialProviders|could not find credentials", "No AWS credentials", "Sign in with aws configure sso then aws sso login --profile <profile>; avoid long-lived access keys."),
    (r"AccessDenied|UnauthorizedOperation|AuthorizationError|is not authorized to perform|AccessDeniedException", "AWS permission missing", "The role lacks the IAM action named in the error: check it with infra action=access, or ask for a scoped policy; never widen to *:*."),
    (r"Reauthentication required|gcloud auth login|There was a problem refreshing your current auth tokens|invalid_grant", "Google Cloud login expired", "Run gcloud auth login (or gcloud auth application-default login for SDKs)."),
    (r"PERMISSION_DENIED|does not have .* permission|Required '[\w.]+' permission", "Google Cloud permission missing", "Grant the missing permission to the impersonated service account, scoped to the project."),
    (r"AADSTS\d+|Please run 'az login'|az login", "Azure login required", "Run az login (or az login --identity on Azure hosts) and az account set --subscription <id>."),
    (r"AuthorizationFailed", "Azure permission missing", "The principal lacks a role assignment on the scope in the error."),
    (r"Unable to connect to the server|connection refused|no such host|i/o timeout|dial tcp", "Cluster or endpoint unreachable", "Check the context (kubectl config current-context), the VPN or bastion, and the API endpoint; for EKS refresh with aws eks update-kubeconfig."),
    (r"You must be logged in to the server|Unauthorized|the server has asked for the client to provide credentials", "Cluster credentials rejected", "Refresh the kubeconfig credentials (aws eks update-kubeconfig / gcloud container clusters get-credentials / az aks get-credentials)."),
    (r"forbidden: User .* cannot|is forbidden", "Kubernetes RBAC denied", "Check with kubectl auth can-i <verb> <resource> -n <ns>; request a namespaced Role, not cluster-admin."),
    (r"ImagePullBackOff|ErrImagePull|pull access denied|manifest unknown", "Image cannot be pulled", "Check the image name and tag, the registry credentials (imagePullSecrets / node IAM role) and that the tag was pushed."),
    (r"CrashLoopBackOff|Back-off restarting failed container", "Container keeps crashing", "Read kubectl logs <pod> --previous and kubectl describe pod <pod>: config, secrets, probes or the command."),
    (r"OOMKilled", "Out of memory", "Raise the memory limit or fix the leak; check kubectl top pod."),
    (r"Insufficient (cpu|memory)|0/\d+ nodes are available|FailedScheduling", "Pods cannot be scheduled", "Not enough capacity or a selector/taint mismatch: kubectl describe pod, then scale the node group or adjust requests."),
    (r"progress deadline exceeded|exceeded its progress deadline|timed out waiting for the condition", "Rollout did not finish", "Inspect kubectl rollout status and the new pods' events; roll back with kubectl rollout undo if the release is bad."),
    (r"another operation \(install/upgrade/rollback\) is in progress", "Helm release locked", "A previous Helm operation is stuck: helm history <release>, then helm rollback <release> <last-good-revision>."),
    (r"UPGRADE FAILED|INSTALLATION FAILED", "Helm operation failed", "Read the error above; with --atomic Helm rolled back already, otherwise helm rollback <release> <previous>."),
    (r"Error acquiring the state lock|state lock", "Terraform state locked", "Someone else holds the lock: wait, or after checking nobody runs, terraform force-unlock <id> (asks you)."),
    (r"Error: Inconsistent dependency lock file|Required plugins are not installed|terraform init", "Terraform not initialized", "Run terraform init (-upgrade if the lock file changed)."),
    (r"already exists|AlreadyExists|EntityAlreadyExists|ResourceInUseException", "Resource already exists", "Import it (terraform import / kubectl apply instead of create) or pick another name."),
    (r"LimitExceeded|QuotaExceeded|quota|ServiceQuotaExceeded|RESOURCE_EXHAUSTED", "Quota reached", "Request a quota increase or free capacity; check aws service-quotas / gcloud compute project-info describe."),
    (r"x509|certificate (has expired|is not valid|signed by unknown)|SSL certificate problem", "TLS certificate problem", "Check the certificate chain and dates (openssl s_client -connect host:443 -servername host) and the CA bundle."),
    (r"No space left on device", "Disk full", "Free space (docker system df, journalctl --vacuum-size) or grow the volume."),
    (r"context deadline exceeded|timed out|Timeout", "Timeout", "The target is slow or unreachable: check network paths and retry with a longer timeout."),
    (r"command not found|executable file not found|No such file or directory: '[\w-]+'", "Tool missing", "Install the CLI (infra action=status lists what is present)."),
    (r"Cannot connect to the Docker daemon|docker daemon is not running|permission denied while trying to connect to the Docker daemon", "Docker daemon unreachable", "Start Docker (systemctl start docker / Docker Desktop) or add the user to the docker group."),
    (r"Throttling|Rate exceeded|TooManyRequests|429", "Rate limited", "Back off and retry; batch the calls."),
    (r"InvalidParameter|ValidationError|validation failed|is invalid", "Invalid parameters", "Read infra action=describe for the operation: required parameters and allowed values."),
]


def diagnose(text: str) -> list[dict[str, str]]:
    """Known causes of a failure in ``text`` (most specific first), with their fix."""
    found = []
    for pattern, title, advice in _PATTERNS:
        match = re.search(pattern, text or "", re.I)
        if match:
            found.append({"cause": title, "advice": advice, "evidence": match.group(0)[:160]})
        if len(found) >= 3:
            break
    return found


def recovery_plan(c: Classification, snapshot: dict[str, Any] | None, rollback_hints: list[str]) -> list[str]:
    """The way back, most direct first."""
    steps = []
    if snapshot and snapshot.get("restore"):
        steps.append("restore the snapshot: " + " ".join(snapshot["restore"]))
    p = c.parsed
    if p is not None and p.binary in {"kubectl", "oc"} and p.words[:1] in (["apply"], ["set"], ["patch"]) or (p is not None and p.words[:2] == ["rollout", "restart"]):
        target = next((word for word in (p.words[2:3] if p.words[:1] in (["set"], ["rollout"]) else p.words[1:2]) if "/" in word), "")
        if target:
            steps.append(f"roll the workload back: {p.binary} rollout undo {target}")
    steps.extend(hint for hint in rollback_hints if hint not in steps)
    if not steps:
        steps.append("no automatic way back is known for this operation: restore from the last backup, then re-apply the last known good configuration")
    return steps
