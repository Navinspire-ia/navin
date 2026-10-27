# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What a target needs on this machine, and the official way to install it.

Read-only: it runs ``--version`` probes with a short timeout and never installs
anything. Installing goes through the agent's shell, after the user agreed.
Minimums follow the official requirements: Capacitor 8 needs Node 22+, JDK 21,
compile/target SDK 36 and Xcode 26; Tauri 2 needs Rust 1.77.2+, MSVC and
WebView2 on Windows, WebKitGTK 4.1 on Linux.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from navin.appfactory.compat import host_can_build, host_info
from navin.appfactory.targets import Target, get_target
from navin.utils.proc import no_window_kwargs

Which = Callable[[str], "str | None"]
Run = Callable[[list[str]], str]

_DOCS = {
    "capacitor": "https://capacitorjs.com/docs/getting-started/environment-setup",
    "tauri": "https://v2.tauri.app/start/prerequisites/",
}


def _run(cmd: list[str]) -> str:
    """stdout+stderr of a version probe, '' when it cannot run within 15 s."""
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15, check=False, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") + (done.stderr or "")


def _ver(text: str) -> str:
    found = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    return ".".join(part for part in found.groups() if part is not None) if found else ""


def _at_least(version: str, minimum: str) -> bool:
    if not minimum:
        return True
    if not version:
        return False
    have = [int(x) for x in version.split(".")]
    need = [int(x) for x in minimum.split(".")]
    have += [0] * (len(need) - len(have))
    return have[: len(need)] >= need


def _install(system: str, linux_family: str, commands: Mapping[str, str]) -> str:
    if system == "Linux":
        return commands.get(f"linux-{linux_family}") or commands.get("linux", "")
    return commands.get(system.lower() if system != "Darwin" else "macos", "")


def linux_family(read: Callable[[], str] | None = None) -> str:
    """'debian' or 'fedora' from /etc/os-release (debian when unknown)."""
    try:
        text = read() if read else Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "debian"
    lowered = text.lower()
    if any(word in lowered for word in ("fedora", "rhel", "centos", "rocky", "almalinux")):
        return "fedora"
    return "debian"


def android_sdk(env: Mapping[str, str], home: Path, system: str) -> Path | None:
    """The Android SDK in use: ANDROID_HOME, the usual Studio folder, or the one Navin prepared."""
    candidates = [env.get("ANDROID_HOME"), env.get("ANDROID_SDK_ROOT")]
    if system == "Windows":
        local = Path(env.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        candidates += [str(local / "Android" / "Sdk"), str(local / "Navin" / "android-platform-tools")]
    elif system == "Darwin":
        candidates.append(str(home / "Library" / "Android" / "sdk"))
    else:
        candidates.append(str(home / "Android" / "Sdk"))
    # The SDK the device preview prepares (navin.mobile.bootstrap.navin_sdk_root).
    candidates.append(str(home / ".navin" / "android-platform-tools"))
    for raw in candidates:
        if raw and Path(raw).is_dir() and any((Path(raw) / sub).is_dir() for sub in ("platform-tools", "cmdline-tools", "platforms")):
            return Path(raw)
    return None


def _tool(tool_id: str, label: str, *, present: bool, version: str = "", minimum: str = "", required: bool = True,
          install: str = "", note: str = "") -> dict[str, Any]:
    ok = present and _at_least(version, minimum) if minimum else present
    return {"id": tool_id, "label": label, "required": required, "present": present, "version": version,
            "minimum": minimum, "ok": ok, "install": "" if ok else install, "note": note}


def _probe(which: Which, run: Run, binary: str, *args: str) -> tuple[bool, str]:
    path = which(binary)
    if not path:
        return False, ""
    return True, _ver(run([path, *args]))


def doctor(target_name: str, *, host: dict[str, Any] | None = None, which: Which = shutil.which, run: Run = _run,
           env: Mapping[str, str] | None = None, home: Path | None = None, family: str | None = None) -> dict[str, Any]:
    """Toolchain report for *target_name* on this host. Never installs anything."""
    target: Target = get_target(target_name)
    host = host or host_info()
    env = os.environ if env is None else env
    home = home or Path.home()
    system = str(host.get("system") or "Linux")
    family = family or (linux_family() if system == "Linux" else "")
    tools: list[dict[str, Any]] = []

    present, version = _probe(which, run, "node", "--version")
    node_min = "22" if target.engine == "capacitor" else "18"
    tools.append(_tool("node", "Node.js", present=present, version=version, minimum=node_min, install=_install(system, family, {
        "linux": "curl -fsSL https://fnm.vercel.app/install | bash && fnm install 22",
        "windows": "winget install --id OpenJS.NodeJS.LTS -e",
        "macos": "brew install node@22",
    })))

    if target.engine == "capacitor" and target.id == "android":
        present, version = False, ""
        java_home = env.get("JAVA_HOME")
        java = str(Path(java_home) / "bin" / ("java.exe" if system == "Windows" else "java")) if java_home else which("java")
        if java and (Path(java).is_file() or which("java")):
            present, version = True, _ver(re.sub(r"^.*?version\s+\"?", "", run([java, "-version"]), flags=re.S))
        tools.append(_tool("jdk", "JDK 21", present=present, version=version, minimum="21", install=_install(system, family, {
            "linux-debian": "sudo apt-get install -y openjdk-21-jdk",
            "linux-fedora": "sudo dnf install -y java-21-openjdk-devel",
            "windows": "winget install --id EclipseAdoptium.Temurin.21.JDK -e",
            "macos": "brew install --cask temurin@21",
        }), note="JAVA_HOME must point to it when several JDKs are installed"))
        sdk = android_sdk(env, home, system)
        sdk_install = _install(system, family, {
            "linux": "download the command line tools from https://developer.android.com/studio#command-tools into ~/Android/Sdk/cmdline-tools/latest, export ANDROID_HOME=~/Android/Sdk, then: sdkmanager \"platform-tools\" \"platforms;android-36\" \"build-tools;36.0.0\" && yes | sdkmanager --licenses",
            "windows": "winget install --id Google.AndroidStudio -e, then in Android Studio > SDK Manager install Android 16 (API 36) and the platform tools",
            "macos": "brew install --cask android-commandlinetools && sdkmanager \"platform-tools\" \"platforms;android-36\" \"build-tools;36.0.0\" && yes | sdkmanager --licenses",
        })
        platforms = sorted((sdk / "platforms").glob("android-*")) if sdk else []
        api = max((int(m.group(1)) for p in platforms if (m := re.match(r"android-(\d+)", p.name))), default=0)
        tools.append(_tool("android-sdk", "Android SDK (API 36)", present=sdk is not None, version=str(api) if api else "",
                           minimum="36", install=sdk_install, note=str(sdk) if sdk else "ANDROID_HOME is not set"))
        adb = which("adb") or (str(sdk / "platform-tools" / ("adb.exe" if system == "Windows" else "adb")) if sdk else "")
        adb_ok = bool(adb) and (which("adb") is not None or Path(adb).is_file())
        tools.append(_tool("adb", "adb (device tests)", present=adb_ok, version=_ver(run([adb, "version"])) if adb_ok else "",
                           install="sdkmanager \"platform-tools\"", note="installs and launches the app on an emulator or a phone"))
        present, version = _probe(which, run, "maestro", "--version")
        tools.append(_tool("maestro", "Maestro (UI flows)", present=present, version=version, required=False,
                           install="curl -fsSL \"https://get.maestro.mobile.dev\" | bash",
                           note="optional: without it the flows run through the device preview"))
    elif target.id == "ios":
        present, version = _probe(which, run, "xcodebuild", "-version")
        tools.append(_tool("xcode", "Xcode 26", present=present, version=version, minimum="26",
                           install="install Xcode 26 from the App Store, then: sudo xcode-select -s /Applications/Xcode.app && xcodebuild -runFirstLaunch"))
    else:
        present, version = _probe(which, run, "rustc", "--version")
        tools.append(_tool("rust", "Rust 1.77.2+", present=present, version=version, minimum="1.77.2", install=_install(system, family, {
            "linux": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
            "macos": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y",
            "windows": "winget install --id Rustlang.Rustup -e && rustup default stable-msvc",
        })))
        present, version = _probe(which, run, "cargo", "--version")
        tools.append(_tool("cargo", "Cargo", present=present, version=version, install="installed with Rust (rustup)"))
        if system == "Windows" and target.id == "windows":
            vswhere = Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
            msvc = ""
            if vswhere.is_file():
                msvc = run([str(vswhere), "-latest", "-products", "*", "-requires",
                            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationVersion"]).strip()
            tools.append(_tool("msvc", "Microsoft C++ Build Tools", present=bool(msvc) or bool(which("cl")), version=_ver(msvc),
                               install="winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override \"--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended\""))
            webview = any(
                (Path(env.get(base, default)) / "Microsoft" / "EdgeWebView" / "Application").is_dir()
                for base, default in (("ProgramFiles(x86)", r"C:\Program Files (x86)"), ("ProgramFiles", r"C:\Program Files"))
            )
            tools.append(_tool("webview2", "WebView2", present=webview, required=False,
                               install="winget install --id Microsoft.EdgeWebView2Runtime -e",
                               note="built into Windows 10 (1803+) and 11; the installer can also bundle it"))
        elif system == "Linux" and target.id in {"ubuntu", "centos", "appimage"}:
            pkg_config = which("pkg-config")
            webkit = _ver(run([pkg_config, "--modversion", "webkit2gtk-4.1"])) if pkg_config else ""
            tools.append(_tool("webkit2gtk", "WebKitGTK 4.1 and build libraries", present=bool(webkit), version=webkit,
                               install=_install(system, family, {
                                   "linux-debian": "sudo apt-get update && sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev",
                                   "linux-fedora": "sudo dnf check-update; sudo dnf install -y webkit2gtk4.1-devel openssl-devel curl wget file libappindicator-gtk3-devel librsvg2-devel libxdo-devel && sudo dnf group install -y \"c-development\"",
                               })))
            if target.id == "appimage":
                tools.append(_tool("file", "file (AppImage bundler)", present=bool(which("file")), install=_install(system, family, {
                    "linux-debian": "sudo apt-get install -y file", "linux-fedora": "sudo dnf install -y file"})))
        elif system == "Darwin" and target.id == "macos":
            present = bool(which("xcrun"))
            tools.append(_tool("xcode-clt", "Xcode command line tools", present=present, version=_ver(run(["xcrun", "--version"])) if present else "",
                               install="xcode-select --install"))

    local = host_can_build(target, host)
    missing = [tool for tool in tools if tool["required"] and not tool["ok"]]
    if not local:
        reason = f"{target.label} packages are built on {' or '.join(target.hosts)}; this machine runs {system}"
        if host.get("wsl") and target.id == "windows":
            reason += " inside WSL: run the build from a Windows terminal in the same folder, or on the CI runner"
    else:
        reason = ""
    return {
        "target": target.id,
        "engine": "Capacitor 8" if target.engine == "capacitor" else "Tauri 2 + Rust",
        "host": host,
        "can_build_here": local,
        "host_note": reason,
        "ci_runner": target.ci_runner,
        "tools": tools,
        "missing": [tool["id"] for tool in missing],
        "ready": local and not missing,
        "docs": _DOCS[target.engine],
        "rule": "never install without the user's agreement; run the install commands one by one and re-run doctor",
    }
