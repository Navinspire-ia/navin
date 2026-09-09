# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""File system tools: read, write, edit, list."""

import asyncio
import difflib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field

from navin.agent.checkpoints import record_file_before
from navin.agent.project_instructions import scoped_project_instructions
from navin.agent.tools.atomic_write import encode_for
from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.file_state import FileStates, _hash_content, current_file_states
from navin.agent.tools.path_utils import closest_existing_match, resolve_workspace_path
from navin.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.config.secrets import RUNTIME_CONFIG_DENIED, is_runtime_secret_path
from navin.config_base import Base
from navin.security.workspace_access import (
    approved_outside_paths,
    current_tool_workspace,
    remember_approved_path,
)
from navin.security.workspace_policy import WorkspaceBoundaryError
from navin.utils import longpath, text_decode
from navin.utils.git_state import uncommitted_note
from navin.utils.helpers import build_image_content_blocks, detect_image_mime


def _split_approved_paths() -> tuple[list[Path], list[Path]]:
    """Turn chat-approved outside paths into extra roots/files for resolve."""
    dirs: list[Path] = []
    files: list[Path] = []
    for raw in approved_outside_paths():
        try:
            item = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        if item.is_dir():
            dirs.append(item)
        else:
            files.append(item)
            dirs.append(item.parent)
    return dirs, files


def _existing_layout(fp: Path) -> text_decode.DecodedText | None:
    """How this file is currently encoded, so a rewrite does not convert it.

    ``write_file`` replaces whole files, including ones it did not create. A
    cp1252 source or a BOM-prefixed script rewritten as plain UTF-8 still reads
    correctly here and breaks in the toolchain that owns it.
    """
    try:
        if not fp.is_file():
            return None
        return text_decode.decode(fp.read_bytes())
    except OSError:
        return None


def _match_line_endings(content: str, layout: text_decode.DecodedText | None) -> str:
    """Keep a CRLF file on CRLF when a whole-file rewrite arrives with LF text.

    Models write LF. Converting the file as a side effect of an unrelated
    rewrite shows up in review as a change on every single line, and flips the
    convention of a checkout that deliberately uses CRLF.
    """
    if layout is None or not layout.crlf or "\r\n" in content:
        return content
    return content.replace("\n", "\r\n")


class FileToolsConfig(Base):
    """Filesystem tools configuration."""

    enable: bool = True  # built-in file tools on by default
    # Names manage_files refuses to remove, wherever they appear in a path.
    # Defaults to the version-control metadata whose loss no baseline can undo;
    # an operator can widen it, or empty it to leave nothing off-limits.
    protected_paths: list[str] = Field(
        # ".checkpoints" = legacy root folder; current store is
        # .navin/checkpoints (fenced via file_manage prefixes).
        default_factory=lambda: [".git", ".hg", ".svn", ".checkpoints"],
        validation_alias=AliasChoices("protectedPaths", "protected_paths"),
        serialization_alias="protectedPaths",
    )
    # How much one manage_files call may touch while staying reviewable and
    # undoable: both limits exist because the baseline is held in memory. 0 on
    # either means no ceiling, and the call stops being cheap to roll back.
    max_tracked_files: int = Field(
        default=200,
        ge=0,
        validation_alias=AliasChoices("maxTrackedFiles", "max_tracked_files"),
        serialization_alias="maxTrackedFiles",
    )
    max_tracked_mib: int = Field(
        default=64,
        ge=0,
        validation_alias=AliasChoices("maxTrackedMib", "max_tracked_mib"),
        serialization_alias="maxTrackedMib",
    )
    # Reading a character device can hang the turn or stream forever, so these
    # are refused by default. Off for an operator who needs /dev or /proc.
    block_device_reads: bool = Field(
        default=True,
        validation_alias=AliasChoices("blockDeviceReads", "block_device_reads"),
        serialization_alias="blockDeviceReads",
    )
    # Run the file-scope linters on whatever an edit touched and return their
    # findings with the edit. Off for a project whose linters are slow enough
    # that the wait costs more than the reminder is worth.
    lint_after_edit: bool = Field(
        default=True,
        validation_alias=AliasChoices("lintAfterEdit", "lint_after_edit"),
        serialization_alias="lintAfterEdit",
    )


class _FsTool(Tool):
    """Shared base for filesystem tools - common init and path resolution."""

    config_key = "file"

    @classmethod
    def config_cls(cls):
        return FileToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.file.enable

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        extra_allowed_dirs: list[Path] | None = None,
        extra_read_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_dirs: list[Path] | None = None,
        extra_write_allowed_files: list[Path] | None = None,
        file_states: FileStates | None = None,
        restrict_to_workspace: bool | None = None,
        sandbox_restricts_workspace: bool = False,
        protected_paths: list[str] | None = None,
        max_tracked_files: int = 200,
        max_tracked_mib: int = 64,
        block_device_reads: bool = True,
        lint_after_edit: bool = True,
    ):
        self._protected_paths = frozenset(
            protected_paths
            if protected_paths is not None
            else FileToolsConfig().protected_paths
        )
        self._max_tracked_files = max_tracked_files
        self._max_tracked_bytes = max_tracked_mib * 1024 * 1024
        self._block_device_reads = block_device_reads
        self._lint_after_edit = lint_after_edit
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        # Legacy alias: extra_allowed_dirs is read-only. Write-capable tools
        # must opt in via extra_write_allowed_dirs.
        self._extra_read_allowed_dirs = [
            *(extra_allowed_dirs or []),
            *(extra_read_allowed_dirs or []),
        ]
        self._extra_write_allowed_dirs = list(extra_write_allowed_dirs or [])
        self._extra_write_allowed_files = list(extra_write_allowed_files or [])
        self._restrict_to_workspace = (
            bool(restrict_to_workspace)
            if restrict_to_workspace is not None
            else allowed_dir is not None
        )
        self._sandbox_restricts_workspace = sandbox_restricts_workspace
        # Explicit state is used by isolated runners like Dream/subagents.
        # Main AgentLoop tools leave this unset and resolve state from the
        # current async task, which keeps shared tool instances session-safe.
        self._explicit_file_states = file_states
        self._fallback_file_states = FileStates()

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        from navin.agent.skills import BUILTIN_SKILLS_DIR

        restrict = bool(ctx.config.restrict_to_workspace)
        allowed_dir = Path(ctx.workspace) if restrict else None
        extra_read = [BUILTIN_SKILLS_DIR]
        return cls(
            workspace=Path(ctx.workspace),
            allowed_dir=allowed_dir,
            extra_read_allowed_dirs=extra_read,
            file_states=ctx.file_state_store,
            restrict_to_workspace=restrict,
            sandbox_restricts_workspace=False,
            protected_paths=ctx.config.file.protected_paths,
            max_tracked_files=ctx.config.file.max_tracked_files,
            max_tracked_mib=ctx.config.file.max_tracked_mib,
            block_device_reads=ctx.config.file.block_device_reads,
            lint_after_edit=ctx.config.file.lint_after_edit,
        )

    async def _with_diagnostics(self, message: str, paths: list[Path]) -> str:
        """Append what the linters say about ``paths``, when there is anything.

        Off the event loop: the linters are subprocesses, and this now runs on
        every edit rather than only when the model asks, so blocking here would
        stall the gateway and every other session with it.
        """
        if not self._lint_after_edit:
            return message
        from navin.agent.tools.edit_feedback import diagnostics_after_write

        report = await asyncio.to_thread(
            diagnostics_after_write, paths, workspace=self._workspace
        )
        return f"{message}\n\n{report}" if report else message

    @property
    def _file_states(self) -> FileStates:
        if self._explicit_file_states is not None:
            return self._explicit_file_states
        return current_file_states(self._fallback_file_states)

    def _effective_allowed_root(self, access_allowed_root: Path | None) -> Path | None:
        if self._allowed_dir is None or self._workspace is None:
            return access_allowed_root
        try:
            allowed_dir = Path(self._allowed_dir).expanduser().resolve(strict=False)
            workspace = Path(self._workspace).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            return access_allowed_root if access_allowed_root is not None else self._allowed_dir
        if allowed_dir == workspace:
            return access_allowed_root
        return allowed_dir

    def _resolve_with_extra(
        self,
        path: str,
        extra_allowed_dirs: list[Path] | None,
        extra_allowed_files: list[Path] | None,
        *,
        include_media_dir: bool,
    ) -> Path:
        access = current_tool_workspace(
            self._workspace,
            restrict_to_workspace=self._restrict_to_workspace,
            sandbox_restricts_workspace=self._sandbox_restricts_workspace,
        )
        lifted_dirs, lifted_files = _split_approved_paths()
        extra_dirs = [*(extra_allowed_dirs or []), *lifted_dirs]
        extra_files = [*(extra_allowed_files or []), *lifted_files]
        resolved = resolve_workspace_path(
            path,
            access.project_path,
            self._effective_allowed_root(access.allowed_root),
            extra_dirs,
            extra_files,
            include_media_dir=include_media_dir,
        )
        # After the boundary check, never before: the extended-length prefix
        # would defeat the containment comparison it is applied to.
        io_resolved = longpath.io_path(resolved)
        if is_runtime_secret_path(io_resolved) or is_runtime_secret_path(resolved):
            raise PermissionError(RUNTIME_CONFIG_DENIED)
        return io_resolved

    def _resolve_read(self, path: str) -> Path:
        return self._resolve_with_extra(
            path,
            self._extra_read_allowed_dirs,
            None,
            include_media_dir=True,
        )

    def _resolve_write(self, path: str) -> Path:
        return self._resolve_with_extra(
            path,
            self._extra_write_allowed_dirs,
            self._extra_write_allowed_files,
            include_media_dir=False,
        )

    def _resolve(self, path: str) -> Path:
        return self._resolve_read(path)

    async def _bound_path(self, path: str, *, write: bool = False) -> Path:
        """Resolve *path*, asking in the chat when it sits outside the project.

        Restrict-to-workspace stays the default. A silent error was indistinguishable
        from a hang; a card in the thread is the only user block.
        """
        try:
            return self._resolve_write(path) if write else self._resolve_read(path)
        except WorkspaceBoundaryError as exc:
            if not await self._ask_outside_workspace(path, write=write):
                raise PermissionError(
                    f"{exc}. The user refused access to this path."
                ) from exc
            remembered = Path(path).expanduser()
            if not remembered.is_absolute() and self._workspace is not None:
                remembered = Path(self._workspace) / remembered
            try:
                remembered = remembered.resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                pass
            remember_approved_path(remembered)
            return self._resolve_write(path) if write else self._resolve_read(path)

    async def _ask_outside_workspace(self, path: str, *, write: bool) -> bool:
        from navin.agent.approval import ApprovalRequest, request_approval

        decision = await request_approval(ApprovalRequest(
            tool="write_file" if write else "read_file",
            action=(
                "Write a file outside the current project"
                if write
                else "Read a file outside the current project"
            ),
            reason=(
                "Restrict to workspace is on. Allow this location for the rest "
                "of the chat?"
            ),
            detail=path,
            consequence=(
                "The agent uses your user permissions at this path. "
                "Secrets and other projects become reachable."
            ),
            scope=f"workspace:{path}",
            allow_when_unattended=False,
        ))
        return decision.allowed

    def _display_workspace(self) -> Path | None:
        return current_tool_workspace(self._workspace).project_path

    def _missing_path_msg(self, kind: str, path: str, resolved: Path) -> str:
        """Report a missing path with the nearest names that do exist.

        A bare "not found" leaves the agent guessing, and guessing shows up as a
        run of failed calls. Naming the closest entries, or the deepest part of
        the path that does exist, gives it something to act on.
        """
        parts = [f"Error: {kind} not found: {path}"]
        parent = resolved.parent
        if parent.is_dir():
            try:
                names = [entry.name for entry in parent.iterdir()]
            except OSError:
                names = []
            close = difflib.get_close_matches(resolved.name, names, n=3, cutoff=0.5)
            if close:
                parts.append(
                    "Did you mean: " + ", ".join(self._display(parent / c) for c in close) + "?",
                )
        else:
            existing = parent
            while existing != existing.parent and not existing.is_dir():
                existing = existing.parent
            if existing.is_dir():
                parts.append(
                    f"Deepest existing directory: {self._display(existing)} "
                    "(list it to see what is actually there).",
                )
        return ToolResult.error("\n".join(parts))

    def _missing_project_file(self, rel: str, root: Path) -> str:
        """Report a project-relative file that is absent, with near matches."""
        return self._missing_path_msg("File", rel, root / rel)

    def _display(self, path: Path) -> str:
        """Show a path relative to the project when it sits inside it."""
        workspace = self._display_workspace()
        if workspace is not None:
            try:
                rel = path.relative_to(Path(workspace).expanduser().resolve(strict=False))
            except ValueError:
                return str(path)
            return "the project root" if str(rel) == "." else str(rel)
        return str(path)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/tty", "/dev/console",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re
    import stat
    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(r"/proc/self/fd/[012]$", resolved):
        return True

    # The rest of /dev is judged by what it is, not where it lives: /dev/null
    # reads as instant EOF, /dev/shm is an ordinary tmpfs that programs use
    # for scratch files, and neither can hang a read. What hangs is a
    # character or block device or a FIFO, so only those are refused.
    if resolved.startswith("/dev/"):
        if resolved == "/dev/null" or resolved.startswith("/dev/shm/"):
            return False
        try:
            mode = os.stat(resolved).st_mode
        except OSError:
            # Missing or unstatable: the read itself will fail with an honest
            # error, which beats a warning about a hang that cannot happen.
            return False
        return not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
    return False


def _builtin_skill_read_path(path: str) -> Path | None:
    """Map workspace-relative skills/<name>/... reads onto bundled skills."""
    from navin.agent.skills import BUILTIN_SKILLS_DIR

    requested = Path(path)
    if requested.is_absolute():
        return None
    parts = requested.parts
    # Custom skills live in .navin/skills; leftover .navin/skill is still
    # falls back to the bundled copy.
    if len(parts) >= 2 and parts[0] == ".navin" and parts[1] in {"skill", "skills"}:
        parts = parts[1:]
    if len(parts) < 2 or parts[0] not in {"skill", "skills"}:
        return None
    root = BUILTIN_SKILLS_DIR.resolve()
    candidate = (root / Path(*parts[1:])).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema("Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"),
        force=BooleanSchema(
            description="Bypass same-file read deduplication and return content again.",
            default=False,
        ),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""
    _scopes = {"core", "subagent", "memory"}

    _MAX_CHARS = 128_000
    # Default window for text reads. Every extra window is one more model call
    # (~1.7 s and a full prompt re-send), so the window must cover the
    # ordinary source file in one read; 1 000 lines does for the vast majority.
    # Claude Code reads 2 000 lines by default. Minified bundles and data
    # files are held in check by the per-line cap, not by a small window.
    _DEFAULT_LIMIT = 1000
    _LARGE_FILE_LINES = 1000
    # A line longer than this is cut: it is a minified bundle, a data blob or
    # a base64 payload, never something the model needs whole.
    _MAX_LINE_CHARS = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Supports PDF, DOCX, XLSX, PPTX documents. "
            "Prefer code_index / grep with a path or glob before reading. "
            "Use find_files/list_dir first when the path is uncertain. "
            "Read the relevant range before editing so replacements or patches "
            "are based on current content. "
            "Default window is 1000 lines - pass offset and limit for larger files. "
            "Lines over 2000 chars are cut. "
            "Use force=true to re-read content even if unchanged. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    @classmethod
    def _clip_line(cls, line: str) -> str:
        if len(line) <= cls._MAX_LINE_CHARS:
            return line
        omitted = len(line) - cls._MAX_LINE_CHARS
        return line[: cls._MAX_LINE_CHARS] + f" (line truncated, {omitted} chars omitted)"

    async def execute(
        self,
        path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
        pages: str | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            if not path:
                return ToolResult.error("Error reading file: Unknown path")

            if self._block_device_reads and _is_blocked_device(path):
                return ToolResult.error(f"Error: Reading {path} is blocked (device path that could hang or produce infinite output). Set tools.file.blockDeviceReads=false to allow it.")

            fp = await self._bound_path(path, write=False)
            if not fp.exists():
                fp = _builtin_skill_read_path(path) or fp
            if self._block_device_reads and _is_blocked_device(fp):
                return ToolResult.error(f"Error: Reading {fp} is blocked (device path that could hang or produce infinite output). Set tools.file.blockDeviceReads=false to allow it.")
            if not fp.exists():
                recovered = self._closest_readable_match(fp)
                if recovered is not None:
                    note = (
                        f"[Note: {path} not found; reading closest match "
                        f"{self._display(recovered)} instead.]\n"
                    )
                    result = await self.execute(
                        path=str(recovered), offset=offset, limit=limit,
                        pages=pages, force=force,
                    )
                    if isinstance(result, ToolResult) and result.is_error:
                        return ToolResult.error(note + result)
                    return note + result if isinstance(result, str) else result
                return self._missing_path_msg("File", path, fp)
            if not fp.is_file():
                return ToolResult.error(f"Error: Not a file: {path}")

            # PDF support
            if fp.suffix.lower() == ".pdf":
                return self._read_pdf(fp, pages)

            # Office document support
            if fp.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                return self._read_office_doc(fp)

            read_mtime = fp.stat().st_mtime
            raw = fp.read_bytes()
            if not raw:
                self._file_states.record_read(fp, offset=offset, limit=limit, content=raw, mtime=read_mtime)
                return f"(Empty file: {path})" + scoped_project_instructions(self._display_workspace(), fp)

            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if mime and mime.startswith("image/"):
                return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")

            # Reuse the bytes already read for MIME detection, deduplication
            # and edit freshness. Reopening and hashing the same file four
            # times made network-mounted and large workspaces feel stalled.
            entry = self._file_states.get(fp)
            if (
                not force
                and entry
                and entry.can_dedup
                and entry.offset == offset
                and entry.limit == limit
                and read_mtime == entry.mtime
                and _hash_content(raw) == entry.content_hash
            ):
                return f"[File unchanged since last read: {path}]" + scoped_project_instructions(self._display_workspace(), fp)

            # Decoding also strips any BOM and normalizes CRLF -> LF before
            # line-splitting. CRLF is primarily a Windows concern (git checkouts
            # with autocrlf, editors saving CRLF) but is normalized on all
            # platforms so downstream StrReplace/Grep behavior is consistent
            # regardless of where the file was written.
            decoded = text_decode.decode(raw)
            if decoded is None:
                # Binary file - return error message
                mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
                if mime and mime.startswith("image/"):
                    return build_image_content_blocks(raw, mime, str(fp), f"(Image file: {path})")
                return ToolResult.error(f"Error: Cannot read binary file {path} (MIME: {mime or 'unknown'}). Only text and images are supported.")

            text_content = decoded.text
            if fp.suffix.lower() == ".ipynb":
                from navin.agent.tools.notebook import render_notebook

                text_content = render_notebook(text_content) or text_content

            all_lines = text_content.splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if offset > total:
                return ToolResult.error(f"Error: offset {offset} is beyond end of file ({total} lines)")

            effective_limit = limit if limit is not None else self._DEFAULT_LIMIT
            start = offset - 1
            end = min(start + effective_limit, total)
            numbered = [
                f"{start + i + 1}| {self._clip_line(line)}"
                for i, line in enumerate(all_lines[start:end])
            ]
            result = "\n".join(numbered)
            large_hint = ""
            if (
                limit is None
                and total > self._LARGE_FILE_LINES
                and end < total
            ):
                large_hint = (
                    f"(Large file: {total} lines. Prefer code_index or grep to "
                    f"locate symbols, then read_file with offset/limit. "
                    f"Default window is {self._DEFAULT_LIMIT} lines.)\n\n"
                )
            if large_hint:
                result = large_hint + result

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    chars += len(line) + 1
                    if chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                if not trimmed:
                    # A first line wider than the whole budget would otherwise
                    # yield an empty window whose "use offset=N to continue"
                    # points back at itself, and the agent replays the same
                    # call forever. Emit that line truncated instead, so the
                    # window always advances by at least one line.
                    first = numbered[0]
                    omitted = len(first) - self._MAX_CHARS
                    trimmed = [
                        first[: self._MAX_CHARS]
                        + f" (line truncated, {omitted} chars omitted)"
                    ]
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file - {total} lines total)"
            self._file_states.record_read(fp, offset=offset, limit=limit, content=raw, mtime=read_mtime)
            return result + scoped_project_instructions(self._display_workspace(), fp)
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error reading file: {e}")

    def _closest_readable_match(self, fp: Path) -> Path | None:
        """Unambiguous stand-in for a missing read path, or None.

        Reading is non-destructive, so a clearly-flagged substitution beats a
        dead-end error. See ``closest_existing_match`` for the exact rules.
        """
        workspace = self._display_workspace()
        root = Path(workspace).expanduser() if workspace is not None else None
        return closest_existing_match(fp, root)

    def _read_pdf(self, fp: Path, pages: str | None) -> str:
        from navin.utils.document import PdfPageRangeError, PdfSafetyError, extract_pdf_pages

        try:
            extraction = extract_pdf_pages(
                fp,
                pages=pages,
                max_pages=self._MAX_PDF_PAGES,
                max_chars=self._MAX_CHARS,
            )
        except PdfPageRangeError:
            return ToolResult.error(f"Error: Invalid page range '{pages}'. Use format like '1-5'.")
        except PdfSafetyError as e:
            return ToolResult.error(f"Error reading PDF: {e}")
        except Exception as e:
            return ToolResult.error(f"Error reading PDF: {e}")

        if not extraction.text:
            return self._read_scanned_pdf(fp, extraction)

        result = extraction.text
        if extraction.end_page < extraction.total_pages - 1:
            next_start = extraction.end_page + 2
            next_end = min(extraction.end_page + 1 + self._MAX_PDF_PAGES, extraction.total_pages)
            result += (
                f"\n\n(Showing pages {extraction.start_page + 1}-{extraction.end_page + 1} "
                f"of {extraction.total_pages}. Use pages='{next_start}-{next_end}' to continue.)"
            )
        return result

    def _read_scanned_pdf(self, fp: Path, extraction: Any) -> Any:
        """Pages of a text-less PDF as images the model reads directly.

        A scanned document used to come back as "no extractable text", which
        the model relayed as if the file were empty. Rendering the requested
        page range (six pages at a time) lets a vision model read it, and the
        note tells it how to page further.
        """
        from navin.utils.pdf_pages import (
            DEFAULT_MAX_PAGES,
            describe_pdf_pages,
            expand_scanned_pdf,
        )

        pages = expand_scanned_pdf(
            fp,
            total_pages=extraction.total_pages,
            max_pages=DEFAULT_MAX_PAGES,
            first_page=extraction.start_page + 1,
        )
        if not pages.ok:
            return f"(PDF has no extractable text: {fp}. {pages.reason})"

        blocks: list[dict[str, Any]] = []
        for path, number in zip(pages.paths, pages.page_numbers, strict=False):
            try:
                raw = Path(path).read_bytes()
            except OSError:
                continue
            blocks.extend(
                build_image_content_blocks(raw, "image/png", path, f"(Page {number} of {fp.name})")
            )
        note = describe_pdf_pages(pages)
        last = pages.page_numbers[-1]
        if last < pages.total_pages:
            next_end = min(last + DEFAULT_MAX_PAGES, pages.total_pages)
            note += f"\nUse pages='{last + 1}-{next_end}' to continue."
        blocks.append({"type": "text", "text": note})
        return blocks

    def _read_office_doc(self, fp: Path) -> str:
        from navin.utils.document import extract_text

        result = extract_text(fp)

        if result is None:
            return ToolResult.error(f"Error: Unsupported file format: {fp.suffix}")

        if result.startswith("[error:"):
            return ToolResult.error(f"Error reading {fp.suffix.upper()} file: {result}")

        if not result:
            return f"({fp.suffix.upper().lstrip('.')} has no extractable text: {fp})"

        if len(result) > self._MAX_CHARS:
            result = result[:self._MAX_CHARS] + "\n\n(Document text truncated at ~128K chars)"

        return result


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to write to"),
        content=StringSchema("The content to write"),
        required=["path", "content"],
    )
)
class WriteFileTool(_FsTool):
    """Write content to a file."""
    _scopes = {"core", "subagent", "memory"}

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a new file or intentionally replace an entire file with "
            "the provided content. Creates parent directories as needed. "
            "Available in every agent mode except Ask. For small/partial code "
            "edits prefer apply_patch; use edit_file for small exact "
            "replacements. write_file is ideal for new docs and full rewrites."
        )

    def _overwrite_warnings(self, fp: Path) -> list[str]:
        """What the agent should know before this file stops existing as it was."""
        if not fp.exists():
            return []
        warnings: list[str] = []
        stale = self._file_states.check_read(fp)
        if stale:
            warnings.append(stale)
        root = self._display_workspace()
        note = uncommitted_note(root, fp) if root else ""
        if note:
            warnings.append(note)
        return warnings

    async def execute(self, path: str | None = None, content: str | None = None, **kwargs: Any) -> str:
        try:
            if not path:
                raise ValueError("Unknown path")
            if content is None:
                raise ValueError("Unknown content")
            fp = await self._bound_path(path, write=True)
            # Replacing a whole file is the only edit with no anchor text to
            # fail against, so a stale or unread target is caught here or not
            # at all.
            warnings = self._overwrite_warnings(fp)
            record_file_before(fp)
            fp.parent.mkdir(parents=True, exist_ok=True)
            layout = _existing_layout(fp)
            fp.write_bytes(encode_for(_match_line_endings(content, layout), layout))
            self._file_states.record_write(fp)
            written = f"Successfully wrote {len(content)} characters to {fp}"
            summary = "\n".join([*warnings, written]) if warnings else written
            return await self._with_diagnostics(summary, [fp])
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error writing file: {e}")


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

_QUOTE_TABLE = str.maketrans({
    "\u2018": "'", "\u2019": "'",  # curly single → straight
    "\u201c": '"', "\u201d": '"',  # curly double → straight
    "'": "'", '"': '"',            # identity (kept for completeness)
})


def _normalize_quotes(s: str) -> str:
    return s.translate(_QUOTE_TABLE)


def _curly_double_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            parts.append("\u201c" if opening else "\u201d")
            opening = not opening
        else:
            parts.append(ch)
    return "".join(parts)


def _curly_single_quotes(text: str) -> str:
    parts: list[str] = []
    opening = True
    for i, ch in enumerate(text):
        if ch != "'":
            parts.append(ch)
            continue
        prev_ch = text[i - 1] if i > 0 else ""
        next_ch = text[i + 1] if i + 1 < len(text) else ""
        if prev_ch.isalnum() and next_ch.isalnum():
            parts.append("\u2019")
            continue
        parts.append("\u2018" if opening else "\u2019")
        opening = not opening
    return "".join(parts)


def _preserve_quote_style(old_text: str, actual_text: str, new_text: str) -> str:
    """Preserve curly quote style when a quote-normalized fallback matched."""
    if _normalize_quotes(old_text.strip()) != _normalize_quotes(actual_text.strip()) or old_text == actual_text:
        return new_text

    styled = new_text
    if any(ch in actual_text for ch in ("\u201c", "\u201d")) and '"' in styled:
        styled = _curly_double_quotes(styled)
    if any(ch in actual_text for ch in ("\u2018", "\u2019")) and "'" in styled:
        styled = _curly_single_quotes(styled)
    return styled


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _reindent_like_match(old_text: str, actual_text: str, new_text: str) -> str:
    """Preserve the outer indentation from the actual matched block."""
    old_lines = old_text.split("\n")
    actual_lines = actual_text.split("\n")
    if len(old_lines) != len(actual_lines):
        return new_text

    comparable = [
        (old_line, actual_line)
        for old_line, actual_line in zip(old_lines, actual_lines)
        if old_line.strip() and actual_line.strip()
    ]
    if not comparable or any(
        _normalize_quotes(old_line.strip()) != _normalize_quotes(actual_line.strip())
        for old_line, actual_line in comparable
    ):
        return new_text

    old_ws = _leading_ws(comparable[0][0])
    actual_ws = _leading_ws(comparable[0][1])
    if actual_ws == old_ws:
        return new_text

    if old_ws:
        if not actual_ws.startswith(old_ws):
            return new_text
        delta = actual_ws[len(old_ws):]
    else:
        delta = actual_ws

    if not delta:
        return new_text

    return "\n".join((delta + line) if line else line for line in new_text.split("\n"))


@dataclass(slots=True)
class _MatchSpan:
    start: int
    end: int
    text: str
    line: int


def _match_end_line(match: _MatchSpan) -> int:
    comparable = match.text[:-1] if match.text.endswith("\n") else match.text
    return match.line + comparable.count("\n")


def _match_covers_line(match: _MatchSpan, line: int) -> bool:
    return match.line <= line <= _match_end_line(match)


def _find_exact_matches(content: str, old_text: str) -> list[_MatchSpan]:
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = content.find(old_text, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(old_text))
    return matches


def _find_trim_matches(content: str, old_text: str, *, normalize_quotes: bool = False) -> list[_MatchSpan]:
    old_lines = old_text.splitlines()
    if not old_lines:
        return []

    content_lines = content.splitlines()
    content_lines_keepends = content.splitlines(keepends=True)
    if len(content_lines) < len(old_lines):
        return []

    offsets: list[int] = []
    pos = 0
    for line in content_lines_keepends:
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)

    if normalize_quotes:
        stripped_old = [_normalize_quotes(line.strip()) for line in old_lines]
    else:
        stripped_old = [line.strip() for line in old_lines]

    matches: list[_MatchSpan] = []
    window_size = len(stripped_old)
    for i in range(len(content_lines) - window_size + 1):
        window = content_lines[i : i + window_size]
        if normalize_quotes:
            comparable = [_normalize_quotes(line.strip()) for line in window]
        else:
            comparable = [line.strip() for line in window]
        if comparable != stripped_old:
            continue

        start = offsets[i]
        end = offsets[i + window_size]
        if content_lines_keepends[i + window_size - 1].endswith("\n"):
            end -= 1
        matches.append(
            _MatchSpan(
                start=start,
                end=end,
                text=content[start:end],
                line=i + 1,
            )
        )
    return matches


def _find_quote_matches(content: str, old_text: str) -> list[_MatchSpan]:
    norm_content = _normalize_quotes(content)
    norm_old = _normalize_quotes(old_text)
    matches: list[_MatchSpan] = []
    start = 0
    while True:
        idx = norm_content.find(norm_old, start)
        if idx == -1:
            break
        matches.append(
            _MatchSpan(
                start=idx,
                end=idx + len(old_text),
                text=content[idx : idx + len(old_text)],
                line=content.count("\n", 0, idx) + 1,
            )
        )
        start = idx + max(1, len(norm_old))
    return matches


def _find_matches(content: str, old_text: str) -> list[_MatchSpan]:
    """Locate all matches using progressively looser strategies."""
    for matcher in (
        lambda: _find_exact_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text),
        lambda: _find_trim_matches(content, old_text, normalize_quotes=True),
        lambda: _find_quote_matches(content, old_text),
    ):
        matches = matcher()
        if matches:
            return matches
    return []


def _collapse_internal_whitespace(text: str) -> str:
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def _diagnose_near_match(old_text: str, actual_text: str) -> list[str]:
    """Return actionable hints describing why text was close but not exact."""
    hints: list[str] = []

    if old_text.lower() == actual_text.lower() and old_text != actual_text:
        hints.append("letter case differs")
    if _collapse_internal_whitespace(old_text) == _collapse_internal_whitespace(actual_text) and old_text != actual_text:
        hints.append("whitespace differs")
    if old_text.rstrip("\n") == actual_text.rstrip("\n") and old_text != actual_text:
        hints.append("trailing newline differs")
    if _normalize_quotes(old_text) == _normalize_quotes(actual_text) and old_text != actual_text:
        hints.append("quote style differs")

    return hints


def _best_window(old_text: str, content: str) -> tuple[float, int, list[str], list[str]]:
    """Find the closest line-window match and return ratio/start/snippet/hints."""
    lines = content.splitlines(keepends=True)
    old_lines = old_text.splitlines(keepends=True)
    window = max(1, len(old_lines))

    best_ratio, best_start = -1.0, 0
    best_window_lines: list[str] = []

    for i in range(max(1, len(lines) - window + 1)):
        current = lines[i : i + window]
        ratio = difflib.SequenceMatcher(None, old_lines, current).ratio()
        if ratio > best_ratio:
            best_ratio, best_start = ratio, i
            best_window_lines = current

    actual_text = "".join(best_window_lines).replace("\r\n", "\n").rstrip("\n")
    hints = _diagnose_near_match(old_text.replace("\r\n", "\n").rstrip("\n"), actual_text)
    return best_ratio, best_start, best_window_lines, hints


def _find_match(content: str, old_text: str) -> tuple[str | None, int]:
    """Locate old_text in content with a multi-level fallback chain:

    1. Exact substring match
    2. Line-trimmed sliding window (handles indentation differences)
    3. Smart quote normalization (curly ↔ straight quotes)

    Both inputs should use LF line endings (caller normalises CRLF).
    Returns (matched_fragment, count) or (None, 0).
    """
    matches = _find_matches(content, old_text)
    if not matches:
        return None, 0
    return matches[0].text, len(matches)


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to edit"),
        old_text=StringSchema("The text to find and replace"),
        new_text=StringSchema("The text to replace with"),
        replace_all=BooleanSchema(description="Replace all occurrences (default false)"),
        occurrence=IntegerSchema(
            1,
            description="Optional 1-based occurrence to replace when old_text appears multiple times.",
            minimum=1,
            nullable=True,
        ),
        line_hint=IntegerSchema(
            1,
            description=(
                "Optional exact 1-based target line copied from read_file. "
                "The selected old_text match must cover this line."
            ),
            minimum=1,
            nullable=True,
        ),
        expected_replacements=IntegerSchema(
            1,
            description="Optional guard for the number of replacements that must be made.",
            minimum=1,
            nullable=True,
        ),
        required=["path", "old_text", "new_text"],
    )
)
class EditFileTool(_FsTool):
    """Edit a file by replacing text with fallback matching."""
    _scopes = {"core", "subagent", "memory"}

    _MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB
    _MARKDOWN_EXTS = frozenset({".md", ".mdx", ".markdown"})

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Perform a small, exact replacement in one file by replacing "
            "old_text with new_text. Use this for narrow text substitutions "
            "with old_text copied from read_file. Keep old_text to the smallest "
            "unique span (a few lines, never a whole function): with line_hint "
            "set to the target line, one line is enough. For multi-file, "
            "structural, or generated code edits, prefer apply_patch. If "
            "old_text matches multiple times, set occurrence, line_hint, "
            "replace_all, or expected_replacements rather than pasting more "
            "context. Shows closest-match diagnostics on failure."
        )

    @staticmethod
    def _strip_trailing_ws(text: str) -> str:
        """Blank a line that is only indentation, and leave content lines alone.

        Whitespace at the end of an otherwise empty line is always accidental
        and every linter flags it. After a line with content it can be the point
        of the edit - fixture data, a snapshot, a patch body - so removing it
        would write bytes the caller did not ask for.
        """
        return "\n".join("" if not line.strip() else line for line in text.split("\n"))

    async def execute(
        self, path: str | None = None, old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False, occurrence: int | None = None,
        line_hint: int | None = None, expected_replacements: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            # Named explicitly: "Unknown old_text" reads as a rejected value
            # rather than a missing argument, and the usual cause is a call that
            # used another tool's parameter names.
            if not path:
                raise ValueError("edit_file requires 'path'")
            if old_text is None:
                raise ValueError(
                    "edit_file requires 'old_text' (the exact text to replace)"
                )
            if new_text is None:
                raise ValueError(
                    "edit_file requires 'new_text' (may be an empty string to delete)"
                )
            if occurrence is not None and occurrence < 1:
                return ToolResult.error("Error: occurrence must be >= 1.")
            if line_hint is not None and line_hint < 1:
                return ToolResult.error("Error: line_hint must be >= 1.")
            if expected_replacements is not None and expected_replacements < 1:
                return ToolResult.error("Error: expected_replacements must be >= 1.")

            fp = await self._bound_path(path, write=True)
            if fp.suffix.lower() == ".ipynb" and fp.exists():
                # read_file shows cells, not the JSON the anchor would have to
                # match; the cell tool is the only edit that lines up with it.
                return ToolResult.error(
                    "Error: use notebook_edit for Jupyter notebooks (cells are numbered by read_file)."
                )
            record_file_before(fp)

            # Create-file semantics: old_text='' + file doesn't exist → create
            if not fp.exists():
                if old_text == "":
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(new_text, encoding="utf-8")
                    self._file_states.record_write(fp)
                    return await self._with_diagnostics(f"Successfully created {fp}", [fp])
                return self._file_not_found_msg(path, fp)

            # File size protection
            try:
                fsize = fp.stat().st_size
            except OSError:
                fsize = 0
            if fsize > self._MAX_EDIT_FILE_SIZE:
                return ToolResult.error(f"Error: File too large to edit ({fsize / (1024**3):.1f} GiB). Maximum is 1 GiB.")

            # Create-file: old_text='' but file exists and not empty → reject
            if old_text == "":
                raw = fp.read_bytes()
                probe = text_decode.decode(raw)
                content = probe.text if probe is not None else ""
                if content.strip() or (probe is None and raw):
                    return ToolResult.error(f"Error: Cannot create file - {path} already exists and is not empty.")
                fp.write_text(new_text, encoding="utf-8")
                self._file_states.record_write(fp)
                return await self._with_diagnostics(f"Successfully edited {fp}", [fp])

            # Read-before-edit check
            warning = self._file_states.check_read(fp)

            raw = fp.read_bytes()
            decoded = text_decode.decode(raw)
            if decoded is None:
                return ToolResult.error(
                    f"Error: Cannot edit binary file {path}. Only text files can be edited."
                )
            content = decoded.text
            norm_old = old_text.replace("\r\n", "\n")
            matches = _find_matches(content, norm_old)

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if replace_all and occurrence is not None:
                return ToolResult.error("Error: occurrence cannot be used with replace_all=true.")
            if replace_all and line_hint is not None:
                return ToolResult.error("Error: line_hint cannot be used with replace_all=true.")
            if occurrence is not None and line_hint is not None:
                return ToolResult.error("Error: line_hint cannot be used with occurrence.")
            if occurrence is not None and occurrence > count:
                return ToolResult.error(
                    f"Error: occurrence {occurrence} is out of range; "
                    f"old_text appears {count} time(s)."
                )
            if count > 1 and not replace_all and occurrence is None and line_hint is None:
                line_numbers = [match.line for match in matches]
                preview = ", ".join(f"line {n}" for n in line_numbers[:3])
                if len(line_numbers) > 3:
                    preview += ", ..."
                location_hint = f" at {preview}" if preview else ""
                # Nothing was edited, so this must read as a failure: a plain
                # string here lets is_error consumers (fail_on_tool_error,
                # retries, stats) believe the edit happened. apply_patch
                # already treats the same ambiguity as an error.
                return ToolResult.error(
                    f"Error: old_text appears {count} times{location_hint}. "
                    "Provide more context, set occurrence to choose one match, "
                    "or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")

            # Trailing whitespace stripping (skip markdown to preserve double-space line breaks)
            if fp.suffix.lower() not in self._MARKDOWN_EXTS:
                norm_new = self._strip_trailing_ws(norm_new)

            if replace_all:
                selected = matches
            elif occurrence is not None:
                selected = [matches[occurrence - 1]]
            elif line_hint is not None:
                candidates = [match for match in matches if _match_covers_line(match, line_hint)]
                if not candidates:
                    locations = ", ".join(f"line {match.line}" for match in matches[:3])
                    if len(matches) > 3:
                        locations += ", ..."
                    return ToolResult.error(
                        f"Error: line_hint {line_hint} does not match the old_text location. "
                        f"old_text appears at {locations}. Re-read the intended region and "
                        "copy old_text that covers the target line."
                    )
                if len(candidates) > 1:
                    return ToolResult.error(
                        f"Error: line_hint {line_hint} is ambiguous; "
                        f"old_text appears {len(candidates)} times on that line."
                    )
                selected = candidates
            else:
                selected = [matches[0]]
            if expected_replacements is not None and len(selected) != expected_replacements:
                return ToolResult.error(
                    f"Error: expected {expected_replacements} replacements but "
                    f"would make {len(selected)}."
                )
            new_content = content
            for match in reversed(selected):
                replacement = _preserve_quote_style(norm_old, match.text, norm_new)
                replacement = _reindent_like_match(norm_old, match.text, replacement)

                # Delete-line cleanup: when deleting text (new_text=''), consume trailing
                # newline to avoid leaving a blank line
                end = match.end
                if replacement == "" and not match.text.endswith("\n") and content[end:end + 1] == "\n":
                    end += 1

                new_content = new_content[: match.start] + replacement + new_content[end:]

            # Re-encode the way the file was stored, so editing one line of a
            # cp1252 or BOM-prefixed file does not rewrite every other line.
            fp.write_bytes(text_decode.encode(new_content, decoded))
            self._file_states.record_write(fp)
            msg = f"Successfully edited {fp}"
            if warning:
                msg = f"{warning}\n{msg}"
            return await self._with_diagnostics(msg, [fp])
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error editing file: {e}")

    def _file_not_found_msg(self, path: str, fp: Path) -> str:
        """Build an error message with 'Did you mean ...?' suggestions."""
        parent = fp.parent
        suggestions: list[str] = []
        if parent.is_dir():
            siblings = [f.name for f in parent.iterdir() if f.is_file()]
            close = difflib.get_close_matches(fp.name, siblings, n=3, cutoff=0.6)
            suggestions = [str(parent / c) for c in close]
        parts = [f"Error: File not found: {path}"]
        if suggestions:
            parts.append("Did you mean: " + ", ".join(suggestions) + "?")
        return ToolResult.error("\n".join(parts))

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        best_ratio, best_start, best_window_lines, hints = _best_window(old_text, content)
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                best_window_lines,
                fromfile="old_text (provided)",
                tofile=f"{path} (actual, line {best_start + 1})",
                lineterm="",
            ))
            hint_text = ""
            if hints:
                hint_text = "\nPossible cause: " + ", ".join(hints) + "."
            return ToolResult.error(
                f"Error: old_text not found in {path}."
                f"{hint_text}\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
            )

        if hints:
            return ToolResult.error(
                f"Error: old_text not found in {path}. "
                f"Possible cause: {', '.join(hints)}. "
                "Copy the exact text from read_file and try again."
            )
        return ToolResult.error(f"Error: old_text not found in {path}. No similar text found. Verify the file content.")


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The directory path to list"),
        recursive=BooleanSchema(description="Recursively list all files (default false)"),
        max_entries=IntegerSchema(
            200,
            description="Maximum entries to return (default 200)",
            minimum=1,
        ),
        required=["path"],
    )
)
class ListDirTool(_FsTool):
    """List directory contents with optional recursion."""
    _scopes = {"core", "subagent"}

    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", ".coverage", "htmlcov", ".next", ".nuxt", ".cache",
        "target", "vendor", ".turbo", ".svelte-kit", ".output", ".gradle",
        ".terraform",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory (project tree). "
            "Set recursive=true for a nested tree of files and folders. "
            "Available in every agent mode/module. "
            "Heavy trees (node_modules, .git, build, ...) are listed at the "
            "point they appear but not walked, so the call stays fast."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self, path: str | None = None, recursive: bool = False,
        max_entries: int | None = None, **kwargs: Any,
    ) -> str:
        try:
            if path is None:
                raise ValueError("Unknown path")
            dp = await self._bound_path(path, write=False)
            if not dp.exists():
                return self._missing_path_msg("Directory", path, dp)
            if not dp.is_dir():
                return ToolResult.error(f"Error: Not a directory: {path}")

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            truncated = False

            if recursive:
                for dirpath, dirnames, filenames in os.walk(dp):
                    current = Path(dirpath)
                    if current != dp:
                        rel = current.relative_to(dp).as_posix() + "/"
                        if len(items) >= cap:
                            truncated = True
                            dirnames.clear()
                            break
                        items.append(rel)
                    dirnames.sort()
                    kept: list[str] = []
                    for name in dirnames:
                        child_rel = (
                            name if current == dp
                            else (current.relative_to(dp) / name).as_posix()
                        )
                        if name in self._IGNORE_DIRS:
                            if len(items) >= cap:
                                truncated = True
                                break
                            items.append(f"{child_rel}/")
                        else:
                            kept.append(name)
                    if truncated:
                        dirnames.clear()
                        break
                    dirnames[:] = kept
                    for name in sorted(filenames):
                        if len(items) >= cap:
                            truncated = True
                            dirnames.clear()
                            break
                        rel = (
                            name if current == dp
                            else (current.relative_to(dp) / name).as_posix()
                        )
                        items.append(rel)
                    if truncated:
                        break
            else:
                try:
                    children = sorted(
                        dp.iterdir(),
                        key=lambda item: (not item.is_dir(), item.name.lower()),
                    )
                except OSError as exc:
                    return ToolResult.error(f"Error listing directory: {exc}")
                for item in children:
                    if len(items) >= cap:
                        truncated = True
                        break
                    pfx = "📁 " if item.is_dir() else "📄 "
                    items.append(f"{pfx}{item.name}")

            if not items:
                return f"Directory {path} is empty" + scoped_project_instructions(self._display_workspace(), dp, directory=True)

            result = "\n".join(items)
            if truncated:
                result += f"\n\n(truncated, showing first {cap} entries)"
            return result + scoped_project_instructions(self._display_workspace(), dp, directory=True)
        except PermissionError as e:
            return ToolResult.error(f"Error: {e}")
        except Exception as e:
            return ToolResult.error(f"Error listing directory: {e}")
