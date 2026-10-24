"""Exhaustive static inventory for Reverse mode.

The agent used to read ``strings`` output by eye and listed a fraction of the
dependencies and CLI options of a target. This module extracts them by
pattern, with counts, so the report can say "26 of 26" instead of guessing.
Nothing here executes the target.

Usage::

    python -m navin.reverse.inventory <target>          # JSON inventory
    python -m navin.reverse.inventory --doctor [target] # engine diagnostic
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs

REA_PACKAGE = "rea-agents@6.5.0"
# rea-agents 6.5.0 engines: ^22.19.0 || ^24.11.0 || >=26.0.0
_REA_NODE_RANGES = (((22, 19, 0), 23), ((24, 11, 0), 25), ((26, 0, 0), None))
_MAX_BYTES = 256 * 1024 * 1024
_MIN_STRING = 4

_CRATE_RE = re.compile(rb"(?:^|[/\\])([a-z0-9][a-z0-9_-]*?)-(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)[/\\]src[/\\]")
_GO_MOD_RE = re.compile(rb"\bdep\t([\w.\-/]+)\t(v[\w.\-+]+)")
_NPM_RE = re.compile(rb"node_modules[/\\]((?:@[\w.\-]+[/\\])?[\w.\-]+)[/\\]")
_PY_DIST_RE = re.compile(rb"([A-Za-z0-9_.]+)-(\d+(?:\.\d+)+)\.dist-info")
_RUSTC_RE = re.compile(rb"rustc[/ -](\d+\.\d+\.\d+)")
_FLAG_RE = re.compile(rb"(?<![\w-])(--[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![\w-])")
_SHORT_FLAG_RE = re.compile(rb"(?:^|[\s,\[(])(-[A-Za-z0-9])(?=[\s,=\])]|$)")
_SO_RE = re.compile(rb"\b(lib[\w+.\-]+\.so(?:\.\d+)*)\b")
_DLL_RE = re.compile(rb"\b([\w\-]+\.dll)\b", re.IGNORECASE)
_URL_RE = re.compile(rb"\bhttps?://[\w.\-]+(?::\d+)?(?:/[\w.\-/%?=&#~+]*)?")


def _strings(data: bytes, minimum: int = _MIN_STRING) -> list[bytes]:
    return re.findall(rb"[\x20-\x7e\t]{%d,}" % minimum, data)


def _kind(data: bytes, path: Path) -> str:
    head = data[:8]
    if head.startswith(b"\x7fELF"):
        return "elf"
    if head.startswith(b"MZ"):
        return "pe"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
        return "macho"
    if head.startswith(b"PK"):
        return "apk" if path.suffix.lower() == ".apk" else "zip"
    return "data"


def _sorted_unique(items) -> list[str]:
    return sorted({item for item in items if item})


def _read_target(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()[:_MAX_BYTES]
    kind = _kind(raw, path)
    if kind in ("apk", "zip"):
        chunks = [raw]
        try:
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist()[:5000]:
                    chunks.append(info.filename.encode("utf-8", "replace") + b"\n")
                    if info.file_size <= 32 * 1024 * 1024:
                        chunks.append(archive.read(info))
        except (zipfile.BadZipFile, OSError):
            pass
        return b"\n".join(chunks), kind
    return raw, kind


def build_inventory(target: Path | str) -> dict[str, Any]:
    """Pattern-based inventory of one file. Never runs the target."""
    path = Path(target)
    data, kind = _read_target(path)
    blob = b"\n".join(_strings(data))

    crates: dict[str, str] = {}
    for name, version in _CRATE_RE.findall(blob):
        crates.setdefault(name.decode(), version.decode())
    go_modules = {m.decode(): v.decode() for m, v in _GO_MOD_RE.findall(blob)}
    npm = _sorted_unique(m.decode().replace("\\", "/") for m in _NPM_RE.findall(blob))
    python = {n.decode(): v.decode() for n, v in _PY_DIST_RE.findall(blob)}
    long_flags = _sorted_unique(f.decode() for f in _FLAG_RE.findall(blob))
    short_flags = _sorted_unique(f.decode() for f in _SHORT_FLAG_RE.findall(blob))
    rustc = _sorted_unique(v.decode() for v in _RUSTC_RE.findall(blob))
    libs = _sorted_unique(s.decode() for s in _SO_RE.findall(blob))
    if kind == "pe":
        libs = _sorted_unique([*libs, *(d.decode().lower() for d in _DLL_RE.findall(blob))])
    urls = _sorted_unique(u.decode() for u in _URL_RE.findall(blob))[:200]

    dependencies = {
        "rust_crates": [{"name": n, "version": v} for n, v in sorted(crates.items())],
        "go_modules": [{"name": n, "version": v} for n, v in sorted(go_modules.items())],
        "npm_packages": npm,
        "python_dists": [{"name": n, "version": v} for n, v in sorted(python.items())],
        "native_libraries": libs,
    }
    return {
        "target": str(path),
        "kind": kind,
        "size_bytes": path.stat().st_size,
        "toolchain": {"rustc": rustc},
        "dependencies": dependencies,
        "cli_options": {"long": long_flags, "short": short_flags},
        "urls": urls,
        "counts": {
            "rust_crates": len(crates),
            "go_modules": len(go_modules),
            "npm_packages": len(npm),
            "python_dists": len(python),
            "native_libraries": len(libs),
            "long_options": len(long_flags),
            "short_options": len(short_flags),
            "urls": len(urls),
        },
        "method": "static pattern extraction over printable strings; target not executed",
    }


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def node_satisfies_rea(version: tuple[int, int, int] | None) -> bool:
    if version is None:
        return False
    for low, below_major in _REA_NODE_RANGES:
        if version >= low and (below_major is None or version[0] < below_major):
            return True
    return False


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


# System package-manager fallbacks, checked against the real registries:
# Ghidra has no winget package and is a Homebrew formula (not a cask) that
# pulls openjdk@21 itself; jadx is a formula too. The private toolchain
# (python -m navin.reverse.toolchain install <tool>) stays the primary path.
_INSTALL = {
    "node": {
        "windows": "winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements",
        "macos": "brew install node",
        "linux": (
            "curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash"
            " && . \"$HOME/.nvm/nvm.sh\" && nvm install 24 && nvm alias default 24"
        ),
    },
    "ghidra": {
        "windows": (
            "winget install --id EclipseAdoptium.Temurin.21.JDK -e --accept-source-agreements"
            " --accept-package-agreements, then unzip the ghidra_*_PUBLIC_*.zip asset of"
            " https://github.com/NationalSecurityAgency/ghidra/releases/latest and set GHIDRA_INSTALL_DIR"
        ),
        "macos": "brew install ghidra",
        "linux": (
            "sudo apt-get install -y openjdk-21-jdk unzip, then unzip the ghidra_*_PUBLIC_*.zip asset of"
            " https://github.com/NationalSecurityAgency/ghidra/releases/latest and export GHIDRA_INSTALL_DIR=<folder>"
        ),
    },
    "jadx": {
        "windows": (
            "winget install --id EclipseAdoptium.Temurin.21.JDK -e --accept-source-agreements"
            " --accept-package-agreements, then unzip the jadx-*.zip asset of"
            " https://github.com/skylot/jadx/releases/latest and add its bin folder to PATH"
        ),
        "macos": "brew install jadx",
        "linux": (
            "sudo apt-get install -y openjdk-21-jdk unzip, then unzip the jadx-*.zip asset of"
            " https://github.com/skylot/jadx/releases/latest and add its bin folder to PATH"
        ),
    },
}


def _which_jadx() -> str | None:
    from navin.reverse import toolchain

    return toolchain.tool_bin("jadx")


def _which_ghidra() -> str | None:
    from navin.reverse import toolchain

    private = toolchain.installed_home("ghidra")
    if private:
        return str(private / toolchain.CATALOG["ghidra"].bins[0])
    for name in ("analyzeHeadless", "ghidraRun", "ghidra"):
        found = shutil.which(name)
        if found:
            return found
    home = os.environ.get("GHIDRA_INSTALL_DIR")
    if home:
        for rel in ("support/analyzeHeadless", "support/analyzeHeadless.bat"):
            candidate = Path(home) / rel
            if candidate.exists():
                return str(candidate)
    return None


def _node_version() -> str | None:
    from navin.reverse import toolchain

    private = toolchain.installed_home("node")
    node = str(private / toolchain.CATALOG["node"].bins[0]) if private else shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, **no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def engine_diagnostic(kind: str | None = None) -> dict[str, Any]:
    """What Reverse can use on this machine, with the fix for each gap."""
    platform = _platform_key()
    node_text = _node_version()
    node_ok = node_satisfies_rea(_parse_version(node_text or ""))
    ghidra = _which_ghidra()
    native = kind in ("elf", "pe", "macho")
    problems: list[dict[str, str]] = []
    if not node_ok:
        problems.append({
            "component": "node",
            "why": f"{REA_PACKAGE} needs Node ^22.19 || ^24.11 || >=26, found {node_text or 'none'}",
            "install": "python -m navin.reverse.toolchain install node",
            "system_install": _INSTALL["node"][platform],
            "auto_install": True,
        })
    if native and not ghidra:
        problems.append({
            "component": "ghidra",
            "why": "native binary: REA decompiles through Ghidra; without it only strings, headers and imports are available",
            "install": "python -m navin.reverse.toolchain install ghidra",
            "system_install": _INSTALL["ghidra"][platform],
            "auto_install": True,
        })
    jadx = _which_jadx() if kind == "apk" else None
    if kind == "apk" and not jadx:
        problems.append({
            "component": "jadx",
            "why": "APK: jadx decompiles DEX to Java sources; without it only the manifest, strings and native libs are available",
            "install": "python -m navin.reverse.toolchain install jadx",
            "system_install": _INSTALL["jadx"][platform],
            "auto_install": True,
        })
    return {
        "platform": platform,
        "rea": {"package": REA_PACKAGE, "node": node_text, "node_ok": node_ok},
        "decompiler": {"ghidra": ghidra, "required_for_target": native},
        "static_tools": {name: bool(shutil.which(name)) for name in ("file", "strings", "readelf", "objdump", "jadx", "rizin")},
        "ready": not problems,
        "auto_install": [p["component"] for p in problems if p["auto_install"]],
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--doctor":
        kind = None
        if len(args) > 1 and Path(args[1]).is_file():
            kind = _kind(Path(args[1]).read_bytes()[:8], Path(args[1]))
        print(json.dumps(engine_diagnostic(kind), indent=2))
        return 0
    if len(args) != 1 or not Path(args[0]).is_file():
        print("usage: python -m navin.reverse.inventory <file> | --doctor [file]", file=sys.stderr)
        return 2
    print(json.dumps(build_inventory(args[0]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
