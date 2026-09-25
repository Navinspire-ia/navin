# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Quit releases the terminal promptly and leaves an honest resume receipt."""

import asyncio
import io
import shlex
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from rich.console import Console

from navin.agent.hook import AgentHookContext
from navin.agent.tools.exec_session import ExecSessionManager
from navin.session.manager import SessionManager
from navin.tui.exit_summary import CliUsageHook, UsageTotals, print_exit_summary, resume_command
from navin.tui.runtime import TuiRuntime


def test_quit_cancels_active_work_before_cleanup_and_flushes_checkpoint(tmp_path):
    async def run():
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
        sessions = SessionManager(tmp_path)
        session = sessions.get_or_create(runtime.session_key)
        started = asyncio.Event()

        async def active_turn():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                session.messages.append({"role": "assistant", "content": "Saved checkpoint"})
                sessions.save(session)

        turn = asyncio.create_task(active_turn())
        await started.wait()
        runtime._loop_task = asyncio.create_task(asyncio.sleep(60))
        runtime._consumer_task = asyncio.create_task(asyncio.sleep(60))
        close_mcp = AsyncMock()
        runtime.agent_loop = SimpleNamespace(stop=Mock(), close_mcp=close_mcp, sessions=sessions,
                                            _active_tasks={runtime.session_key: [turn]})
        start = time.monotonic()
        with patch("navin.cli.commands._close_agent_subprocesses", new=AsyncMock()):
            await asyncio.wait_for(runtime.close(), 0.7)
            await runtime.close()
        assert time.monotonic() - start < 0.7
        assert turn.cancelled()
        close_mcp.assert_not_awaited()  # The loop owns its own cleanup.
        assert not sessions._pending_fsync
        restored = SessionManager(tmp_path).get_or_create(runtime.session_key)
        assert restored.messages[-1]["content"] == "Saved checkpoint"
    asyncio.run(run())


def test_a_stalled_cleanup_does_not_hold_shutdown_forever(tmp_path):
    async def run():
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
        runtime.agent_loop = SimpleNamespace(stop=Mock(), sessions=Mock(), close_mcp=AsyncMock())
        started = asyncio.Event()

        async def stuck():
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(60)  # A cleanup that ignores the first cancellation.

        runtime._loop_task = asyncio.create_task(stuck())
        await started.wait()
        with patch("navin.cli.commands._close_agent_subprocesses", new=AsyncMock()):
            start = time.monotonic()
            await asyncio.wait_for(runtime.close(), 2.8)
        assert time.monotonic() - start < 2.8
        assert runtime._loop_task.done()
        assert runtime.shutdown_warnings
    asyncio.run(run())


def test_terminal_sessions_are_stopped_together():
    async def run():
        manager = ExecSessionManager()
        started = set()
        release = asyncio.Event()

        async def kill(index):
            started.add(index)
            if len(started) == 61:
                release.set()
            await release.wait()

        for index in range(61):
            manager._sessions[str(index)] = SimpleNamespace(
                exit_reported=False, kill=lambda index=index: kill(index))
        assert await asyncio.wait_for(manager.shutdown(), 0.5) == 61
        assert not manager.has_open_sessions()
    asyncio.run(run())


def test_exit_sync_does_not_rewrite_history_or_save_read_only_chats(tmp_path):
    manager = SessionManager(tmp_path)
    edited = manager.get_or_create("cli:edited")
    edited.messages.append({"role": "user", "content": "Large message. " * 10000})
    manager.save(edited)
    for index in range(200):
        manager.get_or_create(f"cli:only-read-{index}")
    with patch.object(manager, "save", side_effect=AssertionError("Do not rewrite history")):
        assert manager.flush_saved() == 1
        assert manager.flush_saved() == 0
    assert len(list(manager.sessions_dir.glob("*.jsonl"))) == 1
    assert SessionManager(tmp_path).get_or_create(edited.key).messages == edited.messages


def test_usage_includes_interrupted_tools_and_never_counts_a_response_twice():
    async def run():
        hook = CliUsageHook()
        context = AgentHookContext(iteration=1, messages=[], session_key="cli:test", usage={
            "prompt_tokens": 100, "completion_tokens": 30, "cached_tokens": 70, "reasoning_tokens": 20,
        })
        await hook.before_execute_tools(context)
        assert hook.sessions["cli:test"].input == 100
        await hook.after_iteration(context)
        context.usage["completion_tokens"] += 5
        await hook.after_iteration(context)
        totals = hook.sessions["cli:test"]
        assert (totals.input, totals.output, totals.cached, totals.reasoning) == (100, 35, 70, 20)
        assert "total=135" in totals.line()
        assert "cached 70 included" in totals.line()
    asyncio.run(run())


def test_usage_disk_write_keeps_the_shared_cli_desktop_loop_responsive():
    from navin.webui.token_usage import TokenUsageHook

    async def run():
        started, release = threading.Event(), threading.Event()

        def write(*args, **kwargs):
            started.set()
            assert release.wait(2), "The event loop could not run while disk I/O waited"

        with patch("navin.webui.token_usage.record_token_usage", side_effect=write):
            task = asyncio.create_task(TokenUsageHook().after_iteration(AgentHookContext(
                iteration=1, messages=[], session_key="cli:test", usage={"prompt_tokens": 100})))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.005)
                assert started.is_set()
                assert not task.done()
            finally:
                release.set()
                await task
    asyncio.run(run())


def test_responses_usage_preserves_cache_without_double_counting():
    from navin.providers.openai_responses.parsing import _usage_from_response_obj

    raw = {"input_tokens": 100, "output_tokens": 30,
           "input_tokens_details": {"cached_tokens": 70},
           "output_tokens_details": {"reasoning_tokens": 20}}
    for response in ({"usage": raw}, SimpleNamespace(usage=SimpleNamespace(**raw))):
        usage = _usage_from_response_obj(response)
        assert usage == {"prompt_tokens": 100, "completion_tokens": 30,
                         "total_tokens": 130, "cached_tokens": 70, "reasoning_tokens": 20}


def test_exit_receipt_has_a_copyable_resume_command_and_literal_title(tmp_path):
    output = io.StringIO()
    console = Console(file=output, width=150, force_terminal=True)
    usage = UsageTotals(input=1095954, output=147509, reasoning=59589, has_reasoning=True)
    workspace = tmp_path / "project with spaces"
    config = tmp_path / "config custom.json"
    command = resume_command(workspace, "cli:my-session", config)
    assert shlex.split(command) == ["navin-cli", str(workspace), "--session", "cli:my-session", "--config", str(config)]
    print_exit_summary(session_key="cli:my-session", workspace=workspace, elapsed=1009,
                       usage=usage, title="[red]Clarify the request[/red]", config_path=config, console=console)
    rendered = output.getvalue()
    assert "Worked for 16m 49s" in rendered
    assert "total=1,243,463" in rendered
    assert "reasoning 59,589" in rendered
    assert "cached not reported" in rendered
    assert "--session" in rendered and "cli:my-session" in rendered
    assert "[red]Clarify the request[/red]" in rendered


def test_speculative_indexing_cannot_keep_the_cli_process_alive(tmp_path):
    script = """
import asyncio, sys, threading, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from navin.index.warmer import schedule_warm
started = threading.Event()
def ensure():
    started.set()
    time.sleep(60)
async def main():
    with patch('navin.index.get_index', return_value=SimpleNamespace(ensure=ensure)):
        assert schedule_warm(Path(sys.argv[1]))
        assert started.wait(1)
asyncio.run(main())
print('Exited while speculative indexing was still pending')
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "Exited while" in result.stdout


def test_real_app_quit_preserves_draft_and_queue_with_large_activity(tmp_path):
    from navin.bus.queue import MessageBus
    from navin.config.loader import get_config_path, set_config_path
    from navin.tui.app import NavinApp, QueuedPrompt
    from navin.tui.prefs import TuiPrefs
    from navin.tui.widgets import AssistantMessage

    class QuitApp(NavinApp):
        async def on_mount(self, event):
            event.prevent_default()
            self.runtime.bus = MessageBus()
            self._engine_ready = True

    async def run():
        prefs = TuiPrefs(sidebar=False)
        app = QuitApp(SimpleNamespace(workspace_path=tmp_path), prefs=prefs)
        async with app.run_test(size=(100, 35)) as pilot:
            block = AssistantMessage()
            await app.transcript.add(block)
            await block.delta("- A result\n" * 80)
            for index in range(790):
                await block.note_file_edit(f"src/file{index}.py", 2, 1)
            app.composer.set_text("Keep this draft")
            app.runtime.status.turn_active = True
            app._queued_prompts[app.runtime.session_key] = [QueuedPrompt(1, "Queued task", "Queued task")]
            await pilot.pause()
            started = time.monotonic()
            await app.action_quit()
        assert time.monotonic() - started < 3.0
        stored = app._session_store.load("cli:direct")
        assert stored["draft"] == "Keep this draft"
        assert stored["queue"][0]["text"] == "Queued task"
        assert stored["paused"]
    original = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        with patch("navin.cli.commands._close_agent_subprocesses", new=AsyncMock()):
            asyncio.run(run())
    finally:
        set_config_path(original)


def test_loop_close_does_not_wait_for_a_stuck_worker_thread():
    import threading
    import time

    from navin.tui.shutdown import run_app_fast_exit

    release = threading.Event()

    class StuckApp:
        async def run_async(self):
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, release.wait, 30)
            return "done"

    started = time.monotonic()
    try:
        assert run_app_fast_exit(StuckApp()) == "done"
        assert time.monotonic() - started < 3
    finally:
        release.set()


def test_terminal_guard_restores_tty_modes():
    from unittest.mock import patch

    from navin.tui.shutdown import TerminalGuard

    guard = TerminalGuard()
    guard._fd, guard._attrs = 0, ["saved"]
    with patch("termios.tcsetattr") as tcsetattr:
        guard.restore()
    tcsetattr.assert_called_once()
    assert tcsetattr.call_args.args[2] == ["saved"]
