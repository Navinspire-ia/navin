# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project discovery for the security audit.

Reads manifests, configuration and a bounded sample of the tree to say what
the project is made of: languages, frameworks, databases, auth mechanisms,
API styles, infrastructure, native targets, AI components, and the attack
surfaces that follow. Every later decision (threat model, skills, scanners,
levels reachable) derives from this record, so it only states what it saw.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_MAX_FILES = 4000
_MAX_READ = 200_000
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "target", "__pycache__",
    ".next", ".nuxt", "coverage", ".tox", ".mypy_cache", ".pytest_cache", "vendor",
}

_LANG_BY_EXT = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".php": "php", ".rb": "ruby", ".cs": "csharp", ".c": "c",
    ".cpp": "cpp", ".h": "c", ".sql": "sql", ".dart": "dart",
}

# Package name (npm / pip / cargo) -> (kind, label)
_PACKAGE_SIGNALS: dict[str, tuple[str, str]] = {
    "react": ("frameworks", "react"), "next": ("frameworks", "nextjs"), "vue": ("frameworks", "vue"),
    "nuxt": ("frameworks", "nuxt"), "@angular/core": ("frameworks", "angular"), "svelte": ("frameworks", "svelte"),
    "express": ("frameworks", "express"), "fastify": ("frameworks", "fastify"), "koa": ("frameworks", "koa"),
    "@nestjs/core": ("frameworks", "nestjs"), "hono": ("frameworks", "hono"),
    "fastapi": ("frameworks", "fastapi"), "django": ("frameworks", "django"), "flask": ("frameworks", "flask"),
    "starlette": ("frameworks", "starlette"), "aiohttp": ("frameworks", "aiohttp"), "tornado": ("frameworks", "tornado"),
    "actix-web": ("frameworks", "actix"), "axum": ("frameworks", "axum"), "rocket": ("frameworks", "rocket"),
    "gin": ("frameworks", "gin"), "spring-boot": ("frameworks", "spring"),
    "pg": ("databases", "postgresql"), "postgres": ("databases", "postgresql"), "psycopg2": ("databases", "postgresql"),
    "psycopg": ("databases", "postgresql"), "asyncpg": ("databases", "postgresql"), "sqlx": ("databases", "sql"),
    "mysql2": ("databases", "mysql"), "pymysql": ("databases", "mysql"), "mongoose": ("databases", "mongodb"),
    "pymongo": ("databases", "mongodb"), "mongodb": ("databases", "mongodb"), "redis": ("databases", "redis"),
    "ioredis": ("databases", "redis"), "sqlalchemy": ("databases", "sql"), "prisma": ("databases", "sql"),
    "@prisma/client": ("databases", "sql"), "typeorm": ("databases", "sql"), "sequelize": ("databases", "sql"),
    "knex": ("databases", "sql"), "drizzle-orm": ("databases", "sql"), "sqlite3": ("databases", "sqlite"),
    "better-sqlite3": ("databases", "sqlite"), "@supabase/supabase-js": ("databases", "supabase"),
    "supabase": ("databases", "supabase"), "firebase": ("databases", "firebase"), "firebase-admin": ("databases", "firebase"),
    "jsonwebtoken": ("auth", "jwt"), "jose": ("auth", "jwt"), "pyjwt": ("auth", "jwt"), "python-jose": ("auth", "jwt"),
    "passport": ("auth", "passport"), "next-auth": ("auth", "oauth"), "@auth/core": ("auth", "oauth"),
    "authlib": ("auth", "oauth"), "oauthlib": ("auth", "oauth"), "django-allauth": ("auth", "oauth"),
    "bcrypt": ("auth", "password"), "argon2-cffi": ("auth", "password"), "passlib": ("auth", "password"),
    "express-session": ("auth", "session"), "@clerk/nextjs": ("auth", "clerk"), "keycloak-js": ("auth", "oidc"),
    "graphql": ("api_styles", "graphql"), "apollo-server": ("api_styles", "graphql"), "@apollo/server": ("api_styles", "graphql"),
    "strawberry-graphql": ("api_styles", "graphql"), "graphene": ("api_styles", "graphql"), "ariadne": ("api_styles", "graphql"),
    "ws": ("api_styles", "websocket"), "socket.io": ("api_styles", "websocket"), "websockets": ("api_styles", "websocket"),
    "@grpc/grpc-js": ("api_styles", "grpc"), "grpcio": ("api_styles", "grpc"),
    "stripe": ("payments", "stripe"), "paypal-rest-sdk": ("payments", "paypal"), "braintree": ("payments", "braintree"),
    "openai": ("ai", "llm"), "anthropic": ("ai", "llm"), "langchain": ("ai", "llm"), "@langchain/core": ("ai", "llm"),
    "llamaindex": ("ai", "rag"), "llama-index": ("ai", "rag"), "chromadb": ("ai", "rag"), "pinecone-client": ("ai", "rag"),
    "@pinecone-database/pinecone": ("ai", "rag"), "qdrant-client": ("ai", "rag"), "weaviate-client": ("ai", "rag"),
    "mcp": ("ai", "mcp"), "@modelcontextprotocol/sdk": ("ai", "mcp"), "litellm": ("ai", "llm"), "transformers": ("ai", "llm"),
    "@capacitor/core": ("native", "capacitor"), "react-native": ("native", "react_native"), "expo": ("native", "expo"),
    "electron": ("native", "electron"), "@tauri-apps/api": ("native", "tauri"), "tauri": ("native", "tauri"),
    "multer": ("features", "file_upload"), "formidable": ("features", "file_upload"), "python-multipart": ("features", "file_upload"),
    "pickle": ("features", "deserialization"), "pyyaml": ("features", "yaml"), "celery": ("features", "task_queue"),
    "bullmq": ("features", "task_queue"), "nodemailer": ("features", "email"), "helmet": ("hardening", "helmet"),
    "express-rate-limit": ("hardening", "rate_limit"), "slowapi": ("hardening", "rate_limit"), "cors": ("features", "cors"),
    "fastapi-cors": ("features", "cors"), "django-cors-headers": ("features", "cors"),
}

_FILE_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|/)dockerfile(\.|$)", re.I), "infrastructure", "docker"),
    (re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", re.I), "infrastructure", "compose"),
    (re.compile(r"\.tf$"), "infrastructure", "terraform"),
    (re.compile(r"(^|/)(k8s|kubernetes|manifests|helm|charts)/.*\.ya?ml$", re.I), "infrastructure", "kubernetes"),
    (re.compile(r"(^|/)Chart\.yaml$"), "infrastructure", "helm"),
    (re.compile(r"(^|/)\.github/workflows/.*\.ya?ml$"), "infrastructure", "github_actions"),
    (re.compile(r"(^|/)\.gitlab-ci\.ya?ml$"), "infrastructure", "gitlab_ci"),
    (re.compile(r"(^|/)Jenkinsfile$"), "infrastructure", "jenkins"),
    (re.compile(r"(^|/)serverless\.ya?ml$"), "infrastructure", "serverless"),
    (re.compile(r"(^|/)cdk\.json$"), "infrastructure", "aws_cdk"),
    (re.compile(r"(^|/)src-tauri/tauri\.conf\.json$"), "native", "tauri"),
    (re.compile(r"(^|/)capacitor\.config\.(ts|js|json)$"), "native", "capacitor"),
    (re.compile(r"(^|/)AndroidManifest\.xml$"), "native", "android"),
    (re.compile(r"(^|/)Info\.plist$"), "native", "ios"),
    (re.compile(r"(^|/)pubspec\.yaml$"), "native", "flutter"),
    (re.compile(r"(^|/)openapi\.(ya?ml|json)$|(^|/)swagger\.(ya?ml|json)$", re.I), "api_styles", "openapi"),
    (re.compile(r"\.graphql$|\.gql$"), "api_styles", "graphql"),
    (re.compile(r"\.proto$"), "api_styles", "grpc"),
    (re.compile(r"(^|/)supabase/.*\.sql$"), "databases", "supabase"),
    (re.compile(r"(^|/)(migrations?|alembic)/.*\.(sql|py)$"), "databases", "sql"),
    (re.compile(r"(^|/)\.mcp\.json$"), "ai", "mcp"),
    (re.compile(r"(^|/)\.env(\..*)?$"), "features", "env_files"),
    (re.compile(r"(^|/)SKILL\.md$"), "ai", "agent_skills"),
]

_TEXT_SIGNALS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\btenant_id\b|\borganization_id\b|\bworkspace_id\b|\baccount_id\b", re.I), "features", "multitenant"),
    (re.compile(r"ROW LEVEL SECURITY|CREATE POLICY", re.I), "features", "rls"),
    (re.compile(r"\bprice\b.*\b(total|amount)\b|\bcheckout\b|\binvoice\b", re.I), "features", "payments_flow"),
    (re.compile(r"webhook", re.I), "features", "webhooks"),
    (re.compile(r"\brole\b.*\b(admin|owner|member)\b|\bpermission", re.I), "features", "rbac"),
    (re.compile(r"dangerouslySetInnerHTML|innerHTML\s*=", re.I), "features", "html_sink"),
    (re.compile(r"system_prompt|systemPrompt|tool_call|function_call", re.I), "ai", "agent"),
]

_SAMPLE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".sql", ".java", ".php", ".rb", ".cs"}


@dataclass
class Discovery:
    root: str
    files_seen: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    auth: list[str] = field(default_factory=list)
    api_styles: list[str] = field(default_factory=list)
    payments: list[str] = field(default_factory=list)
    infrastructure: list[str] = field(default_factory=list)
    native: list[str] = field(default_factory=list)
    ai: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    hardening: list[str] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    dependencies: dict[str, int] = field(default_factory=dict)
    entrypoints: list[str] = field(default_factory=list)
    surfaces: list[str] = field(default_factory=list)
    base_url: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def has_web(self) -> bool:
        return bool(self.frameworks) or "web" in self.surfaces

    @property
    def has_api(self) -> bool:
        return "api" in self.surfaces

    def tag(self, kind: str, label: str) -> None:
        bucket: list[str] = getattr(self, kind)
        if label not in bucket:
            bucket.append(label)


_ROUTE_HINTS = re.compile(
    r"@(app|router|api|blueprint)\.(get|post|put|patch|delete|route)\(|"
    r"\b(app|router)\.(get|post|put|patch|delete|use)\(\s*['\"]/|"
    r"export\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b|"
    r"path\(\s*['\"]|urlpatterns|#\[(get|post|put|delete)\("
)


def discover(root: Path, *, base_url: str = "") -> Discovery:
    """Inspect *root* and return what the project is made of."""
    root = Path(root).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    found = Discovery(root=str(root), base_url=base_url.strip())
    sampled_text = 0
    for rel, full in _walk(root):
        found.files_seen += 1
        ext = full.suffix.lower()
        lang = _LANG_BY_EXT.get(ext)
        if lang:
            found.languages[lang] = found.languages.get(lang, 0) + 1
        name = full.name
        if name in {"package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "composer.json", "Gemfile"}:
            found.manifests.append(rel)
            _read_manifest(found, full)
        for pattern, kind, label in _FILE_SIGNALS:
            if pattern.search(rel):
                found.tag(kind, label)
        if ext in _SAMPLE_EXTS and sampled_text < _MAX_READ * 10:
            text = _read(full)
            sampled_text += len(text)
            if _ROUTE_HINTS.search(text):
                found.entrypoints.append(rel)
            for pattern, kind, label in _TEXT_SIGNALS:
                if pattern.search(text):
                    found.tag(kind, label)
    found.languages = dict(sorted(found.languages.items(), key=lambda kv: -kv[1]))
    found.entrypoints = found.entrypoints[:200]
    _derive_surfaces(found)
    return found


def _walk(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and (not d.startswith(".") or d in {".github", ".gitlab"}))
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                rel = str(full.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            yield rel, full
            count += 1
            if count >= _MAX_FILES:
                return


def _read(path: Path, limit: int = _MAX_READ) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_manifest(found: Discovery, path: Path) -> None:
    text = _read(path)
    if not text:
        return
    names: list[str] = []
    if path.name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            found.notes.append(f"{path.name}: invalid JSON ({exc.msg})")
            return
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(key) if isinstance(data, dict) else None
            if isinstance(deps, dict):
                names.extend(str(k).lower() for k in deps)
    elif path.name in {"pyproject.toml", "requirements.txt", "Cargo.toml", "Gemfile", "composer.json", "go.mod", "pom.xml", "build.gradle"}:
        for match in re.finditer(r"^\s*[\"']?([A-Za-z0-9_.@/-]+)[\"']?\s*(?:[=<>!~\[]|$|\s)", text, re.M):
            token = match.group(1).lower()
            if token and not token.startswith(("#", "[", "//")):
                names.append(token)
        for match in re.finditer(r"[\"']([a-z0-9_.-]+)[\"']\s*[:=]", text, re.I):
            names.append(match.group(1).lower())
        # Inline arrays: dependencies = ["fastapi>=0.110", "asyncpg", "redis"].
        for match in re.finditer(r"[\"']([a-z0-9_.-]+)(?:\[[^\]\"']*\])?\s*(?:[<>=!~;@ ][^\"']*)?[\"']", text, re.I):
            names.append(match.group(1).lower())
    for raw in names:
        base = re.split(r"[\[<>=!~ ;]", raw, maxsplit=1)[0].strip()
        if not base:
            continue
        found.dependencies[base] = found.dependencies.get(base, 0) + 1
        signal = _PACKAGE_SIGNALS.get(base)
        if signal:
            found.tag(*signal)


def _derive_surfaces(found: Discovery) -> None:
    web_frameworks = {"react", "nextjs", "vue", "nuxt", "angular", "svelte"}
    api_frameworks = {"express", "fastify", "koa", "nestjs", "hono", "fastapi", "django", "flask", "starlette", "aiohttp", "tornado", "actix", "axum", "rocket", "gin", "spring"}
    if set(found.frameworks) & web_frameworks or "html_sink" in found.features:
        found.tag("surfaces", "web")
    if set(found.frameworks) & api_frameworks or found.entrypoints or set(found.api_styles) & {"openapi", "graphql", "grpc"}:
        found.tag("surfaces", "api")
    if found.auth or "rbac" in found.features:
        found.tag("surfaces", "auth")
    if found.databases:
        found.tag("surfaces", "database")
    if "multitenant" in found.features or "rls" in found.features or "supabase" in found.databases:
        found.tag("surfaces", "multitenant")
    if found.payments or "payments_flow" in found.features:
        found.tag("surfaces", "payments")
    if "webhooks" in found.features or "websocket" in found.api_styles:
        found.tag("surfaces", "workflow")
    if "graphql" in found.api_styles:
        found.tag("surfaces", "graphql")
    if "websocket" in found.api_styles:
        found.tag("surfaces", "websocket")
    if set(found.infrastructure) & {"docker", "compose"}:
        found.tag("surfaces", "docker")
    if set(found.infrastructure) & {"kubernetes", "helm"}:
        found.tag("surfaces", "kubernetes")
    if set(found.infrastructure) & {"terraform", "aws_cdk", "serverless"}:
        found.tag("surfaces", "cloud")
        found.tag("surfaces", "terraform")
    if set(found.native) & {"capacitor", "react_native", "expo", "android", "ios", "flutter"}:
        found.tag("surfaces", "mobile")
    if set(found.native) & {"tauri", "electron"}:
        found.tag("surfaces", "desktop")
    if found.ai:
        found.tag("surfaces", "ai")
    if "mcp" in found.ai:
        found.tag("surfaces", "mcp")
    if "web" in found.surfaces:
        found.tag("surfaces", "frontend")
