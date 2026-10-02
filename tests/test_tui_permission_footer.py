# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI footer names the permission mode, so nobody grants rights turn
after turn without knowing ``/permission auto`` exists."""

from __future__ import annotations

from types import SimpleNamespace

from navin.config.loader import load_config, save_config
from navin.tui.app import NavinApp
from navin.webui.exec_policy_api import apply_approval_mode


def _label() -> str:
    holder = SimpleNamespace(_PERMISSION_LABELS=NavinApp._PERMISSION_LABELS)
    return NavinApp._permission_label(holder)


def test_the_footer_follows_the_saved_mode() -> None:
    config = load_config()
    apply_approval_mode(config, "risky")
    save_config(config)
    assert _label() == "Ask before deleting"
    config = load_config()
    apply_approval_mode(config, "autonomous")
    save_config(config)
    assert _label() == "Full access"
