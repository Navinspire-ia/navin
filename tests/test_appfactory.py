# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""App Factory: targets, analysis, compatibility plan, toolchain, scaffold, audit, delivery, tool and /apps."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from navin.appfactory import analyze as analyze_mod
from navin.appfactory import compat, deliver, scaffold, toolchain
from navin.appfactory.targets import APP_ID_RE, TARGETS, default_app_id, get_target, targets_payload

LINUX = {"system": "Linux", "wsl": False, "machine": "x86_64"}
WSL = {"system": "Linux", "wsl": True, "machine": "x86_64"}
_DASHES = re.compile("[\u2013\u2014]")


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _pkg(deps: dict[str, str], scripts: dict[str, str] | None = None, name: str = "shop", version: str = "1.2.0") -> str:
    return json.dumps({"name": name, "version": version, "scripts": scripts or {"build": "build-it"}, "dependencies": deps})


@pytest.fixture
def next_server(tmp_path: Path) -> Path:
    """Next.js 16 app with Server Actions, a route handler, SSR, server auth and a FastAPI backend."""
    return _write(tmp_path / "shop", {
        "package.json": _pkg({"next": "^16.0.1", "react": "19.2.0", "next-auth": "5.0.0"}, {"build": "next build", "dev": "next dev"}),
        "pnpm-lock.yaml": "lockfileVersion: 9\n",
        "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["./src/*"]}}}),
        "next.config.ts": "export default { reactStrictMode: true }\n",
        "src/app/actions.ts": "'use server'\nexport async function buy() {}\n",
        "src/app/api/orders/route.ts": "export async function GET() { return Response.json([]) }\n",
        "src/app/account/page.tsx": "import { cookies } from 'next/headers'\nexport default async function Page() { return null }\n",
        "src/app/products/[id]/page.tsx": "export default function P() { fetch('/api/orders'); return null }\n",
        "src/middleware.ts": "export function middleware() {}\n",
        ".env.example": "NEXT_PUBLIC_API_URL=https://api.shop.test\nDATABASE_PASSWORD=hunter2-secret\nAPI_KEY=k\n",
        ".env.local": "NEXT_PUBLIC_API_URL=https://value-that-must-not-leak.test\n",
        "backend/requirements.txt": "fastapi==0.115.0\nuvicorn\n",
        "node_modules/next/package.json": "{}",
    })


@pytest.fixture
def vite_spa(tmp_path: Path) -> Path:
    """React + Vite single page app whose build writes dist/index.html with plain node."""
    make = (
        "import { mkdirSync, writeFileSync } from 'node:fs';\n"
        "mkdirSync('dist/about', { recursive: true });\n"
        "writeFileSync('dist/index.html', '<!doctype html><html><head><meta name=\"viewport\" content=\"width=device-width\">"
        "<title>Spa</title></head><body><div id=\"root\">hello</div></body></html>');\n"
        "writeFileSync('dist/about/index.html', '<html><head></head><body>about</body></html>');\n"
        "writeFileSync('dist/app.js', 'console.log(process.env.VITE_API_URL || \"\")');\n"
    )
    return _write(tmp_path / "spa", {
        "package.json": _pkg({"react": "18.3.1", "vite": "^7.0.0"}, {"build": "node make.mjs"}, name="@acme/spa-app"),
        "package-lock.json": "{}",
        "vite.config.ts": "export default { base: '/static/app/', build: { outDir: 'dist' } }\n",
        "src/main.tsx": "const api = import.meta.env.VITE_API_URL\n",
        "make.mjs": make,
        "node_modules/.keep": "",
    })


def _digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "os" not in p.relative_to(root).parts[:1] and ".navin" not in p.parts}


# -- targets ---------------------------------------------------------------------------------------


def test_seven_targets_with_imposed_engines_and_os_folders():
    assert list(TARGETS) == ["android", "ios", "windows", "macos", "ubuntu", "centos", "appimage"]
    for target in TARGETS.values():
        assert target.folder == f"os/{target.id}"
        assert target.engine == ("capacitor" if target.kind == "mobile" else "tauri")
    assert get_target("android").deliverables == ("apk", "aab")
    assert get_target("windows").deliverables == ("exe", "msi") and get_target("windows").hosts == ("Windows",)
    assert get_target("centos").bundles == ("rpm",) and get_target("appimage").bundles == ("appimage",)
    assert get_target("ios").hosts == ("Darwin",)
    payload = targets_payload()
    assert payload["engines"] == {"mobile": "Capacitor 8", "desktop": "Tauri 2 + Rust"}
    assert "Electron" in payload["other_engines_never_substituted"]["desktop"]


def test_aliases_and_invalid_targets():
    assert get_target("APK").id == "android" and get_target("msi").id == "windows"
    assert get_target("linux").id == "ubuntu" and get_target("rpm").id == "centos" and get_target("mobile").id == "android"
    with pytest.raises(ValueError, match="android, ios, windows"):
        get_target("electron")


def test_default_app_ids_are_valid_reverse_dns():
    for slug in ("shop", "my-shop-2", "2fast", "", "Été"):
        for target in TARGETS.values():
            app_id = default_app_id(slug, target)
            assert APP_ID_RE.match(app_id), app_id
            assert not app_id.endswith(".app")


# -- analysis --------------------------------------------------------------------------------------


def test_analyze_next_finds_every_server_feature_auth_backend_and_env_names(next_server: Path):
    report = analyze_mod.analyze(next_server)
    front = report["frontend"]
    assert front["framework"] == "next" and front["framework_version"] == "16.0.1" and front["router"] == "app"
    assert front["package_manager"] == "pnpm" and front["build_script"] == "build"
    counts = report["server_feature_counts"]
    for kind in ("server_action", "route_handler", "ssr", "middleware", "dynamic_route", "relative_api_call"):
        assert counts.get(kind), kind
    assert report["server_features"]["server_action"][0] == {"path": "src/app/actions.ts", "line": 1}
    assert report["auth"]["server"] == ["next-auth"]
    assert {"framework": "fastapi", "dir": "backend"} in report["backend"]
    assert report["api_env"] == ["NEXT_PUBLIC_API_URL"]
    text = json.dumps(report)
    assert "value-that-must-not-leak" not in text and "hunter2" not in text  # names only, never values


def test_analyze_vite_spa_angular_and_nuxt_outputs(vite_spa: Path, tmp_path: Path):
    spa = analyze_mod.analyze(vite_spa)
    assert spa["frontend"]["framework"] == "react" and spa["frontend"]["package_manager"] == "npm"
    assert spa["frontend"]["output_dir"] == "dist" and spa["frontend"]["vite_base"] == "/static/app/"
    assert spa["app"] == {"name": "Spa App", "slug": "spa-app"}
    angular = _write(tmp_path / "ng", {
        "package.json": _pkg({"@angular/core": "^19.0.0"}),
        "angular.json": json.dumps({"projects": {"crm": {"architect": {"build": {
            "builder": "@angular/build:application", "options": {"outputPath": "dist/crm"}}}}}}),
    })
    assert analyze_mod.analyze(angular)["frontend"]["output_dir"] == "dist/crm/browser"
    nuxt = _write(tmp_path / "nx", {"package.json": _pkg({"nuxt": "^4.0.0"}, {"generate": "nuxt generate"}),
                                     "server/api/hello.ts": "export default defineEventHandler(() => 1)\n"})
    report = analyze_mod.analyze(nuxt)
    assert report["frontend"]["framework"] == "nuxt" and report["frontend"]["build_script"] == "generate"
    assert report["server_feature_counts"]["route_handler"] == 1


def test_analyze_finds_a_frontend_in_a_sub_folder_and_reports_missing_ones(tmp_path: Path):
    mono = _write(tmp_path / "mono", {
        "package.json": json.dumps({"name": "mono", "private": True}),
        "apps/web/package.json": _pkg({"vue": "^3.5.0", "vite": "^7.0.0"}),
        "apps/api/package.json": _pkg({"@nestjs/core": "^11.0.0"}),
    })
    report = analyze_mod.analyze(mono)
    assert report["frontend"]["dir"] == "apps/web" and report["frontend"]["framework"] == "vue"
    assert {"framework": "nestjs", "dir": "apps/api"} in report["backend"]
    empty = _write(tmp_path / "py", {"requirements.txt": "django==5.1\n", "manage.py": ""})
    report = analyze_mod.analyze(empty)
    assert report["frontend"] is None and report["warnings"]
    assert compat.strategy_for(report)[0] == "unsupported"


# -- compatibility plan ----------------------------------------------------------------------------


def test_next_with_server_features_gets_a_client_shell_never_a_static_export(next_server: Path):
    plan = compat.plan(analyze_mod.analyze(next_server), "android", host=LINUX)
    assert plan["strategy"] == "client-shell"
    assert {b["feature"] for b in plan["blockers"]} >= {"server_action", "route_handler", "ssr", "middleware", "server_auth"}
    assert any("src/app/actions.ts:1" in b["files"] for b in plan["blockers"] if b["feature"] == "server_action")
    assert plan["web_build"]["output_dir"] == "web/dist"
    commands = " ".join(step["command"] for step in plan["steps"])
    assert "output" not in commands and "next build" not in commands
    assert any(step["id"] == "client" and step["cwd"] == "os/android/web" for step in plan["steps"])
    assert any("backend" in question for question in plan["questions"])
    assert any("Server Actions" in risk for risk in plan["risks"]) and any("SameSite" in risk for risk in plan["risks"])
    assert plan["origins"] == ["https://localhost"]
    assert f"{plan['target']['folder']}/MIGRATION.md" in plan["files"]


def test_next_without_server_features_is_exported_in_a_staging_copy(tmp_path: Path):
    site = _write(tmp_path / "blog", {"package.json": _pkg({"next": "16.0.0"}, {"build": "next build"}),
                                       "app/page.tsx": "export default function Home() { return null }\n",
                                       "app/posts/[slug]/page.tsx": "export function generateStaticParams() { return [] }\n"})
    plan = compat.plan(analyze_mod.analyze(site), "ubuntu", host=LINUX)
    assert plan["strategy"] == "next-export" and plan["web_build"]["command"] == "npx next build"
    assert any("next.config" in item for item in plan["never"])
    _write(site, {"next.config.mjs": "export default { output: 'export' }\n"})
    assert compat.plan(analyze_mod.analyze(site), "ubuntu", host=LINUX)["strategy"] == "static"


def test_spa_plan_commands_follow_the_package_manager_and_flag_an_absolute_base(vite_spa: Path, next_server: Path):
    plan = compat.plan(analyze_mod.analyze(vite_spa), "windows", host=WSL)
    assert plan["strategy"] == "static" and plan["web_build"]["command"] == "npm run build"
    assert any("--bundles nsis,msi" in step["command"] for step in plan["steps"])
    assert any("absolute base path" in risk for risk in plan["risks"])
    assert plan["host"]["can_build_here"] is False
    assert plan["ci"]["runner"] == "windows-latest" and "WSL" in plan["ci"]["wsl_hint"]
    pnpm = compat.plan(analyze_mod.analyze(next_server), "android", host=LINUX, backend_url="https://api.shop.test")
    assert pnpm["steps"][0]["command"] == "pnpm install" and pnpm["questions"] == []
    assert any(step["command"] == "pnpm exec cap sync android" for step in pnpm["steps"])
    android = {step["id"]: step for step in pnpm["steps"]}
    assert android["build"]["command"] == "./gradlew assembleDebug" and android["build"]["background"]
    assert "JDK 21" in android["build"]["needs"] and "ANDROID_HOME" in android["build"]["needs"]
    assert "NAVIN_ANDROID_KEYSTORE" in android["release"]["needs"]
    assert android["native"]["once"] and android["native"]["skip_if_exists"] == "android"


def test_desktop_plan_builds_a_debug_package_first_then_release(vite_spa: Path):
    """Measured on this machine: the debug deb compiles in 1m40 and installs; release is a third slower."""
    plan = compat.plan(analyze_mod.analyze(vite_spa), "ubuntu", host=LINUX, backend_url="https://api.spa.test")
    steps = {step["id"]: step for step in plan["steps"]}
    assert steps["build"]["command"] == "npx tauri build --debug --bundles deb" and steps["build"]["background"]
    assert steps["release"]["command"] == "npx tauri build --bundles deb"
    order = [step["id"] for step in plan["steps"]]
    assert order.index("icons") < order.index("build") < order.index("release")
    tests = {step["test"]: step for step in plan["tests"]}
    assert tests["navigation"]["command"].endswith("npx playwright test tests/smoke.spec.ts")
    assert "BACKEND_URL=https://api.spa.test" in tests["api"]["command"] and "api.spec.ts" in tests["api"]["command"]


def test_ios_plan_builds_the_capacitor_workspace_not_the_bare_project(vite_spa: Path):
    """Capacitor 8 generates App.xcworkspace (pods or SPM); building App.xcodeproj alone misses them."""
    plan = compat.plan(analyze_mod.analyze(vite_spa), "ios", host=LINUX)
    steps = {step["id"]: step for step in plan["steps"]}
    for step_id in ("build", "release"):
        assert "-workspace ios/App/App.xcworkspace" in steps[step_id]["command"]
        assert "App.xcodeproj" not in steps[step_id]["command"]
        assert steps[step_id]["host"] == "Darwin"


def test_analyze_finds_a_frontend_under_any_folder_name_and_a_computed_out_dir(tmp_path: Path):
    """Navin's own frontend lives in webui/ with ``outDir: path.resolve(__dirname, "../navin/web/dist")``."""
    project = _write(tmp_path / "navin-like", {
        "pyproject.toml": "[project]\nname='engine'\n",
        "webui/package.json": _pkg({"react": "^18.3.1", "vite": "^7.0.0"}, {"build": "tsc && vite build"}),
        "webui/vite.config.ts": (
            "import path from 'node:path';\nexport default defineConfig({\n  build: {\n"
            "    outDir: path.resolve(__dirname, \"../navin/web/dist\"),\n    emptyOutDir: true,\n  },\n});\n"
        ),
        "node_modules/some-dep/package.json": _pkg({"react": "^18.0.0"}),  # never a candidate
        "os/android/package.json": _pkg({"@capacitor/core": "^8.0.0"}),  # a previous scaffold is not the app
    })
    report = analyze_mod.analyze(project)
    assert report["frontend"]["dir"] == "webui" and report["frontend"]["framework"] == "react"
    assert report["frontend"]["output_dir"] == "../navin/web/dist"
    plain = _write(tmp_path / "plain", {
        "front/package.json": _pkg({"vue": "^3.5.0", "vite": "^7.0.0"}),
        "front/vite.config.js": "export default { build: { outDir: './build' } }\n",
    })
    assert analyze_mod.analyze(plain)["frontend"]["dir"] == "front"
    assert analyze_mod.analyze(plain)["frontend"]["output_dir"] == "build"


# -- toolchain -------------------------------------------------------------------------------------


class Probe:
    """Fake which/run: only the listed binaries exist, each with a version line."""

    def __init__(self, versions: dict[str, str]):
        self.versions = versions
        self.calls: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}" if name in self.versions else None

    def run(self, cmd: list[str]) -> str:
        self.calls.append(cmd)
        return self.versions.get(Path(cmd[0]).name, "")


def test_android_doctor_needs_node_22_jdk_21_and_sdk_36(tmp_path: Path):
    probe = Probe({"node": "v20.11.0", "java": 'openjdk version "17.0.9" 2023-10-17'})
    report = toolchain.doctor("android", host=LINUX, which=probe.which, run=probe.run, env={}, home=tmp_path, family="debian")
    tools = {tool["id"]: tool for tool in report["tools"]}
    assert tools["node"]["present"] and not tools["node"]["ok"] and "fnm install 22" in tools["node"]["install"]
    assert tools["jdk"]["version"] == "17.0.9" and not tools["jdk"]["ok"] and "openjdk-21-jdk" in tools["jdk"]["install"]
    assert not tools["android-sdk"]["present"] and "platforms;android-36" in tools["android-sdk"]["install"]
    assert not tools["maestro"]["required"]
    assert report["ready"] is False and set(report["missing"]) == {"node", "jdk", "android-sdk", "adb"}
    # Read-only: only version probes ran.
    assert all(any(flag in call for flag in ("--version", "-version", "version")) for call in probe.calls)


def test_android_doctor_ready_with_the_sdk_navin_prepared(tmp_path: Path):
    sdk = tmp_path / ".navin" / "android-platform-tools"
    (sdk / "platforms" / "android-36").mkdir(parents=True)
    (sdk / "platform-tools").mkdir()
    probe = Probe({"node": "v22.12.0", "java": 'openjdk version "21.0.5"', "adb": "Android Debug Bridge version 1.0.41"})
    report = toolchain.doctor("android", host=LINUX, which=probe.which, run=probe.run, env={}, home=tmp_path)
    assert report["ready"] is True and report["missing"] == []
    assert {tool["id"]: tool["version"] for tool in report["tools"]}["android-sdk"] == "36"


def test_desktop_doctor_per_host_and_linux_family(tmp_path: Path):
    probe = Probe({"node": "v22.1.0", "rustc": "rustc 1.82.0 (f6e511eec 2024-10-15)", "cargo": "cargo 1.82.0"})
    windows = toolchain.doctor("windows", host=WSL, which=probe.which, run=probe.run, env={}, home=tmp_path)
    assert windows["can_build_here"] is False and windows["ready"] is False
    assert "Windows terminal" in windows["host_note"] and windows["ci_runner"] == "windows-latest"
    fedora = toolchain.doctor("centos", host=LINUX, which=probe.which, run=probe.run, env={}, home=tmp_path, family="fedora")
    webkit = {tool["id"]: tool for tool in fedora["tools"]}["webkit2gtk"]
    assert not webkit["present"] and "dnf install -y webkit2gtk4.1-devel" in webkit["install"]
    old = Probe({"node": "v22.1.0", "rustc": "rustc 1.70.0", "cargo": "cargo 1.70.0", "pkg-config": "2.44.0"})
    old.versions["pkg-config"] = "2.44.0"
    ubuntu = toolchain.doctor("ubuntu", host=LINUX, which=old.which, run=old.run, env={}, home=tmp_path, family="debian")
    rust = {tool["id"]: tool for tool in ubuntu["tools"]}["rust"]
    assert rust["version"] == "1.70.0" and not rust["ok"] and "rustup" in rust["install"]
    assert ubuntu["missing"] == ["rust"]
    assert toolchain.linux_family(lambda: 'ID="rocky"\nID_LIKE="rhel centos fedora"') == "fedora"
    assert toolchain.linux_family(lambda: "ID=ubuntu\nID_LIKE=debian") == "debian"


# -- scaffold and audit ----------------------------------------------------------------------------


def _scaffold(root: Path, target: str, **kw):
    analysis = analyze_mod.analyze(root)
    plan = compat.plan(analysis, target, host=LINUX, backend_url=kw.get("backend_url", ""))
    return scaffold.scaffold(root, target, analysis, plan, **kw)


def test_scaffold_writes_only_inside_os_target_and_never_overwrites(next_server: Path):
    before = _digest(next_server)
    result = _scaffold(next_server, "android", backend_url="https://api.shop.test")
    assert _digest(next_server) == before  # the website and the backend are untouched
    folder = next_server / "os" / "android"
    assert {"capacitor.config.json", "appfactory.json", "package.json", "scripts/build-web.mjs", "native/native.js",
            "web/src/api.ts", "web/vite.config.ts", "MIGRATION.md", "tests/smoke.maestro.yaml", "assets/logo.png"} <= set(result["created"])
    outside = [p for p in next_server.rglob("*") if p.is_file() and p.relative_to(next_server).parts[0] not in {"os", ".navin"}
               and p.relative_to(next_server).as_posix() not in before]
    assert outside == []
    assert result["audit"]["ok"], result["audit"]
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix != ".png":
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"\{\{[A-Z_]+\}\}", text), path  # every template placeholder is filled
            assert not _DASHES.search(text), path
    cap = json.loads((folder / "capacitor.config.json").read_text())
    assert cap["appId"] == "com.shop.mobile" and cap["webDir"] == "www" and "url" not in cap.get("server", {})
    config = json.loads((folder / "appfactory.json").read_text())
    assert config["strategy"] == "client-shell" and config["frontend_dir"] == "../.."
    assert config["aliases"] == {"@": "../../src"} and config["backend_url"] == "https://api.shop.test"
    assert "src/app/actions.ts:1" in (folder / "MIGRATION.md").read_text()
    assert "appId: com.shop.mobile" in (folder / "tests" / "smoke.maestro.yaml").read_text()
    deps = json.loads((folder / "package.json").read_text())["dependencies"]
    assert deps["@capacitor/core"].startswith("^8") and deps["@capacitor/android"].startswith("^8")
    assert (next_server / ".navin" / "appfactory" / "android" / "scaffold.json").is_file()
    (folder / "README.md").write_text("edited by the user\n")
    again = _scaffold(next_server, "android", backend_url="https://api.shop.test")
    assert again["created"] == [] and "README.md" in again["kept"]
    assert (folder / "README.md").read_text() == "edited by the user\n"


def test_desktop_scaffold_is_a_minimal_tauri_2_project(vite_spa: Path):
    result = _scaffold(vite_spa, "windows", backend_url="https://api.spa.test")
    folder = vite_spa / "os" / "windows"
    conf = json.loads((folder / "src-tauri" / "tauri.conf.json").read_text())
    assert conf["identifier"] == "com.spaapp.desktop" and conf["build"] == {"frontendDist": "../www"}
    assert conf["bundle"]["targets"] == ["nsis", "msi"] and conf["version"] == "1.2.0"
    assert "connect-src 'self' ipc: http://ipc.localhost https://api.spa.test" in conf["app"]["security"]["csp"]
    caps = json.loads((folder / "src-tauri" / "capabilities" / "default.json").read_text())
    assert caps["permissions"] == ["core:default"] and caps["windows"] == ["main"]
    cargo = (folder / "src-tauri" / "Cargo.toml").read_text()
    assert 'name = "spa_app"' in cargo and 'tauri = { version = "2"' in cargo and 'rust-version = "1.77.2"' in cargo
    assert "spa_app_lib::run()" in (folder / "src-tauri" / "src" / "main.rs").read_text()
    assert (folder / "app-icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "web/package.json" not in result["created"]  # a static SPA needs no client shell
    assert json.loads((folder / "package.json").read_text())["devDependencies"]["@tauri-apps/cli"].startswith("^2")


def test_scaffold_refuses_unsafe_inputs(vite_spa: Path):
    with pytest.raises(ValueError, match="HTTPS"):
        _scaffold(vite_spa, "android", backend_url="http://api.spa.test")
    with pytest.raises(ValueError, match="com.company.app"):
        _scaffold(vite_spa, "android", app_id="my app")
    with pytest.raises(ValueError, match=r"\.app"):
        _scaffold(vite_spa, "macos", app_id="com.acme.app")
    assert not (vite_spa / "os").exists()
    writer = scaffold.Writer(vite_spa / "os" / "android")
    with pytest.raises(ValueError, match="outside"):
        writer.write("../../package.json", "{}")
    assert _scaffold(vite_spa, "android", backend_url="http://10.0.2.2:8000")["audit"]["ok"]  # emulator host is allowed


def test_audit_flags_wrappers_permissions_secrets_and_missing_csp(vite_spa: Path):
    _scaffold(vite_spa, "android", backend_url="https://api.spa.test")
    folder = vite_spa / "os" / "android"
    cap = json.loads((folder / "capacitor.config.json").read_text())
    cap["server"]["url"] = "https://spa.test"
    (folder / "capacitor.config.json").write_text(json.dumps(cap))
    _write(folder, {
        "android/app/src/main/AndroidManifest.xml": '<manifest><uses-permission android:name="android.permission.READ_SMS"/>'
        '<uses-permission android:name="android.permission.CAMERA"/><application android:usesCleartextTraffic="true"/></manifest>',
        "www/assets/app.js": "const key = 'AKIAABCDEFGHIJKLMNOP';\n",
        "www/.env": "TOKEN=x\n",
    })
    report = scaffold.audit(vite_spa, "android")
    rules = {(item["rule"], item["severity"]) for item in report["findings"]}
    assert not report["ok"]
    assert {("forbidden-config", "error"), ("android-permission", "error"), ("android-permission", "warning"),
            ("cleartext", "error"), ("secret", "error"), ("secret-file", "error")} <= rules
    _scaffold(vite_spa, "ubuntu", backend_url="https://api.spa.test")
    desktop = vite_spa / "os" / "ubuntu" / "src-tauri"
    conf = json.loads((desktop / "tauri.conf.json").read_text())
    conf["app"]["security"] = {}
    (desktop / "tauri.conf.json").write_text(json.dumps(conf))
    (desktop / "capabilities" / "default.json").write_text(json.dumps({"permissions": ["core:default", "shell:allow-execute"]}))
    findings = {item["rule"] for item in scaffold.audit(vite_spa, "ubuntu")["findings"]}
    assert {"csp", "tauri-permission"} <= findings
    assert scaffold.audit(vite_spa, "ios")["ok"] is False  # not scaffolded yet


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to run the generated build script")
def test_generated_build_script_builds_the_site_into_www_with_native_helpers(vite_spa: Path):
    _scaffold(vite_spa, "android", backend_url="https://api.spa.test")
    folder = vite_spa / "os" / "android"
    done = subprocess.run(["node", "scripts/build-web.mjs"], cwd=folder, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "--base=./" in done.stdout  # the absolute Vite base is rebuilt relative for the app
    index = (folder / "www" / "index.html").read_text()
    assert "viewport-fit=cover" in index and 'src="./navin-native/native.js"' in index
    assert 'href="../navin-native/native.css"' in (folder / "www" / "about" / "index.html").read_text()
    assert (folder / "www" / "navin-native" / "native.js").is_file()
    assert not (vite_spa / "dist" / "navin-native").exists()  # the site's own build output is not modified
    (vite_spa / "make.mjs").write_text("process.exit(0)\n")
    shutil.rmtree(vite_spa / "dist")
    failed = subprocess.run(["node", "scripts/build-web.mjs"], cwd=folder, capture_output=True, text=True, timeout=120, check=False)
    assert failed.returncode == 2 and "no index.html" in failed.stderr


# -- delivery --------------------------------------------------------------------------------------


def _apk(root: Path) -> Path:
    apk = root / "os" / "android" / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
    apk.parent.mkdir(parents=True, exist_ok=True)
    apk.write_bytes(b"PK" + b"0" * 4096)
    return apk


_PASSED = [{"name": name, "status": "passed", "evidence": f"{name} ok on emulator-5554"} for name in ("install", "start", "navigation", "api")]


def test_delivered_only_with_a_package_and_every_required_test_passed(tmp_path: Path):
    steps = [{"step": "build", "status": "done"}, {"step": "analyze", "status": "done", "note": "Next.js 16"}]
    no_package = deliver.report(tmp_path, "android", steps, _PASSED)
    assert no_package["status"] == "partial" and "no package was built" in no_package["reasons"]
    _apk(tmp_path)
    found = deliver.artifacts(tmp_path, "android")["artifacts"]
    assert [item["path"] for item in found] == ["os/android/android/app/build/outputs/apk/debug/app-debug.apk"]
    assert found[0]["debug"] is True and found[0]["size_text"] == "4.0 KB"
    # A desktop package built with ``tauri build --debug`` lands under target/debug: a real package too.
    deb = tmp_path / "os" / "ubuntu" / "src-tauri" / "target" / "debug" / "bundle" / "deb" / "Spa App_1.0.0_amd64.deb"
    deb.parent.mkdir(parents=True)
    deb.write_bytes(b"!<arch>" + b"\0" * 100)
    debs = deliver.artifacts(tmp_path, "ubuntu")["artifacts"]
    assert [item["name"] for item in debs] == ["Spa App_1.0.0_amd64.deb"] and debs[0]["debug"] is True
    done = deliver.report(tmp_path, "android", steps, _PASSED)
    assert done["status"] == "delivered" and done["reasons"] == []
    markdown = (tmp_path / ".navin" / "appfactory" / "android" / "report.md").read_text()
    assert markdown.startswith("# Android app: Delivered")
    assert markdown.index("- [x] analyze: Next.js 16") < markdown.index("- [x] build")  # steps in pipeline order
    assert json.loads((tmp_path / ".navin" / "appfactory" / "android" / "report.json").read_text())["status"] == "delivered"
    needs_login = deliver.report(tmp_path, "android", steps, _PASSED, has_auth=True)
    assert needs_login["status"] == "partial" and "auth" in needs_login["reasons"][0]


def test_a_compiled_build_without_tests_is_never_delivered(tmp_path: Path):
    _apk(tmp_path)
    claimed = deliver.report(tmp_path, "android", [{"step": "build", "status": "done"}],
                             [{"name": "install", "status": "passed"}, {"name": "start", "status": "not_run", "evidence": "no emulator: /dev/kvm missing"}])
    assert claimed["status"] == "partial"
    assert claimed["not_executed"] == ["install", "start"] and claimed["executed"] == []
    assert claimed["tests"][0]["note"].startswith("reported as passed without evidence")
    markdown = (tmp_path / ".navin" / "appfactory" / "android" / "report.md").read_text()
    assert "## Tests not executed" in markdown and "no emulator: /dev/kvm missing" in markdown
    failed = deliver.report(tmp_path / "other", "windows", [{"step": "build", "status": "failed", "note": "link.exe not found"}],
                            [{"name": "start", "status": "failed", "evidence": "exit 1"}])
    assert failed["status"] == "failed"
    with pytest.raises(ValueError, match="passed, failed or not_run"):
        deliver.report(tmp_path, "android", [], [{"name": "start", "status": "ok"}])
    with pytest.raises(ValueError, match="step="):
        deliver.report(tmp_path, "android", [{"status": "done"}], [])


# -- agent tool and /apps --------------------------------------------------------------------------


def _tool(root: Path):
    from navin.agent.tools.appfactory import AppFactoryTool

    tool = object.__new__(AppFactoryTool)
    tool._project_root = lambda: root
    return tool


def test_the_tool_runs_a_conversion_end_to_end(next_server: Path):
    tool = _tool(next_server)
    run = lambda **kw: asyncio.run(tool.execute(**kw))  # noqa: E731 - short local helper for readability
    assert json.loads(run(action="targets"))["engines"]["desktop"] == "Tauri 2 + Rust"
    assert json.loads(run(action="analyze"))["frontend"]["framework"] == "next"
    plan = json.loads(run(action="plan", target="apk"))
    assert plan["target"]["id"] == "android" and plan["strategy"] == "client-shell" and "backend" in plan["next"]
    missing = run(action="plan")
    assert missing.is_error and "needs target=" in missing
    dry = json.loads(run(action="scaffold", target="android", dry_run=True))
    assert dry["dry_run"] and dry["created"] and not (next_server / "os").exists()
    made = json.loads(run(action="scaffold", target="android", backend_url="https://api.shop.test", app_id="com.acme.shop"))
    assert made["audit"]["ok"] and made["questions"] == []
    replanned = json.loads(run(action="plan", target="android"))
    assert replanned["questions"] == [] and "com.acme.shop" in replanned["tests"][1]["command"]
    refused = run(action="scaffold", target="android", backend_url="http://api.shop.test")
    assert refused.is_error and "HTTPS" in refused
    report = json.loads(run(action="report", target="android", steps=[{"step": "transform", "status": "done"}],
                            tests=[{"name": "start", "status": "not_run", "evidence": "no device"}]))
    assert report["status"] == "partial" and "auth" in " ".join(report["reasons"])  # next-auth: login test required
    assert tool.call_read_only({"action": "plan"}) and tool.call_read_only({"action": "scaffold", "dry_run": True})
    assert not tool.call_read_only({"action": "scaffold"}) and not tool.call_read_only({"action": "report"})
    assert run(action="explode").is_error


def test_apps_command_brief_and_skills():
    from navin.agent.skills import BUILTIN_SKILLS_DIR
    from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS
    from navin.command.builtin import (
        _COMPOSER_MODE_BY_COMMAND,
        _WORKFLOW_BRIEFS,
        BUILTIN_COMMAND_SPECS,
        is_agent_turn_command,
    )

    spec = next(spec for spec in BUILTIN_COMMAND_SPECS if spec.command == "/apps")
    assert spec.accepts_args and "android" in spec.arg_hint
    _title, skills, brief = _WORKFLOW_BRIEFS["/apps"]
    names = [name.strip() for name in skills.split(",")]
    assert names[0] == "app-factory" and len([n for n in names if n.startswith("app-")]) == 13
    for name in names:
        assert (BUILTIN_SKILLS_DIR / name / "SKILL.md").is_file(), name
    assert "never turn on output: \"export\"" in brief and "Tauri 2 + Rust" in brief and "Capacitor 8" in brief
    assert _COMPOSER_MODE_BY_COMMAND["/apps"] == "agent" and is_agent_turn_command("/apps android")
    assert "appfactory" in CODE_BUILD_ALLOWED_TOOLS


def test_app_factory_skills_templates_and_policies_are_complete():
    from navin.agent.skills import BUILTIN_SKILLS_DIR

    root = BUILTIN_SKILLS_DIR / "app-factory"
    for rel in ("SKILL.md", "orchestrator/detect.md", "orchestrator/plan.md", "orchestrator/execute.md",
                "orchestrator/verify.md", "orchestrator/recover.md", "policies/security.yaml", "policies/builds.yaml",
                "templates/tauri/src-tauri/Cargo.toml.tmpl", "templates/capacitor/native/native.js.tmpl",
                "tests/android/smoke.maestro.yaml", "tests/desktop/smoke.spec.ts"):
        assert (root / rel).is_file(), rel
    for path in [*BUILTIN_SKILLS_DIR.glob("app-*/**/*"), *Path(scaffold.__file__).parent.glob("*.py")]:
        if path.is_file():
            assert not _DASHES.search(path.read_text(encoding="utf-8")), path
    for name in (p.name for p in BUILTIN_SKILLS_DIR.glob("app-*")):
        head = (BUILTIN_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        assert f"name: {name}" in head, name
    builds = scaffold.load_policy("builds")["targets"]
    assert set(builds) == set(TARGETS)
    assert all(builds[target]["artifacts"] and builds[target]["required_tests"] for target in TARGETS)
