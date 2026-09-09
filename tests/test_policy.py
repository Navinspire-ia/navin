# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Acceptance tests for S4 - policy learning (which action to take).

The contract under test, in the order of the spec:

* S4.0 flag off = no-op: no file, no thread, no hook, no steer, and the
  per-turn factory costs one ``os.stat``; the flag refuses to go on and the
  trainer refuses to run while the world model radar (S3.3) is not up;
* S4.1 one compact, secret-free line per step of an **eval** episode, with
  the eval reward of the whole episode; written by the training process
  after the run, never from a chat turn, never from a thank-you;
* S4.2 a local policy head fitted in a child process, under a budget, with
  versioned adapters N / N+1 and a one-step rollback; a crash or a blown
  budget leaves N in place;
* S4.3 frozen batteries (code, browser, desk) with a held-out split the
  adapter never saw, N vs N+1 overall and per suite, a gate that needs "up
  with no suite down"; flat keeps N, down keeps N and the evidence; the exam
  set and the battery are tamper-checked;
* S4.4 live steer behind the gates (exam eligible, offline A/B gain): the
  ``policy_next`` tool and a soft block that propose and never execute; the
  kill switch that cuts steer and reloads N when the live precision drops;
* S4.5 the human buttons: train, force a flat adapter, roll back, publish
  outside the project (never automatic), adopt with the same exam;
* S4.6 nothing in the hot path imports the policy; the Guardrails copy says
  off / train in a sandbox / steer = gates.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from navin.agent.hook import AgentHookContext, AgentRunHookContext, AgentTurnHookContext
from navin.agent.hooks import DEFAULT_HOOK_FACTORIES
from navin.policy import checkpoints as ck
from navin.policy import jobs
from navin.policy import train as train_module
from navin.policy.battery import BatteryInvalidError, BatteryTamperedError, load_battery
from navin.policy.dataset import HeldoutTamperedError, load_heldout, training_rows
from navin.policy.episodes import run_cases
from navin.policy.hook import PolicyHook, create_policy_hook, flush_recorder
from navin.policy.journal import append_step, count_steps, flush, read_journal, read_steps
from navin.policy.model import (
    MajorityPolicy,
    Metrics,
    PolicyHead,
    SuiteMetrics,
    UniformPolicy,
    Verdict,
    compare,
    evaluate,
)
from navin.policy.paths import (
    cases_path,
    checkpoint_file,
    heldout_path,
    policy_dir,
    trajectories_path,
)
from navin.policy.publish import adopt, publish, read_published, unpublish
from navin.policy.radar import Radar
from navin.policy.registration import sync_policy_tool
from navin.policy.settings import (
    SETTINGS_NAME,
    clear_settings_cache,
    policy_enabled,
    read_settings,
    update_settings,
)
from navin.policy.state import PolicyActionError, policy_action, policy_state, policy_update
from navin.policy.steerer import (
    KILL_MIN_SUGGESTIONS,
    live_tracker,
    run_ab,
    steer_block_text,
    steer_gate,
    steer_open,
    suggest_next,
)
from navin.policy.train import (
    TrainBudget,
    TrainBudgetExceededError,
    exam,
    force,
    freeze,
    rollback,
    train,
)
from navin.policy.trajectory import (
    GUARDED_TOOLS,
    STOP,
    StateKey,
    build_step,
    intent_of,
    record_is_valid,
)
from navin.providers.base import ToolCallRequest

REPO = Path(__file__).resolve().parents[1]

UP = Radar(up=True, reasons=(), checkpoint=3, heldout_version="abc", verdict="up")
DOWN = Radar(up=False, reasons=("world model has no active checkpoint",), checkpoint=None, heldout_version=None, verdict=None)

SECRET = "sk-live-abcdefghijklmnop1234567890"


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


class _Registry:
    """Just enough of the tool registry for ``sync_policy_tool``."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register(self, tool: Any) -> None:
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)

    def get(self, name: str) -> Any | None:
        return self.tools.get(name)


@contextmanager
def radar(signal: Radar):
    """The world model radar as the policy sees it (flag writer and trainer)."""
    with patch("navin.policy.state.radar", return_value=signal), patch("navin.policy.train.radar", return_value=signal):
        yield


def _fix_case(i: int, split: str) -> dict[str, Any]:
    return {
        "id": f"p-fix-{i}",
        "suite": "code",
        "split": split,
        "prompt": f"Fix the bug in mod{i}.py: value() must return {i + 10}.",
        "workspace": {f"mod{i}.py": f"def value():\n    return {i}\n"},
        "script": [
            {"tool": "read_file", "args": {"path": f"mod{i}.py"}},
            {"tool": "edit_file", "args": {"path": f"mod{i}.py", "old_text": f"return {i}", "new_text": f"return {i + 10}"}},
            {"final": f"Fixed: value() now returns {i + 10}."},
        ],
        "expect": {"tools_ok": ["edit_file"], "files_contain": {f"mod{i}.py": [f"return {i + 10}"]}},
    }


def _find_case(i: int, split: str) -> dict[str, Any]:
    return {
        "id": f"p-find-{i}",
        "suite": "code",
        "split": split,
        "prompt": f"Where is the function helper{i} defined?",
        "workspace": {f"lib{i}.py": f"def helper{i}():\n    return 1\n"},
        "script": [
            {"tool": "grep", "args": {"pattern": f"def helper{i}"}},
            {"final": f"helper{i} is defined in lib{i}.py."},
        ],
        "expect": {"tools_ok": ["grep"], "final_contains": [f"lib{i}.py"]},
    }


def _write_project_cases(workspace: Path, cases: list[dict[str, Any]]) -> None:
    path = cases_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8")


def _habit_cases() -> list[dict[str, Any]]:
    """A project with a habit: fix = read then edit then stop; find = grep then stop."""
    cases = [_fix_case(i, "heldout" if i >= 6 else "train") for i in range(8)]
    cases += [_find_case(i, "heldout" if i >= 3 else "train") for i in range(4)]
    return cases


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_settings_cache)
        self.addCleanup(live_tracker().reset)
        self.addCleanup(ck.clear_checkpoint_cache)
        self.addCleanup(jobs.reset_runner)
        self.workspace = Path(self._tmp.name) / "project"
        self.workspace.mkdir()
        # Training runs in a child process in production; tests run it in-process by hand.
        jobs.configure(auto_thread=False, inline=True)
        self.addCleanup(jobs.configure, auto_thread=True, inline=False)
        import navin.policy.publish as publish_module
        import navin.policy.registration as registration

        self.index_file = Path(self._tmp.name) / "machine" / "policy-projects.json"
        self.published = Path(self._tmp.name) / "machine" / "published"
        for target, value in ((registration, "index_path"), (publish_module, "published_dir")):
            patcher = patch.object(target, value, (lambda f=self.index_file: f) if value == "index_path" else (lambda p=self.published: p))
            patcher.start()
            self.addCleanup(patcher.stop)
        live_tracker().reset()
        ck.clear_checkpoint_cache()

    def enable(self, **fields: Any) -> None:
        update_settings(self.workspace, {"enabled": True, "min_rows": 10, **fields})

    def events(self) -> list[str]:
        return [str(row["event"]) for row in read_journal(self.workspace, limit=200)]

    def trained(self, *, habit: bool = False) -> Any:
        """Flag on, radar up, one in-process training run; returns the result."""
        self.enable()
        if habit:
            _write_project_cases(self.workspace, _habit_cases())
        with radar(UP):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "trained", result.reason)
        return result

    def open_gate(self) -> None:
        """Train on a project with habits + offline A/B so that ``steer`` may go on."""
        self.trained(habit=True)
        ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["status"], "scored", ab)
        self.assertEqual(ab["verdict"], "gain", ab)
        gate = steer_gate(self.workspace)
        self.assertTrue(gate.open, gate.reasons)


# --------------------------------------------------------------------------
# S4.0 - the net: flag off is a no-op, no policy without a radar
# --------------------------------------------------------------------------


class FlagOffTest(_Workspace):
    def test_default_is_off_and_missing_file_means_off(self) -> None:
        settings = read_settings(self.workspace)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.steer)
        for stage in ("log", "train", "steer"):
            self.assertFalse(settings.feature(stage))
        self.assertFalse(policy_enabled(self.workspace))
        self.assertFalse(policy_enabled(None))
        self.assertEqual(SETTINGS_NAME, "policy.json")
        self.assertFalse((self.workspace / ".navin").exists())

    def test_off_state_reports_the_flag_and_the_radar_and_creates_nothing(self) -> None:
        state = policy_state(self.workspace)
        self.assertFalse(state["enabled"])
        self.assertEqual(state["rows"], 0)
        self.assertEqual(state["checkpoints"], [])
        self.assertFalse(state["gate"]["open"])
        self.assertIn("radar", state)
        self.assertFalse(state["radar"]["up"], "no S3 in this project: radar down")
        json.dumps(state)
        self.assertFalse((self.workspace / ".navin").exists(), "reading creates nothing")

    def test_off_turn_has_no_hook_and_no_job(self) -> None:
        self.assertIsNone(create_policy_hook(_turn(self.workspace)))
        self.assertIsNone(create_policy_hook(_turn(None)))
        self.assertFalse(jobs.note_turn(self.workspace))
        self.assertFalse(jobs.runner_alive())
        self.assertIsNone(jobs.run_due(self.workspace))
        self.assertFalse((self.workspace / ".navin").exists())

    def test_off_refuses_every_button(self) -> None:
        for action in ("train", "run", "exam", "ab", "publish"):
            with self.assertRaises(PolicyActionError) as caught:
                policy_action(self.workspace, action, actor="human")
            self.assertEqual(caught.exception.status, 409, action)
        self.assertEqual(train(self.workspace, actor="human", force=True).status, "skipped")
        self.assertEqual(run_ab(self.workspace)["status"], "skipped")
        self.assertFalse((self.workspace / ".navin").exists())

    def test_no_policy_without_a_radar(self) -> None:
        with radar(DOWN):
            with self.assertRaises(PolicyActionError) as caught:
                policy_update(self.workspace, {"enabled": True})
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("no policy without a radar", str(caught.exception))
        self.assertFalse(read_settings(self.workspace).enabled)
        with radar(UP):
            state = policy_update(self.workspace, {"enabled": True})
        self.assertTrue(state["enabled"])
        self.assertFalse(state["steer"], "steer stays off when the master goes on")
        self.assertIn("enabled", self.events())

    def test_train_is_refused_while_the_radar_is_down_even_for_a_human(self) -> None:
        self.enable()
        with radar(DOWN):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "radar_down")
        self.assertIn("radar is not up", result.reason or "")
        self.assertEqual(self.events(), ["train_refused"])
        self.assertFalse(trajectories_path(self.workspace).exists(), "no eval run without a radar")
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [])
        with radar(DOWN):
            with self.assertRaises(PolicyActionError) as caught:
                policy_action(self.workspace, "train", actor="human", inline=True)
        self.assertEqual(caught.exception.status, 409)

    def test_heartbeat_and_ephemeral_turns_never_get_a_hook(self) -> None:
        self.enable()
        self.assertIsNone(create_policy_hook(_turn(self.workspace, ephemeral=True)))
        self.assertIsNone(create_policy_hook(_turn(self.workspace, session_key="heartbeat")))
        self.assertIsNone(create_policy_hook(_turn(self.workspace, metadata={"heartbeat": True})))
        self.assertIsInstance(create_policy_hook(_turn(self.workspace)), PolicyHook)

    def test_train_off_and_steer_off_means_no_hook_at_all(self) -> None:
        self.enable(train=False)
        self.assertIsNone(create_policy_hook(_turn(self.workspace)), "nothing to count, nothing to steer")


# --------------------------------------------------------------------------
# S4.1 - trajectories: eval only, compact, secret-free, written after the run
# --------------------------------------------------------------------------


class TrajectoryTest(unittest.TestCase):
    def test_a_step_is_compact_and_carries_the_eval_reward(self) -> None:
        step = build_step(
            episode="b|code-01|r1",
            suite="code",
            case="code-01",
            split="train",
            step=1,
            intent=intent_of("Fix the bug in calc.py"),
            prev=["read_file:ok"],
            s3="ok",
            action="edit_file",
            arguments={"path": "calc.py", "old_text": SECRET, "new_text": "x"},
            result=f"edited calc.py (token {SECRET})",
            is_error=False,
            reward=1.0,
        )
        record = step.as_record()
        self.assertTrue(record_is_valid(record))
        self.assertEqual(record["source"], "eval")
        self.assertEqual(record["intent"], "fix")
        self.assertEqual(record["action"], "edit_file")
        self.assertEqual(record["akey"], "edit_file|.py")
        self.assertEqual(record["obs"], "changed")
        self.assertEqual(record["reward"], 1.0)
        self.assertNotIn(SECRET, json.dumps(record), "arguments and results are never stored")
        self.assertNotIn("calc", json.dumps(record).replace("code-01", ""), "no path, only the extension")

    def test_reward_is_zero_or_one_from_an_eval_never_a_sentiment(self) -> None:
        base = build_step(
            episode="e", suite="code", case="c", split="train", step=0, intent="fix", prev=[], s3=None,
            action="read_file", arguments={"path": "a.py"}, result="ok", is_error=False, reward=1.0,
        ).as_record()
        self.assertTrue(record_is_valid(base))
        self.assertFalse(record_is_valid({**base, "reward": 0.5}), "a like is not a reward")
        self.assertFalse(record_is_valid({**base, "reward": True}))
        self.assertFalse(record_is_valid({**base, "source": "chat"}), "chat turns are not eval")
        self.assertFalse(record_is_valid({**base, "split": "live"}))
        self.assertFalse(record_is_valid({**base, "obs": "great"}))
        self.assertEqual(build_step(
            episode="e", suite="code", case="c", split="train", step=0, intent="fix", prev=[], s3=None,
            action="read_file", arguments={}, result="ok", is_error=False, reward=0.7,
        ).reward, 0.0, "anything under a full pass is a fail")

    def test_intent_is_a_vocabulary_key_not_the_text(self) -> None:
        self.assertEqual(intent_of("Fix the failing test in net.py"), "fix,test")
        self.assertEqual(intent_of(f"Corrige le bug, la clé est {SECRET}"), "fix")
        self.assertEqual(intent_of("Tell me the price of the Pro plan on example.test"), "fetch", "a host is not a test")
        self.assertEqual(intent_of("Give me the email address"), "mail", "address is not add")
        self.assertEqual(intent_of("Bonjour"), "other")
        self.assertEqual(intent_of(None), "other")

    def test_final_answer_is_an_action_and_guarded_tools_are_listed(self) -> None:
        from navin.policy.trajectory import ASK, final_action

        self.assertEqual(final_action("Done."), STOP)
        self.assertEqual(final_action("Which file do you mean?"), ASK)
        for name in ("write_file", "edit_file", "exec", "send_email", "payment", "delete_file"):
            self.assertIn(name, GUARDED_TOOLS)


class EvalWriterTest(_Workspace):
    def test_the_writer_refuses_anything_that_is_not_an_eval_step(self) -> None:
        self.enable()
        self.assertFalse(append_step(self.workspace, {"source": "chat", "action": "exec", "reward": 1.0}))
        self.assertFalse(append_step(self.workspace, {"thumbs_up": True}))
        self.assertTrue(flush(5.0))
        self.assertFalse(trajectories_path(self.workspace).exists())

    def test_a_chat_turn_never_writes_a_trajectory(self) -> None:
        self.enable()
        hook = create_policy_hook(_turn(self.workspace))
        self.assertIsInstance(hook, PolicyHook)

        async def turn() -> None:
            ctx = _hook_ctx()
            for i in range(4):
                call = _call("exec", {"command": "ls"}, call_id=f"c{i}")
                await hook.after_execute_tool(ctx, call, None, {"command": "ls"}, "ok")
            await hook.after_run(_run_ctx())

        asyncio.run(turn())
        self.assertTrue(flush_recorder(5.0))
        self.assertEqual(count_steps(self.workspace), 0, "chat is not training data")
        self.assertFalse(trajectories_path(self.workspace).exists())
        self.assertEqual(jobs.turns_since_train(self.workspace), 1, "the turn was counted, nothing else")

    def test_an_eval_run_writes_one_line_per_step_after_the_run_with_the_episode_reward(self) -> None:
        self.enable()
        battery = load_battery(workspace=self.workspace)
        episodes = train_module.collect(self.workspace, battery, actor="test")
        self.assertEqual(len(episodes), battery.describe()["total_cases"])
        failed = [e for e in episodes if not e.passed]
        self.assertTrue(failed, "the battery carries cases meant to fail, for the negative signal")
        rows = read_steps(self.workspace)
        self.assertEqual(len(rows), sum(len(e.steps) for e in episodes))
        self.assertTrue(all(record_is_valid(r) for r in rows))
        for episode in episodes:
            own = [r for r in rows if r["episode"] == episode.episode_id]
            self.assertEqual({r["reward"] for r in own}, {episode.reward}, "the episode's eval decides every step")
            self.assertEqual(own[-1]["action"], STOP if not episode.final.endswith("?") else "ask")
            self.assertTrue(own[-1]["terminal"])
        self.assertIn("eval_run", self.events())
        text = trajectories_path(self.workspace).read_text(encoding="utf-8")
        for needle in ("def total", "example.test/pricing", "alice@example.test", "Acme"):
            self.assertNotIn(needle, text, "no fixture content, no prompt text, no result text on disk")

    def test_log_off_means_the_eval_run_writes_nothing(self) -> None:
        self.enable(log=False)
        battery = load_battery(workspace=self.workspace)
        episodes = train_module.collect(self.workspace, battery, actor="test")
        self.assertTrue(episodes)
        self.assertEqual(count_steps(self.workspace), 0)

    def test_a_secret_in_a_fixture_never_reaches_the_trajectory_file(self) -> None:
        self.enable()
        case = _fix_case(0, "train")
        case["workspace"]["mod0.py"] = f"API_KEY = '{SECRET}'\ndef value():\n    return 0\n"
        case["script"][2] = {"final": f"Fixed; the key is {SECRET}."}
        case["expect"] = {"tools_ok": ["edit_file"]}
        _write_project_cases(self.workspace, [case, _fix_case(1, "train"), _fix_case(2, "heldout")])
        battery = load_battery(workspace=self.workspace)
        train_module.collect(self.workspace, battery, actor="test")
        text = trajectories_path(self.workspace).read_text(encoding="utf-8")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("API_KEY", text)


# --------------------------------------------------------------------------
# S4.2 - the local head, the budget, adapters N / N+1
# --------------------------------------------------------------------------


class ModelTest(unittest.TestCase):
    @staticmethod
    def _rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for n in range(6):
            for step, (prev, action) in enumerate(
                (([], "read_file"), (["read_file:ok"], "edit_file"), (["read_file:ok", "edit_file:changed"], STOP))
            ):
                rows.append(
                    build_step(
                        episode=f"e{n}", suite="code", case=f"c{n}", split="train", step=step, intent="fix",
                        prev=prev, s3=None, action=action, arguments={"path": "a.py"}, result="ok",
                        is_error=False, reward=1.0,
                    ).as_record()
                )
        for n in range(3):
            rows.append(
                build_step(
                    episode=f"f{n}", suite="code", case=f"bad{n}", split="train", step=0, intent="fix", prev=[],
                    s3=None, action="edit_file", arguments={"path": "a.py"}, result="not found", is_error=True, reward=0.0,
                ).as_record()
            )
        return rows

    def test_the_head_learns_the_habit_and_flags_what_only_failed(self) -> None:
        head = PolicyHead().fit(self._rows())
        first = head.predict(StateKey("fix", ()))
        self.assertEqual(first.action, "read_file")
        self.assertGreater(first.confidence, 0.7)
        self.assertEqual(first.support, 6, "six successful runs of this kind of request")
        self.assertEqual(first.avoid, ("edit_file",), "editing blind only ever appeared in failed runs")
        self.assertEqual(head.predict(StateKey("fix", ("read_file:ok",))).action, "edit_file")
        self.assertEqual(head.predict(StateKey("fix", ("read_file:ok", "edit_file:changed"))).action, STOP)
        unknown = head.predict(StateKey("other", ("browser:ok",)))
        self.assertEqual(unknown.level, "global")
        self.assertEqual(unknown.support, 0)

    def test_roundtrip_keeps_the_predictions(self) -> None:
        head = PolicyHead().fit(self._rows())
        clone = PolicyHead.from_dict(json.loads(json.dumps(head.to_dict())))
        for state in (StateKey("fix", ()), StateKey("fix", ("read_file:ok",)), StateKey("other", ())):
            self.assertEqual(clone.predict(state).as_dict(), head.predict(state).as_dict())

    def test_evaluate_skips_failed_steps_and_the_head_beats_the_baselines(self) -> None:
        rows = self._rows()
        head = PolicyHead().fit(rows)
        metrics = evaluate(head, rows)
        self.assertEqual(metrics.n, 18, "the failed steps are not an exam of what to do")
        self.assertEqual(metrics.accuracy, 1.0)
        majority = evaluate(MajorityPolicy(rows), rows)
        uniform = evaluate(UniformPolicy({"read_file", "edit_file"}), rows)
        self.assertLess(majority.accuracy, 1.0)
        self.assertLess(uniform.accuracy, majority.accuracy + 0.01)
        self.assertEqual(compare(metrics, majority).overall, "up")

    def test_verdicts_up_flat_down_and_the_gate_needs_no_suite_down(self) -> None:
        def m(acc: float, suites: dict[str, float]) -> Metrics:
            return Metrics(n=30, accuracy=acc, log_loss=1.0, suites={k: SuiteMetrics(10, v, 1.0) for k, v in suites.items()})

        reference = m(0.5, {"code": 0.5, "browser": 0.5, "desk": 0.5})
        better = compare(m(0.7, {"code": 0.7, "browser": 0.7, "desk": 0.7}), reference)
        self.assertEqual(better.overall, "up")
        self.assertTrue(better.eligible)
        self.assertFalse(better.regressed)
        one_down = compare(m(0.7, {"code": 0.9, "browser": 0.9, "desk": 0.3}), reference)
        self.assertEqual(one_down.overall, "up")
        self.assertEqual(one_down.suites["desk"], "down")
        self.assertFalse(one_down.eligible, "up overall but a suite in down: gate closed")
        self.assertTrue(one_down.regressed)
        flat = compare(m(0.52, {"code": 0.52, "browser": 0.5, "desk": 0.5}), reference)
        self.assertEqual(flat.overall, "flat")
        self.assertFalse(flat.eligible)
        self.assertFalse(flat.regressed)
        down = compare(m(0.3, {"code": 0.3, "browser": 0.3, "desk": 0.3}), reference)
        self.assertEqual(down.overall, "down")
        self.assertTrue(down.regressed)
        self.assertEqual(Verdict.from_dict(down.as_dict()), down)


class TrainingTest(_Workspace):
    def test_first_training_run_collects_freezes_fits_scores_and_activates(self) -> None:
        result = self.trained()
        self.assertEqual(result.checkpoint, 1)
        self.assertTrue(result.activated)
        self.assertEqual(result.verdict_vs_baseline["overall"], "up")
        self.assertIsNone(result.verdict_vs_active, "no N yet: judged against the baseline")
        self.assertTrue(result.episodes, "the battery ran")
        self.assertGreater(result.rows_heldout, 0)
        self.assertTrue(checkpoint_file(self.workspace, 1).exists())
        self.assertTrue(heldout_path(self.workspace).exists())
        active = ck.read_active(self.workspace)
        self.assertEqual(active["checkpoint"], 1)
        self.assertFalse(active["forced"])
        self.assertEqual(active["heldout_version"], result.heldout_version)
        for event in ("eval_run", "heldout_frozen", "trained", "activated"):
            self.assertIn(event, self.events())
        payload = json.loads(checkpoint_file(self.workspace, 1).read_text(encoding="utf-8"))
        self.assertEqual(payload["battery_version"], result.battery_version)
        self.assertIn("suites", payload["metrics"])
        for suite in ("code", "browser", "desk"):
            self.assertIn(suite, payload["metrics"]["suites"], "scored per suite")

    def test_same_data_again_is_flat_and_n_stays(self) -> None:
        first = self.trained()
        with radar(UP):
            second = train(self.workspace, actor="human", force=True)
        self.assertEqual(second.status, "trained")
        self.assertEqual(second.checkpoint, 2)
        self.assertIsNotNone(second.verdict_vs_active, "judged against N")
        self.assertEqual(second.verdict_vs_active["overall"], "flat")
        self.assertFalse(second.activated)
        self.assertIn("flat", second.reason or "")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], first.checkpoint)
        self.assertTrue(checkpoint_file(self.workspace, 2).exists(), "N+1 stays on disk")
        summaries = ck.checkpoint_summaries(self.workspace)
        self.assertEqual([s["number"] for s in summaries], [1, 2])
        self.assertTrue(summaries[0]["active"])
        self.assertFalse(summaries[1]["active"])

    def test_train_corridor_off_skips_unless_a_human_forces(self) -> None:
        self.enable(train=False)
        with radar(UP):
            self.assertEqual(train(self.workspace).status, "skipped")
            self.assertIsNone(jobs.run_due(self.workspace))
            forced = train(self.workspace, actor="human", force=True)
        self.assertEqual(forced.status, "trained")

    def test_budget_exceeded_leaves_n_in_place(self) -> None:
        self.trained()
        before = ck.read_active(self.workspace)
        with radar(UP):
            result = train(self.workspace, actor="human", force=True, budget=TrainBudget(timeout_s=0.0))
        self.assertEqual(result.status, "budget_exceeded")
        self.assertIn("timeout", result.reason or "")
        self.assertEqual(ck.read_active(self.workspace), before)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1], "no N+1 written")
        self.assertIn("train_failed", self.events())

    def test_a_crash_in_the_fit_leaves_n_in_place(self) -> None:
        self.trained()
        before = ck.read_active(self.workspace)

        def boom(self_: Any, rows: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated crash")

        with radar(UP), patch.object(PolicyHead, "fit", boom):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "error")
        self.assertIn("simulated crash", result.reason or "")
        self.assertEqual(ck.read_active(self.workspace), before)
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [1])

    def test_the_budget_clock_raises_past_its_deadline(self) -> None:
        clock = train_module._BudgetClock(TrainBudget(timeout_s=0.0))
        with self.assertRaises(TrainBudgetExceededError):
            clock.check("test")

    def test_rollback_is_one_write_and_the_evidence_stays(self) -> None:
        self.trained()
        with radar(UP):
            train(self.workspace, actor="human", force=True)
        forced = force(self.workspace, actor="human")
        self.assertEqual(forced["status"], "forced")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 2)
        self.assertTrue(ck.read_active(self.workspace)["forced"])
        rolled = rollback(self.workspace, actor="human")
        self.assertEqual(rolled, {"status": "rolled_back", "from": 2, "to": 1})
        active = ck.read_active(self.workspace)
        self.assertEqual(active["checkpoint"], 1)
        self.assertEqual(active["rolled_back_from"], 2)
        self.assertFalse(active["forced"])
        self.assertTrue(checkpoint_file(self.workspace, 2).exists())
        self.assertIn("rollback", self.events())
        self.assertIsNotNone(ck.active_checkpoint(self.workspace))
        self.assertEqual(ck.active_checkpoint(self.workspace).number, 1)

    def test_the_job_runner_counts_turns_and_trains_when_due(self) -> None:
        self.enable(train_every=3)
        self.assertFalse(jobs.note_turn(self.workspace))
        self.assertFalse(jobs.note_turn(self.workspace))
        self.assertFalse(jobs.training_due(self.workspace))
        self.assertTrue(jobs.note_turn(self.workspace), "third turn: due")
        self.assertTrue(jobs.training_due(self.workspace))
        with radar(UP):
            result = jobs.run_due(self.workspace)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "trained")
        self.assertEqual(result["process"], "inline")
        self.assertEqual(jobs.turns_since_train(self.workspace), 0)
        with radar(UP):
            self.assertIsNone(jobs.run_due(self.workspace), "not due again, and not sooner than the interval")


class ChildProcessTest(_Workspace):
    """S4.2: the trainer is a child process, never the gateway's."""

    def test_the_child_runs_the_real_radar_and_answers_json(self) -> None:
        self.enable()
        result = jobs.spawn_train(self.workspace, actor="human", force=True, timeout_s=90)
        self.assertEqual(result["process"], "child")
        # No world model in this project: the child's own radar refuses, and says so.
        self.assertEqual(result["status"], "radar_down", result)
        self.assertIn("world model", result["reason"])
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [])
        self.assertIn("train_refused", self.events())

    def test_the_child_entry_never_touches_the_gateway(self) -> None:
        source = (REPO / "navin" / "policy" / "train_job.py").read_text(encoding="utf-8")
        for forbidden in ("navin.webui", "navin.gateway", "navin.providers", "LLMRuntime", "channels"):
            self.assertNotIn(forbidden, source)
        self.assertIn("setrlimit", source, "CPU and memory ceilings are set in the child")
        jobs_source = (REPO / "navin" / "policy" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("subprocess.run", jobs_source)
        self.assertIn("navin.policy.train_job", jobs_source)


# --------------------------------------------------------------------------
# S4.3 - the exam: frozen split, N vs N+1 per suite, tamper checks
# --------------------------------------------------------------------------


class ExamTest(_Workspace):
    def test_the_frozen_set_is_held_out_cases_only_and_never_trained_on(self) -> None:
        result = self.trained()
        heldout = load_heldout(self.workspace)
        self.assertIsNotNone(heldout)
        self.assertEqual(heldout.version, result.heldout_version)
        self.assertTrue(heldout.rows)
        self.assertEqual({r["split"] for r in heldout.rows}, {"heldout"})
        rows = read_steps(self.workspace)
        train_rows = training_rows(rows, heldout)
        self.assertTrue(train_rows)
        self.assertEqual({r["split"] for r in train_rows}, {"train"})
        self.assertFalse({r["case"] for r in train_rows} & {r["case"] for r in heldout.rows})
        description = heldout.describe()
        for suite in ("code", "browser", "desk"):
            self.assertIn(suite, description["suites"], "every suite has held-out steps")

    def test_editing_the_frozen_set_is_caught_and_stops_train_and_exam(self) -> None:
        self.trained()
        path = heldout_path(self.workspace)
        lines = path.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[1])
        row["action"] = "browser"
        lines[1] = json.dumps(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(HeldoutTamperedError):
            load_heldout(self.workspace)
        self.assertEqual(exam(self.workspace, actor="human")["status"], "tampered")
        with radar(UP):
            self.assertEqual(train(self.workspace, actor="human", force=True).status, "tampered")
        self.assertFalse(steer_gate(self.workspace).open)
        with self.assertRaises(PolicyActionError) as caught:
            policy_action(self.workspace, "exam", actor="human")
        self.assertEqual(caught.exception.status, 409)

    def test_a_modified_battery_is_caught_before_the_checkpoint_is_written(self) -> None:
        self.enable()
        with radar(UP), patch("navin.policy.battery.PolicyBattery.verify_unchanged", side_effect=BatteryTamperedError("battery changed")):
            result = train(self.workspace, actor="human", force=True)
        self.assertEqual(result.status, "tampered")
        self.assertEqual(ck.list_checkpoint_numbers(self.workspace), [])
        self.assertIsNone(ck.read_active(self.workspace))

    def test_battery_refuses_unknown_checks_that_would_reward_for_nothing(self) -> None:
        bad = _fix_case(0, "train")
        bad["expect"] = {"tool_ok": ["edit_file"]}
        _write_project_cases(self.workspace, [bad])
        with self.assertRaises(BatteryInvalidError) as caught:
            load_battery(workspace=self.workspace)
        self.assertIn("unknown keys", str(caught.exception))
        bad = _fix_case(0, "train")
        bad["script"][0] = {"tool": "read_file", "arguments": {"path": "mod0.py"}}
        _write_project_cases(self.workspace, [bad])
        with self.assertRaises(BatteryInvalidError):
            load_battery(workspace=self.workspace)

    def test_the_bundled_battery_has_the_three_suites_with_a_held_out_split(self) -> None:
        battery = load_battery()
        described = battery.describe()
        self.assertEqual([s["id"] for s in described["suites"]], ["code", "browser", "desk"])
        for suite in described["suites"]:
            self.assertGreaterEqual(suite["train"], 2)
            self.assertGreaterEqual(suite["heldout"], 1)
        episodes = run_cases(battery.cases, battery_version=battery.version)
        self.assertEqual(len(episodes), described["total_cases"])
        self.assertTrue(all(e.steps[-1].terminal for e in episodes))
        outcomes = {e.case_id: e.passed for e in episodes}
        self.assertFalse(outcomes["code-05"], "the blind edit fails on purpose")
        self.assertTrue(outcomes["code-01"])

    def test_exam_rescores_the_same_frozen_set_and_writes_one_line(self) -> None:
        self.trained()
        before = len(train_module.read_scoreboard(self.workspace, limit=50))
        result = exam(self.workspace, actor="human")
        self.assertEqual(result["status"], "scored")
        self.assertTrue(result["exam"])
        self.assertEqual(result["checkpoint"], 1)
        self.assertEqual(len(train_module.read_scoreboard(self.workspace, limit=50)), before + 1)
        self.assertIn("examined", self.events())

    def test_a_down_adapter_is_never_activated_and_never_forced(self) -> None:
        self.trained()
        # A candidate that learned the opposite habit: judged down, kept as evidence.
        bad = PolicyHead()
        for _ in range(20):
            bad.observe(StateKey("fix", ()), "browser", 1.0)
            bad.observe(StateKey("fetch", ()), "edit_file", 1.0)
        heldout = load_heldout(self.workspace)
        active = ck.active_checkpoint(self.workspace)
        verdict = compare(evaluate(bad, heldout.rows), evaluate(active.model, heldout.rows))
        self.assertTrue(verdict.regressed, verdict.as_dict())
        ck.save_checkpoint(
            self.workspace,
            ck.Checkpoint(
                number=2, trained_at="now", rows=1, episodes=1, battery_version="b", heldout_version=heldout.version,
                metrics={}, baseline={}, verdict_vs_baseline=None, verdict_vs_active=verdict.as_dict(), actor="test", model=bad,
            ),
        )
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)
        refused = force(self.workspace, actor="human", number=2)
        self.assertEqual(refused["status"], "refused")
        self.assertIn("regressed", refused["reason"])
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)
        self.assertIn("force_refused", self.events())
        with self.assertRaises(PolicyActionError) as caught:
            policy_action(self.workspace, "force", actor="human", number=2)
        self.assertEqual(caught.exception.status, 409)


# --------------------------------------------------------------------------
# S4.4 - steer: gated, soft, proposes and never executes, kill switch
# --------------------------------------------------------------------------


class SteerGateTest(_Workspace):
    def test_steer_stays_off_and_locked_after_a_first_training(self) -> None:
        self.trained()
        self.assertFalse(read_settings(self.workspace).steer)
        gate = steer_gate(self.workspace)
        self.assertFalse(gate.open)
        self.assertIn("no offline A/B", "; ".join(gate.reasons))
        with self.assertRaises(PolicyActionError) as caught:
            policy_update(self.workspace, {"steer": True})
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(read_settings(self.workspace).steer)
        self.assertFalse(steer_open(self.workspace))
        self.assertIsNone(steer_block_text(self.workspace, user_text="Fix the bug"))
        registry = _Registry()
        self.assertFalse(sync_policy_tool(registry, workspace=self.workspace))
        self.assertNotIn("policy_next", registry.tools)

    def test_a_small_battery_gives_no_gain_so_the_gate_stays_closed(self) -> None:
        self.trained()
        ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["status"], "scored")
        self.assertNotEqual(ab["verdict"], "gain", "one run per kind of request is not a habit")
        self.assertFalse(steer_gate(self.workspace).open)

    def test_the_gate_opens_with_habits_and_the_head_proposes_without_executing(self) -> None:
        self.open_gate()
        state = policy_update(self.workspace, {"steer": True})
        self.assertTrue(state["steer"])
        self.assertTrue(steer_open(self.workspace))
        self.assertIn("steer_on", self.events())
        block = steer_block_text(self.workspace, user_text="Fix the failing bug in parser.py")
        self.assertIsNotNone(block)
        self.assertIn("advisory", block)
        self.assertIn("never runs a tool", block)
        self.assertIn("approval", block)
        self.assertIn("read_file", block)
        self.assertLessEqual(len(block.splitlines()), 3)
        self.assertIsNone(steer_block_text(self.workspace, user_text="Tell me a joke"), "nothing confident: silence")
        suggestion = suggest_next(self.workspace, intent="fix", prev=("read_file:ok",))
        self.assertEqual(suggestion.action, "edit_file")
        self.assertIn("approval for that call is unchanged", suggestion.text, "a guarded tool is proposed, never run")
        registry = _Registry()
        self.assertTrue(sync_policy_tool(registry, workspace=self.workspace))
        tool = registry.tools["policy_next"]
        self.assertTrue(tool.read_only)
        answer = asyncio.run(tool.execute(goal="Fix the bug in x.py", last_tools="read_file:ok"))
        self.assertIn("edit_file", answer)
        self.assertIn("Advisory only", answer)
        self.assertIn("approvals unchanged", answer)

    def test_the_tool_answers_nothing_useful_while_the_gate_is_closed(self) -> None:
        from navin.agent.tools.policy_next import PolicyNextTool

        self.trained()
        tool = PolicyNextTool(workspace=self.workspace)
        answer = asyncio.run(tool.execute(goal="Fix the bug"))
        self.assertIn("not active", answer)
        self.assertIn("Decide by yourself", answer)

        class _Ctx:
            workspace = self.workspace

        self.assertFalse(PolicyNextTool.enabled(_Ctx()))

    def test_kill_switch_cuts_steer_reloads_n_and_drops_the_tool(self) -> None:
        self.open_gate()
        with radar(UP):
            second = train(self.workspace, actor="human", force=True)
        self.assertEqual(second.status, "trained")
        self.assertFalse(second.activated, "same data: flat")
        # A human forces N+1 on purpose (traced); the gate re-checks with it.
        self.assertEqual(force(self.workspace, actor="human")["status"], "forced")
        ab = run_ab(self.workspace, actor="human")
        self.assertEqual(ab["verdict"], "gain")
        policy_update(self.workspace, {"steer": True})
        registry = _Registry()
        self.assertTrue(sync_policy_tool(registry, workspace=self.workspace))
        serving = ck.read_active(self.workspace)["checkpoint"]
        self.assertEqual(serving, 2)
        fired = False
        for _ in range(KILL_MIN_SUGGESTIONS + 2):
            suggestion = suggest_next(self.workspace, intent="fix", prev=())
            if suggestion is None:
                break
            fired = live_tracker().record(self.workspace, suggestion=suggestion, actual="browser") or fired
        self.assertTrue(fired, "the live precision fell under the floor")
        self.assertFalse(read_settings(self.workspace).steer, "steer cut itself off")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1, "back to N")
        self.assertIn("steer_cut", self.events())
        self.assertFalse(sync_policy_tool(registry, workspace=self.workspace))
        self.assertNotIn("policy_next", registry.tools, "the prompt is back to what it was")
        self.assertFalse(steer_open(self.workspace))

    def test_the_hook_feeds_the_live_ab_only_when_steer_is_open(self) -> None:
        self.open_gate()
        policy_update(self.workspace, {"steer": True})
        hook = create_policy_hook(_turn(self.workspace))
        self.assertIsInstance(hook, PolicyHook)
        self.assertTrue(hook._steer_open)

        from navin.agent.tools.context import RequestContext, request_context

        async def turn() -> None:
            ctx = _hook_ctx()
            with request_context(RequestContext(channel="webui", chat_id="c", original_user_text="Fix the bug in a.py", workspace=self.workspace)):
                await hook.after_execute_tool(ctx, _call("read_file", {"path": "a.py"}), None, {"path": "a.py"}, "def f(): pass")
                await hook.after_execute_tool(ctx, _call("edit_file", {"path": "a.py"}, "c2"), None, {"path": "a.py"}, "edited a.py")
                await hook.after_run(_run_ctx())

        asyncio.run(turn())
        self.assertTrue(flush_recorder(5.0))
        live = live_tracker().snapshot(self.workspace)
        self.assertEqual(live["suggested"], 3, "read, edit, stop: three confident proposals compared")
        self.assertEqual(live["precision"], 1.0)
        self.assertTrue(read_settings(self.workspace).steer)
        self.assertEqual(count_steps(self.workspace), len(read_steps(self.workspace)), "still no chat trajectory")
        self.assertFalse(any(r.get("source") != "eval" for r in read_steps(self.workspace)))


# --------------------------------------------------------------------------
# S4.5 - the human is a plus: force, publish, adopt, routes, CLI
# --------------------------------------------------------------------------


class HumanButtonsTest(_Workspace):
    def test_human_only_buttons_need_a_human(self) -> None:
        self.trained()
        for action in ("train", "freeze", "rollback", "force", "publish", "adopt", "unpublish"):
            with self.assertRaises(PolicyActionError) as caught:
                policy_action(self.workspace, action, actor="auto", name="x")
            self.assertEqual(caught.exception.status, 403, action)
        self.assertEqual(publish(self.workspace, actor="auto")["status"], "refused")
        self.assertEqual(adopt(self.workspace, "x", actor="auto")["status"], "refused")

    def test_publish_is_a_human_click_and_adopt_takes_the_same_exam(self) -> None:
        self.trained()
        self.assertEqual(read_published(), [])
        result = policy_action(self.workspace, "publish", actor="human", name="first")
        published = result["result"]
        self.assertEqual(published["status"], "published")
        self.assertEqual([p["name"] for p in read_published()], [published["name"]])
        self.assertEqual(read_published()[0]["note"], "first")
        self.assertIn("published", self.events())
        self.assertEqual(result["state"]["published"][0]["name"], published["name"])

        other = Path(self._tmp.name) / "other"
        other.mkdir()
        update_settings(other, {"enabled": True})
        adopted = adopt(other, published["name"], actor="human")
        self.assertEqual(adopted["status"], "adopted")
        self.assertFalse(adopted["activated"], "no exam set there yet: saved, not judged, not active")
        self.assertEqual(ck.list_checkpoint_numbers(other), [1])
        self.assertIsNone(ck.read_active(other))
        self.assertEqual(ck.checkpoint_summaries(other)[0]["source"], f"adopted:{published['name']}")

        # Back home: adopting our own adapter is judged against N (flat) and does not serve.
        home = adopt(self.workspace, published["name"], actor="human")
        self.assertEqual(home["status"], "adopted")
        self.assertFalse(home["activated"])
        self.assertEqual(home["verdict_vs_active"]["overall"], "flat")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)

        gone = unpublish(published["name"], actor="human", workspace=self.workspace)
        self.assertEqual(gone["status"], "unpublished")
        self.assertEqual(read_published(), [])
        self.assertEqual(ck.list_checkpoint_numbers(other), [1], "projects that adopted keep their copy")

    def test_force_activates_a_flat_adapter_traced_and_reversible(self) -> None:
        self.trained()
        with radar(UP):
            train(self.workspace, actor="human", force=True)
        self.assertEqual(force(self.workspace, actor="human", number=7)["status"], "nothing_to_force")
        result = policy_action(self.workspace, "force", actor="human")["result"]
        self.assertEqual(result["status"], "forced")
        self.assertEqual(result["checkpoint"], 2)
        self.assertTrue(ck.read_active(self.workspace)["forced"])
        self.assertIn("forced", self.events())
        self.assertTrue(ck.checkpoint_summaries(self.workspace)[1]["forced"])
        policy_action(self.workspace, "rollback", actor="human")
        self.assertEqual(ck.read_active(self.workspace)["checkpoint"], 1)

    def test_freeze_starts_a_new_version_and_old_scores_stop_counting(self) -> None:
        first = self.trained()
        frozen = freeze(self.workspace, actor="human")
        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(frozen["version"], first.heldout_version, "same held-out episodes: same version")
        _write_project_cases(self.workspace, _habit_cases())
        with radar(UP):
            second = train(self.workspace, actor="human", force=True)
        self.assertEqual(second.status, "trained")
        self.assertTrue(second.heldout_stale, "the battery grew: the frozen set predates it")
        frozen = freeze(self.workspace, actor="human")
        self.assertNotEqual(frozen["version"], first.heldout_version)
        self.assertIsNone(train_module.latest_score(self.workspace, heldout_version=frozen["version"]))
        gate = steer_gate(self.workspace)
        self.assertFalse(gate.open)
        self.assertIn("older exam set", "; ".join(gate.reasons))


class PolicyRouteTest(_Workspace):
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
        response = self._get(handler, "/api/sessions/websocket%3Aabc/policy")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertFalse(body["enabled"])
        self.assertEqual(body["settings_file"], ".navin/policy.json")
        self.assertEqual(body["policy_dir"], ".navin/policy")
        self.assertFalse(body["radar"]["up"])
        self.assertFalse((self.workspace / ".navin").exists(), "reading creates nothing")
        refused = self._get(handler, '/api/sessions/websocket%3Aabc/policy?fields={"enabled":true}')
        self.assertEqual(refused.status_code, 409, "no policy without a radar")
        with radar(UP):
            response = self._get(handler, '/api/sessions/websocket%3Aabc/policy?fields={"enabled":true}')
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertTrue(body["enabled"])
        self.assertFalse(body["steer"])
        self.assertIn("tool_registered", body)
        bad = self._get(handler, '/api/sessions/websocket%3Aabc/policy?fields={"steer":true}')
        self.assertEqual(bad.status_code, 409, "gate closed")
        junk = self._get(handler, '/api/sessions/websocket%3Aabc/policy?fields={"nope":1}')
        self.assertEqual(junk.status_code, 400)

    def test_actions_keep_the_actor_rule(self) -> None:
        handler = self._handler()
        self.enable()
        forbidden = self._get(handler, "/api/sessions/websocket%3Aabc/policy/action?action=train&actor=auto")
        self.assertEqual(forbidden.status_code, 403)
        with radar(UP):
            with patch("navin.policy.state.spawn_train", side_effect=lambda ws, **kw: train(ws, actor="human", force=True).as_dict()):
                trained = self._get(handler, "/api/sessions/websocket%3Aabc/policy/action?action=train&actor=human")
        self.assertEqual(trained.status_code, 200, trained.body)
        payload = json.loads(trained.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["status"], "trained")
        self.assertEqual(payload["state"]["active"]["checkpoint"], 1)
        unknown = self._get(handler, "/api/sessions/websocket%3Aabc/policy/action?action=dance&actor=human")
        self.assertEqual(unknown.status_code, 400)
        bad_number = self._get(handler, "/api/sessions/websocket%3Aabc/policy/action?action=force&actor=human&number=x")
        self.assertEqual(bad_number.status_code, 400)


class CliTest(_Workspace):
    def _invoke(self, *args: str):  # type: ignore[no-untyped-def]
        from typer.testing import CliRunner

        from navin.cli.commands import app

        return CliRunner().invoke(app, ["agi", "policy", *args, "--project", str(self.workspace)])

    def test_status_on_set_off(self) -> None:
        status = self._invoke("status", "--json")
        self.assertEqual(status.exit_code, 0, status.output)
        payload = json.loads(status.stdout)
        self.assertFalse(payload["enabled"])
        self.assertFalse((self.workspace / ".navin").exists(), "status reads, never writes")

        refused = self._invoke("on")
        self.assertNotEqual(refused.exit_code, 0, "radar down: refused")
        self.assertIn("no policy without a radar", refused.output)
        with radar(UP):
            turned_on = self._invoke("on")
        self.assertEqual(turned_on.exit_code, 0, turned_on.output)
        self.assertTrue(policy_enabled(self.workspace, "log"))
        self.assertFalse(policy_enabled(self.workspace, "steer"))

        refused = self._invoke("set", "steer", "on")
        self.assertNotEqual(refused.exit_code, 0, "gate closed: refused")
        self.assertIn("gate closed", refused.output)
        self.assertFalse(read_settings(self.workspace).steer)
        changed = self._invoke("set", "train_every", "7")
        self.assertEqual(changed.exit_code, 0, changed.output)
        self.assertEqual(read_settings(self.workspace).train_every, 7)
        rejected = self._invoke("set", "min_rows", "1")
        self.assertNotEqual(rejected.exit_code, 0)

        human = self._invoke("status")
        self.assertEqual(human.exit_code, 0, human.output)
        self.assertIn("Radar", human.output)
        self.assertIn("Steer gate", human.output)

        turned_off = self._invoke("off")
        self.assertEqual(turned_off.exit_code, 0, turned_off.output)
        self.assertFalse(read_settings(self.workspace).enabled)

    def test_agi_status_lists_the_policy_switches(self) -> None:
        from typer.testing import CliRunner

        from navin.cli.commands import app

        result = CliRunner().invoke(app, ["agi", "status", "--json", "--project", str(self.workspace)])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.stdout)
        self.assertIn("policy", payload)
        self.assertFalse(payload["policy"]["enabled"])
        human = CliRunner().invoke(app, ["agi", "status", "--project", str(self.workspace)])
        self.assertIn("steer", human.output)

    def test_train_checkpoints_scoreboard_journal_from_the_terminal(self) -> None:
        self.enable()
        with radar(UP), patch("navin.policy.state.spawn_train", side_effect=lambda ws, **kw: train(ws, actor="human", force=True).as_dict()):
            trained = self._invoke("train")
        self.assertEqual(trained.exit_code, 0, trained.output)
        self.assertIn("trained", trained.output)
        self.assertIn("activated", trained.output)
        for command in ("checkpoints", "scoreboard", "journal", "log"):
            result = self._invoke(command)
            self.assertEqual(result.exit_code, 0, f"{command}: {result.output}")
        listed = self._invoke("checkpoints", "--json")
        self.assertEqual(json.loads(listed.stdout)[0]["number"], 1)
        ab = self._invoke("ab")
        self.assertEqual(ab.exit_code, 0, ab.output)
        published = self._invoke("publish", "--yes")
        self.assertEqual(published.exit_code, 0, published.output)
        self.assertIn("published", published.output)
        listing = self._invoke("published", "--json")
        self.assertEqual(len(json.loads(listing.stdout)), 1)


# --------------------------------------------------------------------------
# S4.6 - proof: the turn envelope, hot path isolation, the Guardrails copy
# --------------------------------------------------------------------------


class TurnEnvelopeTest(_Workspace):
    """Bench: a turn with the flag off costs what it cost before S4 (p95)."""

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

    def test_flag_off_turn_has_no_policy_hook_and_the_same_p95(self) -> None:
        with_s4 = list(DEFAULT_HOOK_FACTORIES)
        without_s4 = [f for f in DEFAULT_HOOK_FACTORIES if f is not create_policy_hook]
        self.assertEqual(len(with_s4), len(without_s4) + 1)
        self.assertEqual(self._hooks_of(self._chain(with_s4, self.workspace)), self._hooks_of(self._chain(without_s4, self.workspace)))
        self.assertNotIn("PolicyHook", self._hooks_of(self._chain(with_s4, self.workspace)))
        rounds = 300
        self._p95_ms(without_s4, 50)  # warm-up
        baseline = self._p95_ms(without_s4, rounds)
        candidate = self._p95_ms(with_s4, rounds)
        # One os.stat per turn: within the noise of the chain itself. The bound
        # is generous on purpose (WSL2, CI); it guards against parsing, threads
        # or disk writes sneaking into the off path, not against jitter.
        self.assertLess(candidate, baseline * 1.5 + 0.5, f"p95 {candidate:.3f} ms vs {baseline:.3f} ms without S4")
        self.assertFalse((self.workspace / ".navin").exists(), "not one file after 300 turns")
        self.assertFalse(jobs.runner_alive(), "no thread")
        self.assertFalse(policy_dir(self.workspace).exists())

    def test_flag_on_turn_only_counts_and_writes_nothing(self) -> None:
        self.enable(train_every=1000)
        chain = self._chain(list(DEFAULT_HOOK_FACTORIES), self.workspace)
        self.assertIn("PolicyHook", self._hooks_of(chain))
        rounds = 100
        candidate = self._p95_ms(list(DEFAULT_HOOK_FACTORIES), rounds)
        self.assertTrue(flush_recorder(10.0))
        self.assertEqual(count_steps(self.workspace), 0, "a chat turn is never a trajectory")
        self.assertFalse(trajectories_path(self.workspace).exists())
        self.assertEqual(jobs.turns_since_train(self.workspace), rounds)
        self.assertLess(candidate, 25.0, f"p95 {candidate:.3f} ms with the counter on")


class HotPathIsolationTest(unittest.TestCase):
    """S4 never leaks into the loop, the runner or the context builder."""

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

    def test_hot_path_files_do_not_mention_the_policy(self) -> None:
        for rel in self.HOT_PATH:
            source = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("navin.policy", source, rel)
            self.assertNotIn("policy_next", source, rel)
            self.assertNotIn("PolicyHook", source, rel)

    def test_the_tool_module_imports_the_policy_lazily(self) -> None:
        source = (REPO / "navin" / "agent" / "tools" / "policy_next.py").read_text(encoding="utf-8")
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertFalse(any("navin.policy" in line for line in top_level), top_level)

    def test_nothing_touches_the_agent_loop_or_the_served_model(self) -> None:
        for path in sorted((REPO / "navin" / "policy").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("navin.agent.loop", source, path.name)
            self.assertNotIn("fine_tune", source, path.name)
            self.assertNotIn("openrouter", source.lower(), path.name)

    def test_no_dashes_in_the_new_modules(self) -> None:
        files = sorted((REPO / "navin" / "policy").rglob("*.py"))
        files.append(REPO / "navin" / "policy" / "exams" / "battery.json")
        files.append(REPO / "navin" / "cli" / "policy.py")
        files.append(REPO / "navin" / "agent" / "tools" / "policy_next.py")
        files.append(REPO / "tests" / "test_policy.py")
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("\u2013", text, path.name)

    def test_guardrails_copy_says_off_sandbox_train_steer_is_gates(self) -> None:
        for locale in ("en", "fr"):
            data = json.loads((REPO / "webui" / "src" / "i18n" / "locales" / locale / "common.json").read_text(encoding="utf-8"))
            policy = data["dev"]["agi"]["policy"]
            hint = policy["hint"]
            self.assertNotIn("\u2014", hint)
            self.assertNotIn("\u2013", hint)
            for key in ("enabledDetail", "enabledLocked", "logDetail", "trainDetail", "steerDetail", "steerLocked", "radarDown"):
                self.assertIn(key, policy)
            for action in ("train", "exam", "ab", "freeze", "rollback", "force", "publish", "adopt", "unpublish"):
                self.assertIn(action, policy["actions"])
                self.assertIn(action, policy["done"])
        en = json.loads((REPO / "webui" / "src" / "i18n" / "locales" / "en" / "common.json").read_text(encoding="utf-8"))
        hint = en["dev"]["agi"]["policy"]["hint"]
        self.assertIn("Off by default", hint)
        self.assertIn("never the chat model", hint)
        self.assertIn("no suite down", hint)
        self.assertIn("executes nothing", hint)
        self.assertIn("cuts itself off", hint)
        self.assertIn("radar", hint)
        self.assertIn("never fine-tuned", en["dev"]["agi"]["policy"]["enabledDetail"])
        self.assertIn("child process", en["dev"]["agi"]["policy"]["trainDetail"])


if __name__ == "__main__":
    unittest.main()
