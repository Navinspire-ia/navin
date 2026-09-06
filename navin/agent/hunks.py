"""Hunk-level selection between a baseline and the current content of a file.

Review is stored as "baseline bytes, current content read from disk" (see
:mod:`navin.agent.review`), which means the whole pending change is exactly
``baseline + every hunk``. That identity is what makes per-hunk decisions cheap,
because both directions collapse into one primitive - apply a chosen subset of
hunks to the baseline:

- **accept one hunk**: the new baseline becomes ``baseline + {that hunk}``. The
  file on disk is untouched, and the hunks left over stay pending.
- **reject one hunk**: the file on disk becomes ``baseline + {every other
  hunk}``. The baseline is untouched.

So there is no second storage format and no patch language to invent. Grouping
comes from :func:`difflib.SequenceMatcher.get_grouped_opcodes`, not from parsing
``@@`` headers, so a hunk boundary here is the same notion a reader is used to
without a text format sitting in the middle to be mis-parsed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher

# Unified-diff convention. Also decides which nearby edits merge into one hunk:
# two changes separated by more context than this are separate decisions.
CONTEXT_LINES = 3
_ID_DIGEST_CHARS = 8


@dataclass(frozen=True, slots=True)
class Hunk:
    """One independently selectable change between baseline and current."""

    id: str
    old_start: int  # 0-based line index into the baseline
    old_count: int
    new_start: int  # 0-based line index into the current content
    new_count: int
    added: int
    deleted: int

    def payload(self) -> dict[str, int | str]:
        """Wire shape for the review API."""
        return {
            "id": self.id,
            "old_start": self.old_start,
            "old_count": self.old_count,
            "new_start": self.new_start,
            "new_count": self.new_count,
            "added": self.added,
            "deleted": self.deleted,
        }


def split_lines(text: str) -> list[str]:
    """Split keeping line endings, so a rebuild is byte-exact.

    ``keepends`` is not a detail: dropping the endings and re-joining with "\\n"
    would rewrite every CRLF file on Windows the first time a single hunk was
    accepted, and would silently add a trailing newline to files that lack one.
    """
    return text.splitlines(keepends=True)


def _hunk_id(index: int, old: list[str], new: list[str]) -> str:
    """Positional id carrying a content digest.

    The index alone is not safe. The review panel polls every few seconds and the
    agent may still be writing, so by the time a click arrives the hunk at index 3
    can be a different change. The digest lets the server refuse rather than
    silently revert the wrong lines.
    """
    digest = hashlib.blake2b(
        "".join(old).encode("utf-8", "surrogatepass")
        + b"\x00\x00"
        + "".join(new).encode("utf-8", "surrogatepass"),
        digest_size=8,
    ).hexdigest()[:_ID_DIGEST_CHARS]
    return f"{index}:{digest}"


def compute_hunks(baseline: str, current: str) -> list[Hunk]:
    """Independently selectable hunks turning ``baseline`` into ``current``."""
    old_lines = split_lines(baseline)
    new_lines = split_lines(current)
    matcher = SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    hunks: list[Hunk] = []
    for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
        changed = [op for op in group if op[0] != "equal"]
        if not changed:
            continue
        # Span from the first to the last real change in the group. Equal opcodes
        # in between are context that both sides share, and including them in the
        # span keeps the splice a single contiguous replacement.
        old_start, old_end = changed[0][1], changed[-1][2]
        new_start, new_end = changed[0][3], changed[-1][4]
        added = sum(op[4] - op[3] for op in changed if op[0] in {"insert", "replace"})
        deleted = sum(op[2] - op[1] for op in changed if op[0] in {"delete", "replace"})
        hunks.append(
            Hunk(
                id=_hunk_id(
                    len(hunks),
                    old_lines[old_start:old_end],
                    new_lines[new_start:new_end],
                ),
                old_start=old_start,
                old_count=old_end - old_start,
                new_start=new_start,
                new_count=new_end - new_start,
                added=added,
                deleted=deleted,
            )
        )
    return hunks


class HunkConflictError(ValueError):
    """The requested hunk no longer matches the file, so nothing was applied."""


def resolve_hunk(hunks: list[Hunk], hunk_id: str) -> Hunk:
    """Find ``hunk_id``, or refuse because the file moved under the request."""
    for hunk in hunks:
        if hunk.id == hunk_id:
            return hunk
    raise HunkConflictError(
        "that hunk is no longer part of this change - the file was edited since "
        "it was displayed; reload the diff and try again"
    )


def apply_hunks(baseline: str, current: str, selected: set[str]) -> str:
    """Rebuild the file with only ``selected`` hunks applied to ``baseline``.

    Unselected regions keep their baseline lines, selected ones take their current
    lines. Hunks never overlap and arrive sorted, so this is a single left-to-right
    splice rather than an offset-tracking exercise.
    """
    old_lines = split_lines(baseline)
    new_lines = split_lines(current)

    out: list[str] = []
    cursor = 0
    for hunk in compute_hunks(baseline, current):
        out.extend(old_lines[cursor : hunk.old_start])
        if hunk.id in selected:
            out.extend(new_lines[hunk.new_start : hunk.new_start + hunk.new_count])
        else:
            out.extend(old_lines[hunk.old_start : hunk.old_start + hunk.old_count])
        cursor = hunk.old_start + hunk.old_count
    out.extend(old_lines[cursor:])
    return "".join(out)
