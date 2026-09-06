"""Delete, move and copy files the way the editing tools write them: on the record.

Three tools call ``record_file_before`` before touching a file - ``write_file``,
``edit_file`` and ``apply_patch``. That baseline is what the review panel diffs
against and what a checkpoint restores from, so anything that changes the tree
without recording one is invisible to both: it cannot be rejected and it cannot
be rolled back.

Until now the agent had no way to delete or move a file, so it reached for
``exec rm`` and ``exec mv`` - precisely the path that records nothing. The
destructive operations were the untracked ones. This module closes that: every
file that is about to disappear or be overwritten has its bytes recorded first,
the destination of a move is recorded as previously absent, and the review panel
therefore shows a deletion as a deletion and a move as a deletion plus a
creation, each individually rejectable.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from navin.agent.checkpoints import record_file_before
from navin.agent.tools.base import ToolResult, tool_parameters
from navin.agent.tools.filesystem import _FsTool
from navin.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.utils import longpath
from navin.utils.git_state import clear_cache, uncommitted_note

_ACTIONS = ("delete", "move", "copy", "mkdir")

# What one call may touch and still be rolled back comes from
# tools.file.maxTrackedFiles / maxTrackedMib, because both limits exist only to
# keep the baseline in memory - they are a property of review, not a rule about
# what the agent is allowed to delete. Set either to 0 to lift it.

# Which names are off-limits comes from tools.file.protectedPaths, defaulting to
# version-control metadata. The entries below are navin's own state in the
# workspace root, which the tool would corrupt rather than merely delete.
# "memory" covers the legacy root layout; under .navin/ we fence the state
# folders that no baseline can restore. board / agents / rules / SOUL / USER
# stay manageable so the agent can maintain them.
_PROTECTED_AT_ROOT = frozenset({"sessions", "memory", "history.jsonl", ".dream_cursor"})
_PROTECTED_PREFIXES_AT_ROOT = (
    (".navin", "memory"),
    (".navin", "checkpoints"),
    (".navin", "metadata"),
)


class _ManageError(ValueError):
    pass


def _relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _protected_component(
    path: Path, root: Path | None, protected: frozenset[str]
) -> str | None:
    """Name the protected entry this path would take out, if any."""
    inside = root is not None and _relative_to(path, root)
    parts = path.relative_to(root).parts if inside and root else path.parts
    for part in parts:
        if part in protected:
            return part
    if inside and parts and parts[0] in _PROTECTED_AT_ROOT:
        return parts[0]
    if inside:
        # A path is protected when it sits under a protected prefix, and also
        # when it *contains* one (deleting `.navin` itself would take out
        # `.navin/memory`).
        for prefix in _PROTECTED_PREFIXES_AT_ROOT:
            depth = min(len(parts), len(prefix))
            if depth and parts[:depth] == prefix[:depth]:
                return "/".join(prefix)
    return None


def _files_under(path: Path) -> list[Path]:
    """Every regular file this operation would destroy, the path itself included."""
    if path.is_file() or path.is_symlink():
        return [path]
    if not path.is_dir():
        return []
    return [entry for entry in sorted(path.rglob("*")) if entry.is_file()]


def _record_baselines(paths: list[Path]) -> None:
    for path in paths:
        record_file_before(path)


def _weigh(files: list[Path]) -> int:
    total = 0
    for file in files:
        try:
            total += file.stat().st_size
        except OSError:
            continue
    return total


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "delete: remove files or directories; "
            "move: move or rename one path to another; "
            "copy: duplicate one path to another; "
            "mkdir: create a directory and its parents.",
            enum=list(_ACTIONS),
        ),
        paths=ArraySchema(
            items=StringSchema("Workspace-relative or absolute path."),
            description="Paths to delete. Only for delete.",
            min_items=1,
            max_items=50,
        ),
        path=StringSchema(
            "Source path, for move and copy, or the directory to create for mkdir.",
            nullable=True,
        ),
        destination=StringSchema(
            "Where the path lands. Required for move and copy.",
            nullable=True,
        ),
        recursive=BooleanSchema(
            description=(
                "Allow delete to remove a directory and everything inside it "
                "(default false)."
            ),
            default=False,
        ),
        overwrite=BooleanSchema(
            description=(
                "Allow move and copy to replace an existing destination "
                "(default false)."
            ),
            default=False,
        ),
        required=["action"],
    )
)
class ManageFilesTool(_FsTool):
    """Delete, move, rename or copy paths, recorded for review and rollback."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "manage_files"

    @property
    def description(self) -> str:
        return (
            "Delete, move, rename or copy files and directories, and create "
            "directories. Prefer this over 'exec rm', 'mv', 'cp', 'del' or "
            "'Remove-Item': what those do is invisible to the review panel and "
            "cannot be undone by a checkpoint, while everything done here shows "
            "up as a normal pending change and can be rejected. A move is "
            "recorded as a deletion plus a creation. Deleting a directory needs "
            "recursive=true, and replacing an existing destination needs "
            "overwrite=true. Moving a file does not update the code that imports "
            "it - fix those references in the same turn."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        action: str = "",
        paths: list[str] | None = None,
        path: str | None = None,
        destination: str | None = None,
        recursive: bool = False,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            if action not in _ACTIONS:
                raise _ManageError(
                    f"unknown action '{action}'. Valid actions: {', '.join(_ACTIONS)}."
                )
            if action == "delete":
                result = await self._delete(paths or [], recursive=recursive)
            elif action == "mkdir":
                result = await self._mkdir(path)
            else:
                result = await self._transfer(
                    action, path, destination, overwrite=overwrite
                )
        except PermissionError as exc:
            return ToolResult.error(f"Error: {exc}")
        except _ManageError as exc:
            return ToolResult.error(f"Error: {exc}")
        except OSError as exc:
            return ToolResult.error(f"Error running {action}: {exc}")

        # The per-turn git block is memoised, and a deletion or a rename is
        # exactly the kind of change it would otherwise describe as it was.
        clear_cache()
        return result

    # -- delete -------------------------------------------------------------

    async def _delete(self, raw_paths: list[str], *, recursive: bool) -> str:
        if not raw_paths:
            raise _ManageError("delete needs at least one path")

        targets: list[tuple[str, Path]] = []
        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                raise _ManageError("every path must be a non-empty string")
            targets.append((raw, await self._bound_path(raw, write=True)))

        doomed: list[Path] = []
        for raw, target in targets:
            self._refuse_protected(raw, target)
            if not target.exists() and not target.is_symlink():
                raise _ManageError(f"path does not exist: {raw}")
            if target.is_dir() and not target.is_symlink():
                if not recursive:
                    raise _ManageError(
                        f"{raw} is a directory. Pass recursive=true to remove it "
                        "and everything inside it."
                    )
                doomed.extend(_files_under(target))
            else:
                doomed.append(target)

        self._refuse_oversized(doomed, "delete")
        warnings = self._warn_about(doomed)
        await self._ask_before_losing(
            "delete",
            warnings,
            detail=", ".join(self._display(target) for _raw, target in targets[:10]),
        )
        _record_baselines(doomed)

        removed_dirs = 0
        for _raw, target in targets:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
                removed_dirs += 1
            else:
                target.unlink()
        for file in doomed:
            self._file_states.record_write(file)

        parts = []
        if doomed:
            parts.append(f"Deleted {len(doomed)} file(s)")
        if removed_dirs:
            parts.append(f"{removed_dirs} directory tree(s)")
        body = ", ".join(parts) if parts else "Deleted nothing"
        names = ", ".join(self._display(target) for _raw, target in targets[:10])
        if len(targets) > 10:
            names += ", ..."
        return self._with_warnings(warnings, f"{body}: {names}")

    # -- mkdir --------------------------------------------------------------

    async def _mkdir(self, raw: str | None) -> str:
        if not raw or not raw.strip():
            raise _ManageError("mkdir needs a path")
        target = await self._bound_path(raw, write=True)
        if target.is_dir():
            return f"Directory already exists: {self._display(target)}"
        if target.exists():
            raise _ManageError(f"{raw} already exists and is not a directory")
        target.mkdir(parents=True, exist_ok=True)
        return f"Created directory {self._display(target)}"

    # -- move / copy --------------------------------------------------------

    async def _transfer(
        self,
        action: str,
        raw_source: str | None,
        raw_destination: str | None,
        *,
        overwrite: bool,
    ) -> str:
        if not raw_source or not raw_source.strip():
            raise _ManageError(f"{action} needs path")
        if not raw_destination or not raw_destination.strip():
            raise _ManageError(f"{action} needs destination")

        source = await self._bound_path(raw_source, write=True)
        destination = await self._bound_path(raw_destination, write=True)
        self._refuse_protected(raw_source, source)
        self._refuse_protected(raw_destination, destination)

        if not source.exists() and not source.is_symlink():
            raise _ManageError(f"path does not exist: {raw_source}")
        if source == destination:
            raise _ManageError("path and destination are the same")

        # An existing directory as the destination means "into it", the way mv
        # and cp behave, rather than replacing the directory itself.
        if destination.is_dir():
            destination = longpath.io_path(destination / source.name)

        if source.is_dir() and _relative_to(destination, source):
            raise _ManageError(f"cannot {action} {raw_source} into itself")

        # Collision is about the path existing at all, not about it holding
        # files: an empty directory at the destination contributes nothing to
        # ``replaced``, and skipping the guard on that basis would let
        # shutil.move nest the source inside it (dest/name/name) instead of
        # replacing it.
        collides = destination.exists() or destination.is_symlink()
        replaced = _files_under(destination) if collides else []
        if collides and not overwrite:
            raise _ManageError(
                f"{self._display(destination)} already exists. Pass overwrite=true "
                "to replace it."
            )

        arriving = _files_under(source)
        tracked = [*replaced, *([] if action == "copy" else arriving)]
        self._refuse_oversized(tracked, action)
        warnings = self._warn_about(tracked)
        await self._ask_before_losing(
            action,
            warnings,
            detail=f"{self._display(source)} to {self._display(destination)}",
        )

        # The destination's own baseline: its bytes if it is being replaced,
        # nothing if it is new. Either way the review panel needs the entry, or
        # the arriving file looks like it was always there.
        _record_baselines(replaced)
        for file in arriving:
            landing = destination if source.is_file() else destination / file.relative_to(source)
            record_file_before(longpath.io_path(landing))
        if action == "move":
            _record_baselines(arriving)

        destination.parent.mkdir(parents=True, exist_ok=True)
        if collides:
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if action == "move":
            shutil.move(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

        landed = _files_under(destination)
        for file in [*arriving, *landed]:
            self._file_states.record_write(file)

        verb = "Moved" if action == "move" else "Copied"
        note = f" ({len(arriving)} files)" if len(arriving) > 1 else ""
        summary = self._with_warnings(
            warnings,
            f"{verb} {self._display(source)} to {self._display(destination)}{note}. "
            "References to the old path are not updated - search for them.",
        )
        # write_file and edit_file come back with what the linters think of
        # the result; a move or copy changes the tree just as much, and a file
        # that is broken in its new location should say so on the same reply
        # rather than a turn later. Delete and mkdir leave nothing to lint.
        return await self._with_diagnostics(summary, landed)

    # -- guards -------------------------------------------------------------

    def _refuse_protected(self, raw: str, target: Path) -> None:
        workspace = self._display_workspace()
        root = Path(workspace).expanduser().resolve(strict=False) if workspace else None
        resolved = Path(target).expanduser().resolve(strict=False)
        if root is not None and resolved == root:
            raise _ManageError("refusing to touch the project root itself")
        if root is not None and _relative_to(root, resolved) and resolved != root:
            raise _ManageError(
                f"{raw} contains the project root, so removing it would take the "
                "workspace with it"
            )
        protected = _protected_component(resolved, root, self._protected_paths)
        if protected:
            raise _ManageError(
                f"refusing to touch {protected}: it holds history or session state "
                "that no baseline can restore. Remove it from tools.file.protectedPaths, "
                "or use exec if this is really intended."
            )

    def _refuse_oversized(self, files: list[Path], action: str) -> None:
        if self._max_tracked_files and len(files) > self._max_tracked_files:
            raise _ManageError(
                f"{action} would affect {len(files)} files, above the "
                f"{self._max_tracked_files} that can be kept reviewable and undoable. "
                "Narrow it down, raise tools.file.maxTrackedFiles, or run it through "
                "exec knowing it will not be reviewable."
            )
        if not self._max_tracked_bytes:
            return
        weight = _weigh(files)
        if weight > self._max_tracked_bytes:
            raise _ManageError(
                f"{action} would affect {weight // (1024 * 1024)} MiB, above the "
                f"{self._max_tracked_bytes // (1024 * 1024)} MiB that can be kept "
                "reviewable and undoable. Narrow it down, raise "
                "tools.file.maxTrackedMib, or run it through exec knowing it will "
                "not be reviewable."
            )

    async def _ask_before_losing(
        self,
        action: str,
        warnings: list[str],
        *,
        detail: str,
    ) -> None:
        """Stop for the user when the operation destroys unsaved, untracked work.

        The trigger is the same condition ``_warn_about`` reports: a tracked file
        whose current content is in neither git nor a checkpoint. Everything else
        proceeds, because the baseline recorded here already makes it reversible,
        and a tool that asks about reversible work would only train the user to
        click through. No "always allow" is offered for the same reason: consent
        to lose one piece of work says nothing about the next.
        """
        if not warnings:
            return

        from navin.agent.approval import ApprovalRequest, request_approval

        decision = await request_approval(ApprovalRequest(
            tool="manage_files",
            action=f"{action.capitalize()} files that hold unsaved work",
            reason=(
                f"{len(warnings)} file(s) in this operation have uncommitted "
                "changes, so their current content exists nowhere else."
            ),
            detail="\n".join([detail, "", *warnings]).strip(),
            consequence="The uncommitted changes are lost and cannot be recovered.",
            # This used to proceed with the warning in the reply, and it still
            # does where no one can be asked; the warning remains either way.
            allow_when_unattended=True,
        ))
        if not decision.allowed:
            raise _ManageError(
                f"{action} refused: it would destroy uncommitted changes. "
                f"{decision.reason}"
            )

    def _warn_about(self, files: list[Path]) -> list[str]:
        """Say what is about to be lost that git does not have a copy of."""
        workspace = self._display_workspace()
        if workspace is None:
            return []
        warnings: list[str] = []
        for file in files[:20]:
            note = uncommitted_note(workspace, file)
            if note:
                warnings.append(note)
        return warnings

    @staticmethod
    def _with_warnings(warnings: list[str], body: str) -> str:
        if not warnings:
            return body
        return "\n".join([*warnings, "", body])
