"""Show the agent what it just broke, without waiting to be asked.

navin has better verification tooling than the editors it competes with -
``lint`` spans seven backends, ``lsp`` adds the semantic errors no linter sees,
and ``verify`` folds both into one verdict. None of it fires on its own. A model
that forgets to call ``verify`` reports the edit as done and the error surfaces
a turn later, or at review time, or in CI.

So the file-scoped linters run on whatever an edit touched and their findings
ride back on the tool result itself. There is nothing for the model to remember,
and nothing new to configure: the same table that backs the ``lint`` tool
decides which linter handles which suffix, and a project with no linter
installed pays nothing.

This deliberately does not run the project-wide type-checkers. ``tsc`` on a
monorepo costs tens of seconds, which is the wrong price to pay after every
edit; that stays the job of ``verify`` at the end of a change.
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

# Errors first, then warnings: a truncated list should drop the notes, not the
# failures. Anything below is noise at this point in the loop.
_REPORTED = ("error", "warning")
_MAX_FILES = 10
_MAX_DIAGNOSTICS = 12
_TIME_BUDGET_S = 1.5


def diagnostics_after_write(paths: list[Path], *, workspace: Path | None) -> str:
    """Render the problems the linters find in ``paths``, or an empty string.

    Empty is the common case and the intended one: a clean edit should read
    exactly as it did before this existed.
    """
    if not paths or workspace is None:
        return ""

    from navin.quality.linters import lint_file

    deadline = time.monotonic() + _TIME_BUDGET_S
    found: list[tuple[int, str]] = []
    truncated_files = False

    for path in _unique(paths)[:_MAX_FILES]:
        if time.monotonic() >= deadline:
            truncated_files = True
            break
        try:
            relative = path.resolve().relative_to(workspace.resolve())
        except (OSError, ValueError):
            # Edits outside the workspace: the linters are configured against
            # the project root, so their verdict there would be meaningless.
            continue
        try:
            results = lint_file(workspace, str(relative))
        except Exception:
            logger.exception("post-edit lint failed for {}", relative)
            continue
        for result in results:
            if not result.ran:
                continue
            for diagnostic in result.diagnostics:
                if diagnostic.severity not in _REPORTED:
                    continue
                # pyflakes_syntax reports a position with no message, which
                # renders as a bare "file:2:1 error". It duplicates whatever
                # named the syntax error properly, and on its own it tells the
                # model there is a problem without saying which.
                if not diagnostic.message.strip():
                    continue
                found.append((_REPORTED.index(diagnostic.severity), diagnostic.render()))

    if not found:
        return ""

    found.sort(key=lambda entry: entry[0])
    lines = [text for _, text in found[:_MAX_DIAGNOSTICS]]
    hidden = len(found) - len(lines)

    report = ["Diagnostics for the files just written:", *(f"  {line}" for line in lines)]
    if hidden > 0:
        report.append(f"  ... and {hidden} more (run lint action=changed for the full list)")
    if truncated_files:
        report.append("  ... more files were left unchecked to keep the edit responsive")
    return "\n".join(report)


def _unique(paths: list[Path]) -> list[Path]:
    """Preserve order while dropping repeats, so one file is linted once."""
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered
