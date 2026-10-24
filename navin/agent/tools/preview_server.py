# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Discover and start a workspace project's local web server for Preview.

The agent (and open_preview) must bring up the user's app - not ask the user
to run npm themselves. This module finds vite/next/dev entrypoints, starts
them in a background exec session, and waits until HTTP answers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger

from navin.ports import is_navin_listener, loopback_host, loopback_url, who_listens

# Ports commonly used by project apps (never Navin gateway :8765).
_PROBE_PORTS = (
    3000,
    3001,
    3100,
    4173,
    4200,
    4321,
    5000,
    5174,
    5175,
    5176,
    5180,
    8000,
    8080,
    8081,
    5173,
)

_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".turbo",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".navin",
}

_FRONTEND_DIR_HINTS = (
    "frontend",
    "web",
    "client",
    "app",
    "ui",
    "apps/web",
    "apps/frontend",
)

_PORT_RE = re.compile(
    r"""(?:--|port\s*[:=]\s*|port\s*\(\s*|server\s*:\s*\{[^}]*port\s*:\s*"""
    r"""|https?://[^\s:/'"]+:)(\d{2,5})""",
    re.IGNORECASE | re.DOTALL,
)

# Fingerprints of Navin's own editor HTML. Preview must never iframe these.
NAVIN_HTML_MARKERS = (
    "data-navin-webui",
    "data-boot-copy",
    "navin-webui",
    "Loading Navin",
    "Couldn't reach navin",
    "Connexion à navin",
    "Chargement de Navin",
)
_NAVIN_PACKAGE_NAMES = frozenset({"navin-webui", "navin"})
_NAVIN_CMDLINE_EXTRA = (
    "navin-webui",
    "navin-ai-v2",
    "navin/web/dist",
    "navin\\web\\dist",
)


@dataclass(frozen=True)
class ProjectDevServer:
    """How to start one local web/UI server in the workspace."""

    cwd: Path
    command: str
    port: int | None
    label: str
    # The CLI flag that moves the server to another port ("--port" for Vite,
    # "-p" for Next), when the command takes one. PORT is set either way.
    port_flag: str | None = None

    @property
    def url(self) -> str | None:
        if self.port is None:
            return None
        return f"http://127.0.0.1:{self.port}"


def _workspace_root(workspace: Path | None) -> Path | None:
    if workspace is None:
        return None
    try:
        root = workspace.expanduser().resolve()
    except OSError:
        return None
    return root if root.is_dir() else None


def html_looks_like_navin(html: str) -> bool:
    """True when the page is Navin's editor shell, not a user project."""
    return any(marker in html for marker in NAVIN_HTML_MARKERS)


def fetch_preview_html(url: str, *, timeout_s: float = 1.5) -> str | None:
    """Return the first bytes of ``url`` when it looks like HTML."""
    try:
        req = Request(url, method="GET", headers={"User-Agent": "navin-preview/1"})
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - local loopback only
            ctype = (resp.headers.get("content-type") or "").lower()
            raw = resp.read(16_000)
            text = raw.decode("utf-8", "replace")
            if ctype and "html" not in ctype and "xhtml" not in ctype:
                return text if html_looks_like_navin(text) else None
            return text
    except (URLError, OSError, ValueError, TimeoutError):
        return None


def url_looks_like_navin(url: str) -> bool:
    """True when ``url`` is the Navin editor (process, port, or HTML)."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        port = parsed.port
    except Exception:
        port = None
    if port == 8765 or port == 18790:
        return True
    if port and _looks_like_navin_port(port):
        return True
    html = fetch_preview_html(url)
    return bool(html and html_looks_like_navin(html))


def is_navin_product_tree(root: Path) -> bool:
    """True when ``root`` is the Navin source repo, not a user project."""
    if not (root / "navin" / "agent").is_dir():
        return False
    index = root / "webui" / "index.html"
    if not index.is_file():
        return False
    return html_looks_like_navin(_read_text(index))


def is_navin_editor_cwd(cwd: Path) -> bool:
    """True when this folder is Navin's WebUI (must never be Preview's target)."""
    if is_navin_product_tree(cwd):
        return True
    index = cwd / "index.html"
    if index.is_file() and html_looks_like_navin(_read_text(index)):
        parent = cwd.parent
        if (parent / "navin" / "agent").is_dir() or (parent / "navin" / "web").is_dir():
            return True
    pkg = cwd / "package.json"
    if pkg.is_file():
        try:
            payload = json.loads(_read_text(pkg, limit=8_000) or "{}")
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            name = str(payload.get("name") or "").strip().lower()
            if name in _NAVIN_PACKAGE_NAMES:
                return True
    return False


def http_reachable(url: str, *, timeout_s: float = 1.5) -> bool:
    """True when GET url returns an HTTP response (any status < 500 preferred).

    A ``127.0.0.1`` URL also counts when the server listens on ::1 only (the
    Vite / Next default where Node resolves ``localhost`` to IPv6 first).
    """
    if _http_answers(url, timeout_s):
        return True
    twin = _ipv6_twin(url)
    return twin is not None and _http_answers(twin, timeout_s)


def _ipv6_twin(url: str) -> str | None:
    """The same URL on ::1 when ``url`` targets 127.0.0.1 / localhost."""
    for host in ("127.0.0.1", "localhost"):
        prefix = f"http://{host}:"
        if url.startswith(prefix):
            return "http://[::1]:" + url[len(prefix):]
    return None


def _http_answers(url: str, timeout_s: float) -> bool:
    try:
        req = Request(url, method="GET", headers={"User-Agent": "navin-preview/1"})
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - local loopback only
            code = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(code) < 500
    except HTTPError as exc:
        # urlopen raises for 4xx/5xx. An API with no route at "/" answers 404:
        # it is up. Treating that as down made start_app / project start wait
        # 75s and report a failure for every backend API.
        return 200 <= int(exc.code) < 500
    except (URLError, OSError, ValueError, TimeoutError):
        return False


async def http_reachable_async(url: str, *, timeout_s: float = 1.5) -> bool:
    return await asyncio.to_thread(http_reachable, url, timeout_s=timeout_s)


def _looks_like_navin_port(port: int) -> bool:
    if port in {8765, 18790}:
        return True
    listener = who_listens(port)
    if listener is not None:
        if is_navin_listener(listener):
            return True
        text = (listener.cmdline or "").lower()
        if text:
            if any(marker in text for marker in _NAVIN_CMDLINE_EXTRA):
                return True
            if "vite" in text and (
                "webui" in text or "navin-ai" in text or "/navin/" in text
            ):
                return True
    html = fetch_preview_html(loopback_url(port))
    return bool(html and html_looks_like_navin(html))


def _listener_belongs_to_workspace(port: int, workspace: Path | None) -> bool:
    """True when the process holding ``port`` runs inside the workspace.

    Foreign dev servers (another project on the same machine, e.g. a Lynara
    app on port 3000) must never be adopted as the workspace app: the agent
    would preview the wrong app and the real one would never start. When we
    cannot prove the listener belongs to the workspace, treat it as foreign.
    """
    if workspace is None:
        return True
    listener = who_listens(port)
    if listener is None or not listener.cwd:
        return False
    try:
        return Path(listener.cwd).resolve() == Path(workspace).resolve() or (
            Path(workspace).resolve() in Path(listener.cwd).resolve().parents
        )
    except OSError:
        return False


def listener_workspace_match(port: int, workspace: Path | None) -> bool | None:
    """True / False when the process on ``port`` is / is not inside the
    workspace, None when that cannot be told (no workspace, cwd not readable)."""
    if workspace is None:
        return None
    listener = who_listens(port)
    if listener is None or not listener.cwd:
        return None
    try:
        cwd = Path(listener.cwd).resolve()
        root = Path(workspace).resolve()
    except OSError:
        return None
    return cwd == root or root in cwd.parents


def discover_running_project_url(
    *,
    preferred_port: int | None = None,
    workspace: Path | None = None,
    allow_unproven: bool = False,
) -> str | None:
    """Return a loopback URL that is already serving a non-Navin project.

    The known port is tried first (even when it is also a usual one, 5173 or
    5175: a fixed order once let another server on 5174 win). With a
    workspace, a server started from it wins; a server proven to run from
    another folder is never taken. ``allow_unproven`` falls back to a server
    whose folder cannot be read (macOS lsof limits) when nothing better answers.
    """
    reserved = _navin_ports()
    declared = workspace_declared_ports(workspace)
    ports: list[int] = [preferred_port] if preferred_port else []
    ports.extend(_PROBE_PORTS)
    seen: set[int] = set()
    unproven: list[str] = []
    for port in ports:
        if port in seen:
            continue
        seen.add(port)
        # Navin's reserved ports (5173-5176, 8765-8768, 18790-18793) are never
        # a project preview: what answers there is Navin or another project.
        if port in reserved:
            continue
        host = loopback_host(port)
        if host is None:
            continue
        if _looks_like_navin_port(port):
            continue
        match = listener_workspace_match(port, workspace)
        if workspace is not None and match is False:
            continue  # another project's app
        if workspace is not None and match is None:
            if not allow_unproven:
                continue
            # The folder cannot be read (a WSL app seen from Windows, macOS
            # lsof limits): only this project's remembered or declared port,
            # never whatever app happens to answer on a usual port.
            if port != preferred_port and port not in declared:
                continue
        url = loopback_url(port, host)
        if not http_reachable(url):
            continue
        if workspace is None or match:
            return url
        unproven.append(url)
    return unproven[0] if unproven else None


def workspace_declared_ports(workspace: Path | None) -> frozenset[int]:
    """Ports the workspace's own dev servers declare (package.json, vite config...)."""
    if workspace is None:
        return frozenset()
    try:
        servers = discover_project_dev_servers(workspace)
    except OSError:
        return frozenset()
    return frozenset(s.port for s in servers if s.port)


def is_navin_reserved_port(port: int | None) -> bool:
    """True for a port Navin keeps for itself: never shown as a project preview."""
    return bool(port) and port in _navin_ports()


def _read_text(path: Path, limit: int = 40_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _port_from_text(text: str) -> int | None:
    match = _PORT_RE.search(text)
    if not match:
        return None
    try:
        port = int(match.group(1))
    except ValueError:
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _port_from_package_and_config(cwd: Path) -> int | None:
    for name in (
        "vite.config.ts",
        "vite.config.js",
        "vite.config.mjs",
        "vite.config.mts",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
    ):
        port = _port_from_text(_read_text(cwd / name))
        if port is not None:
            return port
    pkg = cwd / "package.json"
    if pkg.is_file():
        port = _port_from_text(_read_text(pkg))
        if port is not None:
            return port
    return None


def _npm_dev_command(cwd: Path, scripts: dict[str, Any]) -> str | None:
    if "dev" in scripts:
        script = "dev"
    elif "start" in scripts:
        script = "start"
    else:
        return None
    if (cwd / "pnpm-lock.yaml").is_file():
        return f"pnpm run {script}"
    if (cwd / "yarn.lock").is_file():
        return f"yarn {script}"
    if (cwd / "bun.lockb").is_file() or (cwd / "bun.lock").is_file():
        return f"bun run {script}"
    return f"npm run {script}"


# `npm --prefix app run dev`, `pnpm -C app dev`, `yarn --cwd app dev`, `cd app && ...`:
# a root package.json that only hands the dev server to a sub-folder.
_DELEGATE_RE = re.compile(
    r"(?:--prefix|--cwd|--dir|-C)[ =]+(?P<flag>[^\s&;|]+)|\bcd\s+(?P<cd>[^\s&;|]+)\s*&&"
)


def _delegated_folder(cwd: Path, scripts: dict) -> Path | None:
    """The sub-folder the dev script really runs in, when it delegates to one."""
    for name in ("dev", "start"):
        text = scripts.get(name)
        if not isinstance(text, str):
            continue
        match = _DELEGATE_RE.search(text)
        if match is None:
            return None
        rel = (match.group("flag") or match.group("cd") or "").strip("\"'")
        try:
            root = cwd.resolve()
            folder = (cwd / rel).resolve()
            folder.relative_to(root)
        except (OSError, ValueError):
            return None
        if folder != root and (folder / "package.json").is_file():
            return folder
        return None
    return None


def _package_server(cwd: Path, _depth: int = 0) -> ProjectDevServer | None:
    pkg_path = cwd / "package.json"
    if not pkg_path.is_file():
        return None
    try:
        payload = json.loads(_read_text(pkg_path, limit=200_000) or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return None
    command = _npm_dev_command(cwd, scripts)
    if not command:
        return None
    if _depth < 2:
        # Start the real app where it lives: its port flag then reaches Vite or
        # Next, while `npm run dev -- --port` on the wrapper would not.
        nested = _delegated_folder(cwd, scripts)
        if nested is not None:
            inner = _package_server(nested, _depth + 1)
            if inner is not None:
                return inner
    port = _port_from_package_and_config(cwd)
    script_text = " ".join(str(v) for v in scripts.values()).lower()
    uses_vite = "vite" in script_text
    uses_next = "next" in script_text
    # Vite default when unset.
    if port is None and uses_vite:
        port = 5173
    if port is None and uses_next:
        port = 3000
    port_flag = "--port" if uses_vite else "-p" if uses_next else None
    name = str(payload.get("name") or cwd.name)
    return ProjectDevServer(cwd=cwd, command=command, port=port, label=name, port_flag=port_flag)


def _python_server(cwd: Path) -> ProjectDevServer | None:
    if (cwd / "manage.py").is_file():
        return ProjectDevServer(
            cwd=cwd,
            command="python manage.py runserver 127.0.0.1:8000",
            port=8000,
            label=f"{cwd.name}-django",
        )
    for candidate, cmd, port in (
        (cwd / "main.py", "python main.py", 8000),
        (cwd / "app.py", "uvicorn app:app --reload --host 127.0.0.1 --port 8000", 8000),
        (cwd / "app" / "main.py", "uvicorn app.main:app --reload --host 127.0.0.1 --port 8000", 8000),
    ):
        if candidate.is_file():
            return ProjectDevServer(
                cwd=cwd,
                command=cmd,
                port=port,
                label=cwd.name,
            )
    return None


def _docker_compose_server(cwd: Path) -> ProjectDevServer | None:
    compose = None
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        path = cwd / name
        if path.is_file():
            compose = path
            break
    if compose is None:
        return None
    text = _read_text(compose)
    port = _port_from_text(text) or 3000
    return ProjectDevServer(
        cwd=cwd,
        command="docker compose up",
        port=port,
        label=f"{cwd.name}-compose",
    )


def _cargo_server(cwd: Path) -> ProjectDevServer | None:
    if not (cwd / "Cargo.toml").is_file():
        return None
    text = _read_text(cwd / "Cargo.toml")
    port = _port_from_text(text) or 3000
    return ProjectDevServer(
        cwd=cwd,
        command="cargo run",
        port=port,
        label=f"{cwd.name}-cargo",
    )


def _go_server(cwd: Path) -> ProjectDevServer | None:
    if not (cwd / "go.mod").is_file():
        return None
    if not (cwd / "main.go").is_file() and not (cwd / "cmd").is_dir():
        return None
    port = _port_from_text(_read_text(cwd / "main.go")) if (cwd / "main.go").is_file() else None
    return ProjectDevServer(
        cwd=cwd,
        command="go run .",
        port=port or 8080,
        label=f"{cwd.name}-go",
    )


def _rails_server(cwd: Path) -> ProjectDevServer | None:
    if not (cwd / "Gemfile").is_file():
        return None
    gemfile = _read_text(cwd / "Gemfile").lower()
    if "rails" not in gemfile:
        return None
    cmd = "bin/rails server -b 127.0.0.1 -p 3000"
    if not (cwd / "bin" / "rails").is_file():
        cmd = "bundle exec rails server -b 127.0.0.1 -p 3000"
    return ProjectDevServer(cwd=cwd, command=cmd, port=3000, label=f"{cwd.name}-rails")


def _php_server(cwd: Path) -> ProjectDevServer | None:
    if (cwd / "artisan").is_file():
        return ProjectDevServer(
            cwd=cwd,
            command="php artisan serve --host=127.0.0.1 --port=8000",
            port=8000,
            label=f"{cwd.name}-laravel",
        )
    if (cwd / "public" / "index.php").is_file():
        return ProjectDevServer(
            cwd=cwd,
            command="php -S 127.0.0.1:8000 -t public",
            port=8000,
            label=f"{cwd.name}-php",
        )
    return None


def _deno_server(cwd: Path) -> ProjectDevServer | None:
    cfg = cwd / "deno.json"
    if not cfg.is_file():
        cfg = cwd / "deno.jsonc"
    if not cfg.is_file():
        return None
    text = _read_text(cfg)
    port = _port_from_text(text) or 8000
    if '"dev"' in text or "'dev'" in text:
        return ProjectDevServer(
            cwd=cwd, command="deno task dev", port=port, label=f"{cwd.name}-deno"
        )
    if '"start"' in text or "'start'" in text:
        return ProjectDevServer(
            cwd=cwd, command="deno task start", port=port, label=f"{cwd.name}-deno"
        )
    return None


def _makefile_server(cwd: Path) -> ProjectDevServer | None:
    mf = cwd / "Makefile"
    if not mf.is_file():
        return None
    text = _read_text(mf)
    for target in ("dev", "serve", "run", "start", "up"):
        if re.search(rf"^{re.escape(target)}\s*:", text, re.MULTILINE):
            port = _port_from_text(text) or 3000
            return ProjectDevServer(
                cwd=cwd,
                command=f"make {target}",
                port=port,
                label=f"{cwd.name}-make",
            )
    return None


def _detect_server(cwd: Path) -> ProjectDevServer | None:
    """Pick the best runner for one directory (JS/Python/Docker/etc.)."""
    if is_navin_editor_cwd(cwd):
        return None
    return (
        _package_server(cwd)
        or _docker_compose_server(cwd)
        or _rails_server(cwd)
        or _php_server(cwd)
        or _deno_server(cwd)
        or _python_server(cwd)
        or _cargo_server(cwd)
        or _go_server(cwd)
        or _makefile_server(cwd)
    )


def _score_server(server: ProjectDevServer, root: Path) -> tuple[int, str]:
    rel = "."
    try:
        rel = str(server.cwd.relative_to(root)).replace("\\", "/").lower()
    except ValueError:
        rel = server.cwd.name.lower()
    score = 0
    # Root start.sh usually boots frontend+backend together.
    if server.label == "start.sh" or server.command.strip().startswith("bash start.sh"):
        score += 80
    for hint in _FRONTEND_DIR_HINTS:
        if rel == hint or rel.endswith("/" + hint) or hint in rel.split("/"):
            score += 50
            break
    if "frontend" in rel:
        score += 20
    if server.port and server.port != 5173:
        score += 5
    # Prefer JS UI over backend API alone.
    if server.command.startswith(("npm", "pnpm", "yarn", "bun")):
        score += 10
    return (-score, rel)


def discover_project_dev_servers(workspace: Path | None) -> list[ProjectDevServer]:
    """Find runnable web/dev entrypoints under the workspace (shallow scan)."""
    root = _workspace_root(workspace)
    if root is None:
        return []

    found: list[ProjectDevServer] = []
    seen_cwd: set[Path] = set()

    if is_navin_product_tree(root):
        # The Navin repo itself is the editor, not a user app to preview.
        return []

    start_sh = root / "start.sh"
    if start_sh.is_file():
        found.append(
            ProjectDevServer(
                cwd=root,
                command="bash start.sh",
                port=_port_from_text(_read_text(start_sh)),
                label="start.sh",
            )
        )
        seen_cwd.add(root)

    # Prefer well-known frontend folders first, then shallow walk.
    candidates: list[Path] = [root]
    for hint in _FRONTEND_DIR_HINTS:
        candidates.append(root.joinpath(*hint.split("/")))
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if child.is_dir() and child.name not in _SKIP_DIR_NAMES:
            candidates.append(child)

    for cwd in candidates:
        if not cwd.is_dir() or cwd in seen_cwd:
            continue
        server = _detect_server(cwd)
        if server is None:
            continue
        seen_cwd.add(cwd)
        found.append(server)

    # One more level for monorepos (apps/*, packages/*, services/*) without deep crawl cost.
    for mid_name in ("apps", "packages", "services"):
        mid = root / mid_name
        if not mid.is_dir():
            continue
        try:
            children = list(mid.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name in _SKIP_DIR_NAMES or child in seen_cwd:
                continue
            server = _detect_server(child)
            if server is None:
                continue
            seen_cwd.add(child)
            found.append(server)

    found.sort(key=lambda s: _score_server(s, root))
    return found


def pick_server_for_url(
    servers: list[ProjectDevServer],
    preferred_url: str | None,
) -> ProjectDevServer | None:
    if not servers:
        return None
    if preferred_url:
        try:
            from urllib.parse import urlparse

            port = urlparse(preferred_url).port
        except Exception:
            port = None
        if port:
            for server in servers:
                if server.port == port:
                    return server
    return servers[0]


def _navin_ports() -> frozenset[int]:
    """Ports a project app must never take, even while they are free.

    Navin's editor (Vite 5173-5176 in development), WebUI and engine pairs, and
    the ports this installation really uses. A project left on 5173 by one
    Navin (the desktop app on Windows) is what the browser then shows instead
    of Navin's own editor started from WSL.
    """
    from navin.instance import NAVIN_DEFAULT_PORTS, navin_reserved_ports

    try:
        ports = set(navin_reserved_ports())
    except Exception:  # pragma: no cover - the config read already guards itself
        ports = set(NAVIN_DEFAULT_PORTS)
    return frozenset(ports | {8900})


def _free_port_after(port: int, *, span: int = 50) -> int | None:
    """The first port above ``port`` nothing listens on, Navin's own excluded."""
    reserved = _navin_ports()
    for candidate in range(port + 1, min(port + 1 + span, 65536)):
        if candidate in reserved:
            continue
        if loopback_host(candidate) is None:
            return candidate
    return None


def _command_on_port(server: ProjectDevServer, port: int) -> str:
    """``server.command`` moved to ``port`` (npm needs ``--`` before script args)."""
    if not server.port_flag:
        return server.command
    flag = f"{server.port_flag} {port}"
    if server.port_flag == "--port":
        flag += " --strictPort"  # Vite: fail loudly rather than drift to another port
    separator = " -- " if server.command.startswith("npm ") else " "
    return f"{server.command}{separator}{flag}"


def _moved_to_free_port(server: ProjectDevServer) -> ProjectDevServer | None:
    """The same server on a free port, or None when there is none nearby."""
    if server.port is None:
        return None
    port = _free_port_after(server.port)
    if port is None:
        return None
    return replace(server, port=port, command=_command_on_port(server, port))


async def start_project_dev_server(
    server: ProjectDevServer,
    *,
    owner_session_key: str | None = None,
) -> tuple[str | None, str | None, ProjectDevServer]:
    """Start ``server`` in a background exec session.

    Returns ``(session_id, error, server_started)``. When the project's port is
    taken (Navin's own editor on 5173, or another app), the project starts on
    the next free port instead of failing: ``server_started`` carries that port.
    """
    from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

    taken = bool(server.port) and bool(loopback_host(server.port))
    # A reserved port moves the project even while it is free: the app would
    # otherwise sit where Navin's editor opens. Without a port flag the command
    # cannot be moved, so it keeps its own port.
    reserved = bool(server.port and server.port_flag) and server.port in _navin_ports()
    if taken or reserved:
        navin_port = reserved or _looks_like_navin_port(server.port)
        if taken and not navin_port and http_reachable(loopback_url(server.port)):
            # "Already up" is only true when the listener is actually this
            # project (same cwd). A foreign app on the same port must not
            # be adopted: start on the next free port instead.
            if _listener_belongs_to_workspace(server.port, server.cwd):
                return None, None, server  # already up
        moved = _moved_to_free_port(server)
        if moved is None:
            return None, (
                f"port {server.port} is "
                + ("taken" if taken else "reserved")
                + (" by Navin's editor" if navin_port else " by another app")
                + " and no free port was found near it."
            ), server
        logger.info(
            "Port {} is {}{}; starting {} on {}",
            server.port, "taken" if taken else "reserved",
            " by Navin" if navin_port else "", server.label, moved.port,
        )
        server = moved

    env = os.environ.copy()
    if server.port:
        # Next, CRA, Express and most servers read PORT; Vite takes the flag.
        env["PORT"] = str(server.port)
    # Avoid interactive package managers hanging the session.
    env.setdefault("CI", "1")
    env.setdefault("npm_config_yes", "true")

    try:
        session_id, poll = await DEFAULT_EXEC_SESSION_MANAGER.start(
            command=server.command,
            cwd=str(server.cwd),
            env=env,
            timeout=None,
            shell_program=None,
            login=False,
            yield_time_ms=1500,
            max_output_chars=4000,
            owner_session_key=owner_session_key,
        )
    except Exception as exc:
        return None, f"could not start {server.label}: {exc}", server

    if poll.done and poll.exit_code not in (None, 0):
        detail = (poll.output or "").strip()[-500:]
        return None, (
            f"{server.command} exited with code {poll.exit_code}"
            + (f": {detail}" if detail else "")
        ), server
    return session_id, None, server


async def wait_for_url(url: str, *, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await http_reachable_async(url, timeout_s=1.0):
            return True
        await asyncio.sleep(0.5)
    return False


async def ensure_project_preview_url(
    *,
    workspace: Path | None,
    preferred_url: str | None = None,
    owner_session_key: str | None = None,
    start_if_needed: bool = True,
) -> tuple[str | None, str | None]:
    """Resolve a project Preview URL, starting the app when necessary.

    Returns ``(url, error)``. ``error`` is set when nothing could be opened.
    """
    preferred_port: int | None = None
    if preferred_url:
        try:
            from urllib.parse import urlparse

            preferred_port = urlparse(preferred_url).port
        except Exception:
            preferred_port = None
        # Navin keeps 5173-5176, 8765-8768... for itself: whatever answers
        # there (Navin, or another project) is never this project's Preview.
        if is_navin_reserved_port(preferred_port):
            preferred_url = None
            preferred_port = None

        if preferred_url and await http_reachable_async(preferred_url):
            if preferred_port and _looks_like_navin_port(preferred_port):
                return None, (
                    f"{preferred_url} is the Navin editor, not the project app."
                )
            if await asyncio.to_thread(url_looks_like_navin, preferred_url):
                return None, (
                    f"{preferred_url} is the Navin editor, not the project app."
                )
            if await asyncio.to_thread(
                _listener_belongs_to_workspace, preferred_port, workspace
            ):
                return preferred_url, None
            if not start_if_needed:
                return preferred_url, None
            # The preferred port is held by another app (not this workspace):
            # fall through and start the workspace app on a free port instead
            # of previewing the wrong project.

    running = await asyncio.to_thread(
        discover_running_project_url,
        preferred_port=preferred_port,
        workspace=workspace,
    )
    if running:
        return running, None

    if not start_if_needed:
        return None, (
            "No project server is running. Start it from chat "
            "(open_preview will launch npm/vite/etc. for you)."
        )

    servers = discover_project_dev_servers(workspace)
    server = pick_server_for_url(servers, preferred_url)
    if server is None:
        return None, (
            "No runnable web app found in the workspace "
            "(package.json with a dev script, vite/next, or start.sh)."
        )

    _session_id, start_err, server = await start_project_dev_server(
        server,
        owner_session_key=owner_session_key,
    )
    if start_err:
        return None, start_err

    url = server.url or preferred_url
    if not url:
        # Started but unknown port - scan again after a short boot window.
        await asyncio.sleep(2.0)
        running = await asyncio.to_thread(
            discover_running_project_url, workspace=workspace
        )
        if running:
            return running, None
        return None, (
            f"Started `{server.command}` in {server.cwd}, but could not "
            "detect its HTTP port. Check the terminal output and pass url=."
        )

    if await wait_for_url(url, timeout_s=75.0):
        # The address it really answers on (::1 for a default Vite on Windows).
        return (loopback_url(server.port) if server.port else url), None

    # Port might have shifted (e.g. Vite fell back).
    running = await asyncio.to_thread(
        discover_running_project_url, workspace=workspace
    )
    if running:
        return running, None

    return None, (
        f"Started `{server.command}` in {server.cwd} but {url} never answered. "
        "Check dependencies / terminal logs, then call open_preview again."
    )
