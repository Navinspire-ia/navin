# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Skill tool: on-demand access to the skill catalog.

The system prompt lists only skill *names* (the full catalog of descriptions
costs ~10K tokens per turn). This tool serves descriptions, paths and full
SKILL.md bodies when the model actually needs a playbook.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.skills import skill_source_label
from navin.agent.tools.base import Tool

SKILL_SUGGESTION_HEADER = "Skills that fit this request:"
_SUGGEST_STOP_WORDS = frozenset({
    "with", "that", "this", "from", "your", "into", "about", "have", "make", "want",
    "need", "please", "then", "when", "what", "comme", "dans", "pour", "avec", "veux",
    "faire", "change", "changer", "mettre", "plus", "tout", "tous", "sans", "être",
    "salut", "merci", "bien", "file", "files", "code", "user", "using", "used",
    "the", "and", "for", "les", "des", "une", "est", "app", "new", "all", "son", "ses",
})


class SkillCatalogTool(Tool):
    def __init__(
        self,
        workspace: Path | None = None,
        disabled_skills: set[str] | None = None,
        builtin_skills_dir: Path | None = None,
        trust_workspace_harness_skills: bool = True,
    ) -> None:
        self._workspace = workspace
        self._disabled = disabled_skills or set()
        self._builtin_skills_dir = builtin_skills_dir
        self._trust_workspace_harness_skills = trust_workspace_harness_skills

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        disabled: set[str] = set()
        trust_harness = True
        try:
            disabled = set(ctx.config.agents.defaults.disabled_skills or [])
            trust_harness = bool(
                getattr(ctx.config.agents.defaults, "trust_workspace_harness_skills", True)
            )
        except Exception:
            disabled = set()
        return cls(
            workspace=Path(ctx.workspace),
            disabled_skills=disabled,
            trust_workspace_harness_skills=trust_harness,
        )

    @property
    def name(self) -> str:
        return "skill"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Look up installed skills (playbooks). The system prompt only "
            "lists skill names; use action=find with a few keywords to get "
            "descriptions and availability, then action=read name=<skill> to "
            "load the full SKILL.md before following it. action=list shows "
            "every name. action=graph walks the skill relation graph: give a "
            "task description (query=) or a skill name (name=) and it returns "
            "the best skills plus the related ones, so you can pick the "
            "right playbook even when its description never mentions your "
            "keywords."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["find", "read", "list", "graph"],
                    "description": (
                        "find (keyword search), read (full SKILL.md), "
                        "list (all names), graph (best skills for a task "
                        "with the relations that link them)"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Keywords for action=find, e.g. 'pdf report' or "
                        "'deploy docker'; task description for action=graph"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Exact skill name for action=read; with action=graph, "
                        "show the skills related to this one"
                    ),
                },
            },
            "required": ["action"],
        }

    def _loader(self):
        from navin.agent.skills import SkillsLoader
        from navin.security.workspace_access import current_tool_workspace

        # Use the same project as ContextBuilder and the filesystem tools.
        # SkillsLoader owns discovery across every harness, user library and
        # plugin. A second folder allowlist here hid .agents/.claude skills
        # and could load another project's instructions under the same name.
        root = current_tool_workspace(self._workspace).project_path or self._workspace or Path.cwd()
        return SkillsLoader(
            root,
            builtin_skills_dir=self._builtin_skills_dir,
            disabled_skills=self._disabled,
            trust_workspace_harness_skills=self._trust_workspace_harness_skills,
        )

    def runtime_context_provider(self):
        return self._provide_skill_suggestions

    async def _provide_skill_suggestions(self, request: Any) -> Any:
        """Put the skills that fit this request in front of the model, every
        new request, without waiting for it to think of ``action=graph``. In
        real sessions it never did, so playbooks went unused."""
        from navin.runtime_context import RuntimeContextBlock

        text = (getattr(request, "original_user_text", None) or "").strip()
        if len(text) < 12 or text.startswith(("[", "/")):
            return None
        try:
            rows = await asyncio.to_thread(self._suggest_for_request, text)
        except BaseException:  # orientation is never worth failing a turn
            return None
        if not rows:
            return None
        names = ", ".join(row["name"] for row in rows)
        lines = [f"{SKILL_SUGGESTION_HEADER} {names}."]
        lines.extend(f"- {row['name']}: {row['description']}" for row in rows)
        lines.append("Read one with `skill action=read name=<name>` if it applies; ignore them otherwise.")
        return RuntimeContextBlock(source="skills", content="\n".join(lines))

    def _suggest_for_request(self, text: str) -> list[dict]:
        import re

        from navin.agent.skills_graph import build_skills_graph

        graph = build_skills_graph(self._loader())

        def words_of(value: str, shortest: int = 4) -> set[str]:
            return {
                w for w in re.findall(rf"[^\W_]{{{shortest},}}", value.lower())
                if w not in _SUGGEST_STOP_WORDS
            }

        words = words_of(text, 3)
        picked = []
        for row in graph.suggest(text[:600], limit=8):
            if not row.get("available", True):
                continue
            # A word of the request in the skill's name, or two in its
            # description: one generic word in a long description is noise.
            name_hit = words & words_of(row["name"].replace("-", " "), 3)
            if name_hit or len((words & words_of(row["description"])) - {w for w in words if len(w) < 4}) >= 2:
                picked.append(row)
            if len(picked) == 3:
                break
        return picked

    async def execute(self, action: str = "find", query: str = "", name: str = "", **kwargs: Any) -> Any:
        # Scanning user libraries and parsing YAML must not freeze live
        # frames, approvals or other chats. to_thread preserves workspace
        # ContextVars, including when several projects search concurrently.
        return await asyncio.to_thread(self._execute, action, query, name)

    def _execute(self, action: str, query: str, name: str) -> Any:
        loader = self._loader()
        action = (action or "find").strip().lower()
        if action == "graph":
            from navin.agent.skills_graph import build_skills_graph

            graph = build_skills_graph(loader)
            if (name or "").strip():
                key = name.strip()
                if key not in graph.nodes:
                    return self.error(f"skill not found: {key}. Use action=list to see names.")
                edges = graph.related(key, limit=6)
                if not edges:
                    return f"No related skill for '{key}'."
                lines = [f"Skills related to **{key}**:"]
                for edge in edges:
                    node = graph.nodes.get(edge.dst)
                    status = "" if (node and node.available) else " (unavailable)"
                    lines.append(
                        f"- **{edge.dst}**{status} - {edge.kind} relation "
                        f"(weight {edge.weight}) - {node.description if node else ''}"
                    )
                lines.append(
                    "\nLoad one with `skill action=read name=<name>` before applying it."
                )
                return "\n".join(lines)
            if not (query or "").strip():
                return self.error("action=graph requires query=<task description> or name=<skill>")
            rows = graph.suggest(query, limit=6)
            if not rows:
                return (
                    f"No skill matches '{query}'. Use action=list to see every "
                    "name, or proceed without a skill."
                )
            lines = ["Best skills for this task (graph-ranked):"]
            for row in rows:
                status = ""
                if not row["available"]:
                    status = f" (unavailable: {row.get('missing') or 'missing dependencies'})"
                via = f" - related to {row['via']}" if row.get("via") else ""
                lines.append(f"- **{row['name']}**{status}{via} - {row['description']}")
            lines.append(
                "\nLoad one with `skill action=read name=<name>` before applying it."
            )
            return "\n".join(lines)
        if action == "find":
            if not (query or "").strip():
                return self.error("action=find requires query=<keywords>")
            rows = loader.search_skills(query, limit=10)
            if not rows:
                return (
                    f"No skill matches '{query}'. Use action=list to see every "
                    "name, or proceed without a skill."
                )
            lines = []
            for row in rows:
                status = ""
                if not row["available"]:
                    missing = row.get("missing") or "missing dependencies"
                    status = f" (unavailable: {missing})"
                lines.append(f"- **{row['name']}**{status} - {row['description']}")
            lines.append(
                "\nLoad one with `skill action=read name=<name>` before applying it."
            )
            return "\n".join(lines)
        if action == "read":
            if not (name or "").strip():
                return self.error("action=read requires name=<skill name>")
            key = name.strip()
            if key in loader.disabled_skills:
                return self.error(f"Skill '{key}' is disabled. Choose an enabled skill with action=find.")
            content = loader.load_skill(key)
            if content is None:
                folder = loader.skill_dir(key)
                if folder is not None:
                    return self.error(
                        f"Could not read skill '{key}' in {folder.as_posix()}. "
                        "Check the file's UTF-8 encoding and permissions, or choose another skill."
                    )
                rows = loader.search_skills(key, limit=5)
                if rows:
                    hints = ", ".join(row["name"] for row in rows)
                    return self.error(f"skill not found: {key}. Closest names: {hints}")
                return self.error(f"skill not found: {key}. Use action=list to see names.")
            available, why = loader.get_skill_availability(key)
            header = ""
            folder = loader.skill_dir(key)
            if folder is not None:
                # Skills bundle helper files (scripts/, data/, references/).
                # Without the real folder path the model cannot run them and
                # wastes turns hunting for it (imports, filesystem-wide find).
                # as_posix: forward slashes work in every shell (PowerShell
                # included) while backslashes get re-read as escapes.
                header += (
                    f"[Skill folder: {folder.as_posix()} - bundled files "
                    "like scripts/ and references/ live here; use this "
                    "absolute path to run or read them.]\n\n"
                )
            if not available and why:
                header += (
                    f"(Warning - missing dependencies: {why}. Install them "
                    "first or pick another approach.)\n\n"
                )
            # Origin stamp (audit M2): a workspace-shipped playbook and a
            # builtin do not carry the same trust.
            header += (
                f"(Origin: {skill_source_label(loader.skill_source(key))})\n\n"
            )
            return header + f"### Skill: {key}\n\n{loader._strip_frontmatter(content)}"
        if action == "list":
            index = loader.build_skills_index()
            return index or "No skills installed."
        return self.unknown_action(action)
