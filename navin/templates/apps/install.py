# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Create / install app templates locally. AWS publish is fail-closed until audit."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from navin import workspace_layout
from navin.templates.apps.catalog import get_app_template, is_publishable, list_app_templates
from navin.templates.apps.schema import (
    AWS_TEMPLATE_PREFIX,
    NAVIN_JSON_SCHEMA,
    OVERLAY_SCHEMA,
    AppTemplate,
)

_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".turbo",
    ".cache",
    ".DS_Store",
    "signing_keys.json",
    ".env",
)


class AppTemplateError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def local_cache_dir(slug: str) -> Path:
    return repo_root() / "templates_apps" / slug


def package_dir(slug: str) -> Path:
    """Versioned Navin overlay + patches for a template (audit / normalize)."""
    return Path(__file__).resolve().parent / "packages" / slug


def aws_object_key(slug: str) -> str:
    return f"{AWS_TEMPLATE_PREFIX}/{slug}.tar.gz"


def list_installed_slugs(workspace: Path | str) -> list[str]:
    root = workspace_layout.apps_dir(workspace)
    if not root.is_dir():
        return []
    slugs: list[str] = []
    for path in sorted(root.iterdir()):
        if path.is_dir() and (path / "navin.json").is_file():
            slugs.append(path.name)
    return slugs


def is_installed(workspace: Path | str, slug: str) -> bool:
    return (workspace_layout.workspace_app_dir(workspace, slug) / "navin.json").is_file()


def default_navin_json(row: AppTemplate) -> dict[str, Any]:
    agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for agent_id in [*row["universal_agents"], *row["domain_agents"]]:
        if agent_id in seen:
            continue
        seen.add(agent_id)
        agents.append(
            {
                "id": agent_id,
                "name": agent_id.replace("-", " ").title(),
                "description": f"{row['name']} agent: {agent_id}",
                "enabled": True,
                "tools": [],
            }
        )
    return {
        "schema": NAVIN_JSON_SCHEMA,
        "slug": row["slug"],
        "name": row["name"],
        "version": "0.1.0-local",
        "license": row["license"],
        "source_github": row["source_github"],
        "stack": list(row["stack"]),
        "launch": "",
        "permissions": [],
        "rag_collections": [],
        "docker": False,
        "seed": False,
        "status": row["status"],
        "audit_status": row["audit_status"],
        "agents": agents,
    }


def default_overlay(slug: str) -> dict[str, Any]:
    return {
        "schema": OVERLAY_SCHEMA,
        "slug": slug,
        "disabled_agents": [],
        "extra_agents": [],
        "agent_overrides": {},
        "env": {},
        "notes": "",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _write_agent_stubs(app_dir: Path, row: AppTemplate) -> None:
    seen: set[str] = set()
    for agent_id in [*row["universal_agents"], *row["domain_agents"]]:
        if agent_id in seen:
            continue
        seen.add(agent_id)
        folder = app_dir / "agents" / agent_id
        folder.mkdir(parents=True, exist_ok=True)
        yaml_path = folder / "agent.yaml"
        system_path = folder / "system.md"
        if not yaml_path.exists():
            yaml_path.write_text(
                (
                    f"id: {agent_id}\n"
                    f"name: {agent_id.replace('-', ' ').title()}\n"
                    "enabled: true\n"
                    "tools: []\n"
                ),
                encoding="utf-8",
            )
        if not system_path.exists():
            system_path.write_text(
                (
                    f"# {agent_id}\n\n"
                    f"You are the {agent_id} for {row['name']}.\n"
                    "Call real product tools. Do not invent CRM or finance data.\n"
                    "The user overlay in overlay.json wins over this file.\n"
                ),
                encoding="utf-8",
            )


def apply_package_patches(dest: Path, slug: str) -> bool:
    """Copy audited patches from ``packages/<slug>/patches`` onto the project."""
    patches = package_dir(slug) / "patches"
    if not patches.is_dir():
        return False
    applied = False
    for src in patches.rglob("*"):
        if not src.is_file():
            continue
        target = dest / src.relative_to(patches)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        applied = True
    return applied


def write_plugin_overlay(workspace: Path, row: AppTemplate) -> Path:
    app_dir = workspace_layout.workspace_app_dir(workspace, row["slug"])
    app_dir.mkdir(parents=True, exist_ok=True)
    pkg = package_dir(row["slug"])
    navin_json_path = app_dir / "navin.json"
    overlay_path = app_dir / "overlay.json"
    packaged_navin = pkg / "navin.json"
    packaged_overlay = pkg / "overlay.json"
    packaged_agents = pkg / "agents"
    if packaged_navin.is_file():
        shutil.copy2(packaged_navin, navin_json_path)
    elif not navin_json_path.exists():
        _write_json(navin_json_path, default_navin_json(row))
    if packaged_overlay.is_file() and not overlay_path.exists():
        shutil.copy2(packaged_overlay, overlay_path)
    elif not overlay_path.exists():
        _write_json(overlay_path, default_overlay(row["slug"]))
    if packaged_agents.is_dir():
        if (app_dir / "agents").exists():
            shutil.rmtree(app_dir / "agents")
        shutil.copytree(packaged_agents, app_dir / "agents")
    else:
        _write_agent_stubs(app_dir, row)
    notices = pkg / "THIRD_PARTY_NOTICES.md"
    if notices.is_file():
        shutil.copy2(notices, app_dir / "THIRD_PARTY_NOTICES.md")
    install_playbook = pkg / "install.json"
    if install_playbook.is_file():
        shutil.copy2(install_playbook, app_dir / "install.json")
    env_example = pkg / "env.example"
    if env_example.is_file() and not (app_dir / "env.example").exists():
        shutil.copy2(env_example, app_dir / "env.example")
    return app_dir


def _require_row(slug: str) -> AppTemplate:
    row = get_app_template(slug)
    if row is None:
        raise AppTemplateError(f"unknown template: {slug}", status=404)
    return row


def _dest_is_empty(dest: Path) -> bool:
    if not dest.exists():
        return True
    if not dest.is_dir():
        return False
    return not any(dest.iterdir())


def create_app(
    slug: str,
    dest: Path | str,
    *,
    start: bool = True,
    wait: bool | None = None,
) -> dict[str, Any]:
    """Scaffold a new project from the S3 package, then local cache if needed."""
    from navin.templates.apps.s3pack import (
        download_template_tarball,
        extract_template_tarball,
        template_public_url,
    )

    row = _require_row(slug)
    target = Path(dest).expanduser().resolve()
    if not _dest_is_empty(target):
        raise AppTemplateError(
            f"destination is not empty: {target}. Pick a new folder.",
            status=409,
        )
    target.mkdir(parents=True, exist_ok=True)

    cache = local_cache_dir(row["slug"])
    copied_source = False
    source = "overlay"
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"{row['slug']}.tar.gz"
        if download_template_tarball(row["slug"], archive):
            extract_template_tarball(archive, target)
            copied_source = True
            source = "aws"
        elif cache.is_dir():
            shutil.copytree(
                cache,
                target,
                dirs_exist_ok=True,
                ignore=_COPY_IGNORE,
                symlinks=True,
                ignore_dangling_symlinks=True,
            )
            copied_source = True
            source = "local"

    apply_package_patches(target, row["slug"])
    write_plugin_overlay(target, row)
    readme = target / "NAVIN.md"
    if not readme.exists():
        readme.write_text(_project_readme(row, copied_source, source=source), encoding="utf-8")

    from navin.templates.apps.bootstrap import bootstrap_and_start

    boot = bootstrap_and_start(
        target,
        row["slug"],
        start=start,
        wait=start if wait is None else wait,
        allow_env_without_source=True,
    )
    return {
        "ok": True,
        "action": "create",
        "slug": row["slug"],
        "dest": str(target),
        "copied_source": copied_source,
        "source": source,
        "aws_url": template_public_url(row["slug"]),
        "local_cache": cache.is_dir(),
        "audit_status": row["audit_status"],
        "status": row["status"],
        "can_publish": is_publishable(row),
        "plugin_dir": str(workspace_layout.workspace_app_dir(target, row["slug"])),
        "bootstrap": boot,
        "preview_url": boot.get("preview_url"),
        "started": boot.get("started") or [],
    }


def install_app(
    slug: str,
    workspace: Path | str,
    *,
    start: bool = True,
    wait: bool | None = None,
) -> dict[str, Any]:
    """Install the plugin overlay into an existing workspace (Use button)."""
    row = _require_row(slug)
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    already = is_installed(root, row["slug"])
    plugin_dir = write_plugin_overlay(root, row)
    from navin.templates.apps.bootstrap import bootstrap_and_start

    boot = bootstrap_and_start(
        root,
        row["slug"],
        start=start,
        wait=start if wait is None else wait,
        allow_env_without_source=False,
    )
    return {
        "ok": True,
        "action": "install",
        "slug": row["slug"],
        "workspace": str(root),
        "already_installed": already,
        "plugin_dir": str(plugin_dir),
        "audit_status": row["audit_status"],
        "status": row["status"],
        "can_publish": is_publishable(row),
        "copied_source": False,
        "bootstrap": boot,
        "preview_url": boot.get("preview_url"),
        "started": boot.get("started") or [],
    }


def assert_publishable(slug: str) -> AppTemplate:
    row = _require_row(slug)
    if not is_publishable(row):
        raise AppTemplateError(
            (
                f"Refuse AWS upload for {row['slug']}: audit_status="
                f"{row['audit_status']}, status={row['status']}. "
                "Audit, restyle and test one template at a time. "
                f"Only passed + normalized packages may go to {aws_object_key(row['slug'])}."
            ),
            status=403,
        )
    return row


def publish_to_aws(slug: str) -> dict[str, Any]:
    """Fail-closed. No raw clone is uploaded."""
    row = assert_publishable(slug)
    raise AppTemplateError(
        (
            f"{row['slug']} is marked publishable, but the S3 packager is not "
            f"wired yet. Target key: {aws_object_key(row['slug'])}."
        ),
        status=501,
    )


def catalog_payload(workspace: Path | str | None = None) -> dict[str, Any]:
    from navin.templates.apps.s3pack import (
        catalog_public_url,
        fetch_remote_catalog,
        templates_base_url,
    )

    installed = set(list_installed_slugs(workspace)) if workspace else set()
    remote = fetch_remote_catalog()
    if remote and isinstance(remote.get("templates"), list):
        templates = []
        for item in remote["templates"]:
            if not isinstance(item, dict) or not item.get("slug"):
                continue
            item = dict(item)
            item["installed"] = item["slug"] in installed
            item["local_cache"] = False
            item.setdefault("aws_key", aws_object_key(str(item["slug"])))
            item.setdefault("aws_url", f"{templates_base_url()}/{item['aws_key']}")
            templates.append(item)
        if templates:
            return {
                "templates": templates,
                "installed": sorted(installed),
                "aws_prefix": AWS_TEMPLATE_PREFIX,
                "aws_base_url": templates_base_url(),
                "aws_catalog_url": catalog_public_url(),
                "source": "aws",
                "publish_policy": "S3 templates/v1 - make aws-upload-temp overwrites.",
            }
    templates = [template_payload(row, installed=row["slug"] in installed) for row in list_app_templates()]
    return {
        "templates": templates,
        "installed": sorted(installed),
        "aws_prefix": AWS_TEMPLATE_PREFIX,
        "aws_base_url": templates_base_url(),
        "aws_catalog_url": catalog_public_url(),
        "source": "builtin",
        "publish_policy": "S3 catalog missing - using built-in catalog. Run make aws-upload-temp.",
    }


def template_payload(row: AppTemplate, *, installed: bool = False) -> dict[str, Any]:
    from navin.templates.apps.s3pack import template_public_url

    cache = local_cache_dir(row["slug"])
    return {
        **row,
        "installed": installed,
        "local_cache": cache.is_dir(),
        "can_publish": is_publishable(row),
        "aws_key": aws_object_key(row["slug"]),
        "aws_url": template_public_url(row["slug"]),
    }


def _project_readme(row: AppTemplate, copied_source: bool, *, source: str = "overlay") -> str:
    if source == "aws":
        source_line = f"Package downloaded from S3 {aws_object_key(row['slug'])}."
    elif copied_source:
        source_line = f"Local cache copied from templates_apps/{row['slug']}."
    else:
        source_line = (
            f"Source was not copied. Upstream: {row['source_github']}. "
            "Run make aws-upload-temp so Create can pull from S3."
        )
    return (
        f"# {row['name']}\n\n"
        f"{row['description']}\n\n"
        f"- License: {row['license']}\n"
        f"- Upstream: {row['source_github']} ({row['source_name']})\n"
        f"- Audit: {row['audit_status']}\n"
        f"- Package status: {row['status']}\n\n"
        f"{source_line}\n\n"
        "Customize agents in `.navin/apps/"
        f"{row['slug']}/overlay.json`. Overlay wins over `navin.json`.\n"
    )
