# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-platform installers for Montage packages (Windows / macOS / Linux)."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.host.packages import PkgSpec, pacman_plan, privileged_install
from navin.montage import MONTAGE_HOME
from navin.montage.detect import (
    detect_toolchain,
    ffmpeg_user_bin,
    find_chrome,
    find_ffmpeg,
    hyperframes_bin,
)
from navin.montage.packages import (
    FFMPEG_PACKAGE,
    HYPERFRAMES_PACKAGE,
    REMOTION_PACKAGE,
    get_package,
)
from navin.utils.proc import no_window_kwargs
from navin.utils.task_progress import emit_task_progress

LogFn = Callable[[str], None]

_NPM_TIMEOUT_S = 900
_SYS_TIMEOUT_S = 900
_FFMPEG_DL_TIMEOUT_S = 600
_IS_WINDOWS = sys.platform == "win32"
_IS_DARWIN = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")


def remotion_home() -> Path:
    return MONTAGE_HOME.expanduser() / "remotion"


def remotion_bin() -> Path | None:
    home = remotion_home()
    for name in ("remotion", "remotion.cmd", "remotion.ps1"):
        candidate = home / "node_modules" / ".bin" / name
        if candidate.is_file():
            return candidate
    which = shutil.which("remotion")
    return Path(which) if which else None


def _emit_log(on_log: LogFn | None, text: str) -> None:
    if on_log is None or not text:
        return
    on_log(text if text.endswith("\n") else f"{text}\n")


async def _progress(
    label: str,
    *,
    percent: float | None = None,
    on_log: LogFn | None = None,
) -> None:
    _emit_log(on_log, label)
    await emit_task_progress(
        label=label,
        percent=percent,
        indeterminate=percent is None,
        step=label,
    )


def _sudo_prefix() -> list[str] | None:
    if _IS_WINDOWS:
        return []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if not shutil.which("sudo"):
        return None
    try:
        probe = subprocess.run(  # noqa: S603
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return ["sudo", "-n"] if probe.returncode == 0 else None


def _platform_tag() -> str:
    if _IS_WINDOWS:
        return "win32"
    if _IS_DARWIN:
        return "darwin"
    return "linux"


def _ffmpeg_static_url() -> tuple[str, str] | None:
    """Return (url, archive_kind) for a user-local static build, if supported."""
    machine = platform.machine().lower()
    if _IS_LINUX:
        if machine in {"x86_64", "amd64"}:
            return (
                "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
                "tar.xz",
            )
        if machine in {"aarch64", "arm64"}:
            return (
                "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-arm64-static.tar.xz",
                "tar.xz",
            )
        return None
    if _IS_WINDOWS:
        return (
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "zip",
        )
    if _IS_DARWIN:
        # Prefer brew on macOS; these are the no-root fallbacks.
        if machine in {"x86_64", "amd64"}:
            return ("https://evermeet.cx/ffmpeg/getrelease/zip", "zip")
        if machine in {"arm64", "aarch64"}:
            # evermeet ships x86_64 only, so Apple Silicon had no fallback at
            # all: without Homebrew the user could never get ffmpeg, and every
            # feature behind it stayed dead.
            return ("https://www.osxexperts.net/ffmpeg71arm.zip", "zip")
        return None
    return None


def _ffprobe_static_url() -> tuple[str, str] | None:
    """Separate ffprobe archive, for platforms whose ffmpeg archive lacks it.

    Only macOS needs this: the Linux and Windows static archives already carry
    ffprobe, which ``_extract_ffmpeg_binary`` copies alongside ffmpeg.
    """
    if not _IS_DARWIN:
        return None
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return ("https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip", "zip")
    if machine in {"arm64", "aarch64"}:
        return ("https://www.osxexperts.net/ffprobe71arm.zip", "zip")
    return None


def ffmpeg_install_plan() -> dict[str, Any]:
    """Pick the best runnable recipe for this OS.

    Prefer OS package managers when they can run non-interactively. Otherwise
    fall back to a user-local static binary under ~/.navin/montage/bin so the
    Studio Install button always has something to do when ffmpeg is missing.
    """
    plat = _platform_tag()
    options: list[dict[str, Any]] = []
    for recipe in FFMPEG_PACKAGE.recipes:
        if recipe.platforms and plat not in recipe.platforms:
            continue
        argv: list[str] | None = None
        manual = ""
        manager_available = False
        if recipe.kind == "winget":
            manual = (
                f"winget install --id {recipe.package} -e "
                "--accept-source-agreements --accept-package-agreements"
            )
            manager_available = bool(_IS_WINDOWS and shutil.which("winget"))
            if manager_available:
                argv = manual.split()
        elif recipe.kind == "choco":
            manual = f"choco install {recipe.package} -y"
            manager_available = bool(_IS_WINDOWS and shutil.which("choco"))
            if manager_available:
                argv = ["choco", "install", recipe.package, "-y"]
        elif recipe.kind == "brew":
            manual = f"brew install {recipe.package}"
            manager_available = bool(shutil.which("brew"))
            if manager_available:
                argv = ["brew", "install", recipe.package]
        elif recipe.kind in {"apt", "dnf", "yum"}:
            manager = {
                "apt": "apt-get",
                "dnf": "dnf",
                "yum": "yum",
            }[recipe.kind]
            plan = privileged_install(manager, ["install", "-y"], recipe.package)
            manual = plan.manual
            manager_available = plan.manager_available
            argv = plan.argv
        elif recipe.kind == "pacman":
            plan = pacman_plan(PkgSpec(pacman=recipe.package))
            manual = plan.manual
            manager_available = plan.manager_available
            argv = plan.argv
        elif recipe.kind == "user-local":
            dest = ffmpeg_user_bin()
            manual = f"user-local install → {dest}"
            manager_available = _ffmpeg_static_url() is not None
            if manager_available:
                # Sentinel argv; install_ffmpeg handles the download path.
                argv = ["__navin_ffmpeg_user_local__"]
        options.append(
            {
                "id": recipe.id,
                "kind": recipe.kind,
                "label": recipe.label or manual,
                "manual": manual,
                "manager_available": manager_available,
                "runnable": argv is not None,
                "argv": argv,
            }
        )
    # Prefer a real package manager as the hint even when sudo needs a password.
    # User-local stays the no-sudo fallback for install_ffmpeg, not selected.
    pkg_runnable = next(
        (o for o in options if o["runnable"] and o["kind"] != "user-local"),
        None,
    )
    pkg_available = next(
        (o for o in options if o.get("manager_available") and o["kind"] != "user-local"),
        None,
    )
    user_local = next(
        (o for o in options if o["runnable"] and o["kind"] == "user-local"),
        None,
    )
    preferred = pkg_runnable or pkg_available or user_local
    path = find_ffmpeg()
    return {
        "package": "ffmpeg",
        "present": bool(path),
        "path": path,
        "options": options,
        "selected": preferred,
        "installable": preferred is not None and (
            bool(preferred.get("runnable")) or bool(preferred.get("manager_available"))
        ),
    }


def _run_argv(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    on_log: LogFn | None = None,
    timeout: float = _SYS_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    if on_log is None:
        return subprocess.run(  # noqa: S603
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            **no_window_kwargs(),
        )
    _emit_log(on_log, "$ " + " ".join(argv))
    proc = subprocess.Popen(  # noqa: S603
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **no_window_kwargs(),
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            chunks.append(line)
            on_log(line)
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        with proc.stdout:
            rest = proc.stdout.read()
            if rest:
                chunks.append(rest)
                on_log(rest)
        raise
    out = "".join(chunks)
    return subprocess.CompletedProcess(argv, returncode, out, "")


def _copy_shallowest(root: Path, program: str, dest: Path, *, required: bool) -> bool:
    """Copy the shallowest *program* found under *root* to *dest*."""
    name = f"{program}.exe" if _IS_WINDOWS else program
    matches = [p for p in root.rglob(name) if p.is_file()]
    if not matches:
        if required:
            raise RuntimeError(f"{name} binary missing from downloaded archive")
        return False
    # Prefer the shallowest match (main binary, not helpers).
    matches.sort(key=lambda p: len(p.parts))
    shutil.copy2(matches[0], dest)
    if not _IS_WINDOWS:
        dest.chmod(dest.stat().st_mode | 0o111)
    return True


def _extract_ffmpeg_binary(
    archive: Path, kind: str, dest: Path, program: str = "ffmpeg"
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="navin-ffmpeg-") as tmp:
        tmp_path = Path(tmp)
        if kind == "tar.xz":
            with tarfile.open(archive, "r:xz") as tar:
                tar.extractall(tmp_path)  # noqa: S202 - trusted upstream archive
        elif kind == "zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp_path)
        else:
            raise RuntimeError(f"unsupported archive kind: {kind}")
        _copy_shallowest(tmp_path, program, dest, required=True)
        if program == "ffmpeg":
            # The Linux/Windows archives carry ffprobe too: grab it while the
            # archive is open so stream probing is frame precise, not
            # header-parse precise. Missing is fine - ffmpeg alone still works.
            probe_dest = dest.with_name("ffprobe.exe" if _IS_WINDOWS else "ffprobe")
            _copy_shallowest(tmp_path, "ffprobe", probe_dest, required=False)


def _install_ffprobe_user_local(
    ffmpeg_dest: Path, *, on_log: LogFn | None = None
) -> None:
    """Best-effort ffprobe next to a user-local ffmpeg (macOS separate archive).

    On Linux and Windows ffprobe already came out of the ffmpeg archive. On
    macOS it is a second download; failing it only degrades duration probing
    to ffmpeg's header parse, so it must never fail the ffmpeg install.
    """
    probe_dest = ffmpeg_dest.with_name("ffprobe.exe" if _IS_WINDOWS else "ffprobe")
    if probe_dest.is_file():
        return
    probe_info = _ffprobe_static_url()
    if probe_info is None:
        return
    probe_url, probe_kind = probe_info
    _emit_log(on_log, f"Downloading {probe_url}")
    try:
        with tempfile.TemporaryDirectory(prefix="navin-ffprobe-dl-") as tmp:
            archive = Path(tmp) / f"ffprobe.{probe_kind.replace('.', '_')}"
            with urllib.request.urlopen(  # noqa: S310
                probe_url, timeout=_FFMPEG_DL_TIMEOUT_S
            ) as resp:
                archive.write_bytes(resp.read())
            _extract_ffmpeg_binary(archive, probe_kind, probe_dest, program="ffprobe")
        _emit_log(on_log, f"ffprobe installed: {probe_dest}")
    except Exception as exc:  # noqa: BLE001 - ffprobe is a precision upgrade only
        _emit_log(
            on_log,
            f"ffprobe skipped ({exc}); duration probing falls back to ffmpeg",
        )


def _install_ffmpeg_user_local_sync(*, on_log: LogFn | None = None) -> dict[str, Any]:
    logs: list[str] = []
    url_info = _ffmpeg_static_url()
    if url_info is None:
        msg = (
            "No user-local ffmpeg build for this OS/arch. "
            "Install via your package manager or https://ffmpeg.org/download.html"
        )
        _emit_log(on_log, msg)
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "no_static_build",
            "fix": msg,
            "logs": logs,
        }
    url, kind = url_info
    dest = ffmpeg_user_bin()
    logs.append(f"Downloading {url}")
    _emit_log(on_log, f"Downloading {url}")
    try:
        with tempfile.TemporaryDirectory(prefix="navin-ffmpeg-dl-") as tmp:
            archive = Path(tmp) / f"ffmpeg.{kind.replace('.', '_')}"
            with urllib.request.urlopen(url, timeout=_FFMPEG_DL_TIMEOUT_S) as resp:  # noqa: S310
                archive.write_bytes(resp.read())
            _emit_log(on_log, f"Extracting {kind} archive → {dest}")
            _extract_ffmpeg_binary(archive, kind, dest)
    except Exception as exc:  # noqa: BLE001 - surface any download/extract failure
        _emit_log(on_log, f"User-local ffmpeg install failed: {exc}")
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "user_local_failed",
            "fix": f"User-local ffmpeg install failed: {exc}",
            "logs": logs,
        }
    _install_ffprobe_user_local(dest, on_log=on_log)
    path = find_ffmpeg()
    ok = bool(path)
    logs.append(f"Installed: {dest}")
    _emit_log(on_log, f"Installed: {dest}")
    return {
        "ok": ok,
        "package": "ffmpeg",
        "skipped": False,
        "path": path,
        "kind": "user-local",
        "fix": None if ok else f"Installed to {dest} but not detected",
        "logs": logs,
    }


async def install_ffmpeg(
    *,
    force: bool = False,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    logs: list[str] = []
    existing = find_ffmpeg()
    if existing and not force:
        msg = f"Already available: {existing}"
        _emit_log(on_log, msg)
        return {
            "ok": True,
            "package": "ffmpeg",
            "skipped": True,
            "path": existing,
            "logs": [msg],
        }
    plan = ffmpeg_install_plan()
    selected = plan.get("selected")
    # Prefer a runnable package-manager recipe; else user-local download.
    runnable = next(
        (
            o
            for o in plan.get("options") or []
            if o.get("runnable") and o.get("kind") != "user-local"
        ),
        None,
    )
    if runnable is None:
        runnable = next(
            (o for o in plan.get("options") or [] if o.get("runnable")),
            None,
        )
    selected = runnable or selected
    if not selected:
        manuals = [o["manual"] for o in plan["options"] if o.get("manual")]
        fix = (
            "Install ffmpeg manually, then re-check: "
            + (manuals[0] if manuals else "https://ffmpeg.org/download.html")
        )
        _emit_log(on_log, fix)
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "no_package_manager",
            "fix": fix,
            "options": plan["options"],
            "logs": logs,
        }

    if selected.get("kind") == "user-local" or (
        not selected.get("argv") and selected.get("manager_available")
    ):
        # No passwordless sudo: install user-local when possible, else show sudo cmd.
        user_local = next(
            (
                o
                for o in plan.get("options") or []
                if o.get("kind") == "user-local" and o.get("runnable")
            ),
            None,
        )
        if user_local is not None and (
            selected.get("kind") == "user-local" or not selected.get("argv")
        ):
            await _progress(
                "Installing ffmpeg (user-local, no sudo)…",
                percent=20,
                on_log=on_log,
            )
            result = await asyncio.to_thread(
                _install_ffmpeg_user_local_sync, on_log=on_log
            )
            if result.get("ok"):
                await _progress("FFmpeg ready", percent=100, on_log=on_log)
            elif selected.get("manager_available") and selected.get("manual"):
                result["fix"] = (
                    f"{result.get('fix')}. Or run manually: {selected['manual']}"
                )
                _emit_log(on_log, result["fix"])
            return result
        fix = (
            "Passwordless sudo unavailable. Run this in a terminal, then refresh: "
            + (selected.get("manual") or "https://ffmpeg.org/download.html")
        )
        _emit_log(on_log, fix)
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "needs_manual_sudo",
            "fix": fix,
            "options": plan["options"],
            "logs": logs,
        }

    cmd = selected.get("manual") or " ".join(selected.get("argv") or [])
    if cmd:
        _emit_log(on_log, f"$ {cmd}")
    await _progress(
        f"Installing ffmpeg via {selected['kind']}…",
        percent=20,
        on_log=on_log,
    )
    try:
        completed = await asyncio.to_thread(
            _run_argv, list(selected["argv"]), on_log=on_log
        )
    except subprocess.TimeoutExpired:
        _emit_log(on_log, "ffmpeg install timed out")
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "timeout",
            "fix": selected["manual"],
            "logs": logs,
        }
    except OSError as exc:
        _emit_log(on_log, f"ffmpeg exec failed: {exc}")
        return {
            "ok": False,
            "package": "ffmpeg",
            "error": "exec_failed",
            "fix": str(exc),
            "logs": logs,
        }
    if completed.stdout:
        logs.append(completed.stdout[-2000:])
    if completed.stderr:
        logs.append(completed.stderr[-2000:])
    path = find_ffmpeg()
    if completed.returncode != 0 or not path:
        # Package manager failed (e.g. missing sudo interactive) → user-local.
        user_local = next(
            (
                o
                for o in plan.get("options") or []
                if o.get("kind") == "user-local" and o.get("runnable")
            ),
            None,
        )
        if user_local is not None:
            logs.append("Package manager failed - falling back to user-local install.")
            await _progress(
                "Installing ffmpeg (user-local fallback)…",
                percent=55,
                on_log=on_log,
            )
            result = await asyncio.to_thread(
                _install_ffmpeg_user_local_sync, on_log=on_log
            )
            result["logs"] = [*logs, *(result.get("logs") or [])]
            if result.get("ok"):
                await _progress("FFmpeg ready", percent=100, on_log=on_log)
            return result
    ok = completed.returncode == 0 and bool(path)
    if ok:
        await _progress("FFmpeg ready", percent=100, on_log=on_log)
    elif selected.get("manual"):
        _emit_log(on_log, f"Fix: {selected['manual']}")
    return {
        "ok": ok,
        "package": "ffmpeg",
        "skipped": False,
        "path": path,
        "returncode": completed.returncode,
        "fix": None if ok else selected["manual"],
        "logs": logs,
    }


def _write_hyperframes_package_json(home: Path) -> None:
    package = {
        "name": "navin-montage-hyperframes",
        "private": True,
        "description": "Lazy HyperFrames install managed by Navin Montage",
        "dependencies": {"hyperframes": "^0.7.102"},
    }
    (home / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )


def _write_remotion_package_json(home: Path) -> None:
    # Keep Remotion isolated from HyperFrames (OpenMontage #481 heavy deps).
    package = {
        "name": "navin-montage-remotion",
        "private": True,
        "description": "Optional Remotion CLI install managed by Navin Montage",
        "dependencies": {
            "remotion": "^4.0.0",
            "@remotion/cli": "^4.0.0",
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        },
    }
    (home / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )


def _npm_install(
    home: Path,
    npm: str,
    env: dict[str, str],
    *,
    on_log: LogFn | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_argv(
        [npm, "install", "--no-fund", "--no-audit"],
        cwd=home,
        env=env,
        on_log=on_log,
        timeout=_NPM_TIMEOUT_S,
    )


async def install_hyperframes(
    *,
    force: bool = False,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    home = MONTAGE_HOME.expanduser()
    home.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    out: dict[str, Any] = {
        "ok": False,
        "package": "hyperframes",
        "home": str(home),
        "path": None,
        "chrome": None,
        "logs": logs,
        "skipped": False,
        "size_mb": HYPERFRAMES_PACKAGE.size_mb,
    }
    await _progress("Checking Node/npm…", percent=10, on_log=on_log)
    tc = detect_toolchain()
    if not tc.node or not tc.npm:
        out["error"] = "node_missing"
        out["fix"] = (
            "Install Node.js 22+ (https://nodejs.org) for Windows, macOS, or Linux, "
            "then retry HyperFrames setup."
        )
        _emit_log(on_log, out["fix"])
        return out
    existing = hyperframes_bin()
    if existing is not None and not force:
        out["ok"] = True
        out["skipped"] = True
        out["path"] = str(existing)
        out["chrome"] = tc.chrome
        logs.append(f"Already installed: {existing}")
        _emit_log(on_log, f"Already installed: {existing}")
        return out

    chrome = find_chrome()
    out["chrome"] = chrome
    env = os.environ.copy()
    if chrome:
        env["PUPPETEER_EXECUTABLE_PATH"] = chrome
        env["PUPPETEER_SKIP_DOWNLOAD"] = "1"
        logs.append(f"Reusing system Chrome: {chrome}")
        _emit_log(on_log, f"Reusing system Chrome: {chrome}")
    else:
        note = (
            "No system Chrome/Edge - first render may download Chromium (~200-300 MB)."
        )
        logs.append(note)
        _emit_log(on_log, note)

    await _progress("Writing HyperFrames package.json…", percent=30, on_log=on_log)
    _write_hyperframes_package_json(home)
    await _progress("npm install hyperframes (lazy)…", percent=15, on_log=on_log)
    try:
        completed = await asyncio.to_thread(
            _npm_install, home, tc.npm, env, on_log=on_log
        )
    except subprocess.TimeoutExpired:
        out["error"] = "timeout"
        out["fix"] = "Retry; check network and disk (≥2 GB free)."
        _emit_log(on_log, out["fix"])
        return out
    except OSError as exc:
        out["error"] = "npm_exec"
        out["fix"] = str(exc)
        _emit_log(on_log, str(exc))
        return out
    if completed.stdout:
        logs.append(completed.stdout[-2000:])
    if completed.stderr:
        logs.append(completed.stderr[-2000:])
    if completed.returncode != 0:
        out["error"] = "npm_failed"
        out["fix"] = "See logs; fix Node/npm, free disk, retry."
        _emit_log(on_log, out["fix"])
        return out
    hf = hyperframes_bin()
    if hf is None:
        out["error"] = "binary_missing"
        out["fix"] = "Inspect ~/.navin/montage/node_modules and retry with force=1."
        _emit_log(on_log, out["fix"])
        return out
    out["ok"] = True
    out["path"] = str(hf)
    logs.append(f"Installed: {hf}")
    await _progress("HyperFrames ready", percent=100, on_log=on_log)
    return out


async def install_remotion(
    *,
    force: bool = False,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """Optional heavy React composition toolchain (isolated folder)."""
    home = remotion_home()
    home.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    out: dict[str, Any] = {
        "ok": False,
        "package": "remotion",
        "home": str(home),
        "path": None,
        "logs": logs,
        "skipped": False,
        "optional": True,
        "size_mb": REMOTION_PACKAGE.size_mb,
    }
    await _progress("Checking Node/npm for Remotion…", percent=10, on_log=on_log)
    tc = detect_toolchain()
    if not tc.node or not tc.npm:
        out["error"] = "node_missing"
        out["fix"] = "Install Node.js 22+ then retry Remotion setup."
        _emit_log(on_log, out["fix"])
        return out
    existing = remotion_bin()
    if existing is not None and not force:
        out["ok"] = True
        out["skipped"] = True
        out["path"] = str(existing)
        logs.append(f"Already installed: {existing}")
        _emit_log(on_log, f"Already installed: {existing}")
        return out

    env = os.environ.copy()
    await _progress("Writing Remotion package.json…", percent=30, on_log=on_log)
    _write_remotion_package_json(home)
    await _progress(
        f"npm install remotion (~{REMOTION_PACKAGE.size_mb} MB, optional)…",
        percent=10,
        on_log=on_log,
    )
    try:
        completed = await asyncio.to_thread(
            _npm_install, home, tc.npm, env, on_log=on_log
        )
    except subprocess.TimeoutExpired:
        out["error"] = "timeout"
        out["fix"] = "Remotion is large; retry on a stable network with free disk."
        _emit_log(on_log, out["fix"])
        return out
    except OSError as exc:
        out["error"] = "npm_exec"
        out["fix"] = str(exc)
        _emit_log(on_log, str(exc))
        return out
    if completed.stdout:
        logs.append(completed.stdout[-2000:])
    if completed.stderr:
        logs.append(completed.stderr[-2000:])
    if completed.returncode != 0:
        out["error"] = "npm_failed"
        out["fix"] = "See logs; Remotion is optional - HyperFrames + ffmpeg still work."
        _emit_log(on_log, out["fix"])
        return out
    path = remotion_bin()
    if path is None:
        out["error"] = "binary_missing"
        out["fix"] = "Inspect ~/.navin/montage/remotion/node_modules and retry."
        _emit_log(on_log, out["fix"])
        return out
    readme = home / "README.md"
    if not readme.is_file():
        readme.write_text(
            "# Navin Montage - Remotion (optional)\n\n"
            "Installed on demand. Not required for HyperFrames or ffmpeg packaging.\n"
            "Keep compositions under marketing/montage/compositions-remotion/.\n",
            encoding="utf-8",
        )
    out["ok"] = True
    out["path"] = str(path)
    logs.append(f"Installed: {path}")
    await _progress("Remotion ready", percent=100, on_log=on_log)
    return out


def _chromium_install_argv() -> list[str]:
    """Argv that makes Playwright fetch a Chromium for this installation.

    A packaged build has no interpreter to name, so it goes through
    ``navin python``; ``python_command()`` already encodes that difference.
    """
    from navin.python_runtime import python_command

    return [*python_command(), "-m", "playwright", "install", "chromium"]


async def install_chromium(
    *,
    force: bool = False,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """Fetch a Chromium for the browser tool, unless one is already usable.

    Unlike ffmpeg, Chromium is not bundled: it would add 150-300 MB per
    platform to installers that already carry a webview. It is fetched here
    instead, from the Studio's Install button, so the browser tool stops being
    the one capability whose only remedy was a command in an error message.
    """
    from navin.agent.tools.browser import _installed_chromium

    logs: list[str] = []
    existing = _installed_chromium()
    if existing and not force:
        await _progress("Chromium ready", percent=100, on_log=on_log)
        return {
            "ok": True,
            "package": "chromium",
            "path": existing,
            "logs": [f"Using the Chromium already installed: {existing}"],
        }

    argv = _chromium_install_argv()
    await _progress("Downloading Chromium (~150 MB)…", percent=20, on_log=on_log)
    try:
        proc = await asyncio.to_thread(
            _run_argv, argv, on_log=on_log, timeout=_SYS_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "package": "chromium",
            "error": str(exc),
            "fix": "Install Google Chrome, Edge or Chromium, or set NAVIN_CHROMIUM to an existing one.",
            "logs": logs,
        }

    if proc.stdout:
        logs.extend(proc.stdout.splitlines())
    if proc.stderr:
        logs.extend(proc.stderr.splitlines())

    found = _installed_chromium()
    if proc.returncode != 0 and not found:
        return {
            "ok": False,
            "package": "chromium",
            "error": f"{' '.join(argv)} exited with {proc.returncode}",
            # Linux headless Chromium needs system libraries the download does
            # not carry, and that failure looks nothing like a missing browser.
            "fix": (
                "On Linux, missing system libraries are the usual cause: rerun with "
                "--with-deps. Otherwise install Google Chrome, Edge or Chromium, or "
                "set NAVIN_CHROMIUM to an existing one."
            ),
            "logs": logs,
        }
    await _progress("Chromium ready", percent=100, on_log=on_log)
    return {
        "ok": bool(found),
        "package": "chromium",
        "path": found,
        "fix": None if found else "Installed but not detected; set NAVIN_CHROMIUM.",
        "logs": logs,
    }


async def install_package(
    package_id: str,
    *,
    force: bool = False,
    config: Any | None = None,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """Install one package by id (stock is builtin - status only)."""
    pid = (package_id or "").strip().lower()
    if pid in {"", "core", "light"}:
        # Builtin stock + ffmpeg detect; never pull heavy npm stacks.
        from navin.montage.stock import stock_status

        stock = stock_status(config, probe=False)
        ff = ffmpeg_install_plan()
        return {
            "ok": True,
            "package": "core",
            "skipped": True,
            "stock": stock,
            "ffmpeg": {
                "present": ff["present"],
                "path": ff["path"],
                "installable": bool(ff.get("selected")),
            },
            "note": (
                "Stock clients are builtin. Install ffmpeg / HyperFrames / Remotion "
                "explicitly when needed (lazy)."
            ),
        }
    if pid.startswith("stock"):
        from navin.montage.stock import stock_status

        stock = stock_status(config, probe=True)
        return {
            "ok": stock.get("ready_any", False) or any(
                p.get("configured") for p in (stock.get("providers") or {}).values()
            ),
            "package": pid,
            "skipped": True,
            "tier": "builtin",
            "stock": stock,
            "fix": (
                None
                if stock.get("ready_any")
                else "Add free developer API keys (Pexels / Unsplash / Pixabay) in env or Settings."
            ),
        }
    if pid == "ffmpeg":
        return await install_ffmpeg(force=force, on_log=on_log)
    if pid == "chromium":
        return await install_chromium(force=force, on_log=on_log)
    if pid == "hyperframes":
        return await install_hyperframes(force=force, on_log=on_log)
    if pid == "remotion":
        return await install_remotion(force=force, on_log=on_log)
    if get_package(pid) is None:
        return {
            "ok": False,
            "package": pid,
            "error": "unknown_package",
            "fix": (
                "Use package=core|ffmpeg|hyperframes|remotion|"
                "stock-pexels|stock-unsplash|stock-pixabay"
            ),
        }
    return {
        "ok": False,
        "package": pid,
        "error": "unsupported",
        "fix": f"No installer for {pid}",
    }
