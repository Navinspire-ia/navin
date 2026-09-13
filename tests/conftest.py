# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Global test isolation.

Every test writes its config to a per-test temporary path. Without this, any
code path reaching ``save_config()`` during a test run silently overwrote the
developer's real ``~/.navin/config.json`` with test stubs (device "fp",
activation token "tok"), corrupting the local license state and breaking the
account connect flow of the running gateway.

Tests that need their own path (e.g. PersistedConfigTest) still call
``set_config_path`` themselves; this fixture only guarantees the default is
never the real user config.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The bundled interpreter ships its own copy of the navin package through a
# PyInstaller FrozenImporter that sits in sys.meta_path ahead of PathFinder,
# so sys.path alone cannot make the workspace checkout win. Intercept navin*
# imports and route them to this repository instead.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


class _WorkspaceNavinFinder:
    """Resolve navin* from the workspace checkout, never from the bundle."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "navin" and not fullname.startswith("navin."):
            return None
        import importlib.machinery

        if fullname == "navin":
            return importlib.machinery.PathFinder.find_spec(fullname, [_REPO_ROOT])
        parent = fullname.rpartition(".")[0]
        parent_mod = sys.modules.get(parent)
        parent_path = getattr(parent_mod, "__path__", None)
        if parent_path is None:
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, list(parent_path))


for _stale in [m for m in sys.modules if m == "navin" or m.startswith("navin.")]:
    del sys.modules[_stale]
sys.meta_path.insert(0, _WorkspaceNavinFinder())

import pytest  # noqa: E402

from navin.config import loader  # noqa: E402
from navin.providers import reasoning_control  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_config_path(tmp_path):
    previous = loader._current_config_path
    loader.set_config_path(tmp_path / "config.json")
    try:
        yield
    finally:
        loader._current_config_path = previous


@pytest.fixture(autouse=True)
def _desks_offline(monkeypatch):
    """Desk research never reaches the web from a test; tests inject their own search."""
    monkeypatch.setenv("NAVIN_MARKETING_OFFLINE", "1")
    monkeypatch.setenv("NAVIN_LEADS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _isolated_reasoning_negotiation(tmp_path, monkeypatch):
    """What one test's fake endpoint "refused" must not teach the next test.

    The negotiator persists its learned ladder under ~/.navin/cache; point it
    at a per-test file and start every test with an empty memory.
    """
    monkeypatch.setenv(
        "NAVIN_REASONING_NEGOTIATION_FILE", str(tmp_path / "reasoning_negotiation.json")
    )
    reasoning_control._STORE_CACHE = None
    try:
        yield
    finally:
        reasoning_control._STORE_CACHE = None
