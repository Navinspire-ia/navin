# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Locate the motion runtime assets (three.js, GSAP, Navin runtime).

The libraries are vendored next to this module in ``assets/`` and ship with
the wheel (``pyproject.toml`` includes ``navin/montage/motion/**``) and the
PyInstaller bundle (``collect_data_files("navin")``). Rendering never touches
a CDN: the page is served from loopback and every other request is aborted.

A stripped install (a source checkout where someone removed the minified
libraries, or a third-party repackaging) recovers by fetching the exact npm
tarballs listed in ``assets/manifest.json`` once into
``~/.navin/montage/motion-assets/``; each file's sha256 is checked before it
is used and the cache is reused afterwards.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from navin.montage import MONTAGE_HOME

PACKAGE_ASSETS = Path(__file__).resolve().parent / "assets"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
CACHE_ASSETS = MONTAGE_HOME / "motion-assets"

_FETCH_TIMEOUT_S = 60


class MotionAssetsError(RuntimeError):
    """The runtime libraries are missing and could not be fetched."""


def manifest() -> dict[str, Any]:
    return json.loads((PACKAGE_ASSETS / "manifest.json").read_text(encoding="utf-8"))


def _required_files() -> list[tuple[str, str | None]]:
    rows: list[tuple[str, str | None]] = []
    for package in manifest()["packages"].values():
        for name, meta in package["files"].items():
            rows.append((name, meta.get("sha256")))
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_files(directory: Path) -> list[str]:
    """Vendored library files absent from *directory* (hash mismatches count)."""
    gone: list[str] = []
    for name, expected in _required_files():
        path = directory / name
        if not path.is_file():
            gone.append(name)
        elif expected and _sha256(path) != expected:
            gone.append(name)
    return gone


def library_versions() -> dict[str, str]:
    return {name: meta["version"] for name, meta in manifest()["packages"].items()}


def _download(url: str) -> bytes:
    if not url.startswith("https://registry.npmjs.org/"):
        raise MotionAssetsError(f"refusing to fetch motion assets from {url}")
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as response:  # noqa: S310
        return response.read()


def fetch_into_cache(target: Path = CACHE_ASSETS) -> Path:
    """Build a complete asset directory in the user cache (network, once)."""
    target.mkdir(parents=True, exist_ok=True)
    # Navin's own runtime files (runtime JS/CSS, fonts) always come from the package.
    shutil.copytree(PACKAGE_ASSETS, target, dirs_exist_ok=True)
    for package in manifest()["packages"].values():
        wanted = {
            meta["from"]: (name, meta.get("sha256"))
            for name, meta in package["files"].items()
            if not (target / name).is_file()
            or (meta.get("sha256") and _sha256(target / name) != meta["sha256"])
        }
        if not wanted:
            continue
        try:
            blob = _download(package["tarball"])
        except OSError as exc:
            raise MotionAssetsError(
                "The motion libraries are not installed and could not be downloaded. "
                "Reinstall Navin or retry when online."
            ) from exc
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.name not in wanted or not member.isfile():
                    continue
                name, expected = wanted.pop(member.name)
                handle = archive.extractfile(member)
                data = handle.read() if handle is not None else b""
                if expected and hashlib.sha256(data).hexdigest() != expected:
                    raise MotionAssetsError(f"checksum mismatch for {name}; not using it")
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        if wanted:
            raise MotionAssetsError(
                "motion library archive is missing: " + ", ".join(sorted(wanted))
            )
    return target


def assets_dir(*, allow_fetch: bool = True) -> Path:
    """Directory holding a complete runtime: package first, then the cache."""
    if not missing_files(PACKAGE_ASSETS):
        return PACKAGE_ASSETS
    if CACHE_ASSETS.is_dir() and not missing_files(CACHE_ASSETS):
        # Refresh Navin's own runtime files in case the package was upgraded.
        shutil.copytree(PACKAGE_ASSETS, CACHE_ASSETS, dirs_exist_ok=True)
        return CACHE_ASSETS
    if not allow_fetch:
        raise MotionAssetsError(
            "motion libraries missing: " + ", ".join(missing_files(PACKAGE_ASSETS))
        )
    return fetch_into_cache()


__all__ = [
    "CACHE_ASSETS",
    "PACKAGE_ASSETS",
    "TEMPLATES_DIR",
    "MotionAssetsError",
    "assets_dir",
    "fetch_into_cache",
    "library_versions",
    "manifest",
    "missing_files",
]
