"""App-template tool: list / find / read / install marketplace products."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool


class AppTemplateTool(Tool):
    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        workspace = None
        try:
            workspace = Path(ctx.workspace)
        except Exception:
            workspace = None
        return cls(workspace=workspace)

    @property
    def name(self) -> str:
        return "app_template"

    @property
    def description(self) -> str:
        return (
            "Browse and install Navin app templates (CRM, finance, chat, ...). "
            "action=list shows the catalog, action=find searches by keywords, "
            "action=read loads one slug plus its install.json playbook "
            "(env, Docker, DB, launch, how to modify), action=install writes "
            ".navin/apps/<slug>/ then starts env, databases and the app, "
            "action=create scaffolds a new folder and starts everything. "
            "After install/create, call open_preview with the returned URL. "
            "Prefer S3 packages over local clones."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "find", "read", "install", "create"],
                    "description": "list catalog, find by keywords, read one slug, install and start, or create a folder and start",
                },
                "query": {
                    "type": "string",
                    "description": "Keywords for action=find, e.g. 'crm leads' or 'rag chat'",
                },
                "slug": {
                    "type": "string",
                    "description": "Template slug for action=read, install or create, e.g. crm",
                },
                "dest": {
                    "type": "string",
                    "description": "New project folder for action=create, e.g. ./mon-crm",
                },
            },
            "required": ["action"],
        }

    def _root(self) -> Path:
        from navin.security.workspace_access import current_tool_workspace

        root = self._workspace or Path.cwd()
        try:
            scoped = current_tool_workspace(root).project_path
            if scoped:
                root = Path(scoped)
        except Exception:
            pass
        return Path(root)

    async def execute(
        self,
        action: str = "list",
        query: str = "",
        slug: str = "",
        dest: str = "",
        **kwargs: Any,
    ) -> Any:
        from navin.templates.apps.catalog import get_app_template, list_app_templates
        from navin.templates.apps.install import (
            AppTemplateError,
            install_app,
            is_installed,
        )

        action = (action or "list").strip().lower()
        workspace = self._root()

        if action == "list":
            lines = []
            for row in list_app_templates():
                mark = "installed" if is_installed(workspace, row["slug"]) else "available"
                lines.append(
                    f"- **{row['slug']}** ({row['kind']}, {row['license']}, "
                    f"audit={row['audit_status']}, {mark}) - {row['description']}"
                )
            lines.append(
                "\nRead one with `app_template action=read slug=<slug>`. "
                "Install with `app_template action=install slug=<slug>`."
            )
            return "\n".join(lines)

        if action == "find":
            needle = (query or "").strip().lower()
            if not needle:
                return self.error("action=find requires query=<keywords>")
            tokens = [part for part in needle.replace(",", " ").split() if part]
            matches = []
            for row in list_app_templates():
                hay = " ".join(
                    [
                        row["slug"],
                        row["name"],
                        row["description"],
                        row["category"],
                        row["source_name"],
                        " ".join(row["domain_agents"]),
                    ]
                ).lower()
                if all(token in hay for token in tokens):
                    matches.append(row)
            if not matches:
                return (
                    f"No template matches '{query}'. Use action=list to see every slug."
                )
            lines = []
            for row in matches[:12]:
                lines.append(
                    f"- **{row['slug']}** - {row['name']}: {row['description']}"
                )
            lines.append("\nLoad one with `app_template action=read slug=<slug>`.")
            return "\n".join(lines)

        if action == "read":
            key = (slug or "").strip()
            if not key:
                return self.error("action=read requires slug=<template>")
            row = get_app_template(key)
            if row is None:
                return self.error(f"unknown template: {key}. Use action=list.")
            from navin.templates.apps.install import package_dir

            agents = ", ".join(row["domain_agents"])
            installed = "yes" if is_installed(workspace, row["slug"]) else "no"
            playbook_path = package_dir(row["slug"]) / "install.json"
            playbook = ""
            if playbook_path.is_file():
                playbook = playbook_path.read_text(encoding="utf-8")
            return (
                f"# {row['name']} (`{row['slug']}`)\n"
                f"{row['description']}\n\n"
                f"- kind: {row['kind']}\n"
                f"- license: {row['license']}\n"
                f"- source: {row['source_github']}\n"
                f"- audit: {row['audit_status']}\n"
                f"- installed: {installed}\n"
                f"- domain agents: {agents}\n\n"
                "Follow install.json (system, packages front/back, env, Docker, DB, tools) "
            "before any change. "
                "User overrides live in overlay.json.\n\n"
                f"## install.json\n```json\n{playbook or '{}'}\n```\n\n"
                "Install with `app_template action=install slug="
                f"{row['slug']}`."
            )

        if action == "install":
            key = (slug or "").strip()
            if not key:
                return self.error("action=install requires slug=<template>")
            try:
                result = install_app(key, workspace, start=True, wait=False)
            except AppTemplateError as exc:
                return self.error(exc.message)
            verb = "already installed" if result["already_installed"] else "installed"
            url = result.get("preview_url") or ""
            started = ", ".join(result.get("started") or []) or "none"
            return (
                f"{verb}: {result['slug']} -> {result['plugin_dir']}. "
                f"Started {started}. "
                + (f"Preview {url}. Call open_preview url={url}. " if url else "")
                + f"Audit {result['audit_status']} / {result['status']}."
            )

        if action == "create":
            key = (slug or "").strip()
            dest = str(dest or kwargs.get("dest") or "").strip()
            if not key:
                return self.error("action=create requires slug=<template>")
            if not dest:
                return self.error("action=create requires dest=./mon-app")
            from navin.templates.apps.install import create_app

            try:
                result = create_app(key, dest, start=True, wait=False)
            except AppTemplateError as exc:
                return self.error(exc.message)
            url = result.get("preview_url") or ""
            started = ", ".join(result.get("started") or []) or "none"
            return (
                f"created: {result['slug']} -> {result['dest']}. "
                f"Started {started}. "
                + (f"Preview {url}. Call open_preview url={url}. " if url else "")
                + "Do not ask the user to run npm or docker."
            )

        return self.unknown_action(action)
