# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Read a project before turning it into an app: frontend, backend, server features.

Bounded and read-only: at most a few thousand source files, never ``node_modules``
or build output, and only the NAMES of environment variables (never values).
"""

from __future__ import annotations

import json
import os
import re
import struct
from pathlib import Path
from typing import Any

from navin.appfactory.targets import TARGETS

_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".hg", ".next", ".nuxt", ".output", ".svelte-kit", ".turbo", ".vercel",
    ".expo", "dist", "build", "out", "coverage", "target", "__pycache__", ".venv", "venv", ".navin",
    "os", "android", "ios", "src-tauri", "vendor", ".cache", ".parcel-cache", "storybook-static",
})
_FRONTEND_DIRS = ("", "frontend", "web", "client", "app", "apps/web", "apps/frontend", "apps/client",
                  "packages/web", "packages/frontend", "packages/app", "ui", "site", "webapp")
# Folders a shallow search never enters when the frontend sits under another name
# (webui, front, dashboard, site/front...).
_SKIP_DIRS = frozenset({"node_modules", ".git", "os", ".navin", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit",
                        "target", "vendor", "venv", ".venv", "__pycache__", "coverage", "android", "ios", "src-tauri",
                        "desktop", "desktop-electron", "public", "static", "assets", "docs", "tests", "test", "scripts"})
_SEARCH_DEPTH = 2
_BACKEND_DIRS = ("", "backend", "server", "api", "apps/api", "apps/backend", "apps/server",
                 "services/api", "packages/api", "packages/server")
_SOURCE_EXT = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".astro"})
_MAX_FILES = 6000
_MAX_BYTES = 300_000
_MAX_SAMPLES = 25

# Detection order matters: meta-frameworks before the libraries they embed.
_FRAMEWORKS = (
    ("next", ("next",)),
    ("nuxt", ("nuxt", "nuxt3")),
    ("sveltekit", ("@sveltejs/kit",)),
    ("remix", ("@remix-run/react", "@remix-run/dev", "@react-router/dev")),
    ("astro", ("astro",)),
    ("angular", ("@angular/core",)),
    ("cra", ("react-scripts",)),
    ("vue", ("vue",)),
    ("solid", ("solid-js",)),
    ("svelte", ("svelte",)),
    ("react", ("react",)),
    ("vite", ("vite",)),
)
_SERVER_AUTH = ("next-auth", "@auth/nextjs", "@auth/core", "@clerk/nextjs", "@supabase/ssr", "better-auth",
                "lucia", "iron-session", "@kinde-oss/kinde-auth-nextjs", "@workos-inc/authkit-nextjs", "@nuxt/auth",
                "@sidebase/nuxt-auth", "@auth/sveltekit")
_CLIENT_AUTH = ("firebase", "@supabase/supabase-js", "@auth0/auth0-react", "@auth0/auth0-spa-js", "@clerk/clerk-react",
                "oidc-client-ts", "keycloak-js", "@azure/msal-browser", "aws-amplify", "@okta/okta-auth-js")
_NODE_BACKENDS = {"@nestjs/core": "nestjs", "express": "express", "fastify": "fastify", "hono": "hono", "koa": "koa",
                  "@hapi/hapi": "hapi", "elysia": "elysia", "@adonisjs/core": "adonis"}
_PY_BACKENDS = ("fastapi", "django", "flask", "starlette", "litestar", "sanic", "aiohttp")

_RE_USE_SERVER = re.compile(r"""^\s*['"]use server['"]""", re.M)
_RE_RELATIVE_API = re.compile(
    r"""(?:fetch|axios(?:\.(?:get|post|put|patch|delete|request))?|\$fetch|useFetch|ky(?:\.(?:get|post|put|patch|delete))?)\(\s*[`'"]/(?:api|graphql|trpc|auth)\b"""
    r"""|baseURL\s*:\s*[`'"]/(?:api|graphql)?""",
)
_RE_ENV_CODE = re.compile(r"(?:process\.env|import\.meta\.env)\.((?:NEXT_PUBLIC_|VITE_|REACT_APP_|NUXT_PUBLIC_|PUBLIC_)?[A-Z][A-Z0-9_]*)")
_RE_ENV_NAME = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", re.M)
_URL_HINT = re.compile(r"(?:^|_)(?:URL|URI|API|ENDPOINT|BACKEND|SERVER|HOST|ORIGIN)(?:_|$)")
# Names that hold secrets are never reported, even with API in them (API_KEY).
_SECRET_HINT = re.compile(r"(?:^|_)(?:KEY|SECRET|TOKEN|PASSWORD|PASS|PWD|PRIVATE|CREDENTIALS?)(?:_|$)")
_RE_VITE_OUT = re.compile(
    r"""outDir\s*:\s*(?:path\.|node:path\.)?(?:resolve|join)?\(?\s*(?:__dirname\s*,\s*|import\.meta\.dirname\s*,\s*)?['"]([^'"]+)['"]"""
)
_RE_VITE_BASE = re.compile(r"""\bbase\s*:\s*['"]([^'"]*)['"]""")
_RE_NEXT_OUTPUT = re.compile(r"""output\s*:\s*['"](export|standalone)['"]""")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _deps(pkg: dict[str, Any]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            merged.update({str(name): str(version) for name, version in section.items()})
    return merged


def _version(spec: str) -> str:
    found = re.search(r"\d+(?:\.\d+){0,2}", spec or "")
    return found.group(0) if found else (spec or "")


def _package_manager(root: Path, pkg_dir: Path, pkg: dict[str, Any]) -> str:
    declared = str(pkg.get("packageManager") or "").split("@", 1)[0]
    if declared in {"npm", "pnpm", "yarn", "bun"}:
        return declared
    for folder in (pkg_dir, root):
        if (folder / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (folder / "yarn.lock").is_file():
            return "yarn"
        if (folder / "bun.lockb").is_file() or (folder / "bun.lock").is_file():
            return "bun"
        if (folder / "package-lock.json").is_file():
            return "npm"
    return "npm"


def _framework(deps: dict[str, str]) -> tuple[str, str]:
    for name, packages in _FRAMEWORKS:
        for package in packages:
            if package in deps:
                return name, _version(deps[package])
    return "", ""


def _walk(base: Path, limit: int = _MAX_FILES):
    """Source files under *base*, skipping dependency and build folders."""
    count = 0
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix in _SOURCE_EXT:
                count += 1
                if count > limit:
                    return
                yield path


def _png_size(path: Path) -> tuple[int, int]:
    try:
        with path.open("rb") as handle:
            head = handle.read(24)
    except OSError:
        return 0, 0
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def _icon(root: Path, pkg_dir: Path) -> dict[str, Any] | None:
    best: tuple[int, Path, tuple[int, int]] | None = None
    names = ("icon-1024.png", "icon-512.png", "icon.png", "logo.png", "logo512.png", "android-chrome-512x512.png",
             "apple-touch-icon.png", "app-icon.png")
    for folder in (pkg_dir / "public", pkg_dir / "static", pkg_dir / "assets", pkg_dir / "src" / "assets",
                   pkg_dir / "app", pkg_dir / "src" / "app", root / "assets", root / "public"):
        for name in names:
            path = folder / name
            if not path.is_file():
                continue
            width, height = _png_size(path)
            if width and width == height and (best is None or width > best[0]):
                best = (width, path, (width, height))
    if best is None:
        return None
    return {"path": best[1].relative_to(root).as_posix(), "width": best[2][0], "height": best[2][1]}


def _angular_output(pkg_dir: Path) -> str:
    data = _read_json(pkg_dir / "angular.json")
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    if not projects:
        return "dist"
    name = data.get("defaultProject") if data.get("defaultProject") in projects else next(iter(projects))
    build = (((projects.get(name) or {}).get("architect") or {}).get("build") or {})
    output = (build.get("options") or {}).get("outputPath") or f"dist/{name}"
    if isinstance(output, dict):
        return f"{output.get('base') or f'dist/{name}'}/{output.get('browser', 'browser')}".rstrip("/")
    builder = str(build.get("builder") or "")
    return f"{output}/browser" if builder.endswith(":application") else str(output)


def _output_dir(framework: str, pkg_dir: Path) -> str:
    if framework == "next":
        return "out"
    if framework == "nuxt":
        return ".output/public"
    if framework in {"sveltekit", "cra"}:
        return "build"
    if framework == "remix":
        return "build/client"
    if framework == "angular":
        return _angular_output(pkg_dir)
    for name in ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs", "astro.config.mjs"):
        found = _RE_VITE_OUT.search(_read_text(pkg_dir / name))
        if found:
            # ``outDir: "dist"`` or ``outDir: path.resolve(__dirname, "../navin/web/dist")``:
            # kept relative to the frontend folder, which is what build-web.mjs resolves against.
            raw = found.group(1).strip()
            if raw.startswith("./"):
                raw = raw[2:]
            return raw.rstrip("/") or "dist"
    return "dist"


def _vite_base(pkg_dir: Path) -> str:
    for name in ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs"):
        found = _RE_VITE_BASE.search(_read_text(pkg_dir / name))
        if found:
            return found.group(1)
    return ""


def _add(features: dict[str, list[dict[str, Any]]], kind: str, path: Path, root: Path, line: int = 0) -> None:
    bucket = features.setdefault(kind, [])
    if len(bucket) < _MAX_SAMPLES:
        bucket.append({"path": path.relative_to(root).as_posix(), **({"line": line} if line else {})})
    else:
        bucket[-1]["more"] = int(bucket[-1].get("more", 0)) + 1


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _scan_sources(root: Path, pkg_dir: Path, framework: str) -> tuple[dict[str, list[dict[str, Any]]], set[str], dict[str, Any]]:
    features: dict[str, list[dict[str, Any]]] = {}
    env_names: set[str] = set()
    notes: dict[str, Any] = {"next_image": False, "files_scanned": 0}
    for path in _walk(pkg_dir):
        notes["files_scanned"] += 1
        rel = path.relative_to(pkg_dir).as_posix()
        parts = rel.split("/")
        stem = path.stem
        text = _read_text(path)
        if framework == "next":
            in_app = parts[0] == "app" or parts[:2] == ["src", "app"]
            in_pages = parts[0] == "pages" or parts[:2] == ["src", "pages"]
            if in_app and stem == "route":
                _add(features, "route_handler", path, root)
            if in_pages and ("api" in parts[:3]):
                _add(features, "api_route", path, root)
            if stem in {"middleware", "proxy"} and (len(parts) == 1 or parts[:1] == ["src"] and len(parts) == 2):
                _add(features, "middleware", path, root)
            if in_app and stem == "page" and any(part.startswith("[") for part in parts) and "generateStaticParams" not in text:
                _add(features, "dynamic_route", path, root)
            if "getServerSideProps" in text:
                _add(features, "ssr", path, root, _line_of(text, text.index("getServerSideProps")))
            match = re.search(r"""from\s+['"]next/headers['"]|export\s+const\s+dynamic\s*=\s*['"]force-dynamic['"]""", text)
            if match:
                _add(features, "ssr", path, root, _line_of(text, match.start()))
            if re.search(r"""export\s+const\s+revalidate\s*=""", text):
                _add(features, "isr", path, root)
            if "next/image" in text:
                notes["next_image"] = True
        if framework == "nuxt" and parts[0] == "server" and len(parts) > 1 and parts[1] in {"api", "routes", "middleware"}:
            _add(features, "route_handler", path, root)
        if framework == "sveltekit":
            if re.match(r"\+(page|layout)\.server$", stem):
                _add(features, "ssr", path, root)
            elif stem == "+server":
                _add(features, "route_handler", path, root)
            elif stem == "hooks.server":
                _add(features, "middleware", path, root)
        if framework == "remix" and re.search(r"export\s+(?:async\s+)?(?:function|const)\s+(?:loader|action)\b", text):
            _add(features, "ssr", path, root)
        match = _RE_USE_SERVER.search(text)
        if match:
            _add(features, "server_action", path, root, _line_of(text, match.start()))
        match = _RE_RELATIVE_API.search(text)
        if match:
            _add(features, "relative_api_call", path, root, _line_of(text, match.start()))
        env_names.update(name for name in _RE_ENV_CODE.findall(text) if _is_api_env(name))
    return features, env_names, notes


def _is_api_env(name: str) -> bool:
    return bool(_URL_HINT.search(name)) and not _SECRET_HINT.search(name)


def _env_file_names(*folders: Path) -> set[str]:
    names: set[str] = set()
    for folder in folders:
        for name in (".env.example", ".env.sample", ".env.local.example", ".env.template", ".env", ".env.local",
                     ".env.development", ".env.production"):
            text = _read_text(folder / name)
            names.update(found for found in _RE_ENV_NAME.findall(text) if _is_api_env(found))
    return names


def _backends(root: Path, frontend_dir: Path | None, frontend_framework: str, features: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(framework: str, folder: Path) -> None:
        rel = folder.relative_to(root).as_posix() if folder != root else "."
        if (framework, rel) not in seen:
            seen.add((framework, rel))
            found.append({"framework": framework, "dir": rel})

    for sub in _BACKEND_DIRS:
        folder = root / sub if sub else root
        if not folder.is_dir():
            continue
        py_text = " ".join(_read_text(folder / name).lower() for name in ("requirements.txt", "requirements-prod.txt", "pyproject.toml", "Pipfile"))
        for framework in _PY_BACKENDS:
            if re.search(rf"(^|[\s\"'\[,]){framework}\b", py_text):
                add(framework, folder)
        if (folder / "manage.py").is_file():
            add("django", folder)
        pkg_path = folder / "package.json"
        if pkg_path.is_file() and (frontend_dir is None or folder != frontend_dir or frontend_framework not in {"next", "nuxt", "sveltekit", "remix"}):
            deps = _deps(_read_json(pkg_path))
            for package, framework in _NODE_BACKENDS.items():
                if package in deps:
                    add(framework, folder)
        go_mod = _read_text(folder / "go.mod")
        if go_mod:
            add("go", folder)
        if "rails" in _read_text(folder / "Gemfile"):
            add("rails", folder)
        if "laravel/framework" in _read_text(folder / "composer.json"):
            add("laravel", folder)
        if "spring-boot" in _read_text(folder / "pom.xml") + _read_text(folder / "build.gradle") + _read_text(folder / "build.gradle.kts"):
            add("spring-boot", folder)
    if frontend_dir is not None and (features.get("route_handler") or features.get("api_route") or features.get("server_action")):
        add(f"{frontend_framework or 'web'}-server", frontend_dir or root)
    if (root / "supabase").is_dir():
        add("supabase", root / "supabase")
    if (root / "firebase.json").is_file():
        add("firebase", root)
    return found


def _existing(root: Path, pkg_dir: Path, deps: dict[str, str]) -> dict[str, Any]:
    folders = [target for target in TARGETS if (root / "os" / target).is_dir()]
    configs = [p.relative_to(root).as_posix() for folder in {root, pkg_dir}
               for p in (folder / "capacitor.config.ts", folder / "capacitor.config.js", folder / "capacitor.config.json")
               if p.is_file()]
    return {
        "os_folders": folders,
        "capacitor_config": configs,
        "src_tauri": [p.relative_to(root).as_posix() for p in {root / "src-tauri", pkg_dir / "src-tauri"} if p.is_dir()],
        "android_dir": (root / "android").is_dir(),
        "ios_dir": (root / "ios").is_dir(),
        "electron": "electron" in deps,
        "capacitor_deps": sorted(name for name in deps if name.startswith("@capacitor/")),
        "tauri_deps": sorted(name for name in deps if name.startswith("@tauri-apps/")),
        "react_native": "react-native" in deps or "expo" in deps,
    }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "app"


def _frontend_candidates(root: Path) -> list[Path]:
    """The usual frontend folders first, then every folder (two levels deep) with a package.json.

    Navin's own frontend lives in ``webui/``, a client's in ``front/`` or
    ``site/front/``: a fixed list of names would miss them and the factory
    would answer "no web frontend" to a project that plainly has one.
    """
    ordered: list[Path] = []
    seen: set[Path] = set()

    def add(folder: Path) -> None:
        if folder not in seen:
            seen.add(folder)
            ordered.append(folder)

    for sub in _FRONTEND_DIRS:
        add(root / sub if sub else root)
    pending = [(root, 0)]
    while pending:
        folder, depth = pending.pop(0)
        try:
            entries = sorted(
                (entry for entry in os.scandir(folder) if entry.is_dir(follow_symlinks=False)),
                key=lambda entry: entry.name,
            )
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            child = Path(entry.path)
            if (child / "package.json").is_file():
                add(child)
            if depth + 1 < _SEARCH_DEPTH:
                pending.append((child, depth + 1))
    return ordered


def analyze(project_root: str | Path) -> dict[str, Any]:
    """Describe the project the way the conversion needs it. Read-only."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project folder not found: {root}")
    pkg_dir: Path | None = None
    pkg: dict[str, Any] = {}
    framework = version = ""
    for folder in _frontend_candidates(root):
        data = _read_json(folder / "package.json")
        if not data:
            continue
        name, ver = _framework(_deps(data))
        if name:
            pkg_dir, pkg, framework, version = folder, data, name, ver
            break
        if pkg_dir is None:
            pkg_dir, pkg = folder, data
    deps = _deps(pkg)
    warnings: list[str] = []
    frontend: dict[str, Any] | None = None
    features: dict[str, list[dict[str, Any]]] = {}
    env_names: set[str] = set()
    notes: dict[str, Any] = {}
    if pkg_dir is not None:
        features, env_names, notes = _scan_sources(root, pkg_dir, framework)
        scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
        build_script = "build" if "build" in scripts else ("generate" if "generate" in scripts else "")
        if framework == "nuxt" and "generate" in scripts:
            build_script = "generate"
        next_output = ""
        if framework == "next":
            for name in ("next.config.ts", "next.config.mjs", "next.config.js", "next.config.cjs"):
                found = _RE_NEXT_OUTPUT.search(_read_text(pkg_dir / name))
                if found:
                    next_output = found.group(1)
                    break
        router = ""
        if framework == "next":
            has_app = (pkg_dir / "app").is_dir() or (pkg_dir / "src" / "app").is_dir()
            has_pages = (pkg_dir / "pages").is_dir() or (pkg_dir / "src" / "pages").is_dir()
            router = "both" if has_app and has_pages else ("app" if has_app else ("pages" if has_pages else ""))
        frontend = {
            "dir": pkg_dir.relative_to(root).as_posix() if pkg_dir != root else ".",
            "name": str(pkg.get("name") or root.name),
            "version": str(pkg.get("version") or "0.1.0"),
            "framework": framework or "unknown",
            "framework_version": version,
            "router": router,
            "package_manager": _package_manager(root, pkg_dir, pkg),
            "scripts": {key: str(value) for key, value in scripts.items() if key in {"build", "dev", "start", "generate", "export", "preview", "test", "lint"}},
            "build_script": build_script,
            "output_dir": _output_dir(framework, pkg_dir),
            "vite_base": _vite_base(pkg_dir),
            "next_output": next_output,
            "typescript": (pkg_dir / "tsconfig.json").is_file(),
            "next_image": bool(notes.get("next_image")),
            "files_scanned": int(notes.get("files_scanned", 0)),
        }
        if not build_script:
            warnings.append("the frontend has no build script: the native shell needs static files to bundle")
        if not framework:
            warnings.append("no web framework recognised in package.json")
    else:
        warnings.append("no package.json with a web frontend was found: the app factory converts web frontends")
    env_names |= _env_file_names(root, *( [pkg_dir] if pkg_dir else []))
    auth_server = sorted(name for name in _SERVER_AUTH if name in deps)
    auth_client = sorted(name for name in _CLIENT_AUTH if name in deps)
    display = re.sub(r"[-_]+", " ", (frontend or {}).get("name", root.name).split("/")[-1]).strip().title() or "App"
    slug = _slug((frontend or {}).get("name", root.name).split("/")[-1])
    return {
        "root": str(root),
        "app": {"name": display, "slug": slug},
        "frontend": frontend,
        "server_features": features,
        "server_feature_counts": {kind: len(items) + sum(int(item.get("more", 0)) for item in items) for kind, items in features.items()},
        "auth": {"server": auth_server, "client": auth_client},
        "backend": _backends(root, pkg_dir, framework, features),
        "api_env": sorted(env_names)[:20],
        "icon": _icon(root, pkg_dir or root),
        "existing": _existing(root, pkg_dir or root, deps),
        "warnings": warnings,
    }
