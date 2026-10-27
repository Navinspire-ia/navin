# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Automatic discovery: build the registry from what the machine really has.

Several mechanisms are combined, because no single one covers everything:

- AWS: every operation modelled by the installed botocore (the SDK the CLI is
  built on) and, with ``deep``, the resource types of Cloud Control API.
- Google Cloud: the gcloud command tree shipped with the SDK and, with
  ``deep``, the Google API Discovery directory.
- Kubernetes / OpenShift: the cluster's discovery API (``api-resources``),
  CRDs installed by operators included, and the server version.
- Terraform / OpenTofu: the CLI and the provider schemas of initialized
  modules (every resource type with its required and optional arguments).
- Ansible: ``ansible-doc -l``; OpenStack: ``openstack command list``.
- Everything else (Docker, Podman, Helm, Argo CD, Flux, gh, glab, Pulumi,
  eksctl, crictl, Azure, govc, Proxmox): the CLI's own help tree.

A new service, a new CLI command or a new CRD becomes usable after one
``discover``: no skill has to be written for it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navin.infra.model import DISCOVERABLE, PROVIDER_BINARIES, Operation, effects_for
from navin.infra.parse import classify_command, worst
from navin.infra.registry import Registry, kebab, which
from navin.infra.runner import Runner, RunResult, default_runner


@dataclass
class DiscoveryResult:
    provider: str
    ops: list[Operation] = field(default_factory=list)
    version: str = ""
    source: str = ""
    errors: list[str] = field(default_factory=list)
    context: str = ""
    available: bool = True
    duration_ms: int = 0
    services: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "count": len(self.ops), "available": self.available, "version": self.version,
            "source": self.source, "errors": self.errors[:8], "duration_ms": self.duration_ms, "context": self.context,
        }


def _risk(command: str) -> tuple[str, list[str]]:
    classified = worst(classify_command(command))
    if classified is None:
        return "change", []
    return classified.risk, sorted(classified.categories)


def _first_line(result: RunResult) -> str:
    for line in (result.stdout or result.stderr).splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def _error_of(result: RunResult, what: str) -> str:
    from navin.infra.verify import diagnose

    found = diagnose(result.output)
    hint = f" {found[0]['advice']}" if found else ""
    tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [f"exit {result.exit_code}"]
    return f"{what}: {tail[0][:240]}{hint}"


# -- AWS ----------------------------------------------------------------------------


def discover_aws(runner: Runner, *, deep: bool = False, context: str = "", workdir: str = "", deadline: float = 0, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("aws", source="botocore" + (" + cloudcontrol" if deep else ""))
    try:
        import botocore
        import botocore.exceptions
        import botocore.model
        import botocore.session
    except ImportError:
        result.errors.append("botocore is not installed: pip install botocore (or awscli) to read the AWS service models")
        result.available = bool(which("aws"))
        return result
    session = botocore.session.get_session()
    # The SDK models make every operation known; only the CLI makes them runnable here.
    result.available = bool(which("aws"))
    if not result.available:
        result.errors.append("the aws CLI is not installed here: the operations are known from the SDK models; install AWS CLI v2 to run them")
    cli_version = _first_line(runner(["aws", "--version"], timeout=20)) if result.available else ""
    result.version = f"botocore {botocore.__version__}" + (f", {cli_version}" if cli_version else "")
    result.context = context or os.environ.get("AWS_PROFILE", "default")
    for service in session.get_available_services():
        if deadline and time.monotonic() > deadline:
            result.errors.append("time budget reached: some AWS services were not read; run discover provider=aws again")
            break
        try:
            model = session.get_service_model(service)
        except (botocore.exceptions.BotoCoreError, ValueError) as exc:
            # One damaged service model is reported; the others are still read.
            result.errors.append(f"{service}: {exc}")
            continue
        cli = "s3api" if service == "s3" else service
        prefix = model.signing_name or model.endpoint_prefix
        result.services[cli] = {"api_version": model.api_version, "endpoint_prefix": model.endpoint_prefix, "protocol": model.protocol, "iam_prefix": prefix}
        for name in model.operation_names:
            command = f"aws {cli} {kebab(name)}"
            risk, categories = _risk(command)
            try:
                shape = model.operation_model(name).input_shape
                required = [f"--{kebab(member)}" for member in (getattr(shape, "required_members", None) or [])] if shape is not None else []
            except (botocore.model.OperationNotFoundError, botocore.model.NoShapeFoundError,
                    botocore.model.InvalidShapeError, botocore.model.InvalidShapeReferenceError) as exc:
                result.errors.append(f"{service} {name}: parameters unreadable ({exc})")
                required = []
            result.ops.append(Operation(
                id=f"aws:{service}:{name}", provider="aws", service=cli, name=name, command=command,
                required=required, permissions=[f"{prefix}:{name}"], version=f"API {model.api_version}",
                risk=risk, categories=categories, source="botocore",
            ))
    if deep:
        _cloud_control_types(runner, result, context)
    return result


def _cloud_control_types(runner: Runner, result: DiscoveryResult, profile: str) -> None:
    """Resource types Cloud Control can create, read, update, delete and list."""
    base = ["aws", "cloudformation", "list-types", "--visibility", "PUBLIC", "--type", "RESOURCE", "--output", "json"]
    if profile:
        base += ["--profile", profile]
    for provisioning in ("FULLY_MUTABLE", "IMMUTABLE"):
        response = runner([*base, "--provisioning-type", provisioning], timeout=180)
        if not response.ok:
            result.errors.append(_error_of(response, f"Cloud Control ({provisioning})"))
            continue
        try:
            summaries = json.loads(response.stdout or "{}").get("TypeSummaries", [])
        except ValueError:
            result.errors.append("Cloud Control: unreadable list-types output")
            continue
        verbs = ("get", "list", "create", "delete") + (("update",) if provisioning == "FULLY_MUTABLE" else ())
        for summary in summaries:
            type_name = summary.get("TypeName", "")
            if not type_name:
                continue
            for verb in verbs:
                command = f"aws cloudcontrol {verb}-resource" + ("s" if verb == "list" else "")
                result.ops.append(Operation(
                    id=f"aws:cloudcontrol:{type_name}:{verb}", provider="aws", service="cloudcontrol",
                    name=f"{verb} {type_name}", command=command,
                    required=["--type-name"] + (["--identifier"] if verb in {"get", "update", "delete"} else []) + (["--desired-state"] if verb == "create" else []) + (["--patch-document"] if verb == "update" else []),
                    endpoint=f"X-Amz-Target=CloudApiService.{verb.capitalize()}Resource{'s' if verb == 'list' else ''} ({type_name})",
                    permissions=[f"cloudformation:{verb.capitalize()}Resource{'s' if verb == 'list' else ''}", f"<the {type_name} handler permissions>"],
                    examples=[f"{command} --type-name {type_name}" + (" --identifier <id>" if verb in {"get", "update", "delete"} else "")],
                    verify=["aws cloudcontrol get-resource-request-status --request-token <token>"] if verb in {"create", "update", "delete"} else [],
                    rollback={"create": [f"aws cloudcontrol delete-resource --type-name {type_name} --identifier <id>"], "update": ["aws cloudcontrol update-resource with the inverse JSON patch of the captured state"], "delete": ["recreate from the captured state with create-resource"]}.get(verb, []),
                    risk={"get": "read", "list": "read", "create": "change", "update": "change", "delete": "critical"}[verb],
                    categories=["data"] if verb == "delete" else [], compat=[f"Cloud Control {provisioning}"],
                    doc=(summary.get("Description") or "")[:300], source="cloudcontrol",
                ))


# -- Google Cloud ----------------------------------------------------------------------


_GCP_VERB_PERMISSION = {"list": "list", "describe": "get", "create": "create", "delete": "delete", "update": "update", "start": "start", "stop": "stop", "reset": "reset", "resize": "update", "get-iam-policy": "getIamPolicy", "set-iam-policy": "setIamPolicy", "add-iam-policy-binding": "setIamPolicy", "remove-iam-policy-binding": "setIamPolicy"}


def _gcloud_tree_file(runner: Runner) -> Path | None:
    binary = which("gcloud")
    candidates = []
    if binary:
        candidates.append(Path(os.path.realpath(binary)).parent.parent / "data" / "cli" / "gcloud_completions.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    info = runner(["gcloud", "info", "--format=value(installation.sdk_root)"], timeout=60)
    root = info.stdout.strip() if info.ok else ""
    path = Path(root) / "data" / "cli" / "gcloud_completions.py" if root else None
    return path if path and path.is_file() else None


def discover_gcp(runner: Runner, *, deep: bool = False, context: str = "", deadline: float = 0, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("gcp", source="gcloud tree" + (" + API Discovery" if deep else ""))
    result.available = bool(which("gcloud"))
    if not result.available:
        result.errors.append("gcloud is not installed (Google Cloud SDK)")
        return result
    version = runner(["gcloud", "version", "--format=json"], timeout=60)
    try:
        result.version = "Google Cloud SDK " + json.loads(version.stdout or "{}").get("Google Cloud SDK", "")
    except ValueError:
        result.version = _first_line(version)
    path = _gcloud_tree_file(runner)
    if path is None:
        result.errors.append("the gcloud command tree (data/cli/gcloud_completions.py) was not found in the SDK")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.literal_eval(text[text.index("{"):])
        tracks = set() if deep else {"alpha", "beta", "preview"}

        def walk(node: dict[str, Any], words: list[str]) -> None:
            commands = node.get("commands") or {}
            if not commands and words:
                command = "gcloud " + " ".join(words)
                risk, categories = _risk(command)
                verb = words[-1]
                permission = f"{words[0]}.{words[-2] if len(words) > 2 else words[0]}.{_GCP_VERB_PERMISSION.get(verb, verb)}"
                result.ops.append(Operation(
                    id=f"gcp:{'.'.join(words)}", provider="gcp", service=words[0], name=" ".join(words[1:]) or words[0],
                    command=command, optional=sorted(node.get("flags", {}))[:40], risk=risk, categories=categories,
                    permissions=[f"{permission} (estimated)"], source="gcloud-tree",
                ))
                return
            for name, child in commands.items():
                if not words and name in tracks:
                    continue
                walk(child, [*words, name])

        walk(tree, [])
    if deep:
        _google_api_directory(result)
    return result


def _google_api_directory(result: DiscoveryResult) -> None:
    import httpx

    try:
        response = httpx.get("https://discovery.googleapis.com/discovery/v1/apis", params={"preferred": "true"}, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        result.errors.append(f"Google API Discovery: {exc}")
        return
    for item in items:
        name, version = item.get("name", ""), item.get("version", "")
        result.ops.append(Operation(
            id=f"gcp:api:{name}:{version}", provider="gcp", service=name, name=item.get("title", name),
            command=f"API {name} {version}", endpoint=item.get("discoveryRestUrl", ""), version=version,
            risk="read", doc=(item.get("description") or "")[:300], source="google-api-discovery",
            examples=[f"curl -H \"Authorization: Bearer $(gcloud auth print-access-token)\" {item.get('discoveryRestUrl', '')}"],
        ))


# -- Kubernetes / OpenShift ---------------------------------------------------------------

_BUILTIN_GROUPS = {
    "", "apps", "batch", "autoscaling", "policy", "networking.k8s.io", "rbac.authorization.k8s.io", "storage.k8s.io",
    "apiextensions.k8s.io", "admissionregistration.k8s.io", "apiregistration.k8s.io", "authentication.k8s.io",
    "authorization.k8s.io", "certificates.k8s.io", "coordination.k8s.io", "discovery.k8s.io", "events.k8s.io",
    "flowcontrol.apiserver.k8s.io", "node.k8s.io", "scheduling.k8s.io", "resource.k8s.io", "internal.apiserver.k8s.io",
    "metrics.k8s.io", "storagemigration.k8s.io",
}
_SCALABLE = {"deployments", "statefulsets", "replicasets", "replicationcontrollers", "deploymentconfigs", "machinesets"}
_WORKLOADS = {"deployments", "statefulsets", "daemonsets", "deploymentconfigs"}


def parse_api_resources(text: str) -> list[dict[str, Any]]:
    """Rows of ``kubectl api-resources -o wide`` (fixed-width columns)."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or "APIVERSION" not in lines[0]:
        return []
    header = lines[0]
    names = ["NAME", "SHORTNAMES", "APIVERSION", "NAMESPACED", "KIND", "VERBS", "CATEGORIES"]
    starts = [(name, header.find(name)) for name in names if header.find(name) >= 0]
    rows = []
    for line in lines[1:]:
        row: dict[str, str] = {}
        for index, (name, start) in enumerate(starts):
            end = starts[index + 1][1] if index + 1 < len(starts) else None
            row[name] = line[start:end].strip() if end is not None else line[start:].strip()
        verbs = row.get("VERBS", "").strip("[]").split()
        api = row.get("APIVERSION", "")
        group, _, version = api.rpartition("/")
        rows.append({
            "name": row.get("NAME", ""), "shortnames": [s for s in row.get("SHORTNAMES", "").split(",") if s],
            "group": group, "version": version or api, "namespaced": row.get("NAMESPACED", "").lower() == "true",
            "kind": row.get("KIND", ""), "verbs": verbs, "categories": [c for c in row.get("CATEGORIES", "").split(",") if c],
        })
    return [row for row in rows if row["name"]]


def kube_operations(rows: list[dict[str, Any]], binary: str, provider: str, server_version: str, crd_groups: set[str]) -> list[Operation]:
    ops = []
    for row in rows:
        plural, group, version = row["name"], row["group"], row["version"]
        resource = f"{plural}.{group}" if group and group not in {"apps", "batch"} else plural
        custom = group not in _BUILTIN_GROUPS or group in crd_groups
        prefix = f"/apis/{group}/{version}" if group else f"/api/{version}"
        path = f"{prefix}{'/namespaces/{ns}' if row['namespaced'] else ''}/{plural}"
        verbs = set(row["verbs"])
        plan: list[tuple[str, str, str, list[str]]] = []
        if {"get", "list"} & verbs:
            plan += [("get", f"{binary} get {resource}", "GET", ["get", "list"]), ("describe", f"{binary} describe {resource}", "GET", ["get", "list"])]
        if "create" in verbs:
            plan.append(("create", f"{binary} create -f", "POST", ["create"]))
        if {"create", "patch"} <= verbs:
            plan.append(("apply", f"{binary} apply -f", "PATCH", ["get", "create", "patch"]))
        if "patch" in verbs:
            plan.append(("patch", f"{binary} patch {resource}", "PATCH", ["patch"]))
        if "delete" in verbs:
            plan.append(("delete", f"{binary} delete {resource}", "DELETE", ["delete"]))
        if plural in _SCALABLE and "patch" in verbs:
            plan.append(("scale", f"{binary} scale {resource}", "PATCH", ["patch (scale subresource)"]))
        for verb, command, method, rbac in plan:
            risk, categories = _risk(command if verb not in {"apply", "create"} else f"{binary} {verb} {resource}")
            verify, rollback = [], []
            if verb in {"apply", "create", "patch", "scale"}:
                verify.append(f"{binary} get {resource} <name> -o yaml")
                if plural in _WORKLOADS or verb == "scale":
                    verify.append(f"{binary} rollout status {plural}/<name> -n <ns> --timeout=300s")
                rollback.append(f"{binary} apply -f <snapshot.yaml> (state captured before the change)")
                if plural in _WORKLOADS:
                    rollback.append(f"{binary} rollout undo {plural}/<name> -n <ns>")
                if verb == "create":
                    rollback = [f"{binary} delete {resource} <name>"]
            if verb == "delete":
                verify.append(f"{binary} get {resource} <name> (expect NotFound)")
                rollback.append(f"{binary} apply -f <snapshot.yaml> (state captured before the delete)")
            ops.append(Operation(
                id=f"{provider}:{group or 'core'}/{version}:{plural}:{verb}", provider=provider,
                service=f"crd:{group}" if custom else (group or "core"), name=f"{verb} {row['kind']}", command=command,
                endpoint=f"{method} {path}", version=f"{group + '/' if group else ''}{version}",
                required=["<name>"] if verb in {"patch", "delete", "scale"} else (["-f <manifest>"] if verb in {"apply", "create"} else []),
                optional=(["-n <namespace>"] if row["namespaced"] else []) + ["--context <context>", "-o yaml|json|wide"] + (["--dry-run=server"] if verb in {"apply", "create", "patch", "delete"} else []),
                permissions=[f"{rbac_verb} {plural}{'.' + group if group else ''}" for rbac_verb in rbac],
                examples=[f"{binary} explain {resource} --recursive", f"{command} {'<name> ' if verb in {'patch', 'delete', 'scale', 'describe'} else ''}{'-n <ns>' if row['namespaced'] else ''}".strip()],
                risk=risk, categories=categories, verify=verify, rollback=rollback,
                compat=[f"API {group + '/' if group else ''}{version}"] + ([f"server {server_version}"] if server_version else []) + (["custom resource (operator / CRD)"] if custom else []),
                effects=effects_for(risk, categories), source=f"{binary} api-resources",
            ))
    return ops


def kube_cli() -> list[str]:
    """The kubectl to use: kubectl itself, else the one K3s, MicroK8s or RKE2 ships."""
    if which("kubectl"):
        return ["kubectl"]
    for binary in ("k3s", "microk8s"):
        if which(binary):
            return [binary, "kubectl"]
    rke2 = Path("/var/lib/rancher/rke2/bin/kubectl")
    if rke2.is_file():
        return [str(rke2), "--kubeconfig", "/etc/rancher/rke2/rke2.yaml"]
    return []


def discover_kubernetes(runner: Runner, *, context: str = "", binary: str = "kubectl", provider: str = "kubernetes", **_: Any) -> DiscoveryResult:
    cli = kube_cli() if binary == "kubectl" else ([binary] if which(binary) else [])
    result = DiscoveryResult(provider, source=f"{' '.join(cli[:2]) or binary} api-resources + CRDs")
    result.available = bool(cli)
    if not result.available:
        result.errors.append(f"{binary} is not installed" + (" (nor k3s, microk8s or the RKE2 kubectl)" if binary == "kubectl" else ""))
        return result
    ctx = ["--context", context] if context else []
    current = runner([*cli, "config", "current-context"], timeout=15)
    result.context = context or current.stdout.strip()
    version = runner([*cli, "version", "-o", "json", "--request-timeout=15s", *ctx], timeout=40)
    server = ""
    try:
        data = json.loads(version.stdout or "{}")
        server = (data.get("serverVersion") or {}).get("gitVersion", "")
        result.version = f"client {(data.get('clientVersion') or {}).get('gitVersion', '')}" + (f", server {server}" if server else "")
    except ValueError:
        result.version = _first_line(version)
    resources = runner([*cli, "api-resources", "-o", "wide", "--request-timeout=30s", *ctx], timeout=90)
    if not resources.ok and not resources.stdout.strip():
        result.errors.append(_error_of(resources, "cluster discovery"))
        return result
    if not resources.ok:
        result.errors.append(_error_of(resources, "some API groups did not answer"))
    rows = parse_api_resources(resources.stdout)
    crds = runner([*cli, "get", "crd", "-o", "custom-columns=GROUP:.spec.group", "--no-headers", "--request-timeout=30s", *ctx], timeout=60)
    crd_groups = {line.strip() for line in crds.stdout.splitlines() if line.strip()} if crds.ok else set()
    result.ops = kube_operations(rows, binary, provider, server, crd_groups)
    result.services = {"crd_groups": sorted(crd_groups), "resources": len(rows)}
    return result


# -- CLI help trees ---------------------------------------------------------------------------

_HEADER = re.compile(r"^(?:[A-Za-z][A-Za-z /-]*?)?(commands|COMMANDS|Commands|Subgroups|subcommands|SUBCOMMANDS)\s*:?\s*$")
_ENTRY = re.compile(r"^\s{1,12}([a-z][\w.:-]*?)\*?:?(?:,\s*[\w.-]+)*(?:\s+\[[\w.-]+\])?(?:\s{2,}|\s+:\s+)(\S.*)$")
_FLAG = re.compile(r"^\s+(?:-\w,?\s+)?(--[a-z][\w-]*)")
_SKIP = {"help", "completion", "__complete", "__completenoDesc", "docs", "gendocs"}


def parse_help(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(subcommands, flags) of a CLI help page (cobra, docker, terraform, az, urfave, gh)."""
    commands: list[tuple[str, str]] = []
    flags: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _HEADER.match(stripped) and not line.startswith((" ", "\t")):
            in_section = True
            continue
        if not line.startswith((" ", "\t")):
            in_section = bool(_HEADER.match(stripped))
            continue
        if in_section:
            match = _ENTRY.match(line)
            if match and match.group(1) not in _SKIP:
                name = match.group(1).rstrip(":")
                if name not in {existing for existing, _ in commands}:
                    commands.append((name, match.group(2).strip()))
                continue
        flag = _FLAG.match(line)
        if flag and flag.group(1) not in flags:
            flags.append(flag.group(1))
    return commands, flags


def discover_help_tree(runner: Runner, *, binary: str, provider: str, depth: int = 2, deadline: float = 0,
                       help_args: tuple[str, ...] = ("--help",), version_args: tuple[str, ...] = ("--version",), **_: Any) -> DiscoveryResult:
    result = DiscoveryResult(provider, source=f"{binary} help tree")
    result.available = bool(which(binary))
    if not result.available:
        result.errors.append(f"{binary} is not installed")
        return result
    result.version = _first_line(runner([binary, *version_args], timeout=30))
    top = runner([binary, *help_args], timeout=30)
    commands, flags = parse_help(_strip_prog(top.output, binary))
    if not commands:
        result.errors.append(f"{binary}: no command list in its help output")
        return result

    def page(path: list[str]) -> tuple[list[str], list[tuple[str, str]], list[str]]:
        if deadline and time.monotonic() > deadline:
            return path, [], []
        found = runner([binary, *path, *help_args], timeout=30)
        sub, sub_flags = parse_help(_strip_prog(found.output, binary, *path))
        return path, sub, sub_flags

    with ThreadPoolExecutor(max_workers=8) as pool:
        level = list(pool.map(page, [[name] for name, _ in commands])) if depth > 1 else [([name], [], []) for name, _ in commands]
        summaries = dict(commands)
        for path, sub, sub_flags in level:
            if sub:
                for name, summary in sub:
                    _add_cli_op(result, binary, provider, [*path, name], summary, [])
            else:
                _add_cli_op(result, binary, provider, path, summaries.get(path[0], ""), sub_flags)
    return result


def _strip_prog(text: str, *prefix: str) -> str:
    """yargs pages (CDK) repeat the program in each entry: ``  cdk list [STACKS..]  ...``."""
    lead = " ".join(prefix)
    return re.sub(rf"(?m)^(\s+){re.escape(lead)} (?=[a-z])", r"\1", text) if lead else text


def _add_cli_op(result: DiscoveryResult, binary: str, provider: str, path: list[str], summary: str, flags: list[str]) -> None:
    command = f"{binary} {' '.join(path)}"
    risk, categories = _risk(command)
    result.ops.append(Operation(
        id=f"{provider}:cli:{' '.join(path)}", provider=provider, service=path[0], name=" ".join(path), command=command,
        optional=flags[:40], risk=risk, categories=categories, doc=summary[:300], source=f"{binary} --help",
        examples=[f"{command} --help"],
    ))


# -- Terraform / OpenTofu ------------------------------------------------------------------------


def _terraform_dirs(root: Path, workdir: str) -> list[Path]:
    if workdir:
        path = (root / workdir).resolve() if not Path(workdir).is_absolute() else Path(workdir)
        return [path]
    found = []
    skip = {".git", "node_modules", ".venv", "venv", "__pycache__", ".terraform"}
    for current, dirs, _files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        dirs[:] = [name for name in dirs if name not in skip] if depth < 3 else []
        if (Path(current) / ".terraform").is_dir():
            found.append(Path(current))
        if len(found) >= 5:
            break
    return found


def discover_terraform(runner: Runner, *, binary: str = "terraform", provider: str = "terraform", workdir: str = "", root: str = "", deadline: float = 0, **_: Any) -> DiscoveryResult:
    result = discover_help_tree(runner, binary=binary, provider=provider, depth=1, deadline=deadline, help_args=("-help",), version_args=("version",))
    result.source = f"{binary} CLI + provider schemas"
    if not result.available:
        return result
    for command, extra in (("state", ["list", "show", "mv", "rm", "pull", "push", "replace-provider"]), ("workspace", ["list", "show", "select", "new", "delete"])):
        for sub in extra:
            _add_cli_op(result, binary, provider, [command, sub], "", [])
    version = runner([binary, "version", "-json"], timeout=30)
    selections: dict[str, str] = {}
    try:
        data = json.loads(version.stdout or "{}")
        result.version = f"{binary} {data.get('terraform_version', '')}".strip()
        selections = data.get("provider_selections") or {}
    except ValueError:
        result.version = _first_line(version)
        result.errors.append(f"{binary} version -json: unreadable output, provider versions unknown")
    dirs = _terraform_dirs(Path(root or os.getcwd()), workdir)
    if not dirs:
        result.errors.append("no initialized module (.terraform/) found: run terraform init in the module, or pass workdir=")
    for directory in dirs:
        schema = runner([binary, f"-chdir={directory}", "providers", "schema", "-json"], timeout=180)
        if not schema.ok:
            result.errors.append(_error_of(schema, f"provider schemas in {directory}"))
            continue
        try:
            payload = json.loads(schema.stdout or "{}")
        except ValueError:
            result.errors.append(f"provider schemas in {directory}: unreadable JSON")
            continue
        for source, spec in (payload.get("provider_schemas") or {}).items():
            short = source.split("/", 1)[-1]
            version_note = selections.get(source, "")
            for kind, entries, risk in (("resource", spec.get("resource_schemas") or {}, "change"), ("data", spec.get("data_source_schemas") or {}, "read")):
                for type_name, body in entries.items():
                    block = body.get("block") or {}
                    attributes = block.get("attributes") or {}
                    required = [name for name, attr in attributes.items() if attr.get("required")]
                    optional = [name for name, attr in attributes.items() if attr.get("optional") and not attr.get("required")]
                    optional += [f"{name} {{}}" for name in (block.get("block_types") or {})]
                    address = f"{type_name}.<name>" if kind == "resource" else f"data.{type_name}.<name>"
                    command = f"{binary} plan -target={address}" if kind == "data" else f"{binary} apply -target={address}"
                    result.ops.append(Operation(
                        id=f"{provider}:{short}:{kind}:{type_name}", provider=provider, service=short, name=f"{kind} {type_name}",
                        command=command, endpoint=f"{source} {kind} {type_name} (schema v{body.get('version', 0)})",
                        version=version_note, required=required, optional=optional[:60], risk=risk,
                        examples=[f'{kind} "{type_name}" "<name>" {{ {" ".join(f"{name} = <...>" for name in required[:4])} }}'],
                        verify=[f"{binary} plan -detailed-exitcode -input=false (exit 0: no drift)"] if kind == "resource" else [],
                        rollback=["revert the code in git, plan, then apply; state backups for state repairs"] if kind == "resource" else [],
                        compat=[f"provider {source} {version_note}".strip(), result.version, f"module {directory}"],
                        doc=_doc(block.get("description")), source="terraform-schema",
                    ))
    return result


def _doc(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:300]


# -- Ansible / OpenStack / Proxmox / vSphere / Azure ------------------------------------------------


def discover_ansible(runner: Runner, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("ansible", source="ansible-doc -l")
    result.available = bool(which("ansible-doc"))
    if not result.available:
        result.errors.append("ansible-doc is not installed (pip install ansible-core)")
        return result
    result.version = _first_line(runner(["ansible", "--version"], timeout=30))
    listing = runner(["ansible-doc", "-l", "-j"], timeout=180)
    try:
        modules = json.loads(listing.stdout or "{}")
    except ValueError:
        result.errors.append(_error_of(listing, "ansible-doc -l"))
        return result
    for name, description in modules.items():
        command = f"ansible all -m {name}"
        risk, categories = _risk(command)
        result.ops.append(Operation(
            id=f"ansible:module:{name}", provider="ansible", service=name.rsplit(".", 1)[0] if "." in name else "module",
            name=name, command=command, doc=str(description)[:300], risk=risk, categories=categories, source="ansible-doc",
            examples=[f"ansible <hosts> -m {name} -a '<args>' --check --diff", f"ansible-doc {name}"],
            verify=["re-run the play with --check (expect changed=0)"], rollback=["apply the previous desired state with the same module"],
        ))
    return result


def discover_openstack(runner: Runner, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("openstack", source="openstack command list")
    result.available = bool(which("openstack"))
    if not result.available:
        result.errors.append("openstack is not installed (pip install python-openstackclient)")
        return result
    result.version = _first_line(runner(["openstack", "--version"], timeout=30))
    listing = runner(["openstack", "command", "list", "-f", "json"], timeout=120)
    try:
        groups = json.loads(listing.stdout or "[]")
    except ValueError:
        result.errors.append(_error_of(listing, "openstack command list"))
        return result
    for group in groups:
        for name in group.get("Commands", []):
            command = f"openstack {name}"
            risk, categories = _risk(command)
            result.ops.append(Operation(
                id=f"openstack:{group.get('Command Group', '')}:{name}", provider="openstack", service=group.get("Command Group", ""),
                name=name, command=command, risk=risk, categories=categories, source="openstack command list",
                examples=[f"{command} --help"],
            ))
    return result


def discover_proxmox(runner: Runner, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("proxmox", source="qm / pct help")
    result.available = bool(which("qm") or which("pct") or which("pvesh"))
    if not result.available:
        result.errors.append("qm, pct and pvesh are not on this machine (run on a Proxmox node, or over ssh)")
        return result
    for binary in ("qm", "pct"):
        if not which(binary):
            continue
        page = runner([binary, "help"], timeout=30)
        for name in sorted(set(re.findall(rf"^\s*{binary}\s+([a-z][\w-]*)", page.output, re.M))):
            _add_cli_op(result, binary, "proxmox", [name], "", [])
    return result


def discover_vsphere(runner: Runner, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("vsphere", source="govc commands")
    result.available = bool(which("govc"))
    if not result.available:
        result.errors.append("govc is not installed (github.com/vmware/govmomi)")
        return result
    result.version = _first_line(runner(["govc", "version"], timeout=30))
    page = runner(["govc"], timeout=30)
    for name in sorted(set(re.findall(r"^\s+([a-z][\w.]*)\s*$", page.output, re.M))):
        _add_cli_op(result, "govc", "vsphere", [name], "", [])
    return result


_AZ_CORE = ("vm", "vmss", "aks", "network", "storage", "sql", "postgres", "mysql", "keyvault", "monitor", "role", "ad", "group",
            "deployment", "acr", "webapp", "functionapp", "cosmosdb", "redis", "containerapp", "identity", "policy", "resource",
            "consumption", "costmanagement", "backup", "dns", "lock", "tag", "account")


def discover_azure(runner: Runner, *, deep: bool = False, deadline: float = 0, **_: Any) -> DiscoveryResult:
    result = DiscoveryResult("azure", source="az help tree")
    result.available = bool(which("az"))
    if not result.available:
        result.errors.append("az is not installed (Azure CLI)")
        return result
    version = runner(["az", "version", "-o", "json"], timeout=60)
    try:
        result.version = "azure-cli " + json.loads(version.stdout or "{}").get("azure-cli", "")
    except ValueError:
        result.version = _first_line(version)
    index = Path.home() / ".azure" / "commandIndex.json"
    groups: list[str] = []
    try:
        groups = sorted((json.loads(index.read_text(encoding="utf-8")).get("commandIndex") or {}).keys())
    except (OSError, ValueError):
        top, _flags = parse_help(runner(["az", "--help"], timeout=60).output)
        groups = [name for name, _ in top]
    wanted = groups if deep else [group for group in _AZ_CORE if not groups or group in groups]

    def page(group: str) -> tuple[str, list[tuple[str, str]]]:
        if deadline and time.monotonic() > deadline:
            return group, []
        found, _flags = parse_help(runner(["az", group, "--help"], timeout=60).output)
        return group, found

    with ThreadPoolExecutor(max_workers=6) as pool:
        for group, entries in pool.map(page, wanted):
            for name, summary in entries:
                _add_cli_op(result, "az", "azure", [group, name], summary, [])
    if not deep and groups:
        result.errors.append(f"read {len(wanted)} core groups of {len(groups)}: discover provider=azure deep=true reads them all")
    return result


# -- entry point ------------------------------------------------------------------------------

_HELP_TREES: dict[str, dict[str, Any]] = {
    "docker": {"binary": "docker", "version_args": ("version", "--format", "{{.Client.Version}}")},
    "podman": {"binary": "podman"},
    "containerd": {"binary": "nerdctl"},
    "helm": {"binary": "helm", "version_args": ("version", "--short")},
    "argocd": {"binary": "argocd", "version_args": ("version", "--client", "--short")},
    "flux": {"binary": "flux", "version_args": ("version", "--client")},
    "github": {"binary": "gh"},
    "gitlab": {"binary": "glab"},
    "pulumi": {"binary": "pulumi", "version_args": ("version",)},
    "cdk": {"binary": "cdk"},
    "velero": {"binary": "velero", "version_args": ("version", "--client-only")},
}
#: Providers read from several CLIs, merged into one catalog file.
_MULTI_TREES: dict[str, list[dict[str, Any]]] = {
    "security": [
        {"binary": "trivy"}, {"binary": "vault", "version_args": ("version",)}, {"binary": "cmctl", "version_args": ("version", "--client")},
        {"binary": "falcoctl", "version_args": ("version",)}, {"binary": "kyverno", "version_args": ("version",)},
        {"binary": "opa", "version_args": ("version",)}, {"binary": "conftest", "version_args": ("--version",)},
    ],
}
#: What makes a discoverable provider present when it is not one binary.
_PRESENCE: dict[str, tuple[str, ...]] = {
    "kubernetes": ("kubectl", "k3s", "microk8s"),
    "velero": ("velero",),
    "security": tuple(spec["binary"] for spec in _MULTI_TREES["security"]),
}


def discover(provider: str, *, registry: Registry, runner: Runner = default_runner, deep: bool = False,
             context: str = "", workdir: str = "", budget_s: float = 240) -> list[DiscoveryResult]:
    """Discover one provider (or ``all`` the installed ones) and save it in the registry."""
    names = discoverable_installed() if provider == "all" else [provider]
    unknown = [name for name in names if name not in DISCOVERABLE]
    if unknown:
        raise ValueError(f"unknown provider {unknown[0]}; use one of {', '.join(DISCOVERABLE)} or all")
    results = []
    for name in names:
        started = time.monotonic()
        deadline = started + budget_s
        result = _discover_one(name, runner, deep=deep, context=context, workdir=workdir, root=str(registry.workspace), deadline=deadline)
        result.duration_ms = int((time.monotonic() - started) * 1000)
        if result.ops:
            registry.save(name, {
                "provider": name, "version": result.version, "source": result.source, "context": result.context,
                "discovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "services": result.services,
                "errors": result.errors[:20], "ops": _unique_rows(result.ops),
            })
        results.append(result)
    return results


def _unique_rows(ops: list[Operation]) -> list[dict[str, Any]]:
    seen, rows = set(), []
    for op in ops:
        if op.id in seen:
            continue
        seen.add(op.id)
        rows.append(op.to_row())
    return rows


def _installed(provider: str) -> bool:
    if provider == "jenkins":
        return bool(os.environ.get("JENKINS_URL")) or bool(which("jenkins-cli"))
    if provider == "forgejo":
        return bool(os.environ.get("FORGEJO_URL")) or bool(which("tea"))
    if provider == "kubernetes" and kube_cli():
        return True
    return any(which(binary) for binary in _PRESENCE.get(provider) or PROVIDER_BINARIES.get(provider, ()))


def discoverable_installed() -> list[str]:
    """The providers ``discover`` can read on this machine now."""
    return [name for name in DISCOVERABLE if _installed(name)]


# -- Jenkins and Forgejo (HTTP APIs) ----------------------------------------------------------


def _http_json(url: str, *, auth: tuple[str, str] | None = None, headers: dict[str, str] | None = None) -> Any:
    """GET a JSON document; raises httpx.HTTPError or ValueError."""
    import httpx

    response = httpx.get(url, auth=auth, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def discover_jenkins(runner: Runner, *, deadline: float = 0, **_: Any) -> DiscoveryResult:
    """Jobs of a Jenkins server (JENKINS_URL, JENKINS_USER, JENKINS_TOKEN), each with its operations."""
    import httpx

    result = DiscoveryResult("jenkins", source="Jenkins REST API")
    base = os.environ.get("JENKINS_URL", "").rstrip("/")
    if not base:
        result.available = bool(which("jenkins-cli"))
        result.errors.append("JENKINS_URL is not set: export JENKINS_URL, JENKINS_USER and JENKINS_TOKEN (an API token, not the password)")
        return result
    user, token = os.environ.get("JENKINS_USER", ""), os.environ.get("JENKINS_TOKEN", "")
    auth = (user, token) if user and token else None
    try:
        root = _http_json(f"{base}/api/json?tree=jobs[name,url,color,buildable,jobs[name,url,color]]", auth=auth)
        info = _http_json(f"{base}/api/json?tree=mode,nodeDescription,useSecurity", auth=auth)
    except (httpx.HTTPError, ValueError) as exc:
        result.errors.append(f"Jenkins API {base}: {exc}")
        return result
    result.version = str(info.get("_class", "Jenkins")) if isinstance(info, dict) else "Jenkins"
    result.context = base
    curl = "curl -fsS -u $JENKINS_USER:$JENKINS_TOKEN"
    jobs = []
    for job in (root.get("jobs") or []) if isinstance(root, dict) else []:
        jobs.append(job)
        jobs.extend(job.get("jobs") or [])
    for job in jobs:
        name, url = job.get("name", ""), str(job.get("url") or "").rstrip("/")
        if not name or not url:
            continue
        path = url[len(base):] if url.startswith(base) else f"/job/{name}"
        for verb, method, suffix, risk, perm in (
            ("status", "GET", "/lastBuild/api/json", "read", "Job/Read"),
            ("console", "GET", "/lastBuild/consoleText", "read", "Job/Read"),
            ("build", "POST", "/build", "change", "Job/Build"),
            ("build-with-parameters", "POST", "/buildWithParameters", "change", "Job/Build"),
            ("stop", "POST", "/lastBuild/stop", "change", "Job/Cancel"),
            ("delete", "POST", "/doDelete", "critical", "Job/Delete"),
        ):
            command = f"{curl} {'-X POST ' if method == 'POST' else ''}$JENKINS_URL{path}{suffix}"
            result.ops.append(Operation(
                id=f"jenkins:job:{path}:{verb}", provider="jenkins", service="job", name=f"{verb} {name}",
                command=command, endpoint=f"{method} {path}{suffix}", permissions=[perm], risk=risk,
                categories=["data"] if risk == "critical" else [], examples=[command],
                verify=[f"{curl} $JENKINS_URL{path}/lastBuild/api/json (result SUCCESS)"] if verb.startswith("build") else [],
                rollback=[f"{curl} -X POST $JENKINS_URL{path}/lastBuild/stop"] if verb.startswith("build") else (["restore the job from config.xml or the Job Configuration History plugin"] if verb == "delete" else []),
                compat=["Jenkins REST API", f"color {job.get('color', '')}"], source="jenkins-api",
            ))
    if not result.ops:
        result.errors.append(f"Jenkins {base}: no job visible with these credentials")
    return result


def discover_forgejo(runner: Runner, *, deadline: float = 0, **kwargs: Any) -> DiscoveryResult:
    """Forgejo: the tea CLI tree plus, with FORGEJO_URL and FORGEJO_TOKEN, the Actions API per repository."""
    import httpx

    result = discover_help_tree(runner, binary="tea", provider="forgejo", deadline=deadline) if which("tea") else DiscoveryResult("forgejo", source="Forgejo API", available=False)
    result.source = "tea help tree + Forgejo API" if which("tea") else "Forgejo API"
    base = os.environ.get("FORGEJO_URL", "").rstrip("/")
    token = os.environ.get("FORGEJO_TOKEN", "")
    if not base:
        result.errors.append("FORGEJO_URL is not set: export FORGEJO_URL and FORGEJO_TOKEN to read the Actions of your repositories")
        return result
    result.available = True
    headers = {"Authorization": f"token {token}"} if token else {}
    try:
        version = _http_json(f"{base}/api/v1/version", headers=headers)
        repos = _http_json(f"{base}/api/v1/user/repos?limit=50", headers=headers) if token else []
    except (httpx.HTTPError, ValueError) as exc:
        result.errors.append(f"Forgejo API {base}: {exc}")
        return result
    result.version = f"Forgejo {version.get('version', '')}" if isinstance(version, dict) else "Forgejo"
    result.context = base
    if not token:
        result.errors.append("FORGEJO_TOKEN is not set: only the public API was read")
    curl = 'curl -fsS -H "Authorization: token $FORGEJO_TOKEN"'
    for repo in repos if isinstance(repos, list) else []:
        full = repo.get("full_name", "")
        if not full:
            continue
        api = f"/api/v1/repos/{full}"
        for verb, method, suffix, risk, perm, verify, rollback in (
            ("list action tasks", "GET", "/actions/tasks", "read", "read:repository", [], []),
            ("dispatch workflow", "POST", "/actions/workflows/<workflow.yml>/dispatches", "change", "write:repository",
             [f"{curl} $FORGEJO_URL{api}/actions/tasks (newest task status success)"], ["cancel the run from the Actions page; revert the commit it deployed"]),
            ("set secret", "PUT", "/actions/secrets/<NAME>", "critical", "write:repository",
             [f"{curl} $FORGEJO_URL{api}/actions/secrets (the name is listed)"], ["PUT the previous value again, or DELETE the secret"]),
            ("list runners", "GET", "/actions/runners", "read", "read:repository", [], []),
        ):
            command = f"{curl} {'-X ' + method + ' ' if method != 'GET' else ''}$FORGEJO_URL{api}{suffix}"
            result.ops.append(Operation(
                id=f"forgejo:{full}:{verb.replace(' ', '-')}", provider="forgejo", service="actions", name=f"{verb} ({full})",
                command=command, endpoint=f"{method} {api}{suffix}", permissions=[f"token scope {perm}"], risk=risk,
                categories=["secrets"] if "secret" in verb else [], examples=[command], verify=verify, rollback=rollback,
                compat=[result.version], source="forgejo-api",
            ))
    return result


def discover_security(runner: Runner, *, deadline: float = 0, **_: Any) -> DiscoveryResult:
    """Trivy, Vault, cert-manager (cmctl), Falco (falcoctl), Kyverno, OPA and Conftest help trees in one catalog."""
    result = DiscoveryResult("security", source="security CLIs help trees")
    versions, missing = [], []
    for spec in _MULTI_TREES["security"]:
        if not which(spec["binary"]):
            missing.append(spec["binary"])
            continue
        part = discover_help_tree(runner, provider="security", deadline=deadline, **spec)
        result.ops.extend(part.ops)
        result.errors.extend(part.errors)
        if part.version:
            versions.append(f"{spec['binary']} {part.version}")
    result.available = bool(versions) or any(which(spec["binary"]) for spec in _MULTI_TREES["security"])
    result.version = ", ".join(versions)
    if missing:
        result.errors.append(f"not installed here: {', '.join(missing)}")
    return result


def _discover_one(name: str, runner: Runner, **kwargs: Any) -> DiscoveryResult:
    handlers: dict[str, Callable[..., DiscoveryResult]] = {
        "aws": discover_aws,
        "gcp": discover_gcp,
        "azure": discover_azure,
        "kubernetes": discover_kubernetes,
        "openshift": lambda r, **kw: discover_kubernetes(r, binary="oc", provider="openshift", **kw),
        "terraform": discover_terraform,
        "opentofu": lambda r, **kw: discover_terraform(r, binary="tofu", provider="opentofu", **kw),
        "ansible": discover_ansible,
        "openstack": discover_openstack,
        "proxmox": discover_proxmox,
        "vsphere": discover_vsphere,
        "jenkins": discover_jenkins,
        "forgejo": discover_forgejo,
        "security": discover_security,
    }
    if name in handlers:
        return handlers[name](runner, **kwargs)
    spec = _HELP_TREES.get(name)
    if spec is None:
        return DiscoveryResult(name, errors=[f"no discovery for {name}"], available=False)
    return discover_help_tree(runner, provider=name, deadline=kwargs.get("deadline", 0), **spec)
