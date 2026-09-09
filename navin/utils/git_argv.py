# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Build the argv prefix that runs ``git`` where the project actually lives.

A project opened from Windows at ``\\\\wsl.localhost\\<distro>\\...`` belongs to
a Linux distribution. Windows git either fails outright on the UNC path or
refuses it as dubious ownership, so every panel that shelled out to a bare
``git -C <root>`` printed "not a git repository" over a perfectly healthy
repo - and each panel had to learn that separately.

This module is the single place that knows the routing. It matters most in the
desktop app: launched from an icon on Windows, a WSL project is the normal
case, whereas a developer running the gateway from inside the distribution
never sees the problem.

Most callers only need the prefix and use :func:`git_argv`. Callers that also
hand *paths* to git - a ``--git-dir`` outside the project, a pathspec, an
output file - have to spell those paths the way the git process will read
them, which depends on which side of the boundary it runs on;
:func:`git_route` reports that alongside the prefix.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from navin.utils import wsl


@dataclass(frozen=True)
class GitRoute:
    """Where a git command for a given project root will actually run.

    ``argv`` is the prefix, ending with ``git``. ``root`` is the project root
    *as the git process will see it*: unchanged on a local project, the
    distribution's own POSIX path when routed through ``wsl.exe``. ``distro``
    is set only in that second case.
    """

    argv: tuple[str, ...]
    root: str
    distro: str | None = None


def git_route(root: Path | str, *, platform: str | None = None) -> GitRoute | None:
    """Resolve how to reach git for *root*, or ``None`` when git is missing.

    *platform* is injectable so the Windows routing can be tested from any
    host.
    """
    system = platform if platform is not None else sys.platform
    root_str = str(root)

    if system == "win32":
        location = wsl.parse_unc(root_str)
        if location is not None and wsl.wsl_executable():
            distro = wsl.resolve_distro(location.distro) or location.distro
            return GitRoute(
                argv=(*wsl.command_prefix(distro, location.path), "git"),
                root=location.posix,
                distro=distro,
            )

    git = shutil.which("git")
    if git is None:
        return None
    return GitRoute(argv=(git, "-C", root_str), root=root_str)


def git_argv(root: Path | str, *, platform: str | None = None) -> list[str] | None:
    """Argv prefix for a git command scoped to *root*.

    Returns ``None`` when no git is reachable, which callers report as "git is
    not available" rather than "this is not a repository". *platform* is
    injectable so the Windows routing can be tested from any host.
    """
    route = git_route(root, platform=platform)
    return None if route is None else list(route.argv)
