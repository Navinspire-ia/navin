"""Sandbox backends for shell command execution.

To add a new backend, implement a function with the signature:
    _wrap_<name>(command: str, workspace: str, cwd: str, strict: bool) -> str
and register it in _BACKENDS below.

The OS sandbox (Landlock on Linux/WSL, Seatbelt on macOS, none on Windows
unless WSL) confines *writes* to the workspace plus the host locations a
normal coding agent must touch: package caches, SSH agent sockets,
known_hosts, forge CLI state. Reads stay open. Linux and macOS installs
always ship or compile ``navin-sandbox``; a missing copy is a packaging
or toolchain defect, not a supported mode.

Application-level ``restrict_to_workspace`` is a separate Settings toggle.
It must not treat ``/usr/bin/env`` or ``../README`` inside the project as
an escape; those false denials look like a hung or blocked terminal.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from loguru import logger

from navin.config.paths import get_media_dir
from navin.security.workspace_policy import is_path_within
from navin.utils.proc import no_window_kwargs

_IS_WINDOWS = sys.platform == "win32"


def _shim_dir() -> Path | None:
    """Where the ``python`` shims of a packaged build live, if they exist."""
    from navin.config.paths import get_data_dir

    try:
        candidate = get_data_dir() / "bin"
    except OSError:
        return None
    return candidate.resolve() if candidate.is_dir() else None


def _existing(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        candidate = Path(path).expanduser()
        return candidate if candidate.exists() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


# System prefixes an agent names constantly (interpreters, certs, devices).
# Not project trees. Used by the exec guard so restrict_to_workspace does not
# refuse ``/usr/bin/env python`` or ``C:\\Windows\\System32\\cmd.exe``.
_POSIX_HOST_PREFIXES = (
    "/usr/",
    "/bin/",
    "/sbin/",
    "/lib/",
    "/lib64/",
    "/opt/",
    "/nix/",
    "/snap/",
    "/etc/",
    "/dev/",
    "/proc/",
    "/sys/",
    "/run/",
    "/usr/lib/wsl/",
    "/opt/homebrew/",
    "/home/linuxbrew/",
)
_POSIX_HOST_EXACT = frozenset({
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/nix", "/snap",
    "/etc", "/dev", "/proc", "/sys", "/run",
})
_POSIX_SCRATCH_PREFIXES = ("/tmp/", "/var/tmp/", "/dev/shm/", "/private/tmp/", "/private/var/tmp/", "/private/var/folders/")
_POSIX_SCRATCH_EXACT = frozenset({"/tmp", "/var/tmp", "/dev/shm", "/private/tmp", "/private/var/tmp"})

_WSL_WINDOWS_HOST_PREFIXES = (
    "/mnt/c/windows/",
    "/mnt/c/program files/",
    "/mnt/c/program files (x86)/",
    "/mnt/c/programdata/",
)

# Read-only paths git / ssh / gh / tea need. bwrap sets HOME to the
# workspace so we also expose the operator's real files under that HOME,
# otherwise ``git push`` and Forgejo auth die even when the token is in env.
# ``.navin`` stays out: that is where config.json and the machine key live.
_HOME_CREDENTIAL_RELS = (
    ".ssh",
    ".gitconfig",
    ".git-credentials",
    ".netrc",
    ".config/git",
    ".config/gh",
    ".config/tea",
    ".tea",
    ".config/glab-cli",
    ".config/forgejo-cli",
    ".docker",
    ".gnupg",
)

# User-local toolchains live under the real home and are referenced by PATH
# as absolute paths (nvm, cargo, pyenv). Binding only into $HOME=workspace
# does not help: node is still ``/home/you/.nvm/versions/...``.
_HOME_TOOLCHAIN_RELS = (
    ".nvm",
    ".local",
    ".cargo",
    ".rustup",
    ".go",
    ".pyenv",
    ".asdf",
    ".sdkman",
    ".volta",
    ".fnm",
    ".bun",
    ".npm",
    ".yarn",
    ".pnpm-store",
    ".cache",
    ".gradle",
    ".m2",
    ".nuget",
    ".gem",
    ".bundle",
    ".pub-cache",
    "go/pkg",
    "Library/Caches",
    "Library/Application Support",
    "AppData/Local",
    "AppData/Roaming",
    "AppData/Local/Temp",
)

# Home dirs the exec guard must not treat as "outside the workspace".
_HOME_GUARD_RELS = _HOME_CREDENTIAL_RELS + _HOME_TOOLCHAIN_RELS

_SYSTEM_GIT_SSH = (
    "/etc/gitconfig",
    "/etc/ssh",
)

# Env vars that name extra cache / temp / socket locations.
_WRITABLE_ENV_KEYS = (
    "TMPDIR",
    "TMP",
    "TEMP",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "GOPATH",
    "GOCACHE",
    "NPM_CONFIG_CACHE",
    "npm_config_cache",
    "PNPM_HOME",
    "YARN_CACHE_FOLDER",
    "PIP_CACHE_DIR",
    "UV_CACHE_DIR",
    "GRADLE_USER_HOME",
    "NUGET_PACKAGES",
    "NUGET_HTTP_CACHE_PATH",
    "GEM_HOME",
    "BUNDLE_PATH",
    "COMPOSER_HOME",
    "SSH_AUTH_SOCK",
    "GNUPGHOME",
    "DOCKER_CONFIG",
    "GH_CONFIG_DIR",
)


def docker_socket_paths() -> list[str]:
    """Sockets ``docker`` / ``podman`` write to. Landlock must allow-write them."""
    paths: list[str] = ["/var/run/docker.sock", "/run/docker.sock"]
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if runtime:
        paths.append(str(Path(runtime) / "docker.sock"))
        paths.append(str(Path(runtime) / "podman" / "podman.sock"))
    return paths


def _real_home() -> Path | None:
    raw = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    if not raw:
        return None
    try:
        home = Path(raw).expanduser()
        return home if home.is_dir() else None
    except (OSError, RuntimeError, ValueError):
        return None


def writable_host_paths(workspace: str, cwd: str | None = None) -> list[str]:
    """Host paths a sandboxed command must be allowed to write.

    Workspace writes are already granted via ``--workspace``. This list is the
    rest: caches, SSH agent sockets, known_hosts, forge CLI state, docker
    sockets, and an explicit working_dir that is not inside the project.
    Missing paths are skipped so Landlock/Seatbelt setup never fails closed
    on a laptop that has no cargo or docker.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(path: str | Path | None) -> None:
        existing = _existing(path)
        if existing is None:
            return
        try:
            key = _norm(existing.resolve())
        except (OSError, RuntimeError, ValueError):
            key = _norm(existing)
        if key in seen:
            return
        seen.add(key)
        found.append(str(existing))

    home = _real_home()
    if home is not None:
        for rel in _HOME_GUARD_RELS:
            _add(home / rel)
        _add(home / ".ssh" / "known_hosts")

    # Studio desks live under the instance data dir (usually ~/.navin/<module>
    # on Linux, Windows and macOS). The whole `.navin` tree stays read-only
    # (config + machine key). These subdirs must be writable so
    # `navin career|tenders|trading|leads|marketing start|stop|watch` and desk_cli persist loop
    # state, desk.lock and alerts in a sandbox.
    added_desks = False
    try:
        from navin.config.paths import get_runtime_subdir

        for name in ("career", "tenders", "trading", "leads", "marketing"):
            _add(get_runtime_subdir(name))
        added_desks = True
    except Exception:
        pass
    homes: list[Path] = []
    if home is not None:
        homes.append(home)
    try:
        fallback_home = Path.home()
    except (OSError, RuntimeError, ValueError):
        fallback_home = None
    if fallback_home is not None and (
        home is None or _norm(fallback_home) != _norm(home)
    ):
        homes.append(fallback_home)
    for desk_home in homes:
        if not added_desks:
            for name in ("career", "tenders", "trading", "leads", "marketing"):
                desk = desk_home / ".navin" / name
                try:
                    desk.mkdir(mode=0o700, parents=True, exist_ok=True)
                except OSError:
                    pass
                _add(desk)
        linkedin_dir = desk_home / ".linkedin-mcp"
        try:
            linkedin_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            pass
        _add(linkedin_dir)

    for key in _WRITABLE_ENV_KEYS:
        value = os.environ.get(key) or ""
        if not value:
            continue
        _add(value)
        # SSH_AUTH_SOCK is a socket: Landlock needs write on the socket file
        # itself. Also allow its parent so a replacement socket still works.
        parent = Path(value).expanduser().parent
        _add(parent)

    for sock in docker_socket_paths():
        _add(sock)

    if cwd:
        _add(cwd)

    # Do not repeat the workspace; --workspace already covers it.
    try:
        ws_key = _norm(Path(workspace).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        ws_key = _norm(workspace)
    return [path for path in found if _norm(path) != ws_key]


def _posix_view(path: Path) -> str:
    """Lowercased POSIX-ish path, drive letter stripped so Windows matches /windows/."""
    text = path.as_posix().replace("\\", "/").lower()
    if len(text) >= 2 and text[1] == ":":
        text = text[2:]
    if not text.startswith("/"):
        text = "/" + text.lstrip("/")
    return text


def is_host_tool_path(path: Path | str) -> bool:
    """True for system binaries, libs, certs, devices (not a user project)."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    posix = _posix_view(resolved)
    exact = posix.rstrip("/")
    if exact in _POSIX_HOST_EXACT:
        return True
    if any(posix.startswith(prefix) for prefix in _POSIX_HOST_PREFIXES):
        return True
    if any(posix.startswith(prefix) for prefix in _WSL_WINDOWS_HOST_PREFIXES):
        return True
    if (
        posix.startswith("/windows/")
        or posix == "/windows"
        or "/program files/" in posix
        or posix.startswith("/program files")
        or posix.startswith("/programdata/")
        or posix == "/programdata"
    ):
        return True
    return False


def is_scratch_path(path: Path | str) -> bool:
    """True for OS temp dirs. Absolute mentions are allowed; ``../`` escapes are not."""
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    text = _norm(resolved).replace("\\", "/")
    posix = text if text.startswith("/") else "/" + text.lstrip("/")
    if posix.rstrip("/") in _POSIX_SCRATCH_EXACT:
        return True
    if any(posix.startswith(prefix) for prefix in _POSIX_SCRATCH_PREFIXES):
        return True
    if _IS_WINDOWS:
        temp = os.environ.get("TEMP") or os.environ.get("TMP") or ""
        if temp and is_path_within(resolved, Path(temp)):
            return True
        lowered = str(resolved).replace("/", "\\").lower()
        if "\\temp\\" in lowered or lowered.endswith("\\temp"):
            return True
    return False


def is_toolchain_home_path(path: Path | str) -> bool:
    """True for ``~/.ssh``, ``~/.cargo``, npm/pip caches, forge CLI state."""
    home = _real_home()
    if home is None:
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    for rel in _HOME_GUARD_RELS:
        if is_path_within(resolved, home / rel):
            return True
    return False


def is_unrestricted_exec_path(path: Path | str, *, relative_escape: bool = False) -> bool:
    """Paths the restrict_to_workspace exec guard must not refuse.

    ``relative_escape`` is set when the token contained ``..``. Scratch dirs
    (``/tmp``) stay allowed as absolute operands (``cat /tmp/out``) but a
    ``../secrets`` walk out of a project that happens to live under ``/tmp``
    is still an escape.
    """
    if is_host_tool_path(path) or is_toolchain_home_path(path):
        return True
    if relative_escape:
        return False
    return is_scratch_path(path)


def _bwrap(command: str, workspace: str, cwd: str, strict: bool = False) -> str:
    """Wrap command in a bubblewrap sandbox (requires bwrap in container).

    Only the workspace is bind-mounted read-write; its parent dir (which holds
    config.json) is hidden behind a fresh tmpfs.  The media directory is
    bind-mounted read-only so exec commands can read uploaded attachments.
    """
    if not sys.platform.startswith("linux"):
        raise ValueError(
            "sandbox 'bwrap' (bubblewrap) only exists on Linux; use "
            "tools.exec.sandbox = \"native\" (Landlock / Seatbelt) or clear it."
        )
    if shutil.which("bwrap") is None:
        raise ValueError(
            "sandbox 'bwrap' is configured but the bwrap binary is not on PATH; "
            "install bubblewrap or switch tools.exec.sandbox to \"native\"."
        )
    ws = Path(workspace).resolve()
    media = get_media_dir().resolve()

    try:
        sandbox_cwd = str(ws / Path(cwd).resolve().relative_to(ws))
    except ValueError:
        # Settings restrict off: keep the requested cwd if it exists.
        resolved_cwd = Path(cwd).expanduser()
        sandbox_cwd = str(resolved_cwd.resolve()) if resolved_cwd.is_dir() else str(ws)

    required = ["/usr"]
    optional = [
        "/bin",
        "/lib",
        "/lib64",
        "/opt",
        "/nix",
        "/snap",
        "/mnt",
        "/opt/homebrew",
        "/home/linuxbrew",
        "/usr/lib/wsl",
        "/etc/alternatives",
        "/etc/passwd",
        "/etc/group",
        "/etc/nsswitch.conf",
        "/etc/hosts",
        "/etc/hostname",
        "/etc/localtime",
        "/usr/share/zoneinfo",
        "/etc/ssl",
        "/etc/ssl/certs",
        "/etc/pki",
        "/etc/pki/tls/certs",
        "/etc/pki/ca-trust",
        "/etc/crypto-policies",
        "/etc/resolv.conf",
        "/run/systemd/resolve/stub-resolv.conf",
        "/run/systemd/resolve/resolv.conf",
        "/etc/ld.so.cache",
    ]

    args = ["bwrap", "--new-session", "--die-with-parent", "--setenv", "HOME", str(ws)]
    for p in required:
        args += ["--ro-bind", p, p]
    for p in optional:
        args += ["--ro-bind-try", p, p]
    args += _host_binds(ws)
    for sock in docker_socket_paths():
        args += ["--bind-try", sock, sock]
    for extra in writable_host_paths(str(ws), cwd):
        args += ["--bind-try", extra, extra]
    if not getattr(sys, "frozen", False):
        # Keep the interpreter environment that launched Navin available
        # read-only. This makes bundled Python libraries usable from pipx and
        # virtualenv installs without exposing the Navin source repository.
        runtime_prefix = Path(sys.prefix).resolve()
        if runtime_prefix != Path("/usr") and runtime_prefix != ws and ws not in runtime_prefix.parents:
            args += ["--ro-bind-try", str(runtime_prefix), str(runtime_prefix)]
    else:
        # A packaged build has no interpreter directory to expose: what a
        # sandboxed command needs is the executable itself, which the python
        # shims on PATH call. Without it, `python script.py` inside the sandbox
        # dies on a missing file while the same command works unsandboxed.
        for path in (Path(sys.executable).resolve().parent, _shim_dir()):
            if path is not None and path != ws and ws not in path.parents:
                args += ["--ro-bind-try", str(path), str(path)]
    args += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--tmpfs", str(ws.parent),        # mask config dir
        "--dir", str(ws),                 # recreate workspace mount point
        "--bind", str(ws), str(ws),
        "--ro-bind-try", str(media), str(media),  # read-only access to media
        "--chdir", sandbox_cwd,
        "--", "sh", "-c", command,
    ]
    return shlex.join(args)


def _host_binds(workspace: Path) -> list[str]:
    """Bind git/ssh/forge/toolchain paths from the real home.

    Credential and toolchain dirs are read-write: ``git fetch`` updates
    known_hosts, ``tea``/``gh`` refresh tokens, npm/pip/cargo fill caches.
    ``.navin`` stays out.
    """
    args: list[str] = []
    for path in _SYSTEM_GIT_SSH:
        args += ["--ro-bind-try", path, path]
    home = _real_home()
    if home is not None and home.resolve() != workspace.resolve():
        for rel in _HOME_CREDENTIAL_RELS + _HOME_TOOLCHAIN_RELS:
            src = home / rel
            dest = workspace / rel
            args += ["--bind-try", str(src), str(src)]
            try:
                if dest.resolve() != src.resolve():
                    args += ["--bind-try", str(src), str(dest)]
            except (OSError, RuntimeError, ValueError):
                args += ["--bind-try", str(src), str(dest)]
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    if runtime:
        # Unix sockets (SSH agent, docker) need write, not a read-only bind.
        args += ["--bind-try", runtime, runtime]
    return args


_SANDBOX_NAME = "navin-sandbox.exe" if _IS_WINDOWS else "navin-sandbox"
_MISSING_SANDBOX_WARNED = False
_UNRUNNABLE_WARNED = False
_COMPILE_LOCK = threading.Lock()
_RUNNABLE_CACHE: dict[str, bool] = {}


def staged_sandbox_path() -> Path:
    """Where ``make native`` / packaging stage the helper inside the package."""
    return Path(__file__).resolve().parents[2] / "resources" / "bin" / _SANDBOX_NAME


def sandbox_crate_dir() -> Path | None:
    """``navin-sandbox/`` next to a source checkout, or ``None`` if frozen."""
    if getattr(sys, "frozen", False):
        return None
    here = Path(__file__).resolve()
    for start in (here.parents[3], here.parents[2].parent):
        crate = start / "navin-sandbox"
        if (crate / "Cargo.toml").is_file():
            return crate
    return None


def _runtime_sandbox_path() -> Path | None:
    """Writable copy under ``~/.navin/bin`` when the bundle file is not +x."""
    from navin.config.paths import get_data_dir

    try:
        return get_data_dir() / "bin" / _SANDBOX_NAME
    except OSError:
        return None


def _copy_to_runtime_bin(source: Path) -> str | None:
    dest = _runtime_sandbox_path()
    if dest is None:
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if (
            dest.is_file()
            and dest.stat().st_size == source.stat().st_size
            and os.access(dest, os.X_OK)
        ):
            return str(dest)
        shutil.copy2(source, dest)
        dest.chmod(dest.stat().st_mode | 0o755)
    except OSError:
        return None
    return str(dest) if dest.is_file() and os.access(dest, os.X_OK) else None


def _ensure_executable(path: Path) -> str | None:
    """Return *path* if it is a file we can execute, restoring the exec bit."""
    if not path.is_file():
        return None
    if os.access(path, os.X_OK):
        return str(path)
    try:
        path.chmod(path.stat().st_mode | 0o755)
    except OSError:
        return _copy_to_runtime_bin(path)
    if os.access(path, os.X_OK):
        return str(path)
    return _copy_to_runtime_bin(path)


def _sandbox_binary_candidates() -> list[Path]:
    """Places a packaged or source install actually puts ``navin-sandbox``."""
    name = _SANDBOX_NAME
    found: list[Path] = []
    override = os.environ.get("NAVIN_SANDBOX_BIN")
    if override:
        found.append(Path(override).expanduser())
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        found.append(root / "navin" / "resources" / "bin" / name)
        found.append(root / name)
    found.append(staged_sandbox_path())
    exe_dir = Path(sys.executable).resolve().parent
    found.append(exe_dir / name)
    found.append(exe_dir / "navin" / "resources" / "bin" / name)
    found.append(exe_dir / "_internal" / "navin" / "resources" / "bin" / name)
    found.append(exe_dir / "_internal" / name)
    runtime = _runtime_sandbox_path()
    if runtime is not None:
        found.append(runtime)
    return found


def native_sandbox_binary() -> str | None:
    """Locate the ``navin-sandbox`` binary (the Rust Landlock sandbox).

    Checked in order: an explicit override, a writable runtime copy, a frozen
    PyInstaller tree, the copy a packaged build ships under the resources
    tree, next to the running executable, then anything on PATH.
    ``make native`` stages it at ``navin/resources/bin/navin-sandbox``.
    """
    for candidate in _sandbox_binary_candidates():
        located = _ensure_executable(candidate)
        if located:
            return located
    return shutil.which("navin-sandbox")


def _compile_native_sandbox(*, timeout: float = 600) -> str | None:
    """Build the crate into ``navin/resources/bin``. Source checkouts only."""
    if os.environ.get("NAVIN_SKIP_SANDBOX_BUILD") == "1":
        return None
    crate = sandbox_crate_dir()
    cargo = shutil.which("cargo")
    if crate is None or cargo is None:
        return None
    dest = staged_sandbox_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Building navin-sandbox from {} (cargo --release)", crate)
    result = subprocess.run(
        [cargo, "build", "--release", "--manifest-path", str(crate / "Cargo.toml")],
        cwd=str(crate),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        logger.error(
            "navin-sandbox cargo build failed (exit {}):\n{}",
            result.returncode,
            (result.stderr or result.stdout or "").strip()[-4000:],
        )
        return None
    built = crate / "target" / "release" / _SANDBOX_NAME
    if not built.is_file():
        logger.error("cargo reported success but {} is missing", built)
        return None
    try:
        shutil.copy2(built, dest)
        dest.chmod(dest.stat().st_mode | 0o755)
    except OSError as exc:
        logger.error("could not stage navin-sandbox at {}: {}", dest, exc)
        return None
    return _ensure_executable(dest)


def ensure_native_sandbox(*, required: bool = False, timeout: float = 600) -> str | None:
    """Return the sandbox binary, compiling it when this is a source tree.

    Every Tauri sidecar (Linux, macOS, Windows) ships this helper. Source
    installs compile it with cargo. A missing copy must not brick ``exec``:
    the caller warns and runs unconfined unless ``required`` or ``strict``.
    """
    found = native_sandbox_binary()
    if found:
        return found
    with _COMPILE_LOCK:
        found = native_sandbox_binary()
        if found:
            return found
        found = _compile_native_sandbox(timeout=timeout)
        if found:
            return found
    crate = sandbox_crate_dir()
    cargo = shutil.which("cargo")
    if crate is None:
        reason = (
            "this packaged build shipped no navin-sandbox; rebuild the app"
        )
    elif cargo is None:
        reason = "install rustup (https://rustup.rs) then run `make native`"
    else:
        reason = "cargo failed to build navin-sandbox; see the log above"
    message = f"navin-sandbox is missing ({reason})"
    if required:
        raise RuntimeError(message)
    global _MISSING_SANDBOX_WARNED
    if not _MISSING_SANDBOX_WARNED:
        _MISSING_SANDBOX_WARNED = True
        logger.error(message)
    return None


def _sandbox_binary_is_runnable(binary: str) -> bool:
    """True when wrapping a command through *binary* will not brick exec.

    A present-but-broken helper (wrong arch, not executable, helper crash
    before exec) used to wrap every shell call and fail it. Production
    locators only return a real file; a missing path is treated as runnable
    so unit tests that inject ``/opt/bin/navin-sandbox`` keep checking wrap
    shape. Exit 125 is the helper's own refusal (bad args / setup failure).
    Exit 126 is the kernel refusing to execute the file (wrong arch, not
    an executable). Either case bricks every wrapped command if we still wrap.
    """
    cached = _RUNNABLE_CACHE.get(binary)
    if cached is not None:
        return cached
    if not os.path.isfile(binary):
        _RUNNABLE_CACHE[binary] = True
        return True
    try:
        with tempfile.TemporaryDirectory() as probe:
            result = subprocess.run(
                [
                    binary,
                    "--workspace",
                    probe,
                    "--allow-write",
                    probe,
                    "--",
                    "sh",
                    "-c",
                    ":",
                ],
                capture_output=True,
                timeout=5,
                check=False,
                **no_window_kwargs(),
            )
        ok = result.returncode not in (125, 126)
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _RUNNABLE_CACHE[binary] = ok
    return ok


def _landlock(command: str, workspace: str, cwd: str, strict: bool = False) -> str:
    """Wrap *command* in the native OS sandbox.

    The navin-sandbox binary picks the mechanism the host actually has: Landlock
    plus namespaces on Linux (and therefore inside WSL, which runs a real Linux
    kernel), Seatbelt (sandbox-exec) on macOS. Either way the policy is the
    same: reads stay open across the filesystem (toolchains, system libraries),
    writes are confined to the workspace plus host caches / SSH / forge state.

    ``strict`` decides what happens when the OS cannot enforce the policy:
    False keeps the historical behaviour (warn on stderr, run unconfined),
    True passes ``--strict`` so the binary refuses instead of silently
    dropping the confinement - the fail-closed posture of the ``strict``
    security profile. Unlike bwrap it needs nothing bind-mounted and no
    container, so it works on a bare host.

    The helper is compiled or copied into place before this wraps anything.
    A still-missing or unrunnable binary is a defect: warn once and run the
    command as-is unless ``strict`` is set. Raising here used to brick every
    ``exec`` call, including when a packaged helper existed but could not
    start on this OS version.
    """
    binary = ensure_native_sandbox()
    if binary is not None and not _sandbox_binary_is_runnable(binary):
        global _UNRUNNABLE_WARNED
        if not _UNRUNNABLE_WARNED:
            _UNRUNNABLE_WARNED = True
            logger.error(
                "navin-sandbox at {} cannot run on this OS; "
                "running commands unsandboxed so the terminal stays usable",
                binary,
            )
        if strict:
            raise ValueError(
                "sandbox 'landlock' cannot run navin-sandbox on this OS; "
                "relax tools.security_profile to proceed."
            )
        return command
    if binary is None:
        if strict:
            raise ValueError(
                "sandbox 'landlock' needs the navin-sandbox binary; build it with "
                "`make native` or set NAVIN_SANDBOX_BIN."
            )
        global _MISSING_SANDBOX_WARNED
        if not _MISSING_SANDBOX_WARNED:
            _MISSING_SANDBOX_WARNED = True
            logger.error(
                "navin-sandbox is missing; running commands unsandboxed. "
                "This is not a supported install. Rebuild the app or run "
                "`make native`."
            )
        return command
    ws = str(Path(workspace).resolve())
    shell = "bash" if shutil.which("bash") else "sh"
    chdir = Path(cwd).expanduser()
    if chdir.is_dir():
        resolved_cwd = str(chdir.resolve())
        # Injected into the shell so an older navin-sandbox without --chdir
        # still honours working_dir. The binary also accepts --chdir after a
        # rebuild; we do not pass it yet so a staged binary cannot refuse
        # the flag and block every command.
        if resolved_cwd != ws:
            command = f"cd {shlex.quote(resolved_cwd)} && {command}"
    args = [binary, "--workspace", ws]
    for extra in writable_host_paths(ws, cwd):
        args.extend(["--allow-write", extra])
    if strict:
        args.append("--strict")
    return shlex.join([*args, "--", shell, "-c", command])


# "native" is the portable name for the OS sandbox; "landlock" is kept as an
# alias because existing configs use it.
_BACKENDS = {"bwrap": _bwrap, "landlock": _landlock, "native": _landlock}


def wrap_command(
    sandbox: str,
    command: str,
    workspace: str,
    cwd: str,
    *,
    strict: bool = False,
) -> str:
    """Wrap *command* using the named sandbox backend.

    ``strict`` makes the native backend fail closed when the host cannot
    confine the command (missing Landlock, missing sandbox-exec, win32)
    instead of warning and running unconfined. bwrap is inherently
    fail-closed - a missing binary fails the command - so it ignores it.
    """
    if backend := _BACKENDS.get(sandbox):
        return backend(command, workspace, cwd, strict)
    raise ValueError(f"Unknown sandbox backend {sandbox!r}. Available: {list(_BACKENDS)}")


def native_sandbox_argv(workspace: str, cwd: str | None = None, *, strict: bool = False) -> list[str] | None:
    """``navin-sandbox`` prefix for a long-lived process such as an interactive shell.

    Same policy as :func:`wrap_command` (writes confined to the workspace plus
    the host caches / SSH / forge state), without the ``-- sh -c`` tail: the
    caller appends its own argv. Landlock rules are inherited by every child,
    so a shell started this way confines everything typed into it. ``None``
    when the helper is missing or cannot run here, so the caller can fall back
    to an unconfined process and say so instead of failing to open a shell.
    """
    if _IS_WINDOWS:
        return None
    binary = ensure_native_sandbox()
    if binary is None or not _sandbox_binary_is_runnable(binary):
        return None
    ws = str(Path(workspace).expanduser().resolve())
    args = [binary, "--workspace", ws]
    for extra in writable_host_paths(ws, cwd):
        args.extend(["--allow-write", extra])
    if strict:
        args.append("--strict")
    return args


# What a Landlock / Seatbelt refusal looks like in a command's output.
_SANDBOX_DENIAL_RE = re.compile(
    r"permission denied|read-only file system|operation not permitted"
    r"|\bEACCES\b|\bEROFS\b|\bEPERM\b",
    re.IGNORECASE,
)


def sandbox_denial_suspected(output: str) -> bool:
    """True when a failed command's output reads like a filesystem refusal."""
    return bool(output) and _SANDBOX_DENIAL_RE.search(output) is not None


def sandbox_tool_description(sandbox: str) -> str:
    """Sentence for the exec tool description when an OS sandbox is configured."""
    if not sandbox or _IS_WINDOWS:
        return ""
    return (
        "Commands run inside an OS sandbox: writes are limited to the project "
        "plus toolchain caches and SSH/forge state, reads and network stay "
        "open. If a command fails because of that (permission denied outside "
        "the project, global install, system service), re-run it with "
        "unsandboxed=true: the user is asked to approve and it then runs "
        "unconfined. Do not ask needlessly. "
    )


def sandbox_result_note(
    sandbox: str,
    *,
    exit_code: int | None,
    lifted: bool = False,
    output: str = "",
) -> str:
    """Line appended to an exec result about the confinement it ran under.

    Nothing on success inside the sandbox: that is the common case and the UI
    shows the badge. A failure inside the sandbox gets the hint the model
    needs to tell a sandbox denial from a real error and to know the one way
    out (ask the user through ``unsandboxed=true``). A lifted command always
    says so, because the transcript should record what ran unconfined.
    """
    if lifted:
        return "\n[Ran outside the OS sandbox: the user approved lifting it for this command.]"
    if not sandbox or exit_code in (0, None):
        return ""
    suspicion = (
        " The output mentions a permission refusal, which is what a sandbox denial looks like."
        if sandbox_denial_suspected(output)
        else ""
    )
    return (
        "\n[Sandboxed: this command ran in the OS sandbox (writes limited to the "
        "project and toolchain caches; reads and network open)."
        f"{suspicion} If it failed because of the sandbox, re-run it with "
        "unsandboxed=true so the user can approve; otherwise fix the real error.]"
    )
