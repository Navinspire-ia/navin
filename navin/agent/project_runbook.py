# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The project's own commands, handed to the agent every turn.

Without it the agent guessed: it listed folders, read package.json three
times, tried `npm run dev` in the wrong directory, then explored again. This
reads the manifests a project already has (package.json, pyproject,
go.mod, Cargo.toml, pom.xml, *.csproj, composer.json, Gemfile, deno.json,
pubspec.yaml, mix.exs, compose files, Makefile, justfile, start scripts)
and writes the exact install / run / build / test / lint commands, the
folder to run them from, the port, and whether dependencies are missing.

Pure file reads, no subprocess: cheap enough for every turn, cached by the
manifests' mtimes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

MANIFESTS = (
    "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "manage.py",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
    "Gemfile", "deno.json", "deno.jsonc", "pubspec.yaml", "mix.exs", "docker-compose.yml",
    "docker-compose.yaml", "compose.yml", "compose.yaml", "Makefile", "justfile", "Justfile",
    "start.sh", "run.sh", "dev.sh", "start.ps1", "dev.ps1", "run.ps1", "start.bat", "start.cmd",
    "Taskfile.yml", "Taskfile.yaml", "taskfile.yml", "CMakeLists.txt", "pnpm-workspace.yaml",
    "turbo.json", "nx.json",
)
_SUBPROJECT_HINTS = (
    "frontend", "backend", "web", "client", "server", "api", "app", "ui", "site", "mobile",
)
_SUBPROJECT_PARENTS = ("apps", "packages", "services", "libs")
_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".venv", "venv", "env", "dist", "build", "target", ".next",
    "vendor", "__pycache__", ".navin", ".idea", ".vscode", "coverage",
})
_MAX_DIRS = 12
_IS_WINDOWS = sys.platform == "win32"
_MAKE_TARGETS = (
    "all", "install", "setup", "deps", "dev", "run", "serve", "start", "up", "build", "test", "check",
    "lint", "format", "fmt", "typecheck", "down", "stop", "migrate", "deploy", "logs",
)


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_any(folder: Path, names: tuple[str, ...]) -> bool:
    return any((folder / name).exists() for name in names)


def _venv(folder: Path) -> Path | None:
    for name in (".venv", "venv", "env"):
        candidate = folder / name
        if (candidate / "bin" / "python").exists() or (candidate / "Scripts" / "python.exe").exists():
            return candidate
    return None


def _venv_python() -> str:
    return ".venv\\Scripts\\python.exe" if _IS_WINDOWS else ".venv/bin/python"


def _wrapper(folder: Path, unix: str, windows: str, fallback: str) -> str:
    """./mvnw, ./gradlew and the like, in the host's own form."""
    if _IS_WINDOWS and (folder / windows).exists():
        return f".\\{windows}"
    if (folder / unix).exists():
        return f"./{unix}"
    return fallback


def host_line() -> str:
    """Which shell exec talks to, so commands come in the right form."""
    if _IS_WINDOWS:
        return (
            "Host: Windows. Use Windows forms: .\\script.ps1 (powershell -File), "
            ".\\gradlew.bat, .\\mvnw.cmd, .venv\\Scripts\\python.exe, `;` to chain in "
            "Windows PowerShell 5, $env:NAME=value for variables."
        )
    try:
        wsl = "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        wsl = False
    if sys.platform == "darwin":
        return "Host: macOS (zsh/bash, BSD tools: sed -i '' ..., no GNU-only flags)."
    line = "Host: Linux under WSL (bash)." if wsl else "Host: Linux (bash)."
    if _in_container():
        line += (
            " Navin itself runs inside a container: localhost is the container, and "
            "tools installed on the host machine are not visible here."
        )
    return line


def _in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "libpod"))


# --- one section per stack: list of (label, command) -----------------------------


def _node(folder: Path) -> list[tuple[str, str]]:
    pkg = _json(folder / "package.json")
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    if (folder / "pnpm-lock.yaml").exists():
        manager, run = "pnpm", "pnpm"
    elif (folder / "yarn.lock").exists():
        manager, run = "yarn", "yarn"
    elif (folder / "bun.lockb").exists() or (folder / "bun.lock").exists():
        manager, run = "bun", "bun run"
    else:
        manager, run = "npm", "npm run"
    rows: list[tuple[str, str]] = []
    install = f"{manager} install"
    if not (folder / "node_modules").is_dir():
        install += "   <- node_modules missing: run this first"
    rows.append(("Install", install))
    deps = json.dumps({**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})})
    wanted = [
        ("Dev server", ("dev", "start", "serve")),
        ("Build", ("build",)),
        ("Test", ("test", "test:unit")),
        ("E2E", ("test:e2e", "e2e")),
        ("Lint", ("lint",)),
        ("Type check", ("typecheck", "type-check", "check-types", "tsc")),
        ("Format", ("format", "fmt")),
        ("Preview build", ("preview",)),
        ("Deploy", ("deploy",)),
    ]
    for label, names in wanted:
        name = next((n for n in names if n in scripts), None)
        if name:
            rows.append((label, f"{run} {name}" if not (manager == "npm" and name in ("start", "test")) else f"npm {name}"))
    if not any(label == "Test" for label, _ in rows):
        if '"vitest"' in deps:
            rows.append(("Test", "npx vitest run"))
        elif '"jest"' in deps:
            rows.append(("Test", "npx jest"))
    if not any(label == "Type check" for label, _ in rows) and (folder / "tsconfig.json").exists():
        rows.append(("Type check", "npx tsc --noEmit"))
    return rows


def _python(folder: Path) -> list[tuple[str, str]]:
    pyproject = _read(folder / "pyproject.toml")
    venv = _venv(folder)
    if venv is None:
        python = "python"
    elif _IS_WINDOWS:
        python = f"{venv.name}\\Scripts\\python.exe"
    else:
        python = f"{venv.name}/bin/python"
    rows: list[tuple[str, str]] = []
    if (folder / "uv.lock").exists() or "[tool.uv" in pyproject:
        rows.append(("Install", "uv sync"))
        python = "uv run python"
    elif "[tool.poetry" in pyproject:
        rows.append(("Install", "poetry install"))
        python = "poetry run python"
    elif (folder / "Pipfile").exists():
        rows.append(("Install", "pipenv install"))
        python = "pipenv run python"
    elif "[tool.hatch" in pyproject:
        rows.append(("Install", "hatch env create"))
        python = "hatch run python"
    elif (folder / "requirements.txt").exists():
        install = f"{python} -m pip install -r requirements.txt"
        if venv is None:
            install = f"python -m venv .venv && {_venv_python()} -m pip install -r requirements.txt"
            install += "   <- no virtualenv yet"
        rows.append(("Install", install))
    elif pyproject or (folder / "setup.py").exists():
        install = f"{python} -m pip install -e ."
        if venv is None:
            install = f"python -m venv .venv && {_venv_python()} -m pip install -e .   <- no virtualenv yet"
        rows.append(("Install", install))
    if (folder / "manage.py").exists():
        rows.append(("Dev server", f"{python} manage.py runserver 127.0.0.1:8000"))
        rows.append(("Migrate", f"{python} manage.py migrate"))
    else:
        for module, path in (("app.main:app", "app/main.py"), ("main:app", "main.py"), ("app:app", "app.py")):
            text = _read(folder / path, 40_000)
            if "FastAPI(" in text or "Starlette(" in text:
                rows.append(("Dev server", f"{python} -m uvicorn {module} --reload --port 8000"))
                break
            if "Flask(" in text:
                rows.append(("Dev server", f"{python} -m flask --app {path[:-3].replace('/', '.')} run --port 5000"))
                break
            if text:
                rows.append(("Run", f"{python} {path}"))
                break
    if (
        _has_any(folder, ("tests", "test", "pytest.ini", "conftest.py"))
        or "[tool.pytest" in pyproject
    ):
        rows.append(("Test", "hatch test" if python == "hatch run python" else f"{python} -m pytest -q"))
    if "[tool.ruff" in pyproject or (folder / "ruff.toml").exists():
        rows.append(("Lint", f"{python} -m ruff check ."))
    if "[tool.mypy" in pyproject or (folder / "mypy.ini").exists():
        rows.append(("Type check", f"{python} -m mypy ."))
    return rows


def _go(folder: Path) -> list[tuple[str, str]]:
    rows = [("Install", "go mod download"), ("Build", "go build ./..."), ("Test", "go test ./...")]
    if (folder / "main.go").exists():
        rows.insert(1, ("Run", "go run ."))
    elif (folder / "cmd").is_dir():
        mains = sorted(p.parent.name for p in (folder / "cmd").glob("*/main.go"))
        if mains:
            rows.insert(1, ("Run", f"go run ./cmd/{mains[0]}"))
    rows.append(("Lint", "go vet ./..."))
    return rows


def _rust(folder: Path) -> list[tuple[str, str]]:
    cargo = _read(folder / "Cargo.toml")
    if "[workspace]" in cargo and "[package]" not in cargo:
        return [
            ("Install", "cargo fetch"), ("Build", "cargo build --workspace"),
            ("Test", "cargo test --workspace"), ("Lint", "cargo clippy --workspace"),
        ]
    return [
        ("Install", "cargo fetch"), ("Run", "cargo run"), ("Build", "cargo build"),
        ("Test", "cargo test"), ("Lint", "cargo clippy"),
    ]


def _java(folder: Path) -> list[tuple[str, str]]:
    if (folder / "pom.xml").exists():
        mvn = _wrapper(folder, "mvnw", "mvnw.cmd", "mvn")
        rows = [("Install", f"{mvn} -q dependency:resolve"), ("Build", f"{mvn} -q package -DskipTests"), ("Test", f"{mvn} test")]
        if "spring-boot" in _read(folder / "pom.xml"):
            rows.insert(1, ("Dev server", f"{mvn} spring-boot:run"))
        return rows
    gradle = _wrapper(folder, "gradlew", "gradlew.bat", "gradle")
    text = _read(folder / "build.gradle") + _read(folder / "build.gradle.kts")
    rows = [("Build", f"{gradle} build -x test"), ("Test", f"{gradle} test")]
    if "org.springframework.boot" in text:
        rows.insert(0, ("Dev server", f"{gradle} bootRun"))
    elif "application" in text:
        rows.insert(0, ("Run", f"{gradle} run"))
    return rows


def _dotnet(folder: Path) -> list[tuple[str, str]]:
    return [
        ("Install", "dotnet restore"), ("Run", "dotnet run"), ("Build", "dotnet build"),
        ("Test", "dotnet test"),
    ]


def _php(folder: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if (folder / "composer.json").exists():
        install = "composer install"
        if not (folder / "vendor").is_dir():
            install += "   <- vendor/ missing: run this first"
        rows.append(("Install", install))
    if (folder / "artisan").exists():
        rows += [
            ("Dev server", "php artisan serve --host=127.0.0.1 --port=8000"),
            ("Migrate", "php artisan migrate"),
            ("Test", "php artisan test"),
        ]
    else:
        if (folder / "public" / "index.php").exists():
            rows.append(("Dev server", "php -S 127.0.0.1:8000 -t public"))
        if _has_any(folder, ("phpunit.xml", "phpunit.xml.dist")):
            rows.append(("Test", "vendor/bin/phpunit"))
    return rows


def _ruby(folder: Path) -> list[tuple[str, str]]:
    gemfile = _read(folder / "Gemfile")
    rows = [("Install", "bundle install")]
    if "rails" in gemfile:
        rails = "bin/rails" if (folder / "bin" / "rails").exists() else "bundle exec rails"
        rows += [("Dev server", f"{rails} server -b 127.0.0.1 -p 3000"), ("Migrate", f"{rails} db:migrate")]
    if (folder / "spec").is_dir():
        rows.append(("Test", "bundle exec rspec"))
    elif "rails" in gemfile:
        rows.append(("Test", "bin/rails test"))
    return rows


def _deno(folder: Path) -> list[tuple[str, str]]:
    config = _json(folder / "deno.json") or _json(folder / "deno.jsonc")
    tasks = config.get("tasks") if isinstance(config.get("tasks"), dict) else {}
    rows = [(f"Task {name}", f"deno task {name}") for name in list(tasks)[:8]]
    rows += [("Test", "deno test"), ("Lint", "deno lint")]
    return rows


def _dart(folder: Path) -> list[tuple[str, str]]:
    flutter = "flutter:" in _read(folder / "pubspec.yaml")
    tool = "flutter" if flutter else "dart"
    rows = [("Install", f"{tool} pub get"), ("Run", f"{tool} run"), ("Test", f"{tool} test"), ("Lint", f"{tool} analyze")]
    if flutter:
        rows.insert(2, ("Build", "flutter build web"))
    return rows


def _elixir(folder: Path) -> list[tuple[str, str]]:
    phoenix = "phoenix" in _read(folder / "mix.exs")
    return [
        ("Install", "mix deps.get"),
        ("Dev server" if phoenix else "Run", "mix phx.server" if phoenix else "mix run"),
        ("Test", "mix test"),
    ]


def _compose(folder: Path) -> list[tuple[str, str]]:
    return [
        ("Start services", "docker compose up -d"),
        ("Status", "docker compose ps"),
        ("Logs", "docker compose logs --tail=100"),
        # stop keeps containers, networks and volumes; down removes them.
        ("Stop services", "docker compose stop"),
        ("Remove services", "docker compose down   <- deletes containers and networks: only when the user asks"),
    ]


def _make(folder: Path) -> list[tuple[str, str]]:
    name = "Makefile" if (folder / "Makefile").exists() else ""
    tool = "make"
    if not name:
        name = "justfile" if (folder / "justfile").exists() else "Justfile"
        tool = "just"
    text = _read(folder / name)
    targets = re.findall(r"^([A-Za-z][\w-]*)\s*:(?!=)", text, re.MULTILINE)
    keep = [t for t in dict.fromkeys(targets) if t in _MAKE_TARGETS]
    return [(f"{tool} {target}", f"{tool} {target}") for target in keep]


def _node_runner(folder: Path) -> str:
    if (folder / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (folder / "yarn.lock").exists():
        return "yarn"
    if (folder / "bun.lockb").exists() or (folder / "bun.lock").exists():
        return "bun run"
    return "npm run"


def _monorepo(folder: Path) -> list[tuple[str, str]]:
    """Workspace orchestrators at a monorepo root: one command runs every package."""
    pkg = _json(folder / "package.json")
    deps = json.dumps({**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})})
    rows: list[tuple[str, str]] = []
    if (folder / "turbo.json").exists() or '"turbo"' in deps:
        turbo = "pnpm turbo" if (folder / "pnpm-lock.yaml").exists() else "npx turbo"
        rows += [(f"All packages {task}", f"{turbo} run {task}") for task in ("build", "dev", "test", "lint")]
    if (folder / "nx.json").exists() or '"nx"' in deps:
        rows += [(f"All packages {task}", f"npx nx run-many -t {task}") for task in ("build", "test", "lint")]
    if not rows and ((folder / "pnpm-workspace.yaml").exists() or pkg.get("workspaces")):
        if (folder / "pnpm-lock.yaml").exists() or (folder / "pnpm-workspace.yaml").exists():
            rows += [(f"All packages {task}", f"pnpm -r run {task}") for task in ("build", "test", "lint")]
        elif (folder / "yarn.lock").exists():
            rows += [(f"All packages {task}", f"yarn workspaces foreach -A run {task}") for task in ("build", "test")]
        else:
            rows += [(f"All packages {task}", f"npm run {task} --workspaces --if-present") for task in ("build", "test", "lint")]
    return rows


def _workspace_globs(root: Path) -> list[str]:
    """Package folders a JS monorepo or a Cargo workspace declares."""
    globs: list[str] = []
    text = _read(root / "pnpm-workspace.yaml")
    globs += re.findall(r"^\s*-\s*['\"]?([^'\"#\n]+?)['\"]?\s*$", text, re.MULTILINE)
    workspaces = _json(root / "package.json").get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list):
        globs += [str(item) for item in workspaces]
    members = re.search(r"members\s*=\s*\[([^\]]*)\]", _read(root / "Cargo.toml"), re.DOTALL)
    if members:
        globs += re.findall(r"['\"]([^'\"]+)['\"]", members.group(1))
    return [g.strip().rstrip("/") for g in globs if g.strip() and not g.strip().startswith("!")]


def _taskfile(folder: Path) -> list[tuple[str, str]]:
    name = next((n for n in ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml") if (folder / n).exists()), "")
    text = _read(folder / name)
    block = text.split("tasks:", 1)[1] if "tasks:" in text else ""
    tasks = re.findall(r"^  ([A-Za-z][\w:-]*)\s*:", block, re.MULTILINE)
    return [(f"task {t}", f"task {t}") for t in dict.fromkeys(tasks) if t.split(":")[0] in _MAKE_TARGETS][:10]


def _cmake(folder: Path) -> list[tuple[str, str]]:
    rows = [
        ("Configure", "cmake -S . -B build"),
        ("Build", "cmake --build build"),
    ]
    if "enable_testing" in _read(folder / "CMakeLists.txt") or "add_test" in _read(folder / "CMakeLists.txt"):
        rows.append(("Test", "ctest --test-dir build --output-on-failure"))
    return rows


def _tauri(folder: Path) -> list[tuple[str, str]]:
    scripts = _json(folder / "package.json").get("scripts") or {}
    if isinstance(scripts, dict) and "tauri" in scripts:
        run = _node_runner(folder)
        return [("Desktop app dev", f"{run} tauri dev"), ("Desktop app build", f"{run} tauri build")]
    return [("Desktop app dev", "cargo tauri dev"), ("Desktop app build", "cargo tauri build")]


_README_TOOLS = re.compile(
    r"^(?:\$\s*)?((?:npm|pnpm|yarn|bun|npx|uv|poetry|pip|pipenv|python3?|go|cargo|make|just|task|docker|"
    r"docker-compose|mvn|\./mvnw|gradle|\./gradlew|dotnet|php|composer|bundle|bin/rails|rails|deno|flutter|"
    r"dart|mix|cmake|ctest|kubectl|helm|terraform)\b[^\n]{0,120})$",
    re.MULTILINE,
)


def readme_commands(folder: Path) -> list[str]:
    """Commands shown in the README's code blocks (low confidence: prose can lag)."""
    for name in ("README.md", "README.MD", "Readme.md", "readme.md", "CONTRIBUTING.md", "docs/DEVELOPMENT.md"):
        text = _read(folder / name, 80_000)
        if not text:
            continue
        blocks = re.findall(r"```(?:[a-zA-Z0-9_-]*)\n(.*?)```", text, re.DOTALL)
        found: list[str] = []
        for block in blocks:
            for match in _README_TOOLS.finditer(block):
                command = match.group(1).strip()
                if command not in found:
                    found.append(command)
        if found:
            return found[:12]
    return []


_SCRIPT_NAMES = ("start", "dev", "run", "build", "test", "setup", "install", "deploy")


def _scripts(folder: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for stem in _SCRIPT_NAMES:
        if (folder / f"{stem}.sh").exists():
            rows.append((f"Script {stem}.sh", f"bash {stem}.sh"))
        if (folder / f"{stem}.ps1").exists():
            rows.append((f"Script {stem}.ps1", f"pwsh -File {stem}.ps1" if not _IS_WINDOWS else f"powershell -ExecutionPolicy Bypass -File .\\{stem}.ps1"))
        for ext in ("bat", "cmd"):
            if (folder / f"{stem}.{ext}").exists():
                rows.append((f"Script {stem}.{ext}", f".\\{stem}.{ext}" if _IS_WINDOWS else f"cmd /c {stem}.{ext}  (Windows only)"))
    return rows


_STACKS: tuple[tuple[str, tuple[str, ...], Any], ...] = (
    ("Node", ("package.json",), _node),
    ("Python", ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile", "manage.py"), _python),
    ("Go", ("go.mod",), _go),
    ("Rust", ("Cargo.toml",), _rust),
    ("Java", ("pom.xml", "build.gradle", "build.gradle.kts"), _java),
    ("PHP", ("composer.json", "artisan"), _php),
    ("Ruby", ("Gemfile",), _ruby),
    ("Deno", ("deno.json", "deno.jsonc"), _deno),
    ("Dart/Flutter", ("pubspec.yaml",), _dart),
    ("Elixir", ("mix.exs",), _elixir),
    ("Docker Compose", ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"), _compose),
    ("Make/just", ("Makefile", "justfile", "Justfile"), _make),
    ("Taskfile", ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml"), _taskfile),
    ("CMake", ("CMakeLists.txt",), _cmake),
    ("Tauri", ("src-tauri",), _tauri),
    ("Monorepo", ("pnpm-workspace.yaml", "turbo.json", "nx.json"), _monorepo),
    ("Scripts", tuple(f"{stem}.{ext}" for stem in _SCRIPT_NAMES for ext in ("sh", "ps1", "bat", "cmd")), _scripts),
)


# --- infrastructure, cloud and CI ---------------------------------------------------

_INFRA_DIRS = (
    "", "k8s", "kubernetes", "kube", "manifests", "deploy", "deployment", "deployments", "helm",
    "charts", "chart", "infra", "infrastructure", "terraform", "tf", "iac", "ops", "cdk", ".k8s",
)
_REAL = "   <- deploys to real infrastructure"


def _rel(root: Path, folder: Path) -> str:
    return "." if folder == root else folder.relative_to(root).as_posix()


def _infra_dirs(root: Path) -> list[Path]:
    """The root, the usual infra folders, and their direct sub-folders (deploy/k8s...)."""
    dirs: list[Path] = [root]
    for name in _INFRA_DIRS:
        if not name:
            continue
        folder = root / name
        if not folder.is_dir():
            continue
        if folder not in dirs:
            dirs.append(folder)
        try:
            children = sorted(
                p for p in folder.iterdir()
                if p.is_dir() and p.name not in _SKIP_DIRS and not p.name.startswith(".")
            )
        except OSError:
            children = []
        for child in children[:16]:
            if child not in dirs:
                dirs.append(child)
    return dirs[:48]


def _k8s_manifests(folder: Path) -> bool:
    try:
        files = list(folder.glob("*.y*ml"))[:30]
    except OSError:
        return False
    for path in files:
        text = _read(path, 4000)
        if "apiVersion:" in text and "kind:" in text and "AWS::" not in text:
            return True
    return False


def _infra_rows(root: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for folder in _infra_dirs(root):
        where = _rel(root, folder)
        rows: list[tuple[str, str]] = []
        has = lambda *names: _has_any(folder, names)  # noqa: E731
        if has("Chart.yaml"):
            name = folder.name
            rows += [("Helm lint", f"helm lint {where}"), ("Helm render", f"helm template {name} {where}"),
                     ("Helm deploy", f"helm upgrade --install {name} {where}{_REAL}")]
        if has("kustomization.yaml", "kustomization.yml", "Kustomization"):
            rows += [("Kustomize render", f"kubectl kustomize {where}"), ("Kustomize deploy", f"kubectl apply -k {where}{_REAL}")]
        elif not has("Chart.yaml") and _k8s_manifests(folder):
            rows += [("Kubernetes check", f"kubectl apply --dry-run=client -f {where}"),
                     ("Kubernetes deploy", f"kubectl apply -f {where}{_REAL}"),
                     ("Kubernetes status", "kubectl get pods,svc,deploy")]
        if has("skaffold.yaml"):
            rows += [("Skaffold dev", "skaffold dev"), ("Skaffold deploy", f"skaffold run{_REAL}")]
        if has("Tiltfile"):
            rows.append(("Tilt", "tilt up"))
        try:
            terraform = any(folder.glob("*.tf"))
        except OSError:
            terraform = False
        if terraform:
            tf = "tofu" if (folder / ".terraform.lock.hcl").exists() and "opentofu" in _read(folder / ".terraform.lock.hcl", 4000) else "terraform"
            rows += [("Terraform init", f"{tf} -chdir={where} init"), ("Terraform check", f"{tf} -chdir={where} validate"),
                     ("Terraform plan", f"{tf} -chdir={where} plan"), ("Terraform apply", f"{tf} -chdir={where} apply -auto-approve{_REAL}")]
        if has("Pulumi.yaml"):
            rows += [("Pulumi preview", "pulumi preview"), ("Pulumi deploy", f"pulumi up --yes{_REAL}")]
        if has("serverless.yml", "serverless.yaml", "serverless.ts"):
            rows += [("Serverless local", "npx serverless offline"), ("Serverless deploy", f"npx serverless deploy{_REAL}")]
        for template in ("template.yaml", "template.yml"):
            if "AWS::Serverless" in _read(folder / template, 20_000):
                rows += [("SAM build", "sam build"), ("SAM local", "sam local start-api"), ("SAM deploy", f"sam deploy --no-confirm-changeset{_REAL}")]
                break
        if has("cdk.json"):
            rows += [("CDK synth", "npx cdk synth"), ("CDK diff", "npx cdk diff"), ("CDK deploy", f"npx cdk deploy --require-approval never{_REAL}")]
        if (folder / "amplify").is_dir():
            rows += [("Amplify status", "amplify status"), ("Amplify deploy", f"amplify push --yes{_REAL}")]
        if "runtime:" in _read(folder / "app.yaml", 4000):
            rows.append(("App Engine deploy", f"gcloud app deploy --quiet{_REAL}"))
        if has("cloudbuild.yaml", "cloudbuild.yml"):
            rows.append(("Cloud Build deploy", f"gcloud builds submit{_REAL}"))
        if has("firebase.json"):
            rows += [("Firebase local", "firebase emulators:start"), ("Firebase deploy", f"firebase deploy --non-interactive{_REAL}")]
        if has("azure.yaml"):
            rows += [("Azure (azd) deploy", f"azd up --no-prompt{_REAL}")]
        if has("host.json") and has("local.settings.json", "function_app.py", "package.json", "requirements.txt"):
            rows += [("Azure Functions local", "func start"), ("Azure Functions deploy", f"func azure functionapp publish <app-name>{_REAL}")]
        if has("vercel.json") or (folder / ".vercel").is_dir():
            rows += [("Vercel local", "vercel dev"), ("Vercel deploy", f"vercel deploy --prod --yes{_REAL}")]
        if has("netlify.toml"):
            rows += [("Netlify local", "netlify dev"), ("Netlify deploy", f"netlify deploy --prod{_REAL}")]
        if has("fly.toml"):
            rows.append(("Fly deploy", f"fly deploy{_REAL}"))
        if has("railway.json", "railway.toml"):
            rows.append(("Railway deploy", f"railway up{_REAL}"))
        if has("render.yaml"):
            rows.append(("Render", "render.yaml blueprint: deploys on git push to the linked branch"))
        if has("Procfile"):
            rows.append(("Procfile local", "heroku local  (or foreman start)"))
        if has("wrangler.toml", "wrangler.json", "wrangler.jsonc"):
            rows += [("Cloudflare local", "npx wrangler dev"), ("Cloudflare deploy", f"npx wrangler deploy{_REAL}")]
        if has("Dockerfile"):
            image = re.sub(r"[^a-z0-9_.-]", "-", (root.name if folder == root else folder.name).lower()) or "app"
            exposed = re.search(r"^\s*EXPOSE\s+(\d+)", _read(folder / "Dockerfile", 20_000), re.MULTILINE)
            port = exposed.group(1) if exposed else ""
            rows.append(("Docker build", f"docker build -t {image} {where}"))
            rows.append(("Docker run", f"docker run --rm {'-p ' + port + ':' + port + ' ' if port else ''}{image}"))
        if rows:
            groups.append((where, rows))
    return groups


_CI_FILES = (
    ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", "Jenkinsfile",
    ".circleci/config.yml", ".drone.yml", "cloudbuild.yaml",
)
_CI_SKIP = re.compile(r"^(echo|cd|ls|cat|pwd|export|set |mkdir|cp |mv |rm |source|true|exit|if |fi$|then|else|done|for |do$)")


def _run_steps(text: str) -> list[str]:
    """Shell lines of `run:` / `script:` steps, block scalars (`run: |`) included."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    key = re.compile(r"^(\s*)(?:-\s+)?(run|script|command|sh)\s*:\s*(.*)$")
    while i < len(lines):
        match = key.match(lines[i])
        if not match:
            i += 1
            continue
        indent, value = len(match.group(1)), match.group(3).strip()
        i += 1
        if value.startswith(">"):
            # Folded scalar: its lines are one command joined by spaces.
            parts: list[str] = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                if lines[i].strip():
                    parts.append(lines[i].strip())
                i += 1
            if parts:
                out.append(" ".join(parts))
        elif value in ("|", "|-", "|+", ""):
            continued = False
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                stripped = lines[i].strip()
                if stripped.startswith("- "):
                    stripped = stripped[2:].strip()
                if continued:
                    # The tail of a `\`-continued command, not a command of its own.
                    continued = stripped.endswith("\\")
                elif stripped and not stripped.startswith("#") and not re.match(r"^[\w-]+:\s", stripped):
                    out.append(stripped)
                    continued = stripped.endswith("\\")
                i += 1
        else:
            out.append(value)
    return out


def _ci_commands(root: Path) -> list[str]:
    """Commands the project's CI runs: proven to work, whatever the stack."""
    files: list[Path] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        try:
            files += sorted(workflows.glob("*.y*ml"))[:6]
        except OSError:
            pass
    files += [root / name for name in _CI_FILES if (root / name).is_file()]
    commands: list[str] = []
    for path in files:
        text = _read(path, 60_000)
        found = re.findall(r"\bsh\s+['\"]([^'\"]+)['\"]", text) if path.name == "Jenkinsfile" else _run_steps(text)
        for raw in found:
            command = raw.strip()
            if (
                not command
                or command[0] in "|>{}[]"
                or _CI_SKIP.match(command)
                or "${{" in command
                or command.endswith("\\")
                or len(command) > 140
            ):
                continue
            if command not in commands:
                commands.append(command)
    return commands[:14]


def _dotnet_present(folder: Path) -> bool:
    try:
        return any(folder.glob("*.csproj")) or any(folder.glob("*.sln")) or any(folder.glob("*.fsproj"))
    except OSError:
        return False


def _project_dirs(root: Path) -> list[Path]:
    """The root and the sub-projects that hold their own manifest."""
    found: list[Path] = []

    def consider(folder: Path) -> None:
        if len(found) >= _MAX_DIRS or folder in found or folder.name in _SKIP_DIRS:
            return
        if _has_any(folder, MANIFESTS) or _dotnet_present(folder):
            found.append(folder)

    consider(root)
    # Packages a monorepo / Cargo workspace declares come first: they are the units.
    for pattern in _workspace_globs(root):
        try:
            matches = sorted(root.glob(pattern)) if any(ch in pattern for ch in "*?[") else [root / pattern]
        except (OSError, ValueError):
            continue
        for match in matches:
            if match.is_dir():
                consider(match)
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        children = []
    ordered = sorted(children, key=lambda p: (p.name.lower() not in _SUBPROJECT_HINTS, p.name.lower()))
    for child in ordered:
        if child.name in _SUBPROJECT_PARENTS:
            try:
                for grand in sorted(p for p in child.iterdir() if p.is_dir()):
                    consider(grand)
            except OSError:
                pass
        else:
            consider(child)
    return found


def _dev_port(folder: Path) -> str:
    """Port of the dev server as start_app sees it (same detector)."""
    try:
        from navin.agent.tools.preview_server import _detect_server

        server = _detect_server(folder)
    except Exception:
        return ""
    return str(server.port) if server is not None and server.port else ""


def _signature(root: Path) -> tuple[Any, ...]:
    return bridged_memo("signature", root, lambda: _signature_uncached(root))


# On a WSL project opened from Windows, reading the project layout is a few
# thousand 9P stats (3 s on navin's own repo) and it ran on every project
# tool call and every turn. There the answer is kept until the layout
# changes: one listing inside the distribution (a few tenths of a second)
# tells whether a manifest, a CI file or a folder moved. Without that listing
# the answer is reused for BRIDGED_TTL_S. Local projects are unaffected.
BRIDGED_TTL_S = 15.0
# Calls this close together share one layout check (resolve + runbook of
# one tool call).
_BRIDGED_RECHECK_S = 2.0
_BRIDGED_MEMO: dict[tuple[str, str], tuple[float, int | None, Any]] = {}
_LAYOUT_DEPTH = 3


def _layout_names() -> set[str]:
    names = set(MANIFESTS) | {PurePosixPath(ci).name for ci in _CI_FILES}
    for _stack, markers, _detector in _STACKS:
        names.update(markers)
    return names | {"Dockerfile*", "*.tf", "*.y*ml", "README*", "readme*", "*.csproj", "*.sln", "*.fsproj"}


def bridged_memo(kind: str, root: Path, compute: Any) -> Any:
    """``compute()``, reused on a \\\\wsl.localhost root while its layout holds."""
    from navin.index.service import is_bridged_root

    if not is_bridged_root(root):
        return compute()
    from navin.utils import wsl_bulk

    key = (kind, str(root))
    now = time.monotonic()
    hit = _BRIDGED_MEMO.get(key)
    if hit is not None and now - hit[0] < _BRIDGED_RECHECK_S:
        return hit[2]
    deps = {"node_modules", "vendor", ".venv", "venv"}
    layout = wsl_bulk.layout_digest(
        root, names=_layout_names(), watched_dirs=deps,
        prune=(_SKIP_DIRS | {".git"}) - deps, max_depth=_LAYOUT_DEPTH,
    )
    if hit is not None and (
        (layout is not None and layout == hit[1])
        or (layout is None and now - hit[0] < BRIDGED_TTL_S)
    ):
        _BRIDGED_MEMO[key] = (now, hit[1], hit[2])
        return hit[2]
    value = compute()
    _BRIDGED_MEMO[key] = (now, layout, value)
    return value


def _signature_uncached(root: Path) -> tuple[Any, ...]:
    parts: list[Any] = [str(root), sys.platform, os.environ.get("PATH", "")]
    store = root / ".navin" / "commands.json"
    try:
        parts.append(("store", store.stat().st_mtime_ns))
    except OSError:
        pass
    for folder in _infra_dirs(root):
        try:
            parts.append((str(folder), folder.stat().st_mtime_ns))
        except OSError:
            continue
    for ci in (root / ".github" / "workflows", *(root / name for name in _CI_FILES)):
        try:
            parts.append((str(ci), ci.stat().st_mtime_ns))
        except OSError:
            continue
    for folder in _project_dirs(root):
        for name in MANIFESTS + ("node_modules", "vendor", ".venv", "venv"):
            path = folder / name
            try:
                parts.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                continue
    return tuple(parts)


@lru_cache(maxsize=32)
def _cached_runbook(signature: tuple[Any, ...]) -> tuple[str, ...]:
    from navin.agent.project_runtime import ACTIONS, resolve

    root = Path(signature[0])
    profile = resolve(root)
    lines: list[str] = []
    for unit in profile.units:
        head = f"[{unit.path}] {', '.join(unit.kinds)}"
        if unit.dev_port:
            head += f" - dev server port {unit.dev_port}"
        if unit.needs_install:
            head += " - dependencies NOT installed yet"
        lines.append(head)
        for action in ACTIONS:
            for index, candidate in enumerate(unit.candidates.get(action) or []):
                tag = candidate.source if index == 0 else f"{candidate.source}, alternative"
                line = f"  {action}: {candidate.command}   ({tag})"
                if candidate.note:
                    line += f"  <- {candidate.note}"
                if candidate.missing_tool:
                    hint = candidate.install_hint
                    line += f"  <- {candidate.missing_tool} is not installed" + (f": {hint}" if hint else "")
                lines.append(line)
    for where, rows in _infra_rows(root):
        lines.append(f"[{where}] infrastructure / deploy")
        lines += [f"  {label}: {command}" for label, command in rows]
    if profile.ci:
        lines.append("CI runs these (proven to work here):")
        lines += [f"  {command}" for command in profile.ci]
    if profile.readme:
        lines.append("README shows (low confidence, may be outdated):")
        lines += [f"  {command}" for command in profile.readme]
    header = [
        profile.host,
        "Project commands, read from this project's own files (validated = ran fine "
        "here before; declared = a script of the project; framework = the framework's "
        "convention). Use the first one of each action:",
    ]
    if not lines:
        header = [profile.host, "No project manifest found at the workspace root or in its usual sub-folders."]
    footer = [
        "Run them with the project tool: project action=install|build|start|stop|"
        "restart|status|test|lint|typecheck unit=<folder in brackets>. It runs the "
        "command above in the right folder, installs first when dependencies are "
        "missing, starts web apps on a free port, tracks and stops only what it "
        "started, explains failures and remembers what worked. Use exec only for a "
        "command that is not listed here.",
        "Rules: run servers and watchers in the background, never in the foreground. "
        "When a command fails, read the error and fix its cause before running it "
        "again; never retry it unchanged. Not listed here: look once in README / the "
        "CI files, then the tool's own --help, then web_search the official docs. "
        "Never guess a command and never explore the tree file by file to find one.",
    ]
    return tuple(header + lines + footer)


def project_runbook(root: Path | str) -> list[str]:
    """Runbook lines for ``root``, or [] when no manifest is found. Never raises."""
    try:
        path = Path(root).expanduser().resolve()
        if not path.is_dir():
            return []
        lines = list(_cached_runbook(_signature(path)))
        try:
            from navin.agent.project_remote import cloud_lines

            extra = cloud_lines(path)
        except Exception:
            extra = []
        if extra and len(lines) >= 2:
            lines[-2:-2] = extra  # before the footer rules
        return lines
    except Exception:
        return []


async def project_runbook_context_provider(
    request: RequestContext,
) -> RuntimeContextBlock | None:
    """The project's commands, every turn, so the first command is the right one."""
    workspace = request.workspace
    if workspace is None:
        return None
    lines = await runbook_or_last_good(workspace)
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="project_commands", content=content)


# The signature stats every manifest of every project folder. On a WSL project
# opened from Windows each stat crosses the 9P bridge, so the read went past
# the 2.5 s provider bound (10 times in one desktop day) and the turn started
# without the project's commands: the agent then guessed them. The read now
# keeps running in the background and the last good runbook of the workspace
# serves this turn; commands rarely change between two turns.
FRESH_WAIT_S = 2.0
_LAST_GOOD: dict[str, list[str]] = {}
_PENDING: dict[str, asyncio.Future[list[str]]] = {}


def _compute_and_keep(key: str) -> list[str]:
    lines = project_runbook(key)
    if lines:
        _LAST_GOOD[key] = list(lines)
    else:
        _LAST_GOOD.pop(key, None)
    return lines


async def runbook_or_last_good(workspace: Path | str) -> list[str]:
    """Fresh runbook lines, or the last good ones when the read is slow."""
    key = str(workspace)
    loop = asyncio.get_running_loop()
    task = _PENDING.get(key)
    if task is None or task.done() or task.get_loop() is not loop:
        task = asyncio.ensure_future(asyncio.to_thread(_compute_and_keep, key))
        _PENDING[key] = task
    try:
        # shield: the provider bound cancels this wait, never the read itself.
        return list(await asyncio.wait_for(asyncio.shield(task), timeout=FRESH_WAIT_S))
    except asyncio.TimeoutError:
        return list(_LAST_GOOD.get(key, []))
