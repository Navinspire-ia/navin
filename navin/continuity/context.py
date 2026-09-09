# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Inject long-running project continuity into every agent turn.

Board digest already reminds the model of open tasks. Continuity adds what
Cursor/Claude forget between weeks: the resume brief, durable decisions, and
hard constraints from MEMORY.md. Kept as a short digest so context cost stays
bounded on ultra-large projects.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from navin.project_scaffold import _DECISIONS, _RESUME
from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

_MAX_RESUME_CHARS = 1200
_MAX_DECISION_LINES = 8
_MAX_CONSTRAINTS = 8
_CONSTRAINT_HEADING = re.compile(r"^##\s+Constraints\s*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)
_DECISION_HEADING = re.compile(r"^###\s+.+", re.MULTILINE)

# Placeholders from the scaffold template - if only these remain, skip injection.
_RESUME_PLACEHOLDERS = (
    "(State, open risks, next concrete action)",
    "(Files / modules currently in play)",
    "(Constraints mirrored from memory/MEMORY.md ## Constraints)",
)

# Auto section the Consolidator mirrors its handoff brief into on compaction.
_AUTO_HANDOFF_START = "<!-- navin:auto-handoff:start -->"
_AUTO_HANDOFF_END = "<!-- navin:auto-handoff:end -->"


def _split_auto_handoff(text: str) -> tuple[str, str]:
    """Split RESUME.md into (manual part, auto handoff part)."""
    start = text.find(_AUTO_HANDOFF_START)
    end = text.find(_AUTO_HANDOFF_END)
    if start == -1 or end == -1 or end <= start:
        return text, ""
    auto = text[start + len(_AUTO_HANDOFF_START):end].strip()
    manual = (text[:start] + text[end + len(_AUTO_HANDOFF_END):]).strip()
    return manual, auto

_TEMPLATE_CONSTRAINT_EXAMPLES = (
    "Do not modify billing / license without an explicit review.",
    "Keep public APIs backward compatible unless the user asks otherwise.",
)


def _read_text(path: Path, *, limit: int = 64_000) -> str:
    try:
        if not path.is_file():
            return ""
        data = path.read_text(encoding="utf-8")
        if len(data) > limit:
            return data[:limit]
        return data
    except OSError:
        return ""


def _normalize_md(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _resume_is_actionable(text: str) -> bool:
    cleaned = _normalize_md(text)
    if not cleaned:
        return False
    if cleaned == _normalize_md(_RESUME):
        return False
    if all(token in cleaned for token in _RESUME_PLACEHOLDERS):
        return False
    return len(cleaned) >= 80


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _extract_constraints(memory: str) -> list[str]:
    match = _CONSTRAINT_HEADING.search(memory)
    if not match:
        return []
    rest = memory[match.end() :]
    next_heading = _NEXT_HEADING.search(rest)
    body = rest[: next_heading.start()] if next_heading else rest
    lines: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.startswith("*This file"):
            continue
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        elif re.match(r"^\d+[.)]\s+", line):
            line = re.sub(r"^\d+[.)]\s+", "", line).strip()
        if line.startswith("(") and line.endswith(")"):
            continue
        if line.lower().startswith("example:"):
            continue
        if line.startswith("Hard rules"):
            continue
        if line in _TEMPLATE_CONSTRAINT_EXAMPLES:
            continue
        if line:
            lines.append(line)
        if len(lines) >= _MAX_CONSTRAINTS:
            break
    return lines


def _decision_headings(text: str) -> list[str]:
    if _normalize_md(text) == _normalize_md(_DECISIONS):
        return []
    headings = [m.group(0)[4:].strip() for m in _DECISION_HEADING.finditer(text)]
    # Drop the template example heading if present alone with placeholders.
    out = [h for h in headings if not h.lower().startswith("yyyy-mm-dd")]
    return out[:_MAX_DECISION_LINES]


def continuity_digest(project_path: Path | str) -> list[str]:
    """Build continuity lines for the runtime context. Never raises."""
    root = Path(project_path).expanduser()
    lines: list[str] = []

    resume_raw = _read_text(root / ".navin" / "continuity" / "RESUME.md")
    manual_resume, auto_resume = _split_auto_handoff(resume_raw)
    resume_lines: list[str] = []
    if manual_resume and _resume_is_actionable(manual_resume):
        resume_lines.append(_clip(manual_resume, _MAX_RESUME_CHARS))
    if auto_resume:
        resume_lines.append(_clip(auto_resume, _MAX_RESUME_CHARS))
    if resume_lines:
        lines.append("Project resume (.navin/continuity/RESUME.md):")
        lines.extend(resume_lines)

    decisions = _read_text(root / ".navin" / "continuity" / "DECISIONS.md")
    headings = _decision_headings(decisions) if decisions else []
    if headings:
        lines.append("Durable decisions:")
        for heading in headings:
            lines.append(f"- {heading}")

    from navin import workspace_layout

    memory = _read_text(workspace_layout.read_with_root_fallback(root, "memory/MEMORY.md"))
    constraints = _extract_constraints(memory) if memory else []
    if constraints:
        lines.append("Hard constraints (.navin/memory/MEMORY.md):")
        for item in constraints:
            lines.append(f"- {item}")

    return lines


async def continuity_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Remind the model of resume / decisions / constraints every turn."""
    workspace = request.workspace
    if workspace is None:
        return None
    lines = await asyncio.to_thread(continuity_digest, workspace)
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="continuity", content=content)
