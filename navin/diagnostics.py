"""What this installation can and cannot do, on any of the three systems.

A shell script used to answer this for a source checkout on Linux, which is the
one situation where the answer is easy to get anyway: a packaged build ships no
scripts folder, and a Windows user has no shell to run it in. Everything here
therefore lives in Python, inside the application, and reports the same facts on
every platform and in every install shape.

Nothing in a report is fatal. Missing tools remove capabilities - no ffmpeg means
no video assembly - and the point is to say so before a skill fails halfway
through a task.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from navin.host.packages import PkgSpec, install_hint


@dataclass(frozen=True)
class Check:
    """One fact about this installation."""

    name: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = False


@dataclass(frozen=True)
class Section:
    """A named group of checks."""

    title: str
    checks: list[Check] = field(default_factory=list)


# tool -> (what stops working without it, package names per manager)
_TOOLS: tuple[tuple[str, str, PkgSpec], ...] = (
    (
        "git",
        "version control, checkpoints, packs",
        PkgSpec(apt="git", dnf="git", pacman="git", brew="git", winget="Git.Git"),
    ),
    (
        "curl",
        "web fetch and installers",
        PkgSpec(apt="curl", dnf="curl", pacman="curl", brew="curl"),
    ),
    (
        "node",
        "MCP servers and CLI apps that run on npx",
        PkgSpec(apt="nodejs", dnf="nodejs", pacman="nodejs", brew="node", winget="OpenJS.NodeJS"),
    ),
    (
        "ffmpeg",
        "video and audio assembly, transcription",
        PkgSpec(apt="ffmpeg", dnf="ffmpeg", pacman="ffmpeg", brew="ffmpeg", winget="Gyan.FFmpeg"),
    ),
    (
        "jq",
        "JSON handling in shell workflows",
        PkgSpec(apt="jq", dnf="jq", pacman="jq", brew="jq", winget="jqlang.jq"),
    ),
    (
        "rg",
        "fast code search (a Python fallback exists)",
        PkgSpec(
            apt="ripgrep",
            dnf="ripgrep",
            pacman="ripgrep",
            brew="ripgrep",
            winget="BurntSushi.ripgrep.MSVC",
        ),
    ),
    (
        "sqlite3",
        "local databases in the Dev module",
        PkgSpec(
            apt="sqlite3", dnf="sqlite", pacman="sqlite", brew="sqlite", winget="SQLite.SQLite"
        ),
    ),
    (
        "pandoc",
        "document format conversion",
        PkgSpec(
            apt="pandoc",
            dnf="pandoc",
            pacman="pandoc",
            brew="pandoc",
            winget="JohnMacFarlane.Pandoc",
        ),
    ),
    (
        "soffice",
        "Office to PDF conversion (LibreOffice)",
        PkgSpec(
            apt="libreoffice",
            dnf="libreoffice",
            pacman="libreoffice",
            brew="--cask libreoffice",
            winget="TheDocumentFoundation.LibreOffice",
        ),
    ),
    ("docker", "sandboxed execution and containerised MCP servers", PkgSpec()),
)

_REQUIRED_TOOLS = frozenset({"git"})


def tool_checks() -> list[Check]:
    """External programs navin shells out to, and what their absence costs."""
    checks: list[Check] = []
    for name, purpose, spec in _TOOLS:
        found = shutil.which(name)
        checks.append(
            Check(
                name=name,
                ok=bool(found),
                detail=found or purpose,
                hint="" if found else install_hint(spec),
                required=name in _REQUIRED_TOOLS,
            )
        )
    return checks


def _browser_check() -> Check:
    from navin.documents._chromium import find_chromium

    try:
        found = find_chromium()
    except Exception:
        found = ""
    return Check(
        name="chromium",
        ok=bool(found),
        detail=found or "browser automation, HTML to PDF, screenshots",
        hint="" if found else "install Google Chrome, Edge or Chromium",
    )


def runtime_checks() -> list[Check]:
    """The interpreter and libraries the agent's own scripts depend on."""
    from navin.python_runtime import external_python, packaged, python_command

    checks = [
        Check(
            name="python for skills",
            ok=True,
            detail=" ".join(python_command()),
        )
    ]
    external = external_python()
    checks.append(
        Check(
            name="python for installs",
            ok=bool(external),
            detail=external or "no interpreter outside navin",
            hint="" if external else "install Python if you want to add pip-based CLI apps",
        )
    )
    for module in ("docx", "openpyxl", "pptx", "pandas", "reportlab", "pdfplumber"):
        checks.append(_module_check(module))
    if packaged():
        from navin.optional_features import bundled_extras

        extras = sorted(bundled_extras())
        checks.append(
            Check(
                name="bundled extras",
                ok=bool(extras),
                detail=", ".join(extras) or "none declared by the build",
                hint="" if extras else "this build cannot report which features it ships",
            )
        )
    return checks


def quality_checks() -> list[Check]:
    """The tools behind the lint, test_run, verify and lsp tools.

    Each is reported with the command that will actually be run, because the
    answer differs between installations: a project's own ruff, one on PATH, or
    the copy inside a packaged build, reached through ``navin python -m`` when it
    ships as a module rather than a program.
    """
    from navin.lsp.manager import _read_table
    from navin.quality.linters import linter_table, tool_argv
    from navin.quality.testing import runner_table

    wanted = (
        ("ruff", linter_table().get("ruff"), "Python diagnostics and auto-fix"),
        ("yamllint", linter_table().get("yamllint"), "YAML diagnostics"),
        ("pytest", runner_table().get("pytest"), "running Python test suites"),
        ("pylsp", _read_table().get("pylsp"), "Python go-to-definition and hovers"),
    )
    root = Path.cwd()
    checks: list[Check] = []
    for name, spec, purpose in wanted:
        launch = tool_argv(spec, root) if spec else None
        checks.append(
            Check(
                name=name,
                ok=launch is not None,
                detail=" ".join(launch) if launch else purpose,
                hint="" if launch else f"pip install {name}",
            )
        )
    return checks


def _module_check(module: str) -> Check:
    """Whether one library the skills promise can actually be imported."""
    import importlib.util

    try:
        present = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        present = False
    return Check(
        name=module,
        ok=present,
        detail="importable" if present else "not in this build",
        hint="" if present else "document skills that use it will fail",
    )


def build_checks() -> list[Check]:
    """Which artifact this is, and what it is allowed to do to itself."""
    from navin import __version__
    from navin.config.loader import get_config_path
    from navin.config.paths import get_logs_dir
    from navin.python_runtime import packaged
    from navin.update.service import _install_kind

    kind = _install_kind()
    # A source install updates with git and pip, which is not a defect to report.
    from_source = kind == "source"
    updatable = from_source or kind != "unsupported"
    config_path = get_config_path()
    checks = [
        Check(name="version", ok=True, detail=f"navin {__version__}"),
        Check(
            name="platform",
            ok=True,
            detail=f"{platform.system()} {platform.release()} ({platform.machine()})",
        ),
        Check(
            name="install",
            ok=True,
            detail="packaged build" if packaged() else "source install",
        ),
        Check(name="executable", ok=True, detail=sys.executable),
        Check(
            name="automatic updates",
            ok=updatable,
            detail=("git pull, pip install -U" if from_source else kind)
            if updatable
            else "not supported for this build",
            hint="" if updatable else "download the next release and replace this installation",
        ),
        Check(
            name="config",
            ok=config_path.exists(),
            detail=str(config_path),
            hint="" if config_path.exists() else "run navin onboard",
        ),
        Check(name="logs", ok=True, detail=str(get_logs_dir())),
        webui_bundle_check(),
        sandbox_check(),
    ]
    return checks


def webui_bundle_check() -> Check:
    """Which WebUI build this installation serves.

    The desktop apps do not embed the interface in their binary; they serve
    the bundle frozen in this installation. So the digest below is the answer
    to "is this app running the interface that release shipped": compare it
    with the release notes, or with ``packaging/verify_bundle_stamp.py`` on the
    build tree. The file count is checked here (cheap), the byte-level digest
    at build time (528 file reads, too much for every doctor run).
    """
    import json

    from navin.webui.build import default_webui_dist_dir

    try:
        dist = default_webui_dist_dir()
    except Exception as exc:  # noqa: BLE001 - doctor must never raise
        return Check(name="web bundle", ok=False, detail=f"unresolved ({exc})")
    if not (dist / "index.html").is_file():
        return Check(
            name="web bundle",
            ok=False,
            detail=f"missing at {dist}",
            hint="cd webui && npm run build",
        )
    stamp_path = dist / "build-info.json"
    if not stamp_path.is_file():
        return Check(
            name="web bundle",
            ok=True,
            detail="present, unstamped (built before build-info.json existed)",
            hint="cd webui && npm run build to stamp it",
        )
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        recorded = int(stamp.get("files") or 0)
        digest = str(stamp.get("bundle") or "")[:16]
        version = str(stamp.get("version") or "unknown")
    except (OSError, ValueError, TypeError) as exc:
        return Check(name="web bundle", ok=False, detail=f"unreadable stamp ({exc})")
    actual = sum(
        1 for path in dist.rglob("*") if path.is_file() and path.name != "build-info.json"
    )
    if recorded and actual != recorded:
        return Check(
            name="web bundle",
            ok=False,
            detail=f"{actual} files, stamp says {recorded} (bundle {digest})",
            hint="the bundle was modified after it was built; reinstall or rebuild",
        )
    detail = f"{version}, bundle {digest} ({actual} files)"
    if stamp.get("dirty"):
        detail += ", built from an uncommitted tree"
    return Check(name="web bundle", ok=True, detail=detail)


def sandbox_check() -> Check:
    """Whether the OS command-jail helper is on this machine.

    Presence is reported on Linux, macOS and Windows. Absence is never a
    blocking doctor failure: a missing copy must not stop ``navin doctor``
    or agent ``exec``.
    """
    from navin.agent.tools.sandbox import native_sandbox_binary
    from navin.python_runtime import packaged

    found = native_sandbox_binary()
    if found:
        detail = found
        if sys.platform == "win32":
            detail = f"{found} (pass-through on Windows; WSL uses Landlock)"
        return Check(name="navin-sandbox", ok=True, detail=detail)
    return Check(
        name="navin-sandbox",
        ok=False,
        detail="missing",
        hint=(
            "this packaged build shipped no navin-sandbox; rebuild the app"
            if packaged()
            else "install rustup (https://rustup.rs) then make native"
        ),
        required=False,
    )


def _connect_host(host: str) -> str:
    """Turn a bind address into an address a browser can actually open."""
    value = (host or "").strip() or "127.0.0.1"
    if value in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return value.lstrip("[").rstrip("]")


def _http_health(url: str, *, timeout_s: float = 0.75) -> tuple[bool, str]:
    """Probe a local health endpoint without requiring httpx or aiohttp."""
    try:
        with urlopen(url, timeout=timeout_s) as response:  # noqa: S310
            status = int(getattr(response, "status", 0) or response.getcode())
            body = response.read(4096).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (OSError, URLError, ValueError) as exc:
        return False, f"unreachable ({exc})"
    if status != 200:
        return False, f"HTTP {status}"
    if '"status"' in body and '"ok"' not in body:
        return False, "HTTP 200, unhealthy response"
    return True, "HTTP 200"


def _platform_context(workspace: Path | None) -> Check:
    """Describe native, WSL guest and Windows-on-WSL-workspace contexts."""
    from navin.utils.wsl import is_wsl_guest, looks_like_unc, parse_unc

    raw = str(workspace) if workspace is not None else ""
    location = parse_unc(raw)
    if location is not None:
        return Check(
            name="host context",
            ok=True,
            detail=f"Windows host, WSL UNC workspace ({location.distro}:{location.posix})",
            hint="run project commands through wsl.exe; keep Windows build tools on a local drive",
        )
    if is_wsl_guest():
        return Check(
            name="host context",
            ok=True,
            detail="WSL guest; Windows browsers normally reach local services through localhost",
        )
    if raw and looks_like_unc(raw):
        return Check(
            name="host context",
            ok=True,
            detail="Windows UNC/network workspace",
            hint="stage cmd/npm/Rust builds on a local drive when a tool rejects UNC working directories",
        )
    return Check(name="host context", ok=True, detail=f"native {platform.system()}")


def connectivity_checks(
    config: Any | None = None,
    *,
    workspace: Path | None = None,
    health_probe: Callable[[str], tuple[bool, str]] = _http_health,
) -> list[Check]:
    """Report configured URLs, port ownership and HTTP health when running.

    A stopped service is not a diagnostic failure. HTTP is only attempted when
    a configured port already accepts TCP connections, which keeps doctor fast
    and makes the function straightforward to test without a live server.
    """
    from navin.ports import check_ports

    checks = [_platform_context(workspace)]
    rows = check_ports(config, include_external=False)
    webui = next((row.role for row in rows if row.role.spec.name == "webui"), None)
    if webui is not None:
        url = f"http://{_connect_host(webui.host)}:{webui.port}/"
        checks.append(Check(name="recommended URL", ok=True, detail=url))

    for row in rows:
        role = row.role
        endpoint = f"{role.host}:{role.port}"
        if row.status == "free":
            checks.append(
                Check(
                    name=f"{role.spec.name} port",
                    ok=True,
                    detail=f"{endpoint} free (service not running)",
                )
            )
            continue

        health_url = f"http://{_connect_host(role.host)}:{role.port}/health"
        healthy, detail = health_probe(health_url)
        owner = "Navin listener" if row.status == "navin" else "listener present"
        checks.append(
            Check(
                name=f"{role.spec.name} port",
                ok=healthy,
                detail=f"{endpoint} {owner}; health {detail}",
                hint="" if healthy else f"inspect the listener and open {health_url}",
            )
        )
    return checks


def _hyperframes_check() -> Check:
    """Lazy Montage toolchain (HyperFrames under ~/.navin/montage) - never required."""
    try:
        from navin.montage.detect import hyperframes_bin

        found = hyperframes_bin()
    except Exception:
        found = None
    path = str(found) if found else ""
    return Check(
        name="hyperframes",
        ok=bool(found),
        detail=path
        or "Montage HTML compositions (install via montage(action=setup))",
        hint=""
        if found
        else "optional: run Montage mode → montage(action=setup) (not bundled)",
    )


#: Output tokens per request below which a run is spending a round trip per
#: tool call. One call serializes to roughly 130 tokens, which is the floor a
#: run that batches nothing sits on.
#:
#: Single days are too noisy to read: measured across 2026-08-04 to 08-15 the
#: daily figure ranged from 60 to 356 with the workload, prose-heavy days
#: landing high and tool-heavy days low. Aggregated over a week the same
#: period holds near 225, which is why the window below is days and not one.
_ONE_CALL_PER_TURN = 160
_BATCHING_WELL = 250


def token_efficiency_checks(days: int = 7) -> list[Check]:
    """Whether the agent is grouping its tool calls or spending a turn on each.

    Input is re-sent in full on every round trip, so the count of round trips
    costs more than the size of any one of them. Output tokens per request is
    the cheapest available proxy, and it needs no new instrumentation: the
    usage recorder already stores both numbers.

    It is a proxy, not a proof. A day spent writing long prose answers raises
    it without any batching, and a day of pure tool work lowers it. Read the
    trend across days rather than one number.
    """
    from navin.webui.token_usage import read_token_usage_state

    try:
        state = read_token_usage_state()
    except Exception:
        return []

    rows = [row for _, row in sorted(state.get("days", {}).items())[-max(1, days):]]
    requests = sum(int(row.get("requests") or 0) for row in rows)
    completion = sum(int(row.get("completion_tokens") or 0) for row in rows)
    if requests <= 0:
        return []

    per_request = completion / requests
    detail = f"{per_request:.0f} output tokens per request over {requests} requests"
    if per_request < _ONE_CALL_PER_TURN:
        return [
            Check(
                name="tool call batching",
                ok=False,
                detail=f"{detail} - about one tool call per round trip",
                hint=(
                    "Every round trip re-sends the whole prompt. See the "
                    "batching rule in the Token efficiency section of "
                    "tool_contract.md, and docs/performance.md for the "
                    "measurement behind this threshold."
                ),
            )
        ]
    verdict = "batching well" if per_request >= _BATCHING_WELL else "batching some calls"
    return [Check(name="tool call batching", ok=True, detail=f"{detail} - {verdict}")]


def doctor_report(
    config: Any | None = None,
    *,
    workspace: Path | None = None,
) -> list[Section]:
    """Everything, grouped for display."""
    return [
        Section("Installation", build_checks()),
        Section(
            "Connectivity",
            connectivity_checks(config, workspace=workspace),
        ),
        Section("Python runtime", runtime_checks()),
        Section("Code quality tools", quality_checks()),
        Section(
            "External tools",
            [*tool_checks(), _browser_check(), _hyperframes_check()],
        ),
        Section("Token efficiency", token_efficiency_checks()),
    ]


def missing_required(sections: list[Section]) -> list[Check]:
    """Checks whose failure actually blocks navin rather than reducing it."""
    return [
        check for section in sections for check in section.checks if check.required and not check.ok
    ]


def workspace_note(workspace: Path) -> Check:
    """Whether the configured workspace is usable."""
    exists = workspace.is_dir()
    return Check(
        name="workspace",
        ok=exists,
        detail=str(workspace),
        hint="" if exists else "it is created on first use",
    )
