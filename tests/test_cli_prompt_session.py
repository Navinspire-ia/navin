"""The interactive prompt must not reserve blank rows below the input line."""

import sys
import types

import navin.cli.commands as commands


def test_prompt_session_reserves_no_menu_space(monkeypatch, tmp_path):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_pt = types.ModuleType("prompt_toolkit")
    fake_pt.PromptSession = FakeSession
    monkeypatch.setitem(sys.modules, "prompt_toolkit", fake_pt)
    monkeypatch.setattr(commands, "_safe_file_history_class", lambda: lambda path: path)
    monkeypatch.setattr(commands, "_build_cli_key_bindings", lambda: None)
    monkeypatch.setattr(
        "navin.config.paths.get_cli_history_path", lambda: tmp_path / "h" / "history"
    )
    monkeypatch.setattr(commands, "_PROMPT_SESSION", None)

    commands._init_prompt_session()

    # Default prompt_toolkit reserves ~8 empty rows for a completion menu,
    # which showed up as a gap at the bottom of the terminal.
    assert captured["reserve_space_for_menu"] == 0
    assert captured["multiline"] is True
