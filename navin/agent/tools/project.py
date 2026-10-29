# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run a project's own commands by action: install, build, start, stop...

The model picks an action; Navin picks the command (navin/agent/project_runtime.py),
runs it through the regular exec tool (same sandbox, deny rules and approvals),
in the right folder, after installing dependencies when they are missing. Web
apps start on a free port. What it starts is tracked, so ``stop`` and
``restart`` touch only those processes. A failure comes back with what it
most likely means; a success is remembered in ``.navin/commands.json`` and
preferred next time.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent import project_runtime as runtime
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context

_ONE_SHOT = ("install", "build", "test", "e2e", "lint", "typecheck", "format", "migrate", "preview", "deploy")
_RUN_ACTIONS = _ONE_SHOT + ("start",)
_NEED_DEPS = frozenset({"build", "start", "test", "e2e", "lint", "typecheck", "preview"})
_TIMEOUTS = {"install": 900, "build": 900, "test": 1200, "e2e": 1800, "migrate": 600, "deploy": 1800}
_EXIT_CODE = re.compile(r"exit code:?\s*(-?\d+)", re.IGNORECASE)
# A green check whose inputs did not change is not run again: an audit ran the
# same lint, tests and build twice in one turn (about 150 s for nothing). Only
# passing runs are reused, for this long, and only while no file the unit can
# see (its own folder and shared code, not other units) changed.
_REUSABLE_ACTIONS = frozenset({"build", "test", "lint", "typecheck"})
_REUSE_TTL_S = 15 * 60.0
_GREEN_RUNS: dict[tuple[str, str, str, str], tuple[str, float, str]] = {}
_SESSION_ID = re.compile(r"session_id:\s*([\w-]+)")
_TAIL = 3000
# Seconds a remote process gets before Navin checks it is still alive.
_REMOTE_SETTLE_S = 2


# Never through this tool, whatever the action or the command given: removing
# infrastructure, data, files or history. Deploying is fine; deleting is not.
_DELETION = re.compile(
    r"\b(?:terraform|tofu|pulumi|cdk|cdktf|terragrunt)\b[^|;&]*\bdestroy\b"
    r"|\bhelm\s+(?:uninstall|delete|del)\b"
    r"|\bkubectl\b[^|;&]*\s(?:delete|drain)\b"
    r"|\bdocker(?:-compose|\s+compose)?\b[^|;&]*\s(?:down|rm|rmi|prune)\b"
    r"|\bdocker\s+(?:volume|network|image|container|system)\s+(?:rm|prune)\b"
    r"|\bsam\s+delete\b|\b(?:serverless|sls)\s+remove\b"
    r"|\bfly(?:ctl)?\b[^|;&]*\b(?:destroy|delete)\b"
    r"|\bvercel\s+(?:rm|remove)\b|\bnetlify\s+sites:delete\b|\bfirebase\s+\S*:delete\b"
    r"|\bgcloud\b[^|;&]*\s(?:delete|remove)\b"
    r"|\baws\b[^|;&]*\s(?:delete-\S+|rm|rb|terminate-\S+|deregister-\S+)\b"
    r"|\baz\b[^|;&]*\s(?:delete|purge)\b"
    r"|\b(?:railway|wrangler)\b[^|;&]*\b(?:delete|down)\b"
    r"|\brm\s+-[a-zA-Z]*[rRf]|\brmdir\b|\bdel\s+/[sfqSFQ]|\bRemove-Item\b|\brd\s+/s\b"
    r"|\bgit\b[^|;&]*\bpush\b[^|;&]*(?:--force|\s-f\b|--delete|\s:)"
    r"|\bgit\s+(?:reset\s+--hard|clean\s+-\w*[fdx]|branch\s+-[dD]\b)"
    r"|\b(?:DROP|TRUNCATE)\s+(?:TABLE|DATABASE|SCHEMA)\b|\bdropdb\b|\bFLUSHALL\b"
    r"|\brsync\b[^|;&]*--delete",
    re.IGNORECASE,
)


def deletion_in(command: str) -> str:
    """The deleting part of ``command``, or ""."""
    match = _DELETION.search(command)
    return match.group(0).strip() if match else ""


def _unit_fingerprint(root: Path, unit_path: str, profile: runtime.Profile) -> str | None:
    """Stamps of every file this unit's checks can see; None when unknown.

    Its own folder and shared code count; the folders of the other units do
    not, so a parallel session editing the back end keeps a front-end result.
    """
    from navin.agent.code_validation import workspace_code_snapshot

    try:
        snapshot = workspace_code_snapshot(root)
    except Exception:  # noqa: BLE001 - no fingerprint, no reuse
        return None
    if not snapshot:
        return None
    others = [
        unit.path.rstrip("/") + "/" for unit in profile.units
        if unit.path not in {".", unit_path}
        and not (unit_path + "/").startswith(unit.path.rstrip("/") + "/")
    ]
    digest = hashlib.sha1()  # noqa: S324 - change detection
    for path in sorted(snapshot):
        if any(path.startswith(prefix) for prefix in others):
            continue
        digest.update(f"{path}\0{snapshot[path]}\0".encode())
    return digest.hexdigest()


@dataclass
class Tracked:
    """A process this tool started, so stop/restart never touch anything else."""

    unit: str
    command: str
    session_id: str | None
    url: str | None
    started_at: float
    watcher: Any = None
    # (RemoteTarget, pid, log path) for a process started over SSH.
    remote: Any = None


# (owner session key, project root, unit) -> what Navin started there.
_TRACKED: dict[tuple[str, str, str], Tracked] = {}

_DIAGNOSES: tuple[tuple[re.Pattern[str], str], ...] = (
    # --- SSH / Git ---------------------------------------------------------------
    (re.compile(r"Permission denied \(publickey|Host key verification failed|ssh: Could not resolve hostname|ssh: connect to host .* (?:Connection refused|Connection timed out|No route to host)", re.I),
     "SSH cannot connect: Navin uses the user's own keys / ssh-agent / ~/.ssh/config, never a password. Ask the user to make `ssh <host>` work without a prompt (key added, host reachable)."),
    (re.compile(r"Authentication failed for|could not read Username|terminal prompts disabled|fatal: Authentication failed|remote: Invalid username or password|Support for password authentication was removed", re.I),
     "Git cannot authenticate to the remote: the user needs a token / SSH key for it (Settings > Git & GitHub tokens, or gh auth login)."),
    (re.compile(r"\[rejected\].*\((?:fetch first|non-fast-forward)\)|Updates were rejected because the (?:tip|remote)", re.I),
     "The remote has commits you do not have: pull/rebase first (git pull --rebase), then push. Never force-push."),
    (re.compile(r"^CONFLICT \(|Automatic merge failed|both modified:", re.I | re.M),
     "Merge conflict: resolve the conflicted files, git add them, then continue the merge/rebase."),
    (re.compile(r"not a git repository", re.I),
     "Not inside a git repository: run the command from the project folder (or git init if it is a new project)."),
    # --- Kubernetes / Helm ---------------------------------------------------------
    (re.compile(r"Unable to connect to the server|The connection to the server .* was refused|dial tcp .*:6443", re.I),
     "kubectl cannot reach the cluster: check the current context (kubectl config current-context), VPN/network, or that the local cluster is running."),
    (re.compile(r"You must be logged in to the server|error: You must be logged in|\(Unauthorized\)|getting credentials: exec: executable \S+ failed|Token has expired and refresh failed", re.I),
     "Cluster credentials are missing or expired: refresh them (aws sso login / gcloud auth login / az login, then the cluster credentials command)."),
    (re.compile(r"Forbidden: User .* cannot|is forbidden: User|cannot (?:get|list|create|patch|delete) resource", re.I),
     "The cluster user lacks RBAC rights for this resource/namespace: use a context or namespace you are allowed to change, or ask the user for the rights."),
    (re.compile(r"no matches for kind .* in version|the server doesn't have a resource type|ensure CRDs are installed first", re.I),
     "Kubernetes API version / CRD mismatch: update the manifest apiVersion for this cluster, or install the CRDs first."),
    (re.compile(r"ImagePullBackOff|ErrImagePull|manifest unknown|pull access denied|repository does not exist or may require 'docker login'", re.I),
     "The image cannot be pulled: check the image name/tag, push it to the registry, or log in to the registry (docker login / imagePullSecrets)."),
    (re.compile(r"CrashLoopBackOff|Back-off restarting failed container", re.I),
     "The container keeps crashing: read its logs (kubectl logs <pod> --previous) and fix the app error or a missing env var/secret."),
    (re.compile(r"Insufficient (?:cpu|memory)|0/\d+ nodes are available|Unschedulable", re.I),
     "The cluster has no room for the pod: lower the requests or scale the nodes."),
    (re.compile(r"another operation \(install/upgrade/rollback\) is in progress|has no deployed releases", re.I),
     "The Helm release is stuck from a previous run: check helm history <release>; roll back to the last good revision (helm rollback), never uninstall."),
    # --- Terraform / Pulumi -----------------------------------------------------------
    (re.compile(r"Error acquiring the state lock|state lock|ConditionalCheckFailedException.*lock", re.I),
     "The Terraform state is locked by another run: wait for it, or after checking nobody runs, terraform force-unlock <ID> (ask the user)."),
    (re.compile(r"Backend initialization required|please run \"terraform init\"|Inconsistent dependency lock file|Required plugins are not installed|Module not installed", re.I),
     "Terraform is not initialised for this folder: run terraform init (with -upgrade if the lock file changed), then plan again."),
    (re.compile(r"No valid credential sources found|failed to refresh cached credentials|NoCredentialProviders", re.I),
     "The Terraform/Pulumi provider has no cloud credentials: log in to the cloud CLI (aws sso login / gcloud auth application-default login / az login) or set the profile."),
    # --- Clouds --------------------------------------------------------------------------
    (re.compile(r"Unable to locate credentials|ExpiredToken|security token included in the request is (?:expired|invalid)|The SSO session associated with this profile has expired|Error loading SSO Token", re.I),
     "AWS credentials are missing or expired: aws sso login --profile <profile> (or set AWS_PROFILE to a valid one)."),
    (re.compile(r"AccessDenied|UnauthorizedOperation|is not authorized to perform|AccessDeniedException", re.I),
     "The cloud identity lacks the permission named above: use a role/profile that has it, or ask the user to grant it."),
    (re.compile(r"Could not connect to the endpoint URL|You must specify a region|InvalidClientTokenId", re.I),
     "AWS region or endpoint is wrong/missing: set --region / AWS_REGION to the project's region."),
    (re.compile(r"Reauthentication (?:failed|required)|gcloud auth login|invalid_grant|Application Default Credentials are not available", re.I),
     "GCP credentials are missing or expired: gcloud auth login (and gcloud auth application-default login for SDKs/Terraform)."),
    (re.compile(r"PERMISSION_DENIED|The caller does not have permission", re.I),
     "The GCP account lacks the IAM role for this call: use the right project/account or ask the user for the role."),
    (re.compile(r"has not been used in project .* or it is disabled|SERVICE_DISABLED", re.I),
     "The GCP API is disabled for this project: gcloud services enable <api> (the error names it)."),
    (re.compile(r"Please run 'az login'|AADSTS\d+|AuthorizationFailed|No subscription found", re.I),
     "Azure credentials or subscription missing: az login, then az account set --subscription <id>."),
    (re.compile(r"ThrottlingException|Rate exceeded|429 Too Many Requests|RESOURCE_EXHAUSTED|QuotaExceeded|quota exceeded", re.I),
     "A cloud quota or rate limit was hit: wait and retry once, or raise the quota; do not loop on it."),
    # --- Docker --------------------------------------------------------------------------
    (re.compile(r"permission denied while trying to connect to the Docker daemon socket", re.I),
     "The user cannot use the Docker socket: add them to the docker group (needs a re-login) or start Docker Desktop; do not use sudo."),
    (re.compile(r"denied: requested access to the resource is denied|unauthorized: authentication required", re.I),
     "The container registry refused: docker login to that registry (or the cloud's registry login command) first."),
    # --- Databases / services ---------------------------------------------------------------
    (re.compile(r"ECONNREFUSED [\d.:\[\]]*:(?:5432|3306|6379|27017|5672|9200)|could not connect to server: Connection refused|Can't connect to (?:local )?MySQL server|Connection refused.*(?:5432|3306|6379|27017)", re.I),
     "The database / cache / broker is not running: start it (docker compose up -d <service>, or the local service) and check the connection URL in .env."),
    (re.compile(r"password authentication failed|Access denied for user|authentication failed for user", re.I),
     "Database credentials are wrong: check the user/password in .env / the connection string."),
    (re.compile(r"database \"?\w+\"? does not exist|Unknown database|relation \"\w+\" does not exist|no such table", re.I),
     "The database or its tables are missing: create the database, then run the migrations (project action=migrate)."),
    # --- System / shell ----------------------------------------------------------------------
    (re.compile(r"No space left on device|ENOSPC: no space", re.I),
     "The disk is full: free space (docker builder prune is a deletion - ask the user; clear build caches you created) then retry."),
    (re.compile(r"Too many open files|EMFILE|ENOSPC: System limit for number of file watchers", re.I),
     "Too many open files / file watchers: raise the limit (ulimit -n, fs.inotify.max_user_watches) or close other watchers."),
    (re.compile(r"Read-only file system|EROFS", re.I),
     "The target is on a read-only file system: write inside the project or a writable folder."),
    (re.compile(r"Exec format error|cannot execute binary file|wrong ELF class", re.I),
     "The binary is for another CPU/OS (e.g. amd64 vs arm64, Windows vs Linux): install the build for this machine."),
    (re.compile(r"\$'\\r': command not found|/bin/(?:ba)?sh\^M|bad interpreter: No such file or directory|\r: No such file", re.I),
     "The script has Windows (CRLF) line endings: convert it to LF (sed -i 's/\\r$//' <file> or the editor), then run it again."),
    (re.compile(r"Segmentation fault|core dumped|SIGSEGV", re.I),
     "The program crashed (segfault): usually a native module built for another version - reinstall dependencies; otherwise read the stack above."),
    (re.compile(r"Text file busy|resource busy|EBUSY", re.I),
     "The file is in use by a running process: stop that process (project action=stop for what Navin started) and retry."),
    (re.compile(r"E: Unable to locate package|No package \S+ available|Could not get lock /var/lib/dpkg", re.I),
     "System package install failed: the package name differs on this OS, or another apt/dnf is running; installs into the project (venv, node_modules) avoid system packages."),
    (re.compile(r"running scripts is disabled on this system|UnauthorizedAccess.*PSSecurityException", re.I),
     "PowerShell blocks scripts here: run it with powershell -ExecutionPolicy Bypass -File <script>.ps1, or use the .cmd shim (npm.cmd, npx.cmd)."),
    (re.compile(r"is not recognized as the name of a cmdlet, function, script file, or operable program", re.I),
     "PowerShell does not know this program: install it or use the full path; a bash-only command needs its PowerShell form."),
    (re.compile(r"(?:Filename|path) too long|ENAMETOOLONG|MAX_PATH", re.I),
     "Path too long (Windows): move the project to a shorter path or enable long paths (git config core.longpaths true)."),
    (re.compile(r"command not found|is not recognized as an internal or external command|No such file or directory.*(?:npm|pnpm|yarn|node|python|uv|cargo|go)\b", re.I),
     "A program is missing on this machine: install it (see the hint above) or use the variant the project declares."),
    # Missing dependencies, per ecosystem: the fix is the project's install action.
    (re.compile(r"no required module provides package|missing go\.sum entry|cannot find package \\?\"|go: updates to go\.mod needed", re.I),
     "Go modules are missing or go.sum is stale: run project action=install (go mod download), or `go mod tidy` if a new import was added."),
    (re.compile(r"unresolved import|can't find crate|failed to select a version|no matching package named|error\[E0432\]|error\[E0463\]", re.I),
     "A Rust crate is missing: add it to Cargo.toml [dependencies] (cargo add <crate>), then build again."),
    (re.compile(r"Could not resolve dependencies|Could not find artifact|Could not resolve all (?:files|dependencies)|package [\w.]+ does not exist|ClassNotFoundException|NoClassDefFoundError", re.I),
     "A Java/Kotlin dependency is missing: add it to pom.xml / build.gradle and let Maven/Gradle download it (project action=install)."),
    (re.compile(r"NU1101|NU1102|Unable to find package|The type or namespace name '\w+' could not be found|are you missing a using directive or an assembly reference", re.I),
     "A .NET package is missing: dotnet add package <Name>, then dotnet restore (project action=install)."),
    (re.compile(r"vendor/autoload\.php|Class \"[\w\\\\]+\" not found|Your requirements could not be resolved", re.I),
     "PHP dependencies are missing: run project action=install (composer install), and composer require <package> for a new one."),
    (re.compile(r"cannot load such file|Could not find gem|Bundler::GemNotFound|bundler: command not found", re.I),
     "Ruby gems are missing: run project action=install (bundle install), and add new gems to the Gemfile."),
    (re.compile(r"Target of URI doesn't exist|Could not find package \S+|pub get failed|Because \S+ depends on", re.I),
     "Dart/Flutter packages are missing: run project action=install (flutter/dart pub get), and add new ones to pubspec.yaml."),
    (re.compile(r"Could not find an SCM for dependency|could not find dependency|Unchecked dependencies for environment|\(Mix\) The task \S+ could not be found", re.I),
     "Elixir dependencies are missing: run project action=install (mix deps.get)."),
    (re.compile(r"fatal error: [\w/.+-]+\.h(?:pp)?: No such file|Could not find a package configuration file provided by|Could NOT find \w+", re.I),
     "A C/C++ library or header is missing: install the system package (apt/brew/vcpkg/conan) and re-run the CMake configure step."),
    # Wrong toolchain version.
    (re.compile(r"release version \d+ not supported|UnsupportedClassVersionError|invalid target release|requires Java \d+|class file version", re.I),
     "Wrong JDK version for this project: install / select the JDK the build file asks for (JAVA_HOME, sdkman, toolchains)."),
    (re.compile(r"go\.mod requires go >=|requires go \d|go: downloading go\d", re.I),
     "The Go toolchain is older than go.mod asks: install the Go version from go.mod (or let GOTOOLCHAIN=auto fetch it)."),
    (re.compile(r"requires a different Python|Requires-Python|python_requires|Python \d+\.\d+ or (?:newer|later) is required", re.I),
     "Wrong Python version: create the virtualenv with the version the project asks for (pyproject requires-python / .python-version)."),
    (re.compile(r"requires rustc \d|package `\S+` cannot be built because it requires rustc|edition\s*=?\s*\\?\"2024\\?\" is unstable", re.I),
     "The Rust toolchain is too old: rustup update (or the version in rust-toolchain.toml)."),
    (re.compile(r"Unable to find a matching SDK|The current \.NET SDK does not support targeting|NETSDK1045", re.I),
     "Wrong .NET SDK: install the SDK the project targets (global.json / TargetFramework)."),
    (re.compile(r"Your PHP version \(\S+\) does not satisfy|requires php [<>=^~]", re.I),
     "Wrong PHP version for composer.json: switch PHP version or adjust the platform requirement."),
    (re.compile(r"Your Ruby version is \S+, but your Gemfile specified", re.I),
     "Wrong Ruby version: switch to the one in the Gemfile / .ruby-version (rbenv, asdf)."),
    (re.compile(r"is not installed, run `?pip install|requires the \S+ extra|install ['\"]?\S+\[\w+\]", re.I),
     "A Python extra is missing (e.g. pydantic[email] needs email-validator): add it to the requirements file, then run project action=install."),
    (re.compile(r"Cannot find module|ERR_MODULE_NOT_FOUND|ModuleNotFoundError|No module named|could not find (?:a )?package|vite: not found|next: not found|sh: \d*:? ?\w+: not found", re.I),
     "Dependencies are missing or stale: run project action=install for this unit, then retry."),
    (re.compile(r"EADDRINUSE|address already in use|port \d+ is (?:already )?in use|Port \d+ is in use", re.I),
     "The port is taken: project action=start picks a free port by itself; stop what Navin started with project action=stop."),
    (re.compile(r"Missing script|Could not read package\.json|ENOENT: no such file or directory, open .*package\.json|no package\.json", re.I),
     "Wrong folder or script name: pass the unit= shown in brackets in the project commands."),
    (re.compile(r"ERESOLVE|peer dep|conflicting peer dependency", re.I),
     "npm peer dependency conflict: rerun the install with --legacy-peer-deps (project action=install command=\"npm install --legacy-peer-deps\")."),
    (re.compile(r"npm ci can only install|lockfile.*(?:out of date|not up to date)|frozen-lockfile|ERR_PNPM_OUTDATED_LOCKFILE", re.I),
     "The lockfile does not match package.json: install without the frozen flag (npm install / pnpm install --no-frozen-lockfile)."),
    (re.compile(r"Unsupported engine|engine \"node\" is incompatible|requires Node(?:\.js)? (?:version )?[>=^~]|The engine \"node\"", re.I),
     "Wrong Node.js version for this project: check package.json engines / .nvmrc and switch version (nvm use / fnm use)."),
    (re.compile(r"externally-managed-environment", re.I),
     "System Python refuses installs: create a virtualenv (python -m venv .venv) and install into it."),
    (re.compile(r"Cannot connect to the Docker daemon|docker daemon is not running|error during connect", re.I),
     "Docker is not running: start Docker Desktop / the docker service, then retry."),
    (re.compile(r"JavaScript heap out of memory|ENOMEM|Killed\b", re.I),
     "Out of memory: retry with NODE_OPTIONS=--max-old-space-size=4096 or close other heavy processes."),
    (re.compile(r"EACCES|Permission denied", re.I),
     "Permission denied: do not use sudo; fix the folder's ownership or install into the project (venv, local node_modules)."),
    (re.compile(r"ETIMEDOUT|ENOTFOUND|getaddrinfo|Could not resolve host|certificate verify failed|network is unreachable", re.I),
     "Network problem while downloading: check connectivity or the registry/proxy settings, then retry once."),
    (re.compile(r"error TS\d+|SyntaxError|error\[E\d+\]|could not compile|BUILD FAILED|compilation failed", re.I),
     "The code itself does not compile: read the first error above, fix that file, then run the action again."),
)


def diagnose(output: str) -> list[str]:
    """What a failed command most likely means, most specific first."""
    tail = output[-20_000:]
    return [advice for pattern, advice in _DIAGNOSES if pattern.search(tail)][:3]


def _exit_code(output: str) -> int | None:
    codes = _EXIT_CODE.findall(output)
    return int(codes[-1]) if codes else None


class ProjectTool(Tool):
    """Install / build / start / stop / test the project by action, not by command."""

    _scopes = {"core", "subagent"}

    def __init__(self, workspace: Path | None = None, exec_tool: Any = None, bus: Any = None) -> None:
        self._workspace = workspace
        self._exec = exec_tool
        self._bus = bus

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        from navin.agent.tools.shell import ExecTool

        workspace = Path(str(ctx.workspace)) if getattr(ctx, "workspace", None) else None
        exec_tool = None
        try:
            if ExecTool.enabled(ctx):
                exec_tool = ExecTool.create(ctx)
        except Exception:
            logger.debug("project tool: exec unavailable", exc_info=True)
        return cls(workspace=workspace, exec_tool=exec_tool, bus=getattr(ctx, "bus", None))

    @property
    def name(self) -> str:
        return "project"

    @property
    def description(self) -> str:
        return (
            "Run the project's own commands by ACTION instead of typing them: "
            "detect (show units and commands), install, build, start, stop, restart, "
            "status, logs, test, e2e, lint, typecheck, format, migrate, preview, deploy. "
            "Navin picks "
            "the command the project declares (validated > declared script > framework "
            "convention), runs it in the right folder, installs dependencies first when "
            "missing, starts web apps on a free port, stops only what it started, "
            "explains failures and remembers what worked. Pass unit= (a folder shown in "
            "brackets in the project commands) for a sub-project. Pass command= only when "
            "no listed command fits; it is remembered once it succeeds. Deletions "
            "(destroy, delete, uninstall, down, rm, prune, force push, DROP) are never run "
            "through this tool. host= runs any action on a remote machine over SSH "
            "(sync pushes the code there first, without deleting anything on it)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["detect", *_RUN_ACTIONS, "stop", "restart", "status", "logs", "sync"],
                    "description": "What to do.",
                },
                "unit": {
                    "type": "string",
                    "description": "Sub-project folder (e.g. app, apps/web, backend). Omit for the main one.",
                },
                "command": {
                    "type": "string",
                    "description": "Only when no listed command fits this action: the exact command to run.",
                },
                "host": {
                    "type": "string",
                    "description": (
                        "Run on a remote machine over SSH: a target from .navin/remote.json, an "
                        "~/.ssh/config alias or user@host. Key/agent auth only, never a password."
                    ),
                },
                "remote_path": {
                    "type": "string",
                    "description": "Project folder on the remote machine (when .navin/remote.json does not set it).",
                },
                "fresh": {
                    "type": "boolean",
                    "description": (
                        "Run again even when an identical green build/test/lint/typecheck "
                        "exists and no file changed since (default: reuse it)."
                    ),
                },
            },
            "required": ["action"],
        }

    def call_read_only(self, arguments: Any) -> bool:
        """detect and status only look; every other action runs something."""
        action = str((arguments or {}).get("action") or "") if isinstance(arguments, dict) else ""
        return action in ("detect", "status", "logs")

    # -- helpers ---------------------------------------------------------------------

    def _root(self) -> Path | None:
        ctx = current_request_context()
        workspace = self._workspace
        if ctx is not None and getattr(ctx, "workspace", None):
            workspace = Path(str(ctx.workspace))
        return Path(workspace).expanduser().resolve() if workspace else None

    @staticmethod
    def _owner() -> str:
        ctx = current_request_context()
        return str(getattr(ctx, "session_key", "") or "") if ctx else ""

    def _key(self, root: Path, unit: str) -> tuple[str, str, str]:
        return (self._owner(), str(root), unit)

    async def _run_once(self, root: Path, unit: str, command: str, action: str) -> tuple[bool, str]:
        deleting = deletion_in(command)
        if deleting:
            return False, (
                f"Error: refused - `{deleting}` deletes something, and the project tool never "
                "deletes (infrastructure, containers, data, files or history). Ask the user to "
                "run it themselves if they really want it."
            )
        if self._exec is None:
            return False, "Error: the exec tool is disabled in Settings; the project tool cannot run commands."
        folder = root if unit == "." else root / unit
        output = await self._exec.execute(
            command=command, working_dir=str(folder), timeout=_TIMEOUTS.get(action, 600),
        )
        text = str(output)
        if isinstance(output, ToolResult) and output.is_error:
            return False, text
        if "Process running. session_id:" in text:
            return True, text  # moved to the background: still going, not failed
        code = _exit_code(text)
        return (code == 0 if code is not None else not text.lstrip().startswith("Error")), text

    @staticmethod
    def _report(action: str, unit: str, candidate: runtime.Candidate | None, command: str, ok: bool, output: str) -> str:
        source = f"{candidate.source}, confidence {candidate.confidence}" if candidate else "given by the agent"
        status = "OK" if ok else "FAILED"
        lines = [f"project {action} [{unit}] {status}: `{command}` ({source})"]
        body = output.strip()
        if len(body) > _TAIL:
            body = "..." + body[-_TAIL:]
        if body:
            lines.append(body)
        if not ok:
            hints = diagnose(output)
            if hints:
                lines.append("Likely cause:")
                lines += [f"- {hint}" for hint in hints]
            lines.append("Fix the cause before running this action again; do not retry it unchanged.")
        return "\n".join(lines)

    # -- actions ---------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> Any:
        action = str(kwargs.get("action") or "").strip().lower()
        unit_arg = str(kwargs.get("unit") or "").strip() or None
        override = str(kwargs.get("command") or "").strip() or None
        # resolve() + is_dir() cross the Windows/WSL boundary on a
        # \\wsl.localhost project: keep them off the shared event loop.
        root = await _to_thread(self._root)
        if root is None or not await _to_thread(root.is_dir):
            return ToolResult.error("Error: no project folder is open.")
        profile = await _to_thread(runtime.resolve, root)

        if action == "detect":
            from navin.agent.project_runbook import project_runbook

            return "\n".join(await _to_thread(project_runbook, root))
        host = str(kwargs.get("host") or "").strip() or None
        remote_path = str(kwargs.get("remote_path") or "").strip() or None
        if host or action == "sync":
            return await self._remote(root, profile, action, unit_arg, override, host, remote_path)
        if action == "status":
            return await self._status(root, profile, unit_arg)
        if action == "logs":
            return await self._logs(root, profile, unit_arg)
        if action == "stop":
            return await self._stop(root, profile, unit_arg)
        if action == "restart":
            stopped = await self._stop(root, profile, unit_arg)
            started = await self._run_action(root, profile, "start", unit_arg, override)
            return f"{stopped}\n{started}"
        if action in _RUN_ACTIONS:
            return await self._run_action(
                root, profile, action, unit_arg, override, fresh=bool(kwargs.get("fresh")),
            )
        return ToolResult.error(f"Error: unknown action {action!r}.")

    async def _run_action(
        self, root: Path, profile: runtime.Profile, action: str, unit_arg: str | None, override: str | None,
        *, fresh: bool = False,
    ) -> Any:
        unit = profile.unit_for(action, unit_arg) if not override else (profile.unit(unit_arg or ".") or profile.unit_for(action, unit_arg))
        unit_path = unit.path if unit else (unit_arg or ".")
        if unit_arg and unit is None:
            known = ", ".join(u.path for u in profile.units) or "none"
            return ToolResult.error(f"Error: no project unit {unit_arg!r}. Known units: {known}.")
        candidate = None if override else (unit.best(action) if unit else None)
        if candidate is None and not override:
            listed = ", ".join(sorted(unit.candidates)) if unit else ""
            return ToolResult.error(
                f"Error: this project declares no command for {action!r}"
                + (f" in [{unit_path}] (it has: {listed})" if listed else "")
                + ". Look once in the README / CI, then pass command= with the exact command."
            )
        command = override or candidate.command
        if deletion_in(command):
            ok, refusal = await self._run_once(root, unit_path, command, action)
            return ToolResult.error(refusal)
        folder = root if unit_path == "." else root / unit_path
        if candidate is not None and candidate.missing_tool:
            hint = candidate.install_hint
            return ToolResult.error(
                f"Error: `{command}` needs {candidate.missing_tool}, which is not installed on this "
                "machine." + (f" Install it: {hint}." if hint else "")
                + " Or use another listed command for this action."
            )
        notes: list[str] = []

        # Dependencies first, deterministically: the agent never guesses it.
        if unit is not None and action in _NEED_DEPS and unit.needs_install:
            install_unit = unit
            root_unit = profile.unit(".")
            if unit.path != "." and root_unit is not None and "Monorepo" in root_unit.kinds and root_unit.best("install"):
                install_unit = root_unit  # a workspace installs from its root
            install = install_unit.best("install")
            if install is not None and not install.missing_tool:
                ok, output = await self._run_once(root, install_unit.path, install.command, "install")
                notes.append(self._report("install", install_unit.path, install, install.command, ok, output))
                if not ok:
                    return ToolResult.error("\n".join(notes))
                await _to_thread(runtime.remember, root, install_unit.path, "install", install.command)

        if action == "start" and candidate is not None and "needs a build first" in candidate.note:
            build = unit.best("build") if unit else None
            if build is not None:
                ok, output = await self._run_once(root, unit_path, build.command, "build")
                notes.append(self._report("build", unit_path, build, build.command, ok, output))
                if not ok:
                    return ToolResult.error("\n".join(notes))

        reuse_key = (str(root), unit_path, action, command)
        fingerprint = None
        if action in _REUSABLE_ACTIONS and not notes:
            fingerprint = await _to_thread(_unit_fingerprint, root, unit_path, profile)
            green = _GREEN_RUNS.get(reuse_key)
            if (not fresh and fingerprint is not None and green is not None
                    and green[0] == fingerprint and time.time() - green[1] < _REUSE_TTL_S):
                at = time.strftime("%H:%M:%S", time.localtime(green[1]))
                return (
                    f"project {action} [{unit_path}]: no file changed since the identical "
                    f"passing run at {at}; its result still holds (pass fresh=true to run it "
                    f"again).\n{green[2]}"
                )

        if action == "start":
            result = await self._start(root, unit, unit_path, folder, command, candidate)
        else:
            run_from = candidate.run_from if candidate is not None else unit_path
            ok, output = await self._run_once(root, run_from, command, action)
            result = self._report(action, unit_path, candidate, command, ok, output)
            if fingerprint is not None:
                if ok:
                    _GREEN_RUNS[reuse_key] = (fingerprint, time.time(), str(result))
                else:
                    _GREEN_RUNS.pop(reuse_key, None)
            if ok:
                await _to_thread(runtime.remember, root, unit_path, action, command)
            else:
                if candidate is not None and candidate.source == "validated":
                    await _to_thread(runtime.forget, root, unit_path, action)
                return ToolResult.error("\n".join([*notes, result]))
        return "\n".join([*notes, str(result)])

    async def _start(
        self, root: Path, unit: runtime.Unit | None, unit_path: str, folder: Path, command: str,
        candidate: runtime.Candidate | None,
    ) -> Any:
        from navin.agent.tools import preview_server as ps

        key = self._key(root, unit_path)
        running = _TRACKED.get(key)
        if running is not None and await _alive(running):
            where = f" at {running.url}" if running.url else ""
            return f"project start [{unit_path}]: already running{where} (`{running.command}`). Use action=restart to restart it."

        if command.startswith(("docker compose up", "docker-compose up")):
            ok, output = await self._run_once(root, unit_path, command, "start")
            if ok:
                await _to_thread(runtime.remember, root, unit_path, "start", command)
                _TRACKED[key] = Tracked(unit_path, command, None, None, time.time())
                return self._report("start", unit_path, candidate, command, ok, output)
            return ToolResult.error(self._report("start", unit_path, candidate, command, ok, output))

        port = unit.dev_port if unit else None
        if port:
            # Reads package.json / vite config: 9P round trips on a WSL project.
            detected = await _to_thread(ps._detect_server, folder)
            same = detected is not None and detected.command == command and detected.cwd == folder
            flag = detected.port_flag if same else None
            server = ps.ProjectDevServer(cwd=folder, command=command, port=port, label=unit_path, port_flag=flag)
            session_id, error, server = await ps.start_project_dev_server(server, owner_session_key=self._owner())
            if error:
                if candidate is not None and candidate.source == "validated":
                    await _to_thread(runtime.forget, root, unit_path, "start")
                return ToolResult.error(self._report("start", unit_path, candidate, server.command, False, error))
            url = server.url
            up = await ps.wait_for_url(url, timeout_s=75.0) if url else False
            if up and server.port:
                url = ps.loopback_url(server.port)  # ::1 for a default Vite on Windows
            if not up:
                tail = ""
                if session_id:
                    from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

                    try:
                        tail = await DEFAULT_EXEC_SESSION_MANAGER.peek(session_id)
                    except Exception:
                        tail = ""
                _TRACKED[key] = Tracked(unit_path, server.command, session_id, url, time.time())
                return ToolResult.error(self._report(
                    "start", unit_path, candidate, server.command, False,
                    (tail or "") + f"\n{url} did not answer within 75s.",
                ))
            _TRACKED[key] = Tracked(unit_path, server.command, session_id, url, time.time(),
                                    watcher=self._watch(session_id, unit_path, server.command))
            await _to_thread(runtime.remember, root, unit_path, "start", command)
            return (
                f"project start [{unit_path}] OK: running at {url} (`{server.command}`). "
                "Call open_preview with this URL for the user to see it."
            )

        # Not a web server (worker, API without a known port, desktop app): background session.
        if self._exec is None:
            return ToolResult.error("Error: the exec tool is disabled in Settings.")
        output = str(await self._exec.execute(command=command, working_dir=str(folder), background=True))
        match = _SESSION_ID.search(output)
        code = _exit_code(output)
        if match:
            _TRACKED[key] = Tracked(unit_path, command, match.group(1), None, time.time(),
                                    watcher=self._watch(match.group(1), unit_path, command))
            await _to_thread(runtime.remember, root, unit_path, "start", command)
            return self._report("start", unit_path, candidate, command, True, output) + "\nRunning in the background; stop it with project action=stop."
        ok = code == 0
        report = self._report("start", unit_path, candidate, command, ok, output)
        return report if ok else ToolResult.error(report)

    async def _stop(self, root: Path, profile: runtime.Profile, unit_arg: str | None) -> str:
        owner = self._owner()
        keys = [k for k in _TRACKED if k[0] == owner and k[1] == str(root) and (unit_arg is None or k[2] == unit_arg.strip("/"))]
        if not keys:
            return (
                f"project stop: nothing started by Navin is running{f' for [{unit_arg}]' if unit_arg else ''}. "
                "Navin only stops what it started; other processes are left alone."
            )
        lines = []
        for key in keys:
            tracked = _TRACKED.pop(key)
            if tracked.watcher is not None:
                tracked.watcher.cancel()
            if tracked.session_id:
                from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

                try:
                    await DEFAULT_EXEC_SESSION_MANAGER.kill_session(tracked.session_id)
                    lines.append(f"project stop [{tracked.unit}]: stopped `{tracked.command}`.")
                except KeyError:
                    lines.append(f"project stop [{tracked.unit}]: `{tracked.command}` had already exited.")
            else:
                unit = profile.unit(tracked.unit)
                stop = unit.best("stop") if unit else None
                if stop is None:
                    lines.append(f"project stop [{tracked.unit}]: no stop command declared for `{tracked.command}`.")
                    continue
                ok, output = await self._run_once(root, tracked.unit, stop.command, "stop")
                lines.append(self._report("stop", tracked.unit, stop, stop.command, ok, output))
        return "\n".join(lines)

    async def _remote(
        self, root: Path, profile: runtime.Profile, action: str, unit_arg: str | None,
        override: str | None, host: str | None, remote_path: str | None,
    ) -> Any:
        """The same actions on a remote machine, over the user's own SSH setup."""
        from navin.agent import project_remote as remote

        target = remote.resolve_target(root, host, remote_path)
        if isinstance(target, str):
            return ToolResult.error(f"Error: {target}")
        unit_path = (unit_arg or ".").strip("/") or "."
        key = (self._owner(), f"{root}@{target.host}", unit_path)
        log = f".navin/remote-{unit_path.replace('/', '-').strip('.') or 'app'}.log"

        if action == "sync":
            command = remote.sync_command(root, target)
            ok, output = await self._run_once(root, ".", command, "sync")
            report = self._report("sync", unit_path, None, command, ok, output)
            return report if ok else ToolResult.error(report)
        if action in ("stop", "status", "logs", "restart"):
            tracked = _TRACKED.get(key)
            if tracked is None or tracked.remote is None:
                if action != "restart":
                    return f"project {action} [{target.name}]: nothing started by Navin is running there; other processes are left alone."
            else:
                _target, pid, tracked_log = tracked.remote
                inner = {
                    "stop": remote.remote_stop(pid),
                    "restart": remote.remote_stop(pid),
                    "status": f"kill -0 {pid} 2>/dev/null && echo running || echo stopped",
                    "logs": f"tail -n 200 {shlex.quote(tracked_log)}",
                }[action]
                ok, output = await self._run_once(root, ".", remote.ssh_command(target, inner), action)
                if action in ("stop", "restart"):
                    _TRACKED.pop(key, None)
                if action != "restart":
                    return self._report(action, f"{target.name}:{unit_path}", None, inner, ok, output)
            action = "start"

        unit = profile.unit(unit_path) if unit_arg else profile.unit_for(action)
        candidate = None if override else (unit.best(action) if unit else None)
        if candidate is None and not override:
            return ToolResult.error(
                f"Error: no declared command for {action!r}; pass command= with what to run on {target.name}."
            )
        command = override or candidate.command
        if deletion_in(command):
            ok, refusal = await self._run_once(root, ".", command, action)
            return ToolResult.error(refusal)
        if candidate is not None and candidate.run_from not in (".", unit_path):
            target = remote.RemoteTarget(target.name, target.host, f"{target.path.rstrip('/')}/{candidate.run_from}")
        elif unit is not None and unit.path != ".":
            target = remote.RemoteTarget(target.name, target.host, f"{target.path.rstrip('/')}/{unit.path}")

        if action == "start":
            wrapped = remote.ssh_background(target, command, log)
            ok, output = await self._run_once(root, ".", wrapped, "start")
            pid = re.search(r"NAVIN_PID=(\d+)", output)
            if ok and pid:
                # A server that dies on start (port taken, missing deps) must not be
                # reported as running: give it a moment, then check the PID.
                probe = (
                    f"sleep {_REMOTE_SETTLE_S}; kill -0 {pid.group(1)} 2>/dev/null && echo NAVIN_ALIVE "
                    f"|| {{ echo NAVIN_DEAD; tail -n 40 {shlex.quote(log)}; }}"
                )
                _alive_ok, alive_out = await self._run_once(root, ".", remote.ssh_command(target, probe), "status")
                if "NAVIN_ALIVE" not in alive_out:
                    return ToolResult.error(self._report(
                        "start", f"{target.name}:{unit_path}", candidate, command, False,
                        f"The process exited right after starting on {target.host}.\n{alive_out}",
                    ))
                _TRACKED[key] = Tracked(unit_path, command, None, None, time.time(),
                                        remote=(target, pid.group(1), log))
                return (
                    f"project start [{target.name}:{unit_path}] OK: `{command}` runs on {target.host} "
                    f"(pid {pid.group(1)}, log {target.path}/{log}). Use action=logs / status / stop with the same host."
                )
            return ToolResult.error(self._report("start", f"{target.name}:{unit_path}", candidate, command, False, output))

        wrapped = remote.ssh_command(target, command)
        ok, output = await self._run_once(root, ".", wrapped, action)
        report = self._report(action, f"{target.name}:{unit_path}", candidate, command, ok, output)
        if not ok and re.search(r"Permission denied \(publickey|Host key verification failed|Could not resolve hostname|Connection (?:refused|timed out)", output):
            report += (
                "\nSSH could not connect. Navin uses the user's own SSH setup (keys, ssh-agent, "
                "~/.ssh/config) and never a password: ask the user to make `ssh "
                f"{target.host}` work without a prompt."
            )
        return report if ok else ToolResult.error(report)

    async def _logs(self, root: Path, profile: runtime.Profile, unit_arg: str | None) -> Any:
        owner = self._owner()
        lines: list[str] = []
        for (o, r, unit), tracked in list(_TRACKED.items()):
            if o != owner or r != str(root) or (unit_arg and unit != unit_arg.strip("/")):
                continue
            if tracked.session_id:
                from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

                try:
                    tail = await DEFAULT_EXEC_SESSION_MANAGER.peek(tracked.session_id)
                except KeyError:
                    tail = "(the process has exited; its session is closed)"
                lines.append(f"[{unit}] `{tracked.command}`:\n{tail[-_TAIL:]}")
            else:
                unit_obj = profile.unit(unit)
                logs = unit_obj.best("logs") if unit_obj else None
                if logs is not None:
                    ok, output = await self._run_once(root, logs.run_from, logs.command, "logs")
                    lines.append(self._report("logs", unit, logs, logs.command, ok, output))
        if not lines:
            unit_obj = profile.unit_for("logs", unit_arg)
            logs = unit_obj.best("logs") if unit_obj else None
            if logs is not None:
                ok, output = await self._run_once(root, logs.run_from, logs.command, "logs")
                return self._report("logs", unit_obj.path, logs, logs.command, ok, output)
            return "No process started by Navin is running for this project, so there are no logs to show."
        return "\n\n".join(lines)

    def _watch(self, session_id: str | None, unit: str, command: str) -> Any:
        """Tell the agent when a running server logs a new error (bounded, deduplicated)."""
        ctx = current_request_context()
        if not session_id or self._bus is None or ctx is None or not getattr(ctx, "chat_id", None):
            return None
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        return loop.create_task(_watch_logs(
            self._bus, session_id, unit, command,
            channel=str(ctx.channel), chat_id=str(ctx.chat_id),
            session_key=str(ctx.session_key or f"{ctx.channel}:{ctx.chat_id}"),
        ))

    async def _status(self, root: Path, profile: runtime.Profile, unit_arg: str | None) -> str:
        owner = self._owner()
        lines = []
        for (o, r, unit), tracked in list(_TRACKED.items()):
            if o != owner or r != str(root) or (unit_arg and unit != unit_arg.strip("/")):
                continue
            alive = await _alive(tracked)
            age = int(time.time() - tracked.started_at)
            state = "running" if alive else "stopped"
            lines.append(f"[{unit}] {state} for {age}s: `{tracked.command}`" + (f" at {tracked.url}" if tracked.url else ""))
        if not lines:
            lines.append("Nothing started by Navin is running for this project.")
        for unit in profile.units:
            if unit.needs_install:
                lines.append(f"[{unit.path}] dependencies are not installed (project action=install unit={unit.path}).")
        return "\n".join(lines)


_LOG_ERROR = re.compile(
    r"Traceback \(most recent call last\)|\b\w*Error:|\bERROR\b|\bException\b|Failed to compile"
    r"|Internal server error|Unhandled|panic:|\bFATAL\b|\bSEVERE\b|\s500\s",
)
_LOG_NOT_ERROR = re.compile(r"\b0 errors?\b|no errors|without errors|error_reporting|errorHandler", re.IGNORECASE)
_WATCH_EVERY_S = 3.0
_ALERT_GAP_S = 60.0
_MAX_ALERTS = 3


def new_log_errors(tail: str, seen: set[str]) -> list[str]:
    """Error excerpts in ``tail`` not reported yet (``seen`` keeps their signatures)."""
    lines = tail.splitlines()[-300:]
    found: list[str] = []
    for index, line in enumerate(lines):
        if not _LOG_ERROR.search(line) or _LOG_NOT_ERROR.search(line):
            continue
        signature = re.sub(r"\d+", "#", line.strip())[:160]
        if signature in seen:
            continue
        seen.add(signature)
        found.append("\n".join(lines[index:index + 5]))
    return found


async def _watch_logs(
    bus: Any, session_id: str, unit: str, command: str, *, channel: str, chat_id: str, session_key: str,
) -> None:
    """Poll the server's output; wake the agent on a new error, at most a few times."""
    import asyncio

    from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER
    from navin.bus.events import InboundMessage

    seen: set[str] = set()
    alerts = 0
    last = 0.0
    while alerts < _MAX_ALERTS:
        await asyncio.sleep(_WATCH_EVERY_S)
        try:
            tail = await DEFAULT_EXEC_SESSION_MANAGER.peek(session_id)
        except KeyError:
            return  # the process ended: the completion announcer takes over
        errors = new_log_errors(tail, seen)
        if not errors or time.monotonic() - last < _ALERT_GAP_S:
            continue
        alerts += 1
        last = time.monotonic()
        body = "\n---\n".join(errors[-3:])
        try:
            await bus.publish_inbound(InboundMessage(
                channel="system",
                sender_id="project",
                chat_id=f"{channel}:{chat_id}",
                content=(
                    f"[Server error] [{unit}] `{command}` is still running but logged:\n{body}\n"
                    "Fix the cause in the code; the dev server reloads by itself, so do not "
                    "restart it unless the fix needs it. If it is not related to your work, "
                    f"say so and move on. (alert {alerts}/{_MAX_ALERTS})"
                ),
                session_key_override=session_key,
                metadata={"injected_event": "server_error", "exec_session_id": session_id},
            ))
        except Exception:
            logger.debug("project: server error alert failed", exc_info=True)
            return


async def _alive(tracked: Tracked) -> bool:
    if tracked.session_id:
        from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

        try:
            infos = await DEFAULT_EXEC_SESSION_MANAGER.list()
        except Exception:
            return False
        return any(info.session_id == tracked.session_id and info.returncode is None for info in infos)
    if tracked.url:
        from navin.agent.tools.preview_server import http_reachable_async

        return await http_reachable_async(tracked.url)
    return True  # compose: docker owns it; status shows it as started


async def _to_thread(fn: Any, *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)
