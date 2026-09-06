"""Register or copy project captures into marketing/montage/captures/."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def register_captures(
    root: Path,
    paths: list[str],
    *,
    copy: bool = True,
) -> dict[str, Any]:
    """Copy (or symlink-list) local image paths into the montage captures folder."""
    root = root.resolve()
    out_dir = root / WORKSPACE_MONTAGE_DIR / "captures"
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    errors: list[str] = []

    for raw in paths:
        raw = (raw or "").strip()
        if not raw:
            continue
        src = Path(raw).expanduser()
        if not src.is_absolute():
            src = (root / src).resolve()
        else:
            src = src.resolve()
        try:
            src.relative_to(root)
        except ValueError:
            errors.append(f"outside workspace: {raw}")
            continue
        if not src.is_file():
            errors.append(f"not a file: {raw}")
            continue
        if src.suffix.lower() not in _IMAGE_EXTS:
            errors.append(f"unsupported type: {raw}")
            continue
        dest = out_dir / src.name
        if dest.exists():
            stem, suffix = src.stem, src.suffix
            n = 2
            while dest.exists():
                dest = out_dir / f"{stem}-{n}{suffix}"
                n += 1
        try:
            if copy:
                shutil.copy2(src, dest)
            else:
                dest.write_bytes(src.read_bytes())
            saved.append(str(dest.relative_to(root)).replace("\\", "/"))
        except OSError as exc:
            errors.append(f"{raw}: {exc}")

    return {
        "ok": bool(saved) and not errors,
        "saved": saved,
        "errors": errors,
        "captures_dir": str((out_dir).relative_to(root)).replace("\\", "/"),
    }


def render_screenshot_result(result: dict[str, Any]) -> str:
    lines = [
        "Montage captures:",
        f"  dir: {result.get('captures_dir')}",
        f"  saved: {len(result.get('saved') or [])}",
    ]
    for path in result.get("saved") or []:
        lines.append(f"  - {path}")
    for err in result.get("errors") or []:
        lines.append(f"  error: {err}")
    if not result.get("saved"):
        lines.append(
            "  Fix: pass path= or paths= to existing workspace images, "
            "or capture UI via browser tools then register here."
        )
    return "\n".join(lines)
