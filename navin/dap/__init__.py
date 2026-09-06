"""Debug Adapter Protocol client for the Code workbench (Phase 5)."""

from navin.dap.manager import DebugManager, get_debug_manager
from navin.dap.session import DebugSession, DebugSessionError

__all__ = [
    "DebugManager",
    "DebugSession",
    "DebugSessionError",
    "get_debug_manager",
]
