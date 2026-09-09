# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Load directory-scoped agent instructions along a requested project path."""

from __future__ import annotations

from pathlib import Path

_MAX_INSTRUCTION_BYTES = 256 * 1024
_MAX_CONTEXT_CHARS = 12_000


def scoped_project_instructions(workspace: Path | None, target: Path, *, directory: bool = False) -> str:
    """Read only the target's ancestors, without a recursive repository scan.

    Root instructions are already in ContextBuilder. Nested AGENTS.md files
    apply to their own subtree; CLAUDE.md is a fallback at each level.
    Symlinks outside the active workspace cannot expand read permissions.
    """
    if workspace is None:
        return ""
    try:
        root = workspace.expanduser().resolve()
        parent = (target if directory else target.parent).resolve()
        relative = parent.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    blocks: list[str] = []
    remaining = _MAX_CONTEXT_CHARS
    current = root
    for part in relative.parts:
        current /= part
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = current / name
            try:
                if not path.is_file() or not path.resolve().is_relative_to(root):
                    continue
                if path.resolve() == target.resolve():
                    break
                label = path.relative_to(root).as_posix()
                if path.stat().st_size > _MAX_INSTRUCTION_BYTES or remaining <= 0:
                    blocks.append(f"### {label}\n\nRead this instruction file before editing this subtree.")
                    break
                content = path.read_text(encoding="utf-8-sig").strip()
            except (OSError, UnicodeDecodeError):
                blocks.append(f"### {path.relative_to(root).as_posix()}\n\nCould not read these instructions. Inspect this file before editing this subtree.")
                break
            if content:
                clipped = content[:remaining]
                remaining -= len(clipped)
                if clipped != content:
                    clipped += f"\n[Instructions truncated. Read {label} for the rest before editing.]"
                blocks.append(f"### {label}\n\n{clipped}")
            break
    if not blocks:
        return ""
    return (
        "\n\n[Project instructions for this path. Follow each file within its subtree; "
        "the more specific file takes precedence for that subtree.]\n\n"
        + "\n\n".join(blocks)
    )
