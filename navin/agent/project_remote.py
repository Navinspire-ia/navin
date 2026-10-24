# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Remote hosts and cloud tooling the agent can use for a project.

SSH goes through the user's own setup: ``~/.ssh/config`` aliases, keys and the
ssh-agent (ProxyJump included). Navin stores no password and never waits for
one (BatchMode): a host that needs a password fails at once with a clear
message instead of hanging. The project can name its targets in
``.navin/remote.json``::

    {"default": "prod",
     "hosts": {"prod": {"host": "deploy@app.example.com", "path": "/srv/app"},
               "staging": {"host": "staging", "path": "/srv/app"}}}

Cloud CLIs (aws, gcloud, az, kubectl, helm, terraform, doctl, flyctl...) run
locally with the user's own credentials; this module only reports which ones
are installed and their active context/profile names - never a secret.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REMOTE_FILE = "remote.json"
_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new")
_CLOUD_CLIS = (
    "aws", "gcloud", "az", "kubectl", "helm", "terraform", "tofu", "pulumi", "doctl", "flyctl",
    "fly", "vercel", "netlify", "wrangler", "firebase", "railway", "heroku", "oci", "ibmcloud",
    "scw", "hcloud", "linode-cli", "eksctl", "gke-gcloud-auth-plugin", "kustomize", "skaffold",
    "ansible", "docker", "podman", "ssh", "rsync",
)


@dataclass(frozen=True)
class RemoteTarget:
    name: str
    host: str
    path: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remote_targets(root: Path) -> tuple[dict[str, RemoteTarget], str]:
    """Targets declared in .navin/remote.json, and the default one's name."""
    data = _read_json(root / ".navin" / REMOTE_FILE)
    targets: dict[str, RemoteTarget] = {}
    hosts = data.get("hosts") if isinstance(data.get("hosts"), dict) else {}
    for name, entry in hosts.items():
        if isinstance(entry, dict) and entry.get("host"):
            targets[str(name)] = RemoteTarget(str(name), str(entry["host"]), str(entry.get("path") or ""))
    if data.get("host") and "default" not in targets:
        targets["default"] = RemoteTarget("default", str(data["host"]), str(data.get("path") or ""))
    default = str(data.get("default") or ("default" if "default" in targets else next(iter(targets), "")))
    return targets, default


def ssh_aliases() -> list[str]:
    """Host aliases from ~/.ssh/config (and its Include files), no wildcards."""
    aliases: list[str] = []
    config = Path.home() / ".ssh" / "config"
    files = [config]
    text = ""
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for include in re.findall(r"^\s*Include\s+(.+)$", text, re.MULTILINE | re.IGNORECASE):
        for pattern in include.split():
            base = Path(pattern).expanduser()
            if not base.is_absolute():
                base = Path.home() / ".ssh" / base
            try:
                files += sorted(base.parent.glob(base.name))
            except (OSError, ValueError):
                continue
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in re.findall(r"^\s*Host\s+(.+)$", content, re.MULTILINE | re.IGNORECASE):
            for alias in line.split():
                if not any(ch in alias for ch in "*?!") and alias not in aliases:
                    aliases.append(alias)
    return aliases[:40]


def resolve_target(root: Path, host: str | None, path: str | None) -> RemoteTarget | str:
    """The target to run on, or an error message saying what is missing."""
    targets, default = remote_targets(root)
    name = (host or "").strip() or default
    if not name:
        return (
            "no remote host given and none declared. Pass host= (an ~/.ssh/config alias or "
            "user@host) and remote_path=, or declare them in .navin/remote.json."
        )
    target = targets.get(name) or RemoteTarget(name, name, "")
    remote_path = (path or "").strip() or target.path
    if not remote_path:
        return (
            f"no remote folder for {name}. Pass remote_path= or set \"path\" for it in "
            ".navin/remote.json."
        )
    return RemoteTarget(target.name, target.host, remote_path)


def ssh_command(target: RemoteTarget, command: str) -> str:
    """``command`` run in the target's folder over SSH, never prompting."""
    inner = f"cd {shlex.quote(target.path)} && {command}"
    return " ".join(["ssh", *_SSH_OPTIONS, shlex.quote(target.host), shlex.quote(inner)])


def ssh_background(target: RemoteTarget, command: str, log: str) -> str:
    """Start ``command`` detached on the host; prints its PID to track it.

    Only the job goes to the background (inside ``{ ... & }``): backgrounding
    the whole ``cd && ... &&`` list ran it in a subshell that kept the SSH
    output open until the server stopped, so ssh never returned, and ``$!``
    was that subshell rather than the server. ``setsid`` (when the host has
    it) gives the job its own process group, so stop ends the whole tree.
    """
    job = shlex.quote(command)
    out = shlex.quote(log)
    inner = (
        f"cd {shlex.quote(target.path)} && mkdir -p .navin && "
        f"{{ if command -v setsid >/dev/null 2>&1; then setsid nohup sh -c {job} > {out} 2>&1 < /dev/null & "
        f"else nohup sh -c {job} > {out} 2>&1 < /dev/null & fi; }}; echo NAVIN_PID=$!"
    )
    return " ".join(["ssh", *_SSH_OPTIONS, shlex.quote(target.host), shlex.quote(inner)])


def remote_stop(pid: str) -> str:
    """Stop a job started by ssh_background: its whole process group, else the PID."""
    return f"kill -TERM -- -{pid} 2>/dev/null || kill {pid}"


def sync_command(root: Path, target: RemoteTarget) -> str:
    """Push the project to the host. Never --delete: files on the host are kept."""
    excludes = " ".join(
        f"--exclude {shlex.quote(item)}"
        for item in (".git", "node_modules", ".venv", "venv", "__pycache__", ".navin", "dist", "build", ".next", "target")
    )
    ssh = "ssh " + " ".join(_SSH_OPTIONS)
    return (
        f"rsync -az {excludes} -e {shlex.quote(ssh)} ./ "
        f"{shlex.quote(target.host + ':' + target.path.rstrip('/') + '/')}"
    )


def _kube_context() -> str:
    try:
        text = (Path.home() / ".kube" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"^current-context:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else ""


def _aws_profiles() -> list[str]:
    names: list[str] = []
    for name in ("config", "credentials"):
        try:
            text = (Path.home() / ".aws" / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for section in re.findall(r"^\[(?:profile\s+)?([^\]]+)\]", text, re.MULTILINE):
            if section not in names and not section.startswith("sso-session"):
                names.append(section)
    return names[:12]


def _gcloud_config() -> str:
    try:
        return (Path.home() / ".config" / "gcloud" / "active_config").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def cloud_lines(root: Path) -> list[str]:
    """Installed infra CLIs, their active context/profile names and declared SSH targets."""
    lines: list[str] = []
    from navin.agent.project_runtime import which

    installed = [cli for cli in _CLOUD_CLIS if which(cli)]
    if installed:
        lines.append("Infra / cloud CLIs installed (they use the user's own credentials): " + ", ".join(installed))
    details = []
    if "kubectl" in installed and (context := _kube_context()):
        details.append(f"kubectl context {context}")
    if "aws" in installed and (profiles := _aws_profiles()):
        details.append("aws profiles " + ", ".join(profiles))
    if "gcloud" in installed and (config := _gcloud_config()):
        details.append(f"gcloud configuration {config}")
    if details:
        lines.append("  " + "; ".join(details))
    targets, default = remote_targets(root)
    if targets:
        lines.append("Remote targets (.navin/remote.json, run with project host=<name>):")
        for name, target in targets.items():
            mark = " (default)" if name == default else ""
            lines.append(f"  {name}{mark}: {target.host}:{target.path or '?'}")
    aliases = ssh_aliases()
    if aliases:
        lines.append("SSH hosts in ~/.ssh/config (key/agent auth): " + ", ".join(aliases))
    return lines
