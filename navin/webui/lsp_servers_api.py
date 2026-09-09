# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Language servers the editor can install, for the Extensions panel.

The WebUI view of :mod:`navin.lsp.vsix`: what can be installed, what already
is, and whether it actually works in this project. The catalogue and the
installed table answer different questions, and the panel needs both at once -
an entry can be installed and still not run, typically because the machine has
no node - so they are merged here rather than in the browser.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from navin.lsp import vsix


class LspServersApiError(Exception):
    """A request the Extensions panel made cannot be satisfied."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _entry(
    name: str,
    spec: dict[str, Any],
    installed: dict[str, dict[str, Any]],
    ready: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lsp = spec.get("lsp") or {}
    mark = (installed.get(name) or {}).get(vsix.MANAGED_KEY) or {}
    status = ready.get(name) or {}
    return {
        "name": name,
        "displayName": str(spec.get("display_name") or name),
        "marketplace": str(spec.get("marketplace") or ""),
        "suffixes": [str(s) for s in lsp.get("extensions", [])],
        "languages": [str(s) for s in lsp.get("languages", [])],
        "notes": str(spec.get("notes") or ""),
        "runtime": str(spec.get("runtime") or "node"),
        "installed": name in installed,
        "version": str(mark.get("version") or ""),
        # Installed is not the same as usable: the entry can be written while
        # the interpreter it needs is missing, and the panel has to say so.
        "ready": bool(status.get("available")),
        "detail": str(status.get("reason") or ""),
    }


def lsp_servers_payload(root: Path | None = None) -> dict[str, Any]:
    """Everything the Extensions panel shows in one round trip."""
    from navin.lsp.manager import available_servers

    catalog = vsix.catalog()
    installed = vsix.installed()
    ready: dict[str, dict[str, Any]] = {}
    if root is not None:
        ready = {row["server"]: row for row in available_servers(root)}

    extensions = [_entry(name, spec, installed, ready) for name, spec in sorted(catalog.items())]
    # Anything installed by hand through the CLI still belongs in the list, or
    # the panel would offer to install something it already has.
    others = [
        _entry(
            name,
            {
                "display_name": name,
                "marketplace": (spec.get(vsix.MANAGED_KEY) or {}).get("marketplace", ""),
                "lsp": spec,
            },
            installed,
            ready,
        )
        for name, spec in sorted(installed.items())
        if name not in catalog
    ]
    return {
        "extensions": extensions,
        "others": others,
        "nodeAvailable": shutil.which("node") is not None,
        "target": vsix.host_target(),
    }


def search_lsp_servers(*, query: str, limit: int = 20) -> dict[str, Any]:
    """Search Open VSX, flagging what is already installed.

    The registry knows nothing about this machine, so the answer is joined with
    the installed table here: the panel needs to show "installed" rather than
    offering to install the same extension twice.
    """
    from navin.lsp.vsix_probe import search

    query = query.strip()
    if not query:
        return {"results": [], "query": ""}
    try:
        found = search(query, size=limit)
    except vsix.VsixError as exc:
        raise LspServersApiError(exc.message, status=exc.status) from exc

    installed = vsix.installed()
    slugs = {
        str((spec.get(vsix.MANAGED_KEY) or {}).get("marketplace") or ""): name
        for name, spec in installed.items()
    }
    catalog_slugs = {str(spec.get("marketplace") or ""): name for name, spec in vsix.catalog().items()}
    for entry in found:
        installed_as = slugs.get(entry["slug"], "")
        entry["installedAs"] = installed_as
        entry["installed"] = bool(installed_as)
        entry["catalogued"] = entry["slug"] in catalog_slugs
    return {"results": found, "query": query}


def install_detected_lsp_server(
    *, extension: str, root: Path | None = None, version: str = "", name: str = ""
) -> dict[str, Any]:
    """Install any extension from the registry, working out its server.

    Nothing has to be known about the extension beforehand, and an extension
    with no server that runs outside VS Code is refused rather than registered
    as broken.
    """
    extension = extension.strip()
    if not extension:
        raise LspServersApiError("an extension id is required")
    try:
        result = vsix.install_detected(
            extension, name=name.strip() or None, version=version.strip() or None
        )
    except vsix.VsixError as exc:
        raise LspServersApiError(exc.message, status=exc.status) from exc
    return {"ok": True, "installed": result, **lsp_servers_payload(root)}


def _suffix_list(raw: str) -> list[str]:
    parsed = [s.strip() for s in raw.split(",") if s.strip()]
    if not parsed:
        raise LspServersApiError("at least one file suffix is required")
    bad = [s for s in parsed if not s.startswith(".")]
    if bad:
        raise LspServersApiError(f"file suffixes must start with a dot: {', '.join(bad)}")
    return parsed


def install_lsp_server(
    *,
    name: str,
    root: Path | None = None,
    version: str = "",
    extension: str = "",
    entrypoint: str = "",
    suffixes: str = "",
    language_id: str = "",
    native: bool = False,
    platform_specific: bool = False,
) -> dict[str, Any]:
    """Install one language server, from the catalogue or from any extension."""
    name = name.strip()
    if not name:
        raise LspServersApiError("a server name is required")

    custom: dict[str, Any] | None = None
    if extension or entrypoint or suffixes:
        missing = [
            label
            for label, value in (
                ("the extension id", extension),
                ("the server path inside it", entrypoint),
                ("the file types it handles", suffixes),
            )
            if not value
        ]
        if missing:
            raise LspServersApiError(
                "installing an extension that is not in the catalogue needs "
                + ", ".join(missing)
            )
        parsed = _suffix_list(suffixes)
        language = language_id.strip() or parsed[0].lstrip(".")
        custom = {
            "languages": [language],
            "extensions": parsed,
            "args": [] if native else ["--stdio"],
            "root_markers": ["package.json", ".git"],
            "language_ids": dict.fromkeys(parsed, language),
            "priority": 20,
        }

    try:
        result = vsix.install(
            name,
            version=version.strip() or None,
            slug=extension.strip() or None,
            entrypoint=entrypoint.strip() or None,
            runtime="native" if native else "node",
            lsp=custom,
            platform_specific=platform_specific,
        )
    except vsix.VsixError as exc:
        raise LspServersApiError(exc.message, status=exc.status) from exc

    return {
        "ok": True,
        "installed": result,
        **lsp_servers_payload(root),
    }


def uninstall_lsp_server(*, name: str, root: Path | None = None) -> dict[str, Any]:
    """Remove an installed language server."""
    name = name.strip()
    if not name:
        raise LspServersApiError("a server name is required")
    try:
        removed = vsix.uninstall(name)
    except vsix.VsixError as exc:
        raise LspServersApiError(exc.message, status=exc.status) from exc
    if not removed:
        raise LspServersApiError(f"'{name}' is not installed", status=404)

    from navin.lsp.manager import LspManager

    LspManager.drop_server(name)
    return {"ok": True, **lsp_servers_payload(root)}
