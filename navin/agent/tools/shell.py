# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shell execution tool."""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import shlex
import shutil
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from loguru import logger
from pydantic import Field

from navin.agent.command_output import (
    compact_session_body,
    filter_command_output,
    should_replace_with_compaction,
)
from navin.agent.tool_output import (
    current_tool_call_id,
    emit_tool_meta,
    emit_tool_output,
    tool_output_streaming_enabled,
)
from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import (
    RequestContext,
    current_request_context,
    current_request_session_key,
)
from navin.agent.tools.exec_env import build_exec_env, forge_tokens_from_tools_config
from navin.agent.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_YIELD_MS,
    MAX_OUTPUT_CHARS,
    MAX_YIELD_MS,
    clamp_session_int,
    format_session_poll,
)
from navin.agent.tools.sandbox import (
    is_unrestricted_exec_path,
    sandbox_result_note,
    sandbox_tool_description,
    wrap_command,
)
from navin.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.bus.outbound_events import AgentExecEvent, outbound_message_for_event
from navin.config.paths import get_media_dir
from navin.config_base import Base
from navin.security.workspace_access import current_scope_allows_loopback, current_tool_workspace
from navin.security.workspace_policy import is_path_within, project_rooted_path
from navin.utils import wsl
from navin.utils.proc import (
    kill_posix_process_group,
    kill_windows_process_tree,
    no_window_kwargs,
)
from navin.utils.task_progress import (
    emit_task_progress,
    parse_progress_from_output,
)

_IS_WINDOWS = sys.platform == "win32"

# ``shutil.which`` walks PATH (a dozen stats, slow on Windows network shares)
# and ran on the event loop for every exec call. Resolved programs are kept
# for a minute: long enough to cost nothing, short enough that a shell
# installed mid-session is found.
_WHICH_TTL_S = 60.0
_which_memo: dict[str, tuple[float, str | None]] = {}


def _which_cached(program: str) -> str | None:
    now = time.monotonic()
    hit = _which_memo.get(program)
    if hit is not None and now - hit[0] < _WHICH_TTL_S:
        return hit[1]
    found = shutil.which(program)
    _which_memo[program] = (now, found)
    return found

_SCAN_RE = re.compile(r"(?:^|[\s;&|(])(?:(?:find|du|tree|rg)\b|grep\s+[^|;&]*-\w*[rR]|ls\s+[^|;&]*-\w*R)")
# Both directions of the WSL/Windows boundary: a Windows drive seen from WSL
# (/mnt/c) and a WSL tree seen from Windows (\\wsl$ or \\wsl.localhost).
_MOUNT_RE = re.compile(
    r"/mnt/[a-z](?:/|\b)|[\\/]{2}wsl(?:\$|\.localhost)[\\/]"
    # macOS: network shares / external disks and file-provider cloud folders
    # (iCloud Drive, Dropbox, OneDrive, Google Drive): each stat can trigger a
    # network round trip or an on-demand download.
    r"|/Volumes/|/Library/Mobile Documents/|/Library/CloudStorage/",
    re.IGNORECASE,
)
_WIN_SCAN_RE = re.compile(r"\b(?:dir\b[^|;&]*/s|(?:get-childitem|gci|ls)\b[^|;&]*-recurse)", re.IGNORECASE)
_WIN_BOUND_RE = re.compile(r"-depth\s+\d", re.IGNORECASE)


def unbounded_mount_scan(command: str) -> str | None:
    """Refuse a recursive walk of a Windows drive (/mnt/<x>) with no bound.

    Crossing the WSL/Windows boundary costs milliseconds per file, so a bare
    ``find /mnt/c/...`` can hang for minutes. A walk is allowed when it is
    bounded by ``-maxdepth``/``--max-depth``/``-L`` or wrapped in ``timeout``.
    """
    for segment in re.split(r"&&|\|\||;|\|", command):
        if not _MOUNT_RE.search(segment):
            continue
        if not (_SCAN_RE.search(segment) or _WIN_SCAN_RE.search(segment)):
            continue
        if re.search(r"-maxdepth\s+\d|--max-depth[=\s]+\d|\btree\b[^|;&]*-L\s*\d|\btimeout\s+\d", segment):
            continue
        if _WIN_BOUND_RE.search(segment):
            continue
        return (
            "Error: recursive scan of a slow mount (/mnt/<drive>, \\\\wsl$, macOS "
            "/Volumes or iCloud/CloudStorage) without a bound. These walks are very slow. Add -maxdepth N "
            "(--max-depth N, Get-ChildItem -Depth N) and/or prefix with 'timeout 20', "
            "or target the exact folder."
        )
    return None


def _desktop_logs_note() -> str:
    """Tell the agent where Desktop conversation logs really live."""
    try:
        from navin.config.paths import get_webui_dir
        webui = wsl.windows_path_to_wsl(str(get_webui_dir())) or str(get_webui_dir())
    except Exception:
        return ""
    return (
        f"Navin Desktop conversation logs are {webui}/websocket_<id>.jsonl "
        "(not workspace/sessions); read them directly, never walk /mnt/c with an "
        "unbounded find/grep -r. "
    )


def _runtime_bin_dir() -> str:
    """The directory to put first on PATH so ``python`` means navin's Python.

    A source install points at ``.venv/bin``, which is what gives a skill's
    ``python3 build_report.py`` access to python-docx, openpyxl, pandas and the
    rest of navin's dependencies. A packaged build has the same libraries
    embedded but no interpreter directory, so it gets shims that forward to the
    ``navin python`` subcommand; without them every skill that writes a script
    fails on a machine that happens to have no Python at all, which on Windows is
    most of them.
    """
    from navin.python_runtime import interpreter_shim_dir, packaged

    if not packaged():
        return str(Path(sys.executable).resolve().parent)
    from navin.config.paths import get_runtime_subdir

    try:
        shims = interpreter_shim_dir(get_runtime_subdir("bin"))
    except OSError:
        return ""
    return str(shims) if shims else ""


_PYTHON_PROJECT_MARKERS = (
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "manage.py",
    "poetry.lock", "uv.lock", "requirements-dev.txt", ".python-version",
)
_VENV_NAMES = (".venv", "venv", "env", ".env-py")


def _python_project_root(cwd: Path, *, depth: int = 6) -> Path | None:
    """The nearest folder (cwd or a parent) that is a Python project, or None."""
    try:
        here = cwd.expanduser().resolve()
    except OSError:
        return None
    for folder in [here, *list(here.parents)[:depth]]:
        if any((folder / name).exists() for name in _PYTHON_PROJECT_MARKERS):
            return folder
        if any((folder / name / "pyvenv.cfg").is_file() for name in _VENV_NAMES):
            return folder
    return None


def _project_venv_bin(cwd: Path, project: Path) -> Path | None:
    """bin/ (Scripts\\ on Windows) of the project's virtualenv, nearest first."""
    sub = "Scripts" if _IS_WINDOWS else "bin"
    try:
        here = cwd.expanduser().resolve()
    except OSError:
        return None
    chain = [here, *here.parents]
    for folder in chain:
        for name in _VENV_NAMES:
            candidate = folder / name
            if (candidate / "pyvenv.cfg").is_file() and (candidate / sub).is_dir():
                return candidate / sub
        if folder == project:
            break
    return None


def _navin_python_dir() -> str:
    try:
        from navin.config.paths import get_runtime_subdir
        from navin.python_runtime import navin_python_dir

        found = navin_python_dir(get_runtime_subdir("bin") / "navin")
    except Exception:
        return ""
    return str(found) if found else ""


def _py_launcher_dir() -> str:
    try:
        from navin.config.paths import get_runtime_subdir
        from navin.python_runtime import py_launcher_dir

        found = py_launcher_dir(get_runtime_subdir("bin") / "py-launcher")
    except Exception:
        return ""
    return str(found) if found else ""


async def request_exec_policy_reload(bus: Any, *, timeout: float = 10.0) -> dict[str, Any]:
    """Ask the running agent loop to hot-apply the exec policy from config."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
        InboundMessage,
    )

    loop = asyncio.get_running_loop()
    ack: asyncio.Future[dict[str, Any]] = loop.create_future()
    await bus.publish_inbound(
        InboundMessage(
            channel="system",
            sender_id="webui-settings",
            chat_id="runtime",
            content=RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
            metadata={
                INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
                RUNTIME_CONTROL_ACK: ack,
            },
        )
    )
    try:
        result = await asyncio.wait_for(ack, timeout=timeout)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "message": "Exec policy hot reload timed out; restart navin to apply.",
            "requires_restart": True,
        }
    return result if isinstance(result, dict) else {
        "ok": False,
        "message": "Exec policy hot reload returned an unexpected response.",
        "requires_restart": True,
    }


async def handle_exec_policy_reload(state: Any, msg: Any, registry: Any) -> bool:
    """Runtime-control handler: re-read config and hot-apply the exec policy."""
    from navin.bus.events import (
        INBOUND_META_RUNTIME_CONTROL,
        RUNTIME_CONTROL_ACK,
        RUNTIME_CONTROL_EXEC_POLICY_RELOAD,
    )

    metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
    if metadata.get(INBOUND_META_RUNTIME_CONTROL) != RUNTIME_CONTROL_EXEC_POLICY_RELOAD:
        return False

    ack = metadata.get(RUNTIME_CONTROL_ACK)
    try:
        from navin.config.loader import load_config

        config = load_config()
        approvals = getattr(state, "approvals", None)
        apply_config = getattr(approvals, "apply_config", None)
        if callable(apply_config):
            apply_config(config.tools.approvals)
        tools_config = getattr(state, "tools_config", None)
        if tools_config is not None:
            tools_config.approvals = config.tools.approvals
            tools_config.exec = config.tools.exec
            tools_config.security_profile = config.tools.security_profile
            tools_config.restrict_to_workspace = config.tools.restrict_to_workspace
        if hasattr(state, "exec_config"):
            state.exec_config = config.tools.exec
        restrict = bool(config.tools.restrict_to_workspace)
        if hasattr(state, "restrict_to_workspace"):
            state.restrict_to_workspace = restrict
        scopes = getattr(state, "workspace_scopes", None)
        if scopes is not None:
            from navin.security.workspace_access import WorkspaceScopeResolver

            state.workspace_scopes = WorkspaceScopeResolver(
                default_workspace=scopes.default_workspace,
                default_restrict_to_workspace=restrict,
                scoped_channel=scopes.scoped_channel,
            )
        subagents = getattr(state, "subagents", None)
        if subagents is not None and hasattr(subagents, "restrict_to_workspace"):
            subagents.restrict_to_workspace = restrict
        seen: set[int] = set()
        for name in list(getattr(registry, "_tools", {})):
            item = registry.get(name)
            if item is None or id(item) in seen:
                continue
            seen.add(id(item))
            if hasattr(item, "_restrict_to_workspace"):
                item._restrict_to_workspace = restrict
        from navin.agent.tools.exec_session import WriteStdinTool

        stdin = registry.get("write_stdin")
        if isinstance(stdin, WriteStdinTool):
            stdin.apply_policy(config.tools.exec, config.tools.approvals)
        tool = registry.get("exec")
        if isinstance(tool, ExecTool):
            tool.apply_policy(
                allow_patterns=config.tools.exec.allow_patterns,
                deny_patterns=config.tools.exec.deny_patterns,
                restrict_to_workspace=config.tools.restrict_to_workspace,
                builtin_deny_rules=config.tools.exec.builtin_deny_rules,
                ask_every_command=bool(
                    config.tools.approvals.enabled
                    and getattr(config.tools.approvals, "exec_ask", "destructive")
                    == "always"
                ),
            )
            result: dict[str, Any] = {"ok": True, "requires_restart": False}
        else:
            result = {
                "ok": True,
                "requires_restart": False,
                "message": "exec tool not active; policy will apply on next start",
            }
    except Exception as exc:
        logger.exception("exec policy hot reload failed")
        result = {
            "ok": False,
            "message": "Exec policy hot reload failed; restart navin to apply.",
            "requires_restart": True,
            "error": str(exc),
        }
    if isinstance(ack, asyncio.Future) and not ack.done():
        ack.set_result(result)
    return True


def _reap_pid(pid: int) -> None:
    """Best-effort ``waitpid`` to reap a child and prevent zombies.

    Call this after killing or after normal completion of any subprocess
    as a safety net - asyncio's child-watcher *should* have reaped it,
    but in containers / edge-cases it sometimes doesn't.

    Uses ``os`` capability checks rather than ``_IS_WINDOWS`` so this is
    safe when tests patch the platform flag while still running on Windows
    (``os.waitpid`` / ``os.WNOHANG`` do not exist there).
    """
    waitpid = getattr(os, "waitpid", None)
    wnohang = getattr(os, "WNOHANG", None)
    if waitpid is None or wnohang is None:
        return
    try:
        waitpid(pid, wnohang)
    except (ProcessLookupError, ChildProcessError):
        # Already reaped, or not our child - both are fine.
        pass
    except OSError as exc:
        logger.debug("_reap_pid({}): {}", pid, exc)


# Policy note appended to recoverable workspace-boundary guard errors.
_WORKSPACE_BOUNDARY_NOTE = (
    "\n\nNote: restrict to workspace is on. A card in this chat asks before "
    "leaving the project; do not work around it with shell tricks."
)


# A ready-made deny set for an operator who wants one, applied only when
# ``tools.exec.builtinDenyRules`` is on. It is not the product's opinion of what
# an agent may do: an agent that cannot run ``rm -rf build`` or reboot a machine
# it was asked to reboot is an agent doing half the job, and which half is the
# operator's call. ``label`` keys are i18n identifiers for the WebUI permissions
# panel.
BUILTIN_DENY_RULES: list[dict[str, str]] = [
    {"pattern": r"\brm\s+-[rf]{1,2}\b", "label": "recursiveDelete"},
    # GNU long options and other delete channels the short-flag regex missed:
    # `rm --recursive --force`, `find … -delete`, `python -c 'shutil.rmtree'`.
    {"pattern": r"\brm\s+--(?:recursive|force|dir)\b", "label": "recursiveDelete"},
    {"pattern": r"\bfind\b[^|;&]*\s-delete\b", "label": "recursiveDelete"},
    {
        # Semicolons inside `python -c '…; shutil.rmtree(…)'` are not shell
        # separators, so this looks for the API name rather than forbidding
        # `;` between the interpreter and the call.
        "pattern": r"\bshutil\.rmtree\b",
        "label": "recursiveDelete",
    },
    {"pattern": r"\bdel\s+/[fq]\b", "label": "forcedDelete"},
    {"pattern": r"\brmdir\s+/s\b", "label": "recursiveRmdir"},
    {"pattern": r"(?:^|[;&|]\s*)format(?!=)\b", "label": "diskFormat"},
    {"pattern": r"\b(mkfs|diskpart)\b", "label": "diskOperations"},
    {"pattern": r"\bdd\s+if=", "label": "rawDiskCopy"},
    {"pattern": r">\s*/dev/sd", "label": "rawDiskWrite"},
    {"pattern": r"\b(shutdown|reboot|poweroff)\b", "label": "systemPower"},
    {"pattern": r":\(\)\s*\{.*\};\s*:", "label": "forkBomb"},
    # Git commands that destroy work git itself cannot recover. The git *tool*
    # already asks before these; this closes the raw-shell path so the promise
    # "destructive git operations require approval" holds everywhere. Matching
    # happens on the lowercased command, so -D and -d read the same here.
    {"pattern": r"\bgit\b[^|;&]*\breset\s+--hard\b", "label": "gitHardReset"},
    {"pattern": r"\bgit\b[^|;&]*\bclean\b[^|;&]*\s-[a-z]*[fdx]", "label": "gitClean"},
    {
        "pattern": r"\bgit\b[^|;&]*\bpush\b[^|;&]*(?:--force\b|\s-f\b)",
        "label": "gitForcePush",
    },
    {"pattern": r"\bgit\b[^|;&]*\bbranch\b[^|;&]*\s-d\b", "label": "gitBranchDelete"},
    {
        "pattern": r"\bgit\b[^|;&]*\bcheckout\b[^|;&]*(?:--force\b|\s-f\b)",
        "label": "gitForceCheckout",
    },
    # Heavy-infra deletions: reversible only with backups and never with a
    # keystroke. The agent may legitimately run them (that is the job), but a
    # long autonomous mission drifting into `terraform destroy` or a cluster
    # delete is exactly what supervision is for, so each one is approvable.
    # Only the operations that remove something are here: `terraform apply`,
    # `helm rollback` and `helm install` change a deployment without deleting
    # it, and asking about those made the gate feel like it asked about all
    # ordinary work.
    {"pattern": r"\bkubectl\b[^|;&]*\bdelete\b", "label": "kubectlDelete"},
    {
        "pattern": r"\b(?:terraform|tofu)\b[^|;&]*\bdestroy\b",
        "label": "terraformDestroy",
    },
    {
        "pattern": r"\bhelm\b[^|;&]*\b(?:uninstall|delete)\b",
        "label": "helmUninstall",
    },
    {
        "pattern": (
            r"\bdocker\b[^|;&]*\b(?:system\s+prune|volume\s+(?:rm|prune)"
            r"|image\s+prune|container\s+prune)\b"
        ),
        "label": "dockerPrune",
    },
    {
        "pattern": (
            r"\baws\b[^|;&]*\b(?:s3\s+(?:rm|rb)|delete-[a-z-]+"
            r"|terminate-instances)\b"
        ),
        "label": "cloudDelete",
    },
    {"pattern": r"\b(?:gcloud|az)\b[^|;&]*\bdelete\b", "label": "cloudDelete"},
    {
        "pattern": (
            r"\b(?:psql|mysql|mariadb|sqlite3)\b[^|;&]*"
            r"\b(?:drop\s+(?:table|database|schema)|truncate)\b"
        ),
        "label": "sqlDrop",
    },
    # Block writes to navin internal state files (#2989).
    # history.jsonl / .dream_cursor are managed by append_history();
    # direct writes corrupt the cursor format and crash /dream.
    {"pattern": r">>?\s*\S*(?:history\.jsonl|\.dream_cursor)", "label": "internalState"},
    {"pattern": r"\btee\b[^|;&<>]*(?:history\.jsonl|\.dream_cursor)", "label": "internalState"},
    {
        "pattern": r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*(?:history\.jsonl|\.dream_cursor)",
        "label": "internalState",
    },
    {"pattern": r"\bdd\b[^|;&<>]*\bof=\S*(?:history\.jsonl|\.dream_cursor)", "label": "internalState"},
    {"pattern": r"\bsed\s+-i[^|;&<>]*(?:history\.jsonl|\.dream_cursor)", "label": "internalState"},
]

BUILTIN_DENY_PATTERNS: list[str] = [rule["pattern"] for rule in BUILTIN_DENY_RULES]

# Rules that never go to the chat: rewriting navin's own history file.
_HARD_DENY_LABELS: frozenset[str] = frozenset({"internalState"})

# What each approvable rule is, in words the user can judge. The i18n labels are
# for the permissions panel; this is for the moment of decision.
_DENY_RULE_DESCRIPTIONS: dict[str, str] = {
    "recursiveDelete": (
        "recursive or forced delete (rm -r / rm --recursive / find -delete / "
        "shutil.rmtree)"
    ),
    "forcedDelete": "forced delete (del /f or /q)",
    "recursiveRmdir": "recursive directory removal (rmdir /s)",
    "gitHardReset": "git reset --hard: discards uncommitted changes",
    "gitClean": "git clean -f/-d/-x: deletes untracked files",
    "gitForcePush": "git push --force: rewrites published history",
    "gitBranchDelete": "git branch -d/-D: deletes a branch",
    "gitForceCheckout": "git checkout -f/--force: overwrites local changes",
    "kubectlDelete": "kubectl delete: removes live Kubernetes resources",
    "terraformDestroy": "terraform/tofu destroy: tears down real infrastructure",
    "helmUninstall": "helm uninstall/delete: removes a deployed release",
    "dockerPrune": "docker prune / volume rm: deletes containers, images or volumes",
    "cloudDelete": "cloud CLI delete (aws/gcloud/az): destroys cloud resources",
    "sqlDrop": "DROP/TRUNCATE through a SQL client: destroys data",
    "diskFormat": "format a disk",
    "diskOperations": "mkfs / diskpart",
    "rawDiskCopy": "raw disk copy (dd if=)",
    "rawDiskWrite": "write to a raw disk device",
    "systemPower": "shutdown / reboot / poweroff",
    "forkBomb": "fork bomb",
    "workspaceEscape": "a path outside the current project",
    "ssrf": "an internal or private network URL",
    "allowlist": "a command not on the operator allow-list",
    "operatorDeny": "a deny rule from Settings",
}


def _refusal_result(refusal: Any) -> ToolResult:
    """A refused or unanswered approval is a failed call, never a success.

    It used to come back as a plain string: the runner logged the exec as
    "ok", so neither the identical-failure throttle nor the stall detector
    saw the agent retrying a command the user had already said no to.
    """
    if isinstance(refusal, ToolResult) and refusal.is_error:
        return refusal
    text = str(refusal or "The user refused this operation.")
    if not text.startswith("Error"):
        text = f"Error: not run. {text}"
    return ToolResult.error(
        text,
        recovery_hint="Do not run this command again in this request. Continue without it or ask the user.",
    )


# Filters that read their whole input. `pytest | tail -20` used to report
# "Exit code: 0" (tail's) for a failing test run, so the agent, the stall
# detector and validation all saw a success. With pipefail the pipeline fails
# when the command feeding the filter fails. `head` is left out: it closes the
# pipe early and the writer's SIGPIPE would turn a success into a failure.
_FULL_READ_FILTERS = frozenset({
    "tail", "grep", "egrep", "fgrep", "rg", "sed", "awk", "cut", "sort", "uniq", "wc",
    "tee", "cat", "tr", "nl", "column", "jq", "fold", "fmt", "ts",
})


def _wants_pipefail(command: str) -> bool:
    """True when a pipeline ends in a full-read filter that would hide a failure."""
    if "pipefail" in command:
        return False
    # Quoted text is data (grep -E 'passed|failed'), not pipes or separators.
    command = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "Q", command)
    for segment in re.split(r"&&|;|\n|\|\|", command):
        stages = [stage.strip() for stage in re.split(r"(?<!\|)\|(?!\|)", segment)]
        if len(stages) < 2:
            continue
        last = stages[-1].split()
        if not last:
            continue
        program = last[0].rsplit("/", 1)[-1]
        if program in _FULL_READ_FILTERS and not (program in {"grep", "egrep", "fgrep", "rg"} and any(
            flag in last for flag in ("-q", "--quiet", "-m", "--max-count")
        )):
            return True
    return False


_SQUOTED = re.compile(r"'[^']*'")
_ANY_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
# Command separators, outside quotes. `2>&1`, `&>f` and `>&2` are redirections.
_SEPARATORS = re.compile(r"\|\||&&|;|\n|\|&?|(?<![<>&0-9])&(?![>&])")
_ASSIGNMENTS = re.compile(r"^(?:[a-z_][a-z0-9_]*=(?:'[^']*'|\"[^\"]*\"|\S*)\s+)+")


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def _substitutions(text: str) -> list[str]:
    """Bodies of `$( )`, `<( )`, `>( )` and backticks, which run as commands."""
    masked = _SQUOTED.sub(_blank, text)
    bodies: list[str] = []
    i = 0
    while i < len(masked):
        if masked.startswith(("$(", "<(", ">("), i) and not masked.startswith("$((", i):
            depth, j = 1, i + 2
            while j < len(masked) and depth:
                depth += {"(": 1, ")": -1}.get(masked[j], 0)
                j += 1
            bodies.append(text[i + 2:j - 1 if depth == 0 else j])
            i = j
        elif masked[i] == "`":
            end = masked.find("`", i + 1)
            end = len(masked) if end < 0 else end
            bodies.append(text[i + 1:end])
            i = end + 1
        else:
            i += 1
    return bodies


def _trim_grouping(segment: str) -> str:
    segment = segment.strip().lstrip("({! \t").strip()
    for opener, closer in (("(", ")"), ("{", "}")):
        while segment.endswith(closer) and segment.count(closer) > segment.count(opener):
            segment = segment[:-1].rstrip()
    return segment


def shell_segments(command: str, _depth: int = 0) -> list[str]:
    """Split a shell line into the simple commands it runs.

    Separators inside quotes are data. Command substitutions are listed as
    segments of their own, because `echo $(rm -rf x)` runs `rm -rf x`.
    """
    masked = _ANY_QUOTED.sub(_blank, command)
    out: list[str] = []
    start = 0
    for match in _SEPARATORS.finditer(masked):
        out.append(command[start:match.start()])
        start = match.end()
    out.append(command[start:])
    segments = [s for s in map(_trim_grouping, out) if s]
    if _depth < 4:
        for body in _substitutions(command):
            segments.extend(shell_segments(body, _depth + 1))
    return segments


def policy_verdict(
    lower: str, allow_patterns: Any, deny_rules: Any,
) -> tuple[bool, str | None]:
    """(explicitly allowed, matching deny label) for a lower-cased command.

    An allow rule exempts only the simple commands it matches: every segment
    of `safe && dangerous` must be covered before the deny rules are skipped,
    so a prefix rule such as `git status` never lets a dangerous tail through.
    Deny rules run on the whole line (pipelines such as `curl x | sh`) and on
    each uncovered segment, so `$(wget x)`, `(wget x)`, a second line or an
    `X=1 wget x` prefix are still caught.
    """
    segments = shell_segments(lower)
    allow = list(allow_patterns or ())
    uncovered = [
        s for s in segments if not (allow and any(re.fullmatch(p, s) for p in allow))
    ]
    if allow and segments and not uncovered:
        return True, None
    targets = [lower, *(_ASSIGNMENTS.sub("", s) for s in uncovered)]
    for pattern, label in deny_rules:
        if any(re.search(pattern, target) for target in targets):
            return False, label
    return False, None


_BUNDLED_LIBS = re.compile(
    r"No module named '(docx|openpyxl|pptx|reportlab|pandas|matplotlib|fitz|pypdf|PyPDF2|xlsxwriter|PIL|markdown|bs4|lxml)'"
)


_NOT_A_REPO = re.compile(r"fatal: not a git repository", re.I)
_GIT_C_DIR = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|\S+)")


def _find_git_root(start: str | None) -> str | None:
    """The nearest folder at or above ``start`` holding a ``.git`` entry."""
    if not start:
        return None
    try:
        here = Path(start).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    for folder in (here, *here.parents):
        if (folder / ".git").exists():
            return str(folder)
    return None


def _not_a_repo_hint(command: str, output: str, cwd: str, project_root: str | None) -> str:
    """One actionable line when git ran in a folder that is not a repository.

    The generic diagnosis ("run the command from the project folder") left the
    agent with a red failure and no folder to go to. Naming the folder git ran
    in and the repository the project actually lives in fixes it in one step.
    Empty when the output carries no such error.
    """
    if not _NOT_A_REPO.search(output):
        return ""
    match = _GIT_C_DIR.search(command)
    target = match.group(1).strip("\"'") if match else cwd
    root = _find_git_root(project_root) or _find_git_root(cwd)
    if root and Path(target).expanduser().resolve(strict=False) != Path(root):
        return f"\nHint: {target} is not a git repository - use the project root {root}"
    return f"\nHint: {target} is not a git repository - run git from the project folder, or git init a new project"


_MACOS_PRIVACY_RE = re.compile(
    r"/Users/[^/\s]+/(?:Desktop|Documents|Downloads|Library/Mobile Documents|Library/CloudStorage)\b"
    r"|/Volumes/[^/\s:'\"]+",
    re.IGNORECASE,
)

# Folders macOS (TCC) already refused in this process. Granting access needs a
# Navin restart anyway, so the memory lives as long as the process.
_MACOS_DENIED: set[str] = set()


def _macos_denied_folder(output: str) -> str:
    if sys.platform != "darwin" or "operation not permitted" not in output.lower():
        return ""
    match = _MACOS_PRIVACY_RE.search(output)
    return match.group(0) if match else ""


def _macos_privacy_hint(output: str) -> str:
    """macOS TCC refuses Desktop/Documents/Downloads/iCloud until the user allows it.

    Logs one clear line, remembers the folder so the next command on it is
    refused instantly (no retry loop), and tells the agent to ask once and
    move on.
    """
    folder = _macos_denied_folder(output)
    if not folder:
        return ""
    if folder not in _MACOS_DENIED:
        _MACOS_DENIED.add(folder)
        logger.warning(
            "macOS blocked access to {} (privacy protection). Allow Navin in System Settings > "
            "Privacy & Security > Files and Folders (or Full Disk Access), then restart Navin.",
            folder,
        )
    return (
        f"\nLikely cause:\n- macOS privacy protection (TCC) blocked access to {folder}. "
        "Do not retry: commands on this folder are now refused until Navin restarts. "
        "Tell the user once, in plain words: allow Navin in System Settings > Privacy & Security > "
        "Files and Folders (or Full Disk Access), then restart Navin. Then continue with the "
        "rest of the task that does not need this folder."
    )


def macos_denied_refusal(command: str) -> str | None:
    """Refuse at once a command on a folder macOS already denied."""
    for folder in _MACOS_DENIED:
        if folder in command:
            return (
                f"Error: macOS already refused access to {folder} (privacy protection); not retrying. "
                "Waiting for the user to allow Navin in System Settings > Privacy & Security, "
                "then restart Navin. Skip this folder and continue the rest of the task."
            )
    return None


def _failure_hint(output: str) -> str:
    """The likely cause of a failed command, whatever the language (project diagnoses)."""
    privacy = _macos_privacy_hint(output)
    if privacy:
        return privacy
    bundled = _BUNDLED_LIBS.search(output)
    if bundled:
        return (
            f"\nLikely cause:\n- `{bundled.group(1)}` is bundled with Navin but not installed in this "
            "Python: for a document/data script, run it with navin-python instead of python. "
            "For the project's own code, add it to the project's requirements and install it there."
        )
    try:
        from navin.agent.tools.project import diagnose

        hints = diagnose(output)
    except Exception:
        return ""
    if not hints:
        return ""
    return "\nLikely cause:\n" + "\n".join(f"- {hint}" for hint in hints)


def _chat_rule(label: str, *, fallback: str = "operatorDeny") -> str:
    """Label the chat card should use, or empty for a hard denial."""
    if label in _HARD_DENY_LABELS:
        return ""
    return label or fallback


def _build_deny_rules(
    user_patterns: list[str] | None, *, builtins: bool = False
) -> list[tuple[str, str]]:
    """Pair every deny pattern with its origin, which decides what a hit means.

    A pattern the operator wrote is a decision already taken, so a match there is
    final. A built-in one is navin guessing that the command is dangerous, and a
    few of those guesses are worth putting to the user - but only when the
    operator asked for that set in the first place.
    """
    rules: list[tuple[str, str]] = [(pattern, "") for pattern in user_patterns or []]
    if builtins:
        rules.extend((rule["pattern"], rule["label"]) for rule in BUILTIN_DENY_RULES)
    return rules


@dataclass(frozen=True, slots=True)
class _GuardVerdict:
    """A refusal, and whether the user is allowed to overrule it."""

    message: str
    approvable_rule: str = ""


@dataclass(frozen=True, slots=True)
class StdinCommandGuard:
    """Apply the exec deny/approval policy to bytes written to a live session.

    exec guards the command it starts, but ``write_stdin`` sent raw text into an
    already-running process with no check at all. A background ``bash`` or
    ``python -i`` plus a stdin payload was therefore a clean bypass of the whole
    "destructive commands need approval" promise. This guard re-applies the same
    deny rules (and the same ask-every-command policy) to each line of stdin,
    because a line handed to an interactive shell is a command.
    """

    deny_rules: tuple[tuple[str, str], ...] = ()
    allow_patterns: tuple[str, ...] = ()
    ask_every_command: bool = False

    @classmethod
    def from_config(cls, exec_cfg: Any, approvals_cfg: Any) -> StdinCommandGuard:
        deny_patterns = list(getattr(exec_cfg, "deny_patterns", []) or [])
        builtin = bool(getattr(exec_cfg, "builtin_deny_rules", False))
        rules = _build_deny_rules(deny_patterns, builtins=builtin)
        ask_every = bool(
            getattr(approvals_cfg, "enabled", False)
            and getattr(approvals_cfg, "exec_ask", "destructive") == "always"
        )
        return cls(
            deny_rules=tuple(rules),
            allow_patterns=tuple(getattr(exec_cfg, "allow_patterns", []) or []),
            ask_every_command=ask_every,
        )

    def _line_verdict(self, line: str) -> _GuardVerdict | None:
        lower = line.strip().lower()
        if not lower:
            return None
        allowed, label = policy_verdict(lower, self.allow_patterns, self.deny_rules)
        if allowed or label is None:
            return None
        return _GuardVerdict(
            ToolResult.error("Error: stdin blocked by deny pattern filter"),
            approvable_rule=_chat_rule(label),
        )

    async def check(self, chars: str | None, *, session_command: str | None) -> str | None:
        """Return a refusal message when the stdin payload must not be sent.

        None means the write may proceed. Only meaningful lines are inspected:
        an empty poll (``chars=''``) or pure whitespace never triggers a guard.
        """
        if not chars or not chars.strip():
            return None
        if not self.deny_rules and not self.ask_every_command:
            return None

        from navin.agent.approval import ApprovalRequest, request_approval

        where = session_command or "an exec session"
        for raw_line in chars.splitlines():
            verdict = self._line_verdict(raw_line)
            if verdict is None:
                continue
            if not verdict.approvable_rule:
                return verdict.message
            decision = await request_approval(ApprovalRequest(
                tool="write_stdin",
                action="Send a destructive command to a running session",
                reason=(
                    "The stdin line matches the built-in "
                    f"{_DENY_RULE_DESCRIPTIONS.get(verdict.approvable_rule, verdict.approvable_rule)} rule."
                ),
                detail=f"> {raw_line.strip()}\ninto {where}",
                consequence=(
                    "It runs inside the live process with the agent's permissions; "
                    "files it removes are not recorded as a pending change."
                ),
                scope=f"write_stdin:{verdict.approvable_rule}",
                allow_when_unattended=False,
            ))
            if not decision.allowed:
                return ToolResult.error(f"{verdict.message}\n{decision.reason}")
            logger.warning(
                "write_stdin: user approved a payload matching the {} rule",
                verdict.approvable_rule,
            )

        if self.ask_every_command:
            decision = await request_approval(ApprovalRequest(
                tool="write_stdin",
                action="Send input to a running session",
                reason="Settings require confirmation before every command.",
                detail=f"into {where}",
                consequence=(
                    "The input is written to the live process with the agent's "
                    "permissions."
                ),
                scope="write_stdin:command",
                allow_when_unattended=True,
            ))
            if not decision.allowed:
                return ToolResult.error(
                    decision.reason or "The user refused this operation."
                )
        return None


def _workspace_policy_block(command: str) -> str | None:
    """Refusal when an isolated subagent's workspace policy forbids ``command``."""
    ctx = current_request_context()
    metadata = getattr(ctx, "metadata", None)
    task_id = metadata.get("subagent_task_id") if isinstance(metadata, dict) else None
    if not task_id:
        return None
    from navin.agent import workspace_policy

    policy = workspace_policy.get_policy(str(task_id))
    if policy is None or policy.command_allowed(command):
        return None
    return (
        "Error: command refused by this subagent's workspace policy; allowed "
        "prefixes: " + ", ".join(policy.allowed_commands)
    )


class ExecToolConfig(Base):
    """Shell exec tool configuration."""
    enable: bool = True
    # Foreground window (s); 0 = wait without limit. Not capped by the per-call max.
    timeout: int = Field(default=60, ge=0)
    # What happens to a command still running when its window closes. On, it
    # keeps running in a background session and the agent gets the turn back
    # with a session_id to poll: a test suite that needs eleven minutes is not
    # killed at ten, and a dev server started in the foreground by mistake does
    # not hold the turn hostage. Off restores the historical kill.
    keep_running_in_background: bool = True
    path_prepend: str = ""
    path_append: str = ""
    sandbox: str = ""
    # "deny" cuts the network of sandboxed commands (no route out at all);
    # "open" keeps it, the historical default. Only applies with a sandbox.
    sandbox_network: str = Field(default="open", pattern="^(open|deny)$")
    allowed_env_keys: list[str] = Field(default_factory=list)
    allow_patterns: list[str] = Field(default_factory=list)
    deny_patterns: list[str] = Field(default_factory=list)
    # Off by default: navin does not decide which commands an operator's agent
    # may run. Turning it on adds BUILTIN_DENY_RULES on top of deny_patterns,
    # for an operator who wants the usual set without writing the regexes.
    builtin_deny_rules: bool = False
    # Shells the model may ask for. Empty means any shell on the host, which is
    # what lets a project pick fish, nu, or wsl.exe. A populated list is the
    # operator narrowing it down.
    allowed_shells: list[str] = Field(default_factory=list)
    # Ceiling on a timeout the model asks for. 0 means the model's own number
    # stands: a build that legitimately takes an hour should not be cut off
    # because a constant said ten minutes.
    max_timeout: int = Field(default=0, ge=0)


@dataclass(slots=True)
class _PreparedCommand:
    command: str
    cwd: str
    env: dict[str, str]
    timeout: int | None
    shell_program: str | None
    login: bool
    # Sandbox backend the command actually runs in ("" when unconfined) and
    # whether the user lifted a configured sandbox for this one command.
    sandbox: str = ""
    sandbox_lifted: bool = False


@dataclass(slots=True)
class _RunTiming:
    """Usual duration of one command, so a silent run still shows how far it is."""

    key: str
    expected_s: float | None
    started: float

    def estimate(self) -> tuple[float | None, float | None]:
        """Estimated percent and seconds left, both None without a history."""
        from navin.quality.test_timing import estimated_percent

        elapsed = time.monotonic() - self.started
        percent = estimated_percent(elapsed, self.expected_s)
        if percent is None or self.expected_s is None:
            return None, None
        return round(percent, 1), max(0.0, round(self.expected_s - elapsed))


class _AgentExecStream:
    """Throttled live feed of one exec command to the editor terminal panel.

    Cursor-style agent terminals: a ``start`` frame opens a read-only tab in
    the WebUI, ``output`` frames stream deltas, an ``exit`` frame closes the
    story with the return code. Frames travel on the outbound bus so they
    reach every window subscribed to the chat; when no editor is connected
    (CLI, other channels) :meth:`open` returns ``None`` and exec behaves
    exactly as before.
    """

    _FLUSH_INTERVAL_S = 0.08
    # Ceiling on output held between two flushes; a runaway process must not
    # outpace the websocket. Oldest chunks are dropped and counted.
    _MAX_PENDING_CHARS = 100_000

    def __init__(
        self,
        bus: Any,
        chat_id: str,
        *,
        command: str,
        cwd: str,
        background: bool,
        sandbox: str = "",
        sandbox_lifted: bool = False,
    ) -> None:
        self._bus = bus
        self._chat_id = chat_id
        self._background = background
        self._sandbox = sandbox or None
        self._sandbox_lifted = sandbox_lifted
        self.exec_id = uuid.uuid4().hex[:12]
        self._pending: list[str] = []
        self._pending_chars = 0
        self._dropped_chars = 0
        self._flusher: asyncio.Task | None = None
        self._done = False
        self._emit(phase="start", command=command, cwd=cwd)

    @classmethod
    def open(
        cls,
        bus: Any,
        *,
        command: str,
        cwd: str,
        background: bool,
        sandbox: str = "",
        sandbox_lifted: bool = False,
    ) -> "_AgentExecStream | None":
        ctx = current_request_context()
        if bus is None or ctx is None or ctx.channel != "websocket" or not ctx.chat_id:
            return None
        return cls(
            bus,
            ctx.chat_id,
            command=command,
            cwd=cwd,
            background=background,
            sandbox=sandbox,
            sandbox_lifted=sandbox_lifted,
        )

    def feed(self, text: str) -> None:
        if self._done or not text:
            return
        self._pending.append(text)
        self._pending_chars += len(text)
        while self._pending_chars > self._MAX_PENDING_CHARS and len(self._pending) > 1:
            dropped = self._pending.pop(0)
            self._pending_chars -= len(dropped)
            self._dropped_chars += len(dropped)
        if self._flusher is None or self._flusher.done():
            try:
                self._flusher = asyncio.get_running_loop().create_task(self._flush_loop())
            except RuntimeError:
                self._flusher = None

    async def _flush_loop(self) -> None:
        while self._pending:
            await asyncio.sleep(self._FLUSH_INTERVAL_S)
            self._flush_now()

    def _flush_now(self) -> None:
        if not self._pending:
            return
        data = "".join(self._pending)
        if self._dropped_chars:
            data = f"\n... ({self._dropped_chars:,} chars dropped) ...\n{data}"
            self._dropped_chars = 0
        self._pending.clear()
        self._pending_chars = 0
        self._emit(phase="output", data=data)

    def finish(self, exit_code: int | None) -> None:
        if self._done:
            return
        self._done = True
        if self._flusher is not None:
            self._flusher.cancel()
            self._flusher = None
        self._flush_now()
        self._emit(phase="exit", exit_code=exit_code)

    def _emit(
        self,
        *,
        phase: str,
        command: str | None = None,
        cwd: str | None = None,
        data: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        try:
            self._bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=self._chat_id,
                    event=AgentExecEvent(
                        exec_id=self.exec_id,
                        phase=phase,
                        command=command,
                        cwd=cwd,
                        background=self._background,
                        data=data,
                        exit_code=exit_code,
                        sandbox=self._sandbox if phase == "start" else None,
                        sandbox_lifted=self._sandbox_lifted if phase == "start" else False,
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - the terminal feed must never fail exec
            logger.debug("agent exec stream: emit failed: {}", exc)


# Background commands that keep crashing, per (session, command): each exit
# wakes the agent, which restarted the server, which crashed again, forever.
_CRASH_WINDOW_S = 1800.0
_CRASH_REPORT_AT = 3
_CRASH_SILENT_AFTER = 3
_crash_history: dict[tuple[str, str], list[float]] = {}


def _record_background_exit(session_key: str, command: str, returncode: Any) -> int:
    """How many times this command crashed recently here (0 after a success)."""
    key = (session_key, " ".join(command.split()))
    if returncode == 0:
        _crash_history.pop(key, None)
        return 0
    now = time.monotonic()
    recent = [t for t in _crash_history.get(key, []) if now - t < _CRASH_WINDOW_S]
    recent.append(now)
    _crash_history[key] = recent[-10:]
    return len(recent)


class _ExecCompletionAnnouncer:
    """Tell the agent when a background command ends, so it never has to poll.

    A build or test run handed to the background used to cost one model call
    per ``write_stdin`` poll until it finished. Now the exit is published on
    the bus as a system message for the owning session: mid-turn it lands
    between two tool batches, between turns it wakes the agent up, the same
    way a subagent result does. A poll that already reported the exit keeps
    the announcement quiet.
    """

    _TAIL_CHARS = 1500
    # A poll blocked on the process returns right after the exit; give it a
    # moment to report before deciding the agent has not heard.
    _GRACE_S = 2.0

    def __init__(self, bus: Any, ctx: RequestContext, *, command: str) -> None:
        self._bus = bus
        self._channel = ctx.channel
        self._chat_id = ctx.chat_id
        self._session_key = ctx.session_key or f"{ctx.channel}:{ctx.chat_id}"
        self._command = command
        self._started = time.monotonic()

    @classmethod
    def open(cls, bus: Any, *, command: str) -> "_ExecCompletionAnnouncer | None":
        ctx = current_request_context()
        if bus is None or ctx is None or not ctx.chat_id or ctx.channel == "system":
            return None
        return cls(bus, ctx, command=command)

    def on_finished(self, session: Any) -> None:
        """``on_finished`` hook of the exec session: schedule the announcement."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._announce(session))

    async def _announce(self, session: Any) -> None:
        try:
            await asyncio.sleep(self._GRACE_S)
            if getattr(session, "exit_reported", False):
                return
            returncode = getattr(getattr(session, "process", None), "returncode", None)
            elapsed = time.monotonic() - self._started
            tail = str(getattr(session, "tail", "") or "").strip()
            if len(tail) > self._TAIL_CHARS:
                tail = "..." + tail[-self._TAIL_CHARS:]
            session_id = str(getattr(session, "session_id", "") or "")
            outcome = "succeeded (exit code 0)" if returncode == 0 else f"exited with code {returncode}"
            lines = [
                f"[Background command finished] session {session_id} {outcome} after {elapsed:.0f}s.",
                f"$ {self._command.strip()}",
            ]
            if tail:
                lines.append("--- last output ---")
                lines.append(tail)
            crashes = _record_background_exit(self._session_key, self._command, returncode)
            if crashes > _CRASH_SILENT_AFTER:
                logger.warning(
                    "exec: `{}` crashed {} times in {}; not waking the agent again",
                    self._command.strip()[:80], crashes, self._session_key,
                )
                return
            if crashes >= _CRASH_REPORT_AT:
                lines.append(
                    f"This command has now failed {crashes} times in a row. Do NOT start "
                    "it again. Read the output above, tell the user the exact error and "
                    "its likely cause, and what you need (a fix you could not make, a "
                    "missing tool, a credential, a decision) to go on."
                )
            else:
                lines.append(
                    "Act on this if you were waiting for it (build, tests, install). "
                    "If it was a server or a watcher, it has stopped. This session is "
                    "closed; do not poll it. Before starting it again, fix what the "
                    "output above reports."
                )
            from navin.bus.events import InboundMessage

            await self._bus.publish_inbound(
                InboundMessage(
                    channel="system",
                    sender_id="exec",
                    chat_id=f"{self._channel}:{self._chat_id}",
                    content="\n".join(lines),
                    session_key_override=self._session_key,
                    metadata={
                        "injected_event": "exec_finished",
                        "exec_session_id": session_id,
                        "exit_code": returncode,
                    },
                )
            )
            logger.info(
                "exec: announced completion of background session {} (exit {}) to {}",
                session_id, returncode, self._session_key,
            )
        except Exception as exc:  # noqa: BLE001 - a lost announcement must never fail exec
            logger.debug("exec completion announce failed: {}", exc)


@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute", min_length=1),
        cmd=StringSchema("Compatibility alias for command"),
        working_dir=StringSchema("Optional working directory for the command"),
        workdir=StringSchema("Compatibility alias for working_dir"),
        timeout=IntegerSchema(
            60,
            description=(
                "Seconds to wait for the command in the foreground (default 60). "
                "A command still running after that is not killed: it keeps "
                "running in a background session and this call returns the "
                "output so far plus a session_id to poll with write_stdin. Set "
                "it to what the command usually needs so short commands return "
                "in one call; there is no hard cap here (the operator's "
                "tools.exec.maxTimeout, when set, is the only ceiling)."
            ),
            minimum=1,
        ),
        shell=StringSchema(
            (
                "Override the shell only when needed. Omit to use PowerShell "
                "by default (pwsh when available, else powershell). Pass 'cmd' "
                "for cmd.exe syntax, 'wsl' to run the command in the default "
                "WSL distribution, or 'bash' for git-bash when installed."
                " For a project opened through a WSL UNC path, commands run "
                "inside that project's distribution with Linux syntax and "
                "bash by default; a shell override names a Linux shell there."
                if _IS_WINDOWS
                else "Override the Unix shell only when needed. Omit to use "
                "bash by default. Pass 'sh' for POSIX sh or 'zsh' for "
                "zsh-specific syntax. If this host is a WSL distribution you "
                "are already inside Linux: run commands directly, never "
                "through 'wsl'/'wsl.exe'. Sessions are pipes without a TTY - "
                "interactive shells like 'bash -i' never show a prompt; use "
                "the open_terminal tool to give the user a terminal."
            ),
            nullable=True,
        ),
        login=BooleanSchema(
            description="Whether to run bash/zsh with login shell semantics (default false).",
            default=False,
            nullable=True,
        ),
        yield_time_ms=IntegerSchema(
            description=(
                "Optional milliseconds to wait before returning output. "
                "When set, a still-running command returns a session_id that "
                "can be polled or written to with write_stdin. Omit this field "
                "to keep one-shot exec behavior."
            ),
            minimum=0,
            maximum=MAX_YIELD_MS,
            nullable=True,
        ),
        background=BooleanSchema(
            description=(
                "Run as a background session: return promptly with a session_id "
                "that write_stdin can poll, feed, or terminate. Shorthand for "
                "yield_time_ms=1000; use it for dev servers, watchers, and "
                "anything meant to outlive this call."
            ),
            default=False,
            nullable=True,
        ),
        max_output_chars=IntegerSchema(
            description=(
                "Maximum output characters to return when yield_time_ms is used "
                f"(default 10000, max {MAX_OUTPUT_CHARS}). Oversized values are clamped."
            ),
            minimum=1000,
            maximum=MAX_OUTPUT_CHARS,
            nullable=True,
        ),
        # max_output_tokens (a compatibility alias of max_output_chars) is still
        # accepted by execute() but no longer advertised: describing a legacy
        # alias cost ~45 tokens on every model call for nothing the model needs.
        unsandboxed=BooleanSchema(
            description=(
                "Run this command outside the OS sandbox (unconfined writes on "
                "the machine, as the user). The user is asked to approve it in "
                "the chat. Only after a command failed because of the sandbox "
                "(permission denied outside the project, global install, system "
                "service); never as a first attempt."
            ),
            default=False,
            nullable=True,
        ),
        required=["command"],
    )
)
class ExecTool(Tool):
    """Tool to execute shell commands."""
    _scopes = {"core", "subagent"}

    config_key = "exec"

    # Opt out of the runner-level tool wall clock: exec enforces its own
    # configured timeout and owns child-process cleanup; an outer cancel would
    # orphan that. An operator who set timeout=0 chose unlimited on purpose.
    wall_timeout_s = 0

    @classmethod
    def config_cls(cls):
        return ExecToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.exec.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        cfg = ctx.config.exec
        path_prepend = cfg.path_prepend
        return cls(
            runtime_bin=_runtime_bin_dir(),
            working_dir=ctx.workspace,
            timeout=cfg.timeout,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            webui_allow_local_service_access=ctx.config.webui_allow_local_service_access,
            sandbox=cfg.sandbox,
            sandbox_strict=getattr(ctx.config, "security_profile", None) == "strict",
            sandbox_deny_net=getattr(cfg, "sandbox_network", "open") == "deny",
            path_prepend=path_prepend,
            path_append=cfg.path_append,
            allowed_env_keys=cfg.allowed_env_keys,
            forge_tokens=forge_tokens_from_tools_config(ctx.config),
            allow_patterns=cfg.allow_patterns,
            deny_patterns=cfg.deny_patterns,
            builtin_deny_rules=cfg.builtin_deny_rules,
            ask_every_command=bool(
                ctx.config.approvals.enabled
                and getattr(ctx.config.approvals, "exec_ask", "destructive") == "always"
            ),
            allowed_shells=cfg.allowed_shells,
            max_timeout=cfg.max_timeout,
            keep_running_in_background=cfg.keep_running_in_background,
            bus=getattr(ctx, "bus", None),
        )

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        webui_allow_local_service_access: bool = True,
        allow_local_preview_access: bool | None = None,
        sandbox: str = "",
        sandbox_strict: bool = False,
        sandbox_deny_net: bool = False,
        path_prepend: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
        forge_tokens: dict[str, str] | None = None,
        session_manager: Any | None = None,
        runtime_bin: str = "",
        builtin_deny_rules: bool = False,
        ask_every_command: bool = False,
        allowed_shells: list[str] | None = None,
        max_timeout: int = 0,
        keep_running_in_background: bool = True,
        bus: Any = None,
    ):
        self.timeout = timeout
        self.keep_running_in_background = keep_running_in_background
        self.working_dir = working_dir
        self.sandbox = sandbox
        # Fail-closed confinement (strict security profile): the native
        # sandbox refuses to run when the OS cannot enforce the policy,
        # instead of warning and running unconfined.
        self.sandbox_strict = sandbox_strict
        self.sandbox_deny_net = sandbox_deny_net
        self.builtin_deny_rules = builtin_deny_rules
        self.ask_every_command = ask_every_command
        self.allowed_shells = [s.lower() for s in (allowed_shells or [])]
        self.max_timeout = max_timeout
        self.deny_patterns = list(deny_patterns or [])
        if builtin_deny_rules:
            self.deny_patterns += BUILTIN_DENY_PATTERNS
        self._deny_rules = _build_deny_rules(deny_patterns, builtins=builtin_deny_rules)
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        if allow_local_preview_access is not None:
            webui_allow_local_service_access = allow_local_preview_access
        self.webui_allow_local_service_access = webui_allow_local_service_access
        self.path_prepend = path_prepend
        self.path_append = path_append
        # Navin's own interpreter directory (venv bin, or the packaged shims).
        # Kept apart from the operator's PATH settings: it goes first for skill
        # scripts, but never in front of a Python project's own interpreter.
        self.runtime_bin = runtime_bin
        self.allowed_env_keys = allowed_env_keys or []
        self.forge_tokens = dict(forge_tokens or {})
        self._session_manager = session_manager or DEFAULT_EXEC_SESSION_MANAGER
        self._bus = bus

    def apply_policy(
        self,
        *,
        allow_patterns: list[str],
        deny_patterns: list[str],
        restrict_to_workspace: bool | None = None,
        builtin_deny_rules: bool | None = None,
        ask_every_command: bool | None = None,
    ) -> None:
        """Hot-apply an updated exec permission policy (WebUI permissions panel)."""
        if builtin_deny_rules is not None:
            self.builtin_deny_rules = builtin_deny_rules
        if ask_every_command is not None:
            self.ask_every_command = ask_every_command
        self.allow_patterns = list(allow_patterns)
        self.deny_patterns = list(deny_patterns)
        if self.builtin_deny_rules:
            self.deny_patterns += BUILTIN_DENY_PATTERNS
        self._deny_rules = _build_deny_rules(deny_patterns, builtins=self.builtin_deny_rules)
        if restrict_to_workspace is not None:
            self.restrict_to_workspace = restrict_to_workspace

    @property
    def name(self) -> str:
        return "exec"

    _MAX_OUTPUT = 10_000

    # Kernel device files safe as stdio redirect targets (#3599).
    _BENIGN_DEVICE_PATHS: frozenset[str] = frozenset({
        "/dev/null",
        "/dev/zero",
        "/dev/full",
        "/dev/random",
        "/dev/urandom",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/tty",
    })

    @property
    def description(self) -> str:
        wsl_workspace = _IS_WINDOWS and wsl.parse_unc(str(self.working_dir or "")) is not None
        platform_note = (
            "This project lives inside WSL: commands run in Linux bash, so use "
            "bash syntax (ls, grep, test -f, ~/.navin). PowerShell and cmd are "
            "not available here; Windows paths are under /mnt/c. "
            + _desktop_logs_note()
            if wsl_workspace
            else "On Windows, use PowerShell syntax by default; pass shell='cmd' "
            "only for cmd-specific commands. "
            if _IS_WINDOWS
            else "On Unix, commands run through bash by default; pass shell='sh' "
            "or shell='zsh' when needed. "
        )
        return (
            "Execute a shell command and return its output. Use it for "
            "builds and process execution; prefer the dedicated tools "
            "(read_file/grep/find_files, apply_patch/edit_file, manage_files, "
            "git) over cat/sed/rm/mv/cp or shelling out to git. Use -y/--yes "
            "to avoid interactive prompts. "
            f"{platform_note}"
            f"{sandbox_tool_description(self.sandbox, deny_net=self.sandbox_deny_net)}"
            "For long-running or interactive commands pass background=true; "
            "a still-running command returns a session_id usable with "
            "write_stdin. A foreground command that outlives its timeout is "
            "moved to a background session too (poll that session_id, do not "
            "re-run the command). Output truncated at 10 000 chars; timeout 60s."
        )

    @property
    def exclusive(self) -> bool:
        return True

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        # Keep the legacy alias working while advertising one required field
        # to the model. Validate before dispatch so exec({}) gets a bounded
        # parameter-recovery attempt instead of an execution-error loop.
        if not params.get("command") and params.get("cmd"):
            params = {**params, "command": params["cmd"]}
        return super().cast_params(params)

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        command = params.get("command")
        if isinstance(command, str) and command and not command.strip():
            errors.append("command must not be blank")
        return errors

    async def execute(
        self, command: str | None = None, cmd: str | None = None,
        working_dir: str | None = None, workdir: str | None = None,
        timeout: int | None = None, shell: str | None = None,
        login: bool | None = None, yield_time_ms: int | None = None,
        background: bool | None = None,
        max_output_chars: int | None = None,
        max_output_tokens: int | None = None,
        unsandboxed: bool | None = None,
        **kwargs: Any,
    ) -> str:
        command = command or cmd
        working_dir = working_dir or workdir
        if not command:
            return ToolResult.error("Error: Missing command. Provide command or cmd.")
        if max_output_chars is None:
            max_output_chars = max_output_tokens
        if yield_time_ms is None and background:
            yield_time_ms = DEFAULT_YIELD_MS

        prepared = await self._prepare_command(
            command, working_dir, timeout, shell, login, unsandboxed=bool(unsandboxed)
        )
        if isinstance(prepared, str):
            return prepared
        # Settings > Execution = AWS: same checks and approvals, run in the cloud sandbox.
        from navin.agent.tools.cloud_sandbox import cloud_target_active, run_cloud_command

        if await asyncio.to_thread(cloud_target_active):
            return await run_cloud_command(command)
        # Before the spawn: pytest stops at the first unknown path and runs
        # nothing, so a guessed test file costs the whole run for a raw error.
        missing = await asyncio.to_thread(_missing_pytest_paths, command, prepared.cwd)
        if missing:
            return ToolResult.error(_missing_pytest_message(missing))
        await self._announce_sandbox(prepared)

        waited = await self._wait_for_package_manager(command, prepared.cwd)
        if waited.startswith("Error"):
            return ToolResult.error(waited)

        if yield_time_ms is not None:
            result = await self._execute_session(
                prepared, yield_time_ms, max_output_chars, display_command=command
            )
            return f"{waited}{result}" if waited and isinstance(result, str) else result

        process: asyncio.subprocess.Process | None = None
        handed_over = False
        agent_stream = _AgentExecStream.open(
            self._bus,
            command=command,
            cwd=prepared.cwd,
            background=False,
            sandbox=prepared.sandbox,
            sandbox_lifted=prepared.sandbox_lifted,
        )
        timing = await self._announce_expected_duration(command, prepared.cwd)
        try:
            process = await self._spawn(
                prepared.command,
                prepared.cwd,
                prepared.env,
                prepared.shell_program,
                prepared.login,
            )

            # The pump writes into these so the output read before the window
            # closed survives the cancellation and can be handed back.
            stdout_buf = bytearray()
            stderr_buf = bytearray()
            try:
                stdout, stderr = await asyncio.wait_for(
                    self._communicate_streaming(
                        process,
                        agent_stream,
                        stdout_buf=stdout_buf,
                        stderr_buf=stderr_buf,
                        timing=timing,
                    ),
                    timeout=prepared.timeout,
                )
            except asyncio.TimeoutError:
                if self.keep_running_in_background and process.returncode is None:
                    handed = await self._keep_running(
                        process,
                        prepared,
                        display_command=command,
                        agent_stream=agent_stream,
                        stdout_buf=stdout_buf,
                        stderr_buf=stderr_buf,
                        max_output_chars=max_output_chars,
                    )
                    if handed is not None:
                        handed_over = True
                        return handed
                await self._kill_process(process)
                if agent_stream is not None:
                    agent_stream.feed(
                        f"\n[timed out after {prepared.timeout} seconds]\n"
                    )
                return ToolResult.error(f"Error: Command timed out after {prepared.timeout} seconds")
            except asyncio.CancelledError:
                await self._kill_process(process)
                raise

            # Safety-net reap: asyncio *should* have reaped the child via
            # communicate(), but in containers the child-watcher sometimes
            # misses it, leaving a zombie.
            _reap_pid(process.pid)
            await emit_tool_meta(percent=100, label="Command finished", indeterminate=False)
            await self._remember_duration(timing)

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"
            result = self._compact_exec_output(command, result)
            result += _python_env_hint(result)
            result += _scaffold_hint(command, result)
            result += _shell_search_hint(command)
            if process.returncode not in (0, None):
                repo_hint = _not_a_repo_hint(
                    command, result, prepared.cwd, self._project_root()
                )
                result += repo_hint or _failure_hint(result)
            if waited:
                result = waited + result
            result += sandbox_result_note(
                prepared.sandbox,
                exit_code=process.returncode,
                lifted=prepared.sandbox_lifted,
                output=result,
            )

            max_len = clamp_session_int(max_output_chars, self._MAX_OUTPUT, 1000, MAX_OUTPUT_CHARS)
            if len(result) > max_len:
                spooled = self._spool_full_output(result)
                half = max_len // 2
                note = f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                if spooled:
                    note = (
                        f"\n\n... ({len(result) - max_len:,} chars truncated; "
                        f"full output in {spooled}) ...\n\n"
                    )
                result = result[:half] + note + result[-half:]

            return result

        except Exception as e:
            # Kill and reap the child if it was spawned but an unexpected
            # error prevented communicate() from completing.
            if process is not None:
                await self._kill_process(process)
            return ToolResult.error(f"Error executing command: {str(e)}")
        finally:
            # A handed-over process keeps feeding its terminal tab; the
            # session closes the tab when the process exits.
            if agent_stream is not None and not handed_over:
                agent_stream.finish(process.returncode if process is not None else None)

    async def _keep_running(
        self,
        process: asyncio.subprocess.Process,
        prepared: _PreparedCommand,
        *,
        display_command: str,
        agent_stream: _AgentExecStream | None,
        stdout_buf: bytearray,
        stderr_buf: bytearray,
        max_output_chars: int | None,
    ) -> str | None:
        """Move a still-running foreground command into a background session.

        The turn gets back control with the output so far and a session_id;
        the process is neither killed nor restarted. None when the session
        registry is full, in which case the caller falls back to the kill.
        """
        announcer = _ExecCompletionAnnouncer.open(self._bus, command=display_command)
        try:
            session_id = await self._session_manager.adopt(
                process=process,
                command=prepared.command,
                cwd=prepared.cwd,
                timeout=None,
                owner_session_key=current_request_session_key(),
                on_output=agent_stream.feed if agent_stream is not None else None,
                on_exit=agent_stream.finish if agent_stream is not None else None,
                on_finished=announcer.on_finished if announcer is not None else None,
            )
        except RuntimeError as exc:
            logger.warning(
                "exec: cannot keep '{}' running in the background ({}); killing it",
                display_command,
                exc,
            )
            return None

        parts: list[str] = []
        if stdout_buf:
            parts.append(bytes(stdout_buf).decode("utf-8", errors="replace"))
        if stderr_buf:
            stderr_text = bytes(stderr_buf).decode("utf-8", errors="replace")
            if stderr_text.strip():
                parts.append(f"STDERR:\n{stderr_text}")
        so_far = "\n".join(parts)
        max_len = clamp_session_int(max_output_chars, self._MAX_OUTPUT, 1000, MAX_OUTPUT_CHARS)
        if len(so_far) > max_len:
            so_far = f"... ({len(so_far) - max_len:,} earlier chars omitted) ...\n" + so_far[-max_len:]
        if agent_stream is not None:
            agent_stream.feed(
                f"\n[still running after {prepared.timeout}s; continuing in "
                f"background session {session_id}]\n"
            )
        logger.info(
            "exec: '{}' still running after {}s; continuing as session {}",
            display_command,
            prepared.timeout,
            session_id,
        )
        lines = [so_far] if so_far else ["(no output yet)"]
        lines.append(
            f"\nStill running after {prepared.timeout}s. The command was not killed: "
            f"it continues in background session {session_id}. You will be told "
            "when it finishes, with its last output, so continue other work "
            f"meanwhile. To wait for it now: write_stdin(session_id={session_id!r}, "
            "yield_time_ms=...) or wait_for=...; terminate=true stops it. "
            "Do not run the command again."
        )
        lines.append(f"Process running. session_id: {session_id}")
        return "\n".join(lines)

    def _compact_exec_output(self, command: str, result: str) -> str:
        """Filter ANSI / progress / verbose command noise before char caps.

        Spools the raw result when compaction actually shrinks the payload so
        the model can still open the full log if a filter over-cut.
        """
        if not result or result == "(no output)":
            return result
        try:
            filtered = filter_command_output(command, result)
        except Exception as exc:  # noqa: BLE001 - never break exec on filter bugs
            logger.debug("exec: command_output filter failed: {}", exc)
            return result
        if not should_replace_with_compaction(filtered):
            return result
        spooled = self._spool_full_output(result)
        if spooled:
            note = f"\n[compacted {filtered.saved_chars:,} chars; full output in {spooled}]"
        else:
            note = f"\n[compacted {filtered.saved_chars:,} chars ({filtered.kind})]"
        return filtered.text + note

    def _spool_full_output(self, result: str) -> str | None:
        """Write the untruncated output to disk, so the cut middle is not lost.

        The head-and-tail view is right for a model reading a build log, but
        the elided middle is where the one interesting error usually sits.
        Cursor keeps a terminal file for exactly this reason; this is the
        equivalent, under the same directory the oversized-tool-result store
        already cleans up on a seven-day clock. Returns the path, or None when
        the workspace is not writable - truncation must not fail the command.
        """
        try:
            from navin.utils.helpers import ensure_dir

            root = ensure_dir(Path(self.working_dir) / ".navin" / "tool-results" / "exec")
            path = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.log"
            path.write_text(result, encoding="utf-8")
            return str(path)
        except Exception as exc:  # noqa: BLE001 - best effort by design
            logger.debug("exec: could not spool full output: {}", exc)
            return None

    # Live output streaming to the chat activity card (Cursor-style).
    _STREAM_INTERVAL_S = 0.08
    _STREAM_TAIL_CHARS = 8000
    # A silent command (output piped into tail, a quiet build) moves its
    # estimated bar this often.
    _ESTIMATE_INTERVAL_S = 1.0

    async def _announce_expected_duration(self, command: str, cwd: str | None) -> _RunTiming:
        """Tell the journal how long this command usually takes, before it starts."""
        from navin.quality.test_timing import command_key, expected_command_seconds, run_kind

        key = command_key(command, cwd)
        expected = await asyncio.to_thread(expected_command_seconds, key)
        await emit_tool_meta(test_expected_s=expected, test_kind=run_kind(expected))
        return _RunTiming(key, expected, time.monotonic())

    async def _remember_duration(self, timing: _RunTiming | None) -> None:
        """Keep the duration of a command that ran to its end, for the next estimate."""
        from navin.quality.test_timing import record_command_run

        if timing is not None:
            await asyncio.to_thread(record_command_run, timing.key, time.monotonic() - timing.started)

    async def _emit_estimate(self, timing: _RunTiming) -> None:
        """Progress from the usual duration alone, when the command prints nothing."""
        percent, remaining = timing.estimate()
        if percent is None:
            return
        await emit_tool_meta(percent=percent, eta_s=remaining, indeterminate=False)
        await emit_task_progress(
            label="Running…",
            percent=percent,
            eta_s=remaining,
            indeterminate=False,
            call_id=current_tool_call_id(),
        )

    async def _communicate_streaming(
        self,
        process: asyncio.subprocess.Process,
        agent_stream: _AgentExecStream | None = None,
        *,
        stdout_buf: bytearray | None = None,
        stderr_buf: bytearray | None = None,
        timing: _RunTiming | None = None,
    ) -> tuple[bytes, bytes]:
        """``communicate()`` with periodic live-output emission.

        Reads stdout/stderr incrementally; while a WebUI-style progress
        emitter is bound (see :mod:`navin.agent.tool_output`), the combined
        output tail is pushed every ``_STREAM_INTERVAL_S`` so the user can
        watch builds/installs progress in the chat. Behaves exactly like
        ``process.communicate()`` when no emitter is bound. Caller-owned
        buffers keep what was read if this coroutine is cancelled.
        """
        stdout_buf = bytearray() if stdout_buf is None else stdout_buf
        stderr_buf = bytearray() if stderr_buf is None else stderr_buf
        dirty = False

        async def pump(stream: asyncio.StreamReader | None, buf: bytearray) -> None:
            nonlocal dirty
            if stream is None:
                return
            # Incremental decode for the live terminal feed: read() slices at
            # arbitrary byte offsets, so a chunk-by-chunk decode would turn
            # multi-byte characters cut at the boundary into replacements.
            decoder = (
                codecs.getincrementaldecoder("utf-8")(errors="replace")
                if agent_stream is not None
                else None
            )
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                dirty = True
                if agent_stream is not None and decoder is not None:
                    agent_stream.feed(decoder.decode(chunk))

        async def broadcaster() -> None:
            nonlocal dirty
            last_estimate = time.monotonic()
            while True:
                await asyncio.sleep(self._STREAM_INTERVAL_S)
                if dirty:
                    dirty = False
                    last_estimate = time.monotonic()
                    await self._emit_output_tail(stdout_buf, stderr_buf, timing)
                elif (
                    timing is not None
                    and timing.expected_s
                    and time.monotonic() - last_estimate >= self._ESTIMATE_INTERVAL_S
                ):
                    last_estimate = time.monotonic()
                    await self._emit_estimate(timing)

        emit_task = (
            asyncio.create_task(broadcaster())
            if tool_output_streaming_enabled()
            else None
        )
        try:
            await asyncio.gather(
                pump(process.stdout, stdout_buf),
                pump(process.stderr, stderr_buf),
            )
            await process.wait()
        finally:
            if emit_task is not None:
                emit_task.cancel()
                with suppress(asyncio.CancelledError):
                    await emit_task
        return bytes(stdout_buf), bytes(stderr_buf)

    async def _emit_output_tail(
        self, stdout_buf: bytearray, stderr_buf: bytearray, timing: _RunTiming | None = None,
    ) -> None:
        parts: list[str] = []
        if stdout_buf:
            parts.append(bytes(stdout_buf).decode("utf-8", errors="replace"))
        if stderr_buf:
            stderr_text = bytes(stderr_buf).decode("utf-8", errors="replace")
            if stderr_text.strip():
                parts.append(f"STDERR:\n{stderr_text}")
        output = "\n".join(parts)
        if len(output) > self._STREAM_TAIL_CHARS:
            output = "…" + output[-self._STREAM_TAIL_CHARS:]
        if output:
            await self._emit_progressing_output(output, timing=timing)

    async def _emit_progressing_output(
        self, output: str, *, output_mode: str = "snapshot", timing: _RunTiming | None = None,
    ) -> None:
        parsed = parse_progress_from_output(output)
        percent = parsed.get("percent")
        eta_s = parsed.get("eta_s")
        indeterminate = parsed.get("indeterminate")
        if percent is None and timing is not None:
            # The command prints no percent of its own: the usual duration gives one.
            estimate, remaining = timing.estimate()
            if estimate is not None:
                percent, indeterminate = estimate, False
                eta_s = remaining if eta_s is None else eta_s
        await emit_tool_output(
            output,
            percent=percent,
            eta_s=eta_s,
            label=parsed.get("label"),
            indeterminate=indeterminate,
            output_mode=output_mode,
        )
        await emit_task_progress(
            label=str(parsed.get("label") or "Running…"),
            percent=percent,
            eta_s=eta_s,
            indeterminate=indeterminate,
            call_id=current_tool_call_id(),
        )

    async def _execute_session(
        self,
        prepared: _PreparedCommand,
        yield_time_ms: int | None,
        max_output_chars: int | None,
        display_command: str | None = None,
    ) -> str:
        try:
            agent_stream = _AgentExecStream.open(
                self._bus,
                command=display_command or prepared.command,
                cwd=prepared.cwd,
                background=True,
                sandbox=prepared.sandbox,
                sandbox_lifted=prepared.sandbox_lifted,
            )
            total_yield = clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS)
            max_chars = clamp_session_int(
                max_output_chars,
                DEFAULT_MAX_OUTPUT_CHARS,
                1000,
                MAX_OUTPUT_CHARS,
            )
            owner = current_request_session_key()
            announcer = _ExecCompletionAnnouncer.open(
                self._bus, command=display_command or prepared.command,
            )
            # Chunked yield so background installs keep updating the progress bar
            # instead of freezing on a single "Elapsed: 1.0s" snapshot.
            chunk_ms = 500 if total_yield > 0 else 0
            # No deadline: ``timeout`` is how long a foreground call waits, and
            # a session exists to outlive the call (dev server, watcher, a long
            # test suite). With the foreground default copied here, the next
            # poll past it killed the process: "Command timed out; session was
            # terminated" on a 30 min pytest run after 5 min. Same policy as
            # the foreground-to-background hand-over in ``_keep_running``.
            session_id, poll = await self._session_manager.start(
                command=prepared.command,
                cwd=prepared.cwd,
                env=prepared.env,
                timeout=None,
                shell_program=prepared.shell_program,
                login=prepared.login,
                yield_time_ms=min(chunk_ms, total_yield) if total_yield else 0,
                owner_session_key=owner,
                max_output_chars=max_chars,
                on_output=agent_stream.feed if agent_stream is not None else None,
                on_exit=agent_stream.finish if agent_stream is not None else None,
                on_finished=announcer.on_finished if announcer is not None else None,
            )
            waited = min(chunk_ms, total_yield) if total_yield else 0
            if poll.output:
                await self._emit_progressing_output(poll.output, output_mode="delta")
            while not poll.done and waited < total_yield:
                step = min(chunk_ms or 500, total_yield - waited)
                poll = await self._session_manager.poll(
                    session_id=session_id,
                    yield_time_ms=step,
                    max_output_chars=max_chars,
                    owner_session_key=owner,
                )
                waited += step
                if poll.output:
                    await self._emit_progressing_output(poll.output, output_mode="delta")
            if poll.done and poll.output:
                cmd = display_command or prepared.command
                poll.output = compact_session_body(cmd, poll.output, poll.exit_code)
            result = format_session_poll(session_id, poll)
            if poll.done and not poll.timed_out and not poll.terminated:
                await emit_tool_meta(percent=100, label="Command finished", indeterminate=False)
            result += sandbox_result_note(
                prepared.sandbox,
                exit_code=poll.exit_code if poll.done else None,
                lifted=prepared.sandbox_lifted,
                output=poll.output or "",
            )
            return ToolResult.error(result) if poll.timed_out else result
        except Exception as exc:
            return ToolResult.error(f"Error executing command: {exc}")

    def _resolve_timeout(self, timeout: int | None) -> int | None:
        """Resolve the effective hard timeout in seconds (None = no limit).

        A per-call timeout supplied by the model stands as asked unless the
        operator set ``tools.exec.maxTimeout``. A constant ceiling here used to
        kill any build that ran past ten minutes, which is an ordinary length
        for a full compile or a test suite, and the model had no way to say so.
        The config-level default (self.timeout) applies when the model gives no
        number, and 0 there means no limit at all.
        """
        if timeout:
            return min(timeout, self.max_timeout) if self.max_timeout else timeout
        if self.timeout and self.timeout > 0:
            return self.timeout
        return None

    def _project_root(self) -> str | None:
        """The open project folder, or the configured working dir."""
        try:
            access = current_tool_workspace(
                self.working_dir, restrict_to_workspace=self.restrict_to_workspace
            )
        except Exception:
            return self.working_dir
        return str(access.project_path) if access.project_path is not None else self.working_dir

    @staticmethod
    def _resolve_cwd(
        requested: str | None,
        workspace_root: str | None,
        *,
        restrict: bool,
    ) -> str:
        """Anchor a caller-supplied working_dir on the project root.

        Left alone, a relative ``working_dir`` is resolved by the subprocess
        against wherever navin itself was started, which is unrelated to the
        project the agent is working on, so ``working_dir="webui"`` misses. A
        leading slash gets the same project-relative reading as everywhere else.
        """
        if not requested:
            return workspace_root or os.getcwd()
        if not workspace_root:
            return requested
        rooted = project_rooted_path(
            requested,
            workspace_root,
            [workspace_root] if restrict else [],
        )
        candidate = Path(rooted).expanduser()
        if not candidate.is_absolute():
            candidate = Path(workspace_root).expanduser() / candidate
        return str(candidate)

    async def _prepare_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int | None = None,
        shell: str | None = None,
        login: bool | None = None,
        *,
        unsandboxed: bool = False,
    ) -> _PreparedCommand | str:
        """Resolve the command, or return the text explaining why it will not run.

        Async because a refusal is no longer always final: a built-in deny rule
        can be put to the user, and that answer arrives over the websocket.
        ``unsandboxed`` is the model asking to lift the configured OS sandbox
        for this one command; it goes through the same approval flow.
        """
        access = current_tool_workspace(
            self.working_dir,
            restrict_to_workspace=self.restrict_to_workspace,
        )
        workspace_root = str(access.project_path) if access.project_path is not None else self.working_dir
        cwd = self._resolve_cwd(
            working_dir,
            workspace_root,
            restrict=access.restrict_to_workspace,
        )
        # Off the event loop: on a \\wsl.localhost project every stat crosses
        # the Windows/WSL boundary and can take seconds.
        if working_dir and not await asyncio.to_thread(Path(cwd).is_dir):
            return ToolResult.error(
                f"Error: working_dir not found: {working_dir}. "
                "It is resolved against the project root, so pass a path relative "
                "to the project (for example 'webui') rather than one relative to "
                "wherever navin was started."
            )

        # Prevent an LLM-supplied working_dir from escaping the configured
        # workspace when restrict_to_workspace is enabled (#2826). Without
        # this, a caller can pass working_dir="/etc" and then all absolute
        # paths under /etc would pass the _guard_command check that anchors
        # on cwd.
        if access.restrict_to_workspace and workspace_root:
            try:
                requested = Path(cwd).expanduser().resolve()
                resolved_root = Path(workspace_root).expanduser().resolve()
            except Exception:
                return ToolResult.error(
                    "Error: working_dir could not be resolved"
                    + _WORKSPACE_BOUNDARY_NOTE
                )
            if not is_path_within(requested, resolved_root):
                refusal = await self._ask_to_lift(
                    _GuardVerdict(
                        ToolResult.error(
                            "Error: working_dir is outside the configured workspace"
                        ),
                        approvable_rule="workspaceEscape",
                    ),
                    command,
                    cwd,
                )
                if refusal is not None:
                    return _refusal_result(refusal)

        denied = macos_denied_refusal(command)
        if denied is not None:
            return _refusal_result(denied)
        verdict = await asyncio.to_thread(
            self._guard_command,
            command,
            cwd,
            restrict_to_workspace=access.restrict_to_workspace,
            workspace_root=workspace_root,
        )
        if verdict is not None:
            refusal = await self._ask_to_lift(verdict, command, cwd)
            if refusal is not None:
                return _refusal_result(refusal)
        elif self.ask_every_command:
            refusal = await self._ask_to_run(command, cwd)
            if refusal is not None:
                return _refusal_result(refusal)
        # Infrastructure tools follow the same policy here as through the
        # infra broker, so typing the command in a shell bypasses nothing.
        refusal = await self._infra_gate(
            command, cwd, workspace_root, already_asked=verdict is not None or self.ask_every_command
        )
        if refusal is not None:
            return _refusal_result(refusal)

        applied_sandbox = ""
        sandbox_lifted = False
        if self.sandbox and unsandboxed:
            refusal = await self._ask_to_unsandbox(command, cwd)
            if refusal is not None:
                return _refusal_result(refusal)
            sandbox_lifted = True
        elif self.sandbox:
            if _IS_WINDOWS:
                if self.sandbox_strict:
                    # Fail closed: the strict profile promised OS confinement
                    # and Windows has none, so refuse visibly instead of
                    # quietly running unconfined. The helper is still shipped
                    # in the Tauri sidecar; it is a pass-through on Win32.
                    return ToolResult.error(
                        "Error: the strict security profile requires an OS "
                        "sandbox and Windows has none. Run inside WSL (Landlock) "
                        "or relax tools.security_profile to proceed."
                    )
                logger.warning(
                    "Sandbox '{}' is not supported on Windows; running unsandboxed",
                    self.sandbox,
                )
            else:
                workspace = workspace_root or cwd
                unwrapped = command
                try:
                    # May probe or even compile the helper: keep it off the loop.
                    command = await asyncio.to_thread(
                        wrap_command,
                        self.sandbox, command, workspace, cwd,
                        strict=self.sandbox_strict,
                        deny_net=self.sandbox_deny_net,
                    )
                except Exception as exc:  # noqa: BLE001 - missing/broken helper must not brick exec
                    if self.sandbox_strict:
                        return ToolResult.error(f"Error: {exc}")
                    logger.warning(
                        "Sandbox '{}' wrapper failed ({}); running unsandboxed",
                        self.sandbox,
                        exc,
                    )
                # The native wrapper hands the command back untouched when the
                # helper is missing or cannot run here: that is an unconfined
                # run and must not be reported as sandboxed.
                if command != unwrapped:
                    applied_sandbox = self.sandbox
                # bwrap --chdir is inside the wrapper; start bwrap at the
                # workspace so the binary can recreate the mount. Native
                # landlock/seatbelt honour --chdir and must keep the caller's
                # working_dir, otherwise ``npm test`` in webui runs at root.
                if self.sandbox == "bwrap" and command.lstrip().startswith("bwrap "):
                    cwd = str(Path(workspace).resolve())

        effective_timeout = self._resolve_timeout(timeout)
        if (
            not timeout
            and effective_timeout
            and effective_timeout < _INSTALL_TIMEOUT_S
            and _is_package_manager_command(command)
            and not _LONG_RUNNING_RE.search(command)
        ):
            # An install or a build is meant to finish: keep it in the
            # foreground so its result comes back in this call. At 60s a slow
            # npm install (Windows Defender scans every file) moved to the
            # background and the agent spent turns polling, echoing and
            # re-running it.
            effective_timeout = (
                min(_INSTALL_TIMEOUT_S, self.max_timeout) if self.max_timeout else _INSTALL_TIMEOUT_S
            )
        env = self._build_env()

        wsl_project = _IS_WINDOWS and wsl.parse_unc(cwd) is not None
        # A WSL project runs inside the distribution through wsl.exe: Windows
        # PATH entries (venv Scripts, py launcher) mean nothing there, and
        # looking for them walks every parent folder over the UNC boundary.
        if wsl_project:
            prepend, append = "", ""
        else:
            prepend, append = await asyncio.to_thread(self._path_for, cwd, env.get("PATH", ""))
        if prepend or append:
            if _IS_WINDOWS:
                env["PATH"] = self._compose_path(env.get("PATH", ""), prepend, append)
            else:
                command = self._wrap_path_export(command, env, prepend, append)

        shell_program, shell_error = self._resolve_shell(
            shell, wsl_project=wsl_project,
        )
        if shell_error:
            return shell_error

        return _PreparedCommand(
            command=command,
            cwd=cwd,
            env=env,
            timeout=effective_timeout,
            shell_program=shell_program,
            login=False if login is None else login,
            sandbox=applied_sandbox,
            sandbox_lifted=sandbox_lifted,
        )

    async def _announce_sandbox(self, prepared: _PreparedCommand) -> None:
        """Tell the activity card how this command is confined, before it runs.

        The card shows a "Sandboxed" badge the way Cursor does, or a lifted
        marker when the user approved an unconfined run. Best-effort: emission
        only happens when a WebUI progress emitter is bound to this call.
        """
        if not prepared.sandbox and not prepared.sandbox_lifted:
            return
        await emit_tool_meta(
            sandbox=prepared.sandbox or None,
            sandbox_lifted=True if prepared.sandbox_lifted else None,
        )

    def _path_for(self, cwd: str, current_path: str) -> tuple[str, str]:
        """PATH entries to add in front of / behind the user's PATH for this command.

        Outside a Python project, navin's interpreter goes first, so a skill's
        ``python3 build_report.py`` finds python-docx and friends. Inside one,
        ``python`` must be the project's: its venv, else the user's Python (on
        Windows the ``py -3`` launcher when ``python`` is only the Store alias).
        Navin's interpreter then only comes last, as a fallback, and is always
        reachable by name as ``navin-python``. Without this, the agent created
        venvs from navin.exe and ``pip install`` went into navin's own venv.
        """
        prepend = [self.path_prepend] if self.path_prepend else []
        append = [self.path_append] if self.path_append else []
        extra = _navin_python_dir()
        if not self.runtime_bin:
            return os.pathsep.join(prepend), os.pathsep.join(append + ([extra] if extra else []))
        project = _python_project_root(Path(cwd)) if cwd else None
        if project is None:
            return (
                os.pathsep.join([self.runtime_bin, *prepend]),
                os.pathsep.join(append + ([extra] if extra else [])),
            )
        front: list[str] = []
        venv_bin = _project_venv_bin(Path(cwd), project)
        if venv_bin is not None:
            front.append(str(venv_bin))
        else:
            from navin.python_runtime import system_python

            if _IS_WINDOWS and system_python(current_path) is None:
                launcher = _py_launcher_dir()
                if launcher:
                    front.append(launcher)
        return (
            os.pathsep.join(front + prepend),
            os.pathsep.join(append + [self.runtime_bin] + ([extra] if extra else [])),
        )

    def _compose_path(self, current_path: str, prepend: str | None = None, append: str | None = None) -> str:
        prepend = self.path_prepend if prepend is None else prepend
        append = self.path_append if append is None else append
        parts = []
        if prepend:
            parts.append(prepend)
        if current_path:
            parts.append(current_path)
        if append:
            parts.append(append)
        return os.pathsep.join(parts)

    def _wrap_path_export(
        self, command: str, env: dict[str, str], prepend: str | None = None, append: str | None = None,
    ) -> str:
        prepend = self.path_prepend if prepend is None else prepend
        append = self.path_append if append is None else append
        segments = []
        if prepend:
            env["NAVIN_PATH_PREPEND"] = prepend
            segments.append("$NAVIN_PATH_PREPEND")
        segments.append("$PATH")
        if append:
            env["NAVIN_PATH_APPEND"] = append
            segments.append("$NAVIN_PATH_APPEND")
        path_expr = os.pathsep.join(segments)
        return f'export PATH="{path_expr}"; {command}'

    @staticmethod
    async def _spawn(
        command: str, cwd: str, env: dict[str, str],
        shell_program: str | None = None,
        login: bool = False,
        *,
        stdin: int = asyncio.subprocess.DEVNULL,
    ) -> asyncio.subprocess.Process:
        """Launch *command* in a platform-appropriate shell."""
        if _IS_WINDOWS:
            location = wsl.parse_unc(cwd)
            if location is not None:
                return await ExecTool._spawn_in_wsl(
                    command, location, env, shell_program, login, stdin=stdin,
                )
            return await ExecTool._spawn_windows(command, cwd, env, shell_program, login, stdin=stdin)
        return await ExecTool._spawn_unix(command, cwd, env, shell_program, login, stdin=stdin)

    @staticmethod
    async def _spawn_windows(
        command: str, cwd: str, env: dict[str, str],
        shell_program: str | None, login: bool,
        *, stdin: int,
    ) -> asyncio.subprocess.Process:
        """Route to the shell the model actually asked for.

        The trap this replaces treated every shell but ``cmd`` as PowerShell,
        so ``shell='bash'`` reached bash with ``-NoProfile`` and ``shell='wsl'``
        handed wsl.exe PowerShell flags - both failed on the first argument. The
        resolved program name decides the calling convention now: PowerShell and
        pwsh take the ``-Command`` prelude, cmd its ``/c``, wsl.exe crosses into
        the default distribution, and anything else - git-bash, msys, nu, fish -
        is a POSIX shell and gets ``-c``.
        """
        default_program = (
            await asyncio.to_thread(_which_cached, "pwsh")
            or await asyncio.to_thread(_which_cached, "powershell")
            or "powershell"
        )
        program = shell_program or default_program
        stem = PureWindowsPath(program).name.lower().removesuffix(".exe")

        if stem == "cmd":
            cmd_env = {**env, "COMSPEC": program}
            # chcp 65001 first, so OEM-codepage output (accents in dir, ipconfig)
            # comes back as UTF-8 instead of replacement characters.
            wrapped = f"chcp 65001>nul & {command}"
            return await asyncio.create_subprocess_shell(
                wrapped,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=cmd_env,
                **no_window_kwargs(),
            )
        if stem in ("powershell", "pwsh"):
            command = ExecTool._normalize_powershell_command(command)
            command = (
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'\n"
                f"{command}\n"
                "if ($LASTEXITCODE -ne $null) { exit $LASTEXITCODE }"
            )
            return await asyncio.create_subprocess_exec(
                program, "-NoProfile", "-NonInteractive", "-Command", command,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                **no_window_kwargs(),
            )
        if stem == "wsl":
            return await ExecTool._spawn_wsl_shell(command, cwd, env, stdin=stdin)
        # A POSIX shell present on Windows (git-bash, msys2 bash, nu, fish).
        return await ExecTool._spawn_unix(
            command, cwd, env, program, login, stdin=stdin
        )

    @staticmethod
    async def _spawn_wsl_shell(
        command: str, cwd: str, env: dict[str, str], *, stdin: int,
    ) -> asyncio.subprocess.Process:
        """Run a command in the default distribution for an explicit shell='wsl'.

        Unlike a project opened from a ``\\\\wsl.localhost`` path, here the
        project lives on the Windows side; the model reached for wsl on purpose,
        so the command lands in the default distribution, at the translated
        working directory when the drive maps cleanly.
        """
        wsl_exe = wsl.wsl_executable() or "wsl.exe"
        argv = [wsl_exe]
        mount = wsl.drive_to_mount(cwd)
        if mount:
            argv += ["--cd", mount]
        if _wants_pipefail(command):
            command = "set -o pipefail\n" + command
        # --exec: no second parse by the default shell (see wsl.command_prefix).
        argv += ["--exec", "bash", "-lc", command]
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # wsl.exe is a Windows process; where it starts does not matter once
            # --cd decides where the command runs.
            cwd=os.environ.get("USERPROFILE") or os.environ.get("SYSTEMROOT") or None,
            env=env,
            **no_window_kwargs(),
        )

    @staticmethod
    async def _spawn_unix(
        command: str, cwd: str, env: dict[str, str],
        shell_program: str | None, login: bool,
        *, stdin: int,
    ) -> asyncio.subprocess.Process:
        shell_program = shell_program or await asyncio.to_thread(_which_cached, "bash") or "/bin/bash"
        args = [shell_program]
        shell_name = Path(shell_program).name.lower().removesuffix(".exe")
        if login and shell_name in {"bash", "zsh"}:
            args.append("-l")
        if shell_name in {"bash", "zsh"} and _wants_pipefail(command):
            command = "set -o pipefail\n" + command
        args.extend(["-c", command])
        # Own process group, so a timeout can kill the whole tree. Without it a
        # dev server or watcher the command backgrounded outlives the SIGKILL
        # that only reaches the shell, and keeps its port.
        session_kwargs: dict[str, Any] = {} if _IS_WINDOWS else {"start_new_session": True}
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            **no_window_kwargs(),
            **session_kwargs,
        )

    @staticmethod
    async def _spawn_in_wsl(
        command: str,
        location: wsl.WslLocation,
        env: dict[str, str],
        shell_program: str | None = None,
        login: bool = False,
        *,
        stdin: int = asyncio.subprocess.DEVNULL,
    ) -> asyncio.subprocess.Process:
        """Run a command inside the distribution the project lives in.

        A project opened from Windows at ``\\\\wsl.localhost\\Ubuntu\\...`` is a
        Linux project: its virtualenv points at ``/usr/bin/python3``, its
        ``node_modules`` holds Linux binaries, and its Makefile calls tools that
        exist nowhere on the Windows side. Running its commands in PowerShell
        fails in ways that read like a broken project rather than a command sent
        to the wrong machine - PowerShell cannot even hold a UNC path as its
        working directory, so it starts in ``C:\\Windows`` and reports that
        nothing is there.

        ``bash -lc`` rather than a bare command, because a login shell is what
        puts the distribution's own toolchain - nvm, pyenv, cargo - on PATH.
        """
        # resolve_distro may call `wsl.exe -l -q` (up to 10s): not on the loop.
        distro = await asyncio.to_thread(wsl.resolve_distro, location.distro) or location.distro
        program = shell_program or "bash"
        login_shell = (shell_program is None or login) and PurePosixPath(program).name in {"bash", "zsh"}
        if Path(program).name in {"bash", "zsh"} and _wants_pipefail(command):
            command = "set -o pipefail\n" + command
        argv = [*wsl.command_prefix(distro, location.path), program, "-lc" if login_shell else "-c", command]
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # wsl.exe is a Windows process and cannot itself start in a UNC
                # path; where it starts does not matter, because --cd is what
                # decides where the command lands.
                cwd=os.environ.get("USERPROFILE") or os.environ.get("SYSTEMROOT") or None,
                env=env,
                **no_window_kwargs(),
            )
        except OSError:
            # wsl.exe itself could not be started: WSL is the suspect, not the
            # command. The health check tells the status bar if it is wedged.
            from navin.utils import wsl_health

            wsl_health.suspect(distro)
            raise
        return process

    @staticmethod
    def _normalize_powershell_command(command: str) -> str:
        stripped = command.lstrip()
        if not stripped or stripped[0] not in {"'", '"'}:
            return command

        quote = stripped[0]
        end = stripped.find(quote, 1)
        if end == -1 or end + 1 >= len(stripped) or not stripped[end + 1].isspace():
            return command

        executable = stripped[1:end]
        looks_like_windows_executable = (
            bool(re.match(r"^[A-Za-z]:[\\/]", executable))
            or executable.startswith(r"\\")
            or executable.lower().endswith((".exe", ".cmd", ".bat", ".ps1"))
        )
        if not looks_like_windows_executable:
            return command

        leading = command[: len(command) - len(stripped)]
        return f"{leading}& {stripped}"

    def _shell_allowed(self, name: str) -> bool:
        """Whether the operator's allowlist admits this shell.

        An empty list admits everything, which is the default: the host decides
        what shells exist, and a fixed set of three excluded fish, nu, ksh and -
        the one that made this visible - wsl.exe, so a Windows agent could not
        reach a Linux toolchain even with WSL installed.
        """
        if not self.allowed_shells:
            return True
        lowered = name.lower()
        stem = lowered[:-4] if lowered.endswith(".exe") else lowered
        return lowered in self.allowed_shells or stem in self.allowed_shells

    def _refuse_shell(self, shell: str) -> tuple[None, str]:
        allowed = ", ".join(sorted(self.allowed_shells))
        return None, ToolResult.error(
            f"Error: unsupported shell {shell!r}. Allowed by tools.exec.allowedShells: {allowed}"
        )

    def _resolve_shell(
        self, shell: str | None, *, wsl_project: bool = False,
    ) -> tuple[str | None, str | None]:
        if not shell:
            return None, None
        if "\0" in shell or "\n" in shell or "\r" in shell:
            return None, ToolResult.error("Error: shell contains invalid characters")
        if wsl_project:
            # The Linux executable is resolved by the distribution, not by
            # Windows shutil.which or Windows Path.is_file.
            if "\\" in shell or PureWindowsPath(shell).drive:
                return None, ToolResult.error(
                    "Error: this WSL project requires a Linux shell name or absolute "
                    "Linux path, for example 'bash' or '/bin/bash'."
                )
            name = PurePosixPath(shell).name
            if ("/" in shell and not shell.startswith("/")) or (" " in shell and "/" not in shell):
                return None, ToolResult.error("Error: shell must be a shell name or absolute Linux path")
            if not self._shell_allowed(name):
                return self._refuse_shell(shell)
            if name.lower().removesuffix(".exe") in {"cmd", "powershell", "pwsh"}:
                return None, ToolResult.error(
                    f"Error: this project lives inside WSL, so '{shell}' is not available: "
                    "commands run in Linux bash. Rewrite the command in bash syntax "
                    "(Windows files are under /mnt/c) and omit the shell parameter."
                )
            # 'wsl' explicitly selects the bridge already selected by the cwd.
            if shell.lower() in {"wsl", "wsl.exe"}:
                return None, None
            return shell, None
        path = Path(shell).expanduser()
        if path.is_absolute():
            if not self._shell_allowed(path.name):
                return self._refuse_shell(shell)
            if not path.is_file():
                return None, ToolResult.error(f"Error: shell is not found: {shell}")
            if not _IS_WINDOWS and not os.access(path, os.X_OK):
                return None, ToolResult.error(f"Error: shell is not executable: {shell}")
            return str(path), None
        if "/" in shell or "\\" in shell:
            return None, ToolResult.error("Error: shell must be a shell name or absolute path")
        if not self._shell_allowed(shell):
            return self._refuse_shell(shell)
        if _IS_WINDOWS and shell.lower() in ("cmd", "cmd.exe"):
            return os.environ.get("COMSPEC") or _which_cached("cmd") or "cmd", None
        # Cached: shutil.which walks PATH (every /mnt/c entry in a WSL guest,
        # network shares on Windows) and this runs on the event loop.
        resolved = _which_cached(shell)
        if not resolved:
            return None, ToolResult.error(f"Error: shell not found: {shell}")
        return resolved, None

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        """Kill a subprocess and reap it to prevent zombies.

        Safe to call when the process has already exited (e.g. generic
        exception handlers after a successful ``communicate()``): skips
        ``kill()`` and only runs the safety-net reap.
        """
        if process.returncode is not None:
            _reap_pid(process.pid)
            return
        try:
            with suppress(ProcessLookupError):
                process.kill()
            # The command the user asked for is a *child* of the shell that was
            # spawned, and kill() ends that shell alone. A dev server started
            # through it outlives the timeout and keeps its port, so the next
            # run fails to bind against a process nothing shows. taskkill /T
            # walks the tree on Windows; killpg covers the process group the
            # shell leads on POSIX (spawned with start_new_session=True).
            # taskkill is a process spawn with a 10 s timeout: off the loop.
            if _IS_WINDOWS:
                await asyncio.to_thread(kill_windows_process_tree, process.pid)
            kill_posix_process_group(process.pid)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5.0)
        finally:
            _reap_pid(process.pid)

    async def _wait_for_package_manager(self, command: str, cwd: str) -> str:
        """Let a package-manager command in this project finish before another starts.

        Two installs in one folder race on node_modules and the lockfile: on
        Windows the second fails on locked files ("directory not empty",
        4294963245), elsewhere the tree ends up half written. A measured
        session started npm install, npm list and rmdir node_modules while the
        first install was still running in the background. Returns "" when
        nothing ran, a note when it waited, or "Error..." when still busy.
        """
        if not (_is_package_manager_command(command) or _DEPENDENCY_PATH_RE.search(command or "")):
            return ""
        target = _effective_dir(cwd, command)
        # A session counts when it is a known package-manager command, or when
        # it is any foreground command that outlived its window (meant to
        # finish: an install or a build whose name this list does not know).
        # Servers started with background=true never hold anything.
        busy = [
            (session_id, session)
            for session_id, session in self._session_manager.running_sessions()
            if not _LONG_RUNNING_RE.search(session.command or "")
            and (
                (_is_package_manager_command(session.command) and _families_conflict(command, session.command))
                or (getattr(session, "adopted", False) and not _is_package_manager_command(session.command))
            )
            and _same_project(target, _effective_dir(session.cwd, session.command))
        ]
        if not busy:
            return ""
        session_id, session = busy[0]
        # A known package-manager run is a certain conflict: wait, and refuse
        # rather than race. An adopted command of unknown kind only might be:
        # give it a short head start, then run anyway.
        certain = _is_package_manager_command(session.command)
        started = time.monotonic()
        await emit_tool_meta(
            label=f"Waiting for {_short(session.command)} to finish", indeterminate=True
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(session.process.wait()),
                timeout=_PACKAGE_MANAGER_WAIT_S if certain else _UNKNOWN_SESSION_WAIT_S,
            )
        except asyncio.TimeoutError:
            if not certain:
                return (
                    f"[navin] `{_short(session.command)}` (session {session_id}) is still "
                    "running in this project; ran this anyway after a short wait.\n"
                )
            return (
                f"Error: `{_short(session.command)}` is still running in session "
                f"{session_id} in this project, and a second package-manager command "
                "would corrupt node_modules / the lockfile. This command was not run. "
                f"Wait for it: write_stdin(session_id='{session_id}', yield_time_ms=60000), "
                "then retry."
            )
        return (
            f"[navin] Waited {time.monotonic() - started:.0f}s for `{_short(session.command)}` "
            f"(session {session_id}, exit code {session.process.returncode}) before running this.\n"
        )

    def _build_env(self) -> dict[str, str]:
        """Build a curated environment for subprocess execution.

        PATH is forwarded from the parent on every platform: without it, a
        non-login shell falls back to its compiled-in default, and everything
        the user put on their PATH - nvm, pyenv, cargo, ``~/.local/bin``, the
        Windows interop directories under ``/mnt/c`` in WSL - silently
        disappears.         Secrets that are not needed for git / SSH / a forge CLI stay out
        unless the operator lists them in ``allowedEnvKeys``. Forge tokens
        from Settings > Git are exported under the names ``tea`` / ``gh``
        / Gitea Actions already look for (including ``GITEA_SERVER_TOKEN``).

        On Windows, ``cmd.exe`` has no login-profile mechanism, so a wider
        curated set of system variables is forwarded.
        """
        if _IS_WINDOWS:
            sr = os.environ.get("SYSTEMROOT", r"C:\Windows")
            base = {
                "SYSTEMROOT": sr,
                "COMSPEC": os.environ.get("COMSPEC", f"{sr}\\system32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{sr}\\Temp"),
                "TMP": os.environ.get("TMP", f"{sr}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{sr}\\system32;{sr}"),
                "PYTHONUNBUFFERED": "1",
                "USERNAME": os.environ.get("USERNAME", ""),
                "USERDOMAIN": os.environ.get("USERDOMAIN", ""),
                "APPDATA": os.environ.get("APPDATA", ""),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                "ProgramData": os.environ.get("ProgramData", ""),
                "ProgramFiles": os.environ.get("ProgramFiles", ""),
                "ProgramFiles(x86)": os.environ.get("ProgramFiles(x86)", ""),
                "ProgramW6432": os.environ.get("ProgramW6432", ""),
            }
        else:
            home = os.environ.get("HOME", "/tmp")
            base = {
                "HOME": home,
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "TERM": os.environ.get("TERM", "dumb"),
                "PYTHONUNBUFFERED": "1",
            }
            path = os.environ.get("PATH")
            if path:
                base["PATH"] = path
        env = build_exec_env(
            base=base,
            allowed_env_keys=self.allowed_env_keys,
            forge_tokens=self.forge_tokens,
        )
        try:
            from navin.agent.managed_policy import apply_env

            apply_env(env)  # organization-pinned variables always win
        except Exception:  # noqa: BLE001 - policy errors never break exec
            logger.exception("Managed policy env could not be applied")
        return env

    async def _infra_gate(
        self, command: str, cwd: str, workspace_root: str | None, *, already_asked: bool
    ) -> str | None:
        """Apply the project's infrastructure policy to a shell line; refusal text or None.

        ``deny`` (a change at the observe level) is refused; ``ask`` (a
        critical operation, a change outside the authorized scope, a change at
        the prepare level, a secret read) asks the user, unless this command
        was just approved through another rule. Lines without infrastructure
        tools are not touched.
        """
        from navin.infra.gate import approval_scope, exec_decision

        try:
            decision = await asyncio.to_thread(exec_decision, command, workspace_root or cwd, cwd)
        except (OSError, ValueError) as exc:
            # Fail closed: only lines that call an infrastructure tool get this far.
            logger.warning("exec: infrastructure policy check failed for {}: {}", command, exc)
            return (
                f"Error: the infrastructure policy could not be evaluated ({exc}). Nothing ran. "
                "Check .navin/infra/policy.json and the kube / cloud config files, then retry."
            )
        if decision is None or decision.action == "allow":
            return None
        reasons = "; ".join(decision.reasons) or decision.risk
        if decision.action == "deny":
            return (
                f"Error: refused by the infrastructure policy (level {decision.level}): {reasons}. "
                "Nothing ran. Check the operation with infra action=check, or ask the user to change the level."
            )
        if already_asked:
            return None
        from navin.agent.approval import ApprovalRequest, request_approval

        targets = ", ".join(f"{key} {value}" for key, value in decision.targets.items()) or "the current context"
        approval = await request_approval(ApprovalRequest(
            tool="exec",
            action=f"Run a {decision.risk} infrastructure command",
            reason=f"{reasons} (on {targets}).",
            detail=f"$ {command.strip()}\nin {cwd}",
            consequence=(
                "It acts on real infrastructure with your credentials. Prefer infra action=run: "
                "it captures the state first, verifies the result and logs it."
            ),
            scope=approval_scope(command, decision),
            allow_when_unattended=False,
        ))
        if approval.allowed:
            logger.warning("exec: user approved infrastructure command ({}): {}", decision.risk, command)
            return None
        return f"Error: the infrastructure command was not approved ({reasons}).\n{approval.reason}"

    async def _ask_to_lift(
        self,
        verdict: _GuardVerdict,
        command: str,
        cwd: str,
    ) -> str | None:
        """Put an overrulable refusal to the user; return the refusal if it stands.

        Rewriting navin's own history file stays a hard stop. Everything else
        that used to fail silently is asked in the chat.
        """
        if not verdict.approvable_rule:
            return verdict.message

        from navin.agent.approval import ApprovalRequest, request_approval

        rule = verdict.approvable_rule
        decision = await request_approval(ApprovalRequest(
            tool="exec",
            action="Run a shell command that needs your approval",
            reason=(
                "It matches "
                f"{_DENY_RULE_DESCRIPTIONS.get(rule, rule)}."
            ),
            detail=f"$ {command.strip()}\nin {cwd}",
            consequence=(
                "The command runs with your user permissions. "
                "Destructive steps cannot be undone from the review panel."
            ),
            scope=f"exec:{rule}",
            allow_when_unattended=False,
        ))
        if decision.allowed:
            logger.warning(
                "exec: user approved a command matching the {} rule: {}", rule, command
            )
            return None
        return f"{verdict.message}\n{decision.reason}"

    async def _ask_to_unsandbox(self, command: str, cwd: str) -> str | None:
        """Ask the user to lift the OS sandbox for one command; refusal text if not.

        The strict profile promised confinement and keeps it: no card, a plain
        refusal. Elsewhere this is the one escape hatch the model has when a
        command genuinely needs to write outside the project, and it never
        goes through unattended: no approver means the sandbox stays on.
        """
        if self.sandbox_strict:
            return (
                "Error: the strict security profile does not allow running "
                "outside the OS sandbox. Keep the command inside the project, "
                "or ask the user to relax tools.security_profile in Settings."
            )
        from navin.agent.approval import ApprovalRequest, request_approval

        decision = await request_approval(ApprovalRequest(
            tool="exec",
            action="Run a shell command outside the sandbox",
            reason=(
                "The agent asked to lift the OS sandbox for this command: it "
                "will be able to write anywhere on this machine, as your user."
            ),
            detail=f"$ {command.strip()}\nin {cwd}",
            consequence=(
                "Files outside the project can be created, changed or deleted, "
                "and system-wide installs go through. This cannot be undone "
                "from the review panel."
            ),
            scope="exec:sandboxEscape",
            allow_when_unattended=False,
        ))
        if decision.allowed:
            logger.warning("exec: user approved running outside the sandbox: {}", command)
            return None
        return (
            "Error: the command was not run outside the sandbox. "
            f"{decision.reason or 'The user refused.'} Run it inside the sandbox "
            "(writes limited to the project) or ask the user what to do."
        )

    async def _ask_to_run(self, command: str, cwd: str) -> str | None:
        """Ask before a command that already passed the deny / workspace guards."""
        from navin.agent.approval import ApprovalRequest, request_approval

        decision = await request_approval(ApprovalRequest(
            tool="exec",
            action="Run a shell command",
            reason="Settings require confirmation before every command.",
            detail=f"$ {command.strip()}\nin {cwd}",
            consequence=(
                "The command will run on this machine with the agent's permissions."
            ),
            scope="exec:command",
            # CLI, cron and tests used to run freely; do not take that away.
            allow_when_unattended=True,
        ))
        if decision.allowed:
            return None
        return decision.reason or "The user refused this operation."

    def _guard_command(
        self,
        command: str,
        cwd: str,
        *,
        restrict_to_workspace: bool | None = None,
        workspace_root: str | None = None,
    ) -> _GuardVerdict | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        policy_block = _workspace_policy_block(cmd)
        if policy_block:
            return _GuardVerdict(ToolResult.error(policy_block))

        mount_hint = unbounded_mount_scan(cmd)
        if mount_hint:
            return _GuardVerdict(ToolResult.error(mount_hint))

        # allow_patterns exempt specific commands (e.g. "rm -rf build") from
        # the deny list, but only the simple commands they match: see
        # policy_verdict for why `allowed && dangerous` is not exempt.
        explicitly_allowed, denied_label = policy_verdict(
            lower, self.allow_patterns, self._deny_rules
        )
        if not explicitly_allowed:
            if denied_label is not None:
                return _GuardVerdict(
                    ToolResult.error("Error: Command blocked by deny pattern filter"),
                    approvable_rule=_chat_rule(denied_label),
                )

            if self.allow_patterns:
                return _GuardVerdict(
                    ToolResult.error("Error: Command blocked by allowlist filter (not in allowlist)"),
                    approvable_rule="allowlist",
                )

        from navin.security.network import contains_internal_url
        if contains_internal_url(
            cmd,
            allow_loopback=current_scope_allows_loopback(
                enabled=self.webui_allow_local_service_access,
            ),
        ):
            # The runner turns this marker into a non-retryable security hint.
            return _GuardVerdict(
                ToolResult.error("Error: Command blocked by safety guard (internal/private URL detected)"),
                approvable_rule="ssrf",
            )

        should_restrict = self.restrict_to_workspace if restrict_to_workspace is None else restrict_to_workspace
        if should_restrict:
            cwd_path = Path(cwd).resolve()
            resolved_workspace = (
                Path(workspace_root).expanduser().resolve()
                if workspace_root
                else cwd_path
            )

            def _allowed(target: Path, *, relative_escape: bool = False) -> bool:
                if is_path_within(target, cwd_path) or is_path_within(target, resolved_workspace):
                    return True
                media_path = get_media_dir().resolve()
                if is_path_within(target, media_path):
                    return True
                return is_unrestricted_exec_path(target, relative_escape=relative_escape)

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    # Match against the un-resolved path first.  On Linux,
                    # /dev/stderr is a symlink to /proc/self/fd/2 and
                    # ``Path.resolve()`` would mask the device-file intent.
                    if self._is_benign_device_path(expanded):
                        continue
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue

                if self._is_benign_device_path(str(p)):
                    continue
                if p.is_absolute() and not _allowed(p):
                    return _GuardVerdict(
                        ToolResult.error(
                            "Error: Command blocked by safety guard (path outside working dir)"
                            + _WORKSPACE_BOUNDARY_NOTE
                        ),
                        approvable_rule="workspaceEscape",
                    )

            # ``../`` inside the project is normal (``cat ../README`` from
            # webui). Only refuse when the resolved target actually leaves
            # the workspace and is not a host toolchain path.
            for raw in self._extract_dotdot_paths(cmd):
                try:
                    p = (Path(cwd) / raw).expanduser().resolve()
                except Exception:
                    continue
                if not _allowed(p, relative_escape=True):
                    return _GuardVerdict(
                        ToolResult.error(
                            "Error: Command blocked by safety guard (path traversal detected)"
                            + _WORKSPACE_BOUNDARY_NOTE
                        ),
                        approvable_rule="workspaceEscape",
                    )

        return None

    @classmethod
    def _is_benign_device_path(cls, path: str) -> bool:
        """Return True for kernel device files that should never be workspace-blocked."""
        if path in cls._BENIGN_DEVICE_PATHS:
            return True
        return path.startswith("/dev/fd/")

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        # Windows: match drive-root paths like `C:\` as well as `C:\path\to\file`, and UNC paths like `\\server\share`
        # NOTE: `*` is required so `C:\` (nothing after the slash) is still extracted.
        win_paths = re.findall(
            r"(?<![A-Za-z])(?:[A-Za-z]:[^\s\"'|><;]*|\\\\[^\s\"'|><;]+(?:\\[^\s\"'|><;]+)*)",
            command
        )
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths

    @staticmethod
    def _extract_dotdot_paths(command: str) -> list[str]:
        """Relative operands that walk toward a parent directory.

        A blanket ``../`` substring ban refused ``cat ../README`` from a
        project subfolder. Resolve each operand against cwd instead.
        """
        return re.findall(
            r"(?:^|[\s|>'\"=])((?:\.\.[\\/])+[^\s\"'>;|<]*)",
            command,
        )


def _python_env_hint(output: str) -> str:
    """One line that ends the "pip into the system Python" detour.

    PEP 668 refusals used to cost a measured session a dozen turns of
    --break-system-packages / --user / --target attempts. The fix is always
    the same: a project virtualenv, which test_run and verify also use.
    """
    if "externally-managed-environment" not in output:
        return ""
    installer = (
        "uv venv .venv && uv pip install --python .venv -r requirements.txt"
        if shutil.which("uv")
        else "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )
    return (
        "\n[navin] This Python belongs to the OS. Install into a project virtualenv "
        f"instead (next to the code, e.g. backend/.venv): `{installer}`, then run "
        "with `.venv/bin/python`. test_run and verify pick that venv up; do not use "
        "--break-system-packages or --user."
    )


_PACKAGE_MANAGER_WAIT_S = 300.0
_INSTALL_TIMEOUT_S = 600
_UNKNOWN_SESSION_WAIT_S = 30.0
# Servers, watchers and log tails run until stopped: they never hold anything.
_LONG_RUNNING_RE = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch|preview)\b|"
    r"\b(?:next|nuxt|astro|remix|ng)\s+(?:dev|serve|start)\b|"
    r"\bcargo\s+watch\b|\bwatchexec\b|"
    r"uvicorn|gunicorn|flask\s+run|manage\.py\s+runserver|rails\s+s(?:erver)?\b|"
    r"nodemon|http\.server|\bvite\b(?!\s+build)|next\s+dev|"
    r"docker[\s-]compose\s+(?:[^;&|]*\s)?up\b(?![^;&|]*\s-d\b)|"
    r"(?:logs|tail)\s+(?:[^;&|]*\s)?-f\b|port-forward|--watch\b",
    re.IGNORECASE,
)
_PACKAGE_MANAGER_RE = re.compile(
    # Commands that write (or read mid-write) node_modules / site-packages or a
    # lockfile. Scripts (npm run dev, start, test) are not here: a dev server
    # in the background must not hold the next install for minutes.
    r"(?:^|[;&|(]\s*)(?:"
    r"(?:npm|pnpm|bun)\s+(?:install|i|add|ci|remove|rm|uninstall|un|update|up|upgrade|dedupe|prune|link|create|init|list|ls|audit)\b"
    r"|yarn(?:\s+(?:install|add|remove|upgrade|up|dedupe|create|init|list))?\s*(?:$|[;&|])"
    r"|npx\s+(?:--yes\s+|-y\s+)?create-"
    r"|(?:pip|pip3|python3?\s+-m\s+pip|uv\s+pip|poetry|pipenv)\s+(?:install|uninstall|add|remove|sync|lock|update)\b"
    r"|uv\s+(?:sync|add|remove|lock)\b"
    r"|cargo\s+(?:add|remove|update|install|fetch|generate-lockfile|build|check|test)\b"
    r"|go\s+(?:get|mod\s+(?:tidy|download|vendor)|install|build)\b"
    r"|composer\s+(?:install|update|require|remove)\b"
    r"|bundle(?:\s+(?:install|update|add))?\s*(?:$|[;&|])|gem\s+install\b"
    r"|dotnet\s+(?:restore|add|remove|build)\b"
    r"|(?:flutter|dart)\s+pub\s+(?:get|add|remove|upgrade)\b"
    r"|(?:\./gradlew|gradle)\b[^;&|]*\b(?:build|assemble|dependencies)\b|mvn\b[^;&|]*\b(?:install|package|dependency:)"
    r"|pod\s+(?:install|update)\b"
    r"|terraform\s+(?:init|plan|apply|destroy|import|state\s+(?:mv|rm|push))\b|tofu\s+(?:init|plan|apply|destroy)\b"
    r"|pulumi\s+(?:up|preview|destroy|refresh)\b|helm\s+(?:install|upgrade|uninstall|rollback|dependency\s+(?:build|update))\b"
    r"|docker[\s-]compose\s+(?:[^;&|]*\s)?(?:build|pull)\b"
    # Migration and infra tools are often started through a runner.
    r"|(?:(?:npx|bunx|pnpm\s+(?:exec|dlx)|yarn|python3?(?:\s+-m)?|poetry\s+run|uv\s+run|bundle\s+exec)\s+|bin/|\./)?(?:"
    r"prisma\s+(?:migrate|db\s+push|generate)\b|alembic\s+(?:upgrade|downgrade|revision)\b"
    r"|manage\.py\s+(?:migrate|makemigrations)\b|rails\s+db:|rake\s+db:|flyway\s+migrate\b|liquibase\s+update\b"
    r"|knex\s+migrate|sequelize(?:-cli)?\s+db:migrate|typeorm\s+migration:run|drizzle-kit\s+(?:push|migrate)\b)"
    r"|(?:del|erase)\s+/s\b|rd\s+/s\b"
    r"|(?:rm\s+-[a-z]*r[a-z]*|rmdir\s+/s|remove-item\b[^;&|]*-recurse)[^;&|]*node_modules"
    r")",
    re.IGNORECASE,
)
# Any command naming a dependency tree or lockfile, whatever tool it uses.
_DEPENDENCY_PATH_RE = re.compile(
    r"node_modules|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|"
    r"\.venv\b|requirements[\w.-]*\.txt|poetry\.lock|uv\.lock|Cargo\.lock|go\.sum|"
    r"composer\.lock|Gemfile\.lock|Podfile\.lock|(?:^|[\s/])vendor(?:/|\s|$)",
    re.IGNORECASE,
)
# Which toolchain a command belongs to: only commands of one family share
# state (a JS install and a terraform apply in a sub-folder do not). Order
# matters: "npx prisma migrate" is a migration, not a JS install.
_COMMAND_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("db", r"prisma|alembic|manage\.py\s+(?:migrate|makemigrations)|(?:rails|rake)\s+db:|flyway|liquibase|knex|sequelize|typeorm|drizzle-kit"),
        ("cleanup", r"\brm\s+-[a-z]*r|\brmdir\b|\b(?:del|erase|rd)\s+/s\b|remove-item\b"),
        ("terraform", r"\b(?:terraform|tofu)\b"),
        ("pulumi", r"\bpulumi\b"),
        ("helm", r"\bhelm\b"),
        ("compose", r"docker[\s-]compose"),
        ("js", r"\b(?:npm|pnpm|yarn|bun|bunx|npx)\b|node_modules|package-lock|yarn\.lock|pnpm-lock|bun\.lock"),
        ("python", r"\b(?:pip3?|uv|poetry|pipenv)\b|\.venv\b|requirements[\w.-]*\.txt|poetry\.lock|uv\.lock"),
        ("rust", r"\bcargo\b|Cargo\.lock"),
        ("go", r"\bgo\s|go\.sum"),
        ("php", r"\bcomposer\b"),
        ("ruby", r"\b(?:bundle|gem)\b|Gemfile"),
        ("dotnet", r"\bdotnet\b"),
        ("dart", r"\b(?:flutter|dart)\b"),
        ("jvm", r"\b(?:gradlew?|mvn)\b"),
        ("ios", r"\bpod\b|Podfile"),
    )
)


def _command_family(command: str) -> str | None:
    for name, pattern in _COMMAND_FAMILIES:
        if pattern.search(command or ""):
            return name
    return None


def _families_conflict(new: str, running: str) -> bool:
    """Whether two commands touch the same state (lockfile, tree, state lock)."""
    a, b = _command_family(new), _command_family(running)
    if "cleanup" in (a, b):
        return True  # deleting a tree races with whatever writes it
    return a is not None and a == b


_CD_PREFIX_RE = re.compile(r"^\s*(?:cd|pushd|set-location)\s+(?:/d\s+)?([\"']?)([^\"'&;|]+)\1\s*(?:&&|;)", re.IGNORECASE)


def _is_package_manager_command(command: str) -> bool:
    return bool(_PACKAGE_MANAGER_RE.search(command or ""))


def _effective_dir(cwd: str, command: str) -> Path:
    """The directory a command acts on: its cwd, after leading ``cd x &&`` steps."""
    base = Path(cwd or ".")
    rest = command or ""
    for _ in range(4):
        match = _CD_PREFIX_RE.match(rest)
        if not match:
            break
        step = Path(os.path.expanduser(match.group(2).strip()))
        base = step if step.is_absolute() else base / step
        rest = rest[match.end():]
    try:
        return base.resolve(strict=False)
    except (OSError, RuntimeError):
        return base


def _same_project(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _short(command: str, limit: int = 60) -> str:
    text = " ".join((command or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


_SCAFFOLD_RE = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+create\b|\bnpx\s+(?:--yes\s+|-y\s+)?create-|\bcreate-[a-z0-9-]+\b|"
    r"\bng\s+new\b|\bnuxi\s+init\b|\bcargo\s+new\b|\bdjango-admin\s+startproject\b|\brails\s+new\b",
    re.IGNORECASE,
)
_SCAFFOLD_REFUSAL_RE = re.compile(
    r"operation cancell?ed|not empty|already exists|aborted|cancell?ed by user|is not an empty directory|could conflict|directory is not empty|refusing to",
    re.IGNORECASE,
)


def _scaffold_hint(command: str, output: str) -> str:
    """One line that ends retrying a scaffolder against a non-empty folder.

    The folder an agent works in already holds navin's own files (.navin/),
    so ``npm create vite@latest .`` asks to overwrite, gets no answer (no
    terminal) and prints "Operation cancelled" with exit code 0. A measured
    run then tried six variants (yes |, --y, npx, echo y...).
    """
    if not _SCAFFOLD_RE.search(command or "") or not _SCAFFOLD_REFUSAL_RE.search(output or ""):
        return ""
    return (
        "\n[navin] The scaffolder stopped at a question it cannot get answered here "
        "(no terminal): the target folder is not empty, and it holds navin's .navin/ "
        "which must not be overwritten. Do not retry with yes/echo. Scaffold into a "
        "new sub-folder instead (e.g. `npm create vite@latest app -- --template "
        "react-ts`) and work inside it."
    )


_SHELL_CODE_SEARCH_RE = re.compile(
    r"\bgrep(?:\s+-[a-zA-Z]+)*\s+-[a-zA-Z]*[rR][a-zA-Z]*\b"
    r"|\bgrep\b[^|;&\n]*\s--recursive\b"
    r"|(?:^|[;&(\n])\s*rg\s"
    r"|\bgit\s+grep\b"
)
# Logs, transcripts and home folders are not project code: the grep tool is
# bound to the workspace, so the shell is the right tool there.
_SHELL_SEARCH_OUTSIDE_RE = re.compile(r"/mnt/|\.navin\b|~/|\$HOME")


def _shell_search_hint(command: str) -> str:
    """One line that moves a recursive code search back to the grep tool.

    Measured on real sessions: 154 recursive grep/rg runs on project code went
    through the shell, their output cut by ``| head`` (the rest silently lost),
    unranked, generated trees included. The grep tool ranks declarations first,
    skips generated trees and pages instead of truncating.
    """
    text = command or ""
    if not _SHELL_CODE_SEARCH_RE.search(text) or _SHELL_SEARCH_OUTSIDE_RE.search(text):
        return ""
    return (
        "\n[navin] Recursive code search through the shell: use the grep tool for "
        "the next one (declarations ranked first, generated trees skipped, results "
        "paged instead of cut by head; several patterns in one call)."
    )


# pytest options whose value is the next token, so that value is not a path.
_PYTEST_VALUE_OPTIONS = frozenset({
    "-k", "-m", "-p", "-o", "-W", "-c", "-r", "-n", "--maxfail", "--tb", "--rootdir",
    "--confcutdir", "--basetemp", "--junitxml", "--junit-xml", "--durations",
    "--durations-min", "--timeout", "--log-level", "--log-cli-level", "--log-file",
    "--deselect", "--ignore", "--ignore-glob", "--import-mode", "--override-ini", "--color",
    "--capture", "--cov", "--cov-report", "--cov-config", "--dist", "--numprocesses",
    "--html", "--result-log", "--pythonwarnings", "--assert", "--doctest-glob",
})
# Launchers that run the program named after them: ``uv run pytest``,
# ``timeout 600 python -m pytest``.
_PYTEST_LAUNCHERS = frozenset({"uv", "poetry", "pipenv", "pdm", "hatch", "env", "nohup", "time", "timeout", "nice"})
_PYTHON_PROGRAM_RE = re.compile(r"python[0-9.]*|pypy[0-9.]*|py|navin-python")
_PYTHON_FLAGS = frozenset({"-B", "-u", "-E", "-s", "-S", "-I", "-O", "-OO", "-q", "-b", "-d", "-P"})
# ``> out.log``, ``2>&1``, ``>>log``: the operator, with or without its target.
_REDIRECT_RE = re.compile(r"^\d*(?:>>?|<<?<?|&>>?|>\|)(?:&\d+)?(.*)$")
_PYTEST_BARE_FOLDERS = frozenset({"tests", "test"})


def _pytest_arguments(tokens: list[str]) -> list[str] | None:
    """The arguments of the pytest invocation in ``tokens``, or None when it is not one."""
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]  # FOO=bar prefixes
    while tokens and os.path.basename(tokens[0]) in _PYTEST_LAUNCHERS:
        tokens = tokens[1:]
        while tokens and (tokens[0].startswith("-") or re.fullmatch(r"\d+[smhd]?", tokens[0])):
            tokens = tokens[1:]
        if tokens and tokens[0] in {"run", "exec"}:
            tokens = tokens[1:]
    if not tokens:
        return None
    program = os.path.basename(tokens[0]).lower().removesuffix(".exe")
    args = tokens[1:]
    if program in {"pytest", "py.test"}:
        return args
    if _PYTHON_PROGRAM_RE.fullmatch(program):
        while args and args[0] in _PYTHON_FLAGS:
            args = args[1:]
        if len(args) >= 2 and args[0] == "-m" and args[1] == "pytest":
            return args[2:]
    return None


def _missing_pytest_paths(command: str, cwd: str) -> list[tuple[str, Path]]:
    """pytest file or folder arguments of ``command`` that do not exist, with the
    directory each was resolved from.

    Seen in a session: 21 test files in one run, one of them a guessed name;
    pytest printed "file or directory not found" and ran none of the other 20.
    """
    missing: list[tuple[str, Path]] = []
    current = Path(cwd or ".")
    for segment in shell_segments(command):
        try:
            tokens = shlex.split(segment.replace("\\", "/"))
        except ValueError:
            continue
        if not tokens:
            continue
        if tokens[0] == "cd":
            if len(tokens) > 1 and not tokens[1].startswith("-"):
                step = Path(os.path.expanduser(tokens[1]))
                current = step if step.is_absolute() else current / step
            continue
        args = _pytest_arguments(tokens)
        if args is None:
            continue
        skip_value = False
        for token in args:
            if skip_value:
                skip_value = False
                continue
            redirect = _REDIRECT_RE.match(token)
            if redirect:
                skip_value = not redirect.group(1)
                continue
            if token.startswith("-"):
                skip_value = token in _PYTEST_VALUE_OPTIONS
                continue
            head = token.split("::", 1)[0]
            if not head or "=" in head or any(ch in head for ch in "*?[$`"):
                continue
            if "/" not in head and not head.endswith(".py") and head not in _PYTEST_BARE_FOLDERS:
                continue
            path = Path(os.path.expanduser(head))
            if not path.is_absolute():
                path = current / path
            try:
                exists = path.exists()
            except OSError:
                continue  # unreadable, not absent: pytest reports it with the real error
            if not exists:
                missing.append((head, current))
    return missing


def _missing_pytest_message(missing: list[tuple[str, Path]]) -> str:
    """The refusal for a pytest command naming files that do not exist."""
    from navin.quality.testing import missing_target_error

    notes = []
    for head, base in missing:
        note = missing_target_error(base, head)
        notes.append(note or f"{head} does not exist (resolved from {base}).")
    return (
        "Error: pytest would stop before running a single test, because the "
        "command names a path that does not exist. " + " ".join(notes)
    )
