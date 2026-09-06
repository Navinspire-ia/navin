"""After create/install: write env, start DBs, install deps, launch the app."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from navin.utils.proc import detached_no_window_kwargs, no_window_kwargs

_DB_PORTS = {5432, 54321, 27017, 6379, 3306, 9200, 9000}
_SKIP_ENV_PREFIXES = ("CLAUDE_", "FIGMA_", "NEXT_PUBLIC_ALGOLIA", "NEXT_PUBLIC_SEGMENT")
_SKIP_ENV_KEYS = {"HOME", "PATH", "USER", "PWD", "CI", "TMPDIR", "LANG", "DISPLAY", "HOSTNAME"}


def load_playbook(root: Path, slug: str) -> dict[str, Any]:
    for candidate in (
        root / ".navin" / "apps" / slug / "install.json",
        Path(__file__).resolve().parent / "packages" / slug / "install.json",
    ):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return data
    return {}


def has_app_source(root: Path, playbook: dict[str, Any]) -> bool:
    """True when the workspace looks like the template app, not just a Navin overlay."""
    for rel in playbook.get("env_files") or []:
        name = str(rel).strip()
        if name in {"", ".env"}:
            continue
        if (root / name).is_file():
            return True
    pkgs = playbook.get("packages") or {}
    for bucket in ("frontend", "backend", "all"):
        for pkg in pkgs.get(bucket) or []:
            rel = str(pkg.get("path") or "").strip()
            if rel and (root / rel).is_file():
                return True
    return False


def preview_url_from_playbook(playbook: dict[str, Any]) -> str | None:
    ports = [int(p) for p in (playbook.get("ports") or []) if str(p).isdigit() or isinstance(p, int)]
    for port in ports:
        if int(port) not in _DB_PORTS:
            return f"http://127.0.0.1:{int(port)}"
    launch = str((playbook.get("launch") or {}).get("dev") or "")
    if "5174" in launch or "dev:demo" in launch:
        return "http://127.0.0.1:5174"
    if "5173" in launch:
        return "http://127.0.0.1:5173"
    if "9000" in launch:
        return "http://127.0.0.1:9000"
    if "8000" in launch:
        return "http://127.0.0.1:8000"
    if "5000" in launch:
        return "http://127.0.0.1:5000"
    return "http://127.0.0.1:3000"


def write_env(root: Path, playbook: dict[str, Any]) -> str:
    dest = root / ".env"
    if dest.exists():
        return "exists"
    example = ""
    for rel in playbook.get("env_files") or []:
        src = root / str(rel)
        if src.is_file() and src.name != ".env":
            example = src.read_text(encoding="utf-8", errors="replace")
            break
    if example.strip():
        dest.write_text(example, encoding="utf-8")
        return "copied"
    lines: list[str] = []
    for row in playbook.get("env") or []:
        key = str(row.get("key") or "").strip()
        if not key or key in _SKIP_ENV_KEYS or key.startswith(_SKIP_ENV_PREFIXES):
            continue
        value = str(row.get("default") or "")
        if not value and row.get("secret"):
            value = f"change-me-{key.lower().replace('_', '-')}"
        if not value:
            continue
        lines.append(f"{key}={value}")
    if not lines:
        return "skipped"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "written"


def _run(cmd: str, *, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CI": "1", "npm_config_yes": "true"},
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()[-400:]
    return int(proc.returncode), out


def _spawn(cmd: str, *, cwd: Path) -> str:
    subprocess.Popen(
        cmd,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "CI": "1", "npm_config_yes": "true"},
        **detached_no_window_kwargs(),
    )
    return cmd


def _postgres_credentials(playbook: dict[str, Any]) -> tuple[str, str, str]:
    for row in playbook.get("env") or []:
        if str(row.get("key") or "") != "DATABASE_URL":
            continue
        raw = str(row.get("default") or "")
        parsed = urlparse(raw)
        if parsed.scheme.startswith("postgres"):
            user = parsed.username or "navin"
            password = parsed.password or "navin"
            db = (parsed.path or "/app").lstrip("/") or "app"
            return user, password, db
    return "navin", "navin", "app"


def start_databases(playbook: dict[str, Any], slug: str) -> list[str]:
    if shutil.which("docker") is None:
        return []
    started: list[str] = []
    pg_user, pg_password, pg_db = _postgres_credentials(playbook)
    for db in playbook.get("databases") or []:
        engine = str(db.get("engine") or "")
        if engine in {"", "none", "sqlite", "supabase"}:
            continue
        image = str(db.get("docker_image") or "")
        if engine == "redis" and not image:
            image = "redis:7-alpine"
        if not image:
            continue
        port = int(db.get("port") or 0)
        name = f"navin-{slug}-{engine}"
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            check=False,
            **no_window_kwargs(),
        )
        if inspect.returncode == 0 and "true" in (inspect.stdout or ""):
            started.append(f"{engine}:already")
            continue
        if inspect.returncode == 0:
            subprocess.run(
                ["docker", "start", name],
                capture_output=True,
                check=False,
                **no_window_kwargs(),
            )
            started.append(f"{engine}:started")
            continue
        args = ["docker", "run", "-d", "--name", name]
        if engine == "postgres":
            args.extend(
                [
                    "-e",
                    f"POSTGRES_PASSWORD={pg_password}",
                    "-e",
                    f"POSTGRES_USER={pg_user}",
                    "-e",
                    f"POSTGRES_DB={pg_db}",
                ]
            )
        if engine == "mysql":
            args.extend(["-e", "MYSQL_ROOT_PASSWORD=navin", "-e", "MYSQL_DATABASE=app"])
        if port:
            args.extend(["-p", f"{port}:{port}"])
        args.append(image)
        code, _ = _run(" ".join(args), cwd=Path("."), timeout=40)
        started.append(f"{engine}:ok" if code == 0 else f"{engine}:failed")
    return started


def install_packages(root: Path, playbook: dict[str, Any]) -> list[str]:
    system = playbook.get("system") or {}
    lock = str(system.get("package_manager") or "npm")
    install_cmd = {"pnpm": "pnpm install", "yarn": "yarn", "bun": "bun install"}.get(lock, "npm install")
    done: list[str] = []
    pkgs = playbook.get("packages") or {}
    paths: list[Path] = []
    for bucket in ("frontend", "backend"):
        for pkg in pkgs.get(bucket) or []:
            rel = str(pkg.get("path") or "")
            folder = root / Path(rel).parent if rel.endswith("package.json") else root / rel
            if folder.is_dir():
                paths.append(folder)
    if (root / "package.json").is_file():
        paths.insert(0, root)
    seen: set[Path] = set()
    for folder in paths:
        resolved = folder.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not (resolved / "package.json").is_file():
            continue
        if (resolved / "node_modules").is_dir():
            done.append(f"node:{resolved.name}:cached")
            continue
        code, _ = _run(install_cmd, cwd=resolved, timeout=180)
        done.append(f"node:{resolved.name}:{'ok' if code == 0 else 'failed'}")
    if system.get("python"):
        req = root / "requirements.txt"
        pyproject = root / "pyproject.toml"
        if req.is_file() or pyproject.is_file():
            cmd = "python3 -m pip install -r requirements.txt" if req.is_file() else "python3 -m pip install -e ."
            code, _ = _run(cmd, cwd=root, timeout=180)
            done.append(f"python:{'ok' if code == 0 else 'failed'}")
    if system.get("prisma"):
        prisma_root = root / "backend" if (root / "backend" / "prisma").is_dir() else root
        if (prisma_root / "prisma").is_dir() or (prisma_root / "schema.prisma").is_file():
            _run("npx prisma generate", cwd=prisma_root, timeout=60)
            code, _ = _run("npx prisma migrate deploy", cwd=prisma_root, timeout=60)
            done.append(f"prisma:{'ok' if code == 0 else 'skip'}")
    return done


def _launch_commands(playbook: dict[str, Any]) -> list[str]:
    launch = playbook.get("launch") or {}
    raw = str(launch.get("dev") or "").strip()
    if not raw:
        return ["npm run dev"]
    parts = [raw]
    for sep in ("  (and  ", " (and ", " AND ", " ; "):
        if sep in raw:
            parts = [item.strip(" )") for item in raw.split(sep) if item.strip()]
            break
    return [item for item in parts if item and not item.startswith("(")]


def start_app_processes(root: Path, playbook: dict[str, Any]) -> list[str]:
    started: list[str] = []
    for cmd in _launch_commands(playbook)[:3]:
        cwd = root
        work = cmd
        if work.startswith("cd ") and " && " in work:
            folder, rest = work[3:].split(" && ", 1)
            candidate = root / folder.strip()
            if candidate.is_dir():
                cwd = candidate
                work = rest.strip()
        started.append(_spawn(work, cwd=cwd))
    return started


def _prepare_runtime(
    root: Path,
    playbook: dict[str, Any],
    slug: str,
    *,
    start: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "databases": start_databases(playbook, slug),
        "packages": install_packages(root, playbook),
        "started": [],
        "preview_url": None,
    }
    migrate = str((playbook.get("launch") or {}).get("migrate") or "")
    if migrate:
        _run(migrate, cwd=root, timeout=90)
    seed = str((playbook.get("launch") or {}).get("seed") or "")
    if seed:
        _run(seed, cwd=root, timeout=90)
    auto = playbook.get("auto_start", True)
    if start and auto is not False:
        report["started"] = start_app_processes(root, playbook)
        report["preview_url"] = preview_url_from_playbook(playbook)
    elif start:
        report["preview_url"] = preview_url_from_playbook(playbook)
    return report


def bootstrap_and_start(
    root: Path | str,
    slug: str,
    *,
    start: bool = True,
    wait: bool = True,
    allow_env_without_source: bool = False,
) -> dict[str, Any]:
    """Prepare env/DB/deps and start the template. Safe to call after create/install."""
    workspace = Path(root).expanduser().resolve()
    playbook = load_playbook(workspace, slug)
    report: dict[str, Any] = {
        "env": "skipped",
        "databases": [],
        "packages": [],
        "started": [],
        "preview_url": None,
        "playbook": bool(playbook),
        "has_source": False,
    }
    if not playbook:
        return report
    source = has_app_source(workspace, playbook)
    report["has_source"] = source
    if not source and not allow_env_without_source:
        report["env"] = "no-source"
        return report
    report["env"] = write_env(workspace, playbook)
    if not source:
        return report
    if not start:
        return report
    if not wait:
        def _job() -> None:
            try:
                _prepare_runtime(workspace, playbook, slug, start=True)
            except Exception:
                pass

        threading.Thread(target=_job, name=f"navin-start-{slug}", daemon=True).start()
        report["started"] = ["starting"]
        report["preview_url"] = preview_url_from_playbook(playbook)
        return report
    extra = _prepare_runtime(workspace, playbook, slug, start=True)
    report.update(extra)
    return report
