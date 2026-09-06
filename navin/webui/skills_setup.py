"""Install missing skill CLI requirements from the WebUI ("Setup" button).

Skills can declare install recipes in their frontmatter::

    metadata: {"navin": {
        "requires": {"bins": ["gh"]},
        "install": [
            {"id": "brew", "kind": "brew", "formula": "gh", "bins": ["gh"]},
            {"id": "apt", "kind": "apt", "package": "gh", "bins": ["gh"]},
            {"id": "winget", "kind": "winget", "package": "GitHub.cli", "bins": ["gh"]}
        ]
    }}

This module turns those recipes into safe argv commands for the package
managers present on the host, exposes which options are runnable, and runs
the chosen one when the user clicks Setup.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from navin.host.packages import PkgSpec, linux_package_flavor, pacman_plan, privileged_install
from navin.utils.proc import no_window_kwargs

if TYPE_CHECKING:
    from navin.agent.skills import SkillsLoader

_INSTALL_TIMEOUT = 600
_SAFE_PACKAGE_RE = re.compile(r"^[A-Za-z0-9@._+:-]+$")
_IS_WINDOWS = sys.platform == "win32"


class SkillsSetupError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _package_of(option: dict[str, Any]) -> str:
    value = str(option.get("package") or option.get("formula") or "").strip()
    if not value or not _SAFE_PACKAGE_RE.fullmatch(value):
        return ""
    return value


def _system_install(
    manager_bin: str, install_args: list[str], package: str
) -> tuple[list[str] | None, str]:
    """Build (argv, manual_command) for a root-owned package manager."""
    plan = privileged_install(manager_bin, install_args, package)
    return plan.argv, plan.manual


def _build_command(option: dict[str, Any]) -> tuple[list[str] | None, str]:
    """Return (argv or None when not runnable here, manual command hint)."""
    kind = str(option.get("kind") or "").lower()
    package = _package_of(option)
    if not package:
        return None, ""

    if kind == "brew":
        argv = ["brew", "install", package]
        return (argv if shutil.which("brew") else None), " ".join(argv)
    if kind == "apt":
        return _system_install("apt-get", ["install", "-y"], package)
    if kind == "dnf":
        return _system_install("dnf", ["install", "-y"], package)
    if kind == "pacman":
        plan = pacman_plan(PkgSpec(pacman=package))
        return plan.argv, plan.manual
    if kind == "winget":
        argv = [
            "winget", "install", "--id", package, "-e",
            "--accept-source-agreements", "--accept-package-agreements",
        ]
        return (argv if _IS_WINDOWS and shutil.which("winget") else None), " ".join(argv)
    if kind == "choco":
        argv = ["choco", "install", package, "-y"]
        return (argv if _IS_WINDOWS and shutil.which("choco") else None), " ".join(argv)
    if kind == "npm":
        argv = ["npm", "install", "-g", package]
        return (argv if shutil.which("npm") else None), " ".join(argv)
    if kind == "pip":
        # Navin's own interpreter is not a candidate: a packaged build has no pip
        # and its unpack directory is discarded at exit.
        from navin.python_runtime import external_python

        interpreter = external_python()
        argv = [interpreter, "-m", "pip", "install", package] if interpreter else None
        return argv, f"pip install {package}"
    if kind == "uv":
        argv = ["uv", "tool", "install", package]
        return (argv if shutil.which("uv") else None), " ".join(argv)
    return None, ""


def _preferred_setup_kind() -> str | None:
    """Package-manager kind that should lead Setup option lists on this host."""
    if sys.platform == "win32":
        return "winget"
    if sys.platform == "darwin":
        return "brew"
    if sys.platform.startswith("linux"):
        flavor = linux_package_flavor()
        if flavor in {"omarchy", "arch"}:
            return "pacman"
        if flavor == "deb":
            return "apt"
        if flavor == "rpm":
            return "dnf"
    return None


def _setup_kind_rank(kind: str, preferred: str | None = None) -> int:
    """0 for the host-matching kind, 1 otherwise."""
    if preferred is None:
        preferred = _preferred_setup_kind()
    return 0 if preferred and kind.lower() == preferred else 1


def _order_setup_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable reorder: host-matching kind first; do not drop options."""
    preferred = _preferred_setup_kind()
    return [
        option
        for _, option in sorted(
            enumerate(options),
            key=lambda item: (
                _setup_kind_rank(str(item[1].get("kind") or ""), preferred),
                item[0],
            ),
        )
    ]


def _install_options(loader: SkillsLoader, name: str) -> list[dict[str, Any]]:
    meta = loader._get_skill_meta(name)
    raw = meta.get("install")
    if not isinstance(raw, list):
        return []
    return [option for option in raw if isinstance(option, dict)]


def skill_setup_payload(loader: SkillsLoader, name: str) -> dict[str, Any]:
    """Safe description of setup options for the skill detail payload."""
    options: list[dict[str, Any]] = []
    for index, option in enumerate(_install_options(loader, name)):
        argv, manual = _build_command(option)
        if not manual:
            continue
        options.append(
            {
                "id": str(option.get("id") or option.get("kind") or index),
                "kind": str(option.get("kind") or ""),
                "label": str(option.get("label") or manual),
                "runnable": argv is not None,
                "command": manual,
            }
        )
    ordered = _order_setup_options(options)
    return {
        "can_setup": any(option["runnable"] for option in ordered),
        "options": ordered,
    }


def run_skill_setup(
    workspace_path: Path,
    name: str,
    *,
    option_id: str | None = None,
    disabled_skills: set[str] | None = None,
) -> dict[str, Any]:
    """Run one install recipe and re-check the skill's requirements."""
    from navin.agent.skills import SkillsLoader

    loader = SkillsLoader(workspace_path, disabled_skills=disabled_skills)
    if loader.load_skill(name) is None:
        raise SkillsSetupError("skill not found", status=404)

    requirements = loader.get_skill_requirements(name)
    if not requirements["missing_bins"]:
        if requirements["missing_env"]:
            raise SkillsSetupError(
                "missing environment variables cannot be installed: "
                + ", ".join(requirements["missing_env"])
            )
        return {"ok": True, "message": "requirements already satisfied", "output": ""}

    chosen: tuple[list[str], str] | None = None
    manuals: list[str] = []
    install_options = _install_options(loader, name)
    for option in _order_setup_options(install_options):
        argv, manual = _build_command(option)
        if manual:
            manuals.append(manual)
        identifier = str(
            option.get("id")
            or option.get("kind")
            or next(i for i, item in enumerate(install_options) if item is option)
        )
        if option_id is not None and identifier != option_id:
            continue
        if argv is not None and chosen is None:
            chosen = (argv, manual)
    if chosen is None:
        hint = f" Try manually: {manuals[0]}" if manuals else ""
        raise SkillsSetupError(
            "no runnable install option on this host (missing package manager or sudo rights)."
            + hint,
            status=409,
        )

    argv, manual = chosen
    logger.info("Skills setup: running {}", manual)
    try:
        result = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_INSTALL_TIMEOUT,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillsSetupError("install timed out", status=504) from exc
    except OSError as exc:
        raise SkillsSetupError(f"failed to run installer: {exc}", status=500) from exc

    output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    output = output.strip()[-6000:]
    if result.returncode != 0:
        raise SkillsSetupError(
            f"install failed (exit {result.returncode}): {output or manual}",
            status=500,
        )

    still_missing = loader.get_skill_requirements(name)["missing_bins"]
    if still_missing:
        return {
            "ok": False,
            "message": (
                "installed, but still missing on PATH: "
                + ", ".join(still_missing)
                + ". A restart of Navin (or a new login session) may be required."
            ),
            "output": output,
        }
    return {"ok": True, "message": "requirements installed", "output": output}
