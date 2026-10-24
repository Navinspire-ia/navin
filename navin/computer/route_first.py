# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prefer an installed CLI over driving the screen.

``route(intent)`` looks for a known CLI that fulfils the intent and returns a
suggested command; otherwise it falls back to screen control.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

# (keywords, binaries tried in order, command template)
_CATALOG: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("open url", "open website", "browse"), ("xdg-open", "open", "start"), "{bin} {arg}"),
    (("github", "pull request", "issue"), ("gh",), "gh {arg}"),
    (("gitlab", "merge request"), ("glab",), "glab {arg}"),
    (("docker", "container"), ("docker",), "docker {arg}"),
    (("kubernetes", "kubectl", "pod"), ("kubectl",), "kubectl {arg}"),
    (("clipboard", "copy"), ("wl-copy", "xclip", "pbcopy"), "{bin}"),
    (("screenshot",), ("grim", "scrot", "screencapture"), "{bin}"),
    (("notify", "notification"), ("notify-send", "osascript"), "{bin} {arg}"),
    (("ssh", "remote"), ("ssh",), "ssh {arg}"),
    (("aws",), ("aws",), "aws {arg}"),
)


@dataclass(frozen=True, slots=True)
class Route:
    kind: str  # "cli" or "screen"
    binary: str = ""
    command: str = ""
    reason: str = ""


def route(intent: str, arg: str = "", *, which=shutil.which) -> Route:
    text = intent.lower()
    for words, binaries, template in _CATALOG:
        if any(w in text for w in words):
            for b in binaries:
                if which(b):
                    cmd = template.format(bin=b, arg=arg).strip()
                    return Route("cli", b, cmd, f"{b} is installed and fulfils this intent")
    return Route("screen", reason="no installed CLI matches; fall back to screen control")
