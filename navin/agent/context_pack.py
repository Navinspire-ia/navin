"""Compact Agent Code context pack: open files, symbols, errors.

Injected as a runtime-context block so every Build/Ask turn sees the same
digest without a parallel prompt system. Git dirty names live on the git
state provider so this pack never walks the whole tree before the first token.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.utils.file_mentions import FILE_MENTION_METADATA_KEY, normalize_file_mentions
from navin.utils.path import normalize_relative_path

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

OPEN_FILES_METADATA_KEY = "open_files"

# The pack is truncated by a token budget, not by an arbitrary file count:
# 12 open tabs in a monorepo are all worth naming when they fit, and one
# pathological path must not evict the diagnostics of the others.
_TOKEN_BUDGET = 2000
_MAX_SYMBOLS_PER_FILE = 8
_MAX_DIAG_LINES = 6


def _as_rel(workspace: Path, path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    if not text:
        return ""
    try:
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            return candidate.resolve(strict=False).relative_to(
                workspace.resolve(strict=False)
            ).as_posix()
    except Exception:
        pass
    return normalize_relative_path(text)


def _paths_from_metadata(metadata: dict[str, Any], workspace: Path) -> list[str]:
    ordered: list[str] = []

    def add(path: str) -> None:
        rel = _as_rel(workspace, path)
        if rel and rel not in ordered:
            ordered.append(rel)

    raw_open = metadata.get(OPEN_FILES_METADATA_KEY)
    if isinstance(raw_open, (list, tuple)):
        for item in raw_open:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict) and item.get("path"):
                add(str(item["path"]))

    for mention in normalize_file_mentions(metadata.get(FILE_MENTION_METADATA_KEY)):
        add(str(mention.get("path") or ""))

    return ordered


def _symbol_lines(workspace: Path, rel: str) -> list[str]:
    try:
        from navin.index import get_index

        index = get_index(workspace)
        index.ensure()
        resolved = index.outline(rel)
        if not resolved:
            return []
        _path, symbols = resolved
        names: list[str] = []
        for symbol in symbols[:_MAX_SYMBOLS_PER_FILE]:
            name = getattr(symbol, "name", None) or getattr(symbol, "display", None)
            if name:
                names.append(str(name))
        if not names:
            return []
        return [f"  symbols: {', '.join(names)}"]
    except Exception:
        return []


def _diag_lines(workspace: Path, rel: str) -> list[str]:
    try:
        from navin.quality.linters import lint_file

        results = lint_file(workspace, rel)
    except Exception:
        return []
    rows: list[str] = []
    for result in results or []:
        if not getattr(result, "ran", False):
            continue
        tool = str(getattr(result, "linter", "") or "lint")
        for diag in getattr(result, "diagnostics", None) or []:
            severity = str(getattr(diag, "severity", "") or "warning")
            if severity not in {"error", "warning"}:
                continue
            line = getattr(diag, "line", "?")
            message = str(getattr(diag, "message", "") or "").strip()
            if not message:
                continue
            rows.append(f"  {severity}@{line} ({tool}): {message[:120]}")
            if len(rows) >= _MAX_DIAG_LINES:
                return rows
    return rows


def _budgeted_listing(label: str, paths: list[str], budget: int) -> str | None:
    """One "label: a, b, +N more" line that stays inside *budget* tokens."""
    from navin.utils.helpers import estimate_text_tokens

    if not paths:
        return None
    shown: list[str] = []
    for path in paths:
        candidate = label + ", ".join([*shown, path]) + f", +{len(paths)} more"
        if shown and estimate_text_tokens(candidate) > budget:
            break
        shown.append(path)
    extra = len(paths) - len(shown)
    suffix = f", +{extra} more" if extra > 0 else ""
    return label + ", ".join(shown) + suffix


def build_context_pack_lines(
    *,
    workspace: Path,
    metadata: dict[str, Any] | None,
    token_budget: int = _TOKEN_BUDGET,
) -> list[str]:
    """Pure helper for tests and the async provider.

    Truncation is token-aware: the pack grows until *token_budget* is spent,
    instead of stopping at a fixed number of files. The first focus file is
    always named, so even an absurdly small budget yields a usable digest.
    """
    from navin.utils.helpers import estimate_text_tokens

    meta = metadata if isinstance(metadata, dict) else {}
    open_paths = _paths_from_metadata(meta, workspace)
    # Git dirty names already ride on git_state_context_provider. Scanning the
    # whole tree here (then linting it) sat in front of the first token, so a
    # "salut" on a large WSL repo waited 10s for advisory metadata.

    lines: list[str] = ["Agent context pack:"]
    # Roughly a third of the budget for the listing line, the rest for
    # the per-file focus details below.
    listing_budget = max(64, token_budget // 3)
    open_line = _budgeted_listing("Open/attached: ", open_paths, listing_budget)
    if open_line:
        lines.append(open_line)

    focus = list(open_paths)
    if not focus:
        if len(lines) == 1:
            return []
        return lines

    lines.append("Focus files:")
    used = estimate_text_tokens("\n".join(lines))
    # Lint only the first few paths - full-tree lint is too slow for every turn.
    lint_budget = 3
    for position, rel in enumerate(focus):
        entry = [f"- {rel}"]
        entry.extend(_symbol_lines(workspace, rel))
        if lint_budget > 0:
            diag = _diag_lines(workspace, rel)
            if diag:
                entry.extend(diag)
                lint_budget -= 1
        cost = estimate_text_tokens("\n".join(entry))
        if position > 0 and used + cost > token_budget:
            remaining = len(focus) - position
            lines.append(f"- (+{remaining} more file(s) over the token budget)")
            break
        lines.extend(entry)
        used += cost
    return lines


async def agent_context_pack_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Pack open files + git dirty + symbols + diagnostics for Agent Code turns."""
    workspace = request.workspace
    if workspace is None:
        return None
    # Prefer Code / forge turns; still useful whenever open_files exist.
    lines = await asyncio.to_thread(
        build_context_pack_lines,
        workspace=Path(workspace),
        metadata=dict(request.metadata or {}),
    )
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="agent_context_pack", content=content)
