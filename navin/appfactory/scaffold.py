# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Write the ``os/<target>`` app folder from the templates, and audit it.

Non-destructive by construction: every path is resolved and must stay inside
``os/<target>``; an existing file is kept, never overwritten. The templates and
policies live in ``navin/skills/app-factory`` next to the skill that drives them.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from navin.appfactory.compat import ORIGINS, pm_exec
from navin.appfactory.targets import APP_ID_RE, Target, default_app_id, get_target

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "app-factory"
TEMPLATES = SKILL_DIR / "templates"
POLICIES = SKILL_DIR / "policies"
_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
_HEALTH_PATHS = ("/api/health", "/health", "/healthz", "/api/status", "/status")


def load_policy(name: str) -> dict[str, Any]:
    data = yaml.safe_load((POLICIES / f"{name}.yaml").read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def render(text: str, values: dict[str, str]) -> str:
    """Fill ``{{KEY}}`` placeholders; a placeholder without a value is an error."""
    missing = sorted({key for key in _PLACEHOLDER.findall(text) if key not in values})
    if missing:
        raise KeyError(f"template placeholders without a value: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _png(size: int, rgb: tuple[int, int, int]) -> bytes:
    """A square PNG with a rounded tile of *rgb* (placeholder icon, no image library)."""
    radius = size // 5
    rows = []
    transparent = b"\x00\x00\x00\x00"
    colour = bytes(rgb) + b"\xff"
    for y in range(size):
        dy = max(radius - y, y - (size - 1 - radius), 0)
        if dy == 0:
            rows.append(b"\x00" + colour * size)
            continue
        inset = radius - int((radius * radius - dy * dy) ** 0.5)
        rows.append(b"\x00" + transparent * inset + colour * (size - 2 * inset) + transparent * inset)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def _colour(slug: str) -> tuple[int, int, int]:
    palette = ((37, 99, 235), (13, 148, 136), (124, 58, 237), (220, 38, 38), (234, 88, 12), (22, 163, 74))
    return palette[zlib.crc32(slug.encode()) % len(palette)]


def _aliases(root: Path, frontend_dir: Path, folder: Path) -> dict[str, str]:
    """The site's tsconfig path aliases, re-pointed from os/<target>/web."""
    try:
        data = json.loads(re.sub(r"(?m)^\s*//.*$", "", (frontend_dir / "tsconfig.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        data = {}
    options = data.get("compilerOptions") if isinstance(data, dict) else None
    paths = (options or {}).get("paths") or {}
    base = frontend_dir / str((options or {}).get("baseUrl") or ".")
    aliases: dict[str, str] = {}
    for key, targets in paths.items():
        if not key.endswith("/*") or not targets:
            continue
        target = str(targets[0])
        if not target.endswith("/*"):
            continue
        source = (base / target[:-2]).resolve()
        aliases[key[:-2]] = os.path.relpath(source, folder).replace(os.sep, "/")
    if not aliases:
        src = frontend_dir / "src"
        aliases["@"] = os.path.relpath(src if src.is_dir() else frontend_dir, folder).replace(os.sep, "/")
    return aliases


def _health_path(analysis: dict[str, Any]) -> str:
    for item in analysis.get("server_features", {}).get("route_handler", []) + analysis.get("server_features", {}).get("api_route", []):
        path = item.get("path", "")
        for candidate in _HEALTH_PATHS:
            if candidate.strip("/").split("/")[-1] in path.split("/"):
                return candidate
    return "/api/health"


class Writer:
    """Writes files under one folder only, keeping existing files."""

    def __init__(self, folder: Path, dry_run: bool = False):
        self.folder = folder.resolve()
        self.dry_run = dry_run
        self.created: list[str] = []
        self.kept: list[str] = []

    def path(self, rel: str) -> Path:
        path = (self.folder / rel).resolve()
        if path != self.folder and self.folder not in path.parents:
            raise ValueError(f"refusing to write outside {self.folder}: {rel}")
        return path

    def write(self, rel: str, content: str | bytes) -> None:
        path = self.path(rel)
        if path.exists():
            self.kept.append(rel)
            return
        self.created.append(rel)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="\n")


def _template_files(*groups: str) -> list[tuple[str, Path]]:
    out = []
    for group in groups:
        base = TEMPLATES / group
        for path in sorted(base.rglob("*.tmpl")):
            rel = path.relative_to(base).as_posix()[: -len(".tmpl")]
            out.append((".gitignore" if rel == "gitignore" else rel, path))
    return out


def _api_env_line(api_env: list[str]) -> str:
    return f"{api_env[0]}: ${{{{ vars.BACKEND_URL }}}}" if api_env else "BACKEND_URL: ${{ vars.BACKEND_URL }}"


def _ci_values(target: Target) -> dict[str, str]:
    linux_deps = ("      - run: sudo apt-get update && sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential curl "
                  "wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev")
    if target.id == "android":
        return {
            "CI_PLATFORM_SETUP": "      - uses: actions/setup-java@v4\n        with:\n          distribution: temurin\n          java-version: 21\n      - uses: android-actions/setup-android@v3",
            "CI_BUILD": "      - run: ./gradlew assembleDebug\n        working-directory: " + target.folder + "/android",
            "CI_ARTIFACTS": target.folder + "/android/app/build/outputs/apk/debug/*.apk",
        }
    if target.id == "ios":
        return {
            "CI_PLATFORM_SETUP": "      - run: xcodebuild -version",
            "CI_BUILD": "      - run: xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build",
            "CI_ARTIFACTS": target.folder + "/ios/App/build",
        }
    if target.id == "centos":
        linux_deps += " rpm"
    return {
        "CI_PLATFORM_SETUP": linux_deps if target.id in {"ubuntu", "centos", "appimage"} else "      - run: rustc --version",
        "CI_BUILD": "",
        "CI_ARTIFACTS": target.folder + "/src-tauri/target/release/bundle/",
    }


def _csp(backend_url: str) -> str:
    connect = ["'self'", "ipc:", "http://ipc.localhost"]
    if backend_url:
        parsed = urlparse(backend_url)
        if parsed.scheme and parsed.netloc:
            connect.append(f"{parsed.scheme}://{parsed.netloc}")
            connect.append(f"{'wss' if parsed.scheme == 'https' else 'ws'}://{parsed.netloc}")
    return ("default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
            "style-src 'self' 'unsafe-inline'; font-src 'self' data:; connect-src " + " ".join(connect))


def scaffold(project_root: str | Path, target_name: str, analysis: dict[str, Any], plan: dict[str, Any], *,
             app_name: str = "", app_id: str = "", backend_url: str = "", dry_run: bool = False) -> dict[str, Any]:
    """Create ``os/<target>``; returns created and kept files plus the audit."""
    target = get_target(target_name)
    root = Path(project_root).expanduser().resolve()
    frontend = analysis.get("frontend")
    if not frontend:
        raise ValueError("no web frontend was found: run analyze first and check the project folder")
    if plan.get("strategy") == "unsupported":
        raise ValueError("this project has no web frontend to package")
    slug = (analysis.get("app") or {}).get("slug") or "app"
    name = (app_name or (analysis.get("app") or {}).get("name") or slug).strip()
    app_id = (app_id or default_app_id(slug, target)).strip()
    if not APP_ID_RE.match(app_id):
        raise ValueError(f"app id must look like com.company.app (letters, digits, dots): {app_id!r}")
    if target.engine == "tauri" and app_id.lower().endswith(".app"):
        raise ValueError("a desktop app id must not end with .app (it clashes with the macOS bundle extension)")
    backend_url = (backend_url or plan.get("backend_url") or "").strip().rstrip("/")
    if backend_url:
        parsed = urlparse(backend_url)
        allowed = set(load_policy("security").get("backend", {}).get("http_hosts_allowed") or [])
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError(f"backend address must be a full URL such as https://api.example.com: {backend_url!r}")
        if parsed.scheme == "http" and parsed.hostname not in allowed:
            raise ValueError("the backend must be reached over HTTPS (plain HTTP is only for localhost)")
    folder = root / "os" / target.id
    writer = Writer(folder, dry_run=dry_run)
    frontend_dir = (root / frontend.get("dir", ".")).resolve()
    pm = frontend.get("package_manager") or "npm"
    strategy = plan.get("strategy", "static")
    api_env = list(analysis.get("api_env") or [])
    version = re.sub(r"[^0-9.]", "", str(frontend.get("version") or "0.1.0")) or "0.1.0"
    if version.count(".") < 2:
        version = (version + ".0.0")[: len(version) + 2 * (2 - version.count("."))]
    crate = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_") or "app"
    if crate[0].isdigit():
        crate = "app_" + crate
    values = {
        "APP_NAME": name, "APP_SLUG": slug, "APP_ID": app_id, "TARGET": target.id, "TARGET_LABEL": target.label,
        "FOLDER": target.folder, "ENGINE": "Capacitor 8" if target.engine == "capacitor" else "Tauri 2 + Rust",
        "STRATEGY": strategy, "STRATEGY_NOTE": "; ".join(plan.get("reasons") or []) or strategy,
        "FRONTEND_DIR": os.path.relpath(frontend_dir, folder).replace(os.sep, "/"),
        "BACKEND_URL": backend_url or "not set yet (appfactory.json backend_url)",
        "DELIVERABLES": ", ".join("." + ext for ext in target.deliverables), "CI_RUNNER": target.ci_runner,
        "PM": pm, "PM_EXEC": pm_exec(pm), "PLATFORM": target.platform, "BUNDLES": ",".join(target.bundles),
        "CRATE": crate, "VERSION": version, "API_ENV_LINE": _api_env_line(api_env),
        "HEALTH_PATH": _health_path(analysis),
        "BUILD_COMMANDS": "\n".join(f"(cd {step['cwd']}) {step['command']}" for step in plan.get("steps", []) if not step.get("manual")),
        "TS_PATHS": "{}",
    }
    values.update(_ci_values(target))
    config = {
        "target": target.id, "engine": target.engine, "strategy": strategy, "app_name": name, "app_id": app_id,
        "frontend_dir": values["FRONTEND_DIR"], "framework": frontend.get("framework"), "package_manager": pm,
        "web_build_command": (plan.get("web_build") or {}).get("command") or f"{pm} run build",
        "output_dir": frontend.get("output_dir") or "dist", "vite_base": frontend.get("vite_base") or "",
        "backend_url": backend_url, "api_env": api_env, "origins": ORIGINS[target.id],
        "project_root": os.path.relpath(root, folder).replace(os.sep, "/"),
    }
    groups = ["common", target.engine]
    if strategy == "client-shell":
        groups.append("client-shell")
        config["aliases"] = _aliases(root, frontend_dir, folder)
        values["TS_PATHS"] = json.dumps({f"{key}/*": [f"../{value}/*"] for key, value in config["aliases"].items()})
    for rel, path in _template_files(*groups):
        writer.write(rel, render(path.read_text(encoding="utf-8"), values))
    writer.write("appfactory.json", json.dumps(config, indent=2) + "\n")
    package = {
        "name": f"{slug}-{target.id}", "version": version, "private": True, "type": "module",
        "scripts": {"build:web": "node scripts/build-web.mjs"},
    }
    if target.engine == "capacitor":
        package["scripts"].update({"sync": f"cap sync {target.platform}", "open": f"cap open {target.platform}"})
        package["dependencies"] = {"@capacitor/core": "^8.0.0", f"@capacitor/{target.platform}": "^8.0.0", "@capacitor/app": "^8.0.0"}
        package["devDependencies"] = {"@capacitor/cli": "^8.0.0", "@capacitor/assets": "^3.0.5"}
        writer.write("capacitor.config.json", json.dumps({
            "appId": app_id, "appName": name, "webDir": "www",
            "server": {"androidScheme": "https"},
            "android": {"allowMixedContent": False, "captureInput": True},
            "ios": {"contentInset": "automatic"},
            "plugins": {"SplashScreen": {"launchShowDuration": 0}},
        }, indent=2) + "\n")
    else:
        package["scripts"].update({
            "tauri": "tauri",
            "build": f"node scripts/build-web.mjs && tauri build --bundles {','.join(target.bundles)}",
            "build:debug": f"node scripts/build-web.mjs && tauri build --debug --bundles {','.join(target.bundles)}",
        })
        # @tauri-apps/cli 2.x; the Rust side pins tauri 2 in Cargo.toml.tmpl.
        package["devDependencies"] = {"@tauri-apps/cli": "^2.0.0"}
        windows: dict[str, Any] = {"title": name, "width": 1280, "height": 800, "minWidth": 360, "minHeight": 560, "resizable": True}
        conf: dict[str, Any] = {
            "$schema": "https://schema.tauri.app/config/2",
            "productName": name, "version": version, "identifier": app_id,
            "build": {"frontendDist": "../www"},
            "app": {"windows": [{"label": "main", **windows}], "security": {"csp": _csp(backend_url)}},
            "bundle": {
                "active": True, "targets": list(target.bundles),
                "icon": ["icons/32x32.png", "icons/128x128.png", "icons/128x128@2x.png", "icons/icon.icns", "icons/icon.ico"],
                "windows": {"webviewInstallMode": {"type": "downloadBootstrapper"}},
            },
        }
        writer.write("src-tauri/tauri.conf.json", json.dumps(conf, indent=2) + "\n")
        writer.write("src-tauri/capabilities/default.json", json.dumps({
            "$schema": "../gen/schemas/desktop-schema.json", "identifier": "default",
            "description": "Smallest set of permissions for the main window", "windows": ["main"],
            "permissions": ["core:default"],
        }, indent=2) + "\n")
    writer.write("package.json", json.dumps(package, indent=2) + "\n")
    icon = analysis.get("icon") or {}
    icon_rel = "assets/logo.png" if target.engine == "capacitor" else "app-icon.png"
    source_icon = root / icon["path"] if icon.get("path") and int(icon.get("width") or 0) >= 512 else None
    writer.write(icon_rel, source_icon.read_bytes() if source_icon and source_icon.is_file() else _png(1024, _colour(slug)))
    tests = SKILL_DIR / "tests" / ("android" if target.engine == "capacitor" else "desktop")
    for path in sorted(p for p in tests.iterdir() if p.is_file()):
        text = path.read_text(encoding="utf-8")
        writer.write(f"tests/{path.name}", text.replace("${APP_ID}", app_id))
    if strategy == "client-shell":
        writer.write("web/package.json", json.dumps({
            "name": f"{slug}-{target.id}-web", "private": True, "type": "module",
            "scripts": {"build": "vite build", "dev": "vite --port 5190 --strictPort"},
            "dependencies": {"react": "^19.0.0", "react-dom": "^19.0.0", "react-router": "^7.0.0"},
            "devDependencies": {"vite": "^7.0.0", "@vitejs/plugin-react": "^5.0.0", "typescript": "^5.9.0",
                                "@types/react": "^19.0.0", "@types/react-dom": "^19.0.0"},
        }, indent=2) + "\n")
        writer.write("MIGRATION.md", migration_notes(analysis, plan, target))
    record = {"target": target.id, "app_id": app_id, "strategy": strategy, "created": writer.created, "kept": writer.kept}
    if not dry_run:
        record_dir = root / ".navin" / "appfactory" / target.id
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "scaffold.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {**record, "folder": target.folder, "dry_run": dry_run,
            "audit": audit(root, target.id) if not dry_run else None,
            "next_steps": [step for step in plan.get("steps", []) if step.get("id") not in {"install"}][:8]}


def migration_notes(analysis: dict[str, Any], plan: dict[str, Any], target: Target) -> str:
    lines = [
        f"# Client frontend for the {target.label} app",
        "",
        "The website uses server features, so it is not exported as static files (that would break them).",
        "This `web/` app is the client version: it reuses the site's components through the aliases of",
        "`appfactory.json` and calls the existing backend. The website itself is unchanged.",
        "",
        "## Server features to reconnect",
        "",
    ]
    for blocker in plan.get("blockers") or []:
        lines.append(f"- **{blocker['feature']}** ({blocker['count']}): {blocker['why']}")
        lines.extend(f"  - `{path}`" for path in blocker.get("files", []))
    lines += [
        "",
        "## How to port a screen",
        "",
        "1. Add a route in `web/src/routes.tsx` that renders the site's client components.",
        "2. Replace server data loading (server components, `cookies()`, Server Actions) by calls to",
        "   `api()` from `web/src/api.ts` against an existing backend endpoint.",
        "3. A Server Action without an HTTP endpoint needs one on the backend: propose it, add it only",
        "   with the user's agreement, and keep the website calling it the same way.",
        f"4. The backend must accept the app origin(s) {', '.join(ORIGINS[target.id])} (CORS) and send",
        "   cookies with `SameSite=None; Secure`, or use a bearer token (`setAuthToken`).",
        "",
        "`next/link`, `next/navigation` and `next/image` imports keep working through `web/src/shims/`.",
        "",
    ]
    return "\n".join(lines)


_TEXT_EXT = frozenset({".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".html", ".css", ".map", ".xml", ".txt",
                       ".md", ".yml", ".yaml", ".toml", ".rs", ".gradle", ".properties", ".plist", ".env"})
_AUDIT_SKIP = frozenset({"node_modules", "build", "target", ".gradle", ".stage", "gen", "DerivedData", "Pods", ".git"})


def _dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def audit(project_root: str | Path, target_name: str) -> dict[str, Any]:
    """Security checks of ``os/<target>`` against policies/security.yaml."""
    target = get_target(target_name)
    root = Path(project_root).expanduser().resolve()
    folder = root / "os" / target.id
    policy = load_policy("security")
    findings: list[dict[str, str]] = []

    def add(severity: str, rule: str, path: str, detail: str) -> None:
        findings.append({"severity": severity, "rule": rule, "path": path, "detail": detail})

    if not folder.is_dir():
        return {"ok": False, "findings": [{"severity": "error", "rule": "missing", "path": target.folder,
                                           "detail": "the app folder does not exist yet: scaffold it first"}]}
    config_path = folder / "appfactory.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        config = {}
        add("error", "config", "appfactory.json", "missing or unreadable")
    backend = str(config.get("backend_url") or "")
    if backend:
        parsed = urlparse(backend)
        allowed = set(policy.get("backend", {}).get("http_hosts_allowed") or [])
        if parsed.scheme != "https" and parsed.hostname not in allowed:
            add("error", "https-only", "appfactory.json", f"backend {backend} is not HTTPS")
    if target.engine == "capacitor":
        cap_path = folder / "capacitor.config.json"
        try:
            cap = json.loads(cap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cap = {}
            add("error", "config", "capacitor.config.json", "missing or unreadable")
        for key in policy.get("capacitor", {}).get("forbidden_config") or []:
            value = _dig(cap, key)
            if value not in (None, False, ""):
                add("error", "forbidden-config", "capacitor.config.json", f"{key} is set ({value!r})")
        manifest = folder / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="ignore")
            granted = set(re.findall(r'uses-permission[^>]+android:name="([^"]+)"', text))
            cap_policy = policy.get("capacitor", {})
            for perm in sorted(granted & set(cap_policy.get("android_permissions_forbidden") or [])):
                add("error", "android-permission", manifest.relative_to(folder).as_posix(), f"{perm} is not allowed")
            for perm in sorted(granted & set(cap_policy.get("android_permissions_need_reason") or [])):
                add("warning", "android-permission", manifest.relative_to(folder).as_posix(), f"{perm} needs a reason tied to a feature")
            if 'usesCleartextTraffic="true"' in text:
                add("error", "cleartext", manifest.relative_to(folder).as_posix(), "plain HTTP traffic is enabled")
    else:
        conf_path = folder / "src-tauri" / "tauri.conf.json"
        try:
            conf = json.loads(conf_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            conf = {}
            add("error", "config", "src-tauri/tauri.conf.json", "missing or unreadable")
        tauri_policy = policy.get("tauri", {})
        if tauri_policy.get("require_csp") and not _dig(conf, "app.security.csp"):
            add("error", "csp", "src-tauri/tauri.conf.json", "no content security policy")
        for key in tauri_policy.get("forbidden_config") or []:
            if _dig(conf, key):
                add("error", "forbidden-config", "src-tauri/tauri.conf.json", f"{key} is enabled")
        caps = folder / "src-tauri" / "capabilities"
        known = set(tauri_policy.get("permissions_default") or []) | set(tauri_policy.get("permissions_need_reason") or [])
        for cap_file in sorted(caps.glob("*.json")) if caps.is_dir() else []:
            try:
                perms = json.loads(cap_file.read_text(encoding="utf-8")).get("permissions") or []
            except (OSError, ValueError):
                add("error", "config", cap_file.relative_to(folder).as_posix(), "unreadable capability")
                continue
            for perm in perms:
                ident = perm if isinstance(perm, str) else str((perm or {}).get("identifier", ""))
                rel = cap_file.relative_to(folder).as_posix()
                if ident in set(tauri_policy.get("permissions_forbidden") or []):
                    add("error", "tauri-permission", rel, f"{ident} is not allowed")
                elif ident in set(tauri_policy.get("permissions_need_reason") or []):
                    add("warning", "tauri-permission", rel, f"{ident} needs a reason tied to a feature")
                elif ident not in known:
                    add("warning", "tauri-permission", rel, f"{ident} is not in the policy: check it is the smallest scope")
    secret_policy = policy.get("secrets", {})
    patterns = [(item["name"], re.compile(item["regex"])) for item in secret_policy.get("patterns") or []]
    never = list(secret_policy.get("files_never_in_app") or [])
    scanned = 0
    for current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in _AUDIT_SKIP]
        for file_name in files:
            path = Path(current) / file_name
            rel = path.relative_to(folder).as_posix()
            if any(fnmatch.fnmatch(file_name, pattern) for pattern in never):
                add("error", "secret-file", rel, "this file must not be inside the app")
                continue
            if path.suffix not in _TEXT_EXT or scanned >= 4000:
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for label, pattern in patterns:
                if pattern.search(text):
                    add("error", "secret", rel, f"looks like a {label}")
    errors = [item for item in findings if item["severity"] == "error"]
    return {"ok": not errors, "errors": len(errors), "warnings": len(findings) - len(errors), "files_scanned": scanned,
            "findings": findings}


def remove_stage(project_root: str | Path, target_name: str) -> bool:
    """Delete the Next export staging copy (only that folder)."""
    target = get_target(target_name)
    stage = Path(project_root).expanduser().resolve() / "os" / target.id / ".stage"
    if stage.is_dir():
        shutil.rmtree(stage)
        return True
    return False
