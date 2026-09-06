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

import pytest

from navin.config import loader
from navin.providers import reasoning_control


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
