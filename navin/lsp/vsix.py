# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Install language servers that ship inside VS Code extensions.

A ``.vsix`` is a zip archive. An extension that implements a language feature
carries the language server inside it, and that server speaks LSP over stdio
exactly like the servers already listed in ``servers.json``: it uses no
``vscode.*`` API and needs no extension host. Installing one is therefore a
download plus a table entry, and the LSP layer itself is untouched.

What this cannot do is run the extension. Anything that draws UI - themes,
GitLens, Vim keybindings - lives entirely inside the editor's extension host
and has no server to extract. The catalogue in ``vsix_servers.json`` lists the
extensions whose server was actually verified to answer an ``initialize``
handshake outside VS Code.

Extensions are fetched from Open VSX, the vendor-neutral registry, not from the
Microsoft marketplace, whose terms of use restrict it to Microsoft's own
products.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_CATALOG_PATH = Path(__file__).with_name("vsix_servers.json")
_REGISTRY = "https://open-vsx.org"
_REGISTRY_HOSTS = ("open-vsx.org", ".open-vsx.org")

# A language server is a few megabytes; the largest in the catalogue is under
# 60 MB unpacked. The cap is what stops a redirect to something else from
# filling the disk, so it is generous rather than tight.
_MAX_VSIX_BYTES = 400 * 1024 * 1024
_MAX_UNPACKED_BYTES = 1024 * 1024 * 1024

# Marks the entries this module owns inside the user's lsp_servers.json, so
# uninstall never removes a server the user configured by hand.
MANAGED_KEY = "installed_from_vsix"

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class VsixError(RuntimeError):
    """An extension could not be installed.

    Carries the HTTP status the WebUI should report, so the reason a failure
    happened survives the trip to the browser instead of collapsing into a
    generic 500: a name typo and a registry outage need different words.
    """

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# -- catalogue ---------------------------------------------------------------


def host_target() -> str:
    """This machine, named the way Open VSX names extension builds.

    An extension whose server is a compiled program publishes one archive per
    platform, so the download has to ask for the right one instead of the
    default universal build, which carries no binary at all.
    """
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "win32":
        return "win32-arm64" if arm else "win32-x64"
    if sys.platform == "darwin":
        return "darwin-arm64" if arm else "darwin-x64"
    return "linux-arm64" if arm else "linux-x64"


def catalog() -> dict[str, dict[str, Any]]:
    """The curated table of extensions known to carry a usable server."""
    from navin.quality.linters import _read_json

    entries = _read_json(_CATALOG_PATH).get("extensions", {})
    return entries if isinstance(entries, dict) else {}


def install_root() -> Path:
    """Where extracted extensions live. Regenerable: safe to delete."""
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("lsp-extensions")


def _user_table_path() -> Path:
    from navin.config.loader import get_config_path

    return get_config_path().parent / "lsp_servers.json"


# -- registry ----------------------------------------------------------------


def _check_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise VsixError(
            f"'{slug}' is not a valid extension id, expected '<publisher>/<name>'",
            status=400,
        )


def _check_download_url(url: str) -> None:
    """Refuse a download the registry points somewhere unexpected.

    The URL comes from a registry response rather than from us, so it is
    treated as input: only https, and only Open VSX or its CDN.
    """
    parsed = httpx.URL(url)
    host = parsed.host.lower()
    if parsed.scheme != "https" or not (
        host in {h.lstrip(".") for h in _REGISTRY_HOSTS}
        or any(host.endswith(suffix) for suffix in _REGISTRY_HOSTS if suffix.startswith("."))
    ):
        raise VsixError(
            f"registry pointed at an unexpected download host: {host}", status=502
        )


def resolve(slug: str, version: str | None = None, target: str | None = None) -> tuple[str, str]:
    """Return the download URL and version for an extension on Open VSX."""
    _check_slug(slug)
    parts = [slug]
    if target:
        parts.append(target)
    parts.append(version or "latest")
    url = f"{_REGISTRY}/api/{'/'.join(parts)}"
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code == 404:
                if target:
                    raise VsixError(
                        f"'{slug}' publishes no build for {target}; this server ships as a "
                        "compiled program and upstream has none for this platform",
                        status=404,
                    )
                raise VsixError(f"'{slug}' is not published on Open VSX", status=404)
            response.raise_for_status()
            payload = response.json()
    except VsixError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise VsixError(f"could not reach Open VSX: {exc}", status=503) from exc

    download = str((payload.get("files") or {}).get("download") or "")
    if not download:
        raise VsixError(f"'{slug}' has no downloadable archive on Open VSX", status=404)
    _check_download_url(download)
    return download, str(payload.get("version") or version or "unknown")


def _download(url: str, destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    size = 0
    try:
        with httpx.stream("GET", url, timeout=httpx.Timeout(30.0, read=180.0), follow_redirects=True) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_VSIX_BYTES:
                        raise VsixError("extension download exceeded the size limit", status=502)
                    stream.write(chunk)
        os.replace(partial, destination)
    except (httpx.HTTPError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise VsixError(f"download failed: {exc}", status=503) from exc
    except VsixError:
        partial.unlink(missing_ok=True)
        raise


# -- extraction --------------------------------------------------------------


def _safe_members(archive: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    """Entries that are safe to write, rejecting the archive otherwise.

    A vsix is third-party content, so the archive is not trusted to stay inside
    the directory it is extracted into: an entry named ``../..`` or an absolute
    path would otherwise overwrite files anywhere the process can write.
    """
    root = destination.resolve()
    members: list[zipfile.ZipInfo] = []
    total = 0
    for member in archive.infolist():
        name = member.filename
        if name.endswith("/"):
            continue
        if os.path.isabs(name) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise VsixError(f"archive contains an unsafe path: {name}", status=502)
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise VsixError(f"archive contains an unsafe path: {name}", status=502)
        total += member.file_size
        if total > _MAX_UNPACKED_BYTES:
            raise VsixError("extension is too large once unpacked", status=502)
        members.append(member)
    return members


def _extract(vsix: Path, destination: Path) -> None:
    staging = Path(tempfile.mkdtemp(prefix="navin-vsix-", dir=str(destination.parent)))
    try:
        with zipfile.ZipFile(vsix) as archive:
            members = _safe_members(archive, staging)
            for member in members:
                archive.extract(member, staging)
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(staging, destination)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise VsixError("downloaded file is not a valid vsix archive", status=502) from exc
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _find_entrypoint(root: Path, expected: str) -> Path:
    """Locate the server inside the extracted extension.

    The catalogue records the path seen in the verified version, spelled the
    way the Unix build does. Windows adds ``.exe`` to a compiled server, and a
    later release can move the file, so both are tried before giving up rather
    than failing on a platform or a version bump.
    """
    for name in (expected, f"{expected}.exe"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    leaf = Path(expected).name
    matches = sorted(
        (*root.rglob(leaf), *root.rglob(f"{leaf}.exe")), key=lambda p: len(str(p))
    )
    if matches:
        logger.info("vsix entrypoint moved, using {} instead of {}", matches[0], expected)
        return matches[0]
    raise VsixError(
        f"could not find the language server at '{expected}' in this version; "
        "the extension may have restructured its build",
        status=502,
    )


# -- the navin side ----------------------------------------------------------


def _read_user_table() -> dict[str, Any]:
    from navin.quality.linters import _read_json

    return _read_json(_user_table_path())


def _write_user_table(table: dict[str, Any]) -> None:
    path = _user_table_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".json.part")
    partial.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _server_entry(
    spec: dict[str, Any],
    server: Path,
    *,
    runtime: str,
    slug: str,
    version: str,
) -> dict[str, Any]:
    """Build the servers.json entry that launches this extracted server.

    ``node`` is resolved from PATH like any other declared binary, and the
    server file is passed as its first argument. A native server is named by
    absolute path through ``project_paths``, which wins over the PATH lookup.
    """
    entry: dict[str, Any] = {
        "languages": list(spec.get("languages", [])),
        "extensions": list(spec.get("extensions", [])),
        "root_markers": list(spec.get("root_markers", [])),
        "language_ids": dict(spec.get("language_ids", {})),
        "priority": int(spec.get("priority", 20)),
        MANAGED_KEY: {"marketplace": slug, "version": version, "path": str(server)},
    }
    args = [str(a) for a in spec.get("args", [])]
    if runtime == "native":
        entry["binary"] = {"name": server.name, "project_paths": [str(server)]}
        entry["args"] = args
    else:
        entry["binary"] = {"name": "node"}
        entry["args"] = [str(server), *args]
    if spec.get("settings"):
        entry["settings"] = dict(spec["settings"])
    return entry


def _register(name: str, entry: dict[str, Any]) -> None:
    table = _read_user_table()
    servers = table.get("servers")
    if not isinstance(servers, dict):
        servers = {}
    existing = servers.get(name)
    if isinstance(existing, dict) and MANAGED_KEY not in existing:
        raise VsixError(
            f"'{name}' is already configured by hand in {_user_table_path()}; "
            "remove it there first or install under a different name",
            status=409,
        )
    servers[name] = entry
    table["servers"] = servers
    _write_user_table(table)


def installed() -> dict[str, dict[str, Any]]:
    """The servers this module installed, by table name."""
    servers = _read_user_table().get("servers")
    if not isinstance(servers, dict):
        return {}
    return {
        name: spec
        for name, spec in servers.items()
        if isinstance(spec, dict) and isinstance(spec.get(MANAGED_KEY), dict)
    }


def install(
    name: str,
    *,
    version: str | None = None,
    slug: str | None = None,
    entrypoint: str | None = None,
    runtime: str = "node",
    lsp: dict[str, Any] | None = None,
    platform_specific: bool = False,
) -> dict[str, Any]:
    """Install a language server from a VS Code extension.

    With only ``name``, the extension is taken from the curated catalogue. The
    remaining arguments describe an extension that is not catalogued, which is
    what makes this usable beyond the handful that were verified.
    """
    if not _NAME_RE.match(name):
        raise VsixError(f"'{name}' is not a valid server name", status=400)

    known = catalog().get(name)
    if known is None and (slug is None or entrypoint is None or lsp is None):
        raise VsixError(
            f"'{name}' is not in the catalogue; pass the extension id, its server "
            "path inside the archive, and the file types it handles",
            status=400,
        )
    if known is not None:
        slug = slug or str(known["marketplace"])
        entrypoint = entrypoint or str(known["entrypoint"])
        if not lsp:
            runtime = str(known.get("runtime", "node"))
            platform_specific = bool(known.get("platform_specific", False))
        lsp = lsp or dict(known["lsp"])

    assert slug is not None and entrypoint is not None and lsp is not None
    _check_slug(slug)

    if runtime == "node" and shutil.which("node") is None:
        raise VsixError("this server runs on node, which is not on PATH", status=409)

    url, resolved_version = resolve(
        slug, version, target=host_target() if platform_specific else None
    )
    destination = install_root() / name
    with tempfile.TemporaryDirectory(prefix="navin-vsix-dl-") as tmp:
        archive = Path(tmp) / f"{name}.vsix"
        logger.info("Downloading {} {} from Open VSX", slug, resolved_version)
        _download(url, archive)
        _extract(archive, destination)

    server = _find_entrypoint(destination, entrypoint)
    if runtime == "native":
        server.chmod(server.stat().st_mode | 0o111)

    entry = _server_entry(lsp, server, runtime=runtime, slug=slug, version=resolved_version)
    _register(name, entry)
    logger.info("Installed language server '{}' from {}", name, slug)
    return {
        "server": name,
        "marketplace": slug,
        "version": resolved_version,
        "path": str(server),
        "extensions": entry["extensions"],
    }


def _fetch(slug: str, destination: Path, version: str | None) -> str:
    """Download an extension and unpack it, preferring this machine's build.

    Extensions whose server is a compiled program publish one archive per
    platform and a universal one that carries no binary, so the per-platform
    build is asked for first and the universal one is the fallback rather than
    something the caller has to know to request.
    """
    try:
        url, resolved = resolve(slug, version, target=host_target())
    except VsixError as exc:
        if exc.status != 404:
            raise
        url, resolved = resolve(slug, version)

    with tempfile.TemporaryDirectory(prefix="navin-vsix-dl-") as tmp:
        archive = Path(tmp) / "extension.vsix"
        logger.info("Downloading {} {} from Open VSX", slug, resolved)
        _download(url, archive)
        _extract(archive, destination)
    return resolved


def install_detected(
    slug: str, *, name: str | None = None, version: str | None = None
) -> dict[str, Any]:
    """Install any extension, working out its server rather than being told.

    Unlike :func:`install`, nothing about the extension has to be known in
    advance: the file types come from its manifest and the server is found and
    then proved by an LSP handshake. An extension with no server to prove is
    refused and leaves nothing behind.
    """
    from navin.lsp.vsix_probe import detect

    _check_slug(slug)
    server_name = (name or slug.split("/", 1)[1]).strip()
    if not _NAME_RE.match(server_name):
        raise VsixError(f"'{server_name}' is not a valid server name", status=400)

    destination = install_root() / server_name
    existing = _read_user_table().get("servers")
    if isinstance(existing, dict):
        current = existing.get(server_name)
        if isinstance(current, dict) and MANAGED_KEY not in current:
            raise VsixError(
                f"'{server_name}' is already configured by hand in {_user_table_path()}; "
                "remove it there first or install under a different name",
                status=409,
            )

    resolved_version = _fetch(slug, destination, version)
    try:
        server, args, runtime, spec, _capabilities = detect(destination)
        entry = _server_entry(
            {**spec, "args": args}, server, runtime=runtime, slug=slug, version=resolved_version
        )
        _register(server_name, entry)
    except Exception:
        # Nothing was registered, so the unpacked copy is only wasted disk.
        shutil.rmtree(destination, ignore_errors=True)
        raise

    logger.info("Installed language server '{}' from {}", server_name, slug)
    return {
        "server": server_name,
        "marketplace": slug,
        "version": resolved_version,
        "path": str(server),
        "extensions": entry["extensions"],
    }


def uninstall(name: str) -> bool:
    """Remove an installed server. Returns False when it was not installed."""
    table = _read_user_table()
    servers = table.get("servers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    spec = servers[name]
    if not (isinstance(spec, dict) and isinstance(spec.get(MANAGED_KEY), dict)):
        raise VsixError(
            f"'{name}' was not installed from an extension, leaving it alone", status=409
        )
    servers.pop(name)
    table["servers"] = servers
    _write_user_table(table)
    shutil.rmtree(install_root() / name, ignore_errors=True)
    logger.info("Removed language server '{}'", name)
    return True
