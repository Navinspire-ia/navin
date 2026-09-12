# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Shell execution tool."""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import shutil
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
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
            tools_config.restrict_to_workspace = config.tools.restrict_to_workspace
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
        if self.allow_patterns and any(
            re.fullmatch(p, lower) for p in self.allow_patterns
        ):
            return None
        for pattern, label in self.deny_rules:
            if re.search(pattern, lower):
                return _GuardVerdict(
                    ToolResult.error("Error: stdin blocked by deny pattern filter"),
                    approvable_rule=_chat_rule(label),
                )
        return None

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


class _AgentExecStream:
    """Throttled live feed of one exec command to the editor terminal panel.

    Cursor-style agent terminals: a ``start`` frame opens a read-only tab in
    the WebUI, ``output`` frames stream deltas, an ``exit`` frame closes the
    story with the return code. Frames travel on the outbound bus so they
    reach every window subscribed to the chat; when no editor is connected
    (CLI, other channels) :meth:`open` returns ``None`` and exec behaves
    exactly as before.
    """

    _FLUSH_INTERVAL_S = 0.5
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
            lines.append(
                "Act on this if you were waiting for it (build, tests, install). "
                "If it was a server or a watcher, it has stopped. This session is "
                "closed; do not poll it."
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
        path_prepend = os.pathsep.join(
            value for value in (_runtime_bin_dir(), cfg.path_prepend) if value
        )
        return cls(
            working_dir=ctx.workspace,
            timeout=cfg.timeout,
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            webui_allow_local_service_access=ctx.config.webui_allow_local_service_access,
            sandbox=cfg.sandbox,
            sandbox_strict=getattr(ctx.config, "security_profile", None) == "strict",
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
        path_prepend: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
        forge_tokens: dict[str, str] | None = None,
        session_manager: Any | None = None,
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
        platform_note = (
            "On Windows, use PowerShell syntax by default; pass shell='cmd' "
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
            f"{sandbox_tool_description(self.sandbox)}"
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
        await self._announce_sandbox(prepared)

        if yield_time_ms is not None:
            return await self._execute_session(
                prepared, yield_time_ms, max_output_chars, display_command=command
            )

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
                        process, agent_stream, stdout_buf=stdout_buf, stderr_buf=stderr_buf
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
    _STREAM_INTERVAL_S = 0.5
    _STREAM_TAIL_CHARS = 8000

    async def _communicate_streaming(
        self,
        process: asyncio.subprocess.Process,
        agent_stream: _AgentExecStream | None = None,
        *,
        stdout_buf: bytearray | None = None,
        stderr_buf: bytearray | None = None,
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
            while True:
                await asyncio.sleep(self._STREAM_INTERVAL_S)
                if not dirty:
                    continue
                dirty = False
                await self._emit_output_tail(stdout_buf, stderr_buf)

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

    async def _emit_output_tail(self, stdout_buf: bytearray, stderr_buf: bytearray) -> None:
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
            await self._emit_progressing_output(output)

    async def _emit_progressing_output(self, output: str) -> None:
        parsed = parse_progress_from_output(output)
        await emit_tool_output(
            output,
            percent=parsed.get("percent"),
            eta_s=parsed.get("eta_s"),
            label=parsed.get("label"),
            indeterminate=parsed.get("indeterminate"),
        )
        await emit_task_progress(
            label=str(parsed.get("label") or "Running…"),
            percent=parsed.get("percent"),
            eta_s=parsed.get("eta_s"),
            indeterminate=parsed.get("indeterminate"),
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
            session_id, poll = await self._session_manager.start(
                command=prepared.command,
                cwd=prepared.cwd,
                env=prepared.env,
                timeout=prepared.timeout,
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
                await self._emit_progressing_output(poll.output)
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
                    await self._emit_progressing_output(poll.output)
            if poll.done and poll.output:
                cmd = display_command or prepared.command
                poll.output = compact_session_body(cmd, poll.output, poll.exit_code)
            result = format_session_poll(session_id, poll)
            if poll.output or not poll.done:
                await self._emit_progressing_output(result)
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
        if working_dir and not Path(cwd).is_dir():
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
                    return refusal

        verdict = self._guard_command(
            command,
            cwd,
            restrict_to_workspace=access.restrict_to_workspace,
            workspace_root=workspace_root,
        )
        if verdict is not None:
            refusal = await self._ask_to_lift(verdict, command, cwd)
            if refusal is not None:
                return refusal
        elif self.ask_every_command:
            refusal = await self._ask_to_run(command, cwd)
            if refusal is not None:
                return refusal

        applied_sandbox = ""
        sandbox_lifted = False
        if self.sandbox and unsandboxed:
            refusal = await self._ask_to_unsandbox(command, cwd)
            if refusal is not None:
                return refusal
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
                    command = wrap_command(
                        self.sandbox, command, workspace, cwd,
                        strict=self.sandbox_strict,
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
        env = self._build_env()

        if self.path_prepend or self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = self._compose_path(env.get("PATH", ""))
            else:
                command = self._wrap_path_export(command, env)

        shell_program, shell_error = self._resolve_shell(shell)
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

    def _compose_path(self, current_path: str) -> str:
        parts = []
        if self.path_prepend:
            parts.append(self.path_prepend)
        if current_path:
            parts.append(current_path)
        if self.path_append:
            parts.append(self.path_append)
        return os.pathsep.join(parts)

    def _wrap_path_export(self, command: str, env: dict[str, str]) -> str:
        segments = []
        if self.path_prepend:
            env["NAVIN_PATH_PREPEND"] = self.path_prepend
            segments.append("$NAVIN_PATH_PREPEND")
        segments.append("$PATH")
        if self.path_append:
            env["NAVIN_PATH_APPEND"] = self.path_append
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
                return await ExecTool._spawn_in_wsl(command, location, env, stdin=stdin)
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
        default_program = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
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
        argv += ["--", "bash", "-lc", command]
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
        shell_program = shell_program or shutil.which("bash") or "/bin/bash"
        args = [shell_program]
        shell_name = Path(shell_program).name.lower().removesuffix(".exe")
        if login and shell_name in {"bash", "zsh"}:
            args.append("-l")
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
        distro = wsl.resolve_distro(location.distro) or location.distro
        argv = [*wsl.command_prefix(distro, location.path), "bash", "-lc", command]
        return await asyncio.create_subprocess_exec(
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

    def _resolve_shell(self, shell: str | None) -> tuple[str | None, str | None]:
        if not shell:
            return None, None
        if "\0" in shell or "\n" in shell or "\r" in shell:
            return None, ToolResult.error("Error: shell contains invalid characters")
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
            return os.environ.get("COMSPEC") or shutil.which("cmd") or "cmd", None
        resolved = shutil.which(shell)
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
            kill_windows_process_tree(process.pid)
            kill_posix_process_group(process.pid)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5.0)
        finally:
            _reap_pid(process.pid)

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
        return build_exec_env(
            base=base,
            allowed_env_keys=self.allowed_env_keys,
            forge_tokens=self.forge_tokens,
        )

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

        # allow_patterns take priority over deny_patterns so that users can
        # exempt specific commands (e.g. "rm -rf" inside a build directory)
        # from the hardcoded deny list via configuration.
        explicitly_allowed = bool(self.allow_patterns) and any(
            re.fullmatch(p, lower) for p in self.allow_patterns
        )
        if not explicitly_allowed:
            for pattern, label in self._deny_rules:
                if re.search(pattern, lower):
                    return _GuardVerdict(
                        ToolResult.error("Error: Command blocked by deny pattern filter"),
                        approvable_rule=_chat_rule(label),
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
