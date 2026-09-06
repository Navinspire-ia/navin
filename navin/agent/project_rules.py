"""Project rules carried by imported repositories, read natively.

Repositories configured for other coding agents already carry durable rules:

- ``.cursor/rules/*.mdc`` (Cursor, frontmatter with ``alwaysApply`` / globs)
- ``.cursorrules`` (legacy Cursor single file)
- ``.clinerules`` (Cline - single file or folder of ``*.md``)
- ``.windsurfrules`` (Windsurf)
- ``.github/copilot-instructions.md`` (Copilot)
- ``.navin/rules/*.md`` (Navin's own project rules notes)

Importing such a project into Navin must not lose those instructions, so this
module gathers them into one bounded prompt section. Always-on rules are
inlined; conditional Cursor rules (glob / description scoped) are listed with
their path so the agent can open them when relevant instead of bloating every
turn.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from loguru import logger

# A rule file larger than this is almost certainly not a rule file.
_MAX_RULE_FILE_BYTES = 64 * 1024
# Inlined rule bodies are clipped so one giant file cannot flood the prompt.
_MAX_INLINE_CHARS = 8_000
# Hard cap on scanned rule files per source folder.
_MAX_FILES_PER_SOURCE = 40

_FRONTMATTER = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)


def _read_bounded(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_RULE_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _clip(body: str) -> str:
    body = body.strip()
    if len(body) > _MAX_INLINE_CHARS:
        return body[:_MAX_INLINE_CHARS].rstrip() + "\n[... rule truncated ...]"
    return body


def _split_frontmatter(content: str) -> tuple[dict, str]:
    match = _FRONTMATTER.match(content)
    if not match:
        return {}, content
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, content[match.end():]


def _cursor_rules(root: Path, inline: list[str], listed: list[str]) -> None:
    rules_dir = root / ".cursor" / "rules"
    if not rules_dir.is_dir():
        return
    try:
        files = sorted(rules_dir.rglob("*.mdc"))[:_MAX_FILES_PER_SOURCE]
    except OSError:
        return
    for path in files:
        content = _read_bounded(path)
        if content is None:
            continue
        meta, body = _split_frontmatter(content)
        rel = path.relative_to(root)
        always = meta.get("alwaysApply")
        # Hand-written frontmatter sometimes quotes the boolean.
        if always is True or (isinstance(always, str) and always.strip().lower() == "true"):
            if body.strip():
                inline.append(f"### {rel}\n\n{_clip(body)}")
            continue
        # Conditional rule: surface its existence so the agent can read it
        # when the description / globs match the work at hand.
        details = []
        description = str(meta.get("description") or "").strip()
        if description:
            details.append(description)
        globs = meta.get("globs")
        if globs:
            if isinstance(globs, list):
                globs = ", ".join(str(g) for g in globs)
            details.append(f"globs: {globs}")
        suffix = f" - {'; '.join(details)}" if details else ""
        listed.append(f"- `{rel}`{suffix}")


def _single_file_rules(root: Path, inline: list[str]) -> None:
    for name in (".cursorrules", ".windsurfrules"):
        content = _read_bounded(root / name)
        if content and content.strip():
            inline.append(f"### {name}\n\n{_clip(content)}")

    # .clinerules is a file in older setups and a folder of *.md in newer ones.
    clinerules = root / ".clinerules"
    if clinerules.is_file():
        content = _read_bounded(clinerules)
        if content and content.strip():
            inline.append(f"### .clinerules\n\n{_clip(content)}")
    elif clinerules.is_dir():
        try:
            files = sorted(clinerules.rglob("*.md"))[:_MAX_FILES_PER_SOURCE]
        except OSError:
            files = []
        for path in files:
            content = _read_bounded(path)
            if content and content.strip():
                inline.append(f"### {path.relative_to(root)}\n\n{_clip(content)}")

    copilot = root / ".github" / "copilot-instructions.md"
    content = _read_bounded(copilot)
    if content and content.strip():
        inline.append(f"### .github/copilot-instructions.md\n\n{_clip(content)}")


def _navin_rules(root: Path, inline: list[str]) -> None:
    rules_dir = root / ".navin" / "rules"
    if not rules_dir.is_dir():
        return
    try:
        files = sorted(rules_dir.rglob("*.md"))[:_MAX_FILES_PER_SOURCE]
    except OSError:
        return
    for path in files:
        # README.md is the scaffold placeholder, not a user rule.
        if path.name.lower() == "readme.md":
            continue
        content = _read_bounded(path)
        if content and content.strip():
            inline.append(f"### {path.relative_to(root)}\n\n{_clip(content)}")


def list_navin_rules(workspace: Path) -> list[dict[str, str | int | bool]]:
    """Editable Navin rules under ``.navin/rules/*.md`` (excludes README)."""
    root = workspace.expanduser()
    try:
        root = root.resolve(strict=False)
    except OSError:
        return []
    rules_dir = root / ".navin" / "rules"
    if not rules_dir.is_dir():
        return []
    rows: list[dict[str, str | int | bool]] = []
    try:
        files = sorted(rules_dir.rglob("*.md"))[:_MAX_FILES_PER_SOURCE]
    except OSError:
        return []
    for path in files:
        if path.name.lower() == "readme.md":
            continue
        content = _read_bounded(path)
        if content is None:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        rows.append(
            {
                "path": rel,
                "name": path.stem,
                "exists": True,
                "bytes": len(content.encode("utf-8")),
                "content": content,
            }
        )
    return rows


def write_navin_rule(
    workspace: Path,
    *,
    name: str,
    content: str,
) -> dict[str, str | int | bool]:
    """Create or overwrite ``.navin/rules/<name>.md`` (safe basename only)."""
    root = workspace.expanduser()
    try:
        root = root.resolve(strict=False)
    except OSError as exc:
        raise ValueError(f"invalid workspace: {exc}") from exc
    if not root.is_dir():
        raise ValueError("project directory not found")
    cleaned = (name or "").strip()
    cleaned = cleaned.removesuffix(".md")
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", cleaned):
        raise ValueError("invalid rule name")
    if cleaned.lower() == "readme":
        raise ValueError("README.md is reserved")
    rules_dir = root / ".navin" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    target = (rules_dir / f"{cleaned}.md").resolve(strict=False)
    try:
        target.relative_to((rules_dir).resolve(strict=False))
    except ValueError as exc:
        raise ValueError("path escapes rules directory") from exc
    body = content if isinstance(content, str) else ""
    if len(body.encode("utf-8")) > _MAX_RULE_FILE_BYTES:
        raise ValueError("rule file too large")
    target.write_text(body, encoding="utf-8")
    rel = target.relative_to(root).as_posix()
    return {
        "path": rel,
        "name": cleaned,
        "exists": True,
        "bytes": len(body.encode("utf-8")),
        "content": body,
    }


def project_rules_summary(workspace: Path) -> str:
    """One prompt section with every project rule an imported repo carries.

    Returns an empty string when the project defines no rules.
    """
    root = workspace.expanduser()
    try:
        root = root.resolve(strict=False)
    except OSError:
        return ""
    if not root.is_dir():
        return ""

    inline: list[str] = []
    listed: list[str] = []
    try:
        _navin_rules(root, inline)
        _cursor_rules(root, inline, listed)
        _single_file_rules(root, inline)
    except Exception:
        logger.exception("project rules scan failed for {}", root)
        return ""

    sections: list[str] = []
    if inline:
        sections.append("\n\n".join(inline))
    if listed:
        sections.append(
            "### Conditional rules (read when their scope matches the task)\n\n"
            + "\n".join(listed)
        )
    return "\n\n".join(sections)
