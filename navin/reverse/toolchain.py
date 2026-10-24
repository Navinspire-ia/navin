"""Reverse tool catalog: the agent installs its own analysis tools.

Every tool lands in a private folder owned by the user (no admin, no system
package manager, nothing for the user to do): portable archives are
downloaded from the official source, unpacked, and recorded in a manifest.
The agent then runs analyzers through ``exec`` so PATH, JAVA_HOME and
GHIDRA_INSTALL_DIR point at those copies.

Usage::

    python -m navin.reverse.toolchain list                 # catalog + state
    python -m navin.reverse.toolchain install --for elf    # what a target needs
    python -m navin.reverse.toolchain install ghidra jadx  # explicit tools
    python -m navin.reverse.toolchain env                  # shell exports
    python -m navin.reverse.toolchain exec -- node --version
"""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from navin.utils.proc import no_window_kwargs

NODE_VERSION = "24.11.1"
JDK_MAJOR = 21
_UA = {"User-Agent": "navin-reverse-toolchain"}


def tools_root() -> Path:
    override = os.environ.get("NAVIN_REVERSE_TOOLS")
    return Path(override) if override else Path.home() / ".navin" / "tools" / "reverse"


def host() -> tuple[str, str]:
    """(os, arch) as windows|macos|linux and x64|arm64."""
    system = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
    machine = _platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return system, arch


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    purpose: str
    targets: tuple[str, ...]          # target kinds that need it
    bins: tuple[str, ...]             # executables relative to the tool home
    requires: tuple[str, ...] = ()
    system_names: tuple[str, ...] = ()  # already installed elsewhere counts
    resolve: Callable[[str, str], tuple[str, str]] | None = field(default=None, compare=False)
    # archive: portable download | file: single file (jar) | pip: private venv
    # system: needs the OS installer (drivers/admin) | commercial: licensed, detect only
    method: str = "archive"
    pip: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ("windows", "macos", "linux")  # where `install` works
    manual: dict[str, str] = field(default_factory=dict, compare=False)  # per-OS fallback


def _node_url(system: str, arch: str) -> tuple[str, str]:
    plat = {"windows": "win", "macos": "darwin", "linux": "linux"}[system]
    ext = "zip" if system == "windows" else "tar.gz"
    return NODE_VERSION, f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-{plat}-{arch}.{ext}"


def _jdk_url(system: str, arch: str) -> tuple[str, str]:
    plat = {"windows": "windows", "macos": "mac", "linux": "linux"}[system]
    cpu = "aarch64" if arch == "arm64" else "x64"
    return (
        str(JDK_MAJOR),
        f"https://api.adoptium.net/v3/binary/latest/{JDK_MAJOR}/ga/{plat}/{cpu}/jdk/hotspot/normal/eclipse",
    )


def _github_asset(repo: str, match: Callable[[str], bool]) -> tuple[str, str]:
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=_UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    for asset in data.get("assets", []):
        if match(asset.get("name", "")):
            return data.get("tag_name", "latest"), asset["browser_download_url"]
    raise RuntimeError(f"no matching release asset in {repo}")


def _ghidra_url(system: str, arch: str) -> tuple[str, str]:
    return _github_asset(
        "NationalSecurityAgency/ghidra",
        lambda n: n.startswith("ghidra_") and "_PUBLIC_" in n and n.endswith(".zip"),
    )


def _jadx_url(system: str, arch: str) -> tuple[str, str]:
    return _github_asset("skylot/jadx", lambda n: n.startswith("jadx-") and n.endswith(".zip") and "gui" not in n)


def _apktool_url(system: str, arch: str) -> tuple[str, str]:
    return _github_asset("iBotPeaches/Apktool", lambda n: n.startswith("apktool_") and n.endswith(".jar"))


def _radare2_url(system: str, arch: str) -> tuple[str, str]:
    suffix = "-w64-arm64.zip" if arch == "arm64" else "-w64.zip"
    return _github_asset("radareorg/radare2", lambda n: n.startswith("radare2-") and n.endswith(suffix))


def _ilspy_url(system: str, arch: str) -> tuple[str, str]:
    key = {"windows": "windows_", "macos": "macos-", "linux": "linux-"}[system]
    cpu = "arm64" if arch == "arm64" else "x64"
    return _github_asset(
        "icsharpcode/ILSpy",
        lambda n: n.startswith("ILSpy_" + key) and cpu in n and n.endswith(".zip") and "selfcontained" not in n,
    )


def _retdec_url(system: str, arch: str) -> tuple[str, str]:
    label = {"macos": "macOS", "linux": "Linux"}[system]
    return _github_asset("avast/retdec", lambda n: n == f"RetDec-v5.0-{label}-Release.tar.xz")


def _win(name: str, ext: str) -> str:
    return name + ext if os.name == "nt" else name


CATALOG: dict[str, Tool] = {
    "node": Tool(
        name="node",
        purpose=f"Node {NODE_VERSION} for the REA engine (rea-agents needs >=24.11) and JS bundle analysis",
        targets=("elf", "pe", "macho", "apk", "zip", "data"),
        bins=(_win("node", ".exe"),) if os.name == "nt" else ("bin/node", "bin/npx"),
        resolve=_node_url,
    ),
    "jdk": Tool(
        name="jdk",
        purpose=f"Temurin JDK {JDK_MAJOR}, runtime for Ghidra and jadx",
        targets=("elf", "pe", "macho", "apk"),
        bins=("bin/" + _win("java", ".exe"),),
        resolve=_jdk_url,
    ),
    "ghidra": Tool(
        name="ghidra",
        purpose="Ghidra headless decompiler for native binaries (functions, call graph, pseudo-C)",
        targets=("elf", "pe", "macho"),
        bins=("support/" + ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless"),),
        requires=("jdk",),
        system_names=("analyzeHeadless",),
        resolve=_ghidra_url,
    ),
    "jadx": Tool(
        name="jadx",
        purpose="jadx decompiler for Android APK/DEX to Java sources",
        targets=("apk",),
        bins=("bin/" + ("jadx.bat" if os.name == "nt" else "jadx"),),
        requires=("jdk",),
        system_names=("jadx",),
        resolve=_jadx_url,
    ),
    "apktool": Tool(
        name="apktool",
        purpose="Apktool: decode APK resources, AndroidManifest.xml and smali",
        targets=("apk",),
        bins=("apktool.jar",),
        requires=("jdk",),
        system_names=("apktool",),
        resolve=_apktool_url,
        method="file",
    ),
    "radare2": Tool(
        name="radare2",
        purpose="radare2: scriptable low-level analysis from the CLI (r2 -qc)",
        targets=(),
        bins=("bin/" + _win("radare2", ".exe"),),
        system_names=("radare2", "r2"),
        resolve=_radare2_url,
        platforms=("windows",),
        manual={
            "macos": "brew install radare2",
            "linux": "git clone --depth 1 https://github.com/radareorg/radare2 && radare2/sys/install.sh"
                     " (or the radare2_<ver>_amd64.deb of https://github.com/radareorg/radare2/releases/latest)",
        },
    ),
    "ilspy": Tool(
        name="ilspy",
        purpose="ILSpy: decompile .NET assemblies to C# (ilspycmd for headless use)",
        targets=(),  # .NET only: install on demand once the PE has a CLR header
        bins=(_win("ILSpy", ".exe"),),
        system_names=("ilspycmd", "ILSpy"),
        resolve=_ilspy_url,
        manual={os_: "dotnet tool install --global ilspycmd (needs the .NET SDK)" for os_ in ("windows", "macos", "linux")},
    ),
    "retdec": Tool(
        name="retdec",
        purpose="RetDec: second-opinion decompiler to C for ELF/PE/Mach-O",
        targets=(),
        bins=("bin/retdec-decompiler",),
        system_names=("retdec-decompiler",),
        resolve=_retdec_url,
        platforms=("macos", "linux"),
        manual={"windows": "download RetDec-v5.0-Windows-Release.7z from https://github.com/avast/retdec/releases/tag/v5.0"
                           " and extract it with 7-Zip (stdlib cannot read .7z)"},
    ),
    "frida": Tool(
        name="frida",
        purpose="Frida: dynamic instrumentation, hook functions at runtime (only on targets you are allowed to run)",
        targets=(),
        bins=("venv/" + ("Scripts/frida.exe" if os.name == "nt" else "bin/frida"),),
        system_names=("frida",),
        method="pip",
        pip=("frida-tools",),
    ),
    "mitmproxy": Tool(
        name="mitmproxy",
        purpose="mitmproxy: inspect authorized HTTP/HTTPS traffic (mitmdump for headless capture)",
        targets=(),
        bins=("venv/" + ("Scripts/mitmdump.exe" if os.name == "nt" else "bin/mitmdump"),),
        system_names=("mitmdump",),
        method="pip",
        pip=("mitmproxy",),
    ),
    "playwright": Tool(
        name="playwright",
        purpose="Playwright: drive and observe web apps (navin's browser tool already uses it)",
        targets=(),
        bins=("venv/" + ("Scripts/playwright.exe" if os.name == "nt" else "bin/playwright"),),
        system_names=("playwright",),
        method="pip",
        pip=("playwright",),
    ),
    "wireshark": Tool(
        name="wireshark",
        purpose="Wireshark / tshark: network protocol capture and dissection",
        targets=(),
        bins=("tshark",),
        system_names=("tshark", "wireshark"),
        method="system",
        platforms=(),
        manual={
            "windows": "winget install --id WiresharkFoundation.Wireshark -e (installs Npcap for capture)",
            "macos": "brew install wireshark",
            "linux": "sudo apt-get install -y tshark",
        },
    ),
    "ida": Tool(
        name="ida",
        purpose="IDA Pro: commercial disassembler/decompiler, used when the user has a license",
        targets=(),
        bins=("idat64",),
        system_names=("idat64", "idat", "ida64", "ida"),
        method="commercial",
        platforms=(),
        manual={os_: "commercial: install your licensed IDA Pro from https://hex-rays.com and put idat on PATH"
                for os_ in ("windows", "macos", "linux")},
    ),
    "binaryninja": Tool(
        name="binaryninja",
        purpose="Binary Ninja: commercial analyzer with a Python API and BNIL",
        targets=(),
        bins=("binaryninja",),
        system_names=("binaryninja",),
        method="commercial",
        platforms=(),
        manual={os_: "commercial: install your licensed Binary Ninja from https://binary.ninja"
                " (headless API needs a Commercial/Ultimate license)" for os_ in ("windows", "macos", "linux")},
    ),
}


# --------------------------------------------------------------------------
# Manifest and detection
# --------------------------------------------------------------------------


def _manifest_path() -> Path:
    return tools_root() / "manifest.json"


def load_manifest() -> dict[str, Any]:
    try:
        return json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(data: dict[str, Any]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def installed_home(name: str) -> Path | None:
    entry = load_manifest().get(name)
    if not entry:
        return None
    home = Path(entry["home"])
    tool = CATALOG[name]
    return home if all((home / b).exists() for b in tool.bins[:1]) else None


def tool_bin(name: str) -> str | None:
    """Path of the main executable: private copy first, then the system."""
    home = installed_home(name)
    if home:
        return str(home / CATALOG[name].bins[0])
    for system_name in CATALOG[name].system_names:
        found = shutil.which(system_name)
        if found:
            return found
    return None


def _system_satisfies(name: str) -> bool:
    if name == "node":
        from navin.reverse.inventory import _parse_version, node_satisfies_rea

        node = shutil.which("node")
        if not node:
            return False
        try:
            out = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=10, **no_window_kwargs()
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return node_satisfies_rea(_parse_version(out))
    if name == "jdk":
        return False  # a private JDK avoids guessing the system Java version
    return any(shutil.which(n) for n in CATALOG[name].system_names)


def is_available(name: str) -> bool:
    return installed_home(name) is not None or _system_satisfies(name)


def needed_for(kind: str) -> list[str]:
    """Tools a target kind needs, dependencies first, missing ones only."""
    wanted = [t.name for t in CATALOG.values() if kind in t.targets]
    ordered: list[str] = []

    def add(name: str) -> None:
        for dep in CATALOG[name].requires:
            add(dep)
        if name not in ordered:
            ordered.append(name)

    for name in wanted:
        add(name)
    return [n for n in ordered if not is_available(n)]


# --------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out, length=1024 * 1024)


def _safe_members(names: list[str], root: Path) -> None:
    base = root.resolve()
    for name in names:
        target = (root / name).resolve()
        if target != base and base not in target.parents:
            raise RuntimeError(f"unsafe path in archive: {name}")


def _extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            _safe_members(zf.namelist(), dest)
            for info in zf.infolist():
                zf.extract(info, dest)
                mode = info.external_attr >> 16
                if mode and not info.is_dir():
                    # Permission bits only: no setuid/setgid/sticky from a download.
                    os.chmod(dest / info.filename, mode & 0o777)
        return
    with tarfile.open(archive) as tf:
        _safe_members(tf.getnames(), dest)
        # Names are checked above; the "tar" filter also refuses links that
        # escape dest, device files and setuid bits (names alone miss those).
        if hasattr(tarfile, "tar_filter"):
            tf.extractall(dest, filter="tar")  # noqa: S202
        else:  # pragma: no cover - Python without PEP 706
            for member in tf.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise RuntimeError(f"unsafe link in archive: {member.name}")
            tf.extractall(dest)  # noqa: S202


def _find_home(unpacked: Path, tool: Tool) -> Path:
    main = tool.bins[0]
    candidates = [unpacked, *sorted(p for p in unpacked.rglob("*") if p.is_dir())]
    for candidate in candidates:
        if (candidate / main).exists():
            return candidate
    raise RuntimeError(f"{tool.name}: {main} not found in the archive")


def _install_pip(tool: Tool, log: Callable[[str], None]) -> dict[str, Any]:
    final = tools_root() / tool.name
    venv = final / "venv"
    if not venv.exists():
        # A packaged Desktop build has no interpreter of its own (sys.executable
        # is navin.exe, without venv or pip): build the venv with the user's Python.
        from navin.python_runtime import external_python

        base = external_python()
        if not base:
            raise RuntimeError(
                f"{tool.name}: needs a Python 3 on PATH to create its private venv "
                "(Windows: winget install --id Python.Python.3.12 -e; macOS: brew install python; "
                "Linux: sudo apt-get install -y python3-venv)"
            )
        subprocess.run([base, "-m", "venv", str(venv)], check=True, **no_window_kwargs())
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    log(f"{tool.name}: pip install {' '.join(tool.pip)}")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", *tool.pip], check=True, **no_window_kwargs())
    if tool.name == "playwright":
        subprocess.run([str(final / tool.bins[0]), "install", "chromium"], check=True, **no_window_kwargs())
    out = subprocess.run([str(py), "-m", "pip", "show", tool.pip[0]], capture_output=True, text=True, **no_window_kwargs())
    version = next((ln.split(":", 1)[1].strip() for ln in out.stdout.splitlines() if ln.startswith("Version:")), "latest")
    return {"version": version, "home": str(final), "url": "https://pypi.org/project/" + tool.pip[0]}


def install(name: str, *, force: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    tool = CATALOG[name]
    system, arch = host()
    if tool.method in ("system", "commercial") or (tool.method != "pip" and system not in tool.platforms):
        hint = tool.manual.get(system, "no unattended install on this OS")
        raise RuntimeError(f"{name}: not installable by navin on {system}; {hint}")
    for dep in tool.requires:
        if not is_available(dep):
            install(dep, log=log)
    if not force and installed_home(name):
        entry = load_manifest()[name]
        log(f"{name}: already installed ({entry['version']})")
        return entry
    root = tools_root()
    root.mkdir(parents=True, exist_ok=True)
    final = root / name
    if tool.method == "pip":
        entry = _install_pip(tool, log)
    else:
        assert tool.resolve is not None
        version, url = tool.resolve(system, arch)
        log(f"{name}: downloading {version} from {url}")
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            archive = Path(tmp) / "archive"
            _download(url, archive)
            if tool.method == "file":
                found = Path(tmp) / "single"
                found.mkdir()
                shutil.move(str(archive), str(found / tool.bins[0]))
            else:
                unpacked = Path(tmp) / "unpacked"
                _extract(archive, unpacked)
                found = _find_home(unpacked, tool)
            if final.exists():
                shutil.rmtree(final)
            shutil.move(str(found), str(final))
        for rel in tool.bins:
            path = final / rel
            if path.exists() and os.name != "nt":
                path.chmod(path.stat().st_mode | 0o111)
        entry = {"version": version, "home": str(final), "url": url}
    data = load_manifest()
    data[name] = entry
    _save_manifest(data)
    log(f"{name}: ready at {final}")
    return entry


# --------------------------------------------------------------------------
# Environment for running analyzers
# --------------------------------------------------------------------------


def tool_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    extra: list[str] = []
    for name in CATALOG:
        if name == "apktool":
            continue  # run as: java -jar <home>/apktool.jar
        home = installed_home(name)
        if not home:
            continue
        bin_dir = (home / CATALOG[name].bins[0]).parent
        extra.append(str(bin_dir))
        if name == "jdk":
            env["JAVA_HOME"] = str(home)
        if name == "ghidra":
            env["GHIDRA_INSTALL_DIR"] = str(home)
    if extra:
        env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def status() -> list[dict[str, Any]]:
    rows = []
    manifest = load_manifest()
    for tool in CATALOG.values():
        rows.append({
            "name": tool.name,
            "purpose": tool.purpose,
            "targets": list(tool.targets),
            "available": is_available(tool.name),
            "private": manifest.get(tool.name),
            "bin": tool_bin(tool.name),
            "method": tool.method,
            "auto_install": tool.method == "pip" or host()[0] in tool.platforms,
            "manual": tool.manual.get(host()[0], ""),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args.pop(0) if args else "list"
    if cmd == "list":
        print(json.dumps({"root": str(tools_root()), "tools": status()}, indent=2))
        return 0
    if cmd == "install":
        names: list[str] = []
        if args[:1] == ["--for"] and len(args) > 1:
            names = needed_for(args[1])
        else:
            system = host()[0]
            names = [a for a in args if a in CATALOG] or [
                n for n, t in CATALOG.items() if t.method == "pip" or system in t.platforms
            ]
        if not names:
            print("nothing to install: every tool for this target is available")
        failed = []
        for name in names:
            try:
                install(name)
            except Exception as exc:  # noqa: BLE001 - report and keep going
                failed.append(name)
                print(f"{name}: install failed: {exc}", file=sys.stderr)
        return 1 if failed else 0
    if cmd == "env":
        env = tool_env({})
        for key in ("PATH", "JAVA_HOME", "GHIDRA_INSTALL_DIR"):
            if key in env:
                value = env[key] + (os.pathsep + "$PATH" if key == "PATH" else "")
                print(f'export {key}="{value.rstrip(os.pathsep)}"')
        return 0
    if cmd == "exec":
        if args[:1] == ["--"]:
            args = args[1:]
        if not args:
            print("usage: exec -- <command> [args]", file=sys.stderr)
            return 2
        return subprocess.call(args, env=tool_env())
    print("usage: list | install [--for <kind>] [tool...] | env | exec -- <cmd>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
