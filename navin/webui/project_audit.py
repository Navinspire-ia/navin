# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project 360 audit for the Dev workbench.

Builds, per project folder, a complete "vision 360" report computed live from
the real files on disk: overview KPIs, API endpoint catalog, architecture
diagram (Mermaid), OpenAPI specs, permissions / RBAC mapping, SQL query
catalog, infrastructure services, core layer / request pipeline docs, plus a
static security and performance risk analysis and closing notes.

Everything is heuristic and bounded (file count / file size caps) so the scan
stays fast even on large projects, and no file content ever leaves the machine:
the report is assembled locally and served to the local web UI only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs
from navin.webui.audit_ast import scan_python_file_ast
from navin.webui.audit_sql import extract_sql_audit

_GIT_TIMEOUT_S = 6
_MAX_CONTENT_FILES = 3000
_MAX_FILE_BYTES = 900_000
_MAX_TOTAL_FILES = 30_000
_MAX_SQL_QUERIES = 200
_MAX_FINDINGS_PER_KIND = 250
_MAX_ENDPOINTS = 600

# Python checks owned by the AST pass (regex must not double-report them).
_AST_SECURITY_IDS = frozenset(
    {
        "eval-exec",
        "pickle",
        "yaml-load",
        "shell-true",
        "os-system",
        "weak-hash",
        "tls-verify-off",
    }
)
_AST_PERF_IDS = frozenset(
    {
        "sleep-in-async",
        "requests-in-async",
        "sync-subprocess-async",
    }
)

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "dist", "build", "out", ".next", ".nuxt", ".output", "coverage",
    ".idea", ".vscode", "target", "vendor", ".tox", ".cache",
    "site-packages", ".terraform", ".gradle", "bower_components",
    ".turbo", ".parcel-cache", "htmlcov", ".eggs",
}

_CODE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".swift", ".scala", ".sh", ".bash", ".sql", ".vue", ".svelte",
}

_TEXT_EXTS = _CODE_EXTS | {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".md", ".mdx", ".rst", ".txt", ".html", ".htm", ".css", ".scss",
    ".less", ".xml", ".graphql", ".proto", ".tf", ".dockerfile", ".lock",
}

_LANGUAGE_BY_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript (React)",
    ".js": "JavaScript", ".jsx": "JavaScript (React)", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".c": "C", ".cc": "C++", ".cpp": "C++", ".h": "C/C++ header",
    ".hpp": "C++ header", ".swift": "Swift", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell", ".sql": "SQL", ".vue": "Vue",
    ".svelte": "Svelte", ".html": "HTML", ".htm": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".less": "LESS", ".md": "Markdown", ".mdx": "Markdown",
    ".rst": "reStructuredText", ".json": "JSON", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".tf": "Terraform",
    ".graphql": "GraphQL", ".proto": "Protobuf",
}


class ProjectAuditError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _check_dir(raw_path: str) -> Path:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        raise ProjectAuditError("missing path")
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        raise ProjectAuditError("path must be absolute")
    if not path.is_dir():
        raise ProjectAuditError("not a directory", status=404)
    return path


# ---------------------------------------------------------------------------
# File collection


class _ScannedFile:
    __slots__ = ("rel", "path", "ext", "size", "lines", "text")

    def __init__(self, rel: str, path: Path, ext: str, size: int) -> None:
        self.rel = rel
        self.path = path
        self.ext = ext
        self.size = size
        self.lines = 0
        self.text: str | None = None


def _collect_files(root: Path) -> list[_ScannedFile]:
    files: list[_ScannedFile] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIRS and (not d.startswith(".") or d == ".github")
        )
        for name in sorted(filenames):
            if ".min." in name.lower():
                continue
            full = Path(dirpath) / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            rel = full.relative_to(root).as_posix()
            ext = full.suffix.lower()
            if name.lower() in ("dockerfile", "makefile", "procfile"):
                ext = "." + name.lower()
            files.append(_ScannedFile(rel, full, ext, size))
            if len(files) >= _MAX_TOTAL_FILES:
                return files
    return files


def _load_text(file: _ScannedFile) -> str:
    if file.text is not None:
        return file.text
    if file.size > _MAX_FILE_BYTES:
        file.text = ""
        return ""
    try:
        text = file.path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    file.text = text
    file.lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return text


def _is_test_path(rel: str) -> bool:
    lowered = rel.lower()
    return (
        "/tests/" in f"/{lowered}"
        or "/test/" in f"/{lowered}"
        or "__tests__" in lowered
        or lowered.startswith("tests/")
        or Path(lowered).name.startswith("test_")
        or lowered.endswith((".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js", "_test.go", "_test.py"))
    )


def _is_audit_fixture_path(rel: str) -> bool:
    """Educational / self-referential paths that must not inflate findings."""
    lowered = rel.replace("\\", "/").lower()
    name = Path(lowered).name
    if name in {"poc.py", "project_audit.py"}:
        return True
    if "/security/poc." in f"/{lowered}":
        return True
    if "/gen/schemas/" in f"/{lowered}":
        return True
    return False


def _is_pattern_definition_line(line: str) -> bool:
    """True for regex/check definitions (the scanner must not flag itself)."""
    stripped = line.strip()
    return (
        '"pattern"' in stripped
        or "'pattern'" in stripped
        or "re.compile(" in stripped
        or stripped.startswith("_POC")
        or "_PERF_CHECKS" in stripped
        or "_SECURITY_CHECKS" in stripped
    )


# ---------------------------------------------------------------------------
# Git


def _run_git(root: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        return ""
    try:
        out = subprocess.run(  # noqa: S603
            [git, "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _git_overview(root: Path) -> dict[str, Any]:
    inside = _run_git(root, "rev-parse", "--is-inside-work-tree").strip()
    if inside != "true":
        return {"is_repo": False}
    branch = _run_git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    commit_count_raw = _run_git(root, "rev-list", "--count", "HEAD").strip()
    dirty = bool(_run_git(root, "status", "--porcelain").strip())
    last_commits: list[dict[str, str]] = []
    log = _run_git(root, "log", "-8", "--pretty=format:%h%x1f%an%x1f%ad%x1f%s", "--date=short")
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            last_commits.append(
                {"hash": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]}
            )
    contributors: list[dict[str, Any]] = []
    shortlog = _run_git(root, "shortlog", "-sn", "--no-merges", "HEAD")
    for line in shortlog.splitlines()[:10]:
        chunk = line.strip().split("\t", 1)
        if len(chunk) == 2 and chunk[0].strip().isdigit():
            contributors.append({"name": chunk[1].strip(), "commits": int(chunk[0])})
    return {
        "is_repo": True,
        "branch": branch,
        "dirty": dirty,
        "commit_count": int(commit_count_raw) if commit_count_raw.isdigit() else None,
        "last_commits": last_commits,
        "contributors": contributors,
    }


# ---------------------------------------------------------------------------
# Overview / stack / dependencies


def _detect_dependencies(root: Path, files: list[_ScannedFile]) -> dict[str, Any]:
    result: dict[str, Any] = {"managers": []}
    by_rel = {f.rel: f for f in files}

    def read_json(rel: str) -> dict[str, Any] | None:
        file = by_rel.get(rel)
        if file is None:
            return None
        try:
            return json.loads(_load_text(file))
        except (json.JSONDecodeError, ValueError):
            return None

    pkg = read_json("package.json")
    if pkg:
        deps = pkg.get("dependencies") or {}
        dev = pkg.get("devDependencies") or {}
        result["managers"].append(
            {
                "name": "npm (package.json)",
                "file": "package.json",
                "runtime": len(deps),
                "dev": len(dev),
                "packages": sorted(deps)[:60],
            }
        )
    for rel in sorted(by_rel):
        if rel.endswith("package.json") and rel != "package.json" and rel.count("/") <= 2:
            sub = read_json(rel)
            if sub:
                deps = sub.get("dependencies") or {}
                dev = sub.get("devDependencies") or {}
                result["managers"].append(
                    {
                        "name": f"npm ({rel})",
                        "file": rel,
                        "runtime": len(deps),
                        "dev": len(dev),
                        "packages": sorted(deps)[:60],
                    }
                )
    pyproject = by_rel.get("pyproject.toml")
    if pyproject is not None:
        text = _load_text(pyproject)
        deps = re.findall(r'^\s*"([A-Za-z0-9_.\-\[\]]+)\s*[><=~!]', text, re.MULTILINE)
        result["managers"].append(
            {
                "name": "Python (pyproject.toml)",
                "file": "pyproject.toml",
                "runtime": len(set(deps)),
                "dev": 0,
                "packages": sorted(set(deps))[:60],
            }
        )
    requirements = by_rel.get("requirements.txt")
    if requirements is not None:
        lines = [
            line.strip() for line in _load_text(requirements).splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        result["managers"].append(
            {
                "name": "Python (requirements.txt)",
                "file": "requirements.txt",
                "runtime": len(lines),
                "dev": 0,
                "packages": [re.split(r"[><=~!\[; ]", li)[0] for li in lines][:60],
            }
        )
    for rel, label in (("go.mod", "Go modules"), ("Cargo.toml", "Cargo (Rust)"), ("composer.json", "Composer (PHP)"), ("Gemfile", "Bundler (Ruby)")):
        if rel in by_rel:
            result["managers"].append(
                {"name": label, "file": rel, "runtime": 0, "dev": 0, "packages": []}
            )
    result["lockfiles"] = [
        rel for rel in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "uv.lock", "poetry.lock", "Cargo.lock", "go.sum")
        if rel in by_rel
    ]
    return result


_STACK_MARKERS: list[tuple[str, str]] = [
    ("react", "React"), ("vue", "Vue"), ("@angular/core", "Angular"),
    ("next", "Next.js"), ("svelte", "Svelte"), ("vite", "Vite"),
    ("tailwindcss", "Tailwind CSS"), ("express", "Express"),
    ("@nestjs/core", "NestJS"), ("fastify", "Fastify"),
    ("electron", "Electron"), ("typescript", "TypeScript"),
]

_PY_STACK_MARKERS: list[tuple[str, str]] = [
    ("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"),
    ("websockets", "websockets"), ("sqlalchemy", "SQLAlchemy"),
    ("pydantic", "Pydantic"), ("celery", "Celery"), ("aiohttp", "aiohttp"),
    ("uvicorn", "Uvicorn"), ("starlette", "Starlette"),
    ("pytest", "pytest"), ("loguru", "Loguru"),
]


def _detect_stack(dependencies: dict[str, Any], files: list[_ScannedFile]) -> list[str]:
    stack: list[str] = []
    packages: set[str] = set()
    for manager in dependencies.get("managers", []):
        packages.update(p.lower() for p in manager.get("packages", []))
    for marker, label in _STACK_MARKERS + _PY_STACK_MARKERS:
        if marker in packages and label not in stack:
            stack.append(label)
    rels = {f.rel for f in files}
    if "Dockerfile" in rels or "dockerfile" in {f.rel.lower() for f in files}:
        stack.append("Docker")
    if any(r.startswith(".github/workflows/") for r in rels):
        stack.append("GitHub Actions")
    if "docker-compose.yml" in rels or "docker-compose.yaml" in rels or "compose.yml" in rels:
        stack.append("Docker Compose")
    if "go.mod" in rels:
        stack.append("Go")
    if "Cargo.toml" in rels:
        stack.append("Rust")
    return stack


# ---------------------------------------------------------------------------
# API endpoint catalog

_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "options", "head")

_ENDPOINT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # FastAPI / Flask method decorators: @app.get("/x"), @router.post("/y")
    (
        "decorator",
        re.compile(
            r"@\s*([A-Za-z_][\w.]*)\.(get|post|put|delete|patch|options|head)\(\s*[\"']([^\"']+)[\"']"
        ),
    ),
    # Flask @x.route("/y", methods=["POST"])
    (
        "flask-route",
        re.compile(r"@\s*[\w.]+\.route\(\s*[\"']([^\"']+)[\"'](?:[^)]*methods\s*=\s*\[([^\]]*)\])?"),
    ),
    # Express / Fastify / Koa-router: app.get('/x', ...), router.post("/y", ...
    (
        "js-call",
        re.compile(
            r"\b(app|router|server|api|fastify)\.(get|post|put|delete|patch|options|head|all)\(\s*[\"'`]([^\"'`]+)[\"'`]"
        ),
    ),
    # NestJS decorators: @Get('x'), @Post()
    ("nest", re.compile(r"@(Get|Post|Put|Delete|Patch|Options|Head)\(\s*(?:[\"']([^\"']*)[\"'])?\s*\)")),
    # Django: path("x/", view)
    ("django", re.compile(r"\bpath\(\s*[\"']([^\"']+)[\"']\s*,")),
    # Regex-dispatched handlers (websockets-style gateways):
    #   re.match(r"^/api/x$", got)  /  got == "/api/x"  /  path == "/api/x"
    (
        "regex-dispatch",
        re.compile(
            r"(?:re\.match\(\s*r?[\"']\^?|"
            r"(?:got|path|url|request\.path)\s*==\s*[\"'])"
            r"(/api/[^\"'$)]+)"
        ),
    ),
]

_AUTH_HINTS = re.compile(
    r"check_api_token|_check_api_token|_authorized\(|Depends\(|login_required|"
    r"permission_required|requires?_auth|authenticate|authorize|isAuthenticated|"
    r"requireAuth|passport\.|verify_token|jwt_required|get_current_user|ensure_auth|"
    r"has_perm|IsAuthenticated|auth_guard|UseGuards|Authorization|check_token",
    re.IGNORECASE,
)

_PLACEHOLDER_ROUTE = re.compile(
    r"^(?:/?(?:x|y|z|foo|bar|baz|api/x|api/y)(?:/)?|/)$",
    re.IGNORECASE,
)
_NOISE_ROUTE_CHARS = re.compile(r"[…·]|[.]{2,}|\$\{|<[^>]+>")


def _framework_for(kind: str, ext: str) -> str:
    if kind == "nest":
        return "NestJS"
    if kind == "js-call":
        return "Express / Node"
    if kind == "django":
        return "Django"
    if kind == "flask-route":
        return "Flask"
    if kind == "regex-dispatch":
        return "HTTP gateway (regex dispatch)"
    return "FastAPI / Flask" if ext == ".py" else "HTTP"


def _route_is_noise(route: str) -> bool:
    if not route or len(route) > 200:
        return True
    if _PLACEHOLDER_ROUTE.match(route.strip()):
        return True
    if _NOISE_ROUTE_CHARS.search(route):
        return True
    if " " in route or "\t" in route:
        return True
    return False


def _extract_endpoints(files: list[_ScannedFile]) -> dict[str, Any]:
    modules: dict[str, dict[str, Any]] = {}
    total = 0
    frameworks: set[str] = set()
    for file in files:
        if file.ext not in {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
            continue
        if _is_test_path(file.rel) or _is_audit_fixture_path(file.rel):
            continue
        text = _load_text(file)
        if not text or ("/" not in text and "route" not in text):
            continue
        lines = text.splitlines()
        found: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
                continue
            if _is_pattern_definition_line(line):
                continue
            for kind, pattern in _ENDPOINT_PATTERNS:
                for match in pattern.finditer(line):
                    if kind == "decorator":
                        method, route = match.group(2).upper(), match.group(3)
                    elif kind == "flask-route":
                        route = match.group(1)
                        methods_raw = match.group(2) or ""
                        method = (
                            re.sub(r"[\"' ]", "", methods_raw).split(",")[0].upper()
                            if methods_raw.strip()
                            else "GET"
                        )
                    elif kind == "js-call":
                        method, route = match.group(2).upper(), match.group(3)
                        if method == "ALL":
                            method = "ANY"
                    elif kind == "nest":
                        method, route = match.group(1).upper(), match.group(2) or "/"
                    elif kind == "django":
                        method, route = "ANY", "/" + match.group(1).lstrip("/")
                    elif kind == "regex-dispatch":
                        method, route = "ANY", match.group(1)
                    else:
                        method, route = "GET", match.group(1)
                    if _route_is_noise(route):
                        continue
                    # Route literals must look like URL paths, not prose.
                    if kind in ("js-call", "decorator", "regex-dispatch") and not route.startswith("/"):
                        continue
                    key = (method, route)
                    if key in seen:
                        continue
                    seen.add(key)
                    # Wider window: auth gates often sit a few statements below the match.
                    window = "\n".join(lines[max(0, idx - 2) : idx + 40])
                    handler = ""
                    handler_match = re.search(
                        r"(?:async\s+)?(?:def|function)\s+([A-Za-z_]\w*)", window
                    )
                    if handler_match:
                        handler = handler_match.group(1)
                    found.append(
                        {
                            "method": method,
                            "path": route,
                            "line": idx + 1,
                            "handler": handler,
                            "auth": bool(_AUTH_HINTS.search(window)),
                            "auth_scope": "local" if _AUTH_HINTS.search(window) else "",
                        }
                    )
                    frameworks.add(_framework_for(kind, file.ext))
        if found:
            # Module-level gate (settings router style): many auth helpers vs endpoints.
            auth_calls = len(
                re.findall(
                    r"\b(?:_authorized|_check_api_token|check_api_token)\s*\(",
                    text,
                )
            )
            if auth_calls >= max(3, len(found) // 4):
                for endpoint in found:
                    if not endpoint["auth"]:
                        endpoint["auth"] = True
                        endpoint["auth_scope"] = "module"
            found.sort(key=lambda e: (e["path"], e["method"]))
            modules[file.rel] = {
                "module": file.rel,
                "framework": _framework_for("decorator" if file.ext == ".py" else "js-call", file.ext),
                "endpoints": found[:_MAX_ENDPOINTS],
                "auth_coverage": round(
                    100 * sum(1 for e in found if e["auth"]) / max(len(found), 1)
                ),
            }
            total += len(found)
        if total >= _MAX_ENDPOINTS:
            break
    ordered = sorted(modules.values(), key=lambda m: -len(m["endpoints"]))
    return {"total": total, "modules": ordered, "frameworks": sorted(frameworks)}


# ---------------------------------------------------------------------------
# OpenAPI specs


def _find_openapi_specs(files: list[_ScannedFile]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for file in files:
        name = Path(file.rel).name.lower()
        if name not in (
            "openapi.json", "openapi.yaml", "openapi.yml",
            "swagger.json", "swagger.yaml", "swagger.yml",
        ):
            continue
        entry: dict[str, Any] = {"path": file.rel, "title": "", "version": "", "endpoints": []}
        text = _load_text(file)
        data: Any = None
        if name.endswith(".json"):
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                data = None
        else:
            try:
                import yaml

                data = yaml.safe_load(text)
            except Exception:
                data = None
        if isinstance(data, dict):
            info = data.get("info") or {}
            entry["title"] = str(info.get("title", ""))
            entry["version"] = str(info.get("version", ""))
            paths = data.get("paths") or {}
            for route, ops in list(paths.items())[:200]:
                if not isinstance(ops, dict):
                    continue
                for method, op in ops.items():
                    if method.lower() not in _HTTP_METHODS:
                        continue
                    summary = ""
                    if isinstance(op, dict):
                        summary = str(op.get("summary") or op.get("description") or "")[:160]
                    entry["endpoints"].append(
                        {"method": method.upper(), "path": route, "summary": summary}
                    )
        specs.append(entry)
    return specs


# ---------------------------------------------------------------------------
# Architecture (Mermaid)

_FRONTEND_DIR_HINTS = ("webui", "frontend", "client", "ui", "web", "app", "www", "site")
_BACKEND_DIR_HINTS = ("api", "server", "backend", "src", "core", "services", "lib")
_INFRA_DIR_HINTS = ("docker", "deploy", "infra", "k8s", "kubernetes", "terraform", "ansible", "packaging", "scripts", "ci", "os")
_DOCS_DIR_HINTS = ("docs", "doc", "documentation", "wiki")
_TEST_DIR_HINTS = ("tests", "test", "__tests__", "e2e", "spec")


def _mermaid_escape(label: str) -> str:
    return re.sub(r"[\[\]{}()<>\"'`|]", " ", label).strip() or "x"


def _build_architecture(
    root: Path,
    files: list[_ScannedFile],
    stack: list[str],
    compose_services: list[dict[str, Any]],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    sql_files = 0
    for file in files:
        top = file.rel.split("/", 1)[0]
        if "/" in file.rel:
            counts[top] = counts.get(top, 0) + 1
        if file.ext == ".sql" or "/supabase/" in file.rel.replace("\\", "/").lower():
            sql_files += 1
    top_dirs = sorted(counts.items(), key=lambda kv: -kv[1])[:14]

    layers: dict[str, list[tuple[str, int]]] = {
        "frontend": [], "backend": [], "infra": [], "docs": [], "tests": [], "data": [], "other": [],
    }
    for name, count in top_dirs:
        lowered = name.lower()
        if lowered in _TEST_DIR_HINTS:
            layers["tests"].append((name, count))
        elif lowered in _DOCS_DIR_HINTS:
            layers["docs"].append((name, count))
        elif lowered in _FRONTEND_DIR_HINTS:
            layers["frontend"].append((name, count))
        elif lowered in _INFRA_DIR_HINTS:
            layers["infra"].append((name, count))
        elif lowered in {"supabase", "migrations", "prisma", "db", "database", "sql"}:
            layers["data"].append((name, count))
        elif lowered in _BACKEND_DIR_HINTS:
            layers["backend"].append((name, count))
        else:
            layers["other"].append((name, count))
    if not layers["backend"] and layers["other"]:
        layers["backend"] = layers["other"][:3]
        layers["other"] = layers["other"][3:]
    if sql_files and not layers["data"]:
        layers["data"].append(("sql", sql_files))

    lines = ["flowchart LR", '  U([Client / Navigateur])']
    node_id = 0

    def add_subgraph(key: str, title: str, entries: list[tuple[str, int]]) -> str | None:
        nonlocal node_id
        if not entries:
            return None
        graph_id = f"SG_{key}"
        lines.append(f'  subgraph {graph_id}["{_mermaid_escape(title)}"]')
        first = None
        for name, count in entries[:6]:
            node_id += 1
            nid = f"N{node_id}"
            if first is None:
                first = nid
            lines.append(f'    {nid}["{_mermaid_escape(name)}/ ({count} fichiers)"]')
        lines.append("  end")
        return graph_id

    fe = add_subgraph("fe", "Frontend", layers["frontend"])
    be = add_subgraph("be", "Backend / Coeur", layers["backend"] + layers["other"][:2])
    data = add_subgraph("data", "Données / SQL", layers["data"])
    infra = add_subgraph("infra", "Infrastructure / Ops", layers["infra"])
    tests = add_subgraph("tests", "Tests", layers["tests"])
    docs = add_subgraph("docs", "Documentation", layers["docs"])

    if fe:
        lines.append(f"  U --> {fe}")
        if be:
            lines.append(f"  {fe} -->|HTTP / WebSocket| {be}")
    elif be:
        lines.append(f"  U --> {be}")
    if be and data:
        lines.append(f"  {be} -->|SQL / ORM| {data}")
    for idx, service in enumerate(compose_services[:6]):
        node = f"SV{idx}"
        image = service.get("image") or "service"
        lines.append(f'  {node}[("{_mermaid_escape(str(service.get("name", "service")))}\\n{_mermaid_escape(str(image))}")]')
        if be:
            lines.append(f"  {be} --> {node}")
    if infra and be:
        lines.append(f"  {infra} -.->|build / deploy| {be}")
    if tests and be:
        lines.append(f"  {tests} -.->|valide| {be}")
    if docs and be:
        lines.append(f"  {docs} -.-> {be}")

    described = [
        {
            "name": title,
            "dirs": [name for name, _ in entries],
            "files": sum(count for _, count in entries),
        }
        for title, entries in (
            ("Frontend", layers["frontend"]),
            ("Backend / Coeur", layers["backend"]),
            ("Données / SQL", layers["data"]),
            ("Infrastructure", layers["infra"]),
            ("Tests", layers["tests"]),
            ("Documentation", layers["docs"]),
            ("Autres", layers["other"]),
        )
        if entries
    ]
    return {
        "mermaid": "\n".join(lines),
        "layers": described,
        "stack": stack,
        "sql_files": sql_files,
    }


# ---------------------------------------------------------------------------
# Permissions / RBAC

_PERMISSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Décorateur d'autorisation", re.compile(r"@(login_required|permission_required|requires_[a-z_]+|jwt_required|roles_required|admin_required)\b")),
    ("Dépendance d'auth (FastAPI)", re.compile(r"Depends\(\s*(get_current_user|require_[a-z_]+|verify_[a-z_]+|oauth2[\w_]*)")),
    ("Garde NestJS", re.compile(r"@UseGuards\(([^)]*)\)")),
    ("Middleware d'auth", re.compile(r"\b(requireAuth|ensureAuthenticated|isAuthenticated|authMiddleware|authenticateToken|verifyToken)\b")),
    ("Vérification de jeton", re.compile(r"\b(check_api_token|_check_api_token|_authorized|verify_token|validate_token|check_token)\b")),
    ("Contrôle de rôle", re.compile(r"\b(hasRole|has_role|is_admin|isAdmin|check_role|role_required|@PreAuthorize)\b")),
    ("Session / cookie", re.compile(r"\b(SESSION_COOKIE_SECURE|set_cookie\(|session\[)")),
]

_ROLE_LITERALS = re.compile(
    r"[\"'](admin|superadmin|administrator|owner|editor|viewer|member|manager|moderator|operator|guest|user|read[_-]?only|maintainer)[\"']",
    re.IGNORECASE,
)


def _extract_permissions(files: list[_ScannedFile], api: dict[str, Any]) -> dict[str, Any]:
    mechanisms: list[dict[str, Any]] = []
    roles: dict[str, int] = {}
    for file in files:
        if file.ext not in {".py", ".ts", ".tsx", ".js", ".jsx"} or _is_test_path(file.rel):
            continue
        text = _load_text(file)
        if not text:
            continue
        for idx, line in enumerate(text.splitlines()):
            for label, pattern in _PERMISSION_PATTERNS:
                match = pattern.search(line)
                if match and len(mechanisms) < _MAX_FINDINGS_PER_KIND:
                    mechanisms.append(
                        {
                            "kind": label,
                            "name": match.group(1) if match.groups() else match.group(0),
                            "file": file.rel,
                            "line": idx + 1,
                            "snippet": line.strip()[:180],
                        }
                    )
            if "role" in line.lower() or "perm" in line.lower():
                for role_match in _ROLE_LITERALS.finditer(line):
                    role = role_match.group(1).lower()
                    roles[role] = roles.get(role, 0) + 1

    protected = 0
    unprotected: list[dict[str, Any]] = []
    for module in api.get("modules", []):
        for endpoint in module["endpoints"]:
            if endpoint.get("auth"):
                protected += 1
            else:
                unprotected.append(
                    {
                        "method": endpoint["method"],
                        "path": endpoint["path"],
                        "module": module["module"],
                        "line": endpoint["line"],
                    }
                )
    return {
        "mechanisms": mechanisms,
        "roles": [
            {"name": name, "occurrences": count}
            for name, count in sorted(roles.items(), key=lambda kv: -kv[1])[:20]
        ],
        "protected_endpoints": protected,
        "unprotected_endpoints": unprotected[:80],
        "unprotected_count": len(unprotected),
        "coverage_percent": round(100 * protected / max(protected + len(unprotected), 1)),
        "module_auth_endpoints": sum(
            1
            for module in api.get("modules", [])
            for endpoint in module["endpoints"]
            if endpoint.get("auth_scope") == "module"
        ),
    }


# ---------------------------------------------------------------------------
# SQL catalog + findings


def _extract_sql(files: list[_ScannedFile]) -> dict[str, Any]:
    """Full-project SQL inventory with security and performance findings."""
    return extract_sql_audit(files, load_text=_load_text)


# ---------------------------------------------------------------------------
# Infrastructure


def _parse_compose(files: list[_ScannedFile]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for file in files:
        name = Path(file.rel).name.lower()
        if name not in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            continue
        try:
            import yaml

            data = yaml.safe_load(_load_text(file))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        services = []
        for service_name, config in (data.get("services") or {}).items():
            if not isinstance(config, dict):
                continue
            services.append(
                {
                    "name": str(service_name),
                    "image": str(config.get("image", "")) or ("build: " + str(config.get("build", ""))),
                    "ports": [str(p) for p in (config.get("ports") or [])][:8],
                    "depends_on": list(config.get("depends_on") or [])
                    if isinstance(config.get("depends_on"), list)
                    else list((config.get("depends_on") or {}).keys()),
                    "restart": str(config.get("restart", "")),
                }
            )
        results.append({"file": file.rel, "services": services})
    return results


def _parse_dockerfiles(files: list[_ScannedFile]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for file in files:
        if Path(file.rel).name.lower() != "dockerfile" and not file.rel.lower().endswith(".dockerfile"):
            continue
        text = _load_text(file)
        bases = re.findall(r"^FROM\s+([^\s]+)(?:\s+AS\s+(\w+))?", text, re.MULTILINE | re.IGNORECASE)
        ports = re.findall(r"^EXPOSE\s+(.+)$", text, re.MULTILINE | re.IGNORECASE)
        results.append(
            {
                "path": file.rel,
                "base_images": [b[0] for b in bases][:6],
                "stages": [b[1] for b in bases if b[1]],
                "exposed_ports": " ".join(ports).split()[:8],
            }
        )
    return results


def _parse_ci(files: list[_ScannedFile]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for file in files:
        if not (file.rel.startswith(".github/workflows/") and file.ext in (".yml", ".yaml")):
            if Path(file.rel).name not in (".gitlab-ci.yml", "Jenkinsfile", ".travis.yml"):
                continue
        text = _load_text(file)
        name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        triggers = re.findall(r"^\s{2}(push|pull_request|schedule|workflow_dispatch|release):", text, re.MULTILINE)
        jobs = re.findall(r"^\s{2}([\w-]+):\s*$", text.split("jobs:", 1)[-1], re.MULTILINE) if "jobs:" in text else []
        results.append(
            {
                "path": file.rel,
                "name": name_match.group(1).strip() if name_match else Path(file.rel).name,
                "triggers": sorted(set(triggers)),
                "jobs": jobs[:10],
            }
        )
    return results


def _parse_env_files(root: Path, files: list[_ScannedFile]) -> list[dict[str, Any]]:
    gitignore = ""
    for file in files:
        if file.rel == ".gitignore":
            gitignore = _load_text(file)
            break
    results: list[dict[str, Any]] = []
    for file in files:
        name = Path(file.rel).name
        if not (name == ".env" or name.startswith(".env.")):
            continue
        keys = []
        for line in _load_text(file).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                keys.append(stripped.split("=", 1)[0].strip())
        ignored = ".env" in gitignore
        results.append(
            {"path": file.rel, "keys": keys[:40], "gitignored": ignored, "is_example": "example" in name or "sample" in name}
        )
    return results


def _parse_makefile(files: list[_ScannedFile]) -> list[str]:
    for file in files:
        if Path(file.rel).name.lower() == "makefile" and "/" not in file.rel:
            targets = re.findall(
                r"^([A-Za-z0-9_.-]+):(?!=)", _load_text(file), re.MULTILINE
            )
            return [t for t in targets if not t.startswith(".")][:30]
    return []


# ---------------------------------------------------------------------------
# Core layer / request pipeline

_ENTRYPOINT_NAMES = {
    "main.py": "Point d'entrée Python",
    "app.py": "Application Python",
    "wsgi.py": "Entrée WSGI",
    "asgi.py": "Entrée ASGI",
    "manage.py": "CLI Django",
    "index.ts": "Entrée TypeScript",
    "index.js": "Entrée JavaScript",
    "main.ts": "Entrée TypeScript",
    "main.tsx": "Entrée React",
    "main.go": "Entrée Go",
    "server.js": "Serveur Node",
    "server.ts": "Serveur Node (TS)",
    "app.tsx": "Racine React",
    "App.tsx": "Racine React",
    "cli.py": "CLI Python",
    "__main__.py": "Module exécutable Python",
}

_MIDDLEWARE_PATTERN = re.compile(
    r"(app\.use\(|add_middleware\(|@middleware|process_request|process_response|MIDDLEWARE\s*=|middleware\s*[:=])",
)

_CORE_DIR_LABELS = {
    "core": "Noyau applicatif (logique centrale)",
    "middleware": "Middlewares (traitement des requêtes)",
    "services": "Couche services (logique métier)",
    "models": "Modèles de données",
    "schemas": "Schémas / validation",
    "controllers": "Contrôleurs (entrées HTTP)",
    "routes": "Définition des routes",
    "api": "Couche API",
    "utils": "Utilitaires partagés",
    "lib": "Bibliothèque interne",
    "hooks": "Hooks (React)",
    "components": "Composants UI",
    "providers": "Providers / contexte",
    "store": "État global (store)",
    "db": "Accès base de données",
    "database": "Accès base de données",
    "repositories": "Couche d'accès aux données",
    "auth": "Authentification",
    "config": "Configuration",
    "workers": "Traitements asynchrones (workers)",
    "tasks": "Tâches de fond",
    "agent": "Agent / orchestration",
    "tools": "Outillage interne",
}


def _extract_pipeline(files: list[_ScannedFile]) -> dict[str, Any]:
    entrypoints: list[dict[str, str]] = []
    for file in files:
        name = Path(file.rel).name
        if name in _ENTRYPOINT_NAMES and file.rel.count("/") <= 3:
            entrypoints.append({"path": file.rel, "kind": _ENTRYPOINT_NAMES[name]})
    entrypoints.sort(key=lambda e: e["path"].count("/"))

    middleware: list[dict[str, Any]] = []
    for file in files:
        if file.ext not in {".py", ".ts", ".tsx", ".js", ".jsx"} or _is_test_path(file.rel):
            continue
        text = _load_text(file)
        if not text:
            continue
        for idx, line in enumerate(text.splitlines()):
            if _MIDDLEWARE_PATTERN.search(line) and len(middleware) < 60:
                middleware.append(
                    {"file": file.rel, "line": idx + 1, "snippet": line.strip()[:160]}
                )

    dir_files: dict[str, int] = {}
    for file in files:
        parts = file.rel.split("/")
        for depth in range(min(len(parts) - 1, 3)):
            segment = parts[depth].lower()
            if segment in _CORE_DIR_LABELS:
                key = "/".join(parts[: depth + 1])
                dir_files[key] = dir_files.get(key, 0) + 1
    core_layers = [
        {
            "path": path,
            "name": Path(path).name,
            "description": _CORE_DIR_LABELS[Path(path).name.lower()],
            "files": count,
        }
        for path, count in sorted(dir_files.items(), key=lambda kv: -kv[1])[:16]
    ]
    return {
        "entrypoints": entrypoints[:12],
        "middleware": middleware,
        "core_layers": core_layers,
        "summary": {
            "entrypoints": len(entrypoints[:12]),
            "middleware": len(middleware),
            "core_layers": len(core_layers),
        },
    }


# ---------------------------------------------------------------------------
# Security findings

_PLACEHOLDER_SECRET = re.compile(
    r"(?i)(example|sample|placeholder|changeme|change-me|your[_-]|xxx|dummy|test|fake|"
    r"redacted|re\-?dacted|<|\$\{|process\.env|os\.environ|getenv|\*{3,}|•+|·+)"
)

_KEY_PLACEHOLDER = re.compile(
    r"(?i)placeholder|example|sample|redacted|re\-?dacted|\\\\n|\.\.\.|\*{3,}|•+|·+"
)

_WEAK_HASH_SECRET_CONTEXT = re.compile(
    r"(?i)\b(password|passwd|passphrase|credential|secret|api[_-]?key)\b"
)

_TLS_FALLBACK_HINT = re.compile(
    r"(?i)CERTIFICATE_VERIFY_FAILED|verify\s*=\s*True|SSL verification failed|retrying with verify"
)

_CORS_CREDENTIALS = re.compile(
    r"(?i)Access-Control-Allow-Credentials[\"']?\s*[:,]\s*[\"']?true|allow_credentials\s*=\s*True"
)

_HTTP_SCHEMA_HINT = re.compile(
    r"(?i)\$schema|json-schema\.org|schemas\.|purl\.org|www\.w3\.org|example\.com|example/"
)


def _inside_string_literal(line: str, position: int) -> bool:
    """True when ``position`` falls inside a quoted string on this line.

    Static checks target real calls, not the same words quoted in log
    messages or docstrings. Counting unescaped quotes before the match is a
    cheap, good-enough approximation.
    """
    double = single = 0
    index = 0
    while index < position:
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == '"' and single % 2 == 0:
            double += 1
        elif char == "'" and double % 2 == 0:
            single += 1
        index += 1
    return double % 2 == 1 or single % 2 == 1


def _in_block_comment(line: str, position: int) -> bool:
    """True when position is inside a same-line /* ... */ (or JSDoc) comment."""
    index = 0
    while True:
        start = line.find("/*", index)
        if start == -1 or start >= position:
            return False
        if _inside_string_literal(line, start):
            index = start + 2
            continue
        end = line.find("*/", start + 2)
        if end == -1:
            return start < position
        if start < position <= end + 1:
            return True
        index = end + 2


def _in_non_code_context(line: str, position: int, ext: str) -> bool:
    """Match sits in a string, a comment, or inline doc markup on this line."""
    if _inside_string_literal(line, position):
        return True
    if _in_block_comment(line, position):
        return True
    if position > 0 and line[position - 1] == "`":
        return True
    comment_marker = "#" if ext == ".py" else "//"
    comment_index = line.find(comment_marker)
    while comment_index != -1:
        if not _inside_string_literal(line, comment_index):
            return comment_index < position
        comment_index = line.find(comment_marker, comment_index + 1)
    return False


def _window_text(lines: list[str], index: int, *, before: int = 12, after: int = 4) -> str:
    start = max(0, index - before)
    end = min(len(lines), index + after + 1)
    return "\n".join(lines[start:end])


def _risk_score(summary: dict[str, int]) -> int:
    """100 minus weighted penalties with diminishing returns per severity.

    A linear sum lets dozens of medium findings crush the score to the floor
    and hide the difference between "noisy" and "on fire"; the sub-linear
    exponent keeps the score discriminating on large codebases.
    """
    penalty = 0.0
    for level, weight in (("critical", 18), ("high", 8), ("medium", 3), ("low", 1)):
        count = summary.get(level, 0)
        if count:
            penalty += weight * (count ** 0.7)
    return max(5, round(100 - penalty))


def _make_finding(
    *,
    finding_id: str,
    severity: str,
    category: str,
    message: str,
    recommendation: str,
    file: str,
    line: int,
    snippet: str,
    confidence: str = "medium",
    evidence: str = "",
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "category": category,
        "message": message,
        "recommendation": recommendation,
        "file": file,
        "line": line,
        "snippet": snippet[:200] if snippet else "",
        "confidence": confidence,
        "evidence": evidence,
    }


def _finalize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Dedupe, drop low-confidence noise from the score, sort, summarize."""
    # Prefer AST / higher confidence when the same sink is reported twice.
    rank = {"high": 0, "medium": 1, "low": 2}
    best: dict[tuple[str, str, int], dict[str, Any]] = {}
    for finding in findings:
        finding.setdefault("confidence", "medium")
        finding.setdefault("evidence", "")
        key = (finding["id"], finding["file"], int(finding.get("line") or 0))
        prev = best.get(key)
        if prev is None or rank.get(finding["confidence"], 9) < rank.get(
            prev.get("confidence", "medium"), 9
        ):
            best[key] = finding
    merged = list(best.values())

    # Low-confidence items stay visible but do not crush the trust score.
    scored = [f for f in merged if f.get("confidence") != "low"]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    merged.sort(
        key=lambda f: (
            order.get(f["severity"], 9),
            rank.get(f.get("confidence", "medium"), 9),
            f["file"],
            f["line"],
        )
    )
    summary = {level: 0 for level in ("critical", "high", "medium", "low")}
    for finding in scored:
        summary[finding["severity"]] = summary.get(finding["severity"], 0) + 1
    return {
        "score": _risk_score(summary),
        "summary": summary,
        "findings": merged,
        "scored_findings": len(scored),
        "engine": "ast+heuristics",
    }


_CONFIDENCE_BY_CHECK = {
    "private-key": "high",
    "aws-key": "high",
    "hardcoded-secret": "medium",
    "sql-injection": "medium",
    "jwt-none": "high",
    "cors-wildcard": "high",
    "inner-html": "medium",
    "http-url": "medium",
    "chmod-777": "medium",
    "env-not-ignored": "high",
    "debug-true": "medium",
    "n-plus-one": "medium",
    "sync-call-from-async": "medium",
    "select-star": "high",
    "fetchall": "medium",
    "json-deep-clone": "high",
    "huge-file": "low",
}


_SECURITY_CHECKS: list[dict[str, Any]] = [
    {
        "id": "private-key",
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "severity": "critical",
        "category": "Secrets",
        "message": "Clé privée présente dans le dépôt",
        "recommendation": "Retirer la clé du dépôt, la faire tourner (rotation) et la stocker dans un gestionnaire de secrets.",
        "exclude": _KEY_PLACEHOLDER,
    },
    {
        "id": "aws-key",
        "pattern": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "severity": "critical",
        "category": "Secrets",
        "message": "Identifiant d'accès AWS codé en dur",
        "recommendation": "Révoquer la clé immédiatement et utiliser des variables d'environnement ou un rôle IAM.",
        "exclude": _PLACEHOLDER_SECRET,
    },
    {
        "id": "hardcoded-secret",
        "pattern": re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*[\"'][^\"']{8,}[\"']"
        ),
        "severity": "high",
        "category": "Secrets",
        "message": "Secret potentiellement codé en dur",
        "recommendation": "Déplacer la valeur vers une variable d'environnement ou un coffre de secrets.",
        "exclude": _PLACEHOLDER_SECRET,
    },
    {
        "id": "sql-injection",
        # Uppercase keywords with their SQL context (FROM / INTO / SET), so a
        # plain English word like "update" in a message never matches.
        "pattern": re.compile(
            r"(f[\"'][^\"']*(SELECT\s[^\"']*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"']*\{"
            r"|[\"'][^\"']*(SELECT\s[^\"']*\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)[^\"']*[\"']\s*(%|\+|\.format\())"
        ),
        "severity": "critical",
        "category": "Injection",
        "message": "Requête SQL construite par interpolation de chaîne (risque d'injection SQL)",
        "recommendation": "Utiliser des requêtes paramétrées (placeholders) ou un ORM.",
    },
    {
        "id": "eval-exec",
        "pattern": re.compile(r"(?<![\w.])(eval|exec)\s*\("),
        "severity": "high",
        "category": "Exécution de code",
        "message": "Usage de eval/exec sur du contenu potentiellement contrôlé",
        "recommendation": "Éviter eval/exec ; préférer un parsing explicite (ast.literal_eval, JSON...).",
        "exts": {".py", ".js", ".ts", ".jsx", ".tsx"},
        "skip_in_string": True,
    },
    {
        "id": "pickle",
        "pattern": re.compile(r"\bpickle\.loads?\("),
        "severity": "high",
        "category": "Désérialisation",
        "message": "Désérialisation pickle (exécution de code arbitraire possible)",
        "recommendation": "Ne jamais désérialiser des données non fiables ; préférer JSON.",
        "exts": {".py"},
        "skip_in_string": True,
    },
    {
        "id": "yaml-load",
        "pattern": re.compile(r"\byaml\.load\((?![^)]*Loader)"),
        "severity": "medium",
        "category": "Désérialisation",
        "message": "yaml.load sans Loader explicite",
        "recommendation": "Utiliser yaml.safe_load.",
        "exts": {".py"},
        "skip_in_string": True,
    },
    {
        "id": "shell-true",
        "pattern": re.compile(r"shell\s*=\s*True"),
        "severity": "high",
        "category": "Commandes système",
        "message": "subprocess avec shell=True (risque d'injection de commande)",
        "recommendation": "Passer une liste d'arguments sans shell=True.",
        "exts": {".py"},
        "skip_in_string": True,
    },
    {
        "id": "os-system",
        "pattern": re.compile(r"\bos\.system\("),
        "severity": "medium",
        "category": "Commandes système",
        "message": "os.system (préférer subprocess avec arguments séparés)",
        "recommendation": "Remplacer par subprocess.run([...]) sans shell.",
        "exts": {".py"},
        "skip_in_string": True,
    },
    {
        "id": "debug-true",
        "pattern": re.compile(r"(?i)debug\s*=\s*True"),
        "severity": "medium",
        "category": "Configuration",
        "message": "Mode debug activé dans le code",
        "recommendation": "Piloter le mode debug via l'environnement, jamais en dur pour la production.",
        "exts": {".py"},
        "skip_in_string": True,
    },
    {
        "id": "cors-wildcard",
        "pattern": re.compile(r"(Access-Control-Allow-Origin[\"']?\s*[:,]\s*[\"']\*|allow_origins\s*=\s*\[?\s*[\"']\*)"),
        "severity": "medium",
        "category": "CORS",
        "message": "CORS * avec credentials (origine quelconque peut lire des réponses authentifiées)",
        "recommendation": "Restreindre les origines et/ou désactiver Allow-Credentials avec *.",
        # Only reported when credentials are also enabled (see _scan_security).
        "requires_credentials": True,
    },
    {
        "id": "weak-hash",
        "pattern": re.compile(r"hashlib\.(md5|sha1)\("),
        "severity": "medium",
        "category": "Cryptographie",
        "message": "MD5/SHA1 utilisé pour un secret / mot de passe",
        "recommendation": "Pour les mots de passe utiliser bcrypt/argon2 ; SHA-256+ uniquement pour des checksums non sécuritaires.",
        "exts": {".py"},
        "skip_in_string": True,
        # Only when nearby lines talk about passwords/secrets (see _scan_security).
        "requires_secret_context": True,
    },
    {
        "id": "tls-verify-off",
        "pattern": re.compile(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify\s*:\s*true"),
        "severity": "high",
        "category": "TLS",
        "message": "Vérification TLS désactivée",
        "recommendation": "Réactiver la vérification des certificats ; utiliser un bundle CA interne si nécessaire.",
        "skip_in_string": True,
    },
    {
        "id": "inner-html",
        "pattern": re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|document\.write\("),
        "severity": "medium",
        "category": "XSS",
        "message": "Injection HTML directe (risque XSS)",
        "recommendation": "Assainir le contenu (DOMPurify) ou passer par le rendu React standard.",
        "exts": {".ts", ".tsx", ".js", ".jsx"},
    },
    {
        "id": "jwt-none",
        "pattern": re.compile(r"(?i)(algorithms?\s*[:=]\s*\[?\s*[\"']none[\"']|verify_signature[\"']?\s*[:=]\s*False)"),
        "severity": "high",
        "category": "Authentification",
        "message": "Vérification JWT affaiblie (alg none / signature non vérifiée)",
        "recommendation": "Toujours vérifier la signature avec un algorithme fort explicite.",
    },
    {
        "id": "http-url",
        "pattern": re.compile(
            r"[\"']http://(?!localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|"
            r"schemas\.|www\.w3\.org|purl\.org|json-schema\.org|example\.com|example\.)"
            r"[\w.-]+"
        ),
        "severity": "low",
        "category": "Transport",
        "message": "URL HTTP non chiffrée vers un hôte externe",
        "recommendation": "Préférer HTTPS pour tout appel externe.",
    },
    {
        "id": "chmod-777",
        "pattern": re.compile(r"chmod\s+777|0o777"),
        "severity": "medium",
        "category": "Permissions fichiers",
        "message": "Permissions fichiers 777 (tout le monde peut écrire/exécuter)",
        "recommendation": "Restreindre aux permissions minimales nécessaires.",
        "skip_in_string": True,
    },
]


def _should_skip_security_match(
    check: dict[str, Any],
    *,
    file: _ScannedFile,
    line: str,
    lines: list[str],
    idx: int,
    match: re.Match[str],
) -> bool:
    """Return True when the match is a known false positive."""
    check_id = check["id"]
    if _is_audit_fixture_path(file.rel) and check_id in {
        "private-key",
        "aws-key",
        "hardcoded-secret",
        "chmod-777",
        "http-url",
        "eval-exec",
        "weak-hash",
        "tls-verify-off",
        "cors-wildcard",
        "inner-html",
    }:
        return True
    if _is_pattern_definition_line(line):
        return True

    exclude = check.get("exclude")
    if exclude and exclude.search(line):
        return True
    if check.get("skip_in_string") and _in_non_code_context(line, match.start(), file.ext):
        return True
    # Comment-only suppressions. Do not treat JS/TS quoted keys/values as
    # non-code for CORS / URLs / XSS sinks - those sinks live in strings.
    if check_id in {"eval-exec", "chmod-777", "tls-verify-off"}:
        if _in_non_code_context(line, match.start(), file.ext):
            return True
    if check_id in {"cors-wildcard", "http-url", "inner-html"}:
        comment_marker = "#" if file.ext == ".py" else "//"
        comment_index = line.find(comment_marker)
        if comment_index != -1 and not _inside_string_literal(line, comment_index):
            if comment_index < match.start():
                return True
        if _in_block_comment(line, match.start()):
            return True

    if check_id == "eval-exec":
        # Intentional python -c runner: exec(compile(...)), not eval(user).
        if re.search(r"\bexec\s*\(\s*compile\s*\(", line):
            return True
        if re.search(r"\beval\s*\(\s*[\"']", line):
            return True

    if check.get("requires_secret_context"):
        if not _WEAK_HASH_SECRET_CONTEXT.search(_window_text(lines, idx, before=3, after=2)):
            return True

    if check_id == "tls-verify-off":
        window = _window_text(lines, idx, before=20, after=2)
        if _TLS_FALLBACK_HINT.search(window):
            return True

    if check.get("requires_credentials"):
        # Public * without credentials is normal for local WebUI ↔ catalog APIs.
        if not _CORS_CREDENTIALS.search(_window_text(lines, idx, before=25, after=15)):
            return True

    if check_id == "inner-html":
        window = _window_text(lines, idx, before=8, after=4)
        if "application/ld+json" in window or "JSON.stringify" in line or "jsonLd" in line:
            return True
        if "mermaid" in window.lower() and "securityLevel" in window:
            return True
        if "securityLevel" in window and "strict" in window:
            return True

    if check_id == "http-url":
        if _HTTP_SCHEMA_HINT.search(line) or "$schema" in line:
            return True

    return False


def _scan_security(files: list[_ScannedFile], env_files: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for file in files:
        if file.ext not in _CODE_EXTS and file.ext not in (".yml", ".yaml", ".json", ".env", ".toml", ".pem", ".key"):
            continue
        if _is_test_path(file.rel) or file.rel.endswith(".d.ts"):
            continue
        text = _load_text(file)
        if not text:
            continue
        lines = text.splitlines()

        if (
            file.ext == ".py"
            and not _is_audit_fixture_path(file.rel)
        ):
            for item in scan_python_file_ast(file.rel, text):
                if item["id"] in _AST_SECURITY_IDS:
                    findings.append(item)

        for idx, line in enumerate(lines):
            if len(line) > 500:
                continue
            for check in _SECURITY_CHECKS:
                if file.ext == ".py" and check["id"] in _AST_SECURITY_IDS:
                    continue
                exts = check.get("exts")
                if exts and file.ext not in exts:
                    continue
                match = check["pattern"].search(line)
                if not match:
                    continue
                if _should_skip_security_match(
                    check, file=file, line=line, lines=lines, idx=idx, match=match
                ):
                    continue
                confidence = _CONFIDENCE_BY_CHECK.get(check["id"], "medium")
                findings.append(
                    _make_finding(
                        finding_id=check["id"],
                        severity=check["severity"],
                        category=check["category"],
                        message=check["message"],
                        recommendation=check["recommendation"],
                        file=file.rel,
                        line=idx + 1,
                        snippet=line.strip(),
                        confidence=confidence,
                        evidence=(
                            f"Motif `{check['id']}` sur {file.rel}:{idx + 1} "
                            f"(confiance {confidence})."
                        ),
                    )
                )
                if len(findings) >= _MAX_FINDINGS_PER_KIND:
                    break
            if len(findings) >= _MAX_FINDINGS_PER_KIND:
                break
        if len(findings) >= _MAX_FINDINGS_PER_KIND:
            break

    for env in env_files:
        if not env["is_example"] and not env["gitignored"]:
            findings.append(
                _make_finding(
                    finding_id="env-not-ignored",
                    severity="high",
                    category="Secrets",
                    message=f"Fichier {env['path']} présent et non couvert par .gitignore",
                    recommendation="Ajouter .env au .gitignore et fournir un .env.example sans valeurs.",
                    file=env["path"],
                    line=1,
                    snippet="",
                    confidence="high",
                    evidence="Fichier d'environnement présent hors .gitignore.",
                )
            )

    return _finalize_findings(findings)


# ---------------------------------------------------------------------------
# Performance findings

_BLOCKING_SLEEP = re.compile(r"\btime\.sleep\(")
_BLOCKING_REQUESTS = re.compile(r"\brequests\.(get|post|put|delete|patch|request)\(")
_BLOCKING_SUBPROCESS = re.compile(r"\bsubprocess\.(run|check_output|call)\(")
_PY_DEF = re.compile(r"^(\s*)(async\s+def|def)\s+(\w+)\s*\(")
_PY_CLASS = re.compile(r"^(\s*)class\s+")
_LOOP_DB_QUERY = re.compile(r"\.(execute|query)\s*\(")
_DB_CONTEXT = re.compile(r"(?i)\b(cursor|session|conn|connection|db|database|sqlalchemy|orm)\b")
_RETRY_LOOP = re.compile(r"(?i)\b(attempt|retry|retries|range\s*\(\s*[1-5]\s*\))")


def _python_def_ranges(lines: list[str]) -> list[dict[str, Any]]:
    """Collect def / async def ranges (start inclusive, end exclusive)."""
    defs: list[dict[str, Any]] = []
    classes: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        match = _PY_DEF.match(line)
        if match:
            defs.append(
                {
                    "kind": "async" if match.group(2).startswith("async") else "sync",
                    "name": match.group(3),
                    "indent": len(match.group(1)),
                    "start": i,
                    "end": len(lines),
                }
            )
            continue
        class_match = _PY_CLASS.match(line)
        if class_match:
            classes.append((i, len(class_match.group(1))))
    for item in defs:
        ends = [len(lines)]
        for nxt in defs:
            if nxt["start"] > item["start"] and nxt["indent"] <= item["indent"]:
                ends.append(nxt["start"])
        for class_idx, class_indent in classes:
            if class_idx > item["start"] and class_indent <= item["indent"]:
                ends.append(class_idx)
        item["end"] = min(ends)
    return defs


def _blocking_sync_names(lines: list[str], defs: list[dict[str, Any]]) -> set[str]:
    """Sync functions that sleep / subprocess / requests, plus callers of them."""
    blocking: set[str] = set()
    for item in defs:
        if item["kind"] != "sync":
            continue
        body = "\n".join(lines[item["start"] : item["end"]])
        if (
            _BLOCKING_SLEEP.search(body)
            or _BLOCKING_SUBPROCESS.search(body)
            or _BLOCKING_REQUESTS.search(body)
        ):
            blocking.add(item["name"])

    # One-hop callers (e.g. _collect_hits → _rg_scan) also block when used
    # directly from a coroutine without to_thread.
    changed = True
    while changed:
        changed = False
        for item in defs:
            if item["kind"] != "sync" or item["name"] in blocking:
                continue
            body = "\n".join(lines[item["start"] : item["end"]])
            for name in list(blocking):
                if re.search(rf"\b{re.escape(name)}\s*\(", body):
                    blocking.add(item["name"])
                    changed = True
                    break
    return blocking


def _scan_performance(files: list[_ScannedFile]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    huge_files: list[dict[str, Any]] = []
    for file in files:
        if file.ext not in _CODE_EXTS or _is_test_path(file.rel):
            continue
        if _is_audit_fixture_path(file.rel):
            # Don't score the audit engine / PoC fixtures against themselves.
            text = _load_text(file)
            if text and text.count("\n") > 1500:
                huge_files.append({"path": file.rel, "lines": text.count("\n") + 1})
            continue
        text = _load_text(file)
        if not text:
            continue
        lines = text.splitlines()
        if len(lines) > 1500:
            huge_files.append({"path": file.rel, "lines": len(lines)})

        if file.ext == ".py":
            for item in scan_python_file_ast(file.rel, text):
                if item["id"] in _AST_PERF_IDS:
                    findings.append(item)

            defs = _python_def_ranges(lines)
            blocking_names = _blocking_sync_names(lines, defs)
            for item in defs:
                if item["kind"] != "async":
                    continue
                skip_until_indent: int | None = None
                for idx in range(item["start"] + 1, item["end"]):
                    line = lines[idx]
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    indent = len(line) - len(line.lstrip())
                    if skip_until_indent is not None:
                        if indent > skip_until_indent:
                            continue
                        skip_until_indent = None
                    if _is_pattern_definition_line(line):
                        continue
                    nested = _PY_DEF.match(line)
                    if nested and len(nested.group(1)) > item["indent"]:
                        skip_until_indent = len(nested.group(1))
                        continue
                    if "to_thread" in line or "create_subprocess" in line:
                        continue
                    for name in blocking_names:
                        call = re.search(rf"\b{re.escape(name)}\s*\(", line)
                        if not call:
                            continue
                        if _in_non_code_context(line, call.start(), file.ext):
                            continue
                        findings.append(
                            _make_finding(
                                finding_id="sync-call-from-async",
                                severity="medium",
                                category="Event loop",
                                message=(
                                    f"Coroutine async appelle `{name}()` qui fait du I/O "
                                    "synchrone (sleep/subprocess/requests) sans to_thread"
                                ),
                                recommendation="Envelopper l'appel avec await asyncio.to_thread(...).",
                                file=file.rel,
                                line=idx + 1,
                                snippet=line.strip(),
                                confidence="medium",
                                evidence=(
                                    f"`{name}` contient du I/O synchrone et est appelé "
                                    f"depuis async `{item['name']}` sans to_thread."
                                ),
                            )
                        )
                        break

        for idx, line in enumerate(lines):
            if file.ext in {".ts", ".tsx", ".js", ".jsx"}:
                clone = re.search(r"JSON\.parse\(\s*JSON\.stringify\(", line)
                if clone and not _in_non_code_context(line, clone.start(), file.ext):
                    findings.append(
                        _make_finding(
                            finding_id="json-deep-clone",
                            severity="low",
                            category="CPU",
                            message="Clonage profond via JSON.parse(JSON.stringify(...)) (coûteux)",
                            recommendation="Utiliser structuredClone(...) ou un clonage ciblé.",
                            file=file.rel,
                            line=idx + 1,
                            snippet=line.strip(),
                            confidence="high",
                            evidence="Appel JSON.parse(JSON.stringify(...)) détecté.",
                        )
                    )

            if file.ext == ".py" and ".fetchall(" in line:
                match = re.search(r"\.fetchall\(\)", line)
                if match and not _in_non_code_context(line, match.start(), file.ext):
                    findings.append(
                        _make_finding(
                            finding_id="fetchall",
                            severity="low",
                            category="Base de données",
                            message="fetchall() sans pagination (charge tout le résultat en mémoire)",
                            recommendation="Paginer (LIMIT/OFFSET, curseurs) ou itérer sur le curseur.",
                            file=file.rel,
                            line=idx + 1,
                            snippet=line.strip(),
                            confidence="medium",
                            evidence="Appel .fetchall() sans borne visible sur cette ligne.",
                        )
                    )

            select = re.search(r"(?i)[\"'`]\s*SELECT\s+\*\s+FROM", line)
            if select and not _in_non_code_context(line, select.start(), file.ext):
                findings.append(
                    _make_finding(
                        finding_id="select-star",
                        severity="low",
                        category="Base de données",
                        message="SELECT * (colonnes non bornées, plus de données que nécessaire)",
                        recommendation="Lister explicitement les colonnes nécessaires.",
                        file=file.rel,
                        line=idx + 1,
                        snippet=line.strip(),
                        confidence="high",
                        evidence="Literal SQL SELECT * FROM détecté.",
                    )
                )

            stripped = line.lstrip()
            if stripped.startswith(("for ", "for(")) and file.ext in {
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
            }:
                if _RETRY_LOOP.search(stripped):
                    continue
                indent = len(line) - len(stripped)
                for offset in range(1, 12):
                    if idx + offset >= len(lines):
                        break
                    inner = lines[idx + offset]
                    inner_stripped = inner.lstrip()
                    if not inner_stripped:
                        continue
                    if len(inner) - len(inner_stripped) <= indent:
                        break
                    if not _LOOP_DB_QUERY.search(inner):
                        continue
                    if not _DB_CONTEXT.search(inner) and not _DB_CONTEXT.search(stripped):
                        if not re.search(
                            r"(?i)\b(session|cursor|conn|db)\s*\.\s*(execute|query)\s*\(",
                            inner,
                        ):
                            continue
                    if "client.get" in inner or "httpx" in inner or "requests." in inner:
                        continue
                    findings.append(
                        _make_finding(
                            finding_id="n-plus-one",
                            severity="medium",
                            category="Base de données",
                            message="Requête DB exécutée à l'intérieur d'une boucle (motif N+1 possible)",
                            recommendation="Regrouper les requêtes (IN, jointure, bulk) hors de la boucle.",
                            file=file.rel,
                            line=idx + offset + 1,
                            snippet=inner_stripped,
                            confidence="medium",
                            evidence="Appel .execute/.query dans le corps d'une boucle for.",
                        )
                    )
                    break

            if len(findings) >= _MAX_FINDINGS_PER_KIND:
                break
        if len(findings) >= _MAX_FINDINGS_PER_KIND:
            break

    huge_files.sort(key=lambda f: -f["lines"])
    for huge in huge_files[:8]:
        findings.append(
            _make_finding(
                finding_id="huge-file",
                severity="low",
                category="Maintenabilité",
                message=(
                    f"Fichier très long ({huge['lines']} lignes) : "
                    "lecture, revue et build plus lents"
                ),
                recommendation="Découper en modules plus petits et ciblés.",
                file=huge["path"],
                line=1,
                snippet="",
                # Size alone is maintainability noise, not a runtime hotspot:
                # keep it visible but out of the Performance score.
                confidence="low",
                evidence=f"Comptage: {huge['lines']} lignes (> 1500).",
            )
        )
    return _finalize_findings(findings)


# ---------------------------------------------------------------------------
# Notes


def _build_notes(
    overview: dict[str, Any],
    api: dict[str, Any],
    permissions: dict[str, Any],
    sql: dict[str, Any],
    infrastructure: dict[str, Any],
    security: dict[str, Any],
    performance: dict[str, Any],
) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []

    sec_summary = security["summary"]
    if sec_summary["critical"]:
        notes.append(
            {
                "kind": "risk",
                "title": f"{sec_summary['critical']} risque(s) critique(s) de sécurité",
                "body": "Des problèmes critiques (secrets exposés, injection SQL...) ont été détectés. À traiter en priorité absolue avant toute mise en production.",
            }
        )
    elif sec_summary["high"]:
        notes.append(
            {
                "kind": "risk",
                "title": f"{sec_summary['high']} risque(s) de sécurité élevé(s)",
                "body": "Consulter l'onglet Sécurité : chaque constat inclut le fichier, la ligne et une recommandation concrète.",
            }
        )
    else:
        notes.append(
            {
                "kind": "strength",
                "title": "Aucun risque de sécurité critique ou élevé détecté",
                "body": "L'analyse statique n'a rien relevé de critique. Elle reste heuristique : elle ne remplace pas un audit de dépendances (npm audit, pip-audit) ni un pentest.",
            }
        )

    unprotected = permissions.get("unprotected_count", 0)
    total_endpoints = api.get("total", 0)
    coverage = permissions.get("coverage_percent", 0)
    if total_endpoints and unprotected and coverage < 70:
        notes.append(
            {
                "kind": "risk",
                "title": f"{unprotected} endpoint(s) sans garde locale ({coverage}% couverts)",
                "body": (
                    "Heuristique : voisinage du handler + portes module (`_authorized` / "
                    "`check_api_token`). Vérifier middleware global et routes volontairement publiques."
                ),
            }
        )
    elif total_endpoints and coverage >= 85:
        notes.append(
            {
                "kind": "strength",
                "title": f"Couverture d'auth API élevée ({coverage}%)",
                "body": f"{permissions.get('protected_endpoints', 0)} endpoints avec garde détectée sur {total_endpoints}.",
            }
        )

    risky_sql = sum(1 for q in sql.get("queries", []) if q.get("risky"))
    sql_findings = sql.get("summary") or {}
    sql_critical = int(sql_findings.get("critical") or 0)
    sql_high = int(sql_findings.get("high") or 0)
    if sql_critical or sql_high:
        notes.append(
            {
                "kind": "risk",
                "title": (
                    f"SQL : {sql_critical} critique(s), {sql_high} élevé(s) "
                    f"sur {sql.get('files_scanned', 0)} fichier(s)"
                ),
                "body": (
                    "L'onglet SQL analyse tout le dépôt (fichiers .sql + requêtes embarquées) : "
                    "injection, DELETE/UPDATE sans WHERE, SELECT *, LIKE '%...', etc. "
                    "Traiter ces constats avant la prod."
                ),
            }
        )
    elif int(sql_findings.get("medium") or 0) or int(sql_findings.get("low") or 0):
        notes.append(
            {
                "kind": "action",
                "title": f"{risky_sql} requête(s) SQL avec signal(s) sécu/perf",
                "body": "Ouvrir l'onglet SQL : chaque requête listée peut porter des findings sécurité et performance.",
            }
        )
    elif risky_sql and sql.get("scored_findings"):
        notes.append(
            {
                "kind": "action",
                "title": f"{risky_sql} requête(s) SQL avec signal(s) sécu/perf",
                "body": "Ouvrir l'onglet SQL : chaque requête listée peut porter des findings sécurité et performance.",
            }
        )

    perf_summary = performance["summary"]
    if perf_summary["high"] or perf_summary["medium"]:
        notes.append(
            {
                "kind": "action",
                "title": f"Performance : {perf_summary['high']} constat(s) élevé(s), {perf_summary['medium']} moyen(s)",
                "body": "Points chauds probables : appels bloquants dans du code async, requêtes en boucle (N+1). Détails dans l'onglet Performance.",
            }
        )

    tests = overview.get("tests", {}).get("files", 0)
    if tests:
        notes.append(
            {
                "kind": "strength",
                "title": f"{tests} fichier(s) de test présent(s)",
                "body": "Le projet dispose d'une base de tests. Maintenir la couverture sur les modules critiques (auth, API, données).",
            }
        )
    else:
        notes.append(
            {
                "kind": "action",
                "title": "Aucun fichier de test détecté",
                "body": "Ajouter des tests automatisés au moins sur les chemins critiques (authentification, écriture de données).",
            }
        )

    if infrastructure.get("ci"):
        notes.append(
            {
                "kind": "strength",
                "title": "Intégration continue configurée",
                "body": "Des workflows CI existent : c'est la bonne base pour ajouter lint, tests et audit de dépendances à chaque commit.",
            }
        )
    else:
        notes.append(
            {
                "kind": "action",
                "title": "Pas de CI détectée",
                "body": "Mettre en place une CI (GitHub Actions, GitLab CI...) pour exécuter lint et tests automatiquement.",
            }
        )

    env_leaks = (infrastructure.get("summary") or {}).get("env_not_gitignored") or []
    if env_leaks:
        notes.append(
            {
                "kind": "risk",
                "title": f"{len(env_leaks)} fichier(s) .env non gitignoré(s)",
                "body": "Risque de fuite de secrets : ajouter ces fichiers à .gitignore et rotater les clés exposées.",
            }
        )

    todos = overview.get("todos", {})
    total_todos = sum(todos.values()) if todos else 0
    if total_todos > 20:
        notes.append(
            {
                "kind": "info",
                "title": f"{total_todos} marqueurs TODO / FIXME / HACK dans le code",
                "body": "Dette technique déclarée par les développeurs eux-mêmes : planifier leur résorption progressive.",
            }
        )

    git = overview.get("git", {})
    if git.get("is_repo") and git.get("dirty"):
        notes.append(
            {
                "kind": "info",
                "title": "Copie de travail non commitée",
                "body": "Des modifications locales ne sont pas commitées : le rapport reflète l'état du disque, pas celui du dernier commit.",
            }
        )

    notes.append(
        {
            "kind": "info",
            "title": "Limites de l'analyse",
            "body": "Rapport généré par analyse statique locale, sans exécution du code : les scores sont des indicateurs, pas des certifications. Compléter par npm audit / pip-audit (dépendances vulnérables), un profilage réel (performance) et une revue humaine.",
        }
    )
    return notes


# ---------------------------------------------------------------------------
# Main payload


def project_audit_payload(raw_path: str) -> dict[str, Any]:
    started = time.monotonic()
    root = _check_dir(raw_path)
    files = _collect_files(root)

    # Language stats: count lines for text files within budget.
    languages: dict[str, dict[str, int]] = {}
    total_lines = 0
    total_bytes = 0
    content_budget = _MAX_CONTENT_FILES
    largest: list[dict[str, Any]] = []
    todo = fixme = hack = 0
    test_files = 0
    doc_files = 0
    for file in files:
        total_bytes += file.size
        if _is_test_path(file.rel):
            test_files += 1
        if file.ext in (".md", ".mdx", ".rst") or file.rel.lower().startswith("docs/"):
            doc_files += 1
        if file.ext not in _TEXT_EXTS or content_budget <= 0:
            continue
        content_budget -= 1
        text = _load_text(file)
        if not text:
            continue
        total_lines += file.lines
        lang = _LANGUAGE_BY_EXT.get(file.ext)
        if lang:
            bucket = languages.setdefault(lang, {"files": 0, "lines": 0})
            bucket["files"] += 1
            bucket["lines"] += file.lines
        if file.ext in _CODE_EXTS:
            largest.append({"path": file.rel, "lines": file.lines, "bytes": file.size})
            todo += text.count("TODO")
            fixme += text.count("FIXME")
            hack += text.count("HACK")

    largest.sort(key=lambda f: -f["lines"])
    code_lines = sum(
        stats["lines"]
        for lang, stats in languages.items()
        if lang not in ("Markdown", "JSON", "YAML", "TOML", "reStructuredText")
    )
    language_rows = [
        {
            "name": lang,
            "files": stats["files"],
            "lines": stats["lines"],
            "percent": round(stats["lines"] * 100 / max(total_lines, 1), 1),
        }
        for lang, stats in sorted(languages.items(), key=lambda kv: -kv[1]["lines"])
    ][:14]

    dependencies = _detect_dependencies(root, files)
    stack = _detect_stack(dependencies, files)
    git = _git_overview(root)
    api = _extract_endpoints(files)
    openapi_specs = _find_openapi_specs(files)
    compose = _parse_compose(files)
    compose_services = [svc for entry in compose for svc in entry["services"]]
    architecture = _build_architecture(root, files, stack, compose_services)
    permissions = _extract_permissions(files, api)
    sql = _extract_sql(files)
    env_files = _parse_env_files(root, files)
    env_risks = [
        env["path"]
        for env in env_files
        if not env.get("is_example") and not env.get("gitignored")
    ]
    dockerfiles = _parse_dockerfiles(files)
    ci = _parse_ci(files)
    makefile_targets = _parse_makefile(files)
    infrastructure = {
        "docker_compose": compose,
        "dockerfiles": dockerfiles,
        "ci": ci,
        "makefile_targets": makefile_targets,
        "env_files": [
            {**env, "keys": env["keys"] if env["is_example"] else [f"{k}" for k in env["keys"]]}
            for env in env_files
        ],
        "summary": {
            "compose_services": sum(len(c["services"]) for c in compose),
            "dockerfiles": len(dockerfiles),
            "ci_workflows": len(ci),
            "env_files": len(env_files),
            "env_not_gitignored": env_risks,
            "has_ci": bool(ci),
            "has_containers": bool(compose or dockerfiles),
        },
    }
    pipeline = _extract_pipeline(files)
    security = _scan_security(files, env_files)
    performance = _scan_performance(files)

    overview = {
        "git": git,
        "totals": {
            "files": len(files),
            "lines": total_lines,
            "code_lines": code_lines,
            "bytes": total_bytes,
        },
        "languages": language_rows,
        "stack": stack,
        "dependencies": dependencies,
        "tests": {"files": test_files},
        "docs": {"files": doc_files},
        "todos": {"todo": todo, "fixme": fixme, "hack": hack},
        "largest_files": largest[:8],
        "api_endpoints": api["total"],
        "sql_queries": sql["total"],
        "sql_files": sql.get("files_scanned", 0),
        "scores": {
            "security": security["score"],
            "performance": performance["score"],
            "sql": sql.get("score", 100),
        },
    }
    overview["health_score"] = round(
        (security["score"] * 0.35)
        + (performance["score"] * 0.22)
        + (sql.get("score", 100) * 0.18)
        + (min(100, test_files * 4) * 0.15)
        + (min(100, doc_files * 5) * 0.10)
    )

    notes = _build_notes(
        overview, api, permissions, sql, infrastructure, security, performance
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "project": {"name": root.name, "path": str(root)},
        "overview": overview,
        "api": api,
        "openapi": {"specs": openapi_specs},
        "architecture": architecture,
        "permissions": permissions,
        "sql": sql,
        "infrastructure": infrastructure,
        "pipeline": pipeline,
        "security": security,
        "performance": performance,
        "notes": notes,
    }
