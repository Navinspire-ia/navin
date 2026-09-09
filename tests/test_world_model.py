# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Acceptance tests for S3 - the world model (tool outcome prediction).

The contract under test, in the order of the spec:

* S3.0 flag off = no-op: no file, no thread, no hook, no advice, and the
  per-turn factory costs one ``os.stat``;
* S3.1 one secret-free line per tool call, written after the call by a
  background thread; the turn never waits for the disk;
* S3.2 a local head trained outside any turn, under a budget, with
  versioned checkpoints and a one-step rollback;
* S3.3 a frozen held-out set (hash / version, tamper check) and a scoreboard
  with a verdict vs the baselines; ``down`` never activates a checkpoint;
* S3.4 ``BELIEFS.md`` as a readable summary, never read by the prompt while
  ``advise`` is off, editable and discardable by a human;
* S3.5 live advice behind the gates (exam up, offline A/B gain), the
  ``world_predict`` tool that appears and disappears with them, and the kill
  switch that cuts advice and rolls back when the live precision drops;
* S3.6 nothing in the hot path imports the world model; the Guardrails copy
  says off / journal + train auto / advice = gate + you.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from navin.agent.hook import AgentHookContext, AgentRunHookContext, AgentTurnHookContext
from navin.agent.hooks import DEFAULT_HOOK_FACTORIES
from navin.providers.base import ToolCallRequest
from navin.world_model import checkpoints as ck
from navin.world_model import hook as hook_module
from navin.world_model import jobs
from navin.world_model.advisor import (
    KILL_MIN_ADVICE,
    Suggestion,
    advice_block_text,
    advice_gate,
    advice_lines,
    advice_open,
    live_tracker,
    run_ab,
)
from navin.world_model.beliefs import (
    BEGIN,
    END,
    current_beliefs,
    discard_belief,
    keys_in_file,
    read_beliefs_file,
    render_beliefs,
    restore_beliefs,
)
from navin.world_model.dataset import HeldoutTamperedError, load_heldout, split_of, training_rows
from navin.world_model.hook import (
    WorldModelHook,
    create_world_model_hook,
    flush_recorder,
    reset_recorder,
)
from navin.world_model.journal import (
    append_trajectory,
    count_trajectories,
    flush,
    read_journal,
    read_trajectories,
)
from navin.world_model.model import AlwaysOkModel, CountModel, MajorityModel, compare, evaluate
from navin.world_model.paths import (
    beliefs_file,
    heldout_path,
    trajectories_path,
    world_dir,
)
from navin.world_model.settings import (
    SETTINGS_NAME,
    clear_settings_cache,
    read_settings,
    settings_path,
    update_settings,
    world_enabled,
)
from navin.world_model.state import WorldActionError, world_action, world_state, world_update
from navin.world_model.train import TrainBudget, exam, freeze, rollback, train
from navin.world_model.trajectory import (
    CLASSES,
    SessionHistory,
    build_trajectory,
    classify_observation,
    normalize_call,
    scrub_secrets,
)

REPO = Path(__file__).resolve().parents[1]

# The package exports the ``train`` function, which shadows the submodule name.
train_module = importlib.import_module("navin.world_model.train")

SALT = "test-salt"

# A project with habits: the same six calls, the same six answers. The head
# must learn "npm test fails here", "git push is denied here", "that API
# answers 404"; the baselines cannot.
SCENARIO: tuple[tuple[str, dict[str, Any], str, bool, str], ...] = (
    ("exec", {"command": "npm test"}, "npm ERR! Test failed. See above for more details.\nexit code 1", True, "error"),
    (
        "exec",
        {"command": "git push origin main"},
        "remote: Permission denied (publickey). fatal: Could not read from remote repository.",
        True,
        "denied",
    ),
    ("exec", {"command": "ls -la src"}, "total 12\napp.py\nutil.py", False, "ok"),
    ("read_file", {"path": "src/app.py"}, "import os\nprint('hello')", False, "ok"),
    ("browser", {"url": "https://api.example.test/v1/missing", "action": "fetch"}, "HTTP 404 Not Found", True, "not_found"),
    ("write_file", {"path": "notes.md", "content": "hello"}, "written 5 bytes", False, "changed"),
)

SECRET_ARGS = {
    "command": (
        "curl -H 'Authorization: Bearer sk-live-abcdefghijklmnop1234567890' "
        "https://alice:Sup3rS3cret@api.example.test/v1/me?api_key=AKIAABCDEFGHIJKLMNOP"
    ),
    "env": {"OPENAI_API_KEY": "sk-proj-zzzzzzzzzzzzzzzzzzzzzzzz", "DB_PASSWORD": "hunter2hunter2"},
}
SECRET_OUTPUT = (
    "token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123\n"
    "export STRIPE_SECRET=sk_live_0123456789abcdefghijkl\n"
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7\n-----END RSA PRIVATE KEY-----\n"
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U\n"
    "Permission denied"
)
SECRET_MARKERS = (
    "sk-live-abcdefghijklmnop1234567890",
    "Sup3rS3cret",
    "AKIAABCDEFGHIJKLMNOP",
    "sk-proj-zzzzzzzzzzzzzzzzzzzzzzzz",
    "hunter2hunter2",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123",
    "sk_live_0123456789abcdefghijkl",
    "MIIEpAIBAAKCAQEA7",
    "eyJhbGciOiJIUzI1NiJ9",
)


def _turn(workspace: Path | None, **overrides: Any) -> AgentTurnHookContext:
    base: dict[str, Any] = dict(
        workspace=workspace,
        channel="webui",
        chat_id="chat-1",
        session_key="webui:chat-1",
        metadata={"turn_id": "t-1"},
        ephemeral=False,
    )
    base.update(overrides)
    return AgentTurnHookContext(**base)


def _hook_ctx() -> AgentHookContext:
    return AgentHookContext(iteration=1, messages=[{"role": "user", "content": "go"}])


def _run_ctx() -> AgentRunHookContext:
    return AgentRunHookContext(messages=[{"role": "user", "content": "go"}], final_content="done")


def _call(name: str, arguments: Any, call_id: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=name, arguments=arguments)


class _ReadOnlyTool:
    read_only = True


class _WriteTool:
    read_only = False


class _Registry:
    """Just enough of the tool registry for ``sync_world_tool``."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register(self, tool: Any) -> None:
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def get(self, name: str) -> Any | None:
        return self.tools.get(name)


def _appended(workspace: Path) -> int:
    """Lines the background writer put in this project's journal (process-wide counter)."""
    from navin.world_model import journal as journal_module

    return journal_module._WRITER.appended.get(str(trajectories_path(workspace)), 0)


def _fill_journal(workspace: Path, *, cycles: int, session: str = "webui:chat-1") -> list[dict[str, Any]]:
    """Write ``cycles`` rounds of the scenario straight into trajectories.jsonl."""
    history = SessionHistory()
    written: list[dict[str, Any]] = []
    for _ in range(cycles):
        for tool, args, output, is_error, _expected in SCENARIO:
            trajectory = build_trajectory(
                tool_name=tool,
                arguments=args,
                result=output,
                is_error=is_error,
                salt=SALT,
                duration_ms=12,
                prev=history.prev(session),
                session=session,
                turn="t-1",
            )
            history.push(session, trajectory.tool, trajectory.cls)
            record = trajectory.as_record()
            append_trajectory(workspace, record)
            written.append(record)
    assert flush(5.0)
    return written


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_settings_cache)
        self.addCleanup(reset_recorder)
        self.addCleanup(live_tracker().reset)
        self.addCleanup(ck.clear_checkpoint_cache)
        self.addCleanup(jobs.reset_runner)
        self.workspace = Path(self._tmp.name) / "project"
        self.workspace.mkdir()
        # Training runs on a daemon thread in production; tests run it by hand.
        jobs.configure(auto_thread=False)
        self.addCleanup(jobs.configure, auto_thread=True)
        # The machine index must never be the real ~/.local/share/navin.
        import navin.world_model.registration as registration

        self.index_file = Path(self._tmp.name) / "machine" / "world-model-projects.json"
        patcher = patch.object(registration, "index_path", lambda: self.index_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        reset_recorder()
        live_tracker().reset()
        ck.clear_checkpoint_cache()

    def enable(self, **fields: Any) -> None:
        update_settings(self.workspace, {"enabled": True, "min_rows": 10, **fields})

    def events(self) -> list[str]:
        return [str(row["event"]) for row in read_journal(self.workspace, limit=200)]

    def files(self) -> list[str]:
        return sorted(str(p.relative_to(self.workspace)) for p in self.workspace.rglob("*"))

    def trained(self, *, cycles: int = 40) -> Any:
        """Flag on, journal filled, one training run; returns the result."""
        self.enable()
        _fill_journal(self.workspace, cycles=cycles)
        result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "trained", result.reason)
        return result

    def open_gate(self) -> None:
        """Train + offline A/B so that ``advise`` may be switched on."""
        self.trained()
        ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["status"], "scored", ab)
        self.assertEqual(ab["verdict"], "gain", ab)
        self.assertTrue(advice_gate(self.workspace).open, advice_gate(self.workspace).reasons)


# --------------------------------------------------------------------------
# S3.0 - the net: flag off is a no-op
# --------------------------------------------------------------------------


class FlagOffTest(_Workspace):
    def test_default_is_off_and_missing_file_means_off(self) -> None:
        settings = read_settings(self.workspace)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.feature("log"))
        self.assertFalse(settings.feature("train"))
        self.assertFalse(settings.feature("advise"))
        self.assertFalse(world_enabled(self.workspace))
        self.assertFalse(world_enabled(None))
        self.assertEqual(SETTINGS_NAME, "world-model.json")
        self.assertFalse((self.workspace / ".navin").exists())

    def test_malformed_flag_stays_off(self) -> None:
        path = settings_path(self.workspace)
        path.parent.mkdir(parents=True)
        for raw in ("not json", "[]", '{"enabled": "yes"}', '{"log": true}', '{"advise": true}', ""):
            path.write_text(raw, encoding="utf-8")
            clear_settings_cache()
            self.assertFalse(world_enabled(self.workspace), raw)
            self.assertFalse(world_enabled(self.workspace, "advise"), raw)

    def test_hook_factory_answers_none_and_touches_nothing(self) -> None:
        self.assertIsNone(create_world_model_hook(_turn(self.workspace)))
        self.assertIsNone(create_world_model_hook(_turn(None)))
        self.assertIsNone(create_world_model_hook(_turn(self.workspace, ephemeral=True)))
        self.assertIsNone(create_world_model_hook(_turn(self.workspace, session_key="heartbeat")))
        self.assertFalse((self.workspace / ".navin").exists())
        self.assertEqual(_appended(self.workspace), 0)
        self.assertEqual(count_trajectories(self.workspace), 0)

    def test_master_on_but_log_and_advise_off_means_no_hook(self) -> None:
        update_settings(self.workspace, {"enabled": True, "log": False, "advise": False})
        self.assertIsNone(create_world_model_hook(_turn(self.workspace)))
        self.assertFalse(world_dir(self.workspace).exists(), "the flag file is the only file")

    def test_jobs_train_actions_and_advice_are_no_ops(self) -> None:
        self.assertFalse(jobs.kick(self.workspace))
        self.assertIsNone(jobs.run_due(self.workspace))
        self.assertEqual(train(self.workspace).status, "skipped")
        self.assertEqual(exam(self.workspace)["status"], "skipped")
        self.assertEqual(freeze(self.workspace)["status"], "skipped")
        self.assertEqual(run_ab(self.workspace)["status"], "skipped")
        self.assertFalse(advice_open(self.workspace))
        self.assertIsNone(advice_block_text(self.workspace))
        with self.assertRaises(WorldActionError) as caught:
            world_action(self.workspace, "train", actor="human")
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse((self.workspace / ".navin").exists())
        self.assertFalse(jobs.runner_alive())

    def test_state_is_readable_off_and_creates_nothing(self) -> None:
        state = world_state(self.workspace)
        self.assertFalse(state["enabled"])
        self.assertFalse(state["advise"])
        self.assertEqual(state["rows"], 0)
        self.assertEqual(state["checkpoints"], [])
        self.assertIsNone(state["active"])
        self.assertFalse(state["gate"]["open"])
        self.assertEqual(state["belief_items"], [])
        self.assertEqual(state["classes"], list(CLASSES))
        self.assertFalse((self.workspace / ".navin").exists())

    def test_default_factories_include_the_hook_once_after_s1_and_s2(self) -> None:
        names = [f.__name__ for f in DEFAULT_HOOK_FACTORIES]
        self.assertEqual(names.count("create_world_model_hook"), 1)
        self.assertLess(names.index("create_episode_journal_hook"), names.index("create_world_model_hook"))
        self.assertLess(names.index("create_skills_evolve_hook"), names.index("create_world_model_hook"))

    def test_off_path_is_one_stat_cheap(self) -> None:
        """Micro-bench: a chat turn with the flag off pays a few microseconds."""
        turn = _turn(self.workspace)
        loops = 20_000
        start = time.perf_counter()
        for _ in range(loops):
            create_world_model_hook(turn)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed / loops, 50e-6, f"{elapsed / loops * 1e6:.1f} us per call")

    def test_world_tool_is_absent_off(self) -> None:
        from navin.agent.tools.world_predict import WorldPredictTool
        from navin.world_model.registration import sync_world_tool

        class _Ctx:
            workspace = self.workspace

        self.assertFalse(WorldPredictTool.enabled(_Ctx()))
        registry = _Registry()
        self.assertFalse(sync_world_tool(registry, workspace=self.workspace))
        self.assertEqual(registry.tools, {})


class SettingsTest(_Workspace):
    def test_master_corridors_and_knobs(self) -> None:
        self.enable()
        settings = read_settings(self.workspace)
        self.assertTrue(settings.enabled and settings.log and settings.train)
        self.assertFalse(settings.advise, "advice is never on by default")
        self.assertFalse(settings.beliefs)
        update_settings(self.workspace, {"log": False})
        self.assertFalse(world_enabled(self.workspace, "log"))
        self.assertTrue(world_enabled(self.workspace, "train"))
        update_settings(self.workspace, {"enabled": False})
        self.assertFalse(world_enabled(self.workspace, "train"), "master off gates every corridor")
        self.assertTrue(read_settings(self.workspace).train, "the stored choice survives a pause")

    def test_validation(self) -> None:
        for bad in ({}, {"bogus": True}, {"enabled": "yes"}, {"min_rows": 5}, {"confidence_threshold": 2}, {"train_every": True}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                update_settings(self.workspace, bad)
        self.assertFalse(settings_path(self.workspace).exists())
        for bad in ({"bogus": True}, {"advise": "on"}):
            with self.assertRaises(WorldActionError) as caught:
                world_update(self.workspace, bad)
            self.assertEqual(caught.exception.status, 400)

    def test_advise_is_refused_while_the_gate_is_closed(self) -> None:
        with self.assertRaises(WorldActionError) as caught:
            world_update(self.workspace, {"advise": True})
        self.assertEqual(caught.exception.status, 409, "master off first")
        self.enable()
        with self.assertRaises(WorldActionError) as caught:
            world_update(self.workspace, {"advise": True})
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("no active checkpoint", str(caught.exception))
        self.assertFalse(read_settings(self.workspace).advise)
        # Off is always allowed, and journaled.
        state = world_update(self.workspace, {"advise": False})
        self.assertFalse(state["advise"])
        self.assertIn("advise_off", self.events())


# --------------------------------------------------------------------------
# S3.1 - one line per call, no secret, written after the call
# --------------------------------------------------------------------------


class TrajectoryTest(unittest.TestCase):
    def test_scrub_removes_every_kind_of_credential(self) -> None:
        text = json.dumps(SECRET_ARGS) + "\n" + SECRET_OUTPUT
        clean = scrub_secrets(text)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, clean, marker)
        self.assertIn("<redacted>", clean)
        # Variable names survive, values do not.
        self.assertIn("DB_PASSWORD", clean)
        self.assertIn("Permission denied", clean)

    def test_classes_follow_the_observation(self) -> None:
        cases = [
            ("exec", "ok output", False, None, "ok"),
            ("exec", "", False, None, "empty"),
            ("write_file", "written", False, None, "changed"),
            ("exec", "written", False, False, "changed"),
            ("exec", "bash: foo: command not found", True, None, "not_found"),
            ("exec", "Permission denied", True, None, "denied"),
            ("exec", TimeoutError("cancelled after 30s"), True, None, "timeout"),
            ("exec", "process exited with code 2\nsomething broke", False, None, "error"),
            ("exec", "exit code 1: No such file or directory", False, None, "not_found"),
            ("browser", "HTTP 403 Forbidden", True, None, "denied"),
            ("read_file", RuntimeError("boom"), True, None, "error"),
        ]
        for tool, result, is_error, read_only, expected in cases:
            self.assertEqual(classify_observation(tool, result, is_error=is_error, read_only=read_only), expected, (tool, result))
        for _tool, _args, output, is_error, expected in SCENARIO:
            self.assertEqual(classify_observation(_tool, output, is_error=is_error), expected, output)

    def test_features_carry_no_values(self) -> None:
        features = normalize_call("exec", {"command": "cd /home/me/secret-project && npm test --watch=false"}, salt=SALT)
        self.assertEqual(features.key, "exec|npm|test")
        self.assertEqual(features.shape["flags"], 1)
        self.assertNotIn("secret-project", json.dumps(features.shape))
        self.assertEqual(len(features.args_hash), 16)
        browser = normalize_call("browser", {"url": "https://api.example.test/v1/users/42?token=abc", "action": "fetch"}, salt=SALT)
        self.assertEqual(browser.key, "browser|fetch|api.example.test")
        self.assertNotIn("42", json.dumps(browser.shape))
        self.assertNotIn("token", json.dumps(browser.shape))
        file = normalize_call("read_file", {"path": "/home/me/.ssh/id_rsa.pub"}, salt=SALT)
        self.assertEqual(file.key, "read_file|.pub")
        self.assertNotIn("id_rsa", json.dumps(file.shape))
        # The salt changes the hash: two projects never share an argument hash.
        other = normalize_call("exec", {"command": "cd /home/me/secret-project && npm test --watch=false"}, salt="other")
        self.assertNotEqual(features.args_hash, other.args_hash)

    def test_record_shape_and_unique_ids(self) -> None:
        history = SessionHistory()
        history.push("s", "exec", "ok")
        first = build_trajectory(
            tool_name="exec", arguments=SECRET_ARGS, result=SECRET_OUTPUT, is_error=True, salt=SALT,
            duration_ms=7, prev=history.prev("s"), session="s", turn="t-9",
        )
        second = build_trajectory(
            tool_name="exec", arguments=SECRET_ARGS, result=SECRET_OUTPUT, is_error=True, salt=SALT,
            duration_ms=7, prev=history.prev("s"), session="s", turn="t-9",
        )
        self.assertNotEqual(first.id, second.id, "same call twice in a second: two ids, two buckets possible")
        record = first.as_record()
        line = json.dumps(record)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, line, marker)
        self.assertEqual(record["cls"], "denied")
        self.assertFalse(record["ok"])
        self.assertEqual(record["prev"], ["exec:ok"])
        self.assertEqual(record["turn"], "t-9")
        self.assertLessEqual(len(record["obs"]), 96)
        self.assertTrue(set(record) >= {"v", "id", "ts", "tool", "key", "args", "shape", "cls", "fp", "obs", "exit", "ms", "ok", "prev", "session"})
        self.assertNotIn("command", line, "arguments are hashed, never stored")

    def test_split_is_stable_and_about_one_in_five(self) -> None:
        ids = [f"row-{i}" for i in range(5000)]
        held = sum(1 for i in ids if split_of(i) == "heldout")
        self.assertAlmostEqual(held / len(ids), 0.2, delta=0.03)
        self.assertEqual([split_of(i) for i in ids[:50]], [split_of(i) for i in ids[:50]])


class JournalHookTest(_Workspace):
    def _hook(self) -> WorldModelHook:
        hook = create_world_model_hook(_turn(self.workspace))
        self.assertIsInstance(hook, WorldModelHook)
        return hook  # type: ignore[return-value]

    def _tool_call(self, hook: WorldModelHook, name: str, args: Any, result: Any, *, error: bool = False, tool: Any = None) -> None:
        call = _call(name, args)
        asyncio.run(hook.before_execute_tool(_hook_ctx(), call, tool, args))
        if error:
            asyncio.run(hook.on_execute_tool_error(_hook_ctx(), call, tool, args, result))
        else:
            asyncio.run(hook.after_execute_tool(_hook_ctx(), call, tool, args, result))

    def test_one_line_per_call_and_no_secret_on_disk(self) -> None:
        self.enable()
        hook = self._hook()
        self._tool_call(hook, "exec", SECRET_ARGS, SECRET_OUTPUT, error=True)
        self._tool_call(hook, "read_file", {"path": "src/app.py"}, "print('x')", tool=_ReadOnlyTool())
        self._tool_call(hook, "exec", {"command": "sleep 99"}, TimeoutError("cancelled after 30s"), error=True)
        self._tool_call(hook, "apply_patch", {"patch": "--- a\n+++ b"}, "patched", tool=_WriteTool())
        self.assertTrue(flush_recorder(5.0))
        raw = trajectories_path(self.workspace).read_text(encoding="utf-8")
        self.assertEqual(raw.count("\n"), 4)
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, raw, marker)
        # The command line itself never reaches the disk: program name only.
        self.assertNotIn("alice", raw)
        self.assertNotIn("api.example.test", raw)
        self.assertNotIn("Bearer", raw)
        rows = read_trajectories(self.workspace)
        self.assertEqual([r["cls"] for r in rows], ["denied", "ok", "timeout", "changed"])
        self.assertEqual(rows[1]["prev"], ["exec:denied"])
        self.assertEqual(rows[2]["prev"], ["exec:denied", "read_file:ok"])
        self.assertEqual(rows[0]["turn"], "t-1", "linked to the S1 episode of the turn")
        self.assertTrue(all(r["session"] == "webui:chat-1" for r in rows))
        self.assertTrue(all(isinstance(r["ms"], int) for r in rows))
        self.assertEqual(count_trajectories(self.workspace), 4)
        # Only the world folder appeared: flag, salt, journal.
        self.assertEqual(
            set(self.files()),
            {".navin", ".navin/world", ".navin/world/salt", ".navin/world/trajectories.jsonl", f".navin/{SETTINGS_NAME}"},
        )

    def test_log_off_writes_nothing_even_with_the_hook(self) -> None:
        """A hook exists for advice tracking; the journal corridor is separate."""
        self.enable(log=False)
        self.assertIsNone(create_world_model_hook(_turn(self.workspace)), "no advice, no log: no hook")

    def test_the_turn_never_waits_for_the_disk(self) -> None:
        self.enable()
        hook = self._hook()
        gate = threading.Event()
        original = hook_module._RECORDER._record

        def slow_record(item: Any) -> None:
            gate.wait(5.0)
            original(item)

        with patch.object(hook_module._RECORDER, "_record", slow_record):
            started = time.perf_counter()
            self._tool_call(hook, "exec", {"command": "ls"}, "a\nb")
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.2, "after_execute_tool returned while the recorder was blocked")
            self.assertFalse(trajectories_path(self.workspace).exists(), "nothing on disk yet: the turn is not waiting")
            gate.set()
            self.assertTrue(flush_recorder(5.0))
        self.assertEqual(count_trajectories(self.workspace), 1)

    def test_per_call_cost_is_a_copy_and_a_queue_put(self) -> None:
        self.enable()
        hook = self._hook()
        args = {"command": "ls -la"}
        loops = 2000
        # The async wrappers only forward to these two synchronous steps; timing
        # them directly keeps the event-loop start-up out of the measure.
        start = time.perf_counter()
        for i in range(loops):
            call = _call("exec", args, call_id=f"c{i}")
            hook._started[hook._key(call)] = time.monotonic()
            hook._submit(call, None, args, "ok", is_error=False)
        elapsed = time.perf_counter() - start
        self.assertTrue(flush_recorder(10.0))
        self.assertLess(elapsed / loops, 500e-6, f"{elapsed / loops * 1e6:.1f} us per call on the turn's thread")
        self.assertEqual(count_trajectories(self.workspace), loops)

    def test_hook_never_raises_on_odd_results(self) -> None:
        self.enable()
        hook = self._hook()
        odd: list[Any] = [None, b"bytes", {"a": object()}, 42, ["x", 1], ValueError("bad")]
        for index, result in enumerate(odd):
            call = _call("exec", "not json {", call_id=f"o{index}")
            asyncio.run(hook.after_execute_tool(_hook_ctx(), call, None, None, result))
        asyncio.run(hook.after_run(_run_ctx()))
        asyncio.run(hook.on_error(_run_ctx()))
        self.assertTrue(flush_recorder(5.0))
        self.assertEqual(count_trajectories(self.workspace), len(odd))

    def test_after_run_kicks_the_trainer_only_when_train_is_on(self) -> None:
        self.enable(train=False)
        hook = self._hook()
        with patch.object(jobs, "kick") as kick:
            asyncio.run(hook.after_run(_run_ctx()))
        kick.assert_not_called()
        update_settings(self.workspace, {"train": True})
        hook = self._hook()
        with patch.object(jobs, "kick") as kick:
            asyncio.run(hook.after_run(_run_ctx()))
        kick.assert_called_once()

    def test_rotation_keeps_one_previous_generation(self) -> None:
        from navin.world_model import journal as journal_module

        self.enable()
        with patch.object(journal_module._WRITER, "_max_bytes", 2048):
            _fill_journal(self.workspace, cycles=4)
        path = trajectories_path(self.workspace)
        self.assertTrue(path.with_name("trajectories.1.jsonl").exists())
        self.assertLess(path.stat().st_size, 4096)


# --------------------------------------------------------------------------
# S3.2 / S3.3 - local head, frozen exam, scoreboard, checkpoints
# --------------------------------------------------------------------------


class ModelTest(unittest.TestCase):
    def _rows(self) -> list[dict[str, Any]]:
        history = SessionHistory()
        rows = []
        for _ in range(30):
            for tool, args, output, is_error, _expected in SCENARIO:
                t = build_trajectory(
                    tool_name=tool, arguments=args, result=output, is_error=is_error, salt=SALT,
                    duration_ms=1, prev=history.prev("s"), session="s", turn=None,
                )
                history.push("s", t.tool, t.cls)
                rows.append(t.as_record())
        return rows

    def test_baselines_are_reproducible_and_the_head_beats_them(self) -> None:
        rows = self._rows()
        held = [r for r in rows if split_of(r["id"]) == "heldout"]
        train_rows = training_rows(rows, None)
        self.assertGreaterEqual(len(held), 8)
        always = evaluate(AlwaysOkModel(), held)
        majority = evaluate(MajorityModel(train_rows), held)
        self.assertEqual(evaluate(AlwaysOkModel(), held), always, "the baseline is a pure function of the set")
        head = evaluate(CountModel().fit(train_rows), held)
        self.assertLess(head.log_loss, min(always.log_loss, majority.log_loss))
        self.assertLess(head.error_rate, always.error_rate)
        self.assertEqual(compare(head, majority), "up")
        self.assertEqual(compare(head, always), "up")
        self.assertEqual(compare(majority, majority), "flat")
        self.assertEqual(compare(always, head), "down")
        self.assertGreaterEqual(head.ece, 0.0)

    def test_head_predicts_with_confidence_and_round_trips(self) -> None:
        rows = self._rows()
        model = CountModel().fit(rows)
        npm = normalize_call("exec", {"command": "npm test"}, salt=SALT)
        prediction = model.predict(npm.tool, npm.key, npm.args_hash, [])
        self.assertEqual(prediction.cls, "error")
        self.assertGreater(prediction.confidence, 0.8)
        self.assertGreaterEqual(prediction.support, 3)
        again = CountModel.from_dict(model.to_dict())
        self.assertEqual(again.predict(npm.tool, npm.key, npm.args_hash, []).as_dict(), prediction.as_dict())
        unknown = model.predict("never_seen", "never_seen|x", "0000", [])
        self.assertEqual(unknown.level, "global")
        self.assertLess(unknown.confidence, 0.6)


class TrainingTest(_Workspace):
    def test_train_freezes_scores_activates_and_journals(self) -> None:
        result = self.trained()
        self.assertEqual(result.checkpoint, 1)
        self.assertTrue(result.activated)
        self.assertEqual(result.verdict_vs_baseline, "up")
        self.assertLess(result.metrics["log_loss"], result.baseline["log_loss"])
        self.assertIn(result.baseline["kind"], ("majority", "always_ok"))
        heldout = load_heldout(self.workspace)
        self.assertIsNotNone(heldout)
        self.assertEqual(heldout.version, result.heldout_version)
        self.assertGreaterEqual(len(heldout.rows), 8)
        # Frozen rows never trained on.
        frozen = heldout.ids
        self.assertTrue(all(split_of(i) == "heldout" for i in frozen))
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)
        self.assertIsNotNone(ck.active_checkpoint(self.workspace))
        board = train_module.read_scoreboard(self.workspace)
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["verdict_vs_baseline"], "up")
        self.assertTrue(board[0]["activated"])
        self.assertEqual(self.events(), ["heldout_frozen", "trained"])
        state = world_state(self.workspace)
        self.assertEqual(state["active"]["checkpoint"], 1)
        self.assertEqual(state["score"]["verdict"], "up")
        self.assertEqual(len(state["checkpoints"]), 1)
        self.assertTrue(state["checkpoints"][0]["active"])

    def test_not_enough_data_writes_no_checkpoint(self) -> None:
        self.enable()
        _fill_journal(self.workspace, cycles=2)
        result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "not_enough_data")
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [])
        self.assertIsNone(ck.read_active(self.workspace))

    def test_train_corridor_off_skips_unless_a_human_forces(self) -> None:
        self.enable(train=False)
        _fill_journal(self.workspace, cycles=40)
        self.assertEqual(train(self.workspace).status, "skipped")
        self.assertFalse(train_module.training_due(self.workspace))
        self.assertIsNone(jobs.run_due(self.workspace))
        self.assertEqual(train(self.workspace, actor="human", force=True).status, "trained")

    def test_budget_exceeded_fails_and_leaves_production_intact(self) -> None:
        self.trained()
        before = self.files()
        result = train(self.workspace, actor="human", force=True, budget=TrainBudget(timeout_s=0.0))
        self.assertEqual(result.status, "budget_exceeded")
        self.assertIn("timeout", result.reason)
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1])
        self.assertEqual(self.events()[-1], "train_failed")
        after = [f for f in self.files() if not f.endswith("journal.jsonl")]
        self.assertEqual(after, [f for f in before if not f.endswith("journal.jsonl")])

    def test_down_keeps_the_checkpoint_as_evidence_but_does_not_activate(self) -> None:
        self.trained()
        with patch.object(train_module, "compare", lambda candidate, reference: "down"):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "trained")
        self.assertEqual(result.checkpoint, 2)
        self.assertFalse(result.activated)
        self.assertEqual(result.verdict_vs_baseline, "down")
        self.assertEqual(result.reason, "did not beat the baseline")
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1, 2], "kept on disk")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1, "the pointer did not move")
        board = train_module.read_scoreboard(self.workspace)
        self.assertEqual(board[-1]["checkpoint"], 2)
        self.assertFalse(board[-1]["activated"])
        summaries = ck.checkpoint_summaries(self.workspace)
        self.assertEqual([s["active"] for s in summaries], [True, False])

    def test_regression_vs_the_active_head_does_not_replace_it(self) -> None:
        self.trained()
        # First compare is vs the baseline (up), the second vs the serving head (down).
        with patch.object(train_module, "compare", side_effect=["up", "down"]):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.verdict_vs_baseline, "up")
        self.assertEqual(result.verdict_vs_active, "down")
        self.assertFalse(result.activated)
        self.assertEqual(result.reason, "kept as evidence, not activated")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)

    def test_rollback_goes_back_to_n_minus_one_and_is_journaled(self) -> None:
        self.trained()
        second = train(self.workspace, actor="human", force=True)
        self.assertEqual(second.checkpoint, 2)
        self.assertTrue(second.activated)
        active = ck.read_active(self.workspace)
        self.assertEqual((active["checkpoint"], active["previous"]), (2, 1))
        rolled = rollback(self.workspace, actor="human")
        self.assertEqual((rolled["from"], rolled["to"]), (2, 1))
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)
        self.assertEqual(ck.active_checkpoint(self.workspace).number, 1)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1, 2], "the newer one stays as evidence")
        self.assertEqual(self.events()[-1], "rollback")
        # Once more: nothing older, nothing serves.
        rolled = rollback(self.workspace, actor="human")
        self.assertEqual(rolled["status"], "rolled_back")
        self.assertIsNone(rolled["to"])
        self.assertIsNone(ck.read_active(self.workspace))
        self.assertEqual(rollback(self.workspace, actor="human")["status"], "nothing_active")

    def test_exam_rescoring_uses_the_same_frozen_set(self) -> None:
        result = self.trained()
        scored = exam(self.workspace, actor="human")
        self.assertEqual(scored["status"], "scored")
        self.assertEqual(scored["heldout_version"], result.heldout_version)
        self.assertEqual(scored["checkpoint"], 1)
        self.assertEqual(scored["verdict_vs_baseline"], "up")
        self.assertTrue(scored["exam"])
        self.assertEqual(len(train_module.read_scoreboard(self.workspace)), 2)
        self.assertEqual(self.events()[-1], "examined")

    def test_editing_the_frozen_set_is_caught_everywhere(self) -> None:
        self.trained()
        path = heldout_path(self.workspace)
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(HeldoutTamperedError):
            load_heldout(self.workspace)
        self.assertEqual(train(self.workspace, actor="human", force=True).status, "tampered")
        self.assertEqual(exam(self.workspace)["status"], "tampered")
        self.assertEqual(run_ab(self.workspace)["status"], "tampered")
        self.assertFalse(advice_gate(self.workspace).open)
        with self.assertRaises(WorldActionError) as caught:
            world_action(self.workspace, "exam", actor="human")
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("modified on disk", str(caught.exception))
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1], "no new checkpoint from a reworked exam")
        self.assertIn("error", world_state(self.workspace)["heldout"])

    def test_a_new_freeze_is_a_new_version_and_scores_do_not_carry_over(self) -> None:
        result = self.trained()
        _fill_journal(self.workspace, cycles=20, session="webui:chat-2")
        frozen = freeze(self.workspace, actor="human")
        self.assertEqual(frozen["status"], "frozen")
        self.assertNotEqual(frozen["version"], result.heldout_version)
        self.assertGreater(frozen["rows"], 0)
        gate = advice_gate(self.workspace)
        self.assertFalse(gate.open)
        self.assertIn("the active checkpoint has no score on the current exam set", gate.reasons)
        self.assertIsNone(world_state(self.workspace)["score"], "old scores never compare with the new set")

    def test_training_due_and_the_runner_path(self) -> None:
        self.enable(train_every=20)
        self.assertFalse(train_module.training_due(self.workspace))
        _fill_journal(self.workspace, cycles=40)
        self.assertTrue(train_module.training_due(self.workspace))
        self.assertTrue(jobs.kick(self.workspace), "on: the kick is accepted (no thread in tests)")
        self.assertFalse(jobs.runner_alive())
        ran = jobs.run_due(self.workspace)
        self.assertIsNotNone(ran)
        self.assertEqual(ran["status"], "trained")
        self.assertEqual(ran["checkpoint"], 1)
        self.assertFalse(train_module.training_due(self.workspace), "rows_total recorded")
        self.assertIsNone(jobs.run_due(self.workspace), "not due, and too soon anyway")
        _fill_journal(self.workspace, cycles=4)
        self.assertTrue(train_module.training_due(self.workspace))
        jobs.reset_runner()
        self.assertEqual(jobs.run_due(self.workspace)["checkpoint"], 2)
        self.assertEqual(train_module.read_train_state(self.workspace)["last_checkpoint"], 2)

    def test_prune_keeps_recent_active_and_previous(self) -> None:
        self.trained()
        for _ in range(7):
            train(self.workspace, actor="human", force=True)
        numbers = ck.list_checkpoint_numbers(self.workspace)
        self.assertLessEqual(len(numbers), ck.KEEP_CHECKPOINTS + 1)
        active = ck.read_active(self.workspace)
        self.assertIn(active["checkpoint"], numbers)
        self.assertIn(active["previous"], numbers)


# --------------------------------------------------------------------------
# S3.4 - readable beliefs, never the brain
# --------------------------------------------------------------------------


class BeliefsTest(_Workspace):
    def test_train_with_beliefs_on_writes_ten_lines_max(self) -> None:
        self.enable(beliefs=True)
        _fill_journal(self.workspace, cycles=40)
        train(self.workspace, actor="human", force=True)
        text = read_beliefs_file(self.workspace)
        self.assertIsNotNone(text)
        self.assertIn(BEGIN, text)
        self.assertIn(END, text)
        block = text.split(BEGIN, 1)[1].split(END, 1)[0]
        lines = [line for line in block.splitlines() if line.startswith("- ")]
        self.assertGreaterEqual(len(lines), 2)
        self.assertLessEqual(len(lines), 10)
        joined = "\n".join(lines)
        self.assertIn("npm test", joined)
        self.assertIn("error", joined)
        self.assertIn("denied", joined)
        self.assertNotIn("`exec ls`", joined, "a belief is never 'this works'")
        keys = keys_in_file(text)
        self.assertEqual(len(keys), len(lines))
        for marker in SECRET_MARKERS:
            self.assertNotIn(marker, text)

    def test_beliefs_off_by_default_writes_no_file(self) -> None:
        self.trained()
        self.assertIsNone(read_beliefs_file(self.workspace))
        self.assertFalse(beliefs_file(self.workspace).exists())
        # But the panel still lists the beliefs of the head, from the head.
        self.assertGreaterEqual(len(current_beliefs(self.workspace)), 2)

    def test_human_edits_survive_and_discards_stick_without_retraining(self) -> None:
        self.trained()
        render_beliefs(self.workspace)
        path = beliefs_file(self.workspace)
        text = path.read_text(encoding="utf-8")
        path.write_text("My own note above.\n\n" + text + "\nMy own note below.\n", encoding="utf-8")
        checkpoints_before = ck.list_checkpoint_numbers(self.workspace)
        beliefs = current_beliefs(self.workspace)
        target = beliefs[0].key
        remaining = discard_belief(self.workspace, target)
        self.assertNotIn(target, [b.key for b in remaining])
        after = path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith("My own note above."))
        self.assertTrue(after.rstrip().endswith("My own note below."))
        self.assertNotIn(f"key:{target} ", after)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), checkpoints_before, "no retrain")
        self.assertEqual(self.events().count("trained"), 1)
        restored = restore_beliefs(self.workspace)
        self.assertIn(target, [b.key for b in restored])
        self.assertIn(f"key:{target} ", path.read_text(encoding="utf-8"))
        state = world_state(self.workspace)
        self.assertEqual(state["beliefs_ignored"], [])
        self.assertIn(target, [b["key"] for b in state["belief_items"]])

    def test_beliefs_are_not_in_the_prompt_while_advise_is_off(self) -> None:
        self.enable(beliefs=True)
        _fill_journal(self.workspace, cycles=40)
        train(self.workspace, actor="human", force=True)
        run_ab(self.workspace, actor="human")
        self.assertTrue(advice_gate(self.workspace).open, "gate open, yet advise is off")
        self.assertTrue(beliefs_file(self.workspace).exists())
        self.assertFalse(advice_open(self.workspace))
        self.assertEqual(advice_lines(self.workspace), [])
        self.assertIsNone(advice_block_text(self.workspace))
        from navin.agent.tools.world_predict import WorldPredictTool

        class _Ctx:
            workspace = self.workspace

        self.assertFalse(WorldPredictTool.enabled(_Ctx()))
        registry = _Registry()
        from navin.world_model.registration import sync_world_tool

        self.assertFalse(sync_world_tool(registry, workspace=self.workspace))
        self.assertEqual(registry.tools, {})

    def test_beliefs_action_needs_a_head(self) -> None:
        self.enable()
        with self.assertRaises(WorldActionError) as caught:
            world_action(self.workspace, "beliefs", actor="human")
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(beliefs_file(self.workspace).exists())


# --------------------------------------------------------------------------
# S3.5 - live advice behind the gates, kill switch
# --------------------------------------------------------------------------


class AdviceGateTest(_Workspace):
    def test_gate_reasons_step_by_step(self) -> None:
        self.enable()
        self.assertEqual(advice_gate(self.workspace).reasons, ("no active checkpoint: train first",))
        _fill_journal(self.workspace, cycles=40)
        train(self.workspace, actor="human", force=True)
        gate = advice_gate(self.workspace)
        self.assertFalse(gate.open)
        self.assertEqual(gate.exam_verdict, "up")
        self.assertEqual(gate.reasons, ("no offline A/B for this checkpoint",))
        ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["verdict"], "gain")
        self.assertGreater(ab["avoided"], 0)
        self.assertEqual(ab["false_alarms"], 0)
        gate = advice_gate(self.workspace)
        self.assertTrue(gate.open)
        self.assertEqual(gate.ab_verdict, "gain")
        self.assertEqual(self.events()[-1], "ab")

    def test_ab_without_gain_keeps_the_gate_closed(self) -> None:
        self.trained()
        from navin.world_model import advisor

        with patch.object(advisor, "_replay", lambda model, rows, threshold: {
            "n": len(rows), "useless_calls": 5, "flagged": 4, "avoided": 2, "false_alarms": 2,
            "precision": 0.5, "avoided_share": 0.4, "verdict": "regress",
        }):
            ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["verdict"], "regress")
        gate = advice_gate(self.workspace)
        self.assertFalse(gate.open)
        self.assertIn("offline A/B says regress, not gain", gate.reasons)
        with self.assertRaises(WorldActionError) as caught:
            world_update(self.workspace, {"advise": True})
        self.assertEqual(caught.exception.status, 409)

    def test_advise_on_once_the_gates_open_and_the_prompt_hears_three_lines_at_most(self) -> None:
        self.open_gate()
        state = world_update(self.workspace, {"advise": True})
        self.assertTrue(state["advise"])
        self.assertIn("advise_on", self.events())
        self.assertTrue(advice_open(self.workspace))
        lines = advice_lines(self.workspace)
        self.assertGreaterEqual(len(lines), 1)
        self.assertLessEqual(len(lines), 3)
        text = advice_block_text(self.workspace)
        self.assertIsNotNone(text)
        self.assertTrue(text.startswith("World model (advisory"))
        self.assertIn("never skip a write, delete, mail or payment", text)
        self.assertLessEqual(len(text.splitlines()), 4)
        # The machine index remembers the project so a gateway can re-sync at boot.
        self.assertTrue(self.index_file.exists())
        self.assertIn(str(self.workspace.resolve()), self.index_file.read_text(encoding="utf-8"))
        world_update(self.workspace, {"advise": False})
        self.assertNotIn(str(self.workspace.resolve()), self.index_file.read_text(encoding="utf-8"))
        self.assertIsNone(advice_block_text(self.workspace))

    def test_world_tool_appears_with_the_gate_and_predicts_without_blocking(self) -> None:
        from navin.agent.tools.world_predict import WorldPredictTool
        from navin.world_model.registration import sync_world_tool

        self.open_gate()
        registry = _Registry()
        self.assertFalse(sync_world_tool(registry, workspace=self.workspace), "advise still off")
        world_update(self.workspace, {"advise": True})
        self.assertTrue(sync_world_tool(registry, workspace=self.workspace))
        tool = registry.get("world_predict")
        self.assertIsInstance(tool, WorldPredictTool)
        self.assertTrue(tool.read_only)
        # Same salt as the journal: the tool sees the same features as the head.
        with patch("navin.world_model.journal.project_salt", lambda ws: SALT):
            answer = asyncio.run(tool.execute(tool="exec", arguments="npm test"))
        self.assertIn("expected error", answer)
        self.assertIn("Advisory only", answer)
        self.assertIn("Usually fails here", answer)
        never_seen = asyncio.run(tool.execute(tool="exec", arguments="cargo build"))
        self.assertIn("Low confidence: make the call", never_seen)
        provide = tool.runtime_context_provider()
        block = asyncio.run(provide(type("R", (), {"workspace": self.workspace, "session_key": "webui:x"})()))
        self.assertIsNotNone(block)
        self.assertEqual(block.source, "world-model")
        self.assertTrue(block.content.startswith("World model (advisory"))
        heartbeat = asyncio.run(provide(type("R", (), {"workspace": self.workspace, "session_key": "heartbeat"})()))
        self.assertIsNone(heartbeat)
        # Gate lost (advise off): the tool leaves and the prompt is what it was.
        world_update(self.workspace, {"advise": False})
        self.assertFalse(sync_world_tool(registry, workspace=self.workspace))
        self.assertEqual(registry.tools, {})

    def test_suggestions_never_skip_a_write_and_stay_quiet_under_the_threshold(self) -> None:
        from navin.world_model.advisor import suggest
        from navin.world_model.model import Prediction

        sure_denied = Prediction(cls="denied", confidence=0.95, probs=(0.0,) * len(CLASSES), support=12, level="key")
        self.assertEqual(suggest(sure_denied, tool="exec", label="git push", threshold=0.8).kind, "useless")
        sure_error = Prediction(cls="error", confidence=0.9, probs=(0.0,) * len(CLASSES), support=12, level="key")
        self.assertEqual(suggest(sure_error, tool="exec", label="npm test", threshold=0.8).kind, "risky")
        unsure = Prediction(cls="denied", confidence=0.7, probs=(0.0,) * len(CLASSES), support=12, level="key")
        self.assertIsNone(suggest(unsure, tool="exec", label="x", threshold=0.8))
        thin = Prediction(cls="denied", confidence=0.99, probs=(0.0,) * len(CLASSES), support=2, level="call")
        self.assertIsNone(suggest(thin, tool="exec", label="x", threshold=0.8))
        fine = Prediction(cls="ok", confidence=0.99, probs=(0.0,) * len(CLASSES), support=50, level="key")
        self.assertIsNone(suggest(fine, tool="exec", label="ls", threshold=0.8), "nothing to say when it works")
        # "Seen": only read-only tools, never a write / shell / mail / payment.
        self.assertEqual(suggest(fine, tool="read_file", label="x", threshold=0.8, seen_same_answer=True, read_only=True).kind, "seen")
        for tool in ("write_file", "delete_file", "send_email", "payment", "exec", "apply_patch"):
            self.assertIsNone(suggest(fine, tool=tool, label="x", threshold=0.8, seen_same_answer=True, read_only=True), tool)
        self.assertIsNone(suggest(fine, tool="read_file", label="x", threshold=0.8, seen_same_answer=True, read_only=False))

    def test_hook_tracks_live_advice_only_when_advise_is_open(self) -> None:
        self.open_gate()
        hook = create_world_model_hook(_turn(self.workspace))
        self.assertFalse(hook._advise_open, "gate open but advise off: nothing tracked")
        world_update(self.workspace, {"advise": True})
        hook = create_world_model_hook(_turn(self.workspace))
        self.assertTrue(hook._advise_open)
        with patch.object(hook_module, "project_salt", lambda ws: SALT):
            args = {"command": "npm test"}
            call = _call("exec", args, call_id="live-1")
            asyncio.run(hook.before_execute_tool(_hook_ctx(), call, None, args))
            asyncio.run(hook.on_execute_tool_error(_hook_ctx(), call, None, args, "npm ERR! Test failed"))
            self.assertTrue(flush_recorder(5.0))
        live = live_tracker().snapshot(self.workspace)
        self.assertEqual(live["advised"], 1)
        self.assertEqual(live["precision"], 1.0)
        self.assertEqual(world_state(self.workspace)["live"]["logged"], 1)
        self.assertTrue(read_settings(self.workspace).advise, "one hit: nothing cut")

    def test_kill_switch_cuts_advice_and_rolls_back(self) -> None:
        self.open_gate()
        second = train(self.workspace, actor="human", force=True)
        self.assertEqual(second.checkpoint, 2)
        run_ab(self.workspace, actor="human")
        world_update(self.workspace, {"advise": True})
        self.assertTrue(advice_open(self.workspace))
        registry = _Registry()
        from navin.world_model.registration import sync_world_tool

        self.assertTrue(sync_world_tool(registry, workspace=self.workspace))
        wrong = Suggestion(kind="useless", cls="denied", confidence=0.95, support=10, text="x")
        fired = False
        for _ in range(KILL_MIN_ADVICE):
            fired = live_tracker().record(self.workspace, suggestion=wrong, actual_cls="ok") or fired
        self.assertTrue(fired)
        self.assertFalse(read_settings(self.workspace).advise, "advise switched itself off")
        self.assertTrue(read_settings(self.workspace).enabled, "the journal and training go on")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1, "back to N-1")
        self.assertIn("advise_cut", self.events())
        self.assertIn("rollback", self.events())
        self.assertFalse(advice_open(self.workspace))
        self.assertIsNone(advice_block_text(self.workspace))
        self.assertFalse(sync_world_tool(registry, workspace=self.workspace), "the tool leaves at the next sync")
        self.assertEqual(registry.tools, {})
        # A human has to switch it on again, and only once the gate reopens.
        with self.assertRaises(WorldActionError):
            world_update(self.workspace, {"advise": True})
        # One hit later, no second cut: the switch fired once.
        again = live_tracker().record(self.workspace, suggestion=wrong, actual_cls="ok")
        self.assertFalse(again)


class HumanButtonsTest(_Workspace):
    def test_human_only_actions_refuse_the_engine(self) -> None:
        self.enable()
        for action in ("train", "freeze", "rollback", "discard_belief", "restore_beliefs"):
            with self.assertRaises(WorldActionError) as caught:
                world_action(self.workspace, action, actor="auto")
            self.assertEqual(caught.exception.status, 403, action)
        with self.assertRaises(WorldActionError) as caught:
            world_action(self.workspace, "nope", actor="human")
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(WorldActionError) as caught:
            world_action(self.workspace, "discard_belief", actor="human")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [])

    def test_buttons_end_to_end(self) -> None:
        self.enable()
        _fill_journal(self.workspace, cycles=40)
        payload = world_action(self.workspace, "run", actor="auto")
        self.assertEqual(payload["result"]["status"], "trained")
        self.assertEqual(payload["state"]["active"]["checkpoint"], 1)
        payload = world_action(self.workspace, "exam", actor="human")
        self.assertEqual(payload["result"]["status"], "scored")
        payload = world_action(self.workspace, "ab", actor="human")
        self.assertEqual(payload["result"]["verdict"], "gain")
        self.assertTrue(payload["state"]["gate"]["open"])
        payload = world_action(self.workspace, "beliefs", actor="human")
        self.assertTrue(beliefs_file(self.workspace).exists())
        self.assertGreaterEqual(len(payload["result"]["beliefs"]), 1)
        key = payload["result"]["beliefs"][0]["key"]
        payload = world_action(self.workspace, "discard_belief", actor="human", key=key)
        self.assertNotIn(key, [b["key"] for b in payload["state"]["belief_items"]])
        payload = world_action(self.workspace, "restore_beliefs", actor="human")
        self.assertIn(key, [b["key"] for b in payload["state"]["belief_items"]])
        payload = world_action(self.workspace, "train", actor="human")
        self.assertEqual(payload["result"]["checkpoint"], 2)
        payload = world_action(self.workspace, "rollback", actor="human")
        self.assertEqual(payload["result"]["to"], 1)
        self.assertEqual(payload["state"]["active"]["checkpoint"], 1)
        _fill_journal(self.workspace, cycles=10, session="webui:chat-3")
        payload = world_action(self.workspace, "freeze", actor="human")
        self.assertEqual(payload["result"]["status"], "frozen")
        self.assertFalse(payload["state"]["gate"]["open"], "new set: the head must prove itself again")
        self.assertEqual(world_action(self.workspace, "run", actor="auto")["result"]["status"], "not_due")


# --------------------------------------------------------------------------
# API / CLI
# --------------------------------------------------------------------------


class WorldRouteTest(_Workspace):
    """The AGI panel talks HTTP: the routes must answer and keep the actor rule."""

    def _handler(self):  # type: ignore[no-untyped-def]
        from navin.webui.ws_http import GatewayHTTPHandler

        project = str(self.workspace)

        class _Scope:
            project_path = project

        class _Workspaces:
            def scope_for_session_key(self, key: str):  # type: ignore[no-untyped-def]
                return _Scope()

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.bus = None
        handler.workspaces = _Workspaces()
        handler.skills_workspace_path = self.workspace / "gateway-home"
        return handler

    def _get(self, handler, path: str):  # type: ignore[no-untyped-def]
        class _Request:
            def __init__(self, full_path: str) -> None:
                self.path = full_path
                self.headers = {}

        got = path.split("?", 1)[0]
        return asyncio.run(handler._dispatch_session_routes(_Request(path), got))

    def test_read_then_toggle_over_http(self) -> None:
        handler = self._handler()
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertFalse(body["enabled"])
        self.assertEqual(body["settings_file"], ".navin/world-model.json")
        self.assertEqual(body["world_dir"], ".navin/world")
        self.assertFalse((self.workspace / ".navin").exists(), "reading creates nothing")
        response = self._get(handler, '/api/sessions/websocket%3Aabc/world?fields={"enabled":true}')
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertTrue(body["enabled"] and body["log"] and body["train"])
        self.assertFalse(body["advise"])
        response = self._get(handler, '/api/sessions/websocket%3Aabc/world?fields={"log":false}')
        self.assertFalse(json.loads(response.body)["log"])
        self.assertTrue(json.loads(response.body)["enabled"])

    def test_advise_refused_over_http_while_the_gate_is_closed(self) -> None:
        handler = self._handler()
        self.enable()
        response = self._get(handler, '/api/sessions/websocket%3Aabc/world?fields={"advise":true}')
        self.assertEqual(response.status_code, 409)
        self.assertIn("gate closed", response.body.decode("utf-8"))
        self.assertFalse(read_settings(self.workspace).advise)

    def test_bad_fields_answer_400_and_write_nothing(self) -> None:
        handler = self._handler()
        for query in ("fields=nope", 'fields=["x"]', 'fields={"bogus":true}', 'fields={"enabled":"yes"}', 'fields={"min_rows":1}'):
            response = self._get(handler, f"/api/sessions/websocket%3Aabc/world?{query}")
            self.assertEqual(response.status_code, 400, query)
        self.assertFalse(settings_path(self.workspace).exists())

    def test_actions_keep_the_actor_rule(self) -> None:
        handler = self._handler()
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=train&actor=human")
        self.assertEqual(response.status_code, 409, "flag off")
        self.enable()
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=train")
        self.assertEqual(response.status_code, 403, "no actor means the engine: refused")
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=train&actor=human")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["result"]["status"], "not_enough_data")
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action")
        self.assertEqual(response.status_code, 400)
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=bogus&actor=human")
        self.assertEqual(response.status_code, 400)
        _fill_journal(self.workspace, cycles=40)
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=train&actor=human")
        payload = json.loads(response.body)
        self.assertEqual(payload["result"]["status"], "trained")
        self.assertEqual(payload["state"]["active"]["checkpoint"], 1)
        response = self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=ab")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["result"]["verdict"], "gain")
        response = self._get(handler, '/api/sessions/websocket%3Aabc/world?fields={"advise":true}')
        self.assertEqual(response.status_code, 200, "gate open: advise accepted")
        self.assertTrue(json.loads(response.body)["advise"])

    def test_non_webui_session_is_404(self) -> None:
        response = self._get(self._handler(), "/api/sessions/telegram%3A42/world")
        self.assertEqual(response.status_code, 404)

    def test_every_read_syncs_the_tool_with_the_gate(self) -> None:
        """A flag flipped from the CLI reaches the serving agent at the next panel read."""
        handler = self._handler()
        registry = _Registry()
        handler.tool_registry = lambda: registry
        body = json.loads(self._get(handler, "/api/sessions/websocket%3Aabc/world").body)
        self.assertFalse(body["tool_registered"])
        self.open_gate()
        update_settings(self.workspace, {"advise": True})  # the CLI path: no gateway involved
        self.assertEqual(registry.tools, {}, "nothing registered yet")
        body = json.loads(self._get(handler, "/api/sessions/websocket%3Aabc/world").body)
        self.assertTrue(body["tool_registered"])
        self.assertIn("world_predict", registry.tools)
        response = self._get(handler, '/api/sessions/websocket%3Aabc/world?fields={"advise":false}')
        self.assertFalse(json.loads(response.body)["tool_registered"])
        self.assertEqual(registry.tools, {}, "advise off: the tool left, the prompt is what it was")
        payload = json.loads(self._get(handler, "/api/sessions/websocket%3Aabc/world/action?action=exam").body)
        self.assertFalse(payload["state"]["tool_registered"])


class CliTest(_Workspace):
    def _invoke(self, *args: str):  # type: ignore[no-untyped-def]
        from typer.testing import CliRunner

        from navin.cli.commands import app

        return CliRunner().invoke(app, ["agi", "world", *args, "--project", str(self.workspace)])

    def test_status_on_set_off(self) -> None:
        status = self._invoke("status", "--json")
        self.assertEqual(status.exit_code, 0, status.output)
        payload = json.loads(status.stdout)
        self.assertFalse(payload["enabled"])
        self.assertFalse((self.workspace / ".navin").exists(), "status reads, never writes")

        turned_on = self._invoke("on")
        self.assertEqual(turned_on.exit_code, 0, turned_on.output)
        self.assertTrue(world_enabled(self.workspace, "log"))

        changed = self._invoke("set", "beliefs", "on")
        self.assertEqual(changed.exit_code, 0, changed.output)
        self.assertTrue(read_settings(self.workspace).beliefs)
        refused = self._invoke("set", "advise", "on")
        self.assertNotEqual(refused.exit_code, 0, "gate closed: refused")
        self.assertIn("gate closed", refused.output)
        self.assertFalse(read_settings(self.workspace).advise)
        rejected = self._invoke("set", "min_rows", "1")
        self.assertNotEqual(rejected.exit_code, 0)
        self.assertEqual(read_settings(self.workspace).min_rows, 40)

        turned_off = self._invoke("off")
        self.assertEqual(turned_off.exit_code, 0, turned_off.output)
        self.assertFalse(world_enabled(self.workspace))

    def test_train_exam_ab_rollback_from_the_terminal(self) -> None:
        self.enable()
        refused = self._invoke("train")
        self.assertEqual(refused.exit_code, 0, refused.output)
        self.assertIn("not_enough_data", refused.output)
        _fill_journal(self.workspace, cycles=40)
        trained = self._invoke("train")
        self.assertEqual(trained.exit_code, 0, trained.output)
        self.assertIn("trained", trained.output)
        self.assertIn("activated", trained.output)
        examined = self._invoke("exam")
        self.assertEqual(examined.exit_code, 0, examined.output)
        self.assertIn("up", examined.output)
        ab = self._invoke("ab")
        self.assertEqual(ab.exit_code, 0, ab.output)
        self.assertIn("gain", ab.output)
        log = self._invoke("log", "-n", "3")
        self.assertEqual(log.exit_code, 0, log.output)
        self.assertIn("write_file|.md", log.output)
        self.assertIn("not_found", log.output)
        self.assertNotIn("notes.md", log.output, "the journal shows classes and keys, never arguments")
        checkpoints = self._invoke("checkpoints", "--json")
        self.assertEqual(checkpoints.exit_code, 0, checkpoints.output)
        self.assertEqual(json.loads(checkpoints.stdout)[0]["number"], 1)
        board = self._invoke("scoreboard", "--json")
        self.assertEqual(board.exit_code, 0, board.output)
        self.assertGreaterEqual(len(json.loads(board.stdout)), 2)
        beliefs = self._invoke("beliefs")
        self.assertEqual(beliefs.exit_code, 0, beliefs.output)
        self.assertIn("npm test", beliefs.output)
        rolled = self._invoke("rollback")
        self.assertEqual(rolled.exit_code, 0, rolled.output)
        self.assertIsNone(ck.read_active(self.workspace))
        journal = self._invoke("journal", "--json")
        self.assertEqual(journal.exit_code, 0, journal.output)
        self.assertIn("rollback", [row["event"] for row in json.loads(journal.stdout)])

    def test_agi_status_shows_the_world_switches(self) -> None:
        from typer.testing import CliRunner

        from navin.cli.commands import app

        result = CliRunner().invoke(app, ["agi", "status", "--json", "--project", str(self.workspace)])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.stdout)
        self.assertIn("world_model", payload)
        self.assertFalse(payload["world_model"]["enabled"])
        self.assertFalse(payload["world_model"]["advise"])
        self.assertFalse((self.workspace / ".navin").exists())


# --------------------------------------------------------------------------
# S3.6 - proof that nothing else moved
# --------------------------------------------------------------------------


class TurnEnvelopeTest(_Workspace):
    """Bench: a turn with the flag off costs what it cost before S3 (p95)."""

    TOOL_CALLS = 6

    @staticmethod
    def _chain(factories: list[Any], workspace: Path) -> Any:
        from navin.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook

        return build_agent_turn_hook(
            AgentTurnHookSpec(
                channel="webui",
                chat_id="chat-1",
                session_key="webui:chat-1",
                workspace=workspace,
                metadata={"turn_id": "t-1"},
                registered_hook_factories=factories,
            )
        )

    @staticmethod
    def _hooks_of(chain: Any) -> list[str]:
        inner = getattr(chain, "_hooks", None)
        return [type(h).__name__ for h in (inner if inner is not None else [chain])]

    async def _turn(self, factories: list[Any]) -> None:
        """The per-turn work S3 could touch: assembly, N tool calls, after_run."""
        chain = self._chain(factories, self.workspace)
        ctx = _hook_ctx()
        args = {"command": "ls"}
        for i in range(self.TOOL_CALLS):
            call = _call("exec", args, call_id=f"b{i}")
            await chain.before_execute_tool(ctx, call, None, args)
            await chain.after_execute_tool(ctx, call, None, args, "ok")
        await chain.after_run(_run_ctx())

    def _p95_ms(self, factories: list[Any], rounds: int) -> float:
        samples: list[float] = []

        async def run() -> None:
            for _ in range(rounds):
                start = time.perf_counter()
                await self._turn(factories)
                samples.append((time.perf_counter() - start) * 1000)

        asyncio.run(run())
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    def test_flag_off_turn_has_no_world_hook_and_the_same_p95(self) -> None:
        with_s3 = list(DEFAULT_HOOK_FACTORIES)
        without_s3 = [f for f in DEFAULT_HOOK_FACTORIES if f is not create_world_model_hook]
        self.assertEqual(len(with_s3), len(without_s3) + 1)
        # Same chain, hook for hook: the factory answered None.
        self.assertEqual(self._hooks_of(self._chain(with_s3, self.workspace)), self._hooks_of(self._chain(without_s3, self.workspace)))
        self.assertNotIn("WorldModelHook", self._hooks_of(self._chain(with_s3, self.workspace)))
        rounds = 300
        self._p95_ms(without_s3, 50)  # warm-up
        baseline = self._p95_ms(without_s3, rounds)
        candidate = self._p95_ms(with_s3, rounds)
        # One os.stat per turn: within the noise of the chain itself. The bound
        # is generous on purpose (WSL2, CI); it guards against parsing, threads
        # or disk writes sneaking into the off path, not against jitter.
        self.assertLess(candidate, baseline * 1.5 + 0.5, f"p95 {candidate:.3f} ms vs {baseline:.3f} ms without S3")
        self.assertFalse((self.workspace / ".navin").exists(), "not one file after 300 turns")
        self.assertEqual(_appended(self.workspace), 0, "the writer never heard of this project")

    def test_flag_on_turn_stays_in_the_envelope_and_writes_after(self) -> None:
        self.enable()
        chain = self._chain(list(DEFAULT_HOOK_FACTORIES), self.workspace)
        self.assertIn("WorldModelHook", self._hooks_of(chain))
        rounds = 100
        candidate = self._p95_ms(list(DEFAULT_HOOK_FACTORIES), rounds)
        self.assertTrue(flush_recorder(10.0))
        self.assertEqual(count_trajectories(self.workspace), rounds * self.TOOL_CALLS, "every call landed, after the turn")
        # The turn only paid a dict copy and a queue.put per call; hashing,
        # classing and the disk happened on the recorder and writer threads.
        # Those threads share the GIL with the timed loop, so the bound is an
        # absolute ceiling per six-call turn, far under one provider round-trip.
        self.assertLess(candidate, 25.0, f"p95 {candidate:.3f} ms for {self.TOOL_CALLS} logged calls")


class HotPathIsolationTest(unittest.TestCase):
    """S3 never leaks into the loop, the runner or the context builder."""

    HOT_PATH = (
        "navin/agent/loop.py",
        "navin/agent/runner.py",
        "navin/agent/context.py",
        "navin/agent/memory.py",
        "navin/agent/turn_hooks.py",
        "navin/agent/hook.py",
        "navin/agent/tools/registry.py",
        "navin/agent/tools/loader.py",
        "navin/runtime_context.py",
    )

    def test_hot_path_files_do_not_mention_the_world_model(self) -> None:
        for rel in self.HOT_PATH:
            source = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("world_model", source, rel)
            self.assertNotIn("world_predict", source, rel)
            self.assertNotIn("BELIEFS", source, rel)

    def test_the_tool_module_imports_the_world_model_lazily(self) -> None:
        source = (REPO / "navin" / "agent" / "tools" / "world_predict.py").read_text(encoding="utf-8")
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse(any("world_model" in line for line in top_level), top_level)

    def test_no_dashes_in_the_new_modules(self) -> None:
        files = sorted((REPO / "navin" / "world_model").rglob("*.py"))
        files.append(REPO / "navin" / "cli" / "world.py")
        files.append(REPO / "navin" / "agent" / "tools" / "world_predict.py")
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("\u2013", text, path.name)

    def test_guardrails_copy_says_off_auto_journal_train_advice_is_gate_plus_you(self) -> None:
        for locale in ("en", "fr"):
            data = json.loads((REPO / "webui" / "src" / "i18n" / "locales" / locale / "common.json").read_text(encoding="utf-8"))
            world = data["dev"]["agi"]["world"]
            hint = world["hint"]
            self.assertEqual(hint.count(". "), 2, f"{locale}: three sentences expected")
            self.assertNotIn("\u2014", hint)
            self.assertNotIn("\u2013", hint)
            for key in ("enabledDetail", "logDetail", "trainDetail", "adviseDetail", "adviseLocked", "beliefsDetail"):
                self.assertIn(key, world)
        en = json.loads((REPO / "webui" / "src" / "i18n" / "locales" / "en" / "common.json").read_text(encoding="utf-8"))
        hint = en["dev"]["agi"]["world"]["hint"]
        self.assertIn("Off by default", hint)
        self.assertIn("trains outside the chat", hint)
        self.assertIn("A/B proved a gain", hint)


if __name__ == "__main__":
    unittest.main()
