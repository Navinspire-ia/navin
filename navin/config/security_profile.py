# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Named security postures for tools, without changing factory autonomy defaults.

``autonomous`` is what ``Config()`` ships with: no approvals gate, no builtin
deny list, no workspace fence. The product must not tighten that for CLI,
headless, or tests.

``assisted`` is the interactive WebUI posture: the agent still runs freely for
ordinary work, and only pauses (with session remember) before destructive
shell / file ops that match the builtin deny rules. It does not fence the
agent into the project folder: reading a spec in another repo, writing under
/tmp or running a build one directory up is ordinary development, and a card
for each of those is what made the gate feel like it asked about everything.
The workspace fence belongs to ``strict``. Applying ``assisted`` is opt-in at
WebUI setup time via :func:`ensure_webui_assisted_profile`.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from navin.config.schema import Config, ToolsConfig

SecurityProfileName = Literal["autonomous", "assisted", "strict"]


def apply_security_profile(
    tools: ToolsConfig,
    profile: SecurityProfileName | None = None,
) -> None:
    """Materialize the concrete tool knobs for ``profile`` (or ``tools.security_profile``).

    ``None`` / ``autonomous`` are no-ops so an explicit opt-out never rewrites
    the operator's other settings. ``assisted`` / ``strict`` set only the knobs
    that define the posture; they do not clear custom allow/deny patterns.
    """
    name = profile if profile is not None else tools.security_profile
    if name is None or name == "autonomous":
        return

    tools.approvals.enabled = True
    tools.approvals.remember = True
    tools.exec.builtin_deny_rules = True
    if not tools.exec.sandbox and sys.platform != "win32":
        # Prefer the native Landlock/Seatbelt helper when present; operators can
        # still switch to bwrap in config. Windows has no OS sandbox yet.
        tools.exec.sandbox = "native"

    if name == "strict":
        tools.restrict_to_workspace = True
        tools.ssrf_protection = True


def _looks_like_factory_autonomy(tools: ToolsConfig) -> bool:
    """True when the operator has not chosen a named posture yet."""
    return tools.security_profile is None and not tools.approvals.enabled


def ensure_webui_assisted_profile(config: Config) -> bool:
    """Switch a still-unset config to assisted for WebUI use.

    Returns True when the config was modified. Skips when the operator already
    chose a profile (including explicit ``autonomous``) or already turned
    approvals on, so a deliberate opt-out is never overwritten.
    """
    if not _looks_like_factory_autonomy(config.tools):
        return False
    config.tools.security_profile = "assisted"
    apply_security_profile(config.tools)
    return True
