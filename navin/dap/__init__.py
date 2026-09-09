# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Debug Adapter Protocol client for the Code workbench (Phase 5)."""

from navin.dap.manager import DebugManager, get_debug_manager
from navin.dap.session import DebugSession, DebugSessionError

__all__ = [
    "DebugManager",
    "DebugSession",
    "DebugSessionError",
    "get_debug_manager",
]
