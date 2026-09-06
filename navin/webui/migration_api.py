"""One-click migration from Cursor / VS Code into Navin.

What gets imported:

- **MCP servers** from ``~/.cursor/mcp.json`` (global) and
  ``<project>/.cursor/mcp.json`` (project): merged into Navin's
  ``tools.mcp_servers`` config without overwriting servers that already
  exist. Cursor's schema (command/args/env or url/headers) maps 1:1 onto
  :class:`navin.config.schema.MCPServerConfig`.
- **Copilot instructions** (``.github/copilot-instructions.md``): converted
  into a Navin project rule under ``.navin/rules/``.

Cursor *rules* need no import at all: Navin reads ``.cursor/rules/*.mdc``
and ``.cursorrules`` natively at runtime (see ``navin.agent.project_rules``),
so the scan simply reports them as already supported.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class MigrationError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def cursor_home() -> Path:
    override = os.environ.get("NAVIN_CURSOR_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cursor"


def _read_mcp_file(path: Path) -> dict[str, Any]:
    """Return the ``mcpServers`` mapping of a Cursor mcp.json, or ``{}``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    servers = data.get("mcpServers", data)
    return servers if isinstance(servers, dict) else {}


def _mcp_sources(project_root: Path | None) -> list[tuple[str, Path]]:
    sources = [("global", cursor_home() / "mcp.json")]
    if project_root is not None:
        sources.append(("project", Path(project_root) / ".cursor" / "mcp.json"))
    return sources


def scan_migration_sources(project_root: Path | str | None) -> dict[str, Any]:
    """What could be imported (or is already supported natively)."""
    root = Path(project_root).expanduser() if project_root else None

    mcp: list[dict[str, Any]] = []
    for origin, path in _mcp_sources(root):
        servers = _read_mcp_file(path) if path.is_file() else {}
        if servers:
            mcp.append(
                {
                    "origin": origin,
                    "path": str(path),
                    "servers": sorted(servers.keys()),
                }
            )

    native_rules: list[str] = []
    copilot: str | None = None
    vscode_settings: str | None = None
    if root is not None and root.is_dir():
        if (root / ".cursorrules").is_file():
            native_rules.append(".cursorrules")
        rules_dir = root / ".cursor" / "rules"
        if rules_dir.is_dir():
            native_rules.extend(
                sorted(
                    f".cursor/rules/{p.name}"
                    for p in rules_dir.glob("*.mdc")
                    if p.is_file()
                )
            )
        copilot_path = root / ".github" / "copilot-instructions.md"
        if copilot_path.is_file():
            copilot = str(copilot_path)
        settings_path = root / ".vscode" / "settings.json"
        if settings_path.is_file():
            vscode_settings = str(settings_path)

    return {
        "mcp": mcp,
        "nativeRules": native_rules,
        "copilotInstructions": copilot,
        "vscodeSettings": vscode_settings,
    }


def import_mcp_servers(
    project_root: Path | str | None,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Merge Cursor MCP servers into Navin's config (existing names win)."""
    from navin.config.loader import load_config, save_config
    from navin.config.schema import MCPServerConfig

    root = Path(project_root).expanduser() if project_root else None
    config = load_config(config_path)
    existing = dict(config.tools.mcp_servers or {})

    imported: list[str] = []
    skipped: list[str] = []
    invalid: list[str] = []
    for _origin, path in _mcp_sources(root):
        if not path.is_file():
            continue
        for name, server_conf in _read_mcp_file(path).items():
            clean = str(name).strip()
            if not clean:
                continue
            if clean in existing:
                if clean not in skipped:
                    skipped.append(clean)
                continue
            if not isinstance(server_conf, dict):
                invalid.append(clean)
                continue
            known = {
                k: v
                for k, v in server_conf.items()
                if k in MCPServerConfig.model_fields
            }
            try:
                existing[clean] = MCPServerConfig.model_validate(known)
            except Exception:
                invalid.append(clean)
                continue
            imported.append(clean)

    if imported:
        config.tools.mcp_servers = existing
        save_config(config, config_path)
    return {"imported": imported, "skipped": skipped, "invalid": invalid}


def import_copilot_instructions(project_root: Path | str) -> dict[str, Any]:
    """Convert ``.github/copilot-instructions.md`` into a Navin project rule."""
    from navin.agent.project_rules import write_navin_rule

    root = Path(project_root).expanduser()
    source = root / ".github" / "copilot-instructions.md"
    if not source.is_file():
        return {"imported": False, "reason": "not found"}
    try:
        body = source.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        raise MigrationError(f"cannot read {source}: {exc}", status=500) from exc
    if not body:
        return {"imported": False, "reason": "empty"}
    target = root / ".navin" / "rules" / "copilot-instructions.md"
    if target.is_file():
        return {"imported": False, "reason": "already imported"}
    write_navin_rule(root, name="copilot-instructions", content=body)
    return {"imported": True, "rule": ".navin/rules/copilot-instructions.md"}


def run_import(
    project_root: Path | str | None,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Import everything importable; returns one combined report."""
    report: dict[str, Any] = {
        "mcp": import_mcp_servers(project_root, config_path=config_path),
    }
    if project_root:
        report["copilot"] = import_copilot_instructions(project_root)
    else:
        report["copilot"] = {"imported": False, "reason": "no project"}
    return report
