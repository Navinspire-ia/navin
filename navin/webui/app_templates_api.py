"""WebUI API for the app-template gallery (list + Use / create)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.templates.apps.catalog import get_app_template
from navin.templates.apps.install import (
    AppTemplateError,
    catalog_payload,
    create_app,
    install_app,
    is_installed,
    template_payload,
)


def list_app_templates_payload(workspace: Path) -> dict[str, Any]:
    return catalog_payload(workspace)


def app_template_detail_payload(workspace: Path, slug: str) -> dict[str, Any]:
    row = get_app_template(slug)
    if row is None:
        raise AppTemplateError(f"unknown template: {slug}", status=404)
    return template_payload(row, installed=is_installed(workspace, row["slug"]))


def install_app_template_payload(workspace: Path, slug: str) -> dict[str, Any]:
    return install_app(slug, workspace, start=True, wait=False)


def create_app_template_payload(slug: str, dest: str, *, workspace: Path) -> dict[str, Any]:
    dest_raw = (dest or "").strip()
    if not dest_raw:
        raise AppTemplateError("dest is required for create")
    path = Path(dest_raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return create_app(slug, path, start=True, wait=False)
