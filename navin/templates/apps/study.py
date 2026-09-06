"""Study each app-template clone and write a complete packages/<slug>/install.json."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from navin.templates.apps.catalog import APP_TEMPLATES, get_app_template
from navin.templates.apps.install import local_cache_dir, package_dir
from navin.templates.apps.playbooks import apply_curated
from navin.templates.apps.schema import INSTALL_JSON_SCHEMA

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".next",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".turbo",
    ".cache",
    "coverage",
    ".pytest_cache",
    "vendor",
    "target",
}

ENV_FILENAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    "env.example",
    ".env.local.example",
    ".env.development.example",
    ".env-example",
    ".env.single-bucket-example",
}

ENV_SKIP_PREFIXES = (
    "www/",
    "docs/",
    "doc/",
    ".github/",
    ".claude/",
    ".cursor/",
    "integration-tests/",
    "e2e/",
    "storybook/",
)

NOISE_ENV_PREFIXES = (
    "CLAUDE_",
    "FIGMA_",
    "PLAYWRIGHT_",
    "VITEST_",
    "CHROME_",
    "GITHUB_",
    "HARNESS_",
    "NEXT_PUBLIC_ALGOLIA",
    "NEXT_PUBLIC_SEGMENT",
)

NOISE_ENV_KEYS = {
    "HOME",
    "PATH",
    "USER",
    "PWD",
    "CI",
    "TMPDIR",
    "LANG",
    "DISPLAY",
    "HOSTNAME",
    "MODE",
    "APP_DIR",
    "TERM",
    "SHELL",
}

PKG_SKIP_TOKENS = (
    "eslint",
    "prettier",
    "typescript",
    "telemetry",
    "test-utils",
    "oas-github",
    "http-types-generator",
    "sandbox_base_image",
)

FRONT_DEPS = {"next", "vite", "react", "react-dom", "astro", "nuxt", "svelte"}
BACK_DEPS = {
    "express",
    "fastify",
    "@nestjs/core",
    "mongoose",
    "prisma",
    "@prisma/client",
    "pg",
    "koa",
    "hono",
}

SOURCE_ENV_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go"}

FRONT_DIR_HINTS = ("frontend", "front", "client", "web", "app", "ui", "dashboard", "www")
BACK_DIR_HINTS = ("backend", "back", "server", "api", "service", "services")

ENV_PURPOSE = {
    "DATABASE_URL": "SQL connection string",
    "POSTGRES_URL": "Postgres connection string",
    "POSTGRES_PASSWORD": "Postgres password",
    "POSTGRES_USER": "Postgres user",
    "POSTGRES_DB": "Postgres database name",
    "POSTGRES_HOST": "Postgres host",
    "POSTGRES_PORT": "Postgres port",
    "MONGODB_URI": "MongoDB connection string",
    "MONGO_URI": "MongoDB connection string",
    "MONGO_URL": "MongoDB connection string",
    "REDIS_URL": "Redis connection string",
    "REDIS_HOST": "Redis host",
    "SUPABASE_URL": "Supabase API URL",
    "VITE_SUPABASE_URL": "Supabase API URL (Vite)",
    "NEXT_PUBLIC_SUPABASE_URL": "Supabase API URL (Next)",
    "SUPABASE_ANON_KEY": "Supabase anon key",
    "VITE_SB_PUBLISHABLE_KEY": "Supabase publishable key (Vite)",
    "SUPABASE_SERVICE_ROLE_KEY": "Supabase service role (secret)",
    "OPENAI_API_KEY": "OpenAI API key",
    "ANTHROPIC_API_KEY": "Anthropic API key",
    "JWT_SECRET": "JWT signing secret",
    "NEXTAUTH_SECRET": "NextAuth secret",
    "NEXTAUTH_URL": "Public app URL for NextAuth",
    "STRIPE_SECRET_KEY": "Stripe secret key",
    "STRIPE_WEBHOOK_SECRET": "Stripe webhook secret",
    "PORT": "HTTP port",
    "NODE_ENV": "Node environment",
    "HOST": "Bind host",
}

DB_HINTS = (
    ("postgres", ("postgres", "postgresql", "pg", "@prisma/client", "supabase")),
    ("mongodb", ("mongodb", "mongo", "mongoose")),
    ("mysql", ("mysql", "mariadb", "mysql2")),
    ("sqlite", ("sqlite", "better-sqlite3")),
    ("redis", ("redis", "ioredis", "bullmq")),
    ("supabase", ("supabase", "@supabase")),
    ("elasticsearch", ("elasticsearch", "opensearch", "elastic")),
    ("minio", ("minio", "s3-compatible")),
)

DEP_TO_ENGINE = {
    "pg": "postgres",
    "postgres": "postgres",
    "postgresql": "postgres",
    "@prisma/client": "postgres",
    "prisma": "postgres",
    "@supabase/supabase-js": "supabase",
    "mongoose": "mongodb",
    "mongodb": "mongodb",
    "mysql2": "mysql",
    "mysql": "mysql",
    "ioredis": "redis",
    "redis": "redis",
    "better-sqlite3": "sqlite",
    "knex": "postgres",
    "typeorm": "postgres",
    "@mikro-orm/postgresql": "postgres",
    "elasticsearch": "elasticsearch",
    "minio": "minio",
}


def _rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _iter_files(root: Path, names: set[str] | None = None) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            if names and filename not in names:
                continue
            found.append(Path(dirpath) / filename)
    return found


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _purpose(key: str) -> str:
    if key in ENV_PURPOSE:
        return ENV_PURPOSE[key]
    upper = key.upper()
    if "MONGO" in upper:
        return "MongoDB setting"
    if "POSTGRES" in upper or "DATABASE" in upper or "PG_" in upper:
        return "Database setting"
    if "REDIS" in upper:
        return "Redis setting"
    if "OPENAI" in upper or "ANTHROPIC" in upper or "GEMINI" in upper or "LLM" in upper:
        return "Model / LLM provider"
    if "STRIPE" in upper:
        return "Payments (Stripe)"
    if "SMTP" in upper or "MAIL" in upper or "POSTMARK" in upper:
        return "Email / SMTP"
    if "S3" in upper or "AWS" in upper or "BUCKET" in upper:
        return "Object storage"
    if "JWT" in upper or "SECRET" in upper or "TOKEN" in upper:
        return "Auth / secret"
    if "PORT" in upper:
        return "HTTP / service port"
    if "URL" in upper:
        return "Service URL"
    return ""


def _parse_env_file(path: Path, root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key or key in seen or "${" in key:
            continue
        seen.add(key)
        secret = any(tok in key.upper() for tok in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE", "SERVICE_ROLE"))
        if key.upper().endswith("_KEY") and "PUBLIC" not in key.upper() and "PUBLISHABLE" not in key.upper():
            secret = True
        rows.append(
            {
                "key": key,
                "default": value[:180],
                "required": (not value) or secret,
                "secret": secret,
                "purpose": _purpose(key),
                "from": _rel(root, path),
            }
        )
    return rows


def _lockfile(root: Path) -> str:
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    if (root / "package-lock.json").is_file():
        return "npm"
    if (root / "poetry.lock").is_file():
        return "poetry"
    if (root / "uv.lock").is_file():
        return "uv"
    return ""


def _skipped_rel(rel: str) -> bool:
    low = rel.lower()
    return any(low.startswith(prefix) or f"/{prefix}" in f"/{low}" for prefix in ENV_SKIP_PREFIXES)


def _role_for_pkg(rel: str, deps_hint: list[str] | None = None, name: str = "") -> str:
    parts = rel.lower().split("/")
    parents = parts[:-1]
    hint = {str(item).lower() for item in (deps_hint or [])}
    low_name = (name or "").lower()
    if any(token in low_name or token in rel.lower() for token in PKG_SKIP_TOKENS):
        return "tooling"
    if any(part in BACK_DIR_HINTS for part in parents):
        return "backend"
    if any(part in FRONT_DIR_HINTS for part in parents):
        return "frontend"
    has_front = bool(hint & FRONT_DEPS)
    has_back = bool(hint & BACK_DEPS)
    if rel == "package.json":
        if has_front and has_back:
            return "fullstack"
        if has_front:
            return "frontend"
        if has_back:
            return "backend"
        return "root"
    if has_front and not has_back:
        return "frontend"
    if has_back and not has_front:
        return "backend"
    return "package"


def _scan_packages(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    paths = sorted(_iter_files(root, names={"package.json"}), key=lambda p: (_rel(root, p).count("/"), _rel(root, p)))
    for path in paths[:36]:
        data = _read_json(path)
        rel = _rel(root, path)
        if _skipped_rel(rel):
            continue
        scripts = data.get("scripts") or {}
        keep = ("dev", "start", "build", "lint", "test", "db", "migrate", "seed", "docker", "demo")
        slim: dict[str, str] = {}
        if isinstance(scripts, dict):
            for key, value in scripts.items():
                if isinstance(value, str) and any(tok in key.lower() for tok in keep):
                    slim[str(key)] = value
        deps = {}
        for bucket in ("dependencies", "devDependencies"):
            block = data.get(bucket) or {}
            if isinstance(block, dict):
                deps.update({str(k): str(v) for k, v in list(block.items())[:80]})
        engines = data.get("engines") if isinstance(data.get("engines"), dict) else {}
        name = str(data.get("name") or Path(rel).parent.name or "app")
        deps_hint = sorted(
            dep
            for dep in deps
            if dep in DEP_TO_ENGINE
            or dep
            in {
                "next",
                "react",
                "express",
                "fastify",
                "vite",
                "prisma",
                "mongoose",
                "stripe",
                "framer-motion",
                "antd",
                "axios",
                "hono",
                "wrangler",
            }
        )[:20]
        role = _role_for_pkg(rel, deps_hint, name)
        if role == "tooling":
            continue
        out.append(
            {
                "role": role,
                "path": rel,
                "name": name,
                "scripts": slim,
                "node_engine": str(engines.get("node") or ""),
                "deps_hint": deps_hint,
            }
        )
    py_files = []
    for name in ("pyproject.toml", "requirements.txt", "Pipfile"):
        for path in _iter_files(root, names={name})[:6]:
            py_files.append(_rel(root, path))
    if py_files:
        out.append(
            {
                "role": "backend",
                "path": py_files[0],
                "name": "python",
                "scripts": {},
                "node_engine": "",
                "deps_hint": py_files,
            }
        )
    return out


def _engines_from_packages(packages: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for pkg in packages:
        for dep in pkg.get("deps_hint") or []:
            engine = DEP_TO_ENGINE.get(str(dep))
            if engine and engine not in found:
                found.append(engine)
    return found


def _parse_compose(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    services: list[dict[str, Any]] = []
    current = ""
    image = ""
    ports: list[str] = []
    in_services = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("services:"):
            in_services = True
            continue
        if in_services and re.match(r"^[a-zA-Z0-9_-]+:\s*$", line):
            break
        match = re.match(r"^  ([a-zA-Z0-9_-]+):\s*$", line)
        if match:
            if current:
                services.append({"name": current, "image": image, "ports": ports})
            current = match.group(1)
            image = ""
            ports = []
            continue
        img = re.search(r"image:\s*['\"]?([^'\"\s]+)", line)
        if img and current:
            image = img.group(1)
        port = re.search(r"-\s*['\"]?(?:\$\{[A-Z0-9_]+(?::-?\d+)?\}|(\d+)):(\d+)", line)
        if port and current:
            host = port.group(1) or port.group(2)
            ports.append(f"{host}:{port.group(2)}")
    if current:
        services.append({"name": current, "image": image, "ports": ports})
    return services[:16]


def _compose_bundle(root: Path) -> dict[str, Any]:
    files: list[str] = []
    services: list[dict[str, Any]] = []
    for path in _iter_files(root):
        name = path.name.lower()
        if not (name.startswith("docker-compose") and name.endswith((".yml", ".yaml"))):
            if name not in {"compose.yml", "compose.yaml"}:
                continue
        rel = _rel(root, path)
        if rel.count("/") > 3 or _skipped_rel(rel):
            continue
        files.append(rel)
        services.extend(_parse_compose(path))
    return {"files": files[:10], "services": services[:24]}


def _readme_summary(root: Path) -> dict[str, Any]:
    readme = None
    for name in ("README.md", "readme.md", "README.MD"):
        if (root / name).is_file():
            readme = root / name
            break
    extra = [f"{folder}/" for folder in ("docs", "doc", "documentation") if (root / folder).is_dir()]
    summary = ""
    if readme is not None:
        lines: list[str] = []
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("![", "<", "|", "```", "- [")):
                continue
            if stripped.startswith("#"):
                stripped = stripped.lstrip("#").strip()
            low = stripped.lower()
            if "badge" in low or "shields.io" in low or "http" in low and "img" in low:
                continue
            lines.append(stripped)
            if len(" ".join(lines)) > 480:
                break
        summary = " ".join(lines)[:560]
    return {"readme": "README.md" if readme is not None else "", "extra": extra, "summary": summary}


def _detect_engines(hay: str, stack: list[str], extra: list[str]) -> list[str]:
    blob = f"{hay} {' '.join(stack)} {' '.join(extra)}".lower()
    engines: list[str] = []
    for engine, tokens in DB_HINTS:
        if any(token in blob for token in tokens) and engine not in engines:
            engines.append(engine)
    return engines


def _database_specs(engines: list[str], compose_files: list[str]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for engine in engines:
        if engine == "postgres":
            specs.append(
                {
                    "engine": "postgres",
                    "required": True,
                    "docker_image": "postgres:16-alpine",
                    "port": 5432,
                    "env_url": "DATABASE_URL",
                    "compose": compose_files[:1],
                    "bootstrap": [
                        "docker compose up -d postgres || docker run -d --name navin-postgres -e POSTGRES_PASSWORD=navin -e POSTGRES_USER=navin -e POSTGRES_DB=app -p 5432:5432 postgres:16-alpine",
                        "Wait for pg_isready, then run launch.migrate and launch.seed.",
                    ],
                }
            )
        elif engine == "mongodb":
            specs.append(
                {
                    "engine": "mongodb",
                    "required": True,
                    "docker_image": "mongo:7",
                    "port": 27017,
                    "env_url": "MONGODB_URI",
                    "compose": compose_files[:1],
                    "bootstrap": [
                        "docker compose up -d mongo || docker run -d --name navin-mongo -p 27017:27017 mongo:7",
                        "Set MONGODB_URI=mongodb://127.0.0.1:27017/app then start the API.",
                    ],
                }
            )
        elif engine == "mysql":
            specs.append(
                {
                    "engine": "mysql",
                    "required": True,
                    "docker_image": "mysql:8",
                    "port": 3306,
                    "env_url": "DATABASE_URL",
                    "compose": compose_files[:1],
                    "bootstrap": [
                        "docker compose up -d mysql || docker run -d --name navin-mysql -e MYSQL_ROOT_PASSWORD=navin -e MYSQL_DATABASE=app -p 3306:3306 mysql:8",
                    ],
                }
            )
        elif engine == "redis":
            specs.append(
                {
                    "engine": "redis",
                    "required": False,
                    "docker_image": "redis:7-alpine",
                    "port": 6379,
                    "env_url": "REDIS_URL",
                    "compose": compose_files[:1],
                    "bootstrap": [
                        "docker compose up -d redis || docker run -d --name navin-redis -p 6379:6379 redis:7-alpine",
                    ],
                }
            )
        elif engine == "supabase":
            specs.append(
                {
                    "engine": "supabase",
                    "required": True,
                    "docker_image": "supabase/postgres",
                    "port": 54321,
                    "env_url": "VITE_SUPABASE_URL",
                    "compose": compose_files[:1],
                    "bootstrap": [
                        "If Makefile has `make start`, use it. Else `npx supabase start` or `npm run dev:demo`.",
                    ],
                }
            )
        elif engine == "sqlite":
            specs.append(
                {
                    "engine": "sqlite",
                    "required": True,
                    "docker_image": "",
                    "port": 0,
                    "env_url": "DATABASE_URL",
                    "compose": [],
                    "bootstrap": ["No Docker. File DB is created on first migrate / start."],
                }
            )
        elif engine == "elasticsearch":
            specs.append(
                {
                    "engine": "elasticsearch",
                    "required": True,
                    "docker_image": "elasticsearch:8.11.3",
                    "port": 9200,
                    "env_url": "ELASTIC_PASSWORD",
                    "compose": compose_files[:2],
                    "bootstrap": [
                        "Start via docker compose profile elasticsearch (do not invent a local sqlite file).",
                    ],
                }
            )
        elif engine == "minio":
            specs.append(
                {
                    "engine": "minio",
                    "required": True,
                    "docker_image": "minio/minio",
                    "port": 9000,
                    "env_url": "MINIO_PASSWORD",
                    "compose": compose_files[:2],
                    "bootstrap": ["Start MinIO via the project docker compose (object store for documents)."],
                }
            )
    return specs


def _tools(*, has_node: bool, has_py: bool, lock: str, engines: list[str], has_make: bool, has_supabase: bool, has_prisma: bool) -> list[dict[str, str]]:
    tools = [{"id": "git", "why": "Clone / update the template source"}]
    if has_node:
        tools.append({"id": lock or "npm", "why": "Install frontend / Node backend packages"})
        tools.append({"id": "node", "why": "Run Vite / Next / Express"})
    if has_py:
        tools.append({"id": "python3", "why": "Python backend / RAG / agents"})
        tools.append({"id": lock if lock in {"poetry", "uv"} else "pip", "why": "Install Python deps"})
    if engines:
        tools.append({"id": "docker", "why": "Run Postgres / Mongo / Redis / Supabase locally"})
    if has_make:
        tools.append({"id": "make", "why": "Project Makefile (start / install / seed)"})
    if has_supabase:
        tools.append({"id": "supabase", "why": "Local Supabase (`npx supabase start`)"})
    if has_prisma:
        tools.append({"id": "prisma", "why": "npx prisma migrate / generate / seed"})
    return tools


def _launch(packages: list[dict[str, Any]], *, has_node: bool, has_py: bool) -> dict[str, str]:
    scripts: dict[str, str] = {}
    for pkg in packages:
        if pkg.get("role") == "root" and pkg.get("scripts"):
            scripts = dict(pkg["scripts"])
            break
    if not scripts:
        for pkg in packages:
            if pkg.get("scripts"):
                scripts = dict(pkg["scripts"])
                break
    launch: dict[str, str] = {}
    for key in ("dev:demo", "demo", "dev", "start", "develop"):
        if key in scripts:
            launch["dev"] = f"npm run {key}"
            break
    for key in ("db:migrate", "migrate", "prisma:migrate", "prisma:migrate:dev"):
        if key in scripts:
            launch["migrate"] = f"npm run {key}"
            break
    for key in ("db:seed", "seed", "prisma:seed"):
        if key in scripts:
            launch["seed"] = f"npm run {key}"
            break
    if "build" in scripts:
        launch["build"] = "npm run build"
    if not launch.get("dev"):
        if has_node:
            launch["dev"] = "npm run dev"
        elif has_py:
            launch["dev"] = "python -m uvicorn app:app --reload || python manage.py runserver"
    return launch


def _install_steps(row: dict[str, Any], *, lock: str, has_node: bool, has_py: bool, env_files: list[str], dbs: list[dict[str, Any]], launch: dict[str, str]) -> list[dict[str, str]]:
    steps = [
        {"id": "read-playbook", "run": f"Read .navin/apps/{row['slug']}/install.json first. Do not guess env or DB."}
    ]
    if env_files:
        steps.append({"id": "env", "run": f"Copy {env_files[0]} to .env and fill every required secret."})
    else:
        steps.append({"id": "env", "run": "Create .env from install.json env[] keys."})
    if has_node:
        install = {"pnpm": "pnpm install", "yarn": "yarn", "bun": "bun install"}.get(lock, "npm install")
        steps.append({"id": "node-deps", "run": f"In each packages[].path folder: {install}."})
    if has_py:
        steps.append({"id": "py-deps", "run": "python -m venv .venv && .venv/bin/pip install -r requirements.txt (or poetry / uv)."})
    for db in dbs:
        if db.get("required"):
            steps.append({"id": f"db-{db['engine']}", "run": " | ".join(db.get("bootstrap") or [])})
    if launch.get("migrate"):
        steps.append({"id": "migrate", "run": launch["migrate"]})
    if launch.get("seed"):
        steps.append({"id": "seed", "run": launch["seed"]})
    steps.append({"id": "overlay", "run": f"Customize .navin/apps/{row['slug']}/overlay.json (agents, prompts, extra env)."})
    steps.append({"id": "dev", "run": launch.get("dev") or "Start launch.dev"})
    return steps


def _infer_env(engines: list[str], has_node: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if "postgres" in engines or "supabase" in engines:
        rows.append(
            {
                "key": "DATABASE_URL",
                "default": "postgres://navin:navin@127.0.0.1:5432/app",
                "required": True,
                "secret": False,
                "purpose": "Postgres connection string",
                "from": "inferred",
            }
        )
    if "mongodb" in engines:
        rows.append(
            {
                "key": "MONGODB_URI",
                "default": "mongodb://127.0.0.1:27017/app",
                "required": True,
                "secret": False,
                "purpose": "MongoDB connection string",
                "from": "inferred",
            }
        )
    if "redis" in engines:
        rows.append(
            {
                "key": "REDIS_URL",
                "default": "redis://127.0.0.1:6379",
                "required": False,
                "secret": False,
                "purpose": "Redis connection string",
                "from": "inferred",
            }
        )
    if has_node:
        rows.append(
            {
                "key": "PORT",
                "default": "3000",
                "required": False,
                "secret": False,
                "purpose": "HTTP port",
                "from": "inferred",
            }
        )
    return rows


def _scan_env_from_source(root: Path) -> list[dict[str, Any]]:
    keys: dict[str, str] = {}
    scanned = 0
    for path in _iter_files(root):
        if path.suffix.lower() not in SOURCE_ENV_EXTS:
            continue
        rel = _rel(root, path)
        if _skipped_rel(rel) or rel.count("/") > 5:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        found = re.findall(r"process\.env\.([A-Z][A-Z0-9_]+)", text)
        found += re.findall(r"import\.meta\.env\.([A-Z][A-Z0-9_]+)", text)
        found += re.findall(r"os\.environ(?:\.get)?\(\s*[\"']([A-Z][A-Z0-9_]+)", text)
        for key in found:
            if _is_noise_env(key):
                continue
            keys.setdefault(key, rel)
        if scanned >= 80 or len(keys) >= 80:
            break
    return [
        {
            "key": key,
            "default": "",
            "required": True,
            "secret": any(tok in key for tok in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE")),
            "purpose": _purpose(key),
            "from": f"source:{src}",
        }
        for key, src in sorted(keys.items())
    ]


def _env_from_readme(root: Path) -> list[dict[str, Any]]:
    readme = None
    for name in ("README.md", "readme.md"):
        if (root / name).is_file():
            readme = root / name
            break
    if readme is None:
        return []
    try:
        text = readme.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence or "=" not in stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("export "):
            if stripped.startswith("export "):
                stripped = stripped[7:].strip()
            else:
                continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", key) or key in seen:
            continue
        seen.add(key)
        secret = any(tok in key for tok in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE"))
        rows.append(
            {
                "key": key,
                "default": value[:180],
                "required": (not value) or secret,
                "secret": secret,
                "purpose": _purpose(key),
                "from": "README.md",
            }
        )
    return rows


def _is_noise_env(key: str) -> bool:
    name = (key or "").strip()
    if not name or name in NOISE_ENV_KEYS:
        return True
    return name.startswith(NOISE_ENV_PREFIXES)


def _merge_env(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.get("key")
            if not key or key in seen or _is_noise_env(str(key)):
                continue
            seen.add(str(key))
            out.append(item)
    return out


def study_slug(slug: str) -> dict[str, Any]:
    row = get_app_template(slug)
    if row is None:
        raise KeyError(slug)
    cache = local_cache_dir(slug)
    env_rows: list[dict[str, Any]] = []
    env_files: list[str] = []
    packages: list[dict[str, Any]] = []
    compose = {"files": [], "services": []}
    docs = {"readme": "", "extra": [], "summary": row["description"]}
    hay = " ".join(row["stack"]) + " " + row["description"]
    has_node = False
    has_py = False
    has_dockerfile = False
    has_make = False
    has_supabase = False
    has_prisma = False
    lock = ""
    node_engine = ""
    if cache.is_dir():
        lock = _lockfile(cache)
        has_make = (cache / "Makefile").is_file() or (cache / "makefile").is_file()
        has_supabase = (cache / "supabase").is_dir()
        has_prisma = bool(_iter_files(cache, names={"schema.prisma"}))
        has_dockerfile = bool(_iter_files(cache, names={"Dockerfile", "dockerfile"}))
        packages = _scan_packages(cache)
        has_node = any(p.get("path", "").endswith("package.json") for p in packages)
        has_py = any(str(p.get("path", "")).endswith((".toml", ".txt", "Pipfile")) for p in packages)
        compose = _compose_bundle(cache)
        docs = _readme_summary(cache)
        for path in _iter_files(cache, names=ENV_FILENAMES):
            rel = _rel(cache, path)
            if rel.count("/") > 3 or _skipped_rel(rel):
                continue
            env_files.append(rel)
            env_rows.extend(_parse_env_file(path, cache))
        env_rows.extend(_env_from_readme(cache))
        env_rows.extend(_scan_env_from_source(cache))
        for pkg in packages:
            if pkg.get("node_engine") and not node_engine:
                node_engine = pkg["node_engine"]
        hay = " ".join(
            [
                hay,
                " ".join(env_files),
                " ".join(e["key"] + " " + str(e.get("default") or "") for e in env_rows),
                " ".join(compose["files"]),
                " ".join(s.get("name", "") + " " + s.get("image", "") for s in compose["services"]),
                " ".join(d for p in packages for d in p.get("deps_hint") or []),
                docs.get("summary") or "",
            ]
        )
    else:
        stack_l = " ".join(row["stack"]).lower()
        has_node = any(token in stack_l for token in ("node", "react", "next.js", "next"))
        has_py = "python" in stack_l
        lock = "npm" if has_node else ""
        if has_node:
            packages = [
                {
                    "role": "root",
                    "path": "package.json",
                    "name": slug,
                    "scripts": {"dev": "dev", "build": "build"},
                    "node_engine": ">=18",
                    "deps_hint": [item for item in row["stack"] if item],
                }
            ]
    extra_engines = _engines_from_packages(packages)
    compose_hay = " ".join(s.get("name", "") + " " + s.get("image", "") for s in compose["services"])
    script_blob = " ".join(
        f"{key} {value}" for pkg in packages for key, value in (pkg.get("scripts") or {}).items()
    )
    if "wrangler" in script_blob.lower() or "d1 execute" in script_blob.lower():
        extra_engines = list(extra_engines)
        if "sqlite" not in extra_engines:
            extra_engines.append("sqlite")
    engines = _detect_engines(hay + " " + compose_hay + " " + script_blob, list(row["stack"]), extra_engines)
    for engine in extra_engines:
        if engine not in engines:
            engines.append(engine)
    if "supabase" in engines and "postgres" not in engines:
        engines.insert(0, "postgres")
    if "sqlite" in engines and any(tok in engines for tok in ("mysql", "postgres", "mongodb", "elasticsearch")):
        engines = [item for item in engines if item != "sqlite"]
    if not engines:
        stack_l = " ".join(row["stack"]).lower()
        if row["kind"] == "business" and has_node:
            engines = ["postgres"]
        else:
            engines = []
    dbs = _database_specs(engines, compose["files"])
    if not dbs:
        dbs = [
            {
                "engine": "none",
                "required": False,
                "docker_image": "",
                "port": 0,
                "env_url": "",
                "compose": [],
                "bootstrap": [
                    "No server database detected. Uses local files or browser storage. Confirm in README before adding Docker.",
                ],
            }
        ]
    launch = _launch(packages, has_node=has_node, has_py=has_py)
    uniq_env = _merge_env(env_rows, _infer_env(engines, has_node))
    ports = sorted(
        {
            int(item["default"])
            for item in uniq_env
            if "PORT" in item["key"].upper() and str(item.get("default") or "").isdigit()
        }
        | {int(db["port"]) for db in dbs if db.get("port")}
    )
    tools = _tools(
        has_node=has_node,
        has_py=has_py,
        lock=lock,
        engines=engines,
        has_make=has_make,
        has_supabase=has_supabase,
        has_prisma=has_prisma,
    )
    front = [p for p in packages if p.get("role") in {"frontend", "fullstack"}]
    back = [p for p in packages if p.get("role") in {"backend", "fullstack"}]
    missing: list[str] = []
    if not uniq_env:
        missing.append("env")
    if not dbs or (len(dbs) == 1 and dbs[0].get("engine") == "none" and row["kind"] == "business"):
        missing.append("databases")
    if not packages:
        missing.append("packages")
    if cache.is_dir() and not docs.get("readme"):
        missing.append("docs")
    payload = {
        "schema": INSTALL_JSON_SCHEMA,
        "slug": slug,
        "name": row["name"],
        "kind": row["kind"],
        "category": row["category"],
        "source_github": row["source_github"],
        "source_name": row["source_name"],
        "license": row["license"],
        "stack": list(row["stack"]),
        "studied_from": f"templates_apps/{slug}" if cache.is_dir() else "catalog-only",
        "completeness": {
            "studied": cache.is_dir(),
            "missing": missing,
            "env_count": len(uniq_env),
            "package_count": len(packages),
        },
        "docs": docs,
        "system": {
            "node": has_node,
            "node_engine": node_engine or (">=18" if has_node else ""),
            "python": has_py,
            "docker": bool(compose["files"]) or has_dockerfile or bool(dbs),
            "package_manager": lock or ("npm" if has_node else ("pip" if has_py else "")),
            "make": has_make,
            "prisma": has_prisma,
            "supabase_cli": has_supabase,
        },
        "tools": tools,
        "packages": {
            "frontend": front,
            "backend": back,
            "all": packages,
        },
        "databases": dbs,
        "ports": ports,
        "env_files": env_files[:12],
        "env": uniq_env[:160],
        "docker": {
            "compose": compose["files"],
            "services": compose["services"],
            "dockerfile": has_dockerfile,
            "note": "If compose is empty, start databases[].docker_image with the bootstrap command.",
        },
        "launch": launch,
        "install": {
            "steps": _install_steps(
                row,
                lock=lock,
                has_node=has_node,
                has_py=has_py,
                env_files=env_files,
                dbs=dbs,
                launch=launch,
            )
        },
        "modify": {
            "overlay": f".navin/apps/{slug}/overlay.json",
            "agents": f".navin/apps/{slug}/agents/",
            "env": ".env",
            "frontend": front[0]["path"] if front else "package.json of role=frontend or root",
            "backend": back[0]["path"] if back else "package.json / pyproject of role=backend",
            "theme": "Use packages/<slug>/patches when present. Do not rewrite the upstream repo.",
            "rule": "overlay.json wins over navin.json. Change prompts, tools and env there first.",
        },
        "agents": {
            "universal": list(row["universal_agents"]),
            "domain": list(row["domain_agents"]),
        },
        "agent_contract": (
            "Before any edit: read this file, copy env, start databases with Docker, "
            "install front and back packages, then change overlay.json. "
            "Do not invent connection strings or skip DB bootstrap."
        ),
    }
    return apply_curated(slug, payload)


def write_all_install_json() -> list[Path]:
    written: list[Path] = []
    for row in APP_TEMPLATES:
        payload = study_slug(row["slug"])
        dest = package_dir(row["slug"]) / "install.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        written.append(dest)
    return written


def main() -> int:
    paths = write_all_install_json()
    missing = 0
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        gaps = payload.get("completeness", {}).get("missing") or []
        if gaps:
            missing += 1
            print(f"{payload['slug']}: missing {', '.join(gaps)}")
    print(f"wrote {len(paths)} install.json playbooks ({missing} still incomplete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
