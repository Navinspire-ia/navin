"""Lazy Montage package installs (HyperFrames by default for backward compat)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from navin.montage import MONTAGE_HOME
from navin.montage.install import install_hyperframes, install_package
from navin.toolchains import is_stale, record_build

LogFn = Callable[[str], None]


def _installed_by_another_build() -> bool:
    """Whether ~/.navin/montage was filled by a different Navin build.

    Reinstalling the app never touches this directory, so without the check the
    setup keeps answering "already ready" and a machine stays on the toolchain
    its very first install pulled.
    """
    from navin.montage.detect import hyperframes_bin

    return is_stale(MONTAGE_HOME, installed=hyperframes_bin() is not None)


async def run_setup(
    *,
    force: bool = False,
    package: str | None = None,
    config: Any | None = None,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """Install a Montage package.

    *package*:
    - omitted / ``hyperframes`` - previous default (HTML composition)
    - ``core`` - builtin stock status + ffmpeg detect (no heavy download)
    - ``ffmpeg`` / ``remotion`` / ``stock-*`` - targeted install
    - ``all`` - sequential install of non-optional packages that still need action
    """
    pid = (package or "hyperframes").strip().lower() or "hyperframes"
    if pid == "all":
        return await _run_setup_all(force=force, config=config, on_log=on_log)
    if pid == "hyperframes":
        # An upgrade must refresh the renderer even when one is already there.
        refresh = force or _installed_by_another_build()
        if refresh and not force and on_log:
            on_log("Toolchain was installed by another Navin build - refreshing.\n")
        # Keep the historical return shape expected by older callers/tests.
        result = await install_hyperframes(force=refresh, on_log=on_log)
        if result.get("ok"):
            record_build(MONTAGE_HOME)
        return {
            "ok": result.get("ok", False),
            "home": result.get("home"),
            "hyperframes": result.get("path"),
            "chrome": result.get("chrome"),
            "logs": result.get("logs") or [],
            "skipped": result.get("skipped", False),
            "error": result.get("error"),
            "fix": result.get("fix"),
            "package": "hyperframes",
        }
    return await install_package(pid, force=force, config=config, on_log=on_log)


async def _run_setup_all(
    *,
    force: bool,
    config: Any | None,
    on_log: LogFn | None,
) -> dict[str, Any]:
    from navin.webui.montage_api import packages_status

    status = packages_status(config)
    targets = [
        str(row.get("id") or "").strip().lower()
        for row in (status.get("items") or [])
        if row.get("needs_action")
        and row.get("installable")
        and not row.get("optional")
        and not row.get("ui_hidden")
        and str(row.get("id") or "").strip().lower()
        in {"ffmpeg", "hyperframes"}
    ]
    # Preserve a stable install order.
    order = {"ffmpeg": 0, "hyperframes": 1}
    targets = sorted(set(targets), key=lambda name: order.get(name, 99))
    # Only HyperFrames is refreshed after an upgrade: ffmpeg is a third-party
    # static build that owes nothing to the Navin version, and re-downloading
    # it on every release would cost the user a lot for nothing.
    refresh = not targets and _installed_by_another_build()
    if refresh:
        targets = ["hyperframes"]
        force = True
        if on_log:
            on_log("Toolchain was installed by another Navin build - refreshing.\n")
    if not targets:
        if on_log:
            on_log("Nothing to install - toolchain already ready.\n")
        return {
            "ok": True,
            "package": "all",
            "skipped": True,
            "results": [],
            "note": "Nothing to install - toolchain already ready.",
        }

    results: list[dict[str, Any]] = []
    for name in targets:
        if on_log:
            on_log(f"\n=== Installing {name} ===\n")
        results.append(
            await install_package(name, force=force, config=config, on_log=on_log)
        )
    ok = all(bool(row.get("ok")) for row in results)
    if ok:
        record_build(MONTAGE_HOME)
    return {
        "ok": ok,
        "package": "all",
        "skipped": False,
        "results": results,
        "error": None if ok else "one_or_more_failed",
        "fix": None if ok else "See console output for the failing package.",
        "logs": [
            chunk
            for row in results
            for chunk in (row.get("logs") or [])
        ],
    }


def render_setup_result(result: dict[str, Any]) -> str:
    package = result.get("package") or "hyperframes"
    lines = [
        "Montage setup:",
        f"  package: {package}",
        f"  ok: {result.get('ok')}",
        f"  skipped: {result.get('skipped')}",
    ]
    if result.get("home"):
        lines.append(f"  home: {result.get('home')}")
    if result.get("hyperframes") or result.get("path"):
        lines.append(f"  path: {result.get('hyperframes') or result.get('path')}")
    if result.get("chrome"):
        lines.append(f"  chrome: {result.get('chrome')}")
    if result.get("error"):
        lines.append(f"  error: {result['error']}")
    if result.get("fix"):
        lines.append(f"  Fix: {result['fix']}")
    if result.get("note"):
        lines.append(f"  note: {result['note']}")
    logs = result.get("logs") or []
    if logs:
        lines.append("")
        lines.append("Logs (tail):")
        for chunk in logs[-3:]:
            for line in str(chunk).splitlines()[-20:]:
                lines.append(f"  | {line}")
    from navin.montage.detect import find_ffmpeg

    which = find_ffmpeg()
    lines.append("")
    lines.append(f"ffmpeg: {which or 'missing (install via package=ffmpeg)'}")
    return "\n".join(lines)
