"""What every frozen navin build has to contain, for both spec files.

The one-file and one-folder builds are the same application in two shapes, so
what goes inside them must not depend on which spec was run. It did: the portable
build shipped playwright's driver and the installed one did not, which meant the
browser tool could fetch a Chromium in one build and not in the other. The two
specs now read this module, so a capability is added once.

Every helper degrades to an empty list when the package is absent from the build
environment: an extra that was not installed is a build without that feature, not
a build that fails.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

REPO_ROOT = Path(__file__).resolve().parents[2]

# Libraries the agent's own scripts import, which navin never imports itself.
# PyInstaller ships what it sees being imported: python-docx, pandas and
# matplotlib arrive because navin uses them, these do not, even though the
# pdf-generator, invoice-reader and spreadsheet-analyst skills tell the model
# that "Navin ships the Python document libraries, so do not install anything".
# playwright is here for its CLI: the browser tool imports playwright.async_api,
# which PyInstaller sees, but not playwright.__main__, and without it
# `navin python -m playwright install chromium` cannot fetch a browser on a
# machine that has none. HTML→PDF goes through Chromium / ReportLab, not
# weasyprint (GTK stack is a packaging landmine on Windows/macOS).
# Xlib (python-xlib) is imported lazily inside navin.computer.x11, so the
# analyzer never sees it; without it a frozen Linux build falls back to the
# xdotool CLI for desktop control and cannot drive an Xvfb display on its own.
_SKILL_LIBRARIES = (
    "reportlab",
    "pdfplumber",
    "fitz",
    "yt_dlp",
    "playwright",
    # yt-dlp PyInstaller hook prefers Cryptodome (pycryptodomex) over Crypto.
    "Cryptodome",
    "Xlib",
    "PIL",
)

# The tools behind `lint`, `test_run`, `verify` and `lsp`. Nothing in navin
# imports them: they are subprocesses, normally started through a console script
# that a frozen build does not have. Collected here, `navin python -m pytest` and
# `-m pylsp` work inside the bundle, so a packaged build lints and tests like a
# source install instead of reporting the tools as not installed.
_QUALITY_MODULES = ("pytest", "pylsp", "pylsp_jsonrpc", "yamllint", "pyflakes", "pluggy")

# pytest and pylsp discover their plugins through entry points, which live in the
# distribution metadata; without it pylsp starts and answers nothing.
_QUALITY_METADATA = ("pytest", "python-lsp-server", "pluggy")

# MCP (and httpx/httpcore) resolve optional async backends through
# importlib.metadata. Without these distributions in the frozen tree, connecting
# MCP servers fails at runtime with KeyError/PackageNotFoundError('anyio').
_MCP_RUNTIME_MODULES = ("anyio", "sniffio", "mcp", "httpx", "httpcore")
_MCP_RUNTIME_METADATA = ("anyio", "sniffio", "mcp", "httpx", "httpcore")

# Quality tools that are programs rather than importable modules: ruff is a Rust
# binary, and `python -m ruff` only locates and executes it.
_QUALITY_PROGRAMS = ("ruff",)

# Navin's own metadata. `navin.__version__` reads it, and falls back to a
# hard-coded "1.0.0" when it is missing - which is what a frozen build got, so
# every release would have introduced itself as 1.0.0: the updater would keep
# offering an update it had just installed, and nothing could tell which build
# wrote the toolchains under ~/.navin.
_APP_METADATA = ("navin-ai",)

# Studio desks the Tauri sidecar must freeze on Linux, Windows and macOS.
# `collect_submodules("navin")` already walks them. Naming them here keeps a
# later narrowing of that collect from shipping a missing import, and the
# packaging test asserts these strings stay in this file and the spec.
DESK_PACKAGES = (
    "navin.career",
    "navin.leads",
    "navin.marketing",
    "navin.tenders",
    "navin.trading",
)


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _importable(name: str) -> bool:
    """True when the package exists and importing it succeeds.

    ``find_spec`` alone is not enough for packages that dlopen optional system
    libraries at import time: feeding a broken import to ``collect_submodules``
    prints a PyInstaller WARNING and ships an incomplete tree. Skip those.
    """
    if not _installed(name):
        return False
    try:
        importlib.import_module(name)
    except Exception as exc:
        detail = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        print(
            f"note: skipping {name} in the bundle ({detail}); "
            "features that need it fall back at runtime.",
            file=sys.stderr,
        )
        return False
    return True


def _submodules(names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in names:
        if _importable(name):
            found += collect_submodules(name)
    return found


def desk_hidden_imports() -> list[str]:
    """Every studio desk, even when analysis never sees a top-level import."""
    found: list[str] = []
    for name in DESK_PACKAGES:
        found += collect_submodules(name)
    return found


def hidden_imports() -> list[str]:
    """Modules no import statement in navin reveals."""
    found = (
        _submodules(_SKILL_LIBRARIES)
        + _submodules(_QUALITY_MODULES)
        + _submodules(_MCP_RUNTIME_MODULES)
        + desk_hidden_imports()
    )
    # The Rust extension is imported inside a try in navin.utils.native, which
    # the analyzer can miss; named explicitly, it ships whenever the build venv
    # has it (see the native step in the build-offline scripts).
    if _installed("navin_core"):
        found.append("navin_core")
    return found


def script_data() -> list[tuple[str, str]]:
    """Python files that ship as scripts rather than as imported modules.

    ``collect_data_files`` is told to skip ``.py``, which is right for modules and
    wrong for these two sets: the document converters are copied into the user's
    workspace and run there, and the skill scripts are read and executed by the
    agent. Left out, the converter command quietly disappears from the prompt and
    the deck and document generators fall back to rebuilding files by hand.
    """
    found: list[tuple[str, str]] = []
    for relative, pattern in (("navin/documents", "*.py"), ("navin/skills", "*/scripts/*.py")):
        base = REPO_ROOT / relative
        for source in sorted(base.glob(pattern)):
            if source.name == "__init__.py":
                continue
            destination = Path(relative) / source.parent.relative_to(base)
            found.append((str(source), destination.as_posix()))
    return found


def template_data() -> list[tuple[str, str]]:
    """The bundled template library."""
    found: list[tuple[str, str]] = []
    for category in ("ppt", "word", "pdf", "excel"):
        source = REPO_ROOT / "templates" / category
        if source.is_dir():
            found.append((str(source), f"templates/{category}"))
    return found


def extra_data() -> list[tuple[str, str]]:
    """Data files the bundled tools read at runtime, plus their metadata."""
    found: list[tuple[str, str]] = []
    if _installed("playwright"):
        # playwright's bundled node driver, which its CLI runs to fetch browsers.
        found += collect_data_files("playwright", include_py_files=False)
    if _installed("yamllint"):
        # yamllint reads its default and "relaxed" configs from its own package.
        found += collect_data_files("yamllint")
    for distribution in (*_APP_METADATA, *_QUALITY_METADATA, *_MCP_RUNTIME_METADATA):
        try:
            found += copy_metadata(distribution)
        except Exception:
            continue
    sandbox_name = "navin-sandbox.exe" if sys.platform == "win32" else "navin-sandbox"
    staged_sandbox = REPO_ROOT / "navin" / "resources" / "bin" / sandbox_name
    if not staged_sandbox.is_file():
        raise RuntimeError(
            "navin-sandbox is not staged at navin/resources/bin/; "
            "Linux, macOS and Windows Tauri sidecars must ship it. cargo is "
            "required on the build machine (https://rustup.rs)."
        )
    # Data, not a PyInstaller "binary": macholib must not rewrite the
    # Rust helper. Packaging restores the exec bit after collect.
    found.append((str(staged_sandbox), "navin/resources/bin"))
    return found


def ffmpeg_data() -> list[tuple[str, str]]:
    """The static ffmpeg and ffprobe staged for this target, under ``tools/``.

    Shipped as data rather than as a PyInstaller *binary* on purpose: the
    binary path runs macholib / ldd analysis and can rewrite load commands,
    which would corrupt a self-contained third-party program. The exec bit is
    restored at runtime by ``navin.montage.detect.find_ffmpeg``.

    An unstaged target degrades to a build without a bundled ffmpeg, which is
    the pre-existing behaviour: the runtime installer still downloads one.
    ffprobe alone missing only degrades probing precision, so it is a warning
    rather than a gate here; the build scripts enforce both.
    """
    sys.path.insert(0, str(REPO_ROOT / "packaging"))
    try:
        import ffmpeg_vendor
    except ImportError:
        return []
    target = ffmpeg_vendor.current_target()
    staged = ffmpeg_vendor.vendor_path(target)
    if not staged.is_file():
        print(f"[bundle] ffmpeg not staged ({staged}); build will rely on runtime install")
        return []
    found = [(str(staged), "tools")]
    probe = ffmpeg_vendor.vendor_path(target, "ffprobe")
    if probe.is_file():
        found.append((str(probe), "tools"))
    else:
        print(f"[bundle] ffprobe not staged ({probe}); probing falls back to ffmpeg")
    # GPLv3 section 4 requires the licence to travel with the binary, including
    # in a portable sidecar that ships without the Tauri resources tree.
    licence = (
        REPO_ROOT / "desktop" / "src-tauri" / "resources" / "licenses" / "ffmpeg-COPYING.GPLv3.txt"
    )
    if licence.is_file():
        found.append((str(licence), "tools"))
    else:
        raise SystemExit(f"bundling ffmpeg requires its GPL licence text at {licence}")
    return found


def typescript_data() -> list[tuple[str, str]]:
    """The npm typescript package, under ``tools/node_modules/``.

    Laid out as a node package root so that ``require("typescript")`` resolves
    against ``tools/`` as well, which is what a language server extracted from
    a VS Code extension needs. ``navin.python_runtime.bundled_node_package``
    finds it at runtime.

    An unstaged package degrades to the pre-existing behaviour: type checking
    falls back to the project's own tsc, or is skipped when it has none.
    """
    sys.path.insert(0, str(REPO_ROOT / "packaging"))
    try:
        import typescript_vendor
    except ImportError:
        return []
    staged = typescript_vendor.vendor_path()
    if not (staged / "package.json").is_file():
        print(f"[bundle] typescript not staged ({staged}); tsc will rely on the project")
        return []
    return [(str(staged), "tools/node_modules/typescript")]


def prefer_newest_openssl(binaries):
    """One OpenSSL per build, the newest one linked - see openssl_dedupe.

    Both specs run this over ``a.binaries``: python's ``_ssl`` and
    cryptography's rust binding can be linked against two different OpenSSL
    installs on the build machine, and shipping the older one produced a Mac
    build whose websocket channel and WebUI never started.
    """
    import openssl_dedupe  # SPECPATH is on sys.path when the specs run.

    return openssl_dedupe.prefer_newest_openssl(binaries)


def tool_binaries() -> list[tuple[str, str]]:
    """Helper programs, under ``tools/`` where ``bundled_tool`` looks for them."""
    suffix = sysconfig.get_config_var("EXE") or ""
    scripts = Path(sysconfig.get_path("scripts"))
    found: list[tuple[str, str]] = []
    for name in _QUALITY_PROGRAMS:
        candidate = scripts / f"{name}{suffix}"
        if candidate.is_file():
            found.append((str(candidate), "tools"))
    return found
