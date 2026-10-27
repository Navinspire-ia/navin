# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Compatibility diagnosis and the ordered conversion plan for one target.

The strategy decides how the native shell gets static files:

- ``static``: the project's own build already produces static files (SPA,
  Next with ``output: "export"``, Nuxt generate, SvelteKit adapter-static).
- ``next-export``: Next.js without server features. The export is configured
  in a staging copy under ``os/<target>/.stage``, never in the site's config.
- ``client-shell``: the app relies on the server (Server Actions, SSR, route
  handlers, middleware, server auth). A client frontend is built in
  ``os/<target>/web`` and talks to the existing backend over HTTPS. Turning on
  ``output: "export"`` there would break those features, so it is never done.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any

from navin.appfactory.targets import Target, default_app_id, get_target

# Server features that a static export cannot carry, per framework family.
_NEXT_BLOCKERS = {
    "server_action": "Server Actions (\"use server\") run on the server",
    "route_handler": "route handlers (app/**/route) are server endpoints",
    "api_route": "API routes (pages/api) are server endpoints",
    "middleware": "middleware runs on the server for every request",
    "ssr": "server rendering (cookies(), headers(), getServerSideProps, force-dynamic)",
    "dynamic_route": "dynamic routes without generateStaticParams are rendered on demand",
    "isr": "incremental static regeneration (revalidate) needs a server",
}
_GENERIC_BLOCKERS = {
    "server_action": "server functions run on the server",
    "route_handler": "server routes are server endpoints",
    "middleware": "server hooks run for every request",
    "ssr": "server loaders render on demand",
}

# Origins the native shells load the app from: the backend must accept them (CORS).
ORIGINS = {
    "android": ["https://localhost"],
    "ios": ["capacitor://localhost"],
    "windows": ["http://tauri.localhost"],
    "macos": ["tauri://localhost"],
    "ubuntu": ["tauri://localhost"],
    "centos": ["tauri://localhost"],
    "appimage": ["tauri://localhost"],
}


def pm_exec(pm: str) -> str:
    return {"pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx"}.get(pm, "npx")


def pm_run(pm: str, script: str) -> str:
    return f"{pm} run {script}"


def host_info() -> dict[str, Any]:
    system = platform.system() or "Linux"
    wsl = False
    if system == "Linux":
        try:
            wsl = "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            wsl = False
    return {"system": system, "wsl": wsl, "machine": platform.machine()}


def host_can_build(target: Target, host: dict[str, Any]) -> bool:
    return host.get("system") in target.hosts


def _blockers(analysis: dict[str, Any], framework: str) -> list[dict[str, Any]]:
    labels = _NEXT_BLOCKERS if framework == "next" else _GENERIC_BLOCKERS
    found = []
    for kind, label in labels.items():
        items = analysis.get("server_features", {}).get(kind) or []
        if items:
            found.append({"feature": kind, "why": label, "count": analysis.get("server_feature_counts", {}).get(kind, len(items)),
                          "files": [item["path"] + (f":{item['line']}" if item.get("line") else "") for item in items[:8]]})
    server_auth = analysis.get("auth", {}).get("server") or []
    if server_auth:
        found.append({"feature": "server_auth", "why": "authentication runs on the server (" + ", ".join(server_auth) + ")",
                      "count": len(server_auth), "files": []})
    return found


def strategy_for(analysis: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    """(strategy, reasons, blockers) for the analysed project."""
    frontend = analysis.get("frontend")
    if not frontend:
        return "unsupported", ["no web frontend was found to package"], []
    framework = frontend.get("framework") or "unknown"
    if framework == "next":
        blockers = _blockers(analysis, "next")
        if blockers:
            return "client-shell", [
                "Next.js uses server features: a static export would break them, so the site keeps running as it is and "
                "the app gets its own client frontend in os/<target>/web that calls the existing backend",
            ], blockers
        if frontend.get("next_output") == "export":
            return "static", ["next.config already exports static files (output: \"export\")"], []
        return "next-export", ["Next.js without server features: exported in a staging copy, the site's next.config is untouched"], []
    if framework in {"nuxt", "sveltekit", "remix"}:
        blockers = _blockers(analysis, framework)
        if blockers:
            return "client-shell", [f"{framework} renders on the server: the app gets a client frontend in os/<target>/web"], blockers
        return "static", [f"{framework} without server routes: its static build is packaged as is"], []
    if framework == "unknown":
        return "static", ["no framework recognised: the build output is packaged as is if it contains index.html"], []
    return "static", [f"{framework} builds a single page app: its static build is packaged as is"], []


def _web_build_command(frontend: dict[str, Any], strategy: str) -> str:
    pm = frontend.get("package_manager") or "npm"
    framework = frontend.get("framework")
    if strategy == "client-shell":
        return pm_run(pm, "build")
    if strategy == "next-export":
        return f"{pm_exec(pm)} next build"
    if framework == "nuxt" and frontend.get("build_script") != "generate":
        return f"{pm_exec(pm)} nuxi generate"
    return pm_run(pm, frontend.get("build_script") or "build")


def _risks(analysis: dict[str, Any], target: Target, strategy: str) -> list[str]:
    frontend = analysis.get("frontend") or {}
    risks = []
    if analysis.get("server_features", {}).get("relative_api_call"):
        sample = analysis["server_features"]["relative_api_call"][0]["path"]
        risks.append(
            f"the frontend calls relative URLs such as /api (e.g. {sample}): inside the app they would hit the app itself; "
            "they must use the backend URL (API base variable or a fetch base)"
        )
    if analysis.get("backend") or analysis.get("server_features", {}).get("relative_api_call"):
        risks.append("the backend must accept the app origin(s) " + ", ".join(ORIGINS[target.id]) + " (CORS) and HTTPS only")
    if analysis.get("auth", {}).get("server") or analysis.get("auth", {}).get("client"):
        risks.append(
            "auth cookies across origins need SameSite=None; Secure, or a token flow; the login must be tested in the app"
        )
    base = frontend.get("vite_base") or ""
    if base.startswith("/") and base not in {"/", "./"}:
        risks.append(f"the build uses an absolute base path {base!r}: assets would not load in the app (blank screen); build with base './'")
    if strategy == "client-shell" and analysis.get("server_features", {}).get("server_action"):
        risks.append(
            "Server Actions have no public HTTP contract: each one the app uses needs an API endpoint on the existing "
            "backend (added only with the user's agreement), or a call to an endpoint that already exists"
        )
    if frontend.get("next_image") and strategy == "next-export":
        risks.append("next/image is exported unoptimized (images served as they are)")
    if target.engine == "capacitor":
        risks.append("mobile adaptation: touch targets of 44px or more, safe areas, keyboard, back button, small screens")
    return risks


def _tool_steps(target: Target, pm: str) -> list[dict[str, Any]]:
    x = pm_exec(pm)
    folder = target.folder
    steps: list[dict[str, Any]] = [
        {"id": "install", "title": "Install the app shell dependencies", "cwd": folder, "command": f"{pm} install"},
        {"id": "web", "title": "Build the web frontend and copy it into www", "cwd": folder, "command": "node scripts/build-web.mjs"},
    ]
    if target.engine == "capacitor":
        plat = target.platform
        steps += [
            # ``cap add`` refuses to run twice ("android platform already exists"):
            # the step is skipped when the native folder is already there.
            {"id": "native", "title": f"Create the native {target.label} project (first time only)", "cwd": folder,
             "command": f"{x} cap add {plat}", "once": True, "skip_if_exists": plat},
            {"id": "sync", "title": "Copy the web build and plugins into the native project", "cwd": folder, "command": f"{x} cap sync {plat}"},
            # Verified against @capacitor/assets 3.x: ``--android`` / ``--ios`` select the platform;
            # the source images are assets/icon-only.png, icon-foreground.png, icon-background.png, splash.png.
            {"id": "icons", "title": "Generate the app icons and splash screens", "cwd": folder,
             "command": f"{x} capacitor-assets generate --{plat}"},
        ]
        if plat == "android":
            steps += [
                {"id": "build", "title": "Build the debug APK", "cwd": f"{folder}/android", "command": "./gradlew assembleDebug",
                 "windows_command": "gradlew.bat assembleDebug", "background": True,
                 "needs": "JDK 21 on PATH or JAVA_HOME, Android SDK with platform 36 (ANDROID_HOME)"},
                {"id": "release", "title": "Build the signed release bundle (.aab)", "cwd": f"{folder}/android",
                 "command": "./gradlew bundleRelease", "windows_command": "gradlew.bat bundleRelease", "background": True,
                 "needs": "signing keystore from env: NAVIN_ANDROID_KEYSTORE, NAVIN_ANDROID_KEYSTORE_PASSWORD, NAVIN_ANDROID_KEY_ALIAS, NAVIN_ANDROID_KEY_PASSWORD"},
            ]
        else:
            steps += [
                # Capacitor 8 generates an Xcode workspace (CocoaPods or SPM): build the
                # workspace, not the bare project, or the pods are not linked.
                {"id": "build", "title": "Build for the iOS simulator", "cwd": folder,
                 "command": "xcodebuild -workspace ios/App/App.xcworkspace -scheme App -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build",
                 "background": True, "host": "Darwin"},
                {"id": "release", "title": "Archive and export the .ipa", "cwd": folder,
                 "command": "xcodebuild -workspace ios/App/App.xcworkspace -scheme App -configuration Release -archivePath ios/App/build/App.xcarchive archive",
                 "background": True, "host": "Darwin", "needs": "an Apple team id and signing certificate"},
            ]
    else:
        bundles = ",".join(target.bundles)
        steps += [
            {"id": "icons", "title": "Generate the app icons", "cwd": folder, "command": f"{x} tauri icon app-icon.png"},
            # Debug first: a third of the compile time of a release build, and a
            # real installable package; the release build follows once it works.
            {"id": "build", "title": f"Build the {target.label} package (debug, fast)", "cwd": folder,
             "command": f"{x} tauri build --debug --bundles {bundles}", "background": True},
            {"id": "release", "title": f"Build the optimized {target.label} package", "cwd": folder,
             "command": f"{x} tauri build --bundles {bundles}", "background": True},
        ]
    return steps


def _test_steps(target: Target, app_id: str, app_name: str, backend_url: str = "") -> list[dict[str, Any]]:
    folder = target.folder
    backend_url = backend_url or "<backend url>"
    maestro = (f"maestro test -e HOME_TEXT=\"{app_name}\" -e LOGIN_TEXT=<login button text> "
               "-e LOGIN_SCREEN_TEXT=<text of the login screen> tests/smoke.maestro.yaml")
    if target.id == "android":
        return [
            {"id": "test-install", "test": "install", "cwd": folder,
             "command": "adb install -r android/app/build/outputs/apk/debug/app-debug.apk"},
            {"id": "test-start", "test": "start", "cwd": folder,
             "command": f"adb shell monkey -p {app_id} -c android.intent.category.LAUNCHER 1"},
            {"id": "test-flow", "test": "navigation", "cwd": folder, "command": maestro,
             "alternative": "mobile action=preview_start, then ui_dump / tap through the main screens"},
            {"id": "test-logs", "test": "api", "cwd": folder, "command": "adb logcat -d -s Capacitor chromium",
             "check": "no failed request to the backend, no CORS or mixed content error"},
        ]
    if target.id == "ios":
        return [
            {"id": "test-start", "test": "start", "cwd": folder, "command": "npx cap run ios", "host": "Darwin"},
            {"id": "test-flow", "test": "navigation", "cwd": folder, "command": maestro, "host": "Darwin"},
        ]
    return [
        {"id": "test-start", "test": "start", "cwd": folder,
         "command": "launch the built binary from src-tauri/target/debug (or release) and check the process stays alive for 15 s"},
        {"id": "test-web", "test": "navigation", "cwd": folder,
         "command": "BASE_URL=http://127.0.0.1:4173 npx playwright test tests/smoke.spec.ts",
         "before": "serve www on 4173, e.g. npx serve -l 4173 www (or python3 -m http.server 4173 from www)",
         "check": "the packaged frontend (www) renders, navigates and loads every asset of its own"},
        {"id": "test-api", "test": "api", "cwd": folder,
         "command": f"BASE_URL=http://127.0.0.1:4173 BACKEND_URL={backend_url} npx playwright test tests/api.spec.ts",
         "check": "the backend answers and the app reaches it without CORS or mixed-content errors"},
    ]


def _files(target: Target, strategy: str) -> list[str]:
    folder = target.folder
    files = [f"{folder}/package.json", f"{folder}/appfactory.json", f"{folder}/scripts/build-web.mjs", f"{folder}/README.md",
             f"{folder}/.gitignore", f"{folder}/tests/", f"{folder}/ci/github-actions.yml"]
    if target.engine == "capacitor":
        files += [f"{folder}/capacitor.config.json", f"{folder}/assets/logo.png", f"{folder}/native/native.js",
                  f"{folder}/native/native.css"]
    else:
        files += [f"{folder}/app-icon.png", f"{folder}/src-tauri/tauri.conf.json", f"{folder}/src-tauri/Cargo.toml",
                  f"{folder}/src-tauri/build.rs", f"{folder}/src-tauri/src/main.rs", f"{folder}/src-tauri/src/lib.rs",
                  f"{folder}/src-tauri/capabilities/default.json"]
    if strategy == "client-shell":
        files += [f"{folder}/web/package.json", f"{folder}/web/vite.config.ts", f"{folder}/web/index.html",
                  f"{folder}/web/src/main.tsx", f"{folder}/web/src/api.ts", f"{folder}/web/src/routes.tsx",
                  f"{folder}/web/src/shims/", f"{folder}/MIGRATION.md"]
    return files


def existing_config(project_root: str | Path, target: Target) -> dict[str, Any]:
    """``os/<target>/appfactory.json`` when the app folder was already created."""
    path = Path(project_root) / target.folder / "appfactory.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plan(analysis: dict[str, Any], target_name: str, host: dict[str, Any] | None = None, backend_url: str = "",
         app_id: str = "") -> dict[str, Any]:
    """The diagnosis and ordered plan for *target_name*. Reads the analysis and an existing app config."""
    target = get_target(target_name)
    host = host or host_info()
    strategy, reasons, blockers = strategy_for(analysis)
    frontend = analysis.get("frontend") or {}
    pm = frontend.get("package_manager") or "npm"
    existing = existing_config(analysis.get("root") or ".", target) if analysis.get("root") else {}
    backend_url = backend_url or str(existing.get("backend_url") or "")
    app_id = app_id or str(existing.get("app_id") or "") or default_app_id((analysis.get("app") or {}).get("slug", ""), target)
    app_name = str(existing.get("app_name") or (analysis.get("app") or {}).get("name") or "App")
    local = host_can_build(target, host)
    needs_backend = bool(analysis.get("backend") or analysis.get("server_features", {}).get("relative_api_call")
                         or strategy == "client-shell")
    questions = []
    if needs_backend and not backend_url:
        questions.append("the public HTTPS address of the backend the app must call (e.g. https://api.example.com)")
    steps = _tool_steps(target, pm)
    if strategy == "client-shell":
        steps.insert(1, {"id": "client", "title": "Write the client frontend in web/ (port the screens, call the backend)",
                         "cwd": f"{target.folder}/web", "command": f"{pm} install", "manual": True,
                         "see": f"{target.folder}/MIGRATION.md"})
    ci = None
    if not local:
        ci = {
            "runner": target.ci_runner,
            "workflow": f"{target.folder}/ci/github-actions.yml",
            "why": f"{target.label} packages are built on {', '.join(target.hosts)}; this machine runs {host.get('system')}",
            "wsl_hint": (
                "this Linux runs inside WSL: the same os/windows folder can also be built from a Windows terminal"
                if host.get("wsl") and target.id == "windows" else ""
            ),
        }
    return {
        "target": target.to_dict(),
        "app_id": app_id,
        "strategy": strategy,
        "reasons": reasons,
        "blockers": blockers,
        "web_build": {
            "frontend_dir": frontend.get("dir", "."),
            "command": _web_build_command(frontend, strategy) if frontend else "",
            "output_dir": "web/dist" if strategy == "client-shell" else ("out" if strategy == "next-export" else frontend.get("output_dir", "dist")),
        },
        "origins": ORIGINS[target.id],
        "backend_url": backend_url,
        "questions": questions,
        "host": {**host, "can_build_here": local},
        "ci": ci,
        "steps": steps,
        "tests": _test_steps(target, app_id, app_name, backend_url),
        "risks": _risks(analysis, target, strategy),
        "files": _files(target, strategy),
        "never": [
            "edit the site's next.config, vite.config or package.json scripts",
            "write outside " + target.folder + " and .navin/appfactory",
            "turn on output: \"export\" for an app that uses server features",
            "substitute another framework for " + ("Capacitor 8" if target.engine == "capacitor" else "Tauri 2"),
        ],
    }
