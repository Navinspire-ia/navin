# PyInstaller spec for the fully self-contained Navin sidecar.
#
# It embeds Python, Navin, all Python dependencies, the WebUI build, templates
# and skills; it never downloads source code or requires Python on the target
# machine. Two output shapes from the same analysis:
#
# - default: a single self-extracting file (dist/navin-portable). Kept for
#   Linux, where the extraction lands on tmpfs and costs almost nothing.
# - NAVIN_PYI_ONEDIR=1: a flat directory (dist/navin-dist/) that starts
#   instantly. This is what the Windows and macOS desktop bundles ship:
#   the one-file form re-extracted ~400 MB on every launch, and the
#   antivirus re-scan on top made the gateway take a minute to boot.
#
# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# What both builds contain, so the portable and the installed application cannot
# end up with different capabilities.
sys.path.insert(0, SPECPATH)
import bundle_contents


def _winpty_helpers():
    """The pywinpty executables that ``collect_dynamic_libs`` leaves behind.

    ``conpty.dll`` does not host the pseudo-console itself: it launches
    ``OpenConsole.exe``, and the legacy backend launches ``winpty-agent.exe``.
    Collecting only ``.dll`` and ``.pyd`` shipped a build where every terminal
    opened, emitted its two initial escape sequences, and died with
    ``STATUS_CONTROL_C_EXIT`` before printing a prompt.
    """
    import winpty

    package = Path(winpty.__file__).parent
    found = []
    for name in ("OpenConsole.exe", "winpty-agent.exe"):
        helper = package / name
        if helper.is_file():
            found.append((str(helper), "winpty"))
    return found


hiddenimports = (
    collect_submodules("navin")
    + collect_submodules("navin.channels")
    + collect_submodules("navin.agent.tools")
    + collect_submodules("navin.providers")
    # Studio desks (Career, Leads, Marketing, Tenders, Trading). Same store on
    # Linux / Windows / macOS; named so a later narrowing of collect_submodules
    # ("navin") cannot drop them from the sidecar.
    + collect_submodules("navin.career")
    + collect_submodules("navin.leads")
    + collect_submodules("navin.marketing")
    + collect_submodules("navin.tenders")
    + collect_submodules("navin.trading")
    # stdlib C-extension needed by wcwidth/prompt_toolkit; PyInstaller can
    # miss it on some Windows Python installs.
    + ["unicodedata"]
    + collect_submodules("wcwidth")
    + bundle_contents.hidden_imports()
)

# `navin-cli` (Textual full-screen client). Widgets and themes are imported
# lazily by name, so PyInstaller cannot see them from navin.tui alone.
try:
    import textual  # noqa: F401

    _HAS_TEXTUAL = True
except ImportError:  # pragma: no cover - optional at build time
    _HAS_TEXTUAL = False
if _HAS_TEXTUAL:
    hiddenimports += collect_submodules("textual")

binaries = bundle_contents.tool_binaries()
if sys.platform == "win32":
    # pywinpty (ConPTY) backs the WebUI interactive terminals on Windows.
    hiddenimports += collect_submodules("winpty")
    binaries += collect_dynamic_libs("winpty")
    binaries += _winpty_helpers()

datas = (
    collect_data_files("navin", include_py_files=False)
    + copy_metadata("prompt_toolkit")
    + collect_data_files("wcwidth", include_py_files=False)
    + bundle_contents.template_data()
    + bundle_contents.script_data()
    + bundle_contents.extra_data()
    + bundle_contents.ffmpeg_data()
    + bundle_contents.typescript_data()
)
if _HAS_TEXTUAL:
    datas += collect_data_files("textual", include_py_files=False)
    datas += copy_metadata("textual")
updater = Path("../windows/NavinUpdater.exe")
if sys.platform == "win32" and updater.is_file():
    datas.append((str(updater), "."))

a = Analysis(
    ["navin_entry.py"],
    pathex=["../.."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["hooks"],
    hooksconfig={"matplotlib": {"backends": "Agg"}},
    runtime_hooks=[],
    # urllib3.contrib.emscripten needs a browser ``js`` module - never ships
    # outside WASM. Keeping it out avoids a noisy ModuleNotFoundError during
    # collect_submodules (yt-dlp / urllib3 hooks).
    excludes=["tkinter", "PIL.ImageTk", "urllib3.contrib.emscripten"],
    noarchive=False,
)

# python's _ssl and cryptography's rust binding can be linked against two
# different OpenSSL installs; PyInstaller keeps one per basename, and keeping
# the older one shipped a Mac build whose WebUI never started.
a.binaries = bundle_contents.prefer_newest_openssl(a.binaries)

pyz = PYZ(a.pure)

if os.environ.get("NAVIN_PYI_ONEDIR") == "1":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="navin",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        icon="../windows/navin.ico",
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="navin-dist",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Navin-Portable" if sys.platform == "win32" else "navin-portable",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        icon="../windows/navin.ico",
    )
