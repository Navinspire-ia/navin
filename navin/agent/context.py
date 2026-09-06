"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from navin.agent.memory import MemoryStore
from navin.agent.skills import MAX_OWNED_PRELOAD, SkillsLoader
from navin.agent.tools import mcp as mcp_tools
from navin.agent.tools.registry import ToolRegistry
from navin.apps.cli import utils as cli_app_utils
from navin.bus.events import InboundMessage
from navin.runtime_context import (
    RUNTIME_CONTEXT_END,
    RUNTIME_CONTEXT_MESSAGE_META,
    RUNTIME_CONTEXT_TAG,
    RuntimeContextBlock,
    append_runtime_context,
)
from navin.utils.helpers import (
    detect_image_mime,
    load_bundled_template,
    truncate_text_to_tokens,
)
from navin.utils.prompt_templates import render_template


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted kwargs for turn-attached capabilities."""
    return cli_app_utils.session_extra(metadata) | mcp_tools.session_extra(metadata)


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    await mcp_tools.connect_missing_servers(state, tools)


async def close_mcp(state: Any) -> None:
    await mcp_tools.close_mcp_servers(state)


async def handle_runtime_control(state: Any, msg: InboundMessage, tools: ToolRegistry) -> bool:
    from navin.agent.approval import handle_approval_decision, handle_approvals_query
    from navin.agent.choice import handle_choice_answer, handle_choices_query
    from navin.agent.subagent import handle_multitask_spawn, handle_subagents_query
    from navin.agent.tools import shell as shell_tools

    # Approvals and choices go first: a tool call is suspended on this answer,
    # so it is the control message whose latency the user can feel.
    if await handle_approval_decision(state, msg, tools):
        return True
    if await handle_approvals_query(state, msg, tools):
        return True
    if await handle_choice_answer(state, msg, tools):
        return True
    if await handle_choices_query(state, msg, tools):
        return True
    if await handle_subagents_query(state, msg, tools):
        return True
    if await handle_multitask_spawn(state, msg, tools):
        return True
    if await shell_tools.handle_exec_policy_reload(state, msg, tools):
        return True
    return await mcp_tools.handle_runtime_control(state, msg, tools)


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    _RUNTIME_CONTEXT_TAG = RUNTIME_CONTEXT_TAG
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_TOKENS = 8_000  # hard cap on recent history section size (tokens)
    _MAX_PROJECT_RULES_TOKENS = 4_000  # hard cap on imported project rules section
    _RUNTIME_CONTEXT_END = RUNTIME_CONTEXT_END

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        session_summary: str | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
        extra_disabled_skills: set[str] | None = None,
        evidence_only: bool = False,
        slim_skill_preload: bool = True,
        current_message: str | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills.

        ``slim_skill_preload`` defaults on because prompt size is wall-clock,
        not just cost: a 57k prompt costs ~2.3s more per call than a small
        one even at a 100% cache hit, and a multi-step turn pays that on
        every step. Preloading every skill body spent ~9k tokens per call on
        playbooks the turn mostly did not use; they are one
        ``skill action=read`` away instead.
        """
        root = workspace or self.workspace
        parts = [self._get_identity(channel=channel, workspace=root)]
        disabled = set(self.skills.disabled_skills)
        if extra_disabled_skills:
            disabled.update(extra_disabled_skills)
        same_root = (
            root.expanduser().resolve(strict=False)
            == self.workspace.expanduser().resolve(strict=False)
        )
        # One project = one brain: when the turn runs on a project workspace,
        # the "# Memory" section must come from that project's MEMORY.md, not
        # from the gateway's default workspace (identity.md promises this and
        # the Brain panel edits the project file).
        memory_store = self.memory if same_root else MemoryStore(root)
        if same_root and not extra_disabled_skills:
            skills = self.skills
        else:
            skills = SkillsLoader(
                root,
                builtin_skills_dir=self.skills.builtin_skills,
                disabled_skills=disabled,
            )

        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        # Rules the repository already carries (.cursor/rules, .cursorrules,
        # .clinerules, .windsurfrules, copilot-instructions, .navin/rules):
        # imported projects keep their conventions with no migration step.
        from navin.agent.project_rules import project_rules_summary

        rules = project_rules_summary(root)
        if rules:
            rules = truncate_text_to_tokens(rules, self._MAX_PROJECT_RULES_TOKENS)
            parts.append(
                "# Project Rules\n\n"
                "Durable rules this repository defines for coding agents. "
                "Follow them like AGENTS.md instructions.\n\n" + rules
            )

        parts.append(
            render_template(
                "agent/tool_contract_slim.md"
                if slim_skill_preload
                else "agent/tool_contract.md"
            )
        )
        if evidence_only:
            # Review / Security / Debug: hard ban on invented findings.
            parts.append(render_template("agent/evidence_only.md"))

        memory = memory_store.get_memory_context()
        if memory and not self._is_template_content(memory_store.read_memory(), "memory/MEMORY.md"):
            parts.append(f"# Memory\n\n{memory}")

        # always=true skills plus an optional per-turn preload list (workflow
        # briefs set this so "/studio" etc. actually inject SKILL.md bodies
        # instead of only naming them in the user message). Slim /forge turns
        # keep names only and load one playbook per phase via `skill`.
        always_skills = skills.get_always_skills()
        owned = skills.owned_skills()
        owned_names = [entry["name"] for entry in owned[:MAX_OWNED_PRELOAD]]
        mentioned = skills.mentioned_skill_names(current_message or "")
        seen_active: set[str] = set()

        def _take(names: Sequence[str] | None) -> list[str]:
            taken: list[str] = []
            for raw in names or ():
                key = (raw or "").strip()
                if not key or key in seen_active or key in disabled:
                    continue
                seen_active.add(key)
                taken.append(key)
            return taken

        if slim_skill_preload:
            # Mentioned $skills first: the user named the playbook for this task.
            body_names = _take(
                [name for name in always_skills if name == "memory"] + list(mentioned)
            )
            suggested = _take(
                [name for name in always_skills if name != "memory"]
                + list(skill_names or [])
            )
            owned_listed = _take(owned_names)
            blocks: list[str] = [
                "Load playbooks on demand. For the current phase, call "
                "`skill action=read name=<one skill>` and follow that file. "
                "Do not read every listed skill at the start of the turn, "
                "and do not load a second playbook until that phase needs it."
            ]
            if suggested:
                blocks.append("Suggested for this turn: " + ", ".join(suggested) + ".")
            if owned_listed:
                listed = "\n".join(
                    f"{index}. {name}" for index, name in enumerate(owned_listed, start=1)
                )
                blocks.append(
                    "User-added skills (same on-demand rule, catalog order):\n\n"
                    f"{listed}"
                )
            bodies = skills.load_skills_for_context(body_names) if body_names else ""
            if bodies:
                blocks.append(bodies)
            parts.append("# Active Skills\n\n" + "\n\n".join(blocks))
        else:
            active_skills = _take(list(always_skills) + list(skill_names or []))
            active_skills.extend(_take(owned_names))
            active_skills.extend(_take(mentioned))
            if active_skills:
                full_body = {
                    "fullstack-dev",
                    "ui-ux-pro-max",
                    "code-reviewer",
                    "security-auditor",
                    "debug-live",
                    "studio-html-report",
                    "task-planner",
                    "project-board",
                }
                full_body.update(
                    entry["name"]
                    for entry in owned
                    if entry.get("source") in {"workspace", "user"}
                )
                always_content = skills.load_skills_for_context(
                    active_skills,
                    slim=False,
                    full_body_names=full_body,
                )
                if always_content:
                    if owned_names:
                        listed = "\n".join(
                            f"{index}. {name}"
                            for index, name in enumerate(owned_names, start=1)
                        )
                        always_content = (
                            "User-added and installed skills, in catalog order. "
                            "They are loaded below and must be followed. "
                            "Name them in this same order at the start of your reply "
                            "when they apply:\n\n"
                            f"{listed}\n\n"
                            f"{always_content}"
                        )
                    parts.append(f"# Active Skills\n\n{always_content}")

        # Names only: the full per-skill descriptions cost ~10K tokens per
        # turn at catalog size; the `skill` tool serves them on demand.
        skills_index = skills.build_skills_index(exclude=seen_active)
        if skills_index:
            parts.append(render_template("agent/skills_section.md", skills_index=skills_index))

        # Agent definitions carried by the project itself (.claude/agents,
        # .navin/agents, .opencode/agents, ...): the model must know they exist
        # to route work to them through spawn(agent=...).
        from navin.agent.project_agents import project_agents_summary

        agents_summary = project_agents_summary(root)
        if agents_summary:
            parts.append(
                "# Project Subagents\n\n"
                "This project defines its own specialized subagents. Delegate a "
                "task to one with the spawn tool and its name, e.g. "
                'spawn(agent="<name>", task="..."). Their instructions come '
                "from the project's config folders (.claude/agents, "
                ".navin/agents, ...).\n\n"
                f"{agents_summary}"
            )

        if include_memory_recent_history:
            # Recent history stays on the loop store: the Consolidator writes
            # its summaries there for every session, so a project-scoped read
            # would silently show nothing until Dream is re-scoped as well.
            entries = self.memory.read_recent_history_for_prompt(
                since_cursor=self.memory.get_last_dream_cursor(),
                session_key=session_key,
                unified_session=unified_session,
            )
            if entries:
                capped = entries[-self._MAX_RECENT_HISTORY:]
                history_text = "\n".join(
                    f"- [{e['timestamp']}] {e['content']}" for e in capped
                )
                history_text = truncate_text_to_tokens(history_text, self._MAX_HISTORY_TOKENS)
                parts.append("# Recent History\n\n" + history_text)

        if session_summary:
            # Earlier turns were dropped to free context. This is the only
            # record of them, so the model is told to treat it as established
            # fact rather than as background it may re-derive.
            parts.append(
                "# Where This Work Stood\n\n"
                "Earlier turns of this conversation were compacted away. The "
                "notes below are what remains of them: treat them as already "
                "done, and do not redo the steps they describe.\n\n"
                f"{session_summary}"
            )

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None, workspace: Path | None = None) -> str:
        """Get the core identity section."""
        root = workspace or self.workspace
        workspace_path = str(root.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            if not left:
                return right
            if not right:
                return left
            return f"{left}\n\n{right}"

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [item if isinstance(item, dict) else {"type": "text", "text": str(item)} for item in value]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load all bootstrap files from workspace.

        Navin's own copies live in ``.navin/``; a root-level file is still
        honored because it is either a legacy layout not yet migrated or a
        user-managed file that other tools read there (root ``AGENTS.md`` is a
        cross-tool convention). Identical duplicates are loaded once.
        """
        from navin import workspace_layout

        parts = []
        root = workspace or self.workspace

        any_agents_md = False
        for filename in self.BOOTSTRAP_FILES:
            seen: list[str] = []
            candidates = (
                workspace_layout.brain_file(root, filename),
                root / filename,
            )
            for file_path in candidates:
                if not file_path.exists():
                    continue
                if filename == "AGENTS.md":
                    any_agents_md = True
                content = self._resolve_at_imports(
                    file_path.read_text(encoding="utf-8"), file_path
                )
                if content in seen:
                    continue
                seen.append(content)
                parts.append(f"## {filename}\n\n{content}")

        # Projects configured for Claude Code carry their instructions in
        # CLAUDE.md; honor it when no AGENTS.md exists so those repositories
        # work in Navin without an import step. AGENTS.md wins when both are
        # present: most such repos make one a copy of the other, and loading
        # both would duplicate the instructions.
        if not any_agents_md:
            claude_md = root / "CLAUDE.md"
            if claude_md.exists():
                content = self._resolve_at_imports(
                    claude_md.read_text(encoding="utf-8"), claude_md
                )
                parts.insert(0, f"## CLAUDE.md\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    _AT_IMPORT = re.compile(r"^@(\S+)\s*$", re.MULTILINE)
    _MAX_IMPORT_DEPTH = 3
    _MAX_IMPORT_BYTES = 256 * 1024

    @classmethod
    def _resolve_at_imports(
        cls,
        content: str,
        source: Path,
        _depth: int = 0,
        _seen: set[Path] | None = None,
    ) -> str:
        """Inline Claude Code's ``@path`` file imports.

        Repositories wired for Claude Code often ship a CLAUDE.md shim whose
        whole body is ``@AGENTS.md`` (Claude Code reads that import natively;
        AGENTS.md alone it does not). Resolving the same syntax keeps those
        instructions intact here. Only lines that are exactly ``@<path>``
        count - inline mentions like emails or handles are left alone.
        Missing files resolve to nothing, cycles and depth are bounded.
        """
        if _depth >= cls._MAX_IMPORT_DEPTH or "@" not in content:
            return content
        seen = _seen if _seen is not None else {source.resolve()}

        def _inline(match: re.Match[str]) -> str:
            target = (source.parent / match.group(1)).resolve()
            if target in seen:
                return ""
            try:
                if not target.is_file() or target.stat().st_size > cls._MAX_IMPORT_BYTES:
                    return ""
                imported = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return ""
            seen.add(target)
            return cls._resolve_at_imports(imported, target, _depth + 1, seen)

        return cls._AT_IMPORT.sub(_inline, content)

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        tpl = load_bundled_template(template_path)
        if tpl is not None:
            return content.strip() == tpl.strip()
        return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        sender_id: str | None = None,
        session_summary: str | None = None,
        session_metadata: Mapping[str, Any] | None = None,
        runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
        extra_disabled_skills: set[str] | None = None,
        evidence_only: bool = False,
        slim_skill_preload: bool = True,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        root = workspace or self.workspace
        user_content = self._build_user_content(current_message, media)
        blocks = list(runtime_context_blocks or ()) if current_role == "user" else []
        merged, runtime_context_meta = append_runtime_context(user_content, blocks)
        from navin.command.modules import (
            SLIM_SKILL_PRELOAD_METADATA_KEY,
            metadata_requests_evidence_only,
        )

        if not evidence_only:
            evidence_only = metadata_requests_evidence_only(session_metadata)
        if not slim_skill_preload and isinstance(session_metadata, dict):
            slim_skill_preload = bool(
                session_metadata.get(SLIM_SKILL_PRELOAD_METADATA_KEY)
            )
        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    skill_names,
                    channel=channel,
                    session_summary=session_summary,
                    workspace=root,
                    include_memory_recent_history=include_memory_recent_history,
                    session_key=session_key,
                    unified_session=unified_session,
                    extra_disabled_skills=extra_disabled_skills,
                    evidence_only=evidence_only,
                    slim_skill_preload=slim_skill_preload,
                    current_message=current_message,
                ),
            },
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            if current_role == "user" and runtime_context_meta is not None:
                internal_meta = dict(last.get("_meta") or {})
                internal_meta[RUNTIME_CONTEXT_MESSAGE_META] = runtime_context_meta
                last["_meta"] = internal_meta
            messages[-1] = last
            return messages
        current = {"role": current_role, "content": merged}
        if current_role == "user" and runtime_context_meta is not None:
            current["_meta"] = {RUNTIME_CONTEXT_MESSAGE_META: runtime_context_meta}
        messages.append(current)
        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]
