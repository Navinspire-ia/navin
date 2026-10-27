# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from navin.quality import test_timing as tt


def test_unknown_run_has_no_estimate_and_no_kind(tmp_path):
    assert tt.expected_seconds(tmp_path, "pytest", None) is None
    assert tt.run_kind(None) is None


def test_estimate_is_the_median_of_recent_runs_per_target(tmp_path):
    for seconds in (10, 200, 12):
        tt.record_run(tmp_path, "pytest", None, seconds)
    tt.record_run(tmp_path, "pytest", "tests/test_a.py", 2)
    assert tt.expected_seconds(tmp_path, "pytest", None) == 12
    assert tt.expected_seconds(tmp_path, "pytest", "tests/test_a.py") == 2


def test_only_the_last_runs_are_kept(tmp_path):
    for seconds in (500, 500, 500, 1, 1, 1, 1, 1):
        tt.record_run(tmp_path, None, None, seconds)
    assert tt.expected_seconds(tmp_path, None, None) == 1


def test_quick_and_long_split_at_the_threshold():
    assert tt.run_kind(tt.LONG_RUN_SECONDS - 0.1) == "quick"
    assert tt.run_kind(tt.LONG_RUN_SECONDS) == "long"


def test_corrupt_history_is_ignored_and_replaced(tmp_path):
    path = tmp_path / ".navin" / "test-timing.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    assert tt.expected_seconds(tmp_path, "pytest", None) is None
    tt.record_run(tmp_path, "pytest", None, 5)
    assert tt.expected_seconds(tmp_path, "pytest", None) == 5


def test_zero_duration_is_not_recorded(tmp_path):
    tt.record_run(tmp_path, "pytest", None, 0)
    assert not (tmp_path / ".navin" / "test-timing.json").exists()


def test_command_key_ignores_output_filters_so_piped_runs_share_a_history():
    plain = tt.command_key("python -m pytest -q tests/test_a.py", "/repo")
    assert tt.command_key("python -m pytest -q tests/test_a.py 2>&1 | tail -25", "/repo") == plain
    assert tt.command_key("python -m pytest -q tests/test_a.py; echo done", "/repo") == plain
    assert tt.command_key("python -m pytest -q tests/test_a.py", "/other") != plain
    # a real pipeline stage is part of the command, not a filter
    assert tt.command_key("cat a | grep x", "/repo") != tt.command_key("cat a", "/repo")


def test_command_history_lives_in_the_data_dir_not_in_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("navin.config.paths.get_data_dir", lambda: tmp_path / "data")
    key = tt.command_key("npm run build", tmp_path / "proj")
    assert tt.expected_command_seconds(key) is None
    for seconds in (150, 170, 160):
        tt.record_command_run(key, seconds)
    assert tt.expected_command_seconds(key) == 160
    assert tt.run_kind(tt.expected_command_seconds(key)) == "long"
    assert not (tmp_path / "proj").exists()


def test_estimated_percent_never_claims_the_end_before_the_exit():
    assert tt.estimated_percent(30, 60) == 50
    assert tt.estimated_percent(500, 60) == tt.ESTIMATE_CAP_PERCENT
    assert tt.estimated_percent(10, None) is None
    assert tt.estimated_percent(10, 0) is None


def test_duration_hint_for_the_cli_row():
    assert tt.duration_hint(20) == "quick"
    assert tt.duration_hint(240) == "long ~4 min"


def test_a_piped_command_with_a_history_shows_its_usual_time_and_an_estimated_percent(tmp_path, monkeypatch):
    import asyncio
    import tempfile

    from navin.agent.tool_output import bind_tool_call_meta, bind_tool_output_emitter
    from navin.agent.tools.shell import ExecTool

    monkeypatch.setattr("navin.config.paths.get_data_dir", lambda: tmp_path)
    command = "python -m pytest -q 2>&1 | tail -25"
    tt.record_command_run(tt.command_key("python -m pytest -q", "/repo"), 200)
    seen: list[dict] = []

    async def emitter(payload: dict) -> None:
        seen.append(payload)

    async def go() -> None:
        bind_tool_output_emitter(emitter)
        bind_tool_call_meta("call-t", "exec", {"command": command})
        tool = ExecTool(working_dir=tempfile.gettempdir())
        timing = await tool._announce_expected_duration(command, "/repo")
        timing.started -= 50  # a quarter of the usual 200 s already spent
        await tool._emit_progressing_output("....", timing=timing)
        await tool._remember_duration(timing)

    asyncio.run(go())
    announce = next(p for p in seen if "test_expected_s" in p)
    assert announce["test_expected_s"] == 200
    assert announce["test_kind"] == "long"
    live = next(p for p in seen if p.get("output") == "....")
    assert 24 <= live["percent"] <= 30
    assert live["indeterminate"] is False
    # the finished run joined the history of the same command, pipe or not
    assert tt.expected_command_seconds(tt.command_key("python -m pytest -q", "/repo")) is not None


def test_a_first_command_has_no_estimate_and_keeps_an_indeterminate_bar(tmp_path, monkeypatch):
    import asyncio
    import tempfile

    from navin.agent.tool_output import bind_tool_call_meta, bind_tool_output_emitter
    from navin.agent.tools.shell import ExecTool

    monkeypatch.setattr("navin.config.paths.get_data_dir", lambda: tmp_path)
    seen: list[dict] = []

    async def emitter(payload: dict) -> None:
        seen.append(payload)

    async def go() -> None:
        bind_tool_output_emitter(emitter)
        bind_tool_call_meta("call-u", "exec", {"command": "make build"})
        tool = ExecTool(working_dir=tempfile.gettempdir())
        timing = await tool._announce_expected_duration("make build", "/repo")
        await tool._emit_progressing_output("compiling", timing=timing)

    asyncio.run(go())
    assert not any("test_expected_s" in p for p in seen)
    live = next(p for p in seen if p.get("output") == "compiling")
    assert live.get("percent") is None
