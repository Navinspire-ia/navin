# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Keep code edits and their validation together across execution slices."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from navin.quality.check_budget import current_budget
from navin.quality.evidence import VerificationEvidence
from navin.utils import wsl
from navin.utils.proc import no_window_kwargs

_CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts",
    ".vue", ".svelte", ".go", ".rs", ".java", ".kt", ".kts", ".cs", ".fs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".m", ".mm", ".rb", ".php",
    ".ex", ".exs", ".erl", ".hs", ".scala", ".clj", ".dart", ".lua", ".sql",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".r", ".jl", ".pl",
})
# Component markup and styling: a type check / build proves them, and a test
# rarely covers "the sidebar looks like ChatGPT now". Requiring tests for them
# sent a restyle hunting browser suites, installing Playwright and rewriting
# another feature's test to get a green run. Checks are still required.
_UI_SUFFIXES = frozenset({".tsx", ".jsx", ".vue", ".svelte", ".astro"})
# Ops scripts (deploy, expose, install) have no unit tests: a syntax check is the
# proof. Requiring a test run stopped finished subagents on an error status.
_SCRIPT_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd"})
_TEST_REQUIRED_SUFFIXES = _CODE_SUFFIXES - _UI_SUFFIXES - _SCRIPT_SUFFIXES
_CHECK_SUFFIXES = _CODE_SUFFIXES | {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".css", ".scss",
    ".sass", ".less", ".graphql", ".proto", ".tf", ".hcl",
}
_CHECK_FILENAMES = frozenset({"dockerfile", "makefile", "cmakelists.txt", "justfile"})
_RUNTIME_STATE_FILES = frozenset({"cron/jobs.json"})
_EDIT_TOOLS = frozenset({"apply_patch", "edit_file", "write_file", "manage_files"})
_NO_TESTS = re.compile(
    r"no tests (?:were )?(?:ran|found|collected|to run)|(?:collected|ran) 0 (?:items|tests)"
    r"|\b0 passed\b|\[no test files\]|^# pass 0\b",
    re.IGNORECASE | re.MULTILINE,
)
_SESSION_ID = re.compile(r"\bsession_id:\s*([\w.-]+)")
_EXIT_CODE = re.compile(r"\bExit code: (-?\d+)")
_PYTEST_COUNT = r"\d+ (?:passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)"
_PYTEST_SUMMARY = re.compile(
    rf"^[= \t]*(?P<counts>{_PYTEST_COUNT}(?:,\s*{_PYTEST_COUNT})*)"
    r" in \d+(?:\.\d+)?s(?: \([\d:]+\))?[= \t]*$", re.MULTILINE,
)
_GENERATED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".navin", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".next", ".nuxt", ".cache", ".turbo", "dist", "build", "target", "vendor",
})


# Lists the files and stamps them inside the distribution, in one process, so a
# Windows host watching a \\wsl.localhost project pays one wsl.exe start
# instead of a 9P round trip per directory and per file.
#
# `%Y` keeps the epoch seconds as an integer and `%y` carries the nanosecond
# suffix in its human-readable form.  `%.9Y` looks simpler, but GNU stat
# formats that directive through a floating-point value and loses low bits for
# current epoch timestamps.
_WSL_STAT_COMMAND = "stat --printf='%Y %y %s %n\\0' --"
_WSL_SNAPSHOT_SCRIPT = (
    "export LC_ALL=C; "
    "if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then "
    "git ls-files -z --cached --others --exclude-standard -- .; "
    "else find . -mindepth 1 -type d \\( "
    + " -o ".join(f"-name {shlex.quote(name)}" for name in sorted(_GENERATED_DIRS))
    + " \\) -prune -o -type f -printf '%P\\0'; fi"
    " | xargs -0 -r "
    + _WSL_STAT_COMMAND
)
_WSL_STAMP_SCRIPT = "export LC_ALL=C; " + _WSL_STAT_COMMAND + " \"$@\""
_WSL_SNAPSHOT_TIMEOUT_S = 60


def _wsl_root(root: Path) -> wsl.WslLocation | None:
    if os.name != "nt":
        return None
    return wsl.parse_unc(str(root))


def _parse_wsl_stamps(raw: bytes) -> dict[str, tuple[int, int]] | None:
    stamps: dict[str, tuple[int, int]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            # `%Y` is exact epoch seconds. `%y` is split into fields so only
            # its textual fractional seconds are converted to nanoseconds;
            # no timestamp passes through a floating-point value.
            seconds, _date, clock, _zone, size, name = os.fsdecode(record).split(" ", 5)
            _whole, separator, fraction = clock.partition(".")
            if not separator or not fraction or len(fraction) > 9 or not fraction.isdecimal():
                raise ValueError
            stamps[name.removeprefix("./")] = (
                int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0")),
                int(size),
            )
        except ValueError:
            return None  # an older stat without nanoseconds: let the caller fall back
    return stamps


def _run_in_wsl(location: wsl.WslLocation, argv: list[str]) -> bytes | None:
    distro = wsl.resolve_distro(location.distro) or location.distro
    try:
        result = subprocess.run(  # noqa: S603
            [*wsl.command_prefix(distro, location.path), *argv],
            capture_output=True, timeout=_WSL_SNAPSHOT_TIMEOUT_S, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # stat exits 1 (xargs 123) when a listed file vanished mid-run; the rest
    # of the output is still good.
    return result.stdout if result.returncode in (0, 1, 123) else None


def _wsl_snapshot(location: wsl.WslLocation) -> dict[str, tuple[int, int] | None] | None:
    raw = _run_in_wsl(location, ["sh", "-c", _WSL_SNAPSHOT_SCRIPT])
    stamps = _parse_wsl_stamps(raw) if raw is not None else None
    if stamps is None:
        return None
    return {
        path: stamp for path, stamp in stamps.items()
        if development_path(path) and not _GENERATED_DIRS.intersection(PurePosixPath(path).parts)
    }


def workspace_code_snapshot(root: Path) -> dict[str, tuple[int, int] | None]:
    """Observe shell/delegated edits without reading file contents or dependencies.

    Git supplies its ignore rules when available. New projects also work before
    git init. The runner calls this off the event loop and only around tools
    that can edit arbitrary paths, not on each read in a long audit.
    """
    location = _wsl_root(root)
    if location is not None:
        # Windows git refuses a WSL-owned repo (dubious ownership), and the
        # os.walk fallback then stats the whole tree over 9P: minutes per
        # exec in a measured desktop session, against a second from inside.
        snapshot = _wsl_snapshot(location)
        if snapshot is not None:
            return snapshot
    # Plain string work per file: on a 3,700-file repo, building a PurePosixPath
    # and a Path for each one cost more than the stat calls themselves, and
    # this runs twice around every exec.
    snapshot = {}
    root_text = str(root)
    for path, raw in _development_files(root):
        try:
            stat = os.stat(os.path.join(root_text, raw))
            snapshot[path] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            snapshot[path] = None
    return snapshot


# The file list behind a snapshot changes only when a folder gains, loses or
# renames an entry (that folder's mtime moves), when the git index moves (add,
# rm, checkout, commit, stash) or when an ignore file changes. Checking those
# stamps is a few hundred stats; `git ls-files` is a process plus a full index
# read, and the snapshot runs twice around every exec that may edit files (36
# to 75 ms each on 4,500 to 9,900 files, 2026-11-02). The files themselves are
# still stat'ed every time: an edit in place moves no folder.
_RACY_NS = 2_000_000_000  # FAT stamps to 2 s: a stamp this close to the listing is never trusted
_MAX_WATCHED_DIRS = 20_000
_MAX_LISTINGS = 32
_GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


@dataclass(frozen=True)
class _Listing:
    files: tuple[tuple[str, str], ...]  # ("/" path, path as listed) of the development files
    stamps: tuple[tuple[str, int, int], ...] | None  # None: not reusable
    # A tree whose stamps could not be taken (too many folders, git refused)
    # is listed plainly until then, without trying again on every snapshot.
    plain_until: float = 0.0


_LISTINGS: dict[str, _Listing] = {}
_LISTINGS_LOCK = threading.Lock()
_PLAIN_LISTING_S = 600.0


def _development_files(root: Path) -> tuple[tuple[str, str], ...]:
    key = str(root)
    with _LISTINGS_LOCK:
        listing = _LISTINGS.get(key)
    if listing is not None and listing.stamps is not None and _stamps_hold(listing.stamps):
        return listing.files
    plain = listing is not None and listing.stamps is None and time.monotonic() < listing.plain_until
    fresh = _list_files(root, stamped=not plain)
    with _LISTINGS_LOCK:
        if plain:
            pass  # keep the plain marker until it expires
        elif fresh.stamps is None and not fresh.plain_until:
            _LISTINGS.pop(key, None)
        else:
            if len(_LISTINGS) >= _MAX_LISTINGS and key not in _LISTINGS:
                _LISTINGS.pop(next(iter(_LISTINGS)))
            _LISTINGS[key] = fresh
    return fresh.files


def _stamp(path: str) -> tuple[int, int]:
    try:
        stat = os.stat(path)
    except OSError:
        return (-1, -1)
    return (stat.st_mtime_ns, stat.st_size)


def _stamps_hold(stamps: tuple[tuple[str, int, int], ...]) -> bool:
    return all(_stamp(path) == (mtime, size) for path, mtime, size in stamps)


def _git_listing(root: Path) -> list[str] | None:
    """``git ls-files`` of ``root``, or None when git cannot list it.

    None is an answer, not a lost error: no git, not a repository or a
    timeout all mean the caller walks the folder itself instead.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
            cwd=root, capture_output=True, timeout=5, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def _list_files(root: Path, *, stamped: bool = True) -> _Listing:
    # Taken before the listing: a folder that moves while git reads it is
    # newer than this and the listing is not kept.
    listed_ns = time.time_ns()
    paths = _git_listing(root)
    walked: list[str] | None = None
    if paths is None:
        paths, walked = [], []
        for current, directories, names in os.walk(root):
            directories[:] = [name for name in directories if name not in _GENERATED_DIRS]
            walked.append(current)
            paths.extend(str((Path(current) / name).relative_to(root)) for name in names)
    files = tuple(
        (path, raw) for raw in paths if _is_development_file(path := raw.replace("\\", "/"))
    )
    if not stamped:
        return _Listing(files=files, stamps=None)
    stamps = _listing_stamps(root, paths, walked, listed_ns)
    if stamps is None:
        return _Listing(files=files, stamps=None, plain_until=time.monotonic() + _PLAIN_LISTING_S)
    if not stamps:
        return _Listing(files=files, stamps=None)  # a folder moved just now: list again next time
    return _Listing(files=files, stamps=stamps)


def _racy(stamps: Iterable[tuple[str, int, int]], listed_ns: int) -> bool:
    return any(mtime >= listed_ns - _RACY_NS for _path, mtime, _size in stamps)


def _listing_stamps(
    root: Path, paths: list[str], walked: list[str] | None, listed_ns: int,
) -> tuple[tuple[str, int, int], ...] | None:
    """Every folder and file whose change can change the listing.

    None when the tree cannot be stamped (kept plain for a while), () when a
    folder changed too recently to trust its stamp (the agent just wrote a
    file there). That second answer is found from the listed folders alone,
    before the walk for unlisted ones, so a busy folder costs a few stats
    more than the plain listing, never a walk.
    """
    root_text = str(root)
    folders: set[str] = {root_text}
    marks: list[str] = []
    if walked is not None:
        # The walk read every folder it keeps: their stamps cover all of it.
        folders.update(walked)
    else:
        git_marks = _git_marks(root)
        if git_marks is None:
            return None
        marks.extend(git_marks)
        relative: set[str] = set()
        for raw in paths:
            path = raw.replace("\\", "/")
            if path.rsplit("/", 1)[-1] == ".gitignore":
                marks.append(os.path.join(root_text, raw))
            parent = path.rpartition("/")[0]
            while parent and parent not in relative:
                relative.add(parent)
                parent = parent.rpartition("/")[0]
        folders.update(os.path.join(root_text, folder) for folder in relative)
    if len(folders) > _MAX_WATCHED_DIRS:
        return None
    stamps = tuple((path, *_stamp(path)) for path in (*sorted(folders), *marks))
    if _racy(stamps, listed_ns):
        return ()
    if walked is not None:
        return stamps
    # A folder git lists nothing under (empty, or only ignored files) starts
    # to count once a file lands in it: watch those folders too, down to the
    # ones git ignores (their content is never listed).
    unlisted = _unlisted_folders(root, folders)
    if unlisted is None:
        return None
    extra = tuple((path, *_stamp(path)) for path in sorted(unlisted))
    if _racy(extra, listed_ns):
        return ()
    return stamps + extra


def _child_folders(folders: Iterable[str], known: set[str]) -> list[str] | None:
    """Sub-folders of ``folders`` not in ``known``; None when one cannot be read.

    A folder that vanished since the listing has no children to watch (its
    own stamp reads as missing). One that exists but cannot be listed (no
    permission) could hide files git sees later: the tree is then not
    cached at all.
    """
    children: list[str] = []
    for folder in folders:
        try:
            with os.scandir(folder) as entries:
                children.extend(
                    entry.path for entry in entries
                    if entry.name not in _GENERATED_DIRS
                    and entry.is_dir(follow_symlinks=False)
                    and entry.path not in known
                )
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return None
    return children


def _unlisted_folders(root: Path, listed: set[str]) -> set[str] | None:
    """Folders without a listed file that git does not ignore, level by level."""
    found: set[str] = set()
    level = _child_folders(listed, listed)
    while level:
        if len(listed) + len(found) + len(level) > _MAX_WATCHED_DIRS:
            return None
        ignored = _git_ignored(root, level)
        if ignored is None:
            return None
        kept = [folder for folder in level if folder not in ignored]
        found.update(kept)
        level = _child_folders(kept, listed | found)
    if level is None:
        return None
    return found


def _git_ignored(root: Path, folders: list[str]) -> set[str] | None:
    """The folders among ``folders`` that the ignore rules exclude."""
    names = {os.path.relpath(folder, root).replace("\\", "/"): folder for folder in folders}
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-z", "--stdin"],
            cwd=root, input=b"\0".join(os.fsencode(name) for name in names),
            capture_output=True, timeout=5, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode not in (0, 1):  # 1: nothing ignored
        return None
    return {
        names[name] for name in (os.fsdecode(raw).rstrip("/") for raw in result.stdout.split(b"\0") if raw)
        if name in names
    }


def _git_marks(root: Path) -> list[str] | None:
    """The index and the ignore files that rule ``git ls-files`` for ``root``."""
    if any(os.environ.get(name) for name in _GIT_REDIRECTS):
        return None
    ignores: list[str] = []
    for base in (root, *root.parents):
        if base != root:
            ignores.append(str(base / ".gitignore"))
        dot_git = base / ".git"
        if dot_git.is_dir():
            git_dir = common = dot_git
            break
        if dot_git.is_file():
            # A worktree or a submodule: ".git" names the real folder.
            try:
                line = dot_git.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                return None
            if not line.startswith("gitdir:"):
                return None
            git_dir = (base / line.removeprefix("gitdir:").strip()).resolve()
            # A worktree names its shared repository in "commondir"; a
            # submodule has none and is its own repository.
            common = git_dir
            commondir = git_dir / "commondir"
            if commondir.is_file():
                try:
                    common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
                except (OSError, UnicodeDecodeError):
                    return None  # unreadable: this tree is listed plainly
            break
    else:
        return None
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [
        str(git_dir / "index"), str(common / "info" / "exclude"),
        os.path.join(config_home, "git", "ignore"), *ignores,
    ]


def _is_development_file(path: str) -> bool:
    """``development_path`` and not under a generated folder, for a "/" path."""
    if path.lstrip("./").lower() in _RUNTIME_STATE_FILES:
        # Navin's own scheduler store, rewritten at runtime: not an agent edit.
        return False
    name = path.rsplit("/", 1)[-1].lower()
    dot = name.rfind(".")
    suffix = name[dot:] if 0 < dot < len(name) - 1 else ""
    if suffix not in _CHECK_SUFFIXES and name not in _CHECK_FILENAMES:
        return False
    return not _GENERATED_DIRS.intersection(path.split("/"))


# Commands that only read: no snapshot around them. Anything not clearly in
# this list (or with a redirect, substitution, background job...) still gets
# one, so a missed edit is never the price of the shortcut.
_READ_ONLY_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "printf", "grep", "egrep", "fgrep",
    "rg", "which", "type", "whoami", "date", "env", "printenv", "uname", "stat", "file",
    "du", "df", "tree", "realpath", "dirname", "basename", "id", "hostname", "ps", "sleep",
    "true", "false", "cd", "less", "more", "sort", "uniq", "cut", "nl", "diff", "cmp",
    "md5sum", "sha1sum", "sha256sum", "jq", "column", "test", "[",
})
_READ_ONLY_GIT = frozenset({"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "describe"})
# Redirects that write no file: stderr into stdout, or output thrown away.
_HARMLESS_REDIRECT = re.compile(r"(?<![\w>&])(?:[12]?>&[12]|(?:&|[12])?>>?\s*/dev/null)(?![\w/.-])")


def _shell_segments(command: str) -> list[str] | None:
    """``command`` split on unquoted ``;``, ``|``, ``||`` and ``&&``.

    None when it holds anything that may write or run hidden code outside
    single quotes: a redirect to a file, ``$(...)``, backticks, ``<(...)``, a
    background ``&`` or a second line. Quotes are honoured, so the pipes of
    ``grep -E "a|b"`` are not separators: the old regex split them and
    took ``cat x; ls`` for an edit, which handed a parallel chat's changes
    to a turn that only read a password file (desktop log, 2026-10-31).
    """
    text = _HARMLESS_REDIRECT.sub(" ", command)
    segments: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            quote = "" if char == "'" else quote
            current.append(char)
            index += 1
            continue
        if char == "\\":
            current.append(text[index:index + 2])
            index += 2
            continue
        if char == "`" or text.startswith("$(", index):
            return None
        if quote == '"':
            quote = "" if char == '"' else quote
            current.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
        elif char in ">\n" or text.startswith("<(", index):
            return None
        elif char in ";|" or text.startswith("&&", index):
            segments.append("".join(current))
            current = []
            index += 2 if text.startswith(("||", "&&"), index) else 1
            continue
        elif char == "&":
            return None
        current.append(char)
        index += 1
    if quote:
        return None
    segments.append("".join(current))
    return segments


_GIT_TREE_MOVES = frozenset({"switch", "reset", "pull", "merge", "rebase", "stash", "stash_pop", "restore", "clone"})
_GIT_TREE_MOVE_COMMAND = re.compile(
    r"(?:^|&&|;|\|\|)\s*git\s+(?:-C\s+\S+\s+)?(?:checkout|switch|reset|pull|merge|rebase|stash|restore|cherry-pick|revert|am)\b"
)


def moves_worktree(name: str, arguments: Any) -> bool:
    """True when a call replaces the working tree with existing commits (checkout, reset...)."""
    if not isinstance(arguments, dict):
        return False
    if name == "git":
        return arguments.get("action") in _GIT_TREE_MOVES
    if name == "exec":
        command = arguments.get("command") or arguments.get("cmd")
        return isinstance(command, str) and bool(_GIT_TREE_MOVE_COMMAND.search(command))
    return False


def exec_cannot_edit(arguments: Any) -> bool:
    """True when an exec call clearly cannot write a file (``ls``, ``git status``...)."""
    if not isinstance(arguments, dict) or arguments.get("background"):
        return False
    command = arguments.get("command") or arguments.get("cmd")
    if not isinstance(command, str) or not command.strip():
        return False
    segments = _shell_segments(command)
    if segments is None:
        return False
    programs = 0
    for segment in segments:
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            # ``cat a; echo;`` leaves an empty tail: nothing runs there.
            continue
        programs += 1
        program = tokens[0].rsplit("/", 1)[-1]
        args = tokens[1:]
        if program == "env" and args:
            # ``env FOO=1 rm x`` runs the command after it.
            return False
        if program == "sort" and any(arg == "-o" or arg.startswith(("-o", "--output")) for arg in args):
            return False
        if program in _READ_ONLY_PROGRAMS:
            continue
        if program == "git" and args and args[0] in _READ_ONLY_GIT and "--output" not in " ".join(args):
            continue
        if program == "find" and not {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"} & set(args):
            continue
        if program == "sed" and not any(arg.startswith("-i") or arg.startswith("--in-place") for arg in args):
            continue
        if len(args) == 1 and args[0] in {"--version", "-v", "-V", "version", "--help", "-h"}:
            continue
        return False
    return programs > 0


# Files written with a file tool, by chat, across every chat of the process.
# The owner is the chat's FileStates (its subagents share it). A tree change
# another chat made with its own file tools is that chat's work: counting it
# here asked a turn that edited nothing to test and build files it never
# touched (two chats on one project, desktop log of 2026-10-31).
_CHAT_WRITE_WINDOW_S = 1800.0
_CHAT_WRITES_MAX = 4096
_CHAT_WRITES: dict[str, tuple[object, float]] = {}
_CHAT_WRITES_LOCK = threading.Lock()


def _write_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _chat_owner() -> object | None:
    from navin.agent.tools.file_state import bound_file_states

    return bound_file_states()


def note_chat_writes(root: Path, paths: set[str], owner: object | None = None) -> None:
    """Remember that the running chat wrote ``paths`` (relative to ``root`` or absolute)."""
    owner = owner if owner is not None else _chat_owner()
    if owner is None or not paths:
        return
    now = time.monotonic()
    with _CHAT_WRITES_LOCK:
        for path in paths:
            candidate = Path(path)
            _CHAT_WRITES[_write_key(candidate if candidate.is_absolute() else Path(root) / candidate)] = (owner, now)
        while len(_CHAT_WRITES) > _CHAT_WRITES_MAX:
            _CHAT_WRITES.pop(next(iter(_CHAT_WRITES)))


def written_by_other_chats(root: Path, paths: set[str], owner: object | None = None) -> set[str]:
    """The ``paths`` (relative to ``root``) another chat last wrote with a file tool, recently.

    "Another" is anyone but ``owner`` and the agents it started: a sibling
    subagent or the parent chat counts as another writer for a subagent.
    """
    owner = owner if owner is not None else _chat_owner()
    if owner is None or not paths:
        return set()
    now = time.monotonic()
    foreign: set[str] = set()
    with _CHAT_WRITES_LOCK:
        for path in paths:
            entry = _CHAT_WRITES.get(_write_key(Path(root) / path))
            if entry is not None and not _written_by(entry[0], owner) and now - entry[1] < _CHAT_WRITE_WINDOW_S:
                foreign.add(path)
    return foreign


def _written_by(writer: object, owner: object) -> bool:
    """``owner`` wrote it, itself or through an agent it started."""
    descends = getattr(writer, "descends_from", None)
    return writer is owner or (callable(descends) and bool(descends(owner)))


# Logic files a run must change before a test run is the proof. Below that (a
# one or two file fix, a copy or config edit) a passing check of the changed
# files - lint, type check, build - is enough: the user asked that tests stay
# for real development work and that small changes only build what they touch.
_COMPLEX_CHANGE_FILES = 3
_SMALL_CHANGE_NOTE = (
    "Small change: a passing check of the changed files (verify or lint) is enough. "
    "Tests are required for multi-file logic changes or when a test file is edited."
)
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})


def _is_test_file(path: str) -> bool:
    candidate = PurePosixPath(path.lower())
    name = candidate.name
    return (
        name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_spec.rb"))
        or ".test." in name or ".spec." in name
        or bool(_TEST_DIRS & set(candidate.parts[:-1]))
    )


def _complex_change(logic_paths: set[str]) -> bool:
    """Whether the logic changed so far needs a test run, not only a check.

    An edited test has to be run; several logic files changed together are
    real development work with a final test. One or two files are a fix whose
    proof is the check of those files.
    """
    if len(logic_paths) >= _COMPLEX_CHANGE_FILES:
        return True
    return any(_is_test_file(path) for path in logic_paths)


def _requires_tests(path: str) -> bool:
    """Logic code needs a test run; UI markup only needs checks, unless the
    edited file is itself a test (then it has to be run)."""
    candidate = PurePosixPath(path.lower())
    if candidate.suffix in _TEST_REQUIRED_SUFFIXES:
        return True
    if candidate.suffix in _UI_SUFFIXES or candidate.suffix in _SCRIPT_SUFFIXES:
        name = candidate.name
        return ".test." in name or ".spec." in name or "__tests__" in candidate.parts
    return False


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def edit_paths(params: dict[str, Any]) -> set[str]:
    paths = set()
    for key in ("path", "file_path", "source", "destination", "src", "dst"):
        if isinstance(params.get(key), str):
            paths.add(params[key].replace("\\", "/"))
    for value in params.get("paths", []) or []:
        if isinstance(value, str):
            paths.add(value.replace("\\", "/"))
    for key in ("edits", "files"):  # files: write_file writing several at once
        for edit in params.get(key, []) or []:
            if isinstance(edit, dict):
                paths.update(edit_paths(edit))
    return paths


def _is_mutation(name: str, params: dict[str, Any]) -> bool:
    """Whether this tool call writes files (a dry run or a mkdir does not count)."""
    action = params.get("action")
    mutation = name in _EDIT_TOOLS and not params.get("dry_run")
    if name == "manage_files" and action in {"mkdir", "list", "stat"}:
        mutation = False
    if name == "lsp" and action == "rename" and params.get("apply"):
        mutation = True
    if name in {"verify", "lint"} and action in {"fix", "rollback"}:
        mutation = True
    return mutation


def development_path(path: str) -> bool:
    candidate = PurePosixPath(path.replace("\\", "/").lower())
    return candidate.suffix in _CHECK_SUFFIXES or candidate.name in _CHECK_FILENAMES


def python_test_script(command: str) -> bool:
    """A direct Python test script still needs an executed-test summary."""
    try:
        tokens = shlex.split(command.replace("\\", "/"))
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        if not re.fullmatch(r"(?:python[0-9.]*|pypy[0-9.]*|py)(?:\.exe)?", PurePosixPath(token).name):
            continue
        args = tokens[index + 1:]
        while args and args[0] in {"-B", "-u", "-E", "-s", "-S", "-I", "-3"}:
            args.pop(0)
        if args:
            name = PurePosixPath(args[0]).name
            return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))
    return False


def _script_tests_ran(text: str) -> bool:
    if re.search(r"\b[1-9]\d* (?:passed|passing)\b", text, re.I):
        return True
    ran = re.search(r"\bRan ([1-9]\d*) tests?\b", text)
    skipped = re.search(r"\bskipped=(\d+)\b", text)
    return bool(ran and re.search(r"\bOK\b", text) and int(ran[1]) > (int(skipped[1]) if skipped else 0))


def _output_filter(stage: list[str]) -> bool:
    """tail/tee on the runner's own output keep its final summary intact."""
    program, args = PurePosixPath(stage[0]).name, stage[1:]
    if program == "tail":
        return (not args or (len(args) == 1 and bool(re.fullmatch(r"-\d+|-n\d+|--lines=\d+", args[0])))
                or (len(args) == 2 and args[0] in {"-n", "--lines"} and args[1].isdigit()))
    return program == "tee"


def _filtered_pytest_command(command: str, is_test_command: Callable[[str], bool]) -> bool:
    """Recognize output-only pipelines whose pytest summary can prove the result.

    The shell exit belongs to tail/tee, so it is never enough by itself. Only
    these filters preserve a completed summary; arbitrary commands, head and
    tail reading a different file cannot supply validation evidence.
    """
    try:
        lexer = shlex.shlex(command.replace("\\", "/"), posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    stages: list[list[str]] = [[]]
    index = 0
    while index < len(tokens):
        if tokens[index:index + 3] == ["2", ">&", "1"] and len(stages) == 1:
            index += 3
            continue
        token = tokens[index]
        if token == "|":
            stages.append([])
        elif token == "&&" and len(stages) == 1:
            if len(stages[0]) < 2 or stages[0][0] != "cd":
                return False
            stages[0] = []
        elif token in {";", "&", "&&", "||", "<", ">", ">>", ">&", "|&"}:
            return False
        else:
            stages[-1].append(token)
        index += 1
    if len(stages) < 2 or not all(stages) or not is_test_command(shlex.join(stages[0])):
        return False
    if not any(PurePosixPath(token).name in {"pytest", "py.test", "pytest.exe", "py.test.exe"} for token in stages[0]):
        return False
    return all(_output_filter(stage) for stage in stages[1:])


# Steps a test chain may hold besides the runs themselves: none of them can
# write code or print an older result back into the output.
_CHAIN_STEPS = frozenset({"cd", "echo", "printf", "true", ":", "pwd", "sleep", "date", "set", "export"})
_CHAIN_REFUSED = re.compile(r"`|\$\(|<\(|>\(|\n|\b\d+ (?:passed|failed|total)\b")


def _runner_kind(tokens: list[str]) -> str:
    names = [PurePosixPath(token).name.lower().removesuffix(".exe") for token in tokens]
    if {"pytest", "py.test"} & set(names) or any(
        names[index:index + 2] == ["-m", "pytest"] for index in range(len(names))
    ):
        return "pytest"
    if {"vitest", "jest"} & set(names):
        return "js"
    if {"npm", "pnpm", "yarn", "bun"} & set(names) and any(
        name in {"test", "t"} or name.startswith("test:") for name in names
    ):
        return "js"
    return "other"


def _chain_step(tokens: list[str]) -> bool:
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return True
    program, args = PurePosixPath(tokens[0]).name, tokens[1:]
    if program in _CHAIN_STEPS:
        return True
    # ``source .venv/bin/activate`` before the run.
    return program in {"source", "."} and len(args) == 1 and PurePosixPath(args[0]).name == "activate"


def chained_test_kinds(command: str, is_test_command: Callable[[str], bool]) -> list[str] | None:
    """Runner kinds of a command made only of test runs and harmless steps.

    ``cd api && pytest -q ; echo == ; cd ../web && npx vitest run`` is one
    chain: every ``;``/``&&`` part is a test run (optionally piped through
    tail/tee) or a step such as cd/echo that cannot edit code or replay an old
    log. Fallbacks (``||``), background jobs, substitutions, redirections and
    a summary typed into the command itself are refused, so only the runners'
    own summaries can decide the result. None when the command is not one.
    """
    if _CHAIN_REFUSED.search(command):
        return None
    try:
        lexer = shlex.shlex(command.replace("\\", "/"), posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    pipelines: list[list[list[str]]] = [[[]]]
    index = 0
    while index < len(tokens):
        if tokens[index:index + 3] in (["2", ">&", "1"], ["2", ">", "/dev/null"]):
            index += 3
            continue
        token = tokens[index]
        if token in {";", "&&"}:
            pipelines.append([[]])
        elif token == "|":
            pipelines[-1].append([])
        elif token and set(token) <= set(";&|<>"):
            return None
        else:
            pipelines[-1][-1].append(token)
        index += 1
    if len(pipelines) > 1 and pipelines[-1] == [[]]:
        pipelines.pop()  # trailing ``;``
    kinds = []
    for pipeline in pipelines:
        if not all(pipeline):
            return None
        head, filters = pipeline[0], pipeline[1:]
        if is_test_command(shlex.join(head)):
            if not all(_output_filter(stage) for stage in filters):
                return None
            kinds.append(_runner_kind(head))
        elif filters or not _chain_step(head):
            return None
    return kinds or None


def exec_runs_only_tests(arguments: Any, is_test_command: Callable[[str], bool]) -> bool:
    """True when an exec call only runs pytest/JS tests, plus cd/echo steps.

    Files such a run rewrites (a generated module, a fixture, a snapshot) are
    side effects of the run, not edits by the agent.
    """
    if not isinstance(arguments, dict):
        return False
    command = arguments.get("command") or arguments.get("cmd")
    if not isinstance(command, str) or not command.strip():
        return False
    kinds = chained_test_kinds(command, is_test_command)
    return bool(kinds) and "other" not in kinds


_VITEST_TESTS = re.compile(r"^[ \t]*Tests[ \t]+(?P<counts>\d+ [a-z]+(?: \| \d+ [a-z]+)*) \(\d+\)", re.MULTILINE)
_JEST_TESTS = re.compile(r"^Tests:[ \t]+(?P<counts>(?:\d+ [a-z]+, )*\d+ total)", re.MULTILINE)
_JS_FAILED_GROUP = re.compile(
    r"^[ \t]*(?:Test Files|Test Suites:|Errors)[ \t]+.*\b[1-9]\d* (?:failed|errors?)\b", re.MULTILINE,
)


def _chain_outcome(text: str, kinds: list[str]) -> bool | None:
    """False on any reported failure, True when every run printed a passing summary."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    pytest_runs = [match["counts"] for match in _PYTEST_SUMMARY.finditer(text)]
    js_runs = [match["counts"] for match in (*_VITEST_TESTS.finditer(text), *_JEST_TESTS.finditer(text))]
    if any(re.search(r"\b[1-9]\d* (?:failed|errors?)\b", counts) for counts in pytest_runs + js_runs):
        return False
    if "js" in kinds and _JS_FAILED_GROUP.search(text):
        return False
    if len(pytest_runs) < kinds.count("pytest") or len(js_runs) < kinds.count("js"):
        return None
    if not all(re.search(r"\b[1-9]\d* passed\b", counts) for counts in pytest_runs + js_runs):
        return None
    return True


def _pytest_summary_outcome(text: str) -> bool | None:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    summaries = list(_PYTEST_SUMMARY.finditer(text))
    if not summaries:
        return None
    counts = summaries[-1]["counts"]
    if re.search(r"\b[1-9]\d* (?:failed|errors?)\b", counts):
        return False
    return True if re.search(r"\b[1-9]\d* passed\b", counts) else None


def _legacy_evidence(name: str, params: dict[str, Any], text: str) -> VerificationEvidence:
    """Compatibility for custom quality tools that still return plain strings.

    Built-in tools supply structured evidence, including an explicit empty
    result when no checks ran. A generic PASS here never proves tests ran.
    """
    allowed = {
        "verify": {"check", "fix"},
        "lint": {"file", "changed", "project", "fix"},
        "test_run": {"run"},
    }
    if params.get("action") not in allowed.get(name, set()):
        return VerificationEvidence()
    if _NO_TESTS.search(text):
        return VerificationEvidence(summary=text[:800])
    failed = re.search(r"\bFAIL(?:ED|URE|URES)?\b|\b[1-9]\d* (?:failed|error)", text, re.I)
    passed = re.search(r"\b(?:PASS|CLEAN|OK)\b|\b[1-9]\d* passed\b", text, re.I)
    outcome = False if failed else True if passed else None
    if name == "test_run":
        return VerificationEvidence(tests_ok=outcome, summary=text[:800])
    return VerificationEvidence(checks_ok=outcome, summary=text[:800])


def _file_digest(path: Path) -> str | None:
    """SHA-1 of a file's bytes, None when it cannot be read."""
    try:
        return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()
    except OSError:
        return None


def file_digests(root: Path, paths: set[str]) -> dict[str, str | None]:
    """Digest of each path under ``root``. Blocking: call it off the event loop."""
    return {path: _file_digest(root / path) for path in paths}


def _related_targets(root: Path, paths: list[str]) -> list[str]:
    try:
        from navin.quality.testing import related_test_targets

        # Within the testing budget (Settings > Advanced): one or two files by default.
        return related_test_targets(root, paths, budget=current_budget())
    except Exception:  # noqa: BLE001 - a hint only
        return []


@dataclass(slots=True)
class CodeValidationState:
    """Small, request-local evidence record, independent of transcript compaction."""

    paths: set[str] = field(default_factory=set)
    # Every path the run wrote, docs and command-made files included, whether
    # or not it arms the gates: the file list a subagent hands to its parent.
    touched: set[str] = field(default_factory=set)
    # Changed files that are logic code (see ``_requires_tests``): their count
    # decides between a check and a test run (``_complex_change``).
    logic_paths: set[str] = field(default_factory=set)
    revision: int = 0
    needs_tests: bool = False
    checks_revision: int = -1
    tests_revision: int = -1
    check_failure: str = ""
    test_failure: str = ""
    # A running test validates the revision at launch, not subsequent edits.
    pending_tests: dict[str, int] = field(default_factory=dict)
    pending_script_outputs: dict[str, str] = field(default_factory=dict)
    pending_filtered_outputs: dict[str, str] = field(default_factory=dict)
    # Runner kinds of background chained test commands, by session id.
    pending_chains: dict[str, list[str]] = field(default_factory=dict)
    test_result_note: str = ""
    last_nudge_key: tuple[Any, ...] | None = None
    ignored_nudges: int = 0
    total_nudges: int = 0
    workspace_snapshot: dict[str, tuple[int, int] | None] | None = None
    # Content of files seen changing, so a rewrite with identical bytes (a
    # generator run by the tests, a formatter) is not mistaken for an edit.
    content_hashes: dict[str, str] = field(default_factory=dict)
    root: Path | None = None
    # Whether the project has a test runner at all (detected once per run).
    _has_tests: bool | None = None
    # A command that may write files ran (exec, spawn, a project action that
    # changes the tree). Until then a changed tree is someone else's work: a
    # parallel session or the user, not this agent's edits to validate.
    commands_may_edit: bool = False
    # Tests covering the changed files, keyed by (revision, path count).
    _related_cache: tuple[tuple[int, int], list[str]] | None = None

    def stamp_changes(self, snapshot: dict[str, tuple[int, int] | None]) -> set[str]:
        """Paths whose size or mtime differ from the last observed snapshot."""
        before = self.workspace_snapshot
        if before is None:
            return set()
        return {path for path in before.keys() | snapshot.keys() if before.get(path) != snapshot.get(path)}

    def observe_workspace(
        self, snapshot: dict[str, tuple[int, int] | None], *, root: Path | None = None,
        digests: dict[str, str | None] | None = None,
    ) -> None:
        if root is not None:
            self.root = Path(root)
        changed = self.stamp_changes(snapshot)
        self.workspace_snapshot = snapshot
        if self.root is not None:
            changed -= written_by_other_chats(self.root, changed)
        changed = {path for path in changed if self._content_changed(path, digests)}
        if changed:
            self.edited(changed, require_tests=any(
                _requires_tests(path) for path in changed
            ))

    async def observe_workspace_async(
        self, snapshot: dict[str, tuple[int, int] | None], *, root: Path | None = None,
    ) -> None:
        """``observe_workspace`` with the file reads in a worker thread.

        Hashing the changed files on the event loop froze every chat and
        subagent of the engine at once: up to 35 s with several agents on a
        WSL workspace read from Windows.
        """
        base = Path(root) if root is not None else self.root
        candidates = self.stamp_changes(snapshot)
        digests = None
        if candidates and base is not None:
            digests = await asyncio.to_thread(file_digests, base, candidates)
        self.observe_workspace(snapshot, root=root, digests=digests)

    def _content_changed(self, path: str, digests: dict[str, str | None] | None = None) -> bool:
        if self.root is None:
            return True
        digest = digests[path] if digests is not None and path in digests else _file_digest(self.root / path)
        if digest is None:
            self.content_hashes.pop(path, None)
            return True
        previous = self.content_hashes.get(path)
        self.content_hashes[path] = digest
        return previous != digest

    def sync_known_edits(self, root: Path, paths: set[str], *, owner: object | None = None) -> None:
        """File-tool edits were already observed; do not rediscover them as new.

        They are also this chat's: another chat on the same project must not
        take them for its own edits to validate.
        """
        note_chat_writes(root, set(paths), owner)
        if self.workspace_snapshot is None:
            return
        for path in paths:
            candidate = Path(path)
            if candidate.is_absolute():
                try:
                    path = candidate.relative_to(root).as_posix()
                except ValueError:
                    continue
            if development_path(path):
                self.workspace_snapshot[path] = _file_stamp(root / path)
        location = _wsl_root(root)
        if location is not None:
            # Same clock as workspace_code_snapshot: the 9P view of mtime does
            # not carry the guest's full nanoseconds.
            known = [path for path in paths if path in self.workspace_snapshot]
            raw = _run_in_wsl(location, ["sh", "-c", _WSL_STAMP_SCRIPT, "sh", *known]) if known else None
            stamps = _parse_wsl_stamps(raw) if raw is not None else None
            for path, stamp in (stamps or {}).items():
                self.workspace_snapshot[path] = stamp

    def rebaseline(self, snapshot: dict[str, tuple[int, int] | None], *, root: Path | None = None) -> None:
        """Take the tree as it is now without counting the difference as edits.

        A branch switch, reset, pull or stash rewrites the working tree with
        code the agent did not write: counting it as 56 "changed files" made
        a plain "create a branch from this tag" run the whole test suite and
        refuse to finish on failures that live in the tag itself.
        """
        if root is not None:
            self.root = Path(root)
        self.workspace_snapshot = snapshot
        self.content_hashes.clear()

    def note_tool_writes(self, name: str, params: dict[str, Any], *, status: str) -> None:
        """Record what a successful edit tool wrote, without arming any gate."""
        if status == "ok" and _is_mutation(name, params):
            self.touched.update(edit_paths(params))

    def note_tree_changes(
        self,
        before: dict[str, tuple[int, int] | None],
        after: dict[str, tuple[int, int] | None],
    ) -> None:
        """Record the files a command created, changed or deleted, for the file list only."""
        self.touched.update(
            path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
        )

    def edited(self, paths: set[str], *, require_tests: bool) -> None:
        self.paths.update(paths)
        self.touched.update(paths)
        self.revision += 1
        if require_tests and not self._project_has_tests():
            # No test runner in the project: asking for tests only pushed the
            # agent into extra steps (a fake shell "test", then verify) before
            # it could stop. A green verify is the proof here, as on the board.
            require_tests = False
            self.test_result_note = "No test suite in this project: a passing verify is enough."
        elif require_tests:
            self.logic_paths.update(path for path in paths if _requires_tests(path))
            require_tests = _complex_change(self.logic_paths)
            if not self.needs_tests:
                self.test_result_note = "" if require_tests else _SMALL_CHANGE_NOTE
        else:
            self.test_result_note = ""
        self.needs_tests |= require_tests

    def _in_project(self, path: str) -> bool:
        """Scratch files outside the project (a /tmp probe script) are not the delivery.

        Pure path arithmetic, no disk access: a relative path is in the project.
        """
        if self.root is None:
            return True
        candidate = Path(path)
        if not candidate.is_absolute():
            return True
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        return True

    def _project_has_tests(self) -> bool:
        if self.root is None:
            return True  # unknown project: keep asking for tests
        if self._has_tests is None:
            from navin.quality.verification_log import _project_has_tests

            self._has_tests = _project_has_tests(self.root)
        return self._has_tests

    @property
    def tests_detection_pending(self) -> bool:
        """Whether "does this project have tests" is still to be answered."""
        return self.root is not None and self._has_tests is None

    def prime_project_has_tests(self) -> None:
        """Answer it now, so ``edited`` finds the answer ready.

        The detection lists the tree (a UNC listing and a wsl.exe round trip
        on a WSL project): 1.3 s on the event loop in the desktop log of
        2026-10-27. The runner calls this in a worker thread before it
        observes the tool results of a validating turn.
        """
        self._project_has_tests()

    def record(self, evidence: VerificationEvidence, *, revision: int | None = None) -> None:
        checked_revision = self.revision if revision is None else revision
        for kind, ok in (("checks", evidence.checks_ok), ("tests", evidence.tests_ok)):
            if ok is None:
                continue
            failure_attr = "check_failure" if kind == "checks" else "test_failure"
            if ok:
                # An old test completing after an edit cannot clear a new failure.
                if checked_revision == self.revision:
                    setattr(self, f"{kind}_revision", checked_revision)
                    setattr(self, failure_attr, "")
                    if kind == "tests":
                        self.test_result_note = ""
            else:
                setattr(self, f"{kind}_revision", -1)
                setattr(self, failure_attr, evidence.summary)

    def observe(
        self, name: str, params: dict[str, Any], result: Any, *, status: str,
        require_verify: bool, validate_code: bool,
        is_test_command: Callable[[str], bool],
        execution_revision: int | None = None,
    ) -> None:
        mutation = _is_mutation(name, params)
        if status == "ok" and mutation:
            paths = edit_paths(params)
            outside = {path for path in paths if not self._in_project(path)}
            # Scratch files outside the project (a /tmp probe script) are not
            # the delivery: they never arm the gate on their own.
            self.touched.update(outside)
            scratch_only = bool(paths) and outside == paths
            paths -= outside
            relevant = {path for path in paths if development_path(path)}
            # Docs, notes and other non-development files never arm the gate:
            # no checker covers them, so it could never be satisfied. An edit
            # whose paths are unknown still arms it (the safe side).
            if not scratch_only and (
                ((relevant or not paths) and (require_verify or validate_code))
                or (self.revision and name in {"verify", "lint"})
            ):
                self.edited(paths, require_tests=validate_code and any(
                    _requires_tests(path) for path in relevant
                ))

        evidence = getattr(result, "verification", None)
        if isinstance(evidence, VerificationEvidence):
            self.record(evidence)
            if name == "verify" and status == "ok" and self.revision:
                if evidence.no_test_suite and self.needs_tests:
                    # No suite can see this change: demanding tests would only
                    # push the agent into writing unasked ones, or looping.
                    self.needs_tests = False
                    self.test_result_note = "No test suite covers the changed files."
                if (evidence.checks_ok is None and evidence.tests_ok is None
                        and not self.needs_tests and not self.failed):
                    # verify ran and nothing applies (CSS, HTML, a file with
                    # no linter): that is the verdict, not a missing one.
                    self.checks_revision = self.revision
        elif name in {"verify", "lint", "test_run"}:
            self.record(_legacy_evidence(name, params, str(result or "")))

        if name not in {"exec", "write_stdin"}:
            return
        text = str(result or "")
        revision = self.revision if execution_revision is None else execution_revision
        chain: list[str] | None = None
        if name == "exec":
            command = str(params.get("command") or params.get("cmd") or "")
            filtered_pytest = _filtered_pytest_command(command, is_test_command)
            if not is_test_command(command) and not filtered_pytest:
                # ``pytest -q ; echo == ; npx vitest run``: the exit code is
                # the last step's, so the runners' summaries decide instead.
                chain = chained_test_kinds(command, is_test_command)
                if chain is not None and "other" in chain:
                    chain = None
                if chain is None:
                    if ("|" in command and is_test_command(command.split("|", 1)[0])) or (
                        ";" in command and any(is_test_command(part) for part in command.split(";"))
                    ):
                        self.test_result_note = (
                            "The last test command used an unsupported output filter, fallback or chain. "
                            "Run the tests directly, without a pipe or fallback, so their result can be verified."
                        )
                    return
            session_match = _SESSION_ID.search(text)
            if session_match:
                self.pending_tests[session_match[1]] = revision
                if python_test_script(command):
                    self.pending_script_outputs[session_match[1]] = (text + "\n")[-16000:]
                if filtered_pytest or chain is not None:
                    self.pending_filtered_outputs[session_match[1]] = (text + "\n")[-16000:]
                if chain is not None:
                    self.pending_chains[session_match[1]] = chain
        else:
            session_id = str(params.get("session_id") or "")
            if session_id not in self.pending_tests:
                return
            revision = self.pending_tests[session_id]
            chain = self.pending_chains.get(session_id)
            filtered_pytest = session_id in self.pending_filtered_outputs and chain is None
            if session_id in self.pending_filtered_outputs:
                text = self.pending_filtered_outputs[session_id] + text
                self.pending_filtered_outputs[session_id] = text[-16000:]
            if session_id in self.pending_script_outputs:
                text = self.pending_script_outputs[session_id] + text
                self.pending_script_outputs[session_id] = text[-16000:]
        codes = _EXIT_CODE.findall(text)
        if not codes:
            return
        standalone_script = (
            python_test_script(command) if name == "exec" else session_id in self.pending_script_outputs
        )
        if name == "write_stdin":
            self.pending_tests.pop(session_id, None)
            self.pending_script_outputs.pop(session_id, None)
            self.pending_filtered_outputs.pop(session_id, None)
            self.pending_chains.pop(session_id, None)
        ok = codes[-1] == "0" and status == "ok"
        if chain is not None:
            outcome = _chain_outcome(text, chain)
            if outcome is False or (outcome and ok):
                self.record(VerificationEvidence(tests_ok=bool(outcome), summary=text[-800:]), revision=revision)
            else:
                self.test_result_note = (
                    "The chained test command did not show a complete passing summary for every test run "
                    "it started, or it exited non-zero. Run the tests directly so their result can be verified."
                )
            return
        if filtered_pytest:
            outcome = _pytest_summary_outcome(text)
            if ok and outcome is None:
                self.test_result_note = (
                    "The filtered test output contained no complete pytest result with executed tests. "
                    "Run pytest directly without the output filter."
                )
                return
            self.record(VerificationEvidence(tests_ok=ok and outcome is True, summary=_test_failure_summary(text) if not ok or outcome is False else text[-800:]), revision=revision)
            return
        if standalone_script and ok and not _script_tests_ran(text):
            # Importing a file full of uncalled test functions exits zero too.
            # Only a runner summary proves this standalone script ran tests.
            return
        skipped_only = re.search(r"\b[1-9]\d* skipped\b", text) and not re.search(r"\b[1-9]\d* (?:passed|passing)\b", text)
        if ok and (_NO_TESTS.search(text) or skipped_only):
            return
        self.record(VerificationEvidence(tests_ok=ok, summary=text[-800:] if ok else _test_failure_summary(text)), revision=revision)

    @property
    def pending(self) -> bool:
        if not self.revision:
            return False
        return bool(
            self.check_failure or self.test_failure
            or max(self.checks_revision, self.tests_revision) != self.revision
            or (self.needs_tests and self.tests_revision != self.revision)
        )

    @property
    def failed(self) -> bool:
        return bool(self.check_failure or self.test_failure)

    def missing(self) -> str:
        details = []
        if self.check_failure:
            details.append(self.check_failure)
        if self.test_failure:
            details.append(self.test_failure)
        if self.needs_tests and self.tests_revision != self.revision:
            current_tests = sorted(key for key, revision in self.pending_tests.items() if revision == self.revision)
            if current_tests:
                details.append(
                    "Tests are still running. Use write_stdin to collect their final results "
                    "for session_id: " + ", ".join(current_tests) + ". Do not start duplicate tests."
                )
            else:
                details.append(
                    "Run the tests that cover the changed files (one or two, listed below) once "
                    "after the latest edits: never the whole suite, never a green test again "
                    "without a new edit."
                    if current_budget().essential else
                    "Run the tests that cover the changed files after the latest edits, not the "
                    "whole suite: the full suite is for shared code or a final delivery check."
                )
            if self.test_result_note:
                details.append(self.test_result_note)
            if not current_tests:
                hint = self._targeted_tests_hint()
                if hint:
                    details.append(hint)
            details.append("Wait for the test process to finish, inspect its results, fix failures, then retry closing the task.")
        elif max(self.checks_revision, self.tests_revision) != self.revision:
            details.append("Run an appropriate check after the latest edits.")
            if self.test_result_note:
                details.append(self.test_result_note)
        if self.paths:
            details.append("Changed files: " + ", ".join(sorted(self.paths)[:12]))
        return "\n".join(details)

    def _related_tests(self) -> list[str]:
        """Test files that exercise the changed files (cached per revision)."""
        cached = self._related_cache
        if cached is not None and cached[0] == (self.revision, len(self.paths)):
            return cached[1]
        targets: list[str] = []
        if self.root is not None and self.paths:
            targets = _related_targets(self.root, sorted(self.paths))
        self._related_cache = ((self.revision, len(self.paths)), targets)
        return targets

    async def warm_related_tests(self) -> None:
        """Fill the related-tests cache in a worker thread before ``missing()``.

        The lookup reads every changed module and its tests; on the event loop
        it stalled the whole engine for 21 s.
        """
        key = (self.revision, len(self.paths))
        cached = self._related_cache
        if self.root is None or not self.paths or (cached is not None and cached[0] == key):
            return
        targets = await asyncio.to_thread(_related_targets, self.root, sorted(self.paths))
        self._related_cache = (key, targets)

    def _targeted_tests_hint(self) -> str:
        budget = current_budget()
        targets = self._related_tests() or sorted(path for path in self.paths if (
            PurePosixPath(path).name.startswith("test_") and path.endswith(".py")
        ))[: budget.max_files]
        if not targets:
            if budget.essential:
                return ("Pick the one or two tests closest to the changed code (its test file, or a "
                        "-k filter on the changed behavior) and run only those.")
            return ("Pick the tests closest to the changed code (its test file, or a -k filter "
                    "on the changed behavior) and run only those.")
        python = [path for path in targets if path.endswith(".py") or "." not in PurePosixPath(path).name]
        if python and len(python) == len(targets):
            command = "python -m pytest -q " + " ".join(shlex.quote(path) for path in python)
            return "Tests covering the changed files: run them with exec: " + command
        return "Tests covering the changed files (run only these): " + ", ".join(targets)

    def completion_message(self, *, reason: str = "repeated") -> str:
        """A user-facing report, separate from the agent's repair instructions."""
        if self.test_failure:
            test_status = "Failed"
        elif self.tests_revision == self.revision:
            test_status = "Passed"
        elif self.pending_tests:
            test_status = "Still running"
        elif self.tests_revision >= 0:
            test_status = "Needs rerun after edits"
        else:
            test_status = "No current result"
        explanation = (
            "A verification failed. Its result must be resolved before the task can be closed."
            if self.failed else
            "Tests are still running. Their final result has not been received."
            if self.pending_tests else
            "No passing test result was recorded for the latest code changes."
            if self.needs_tests else
            "No successful check was recorded for the latest changes."
        )
        stopped = {
            "repeated": "The agent tried to finish repeatedly without resolving validation, so the run stopped.",
            "no_progress": "The run stopped because repeated actions were no longer making progress.",
            "ended": "The run ended before validation was completed.",
        }.get(reason, "The run ended before validation was completed.")
        lines = [
            "## Validation pending", "",
            "Changes are saved. The task is not validated yet.", "",
            "| Item | Status |", "| --- | --- |", "| Changes | Saved |",
        ]
        if self.needs_tests or self.test_failure or self.pending_tests:
            lines.append(f"| Tests | {test_status} |")
        if self.check_failure:
            lines.append("| Code checks | Failed |")
        elif self.checks_revision == self.revision:
            lines.append("| Code checks | Passed |")
        elif not self.needs_tests:
            lines.append("| Code checks | No current result |")
        lines.extend(["", "### Why it stopped", "", explanation, "", stopped])
        if self.test_result_note:
            lines.extend(["", self.test_result_note])
        for title, detail in (("Check failure", self.check_failure), ("Test failure", self.test_failure)):
            if detail:
                fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", detail)), default=0))
                lines.extend(["", f"### {title}", "", f"{fence}text", detail.strip(), fence])
        lines.extend(["", "### Next step", ""])
        targets = sorted(path for path in self.paths if (
            PurePosixPath(path).name.startswith("test_") and path.endswith(".py")
        ))[: current_budget().max_files]
        if self.pending_tests:
            lines.append("Wait for the running tests to finish and inspect their results before retrying completion.")
        elif self.needs_tests:
            lines.append("Run the tests that cover the changed files (not the whole suite).")
            if targets:
                lines.extend(["", "```bash", "python -m pytest " + " ".join(shlex.quote(path) for path in targets), "```"])
            lines.extend(["", "Inspect the results, fix any failures, then retry completing the task."])
        else:
            lines.append("Run the relevant checks, fix any failures, then retry completing the task.")
        if self.paths:
            lines.extend(["", f"### Changed files ({len(self.paths)})", ""])
            for path in sorted(self.paths)[:12]:
                fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", path)), default=0))
                lines.append(f"- {fence}{path}{fence}")
            if len(self.paths) > 12:
                lines.extend(["", f"{len(self.paths) - 12} more changed paths are listed in the activity above."])
        return "\n".join(lines)

    def nudge(self) -> int:
        # Repair edits reopen a full opportunity to validate. Repeating a
        # final answer with the same missing evidence is the only capped loop.
        key = (self.revision, self.checks_revision, self.tests_revision,
               bool(self.check_failure), bool(self.test_failure))
        self.ignored_nudges = self.ignored_nudges + 1 if key == self.last_nudge_key else 1
        self.last_nudge_key = key
        self.total_nudges += 1
        return self.ignored_nudges


def _test_failure_summary(text: str) -> str:
    """Keep the actual pytest assertion even when a long log ends in boilerplate."""
    tail = text[-800:]
    if len(text) <= 800:
        return tail
    match = re.search(r"(?m)^_{3,}\s+.+\s+_{3,}\s*$|^E\s+(?:AssertionError|[A-Za-z_]+Error):.*$", text)
    if match is None or match.start() >= len(text) - 800:
        return tail
    diagnostic = text[match.start():match.start() + 1600].strip()
    return diagnostic + "\n...\n" + tail
