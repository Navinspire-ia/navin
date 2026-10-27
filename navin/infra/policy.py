# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Policy & Approval Engine: who may run what, where, at which autonomy level.

Levels (per project, ``.navin/infra/policy.json``):

- ``observe``: inventory, logs, diagnosis; nothing is changed.
- ``prepare``: plus IaC generation, plans, dry runs, estimates; a real change
  asks the user every time.
- ``execute``: plus changes inside the authorized scope, verified afterwards.

Whatever the level, critical operations (production targets, deletions, IAM,
sensitive network, data, costly capacity, secret values) ask the user. Raising
the level is the user's call, never the agent's. Credentials stay temporary:
the policy can pin an AWS IAM Identity Center profile and a Google service
account to impersonate; long-lived keys are reported.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from navin.infra.model import LEVELS, RISK_ORDER
from navin.infra.parse import Classification

DEFAULT_MARKERS = ["prod", "production", "prd", "live"]
SCOPE_DIMENSIONS = ("aws_profiles", "kube_contexts", "namespaces", "gcp_projects", "azure_subscriptions")
#: Classification target -> the scope dimension that limits it.
_TARGET_SCOPE = {
    "aws_profile": "aws_profiles",
    "kube_context": "kube_contexts",
    "namespace": "namespaces",
    "gcp_project": "gcp_projects",
    "azure_subscription": "azure_subscriptions",
}
#: Categories that make any change critical.
CRITICAL_CATEGORIES = {"iam", "network", "data", "costly", "secrets", "availability"}


def infra_dir(workspace: str | Path) -> Path:
    return Path(workspace).expanduser() / ".navin" / "infra"


@dataclass
class InfraPolicy:
    level: str = "execute"
    scopes: dict[str, list[str]] = field(default_factory=lambda: {name: [] for name in SCOPE_DIMENSIONS})
    production_markers: list[str] = field(default_factory=lambda: list(DEFAULT_MARKERS))
    auth: dict[str, str] = field(default_factory=lambda: {"aws_profile": "", "gcp_impersonate": ""})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InfraPolicy:
        policy = cls()
        if not isinstance(data, dict):
            return policy
        if data.get("level") in LEVELS:
            policy.level = data["level"]
        scopes = data.get("scopes")
        if isinstance(scopes, dict):
            for name in SCOPE_DIMENSIONS:
                policy.scopes[name] = _clean_list(scopes.get(name, []))
        if isinstance(data.get("production_markers"), list):
            policy.production_markers = _clean_list(data["production_markers"])
        auth = data.get("auth")
        if isinstance(auth, dict):
            for name in ("aws_profile", "gcp_impersonate"):
                policy.auth[name] = str(auth.get(name) or "").strip()
        return policy


def _clean_list(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def load_policy(workspace: str | Path) -> InfraPolicy:
    path = infra_dir(workspace) / "policy.json"
    try:
        return InfraPolicy.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return InfraPolicy()
    except (OSError, ValueError) as exc:
        # A damaged file must not widen anything: fall back to the safest level.
        logger.warning("infra: unreadable policy {} ({}): using the observe level", path, exc)
        return InfraPolicy(level="observe")


def save_policy(workspace: str | Path, policy: InfraPolicy) -> None:
    directory = infra_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / "policy.json.tmp"
    temp.write_text(json.dumps(policy.to_dict(), indent=2) + "\n", encoding="utf-8")
    temp.replace(directory / "policy.json")


def update_policy(policy: InfraPolicy, fields: dict[str, Any]) -> InfraPolicy:
    data = policy.to_dict()
    if "level" in fields:
        if fields["level"] not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        data["level"] = fields["level"]
    if isinstance(fields.get("scopes"), dict):
        for name, values in fields["scopes"].items():
            if name not in SCOPE_DIMENSIONS:
                raise ValueError(f"unknown scope {name}; use {', '.join(SCOPE_DIMENSIONS)}")
            data["scopes"][name] = _clean_list(values)
    if "production_markers" in fields:
        data["production_markers"] = _clean_list(fields["production_markers"])
    if isinstance(fields.get("auth"), dict):
        for name, value in fields["auth"].items():
            if name not in ("aws_profile", "gcp_impersonate"):
                raise ValueError("auth takes aws_profile and gcp_impersonate")
            data["auth"][name] = str(value or "").strip()
    return InfraPolicy.from_dict(data)


def widens(old: InfraPolicy, new: InfraPolicy) -> list[str]:
    """What a policy change gives the agent that it did not have (asked to the user)."""
    out = []
    if LEVELS.index(new.level) > LEVELS.index(old.level):
        out.append(f"autonomy {old.level} -> {new.level}")
    for name in SCOPE_DIMENSIONS:
        before, after = set(old.scopes.get(name, [])), set(new.scopes.get(name, []))
        if before and (not after or after - before):
            out.append(f"scope {name}: {sorted(before)} -> {sorted(after) or 'everything'}")
    if set(old.production_markers) - set(new.production_markers):
        out.append(f"production markers removed: {sorted(set(old.production_markers) - set(new.production_markers))}")
    return out


# -- contexts -----------------------------------------------------------------------

_CONTEXT_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}
_CONTEXT_LOCK = threading.Lock()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _kubeconfig_paths() -> list[Path]:
    raw = os.environ.get("KUBECONFIG", "")
    if raw:
        return [Path(part).expanduser() for part in raw.split(os.pathsep) if part]
    return [Path.home() / ".kube" / "config"]


def _read_text(path: Path, problems: list[str]) -> str:
    """A config file's text, '' when it does not exist.

    A missing file is normal (no kubeconfig, no gcloud). A file that exists but
    cannot be read is reported in ``problems`` and shown to the user.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        problems.append(f"Cannot read {path}: {exc.strerror or exc}")
        return ""


def _read_kube(problems: list[str]) -> dict[str, Any]:
    current, contexts = "", []
    for path in _kubeconfig_paths():
        text = _read_text(path, problems)
        if not text:
            continue
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{path} is not valid YAML ({exc.__class__.__name__}): only current-context was read")
            match = re.search(r"^current-context:\s*(\S+)", text, re.M)
            data = {"current-context": match.group(1) if match else ""}
        if not isinstance(data, dict):
            problems.append(f"{path} is not a kubeconfig (no mapping at the top)")
            continue
        if not current and data.get("current-context"):
            current = str(data["current-context"]).strip("'\"")
        for entry in data.get("contexts") or []:
            if isinstance(entry, dict) and entry.get("name") and entry["name"] not in contexts:
                contexts.append(str(entry["name"]))
    return {"current": current, "contexts": contexts}


def _read_aws(problems: list[str]) -> dict[str, Any]:
    profiles: list[str] = []
    sso: list[str] = []
    config = Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config")).expanduser()
    section = ""
    for line in _read_text(config, problems).splitlines():
        match = re.match(r"^\s*\[(?:profile\s+)?([^\]]+)\]", line)
        if match:
            section = match.group(1).strip()
            if not section.startswith("sso-session") and section not in profiles:
                profiles.append(section)
            continue
        if section and re.match(r"^\s*sso_(session|start_url)\s*=", line) and section not in sso:
            sso.append(section)
    keys: list[str] = []
    credentials = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", Path.home() / ".aws" / "credentials")).expanduser()
    section = ""
    for line in _read_text(credentials, problems).splitlines():
        match = re.match(r"^\s*\[([^\]]+)\]", line)
        if match:
            section = match.group(1).strip()
        elif section and re.match(r"^\s*aws_access_key_id\s*=\s*AKIA", line) and section not in keys:
            keys.append(section)
    for profile in keys:
        if profile not in profiles:
            profiles.append(profile)
    profile = os.environ.get("AWS_PROFILE") or os.environ.get("AWS_DEFAULT_PROFILE") or "default"
    return {"profile": profile, "profiles": profiles, "sso_profiles": sso, "long_lived_keys": keys}


def _read_gcp(problems: list[str]) -> dict[str, Any]:
    root = Path(os.environ.get("CLOUDSDK_CONFIG", Path.home() / ".config" / "gcloud")).expanduser()
    # No active_config file: gcloud itself uses the "default" configuration.
    configuration = os.environ.get("CLOUDSDK_ACTIVE_CONFIG_NAME", "") or _read_text(root / "active_config", problems).strip() or "default"
    project = os.environ.get("CLOUDSDK_CORE_PROJECT", "")
    impersonate = os.environ.get("CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT", "")
    text = _read_text(root / "configurations" / f"config_{configuration}", problems)
    if not project:
        match = re.search(r"^\s*project\s*=\s*(\S+)", text, re.M)
        project = match.group(1) if match else ""
    if not impersonate:
        match = re.search(r"^\s*impersonate_service_account\s*=\s*(\S+)", text, re.M)
        impersonate = match.group(1) if match else ""
    key_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    service_account_key = False
    if key_file:
        body = _read_text(Path(key_file).expanduser(), problems)
        if not body:
            problems.append(f"GOOGLE_APPLICATION_CREDENTIALS points to {key_file}, which is missing or empty")
        else:
            try:
                data = json.loads(body)
            except ValueError:
                problems.append(f"GOOGLE_APPLICATION_CREDENTIALS ({key_file}) is not a JSON credentials file")
            else:
                service_account_key = isinstance(data, dict) and data.get("type") == "service_account"
    return {"project": project, "configuration": configuration, "impersonate": impersonate, "service_account_key": service_account_key}


def _read_terraform(cwd: Path, problems: list[str] | None = None) -> dict[str, Any]:
    workspace = os.environ.get("TF_WORKSPACE", "")
    if not workspace:
        # No environment file in an initialized module: Terraform's "default" workspace.
        workspace = _read_text(cwd / ".terraform" / "environment", problems if problems is not None else []).strip()
        if not workspace and (cwd / ".terraform").is_dir():
            workspace = "default"
    return {"workspace": workspace}


def current_contexts(cwd: str | Path | None = None) -> dict[str, Any]:
    """Kubernetes, AWS, GCP and Terraform contexts, cached on the files' mtimes."""
    cwd_path = Path(cwd or os.getcwd()).expanduser()
    watched = [*_kubeconfig_paths(), Path.home() / ".aws" / "config", Path.home() / ".aws" / "credentials",
               Path(os.environ.get("CLOUDSDK_CONFIG", Path.home() / ".config" / "gcloud")) / "active_config",
               cwd_path / ".terraform" / "environment"]
    env = tuple(os.environ.get(name, "") for name in ("KUBECONFIG", "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "CLOUDSDK_CORE_PROJECT", "CLOUDSDK_ACTIVE_CONFIG_NAME", "TF_WORKSPACE", "GOOGLE_APPLICATION_CREDENTIALS"))
    stamp = (tuple(_mtime(path) for path in watched), env)
    key = str(cwd_path)
    with _CONTEXT_LOCK:
        cached = _CONTEXT_CACHE.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
    problems: list[str] = []
    value = {"kubernetes": _read_kube(problems), "aws": _read_aws(problems), "gcp": _read_gcp(problems),
             "terraform": _read_terraform(cwd_path, problems), "cwd": str(cwd_path), "problems": problems}
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE[key] = (stamp, value)
    return value


def is_production(name: str, markers: list[str]) -> bool:
    """``arn:...:cluster/eks-prod-01`` and ``production-eu`` are production; ``product`` is not."""
    if not name or not markers:
        return False
    wanted = {marker.lower() for marker in markers}
    for token in re.split(r"[^a-z0-9]+", name.lower()):
        if token and (token in wanted or token.rstrip("0123456789") in wanted):
            return True
    return False


# -- decisions ------------------------------------------------------------------------


@dataclass
class Decision:
    action: str  # allow | ask | deny
    risk: str
    categories: list[str]
    reasons: list[str]
    production: bool = False
    out_of_scope: list[str] = field(default_factory=list)
    targets: dict[str, str] = field(default_factory=dict)
    level: str = "execute"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_targets(c: Classification, policy: InfraPolicy, contexts: dict[str, Any]) -> dict[str, str]:
    """The concrete account, cluster, project and workspace a command acts on."""
    targets = {key: value for key, value in c.targets.items() if value}
    provider = c.provider
    if provider in {"kubernetes", "openshift", "helm", "argocd", "flux"} or c.binary in {"helm", "kubectl", "oc"}:
        targets.setdefault("kube_context", contexts["kubernetes"]["current"])
    if provider == "aws" or c.binary == "eksctl":
        targets.setdefault("aws_profile", policy.auth.get("aws_profile") or contexts["aws"]["profile"])
    if provider == "gcp":
        targets.setdefault("gcp_project", contexts["gcp"]["project"])
    if provider in {"terraform", "opentofu"}:
        workdir = targets.get("workdir", "")
        if workdir:
            module = Path(workdir) if Path(workdir).is_absolute() else Path(contexts.get("cwd") or ".") / workdir
            workspace = _read_terraform(module)["workspace"]
        else:
            workspace = contexts["terraform"]["workspace"]
        if workspace:
            targets.setdefault("terraform_workspace", workspace)
    if provider in {"terraform", "opentofu", "pulumi", "cdk"}:
        # IaC acts on the cloud account its credentials name.
        profile = policy.auth.get("aws_profile") or os.environ.get("AWS_PROFILE", "")
        if profile:
            targets.setdefault("aws_profile", profile)
    return {key: value for key, value in targets.items() if value}


def decide(c: Classification, policy: InfraPolicy, contexts: dict[str, Any]) -> Decision:
    """allow / ask / deny for one classified command."""
    risk = c.risk
    categories = set(c.categories)
    reasons = list(c.reasons)
    targets = resolve_targets(c, policy, contexts)
    production_hits = [f"{key}={value}" for key, value in targets.items()
                       if key not in {"plan_file", "name", "release", "unit"} and is_production(value, policy.production_markers)]
    production = bool(production_hits)
    local_only = "local" in categories and not (categories & CRITICAL_CATEGORIES)
    if c.mutation and not local_only:
        if production:
            categories.add("production")
            if risk != "critical":
                risk = "critical"
            reasons.append(f"production target ({', '.join(production_hits)})")
        if categories & CRITICAL_CATEGORIES and risk != "critical":
            risk = "critical"
        if "no-saved-plan" in categories and (production or risk == "critical"):
            reasons.append("production change without a saved plan: run terraform plan -out=tfplan, review it, then terraform apply tfplan")
            categories.discard("no-saved-plan")
    out_of_scope = []
    for target, dimension in _TARGET_SCOPE.items():
        allowed = policy.scopes.get(dimension) or []
        value = targets.get(target)
        if allowed and value and value not in allowed:
            out_of_scope.append(f"{target} {value} is outside the authorized {dimension} ({', '.join(allowed)})")
    if out_of_scope:
        categories.add("out-of-scope")
        reasons.extend(out_of_scope)
    level = policy.level if policy.level in LEVELS else "observe"
    if local_only and risk == "change":
        action = "allow"
    elif risk == "read":
        action = "ask" if "secrets" in categories else "allow"
        if action == "ask":
            reasons.append("the output contains secret values")
    elif risk == "plan":
        action = "ask" if level == "observe" else "allow"
        if action == "ask":
            reasons.append("observe level: plans and dry runs need your OK")
    elif level == "observe":
        action = "deny"
        reasons.append("observe level: nothing may change. Ask the user to raise the level to prepare or execute.")
    elif risk == "critical":
        action = "ask"
        if not reasons:
            reasons.append("critical operation")
    elif level == "prepare":
        action = "ask"
        reasons.append("prepare level: every real change needs your approval")
    elif "no-saved-plan" in categories:
        action = "ask"
        reasons.append("apply without a saved plan: run plan -out=tfplan, review it, then apply tfplan (that runs without asking)")
    else:
        action = "ask" if out_of_scope else "allow"
    return Decision(action=action, risk=risk, categories=sorted(categories), reasons=_dedupe(reasons),
                    production=production, out_of_scope=out_of_scope, targets=targets, level=level)


def combine(decisions: list[Decision]) -> Decision | None:
    """The decision of a whole line: the strictest of its commands."""
    if not decisions:
        return None
    order = {"allow": 0, "ask": 1, "deny": 2}
    strictest = max(decisions, key=lambda d: (order[d.action], RISK_ORDER.get(d.risk, 2)))
    merged = Decision(**{**strictest.to_dict()})
    merged.categories = sorted({name for d in decisions for name in d.categories})
    merged.reasons = _dedupe([reason for d in decisions for reason in d.reasons])
    merged.production = any(d.production for d in decisions)
    return merged


def _dedupe(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def auth_warnings(contexts: dict[str, Any], policy: InfraPolicy) -> list[str]:
    """Credential hygiene, in the user's terms."""
    out = list(contexts.get("problems") or [])
    keys = contexts["aws"].get("long_lived_keys") or []
    if keys:
        out.append(
            f"Long-lived AWS access keys in ~/.aws/credentials ({', '.join(keys)}): prefer IAM Identity Center "
            "(aws configure sso, then aws sso login) and temporary roles."
        )
    if contexts["gcp"].get("service_account_key"):
        out.append(
            "GOOGLE_APPLICATION_CREDENTIALS points to a service account key file: prefer your own login with "
            "service account impersonation (auth.gcp_impersonate or --impersonate-service-account)."
        )
    if policy.level == "execute" and not any(policy.scopes.get(name) for name in SCOPE_DIMENSIONS):
        out.append("Execute level with no scope: autonomous changes can reach every account and cluster you are signed in to. Set scopes to limit them.")
    kube = contexts["kubernetes"].get("current", "")
    if is_production(kube, policy.production_markers):
        out.append(f"The current Kubernetes context {kube} is production: every change there asks you first.")
    return out
