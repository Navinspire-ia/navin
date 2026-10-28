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

import pytest
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


def test_idle_pool_workers_are_woken_and_the_input_thread_is_not_waited_for():
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import thread as pool_thread

    from navin.tui.shutdown import join_background_threads

    # An idle journal worker held the exit for its whole grace (1 s) before:
    # a worker only leaves its queue when its pool is shutting down.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="navin-agent-journal")
    pool.submit(lambda: None).result()
    written = []
    pool.submit(lambda: (time.sleep(0.05), written.append("journal line")))
    workers = [t for t in threading.enumerate() if t.name.startswith("navin-agent-journal")]
    assert workers
    # The terminal input thread only waits out its last poll: no join for it.
    polling = threading.Event()
    reader = threading.Thread(target=polling.wait, args=(5,), name="textual-input")
    reader.start()
    try:
        started = time.monotonic()
        lingering = join_background_threads(grace=1.0)
        elapsed = time.monotonic() - started
        assert elapsed < 0.8, elapsed
        assert written == ["journal line"]  # queued work is finished, not dropped
        assert not any(t.is_alive() for t in workers)
        assert "textual-input" not in lingering
        assert reader.is_alive()
    finally:
        polling.set()
        reader.join(1)
        pool_thread._shutdown = False  # the test process goes on
        pool.shutdown(wait=False)


def test_quit_lets_the_terminal_input_thread_end_while_the_engine_closes():
    from navin.tui.shutdown import release_input_thread

    driver = SimpleNamespace(exit_event=threading.Event(), is_headless=False, _disable_mouse_support=Mock())
    release_input_thread(SimpleNamespace(_driver=driver))
    assert driver.exit_event.is_set()
    driver._disable_mouse_support.assert_called_once()
    headless = SimpleNamespace(exit_event=threading.Event(), is_headless=True, _disable_mouse_support=Mock())
    release_input_thread(SimpleNamespace(_driver=headless))
    assert not headless.exit_event.is_set()
    release_input_thread(SimpleNamespace(_driver=None))  # before the driver exists


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_quit_signals_the_mcp_servers_instead_of_waiting_for_them(tmp_path):
    from navin.tui.shutdown import run_app_fast_exit
    from navin.utils import child_processes

    seen = {}

    class App:
        async def run_async(self):
            loop = asyncio.get_running_loop()
            assert child_processes.registry_of(loop) is not None  # installed by run_app_fast_exit
            with child_processes.tagged("mcp:time"):
                server = await asyncio.create_subprocess_exec(
                    "sleep", "30", stdin=asyncio.subprocess.PIPE, start_new_session=True,
                )
            tool = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
            runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
            runtime.agent_loop = SimpleNamespace(stop=Mock(), sessions=Mock(flush_saved=Mock()), close_mcp=AsyncMock())
            started = time.monotonic()
            with patch("navin.cli.commands._close_agent_subprocesses", new=AsyncMock()):
                await runtime.close()
            seen["close_s"] = time.monotonic() - started
            await asyncio.wait_for(server.wait(), 1.0)
            seen["server_rc"] = server.returncode
            seen["tool_rc"] = tool.returncode  # an ordinary child is not touched
            tool.kill()
            await tool.wait()
            return "done"

    assert run_app_fast_exit(App()) == "done"
    assert seen["server_rc"] == -15  # SIGTERM at once, not stdin EOF then a 2 s wait
    assert seen["tool_rc"] is None
    assert seen["close_s"] < 1.0


def test_terminal_guard_restores_tty_modes():
    from unittest.mock import patch

    from navin.tui.shutdown import TerminalGuard

    guard = TerminalGuard()
    guard._fd, guard._attrs = 0, ["saved"]
    with patch("termios.tcsetattr") as tcsetattr:
        guard.restore()
    tcsetattr.assert_called_once()
    assert tcsetattr.call_args.args[2] == ["saved"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_exit_closes_leftover_children_without_a_closed_loop_traceback(tmp_path):
    # A cleanup cut short left subprocess transports to the GC after
    # loop.close(): "Exception ignored ... Event loop is closed" under the exit
    # summary, and the children kept running as orphans.
    script = """
import asyncio, gc, os, sys, time
from unittest.mock import patch
from navin.tui.shutdown import close_subprocess_transports
from navin.utils import child_processes
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
child_processes.install(loop)
procs = []
async def main():
    procs.append(await asyncio.create_subprocess_exec("sleep", "30", stdout=asyncio.subprocess.PIPE, start_new_session=True))
    procs.append(await asyncio.create_subprocess_exec("sleep", "30", stdout=asyncio.subprocess.PIPE))
loop.run_until_complete(main())
pids = [p.pid for p in procs]
# The loop's child registry knows them: the whole heap is never walked.
with patch("gc.get_objects", side_effect=AssertionError("heap walk")):
    assert close_subprocess_transports(loop) == 2
asyncio.set_event_loop(None)
loop.close()
del procs
gc.collect()
time.sleep(0.2)
def running(pid):
    try:
        return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
    except OSError:
        return False
print("alive", [pid for pid in pids if running(pid)])
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Event loop is closed" not in result.stderr
    assert "Exception ignored" not in result.stderr
    assert "alive []" in result.stdout


def test_cli_engine_resumes_its_own_interrupted_turn(tmp_path):
    """A crash or closed terminal must not lose the CLI turn that was running."""
    runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
    calls = {}

    def run(**kwargs):
        calls.update(kwargs)
        return "loop"

    runtime.agent_loop = SimpleNamespace(run=run)
    assert runtime._run_agent_loop() == "loop"
    assert calls == {"recovery_channel": runtime.channel, "recovery_session_key": runtime.session_key}
    assert calls["recovery_channel"] == "cli"


def test_switching_to_an_interrupted_chat_schedules_its_recovery(tmp_path):
    from navin.bus.events import InboundMessage
    from navin.session.turn_recovery import RECOVERY_KEY, TurnRecovery

    async def run():
        runtime = TuiRuntime(SimpleNamespace(workspace_path=tmp_path), on_event=lambda _: None)
        sessions = SessionManager(tmp_path)
        recovery = TurnRecovery(sessions)
        interrupted = sessions.get_or_create("cli:old")
        recovery.begin(interrupted, InboundMessage(channel="cli", sender_id="u", chat_id="old", content="finish the audit"))
        sessions.save(interrupted)
        sessions.get_or_create("cli:done")
        runtime.agent_loop = SimpleNamespace(turn_recovery=recovery, sessions=sessions)
        runtime._refresh_status = lambda: None

        await runtime.switch_session("cli:done")
        assert recovery.due == {}
        await runtime.switch_session("cli:old")
        assert "cli:old" in recovery.due
        resumed = recovery.take_due(active_keys=set())
        assert len(resumed) == 1 and "finish the audit" in resumed[0].content
        assert SessionManager(tmp_path).get_or_create("cli:old").metadata[RECOVERY_KEY]["status"] == "running"

    asyncio.run(run())
