# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Universal Command & API Registry.

The machine-wide catalog (``~/.navin/infra/catalog/<provider>.json``) holds
what discovery read from the installed CLIs, SDK models and APIs; the curated
base (:mod:`navin.infra.curated`) is always there. What a project learned
stays in the project (``.navin/infra``): the Terraform / OpenTofu catalog read
from its own initialized modules (``catalog/<provider>.json``), the operations
that ran and passed their verification (``tested.json``) and the provider
permission checks (``access.json``).

A full record (:meth:`Registry.describe`) is completed on demand from the
source: botocore for AWS (parameters, IAM action, endpoint, inverse and
verification operations), Terraform provider schemas, ``ansible-doc``.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.infra.curated import curated_operations
from navin.infra.model import PROVIDER_BINARIES, RISKS, TIERS, Operation, effects_for
from navin.infra.parse import classify_command, worst
from navin.infra.policy import InfraPolicy, current_contexts, decide, infra_dir
from navin.utils.atomic_io import atomic_write_text

_FILE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FILE_LOCK = threading.Lock()
_WHICH_CACHE: dict[str, tuple[float, str]] = {}

# Catalogs read from the project's own files (the Terraform / OpenTofu modules
# initialized in it, with their provider schemas): kept in the project, so
# discovering another project never replaces them.
PROJECT_PROVIDERS = frozenset({"terraform", "opentofu"})


def default_catalog_root() -> Path:
    from navin.config.paths import get_runtime_subdir

    root = get_runtime_subdir("infra") / "catalog"
    root.mkdir(parents=True, exist_ok=True)
    return root


def which(binary: str) -> str:
    """``shutil.which`` cached for a minute (PATH lookups cost on WSL mounts)."""
    now = time.monotonic()
    cached = _WHICH_CACHE.get(binary)
    if cached and now - cached[0] < 60:
        return cached[1]
    found = shutil.which(binary) or ""
    _WHICH_CACHE[binary] = (now, found)
    return found


def operation_binary(op: Operation) -> str:
    first = (op.command.split() or [""])[0]
    return first if re.match(r"^[a-z][\w.-]*$", first) else ""


def is_available(op: Operation) -> bool:
    binary = operation_binary(op)
    if binary and which(binary):
        return True
    return any(which(name) for name in PROVIDER_BINARIES.get(op.provider, ())[:1])


class Registry:
    """Read, search and complete the catalog."""

    def __init__(self, workspace: str | Path, catalog_root: str | Path | None = None) -> None:
        self.workspace = Path(workspace).expanduser()
        self.root = Path(catalog_root).expanduser() if catalog_root else default_catalog_root()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- storage --------------------------------------------------------------

    def _provider_file(self, provider: str) -> Path:
        if provider in PROJECT_PROVIDERS:
            return infra_dir(self.workspace) / "catalog" / f"{provider}.json"
        return self.root / f"{provider}.json"

    def _catalog_names(self) -> list[str]:
        """Discovered providers: the machine catalog plus this project's own."""
        names = {path.stem for path in self.root.glob("*.json") if path.stem not in PROJECT_PROVIDERS}
        project = infra_dir(self.workspace) / "catalog"
        if project.is_dir():
            names |= {path.stem for path in project.glob("*.json") if path.stem in PROJECT_PROVIDERS}
        return sorted(names)

    def save(self, provider: str, payload: dict[str, Any]) -> None:
        path = self._provider_file(provider)
        # A private temporary file per write: two engines (CLI projects, the
        # Desktop) saving the same provider at once never publish a partial file.
        atomic_write_text(path, json.dumps(payload, separators=(",", ":")))
        with _FILE_LOCK:
            _FILE_CACHE.pop(str(path), None)

    def load(self, provider: str) -> dict[str, Any]:
        path = self._provider_file(provider)
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return {}  # this provider was never discovered
        with _FILE_LOCK:
            cached = _FILE_CACHE.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("infra: catalog file {} is unreadable ({}): run discover again for it", path, exc)
            return {}
        with _FILE_LOCK:
            _FILE_CACHE[str(path)] = (mtime, data)
        return data

    def providers(self) -> list[dict[str, Any]]:
        out = []
        for name in self._catalog_names():
            data = self.load(name)
            if data:
                out.append({
                    "provider": name, "count": len(data.get("ops", [])), "source": data.get("source", ""),
                    "version": data.get("version", ""), "discovered_at": data.get("discovered_at", ""),
                    "context": data.get("context", ""),
                })
        return out

    def operations(self, provider: str = "") -> list[Operation]:
        ops = [op for op in curated_operations() if not provider or op.provider == provider]
        files = [provider] if provider else self._catalog_names()
        for name in files:
            data = self.load(name)
            for row in data.get("ops", []):
                op = Operation.from_row(row)
                if not op.provider:
                    op.provider = name
                ops.append(op)
        return ops

    # -- project state -----------------------------------------------------------

    def _project_file(self, name: str) -> Path:
        return infra_dir(self.workspace) / name

    def _read_project(self, name: str) -> dict[str, Any]:
        try:
            data = json.loads(self._project_file(name).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}  # nothing tested / checked in this project yet
        except (OSError, ValueError) as exc:
            logger.warning("infra: {} is unreadable ({}): starting it again", self._project_file(name), exc)
            return {}

    def _write_project(self, name: str, data: dict[str, Any]) -> None:
        path = self._project_file(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        temp.replace(path)

    def tested(self) -> dict[str, Any]:
        return self._read_project("tested.json")

    def mark_tested(self, op_id: str, command: str, verified: bool | None) -> None:
        data = self.tested()
        entry = data.get(op_id) or {"runs": 0}
        entry.update({"last": time.strftime("%Y-%m-%dT%H:%M:%S"), "command": command, "verified": verified})
        entry["runs"] = int(entry.get("runs", 0)) + 1
        data[op_id] = entry
        self._write_project("tested.json", data)

    def access(self) -> dict[str, Any]:
        return self._read_project("access.json")

    def record_access(self, op_id: str, allowed: bool | None, how: str, detail: str = "") -> None:
        data = self.access()
        data[op_id] = {"allowed": allowed, "how": how, "detail": detail[:400], "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._write_project("access.json", data)

    # -- tiers ----------------------------------------------------------------------

    def tiers(self, op: Operation, policy: InfraPolicy, contexts: dict[str, Any], tested: dict[str, Any] | None = None, access: dict[str, Any] | None = None) -> dict[str, Any]:
        tested = self.tested() if tested is None else tested
        access = self.access() if access is None else access
        decision = self.decision(op, policy, contexts)
        checked = access.get(op.id, {}).get("allowed")
        return {
            "known": True,
            "available": is_available(op),
            "authorized": decision["action"] != "deny" and checked is not False,
            "tested": bool(tested.get(op.id, {}).get("verified") is not False and op.id in tested),
            "decision": decision,
        }

    def decision(self, op: Operation, policy: InfraPolicy, contexts: dict[str, Any]) -> dict[str, Any]:
        classified = worst(classify_command(op.command))
        if classified is None:
            # Not a CLI line (an API endpoint or a REST call): judge it on its record.
            from navin.infra.parse import Classification

            classified = Classification(command=op.command, binary="", provider=op.provider, risk=op.risk, categories=set(op.categories))
        elif op.risk in RISKS and RISKS.index(op.risk) > RISKS.index(classified.risk):
            classified.risk = op.risk
            classified.categories |= set(op.categories)
        result = decide(classified, policy, contexts)
        return {"action": result.action, "reasons": result.reasons, "risk": result.risk}

    # -- search ------------------------------------------------------------------------

    def search(self, query: str = "", *, provider: str = "", risk: str = "", tier: str = "", limit: int = 50,
               policy: InfraPolicy | None = None, contexts: dict[str, Any] | None = None) -> dict[str, Any]:
        policy = policy or InfraPolicy()
        contexts = contexts or current_contexts(self.workspace)
        terms = [term for term in re.split(r"\s+", (query or "").lower()) if term]
        tested, access = self.tested(), self.access()
        matches: list[tuple[int, Operation]] = []
        for op in self.operations(provider):
            if risk and op.risk != risk:
                continue
            haystack = f"{op.id} {op.command} {op.service} {op.name} {op.doc}".lower()
            if terms and not all(term in haystack for term in terms):
                continue
            score = sum(3 for term in terms if term in op.command.lower()) + sum(1 for term in terms if term in op.name.lower())
            if op.source == "curated":
                score += 2
            matches.append((score, op))
        matches.sort(key=lambda item: (-item[0], item[1].command))
        items, total = [], 0
        for _score, op in matches:
            tiers = self.tiers(op, policy, contexts, tested, access) if tier or len(items) < limit else None
            if tier and tier in TIERS and not (tiers or {}).get(tier):
                continue
            total += 1
            if len(items) < limit:
                tiers = tiers or self.tiers(op, policy, contexts, tested, access)
                items.append({
                    "id": op.id, "provider": op.provider, "service": op.service, "name": op.name,
                    "command": op.command, "risk": op.risk, "known": True, "available": tiers["available"],
                    "authorized": tiers["authorized"], "tested": tiers["tested"],
                })
        return {"total": total, "items": items}

    def counts(self, policy: InfraPolicy, contexts: dict[str, Any]) -> dict[str, int]:
        """Tier counters. ``authorized`` is counted on the risk of each record."""
        ops = self.operations()
        tested = self.tested()
        available_providers = {provider for provider, binaries in PROVIDER_BINARIES.items() if any(which(name) for name in binaries[:1])}
        allowed_risks = {"observe": {"read", "plan"}, "prepare": {"read", "plan", "change", "critical"}, "execute": {"read", "plan", "change", "critical"}}[policy.level]
        access = self.access()
        known = len(ops)
        available = sum(1 for op in ops if op.provider in available_providers or (op.source == "curated" and is_available(op)))
        authorized = sum(1 for op in ops if op.risk in allowed_risks and access.get(op.id, {}).get("allowed") is not False)
        return {"known": known, "available": available, "authorized": authorized, "tested": sum(1 for value in tested.values() if value.get("verified") is not False)}

    # -- describe ------------------------------------------------------------------------

    def find(self, ref: str) -> Operation | None:
        ref = (ref or "").strip()
        if not ref:
            return None
        provider = ref.split(":", 2)[1] if ref.startswith("curated:") else ref.split(":", 1)[0]
        for op in self.operations(provider if provider in PROVIDER_BINARIES or self._provider_file(provider).exists() else ""):
            if op.id == ref:
                return op
        if " " in ref or re.match(r"^[a-z][\w-]*$", ref):
            return self.match_command(ref)
        return None

    def match_command(self, command: str) -> Operation | None:
        """The registry record closest to a command line (longest command prefix)."""
        classified = worst(classify_command(command))
        words = command.split()
        if not words:
            return None
        best: tuple[int, Operation] | None = None
        provider = classified.provider if classified else ""
        for op in self.operations(provider if provider and self._provider_file(provider).exists() else ""):
            parts = op.command.split()
            if not parts or parts[0] != words[0]:
                continue
            length = 0
            for left, right in zip(parts, words):
                if left != right:
                    break
                length += 1
            if length and (best is None or length > best[0] or (length == best[0] and op.source != "curated" and best[1].source == "curated")):
                best = (length, op)
        if best is not None and best[0] >= min(2, len(words)):
            return best[1]
        if classified is None:
            return None
        return Operation(
            id=f"adhoc:{classified.provider}:{classified.service}:{classified.verb}".replace(" ", "-"),
            provider=classified.provider, service=classified.service, name=classified.verb or words[0],
            command=" ".join(words[:3]), risk=classified.risk, categories=sorted(classified.categories),
            effects=effects_for(classified.risk, sorted(classified.categories)), source="classifier",
        )

    def describe(self, ref: str, policy: InfraPolicy, contexts: dict[str, Any]) -> dict[str, Any] | None:
        op = self.find(ref)
        if op is None:
            return None
        op = enrich(op, self)
        tiers = self.tiers(op, policy, contexts)
        record = op.to_dict()
        record.update({
            "known": True, "available": tiers["available"], "authorized": tiers["authorized"], "tested": tiers["tested"],
            "decision": {"action": tiers["decision"]["action"], "reasons": tiers["decision"]["reasons"]},
            "tested_runs": self.tested().get(op.id),
            "access_check": self.access().get(op.id),
        })
        if not record["effects"]:
            record["effects"] = effects_for(op.risk, op.categories)
        return record


# -- enrichment from the sources ------------------------------------------------------

_INVERSE = [
    ("Create", "Delete"), ("Run", "Terminate"), ("Start", "Stop"), ("Attach", "Detach"), ("Associate", "Disassociate"),
    ("Register", "Deregister"), ("Enable", "Disable"), ("Authorize", "Revoke"), ("Allocate", "Release"),
    ("Add", "Remove"), ("Tag", "Untag"), ("Put", "Delete"), ("Import", "Delete"), ("Activate", "Deactivate"),
]
_VERBS = ("Describe", "Get", "List", "Create", "Delete", "Run", "Terminate", "Start", "Stop", "Attach", "Detach", "Associate",
          "Disassociate", "Register", "Deregister", "Enable", "Disable", "Authorize", "Revoke", "Allocate", "Release", "Add",
          "Remove", "Tag", "Untag", "Put", "Update", "Modify", "Import", "Activate", "Deactivate", "Reboot", "Restore", "Copy")


def kebab(name: str) -> str:
    """``DescribeDBInstances`` -> ``describe-db-instances`` (the AWS CLI rule)."""
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first).lower().replace("--", "-")


def split_verb(operation: str) -> tuple[str, str]:
    for verb in sorted(_VERBS, key=len, reverse=True):
        if operation.startswith(verb) and len(operation) > len(verb) and operation[len(verb)].isupper():
            return verb, operation[len(verb):]
    return "", operation


def _botocore_session():
    import botocore.session

    return botocore.session.get_session()


def aws_counterparts(service: str, operation: str, operation_names: list[str]) -> dict[str, list[str]]:
    """Verification and inverse operations of an AWS operation, from the model."""
    verb, noun = split_verb(operation)
    names = set(operation_names)
    verify, rollback = [], []
    singular = noun[:-1] if noun.endswith("s") else noun
    for candidate in (f"Describe{noun}s", f"Describe{noun}", f"Describe{singular}s", f"Get{noun}", f"Get{singular}", f"List{noun}s", f"List{singular}s"):
        if candidate in names and candidate != operation:
            verify.append(f"aws {service} {kebab(candidate)}")
            break
    for left, right in _INVERSE:
        if verb == left and f"{right}{noun}" in names:
            rollback.append(f"aws {service} {kebab(right + noun)}")
        elif verb == right and f"{left}{noun}" in names and left not in {"Put", "Import"}:
            rollback.append(f"aws {service} {kebab(left + noun)} (recreate from the state captured before)")
    if verb in {"Modify", "Update", "Put"} and not rollback:
        rollback.append(f"re-apply the previous values captured with {verify[0] if verify else 'the matching describe call'}")
    if verb in {"Delete", "Terminate"} and not rollback:
        rollback.append("not reversible: restore from a backup, snapshot or object version")
    return {"verify": verify, "rollback": rollback}


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def enrich(op: Operation, registry: Registry | None = None) -> Operation:
    """Complete a record from its source; what cannot be read is said in the record."""
    if op.provider == "aws" and op.source in {"botocore", "curated", "classifier"}:
        return _enrich_aws(op)
    if op.source == "ansible-doc":
        return _enrich_ansible(op)
    return op


def _enrich_aws(op: Operation) -> Operation:
    parts = op.command.split()
    if len(parts) < 3 or parts[0] != "aws":
        return op
    try:
        import botocore
        from botocore.exceptions import BotoCoreError
        from botocore.model import OperationNotFoundError
    except ImportError:
        op.doc = (op.doc + " (install botocore to read this operation's parameters and permissions)").strip()
        return op
    cli_service, op_kebab = parts[1], parts[2]
    service = "s3" if cli_service == "s3api" else cli_service
    try:
        session = _botocore_session()
        if service not in session.get_available_services():
            return op
        model = session.get_service_model(service)
        by_kebab = {kebab(name): name for name in model.operation_names}
        name = by_kebab.get(op_kebab)
        if name is None:
            return op
        operation = model.operation_model(name)
    except (BotoCoreError, OperationNotFoundError) as exc:
        op.doc = (op.doc + f" (the AWS model of {service} could not be read: {exc})").strip()
        return op
    shape = operation.input_shape
    required = list(getattr(shape, "required_members", []) or []) if shape is not None else []
    members = list(shape.members) if shape is not None else []

    prefix = model.signing_name or model.endpoint_prefix
    http = operation.http or {}
    counterparts = aws_counterparts(cli_service, name, model.operation_names)
    op.required = op.required or [f"--{kebab(member)}" for member in required]
    op.optional = op.optional or [f"--{kebab(member)}" for member in members if member not in required][:40]
    op.permissions = op.permissions or [f"{prefix}:{name}"]
    model_endpoint = f"{http.get('method', 'POST')} https://{model.endpoint_prefix}.<region>.amazonaws.com{http.get('requestUri', '/')} ({model.protocol}, {name})"
    if not op.endpoint or "://" not in op.endpoint:
        op.endpoint = model_endpoint
    op.version = op.version or f"API {model.api_version}"
    op.verify = op.verify or counterparts["verify"]
    op.rollback = op.rollback or counterparts["rollback"]
    op.compat = op.compat or [f"botocore {botocore.__version__}", f"API version {model.api_version}", "AWS CLI v2", f"protocol {model.protocol}"]
    if not op.examples:
        args = " ".join(f"{flag} <{flag[2:]}>" for flag in op.required[:4])
        op.examples = [f"aws {cli_service} {op_kebab} {args}".strip() + " --region <region> --output json"]
    if not op.doc:
        op.doc = _strip_html(operation.documentation)[:600]
    if operation.deprecated:
        op.compat.append("deprecated")
    return op


def _enrich_ansible(op: Operation) -> Operation:
    import subprocess

    from navin.utils.proc import no_window_kwargs

    module = op.name
    try:
        result = subprocess.run(
            ["ansible-doc", "-j", module], capture_output=True, text=True, timeout=30,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        op.doc = f"{op.doc} (ansible-doc unavailable: {exc})".strip()
        return op
    try:
        data = json.loads(result.stdout or "{}").get(module, {}).get("doc", {})
    except ValueError:
        op.doc = f"{op.doc} (ansible-doc -j {module} gave no readable JSON: {(result.stderr or '').strip()[-160:]})".strip()
        return op
    options = data.get("options") or {}
    op.required = [name for name, spec in options.items() if isinstance(spec, dict) and spec.get("required")]
    op.optional = [name for name, spec in options.items() if isinstance(spec, dict) and not spec.get("required")][:40]
    description = data.get("description")
    if isinstance(description, list):
        description = " ".join(description)
    op.doc = str(description or op.doc)[:600]
    if data.get("version_added"):
        op.compat = [f"added in {data['version_added']}"]
    return op
