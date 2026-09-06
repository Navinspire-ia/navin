"""Montage studio: project marketing analysis + lazy HyperFrames toolchain."""

from __future__ import annotations

from pathlib import Path

from navin.montage.profiles import RENDER_PROFILES, get_profile, list_profiles

MONTAGE_HOME = Path.home() / ".navin" / "montage"
MONTAGE_NODE_MODULES = MONTAGE_HOME / "node_modules"
MONTAGE_BIN = MONTAGE_HOME / "node_modules" / ".bin"
WORKSPACE_MONTAGE_DIR = "marketing/montage"

__all__ = [
    "MONTAGE_HOME",
    "MONTAGE_NODE_MODULES",
    "MONTAGE_BIN",
    "WORKSPACE_MONTAGE_DIR",
    "RENDER_PROFILES",
    "get_profile",
    "list_profiles",
]
