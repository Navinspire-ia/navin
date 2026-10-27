"""The CLI agents panel names each subagent's model next to time and tokens."""

import time
from types import SimpleNamespace
from unittest import mock

from navin.tui.widgets import AgentsPanel


def _render(model: str | None) -> str:
    panel = AgentsPanel.__new__(AgentsPanel)
    now = time.monotonic()
    panel._agents = {
        "ab12": {"started": now - 5, "tokens": 3400, "label": "build", "task": "",
                 "status": "Working…", "error": False, "model": model},
    }
    panel.expanded = False
    app = SimpleNamespace(current_theme=SimpleNamespace(dark=True))
    with mock.patch.object(AgentsPanel, "app", new=app, create=True):
        return panel._render_rows(160, True, 0.0, now).plain


def test_row_shows_short_model_name_before_time_and_tokens() -> None:
    line = _render("openrouter/anthropic/claude-x").splitlines()[1]
    assert "claude-x · " in line
    assert "openrouter/" not in line
    assert line.index("claude-x") < line.index("3.4k tokens")


def test_row_without_model_keeps_time_and_tokens_only() -> None:
    line = _render(None).splitlines()[1]
    assert "3.4k tokens" in line
    assert " · ↓" in line
