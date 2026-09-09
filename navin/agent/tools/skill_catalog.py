"""Skill tool: on-demand access to the skill catalog.

The system prompt lists only skill *names* (the full catalog of descriptions
costs ~10K tokens per turn). This tool serves descriptions, paths and full
SKILL.md bodies when the model actually needs a playbook.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool


class SkillCatalogTool(Tool):
    def __init__(
        self,
        workspace: Path | None = None,
        disabled_skills: set[str] | None = None,
        builtin_skills_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._disabled = disabled_skills or set()
        self._builtin_skills_dir = builtin_skills_dir

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        disabled: set[str] = set()
        try:
            disabled = set(ctx.config.agents.defaults.disabled_skills or [])
        except Exception:
            disabled = set()
        return cls(workspace=Path(ctx.workspace), disabled_skills=disabled)

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
            "every name."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["find", "read", "list"],
                    "description": "find (keyword search), read (full SKILL.md), list (all names)",
                },
                "query": {
                    "type": "string",
                    "description": "Keywords for action=find, e.g. 'pdf report' or 'deploy docker'",
                },
                "name": {
                    "type": "string",
                    "description": "Exact skill name for action=read",
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
        )

    async def execute(self, action: str = "find", query: str = "", name: str = "", **kwargs: Any) -> Any:
        # Scanning user libraries and parsing YAML must not freeze live
        # frames, approvals or other chats. to_thread preserves workspace
        # ContextVars, including when several projects search concurrently.
        return await asyncio.to_thread(self._execute, action, query, name)

    def _execute(self, action: str, query: str, name: str) -> Any:
        loader = self._loader()
        action = (action or "find").strip().lower()
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
            return header + f"### Skill: {key}\n\n{loader._strip_frontmatter(content)}"
        if action == "list":
            index = loader.build_skills_index()
            return index or "No skills installed."
        return self.unknown_action(action)
