"""Resolve and copy the bundled Navin demo workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

_PACKAGE_DEMO = Path(__file__).resolve().parent.parent / "templates" / "demo"


def demo_root() -> Path:
    """Path to the packaged demo templates (read-only source)."""
    return _PACKAGE_DEMO


def _default_demo_dest() -> Path:
    return Path.home() / ".navin" / "demo-workspace"


def list_demo_files() -> list[str]:
    root = demo_root()
    if not root.is_dir():
        return []
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in root.rglob("*")
        if path.is_file()
    )


def ensure_demo_workspace(dest: Path | None = None) -> Path:
    """Copy the demo pack into a writable workspace (idempotent).

    Default destination: ``~/.navin/demo-workspace``.
    """
    target = dest or _default_demo_dest()
    target.mkdir(parents=True, exist_ok=True)
    source = demo_root()
    if not source.is_dir():
        raise FileNotFoundError(f"Demo pack missing at {source}")
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        out = target / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
            shutil.copy2(path, out)
    return target
