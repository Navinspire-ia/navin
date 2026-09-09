# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""S2 acceptance: autonomous skill evolution (S2.0 to S2.6).

The gate for "S2 is done": on a project with the flag on, a draft skill is
born alone, passes the exam, enters the project without a click, and the
same scenario with the flag off changes nothing. Publishing to every
project still needs a human.

The exam model is swapped for a deterministic "echo" model (the answer is
the skill layer itself), so a case passes when the skill text carries the
expected words and no forbidden phrase. That keeps every scenario offline,
reproducible and fast; the real lexical model is covered separately.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from navin.agent.hook import AgentRunHookContext, AgentTurnHookContext
from navin.agent.hooks import DEFAULT_HOOK_FACTORIES
from navin.agent.skills import SkillsLoader, clear_skills_index_cache
from navin.skills_evolve import jobs, paths
from navin.skills_evolve.author import DraftBrief, TemplateAuthor, brief_from_failure
from navin.skills_evolve.battery import BATTERY_FILE, ExamTamperedError, load_battery
from navin.skills_evolve.drafts import list_drafts, read_draft, read_journal, write_draft
from navin.skills_evolve.exam import ExamBudget, LexicalSkillModel, grade, run_exam
from navin.skills_evolve.guard import run_guard
from navin.skills_evolve.hook import create_skills_evolve_hook, reset_strikes
from navin.skills_evolve.jobs import drain_jobs, enqueue_draft_job, pending_jobs
from navin.skills_evolve.pipeline import PipelineDeps, run_pipeline
from navin.skills_evolve.promote import (
    HumanRequiredError,
    PromotionError,
    force_promote,
    publish_to_harness,
    rollback_promotion,
)
from navin.skills_evolve.settings import (
    SETTINGS_NAME,
    SkillsEvolveSettings,
    clear_settings_cache,
    evolve_enabled,
    read_settings,
    settings_path,
    update_settings,
    write_settings,
)
from navin.skills_evolve.state import AgiActionError, agi_action, agi_state

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _skill(name: str, body: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "Test skill {name}"\n'
        'metadata: {"navin": {"category": "custom", "origin": "skills-evolve"}}\n'
        "---\n"
        f"# {name}\n\n## Overview\n\n{body}\n"
    )


# Covers every expectation of the code suite, nothing forbidden.
CODE_WORDS = (
    "Run the tests, then verify. Read the file and apply a patch. For a refactor keep "
    "behaviour. Reproduce first and collect the logs. Run git status before cleanup. "
    "Install the dependency. Done means evidence."
)
# Covers every expectation of the browser suite.
BROWSER_WORDS = (
    "Take a snapshot and use a stable selector. Wait with a timeout. Fill the form and "
    "submit once. On a captcha stop and ask the user. Take a screenshot and check the "
    "text. Never write a secret. If a click did nothing, snapshot again and retry once."
)
# Trips the forbid list of two code cases.
FORBIDDEN = "When in doubt run git reset --hard and start over."
# Nothing the battery asks for.
NOISE = "Prefer short sentences. Keep the answer brief and polite."


class EchoModel:
    """Answer = the skill layer. A case passes iff the skills say the words."""

    def __init__(self, skills: list[str]) -> None:
        self._blob = "\n".join(skills)

    def complete(self, prompt: str) -> str:
        return self._blob


def echo_factory(skills):  # type: ignore[no-untyped-def]
    return EchoModel(list(skills))


class ScriptedAuthor:
    """Draft = first version, each revise = the next scripted version."""

    def __init__(self, name: str, versions: list[str]) -> None:
        self.name = name
        self.versions = [_skill(name, body) for body in versions]
        self.revise_calls = 0

    def draft(self, brief: DraftBrief) -> str:
        return self.versions[0]

    def revise(self, markdown: str, feedback: list[dict[str, Any]], attempt: int) -> str:
        self.revise_calls += 1
        index = min(attempt, len(self.versions) - 1)
        return self.versions[index]


class TamperingAuthor(ScriptedAuthor):
    """Cheats: rewrites the exam file instead of the draft."""

    def __init__(self, name: str, battery_file: Path) -> None:
        super().__init__(name, [NOISE, NOISE + " tests verify"])
        self._battery = battery_file

    def revise(self, markdown: str, feedback: list[dict[str, Any]], attempt: int) -> str:
        raw = json.loads(self._battery.read_text(encoding="utf-8"))
        for suite in raw["suites"]:
            for case in suite["cases"]:
                case["expect_contains"] = []
        self._battery.write_text(json.dumps(raw), encoding="utf-8")
        return super().revise(markdown, feedback, attempt)


def _turn(workspace: Path | None, **overrides: Any) -> AgentTurnHookContext:
    base: dict[str, Any] = dict(
        workspace=workspace,
        channel="webui",
        chat_id="chat-1",
        session_key="webui:chat-1",
        metadata={},
        ephemeral=False,
    )
    base.update(overrides)
    return AgentTurnHookContext(**base)


def _failed_run(tool: str, detail: str) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": "do it"}],
        final_content="could not",
        tools_used=[tool],
        stop_reason="end_turn",
        tool_events=[{"name": tool, "status": "error", "detail": detail}],
    )


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_settings_cache)
        self.addCleanup(reset_strikes)
        self.addCleanup(clear_skills_index_cache)
        self.workspace = Path(self._tmp.name) / "project"
        self.workspace.mkdir()
        # The corridor runs on a daemon thread in production; tests drain by hand.
        jobs.configure(auto_thread=False)
        self.addCleanup(jobs.configure, auto_thread=True)
        # The harness folder must never be the real ~/.navin/skills.
        self.harness = Path(self._tmp.name) / "home" / ".navin" / "skills"
        patcher = patch.object(paths, "user_skills_dir", lambda: self.harness)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A private copy of the battery, so a cheating author can be caught
        # without touching the shipped file.
        self.battery_file = Path(self._tmp.name) / "battery.json"
        shutil.copyfile(BATTERY_FILE, self.battery_file)
        self.battery = load_battery(self.battery_file)

    def deps(self, author: Any) -> PipelineDeps:
        return PipelineDeps(author=author, model_factory=echo_factory, battery=self.battery)

    def enable(self, **fields: Any) -> None:
        update_settings(self.workspace, {"enabled": True, **fields})

    def project_skill(self, name: str, body: str) -> Path:
        path = paths.project_skill_file(self.workspace, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_skill(name, body), encoding="utf-8")
        clear_skills_index_cache()
        return path

    def live_skill_names(self) -> set[str]:
        clear_skills_index_cache()
        entries = SkillsLoader(self.workspace).list_skills(filter_unavailable=False)
        return {e["name"] for e in entries if e.get("source") == "workspace"}

    def events(self) -> list[str]:
        return [row["event"] for row in read_journal(self.workspace, limit=200)]


# --------------------------------------------------------------------------
# S2.0 - the net: flag off is a no-op
# --------------------------------------------------------------------------


class FlagOffTest(_Workspace):
    def test_default_is_off_and_missing_file_means_off(self) -> None:
        settings = read_settings(self.workspace)
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.feature("draft"))
        self.assertFalse(evolve_enabled(self.workspace))
        self.assertFalse(evolve_enabled(None))
        self.assertEqual(SETTINGS_NAME, "skills-evolve.json")
        self.assertFalse((self.workspace / ".navin").exists())

    def test_malformed_flag_stays_off(self) -> None:
        path = settings_path(self.workspace)
        path.parent.mkdir(parents=True)
        for raw in ("not json", "[]", '{"enabled": "yes"}', '{"draft": true}', ""):
            path.write_text(raw, encoding="utf-8")
            clear_settings_cache()
            self.assertFalse(evolve_enabled(self.workspace), raw)

    def test_hook_factory_answers_none_and_touches_nothing(self) -> None:
        self.assertIsNone(create_skills_evolve_hook(_turn(self.workspace)))
        self.assertIsNone(create_skills_evolve_hook(_turn(None)))
        self.assertFalse((self.workspace / ".navin").exists())

    def test_enqueue_pipeline_guard_and_actions_are_no_ops(self) -> None:
        brief = brief_from_failure("apply_patch", "hunk failed", 3)
        self.assertFalse(enqueue_draft_job(self.workspace, brief))
        self.assertEqual(run_pipeline(self.workspace, brief, self.deps(TemplateAuthor())).status, "skipped")
        self.assertEqual(run_guard(self.workspace)["status"], "skipped")
        self.assertEqual(drain_jobs(self.workspace), [])
        with self.assertRaises(AgiActionError) as caught:
            agi_action(self.workspace, "run")
        self.assertEqual(caught.exception.status, 409)
        # Not a folder, not a file, not a thread.
        self.assertFalse((self.workspace / ".navin").exists())
        self.assertFalse(jobs.runner_alive())

    def test_state_is_readable_off_and_hides_nothing_dangerous(self) -> None:
        state = agi_state(self.workspace)
        self.assertFalse(state["enabled"])
        self.assertEqual(state["drafts"], [])
        self.assertEqual(state["pending_jobs"], [])
        self.assertEqual(state["journal"], [])
        self.assertEqual(state["battery"]["version"], load_battery().version)
        self.assertFalse((self.workspace / ".navin").exists())

    def test_default_factories_include_the_hook_once_after_the_journal(self) -> None:
        names = [f.__name__ for f in DEFAULT_HOOK_FACTORIES]
        self.assertEqual(names.count("create_skills_evolve_hook"), 1)
        self.assertLess(names.index("create_episode_journal_hook"), names.index("create_skills_evolve_hook"))

    def test_off_path_is_one_stat_cheap(self) -> None:
        """Micro-bench: a chat turn with the flag off pays a few microseconds."""
        turn = _turn(self.workspace)
        loops = 20_000
        start = time.perf_counter()
        for _ in range(loops):
            create_skills_evolve_hook(turn)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed / loops, 50e-6, f"{elapsed / loops * 1e6:.1f} us per call")

    def test_same_scenario_off_changes_nothing(self) -> None:
        """The S2 gate, second half: three failures with the flag off leave no trace."""
        hook = create_skills_evolve_hook(_turn(self.workspace))
        self.assertIsNone(hook)
        before = sorted(p.relative_to(self.workspace) for p in self.workspace.rglob("*"))
        self.assertEqual(before, [])
        self.assertEqual(self.live_skill_names(), set())


class SettingsTest(_Workspace):
    def test_master_and_stages(self) -> None:
        self.enable()
        settings = read_settings(self.workspace)
        self.assertTrue(settings.enabled and settings.draft and settings.promote_project)
        self.assertFalse(settings.publish_harness, "publishing is never on by default")
        update_settings(self.workspace, {"promote_project": False})
        self.assertFalse(evolve_enabled(self.workspace, "promote_project"))
        self.assertTrue(evolve_enabled(self.workspace, "draft"))

    def test_turning_the_master_off_keeps_the_choices(self) -> None:
        self.enable(publish_harness=True, max_attempts=2)
        update_settings(self.workspace, {"enabled": False})
        self.assertFalse(evolve_enabled(self.workspace, "publish_harness"))
        stored = read_settings(self.workspace)
        self.assertTrue(stored.publish_harness)
        self.assertEqual(stored.max_attempts, 2)

    def test_bad_fields_are_rejected_without_writing(self) -> None:
        for fields in ({}, {"enabled": "yes"}, {"bogus": True}, {"max_attempts": 99}, {"author": "gpt"}):
            with self.assertRaises(ValueError):
                update_settings(self.workspace, fields)  # type: ignore[arg-type]
        self.assertFalse(settings_path(self.workspace).exists())

    def test_write_settings_round_trip(self) -> None:
        write_settings(self.workspace, SkillsEvolveSettings(enabled=True, exam_model="llm", failure_threshold=2))
        stored = read_settings(self.workspace)
        self.assertEqual((stored.enabled, stored.exam_model, stored.failure_threshold), (True, "llm", 2))


# --------------------------------------------------------------------------
# S2.1 - the draft corridor
# --------------------------------------------------------------------------


class DraftCorridorTest(_Workspace):
    def test_loader_ignores_the_draft_folder(self) -> None:
        self.enable()
        write_draft(self.workspace, "hidden-draft", _skill("hidden-draft", CODE_WORDS), origin={"kind": "manual"})
        self.assertTrue(paths.draft_skill_file(self.workspace, "hidden-draft").is_file())
        self.assertNotIn("hidden-draft", self.live_skill_names())
        self.assertFalse(paths.project_skill_file(self.workspace, "hidden-draft").exists())
        self.assertEqual(self.events(), ["created"])

    def test_write_draft_validates_and_records(self) -> None:
        self.enable()
        record = write_draft(self.workspace, "My Draft", _skill("my-draft", NOISE), origin={"kind": "manual"})
        self.assertEqual(record.name, "my-draft")
        self.assertEqual(record.status, "drafting")
        self.assertEqual([r.name for r in list_drafts(self.workspace)], ["my-draft"])
        from navin.skills_evolve.drafts import DraftError

        with self.assertRaises(DraftError):
            write_draft(self.workspace, "other", "no frontmatter at all")

    def test_repeated_failure_queues_a_job_after_the_turn_not_in_it(self) -> None:
        self.enable(failure_threshold=3)
        hook = create_skills_evolve_hook(_turn(self.workspace))
        self.assertIsNotNone(hook)
        for _ in range(2):
            asyncio.run(hook.after_run(_failed_run("apply_patch", "hunk #1 FAILED at 12")))
        self.assertEqual(pending_jobs(self.workspace), [], "two strikes are not enough")
        asyncio.run(hook.after_run(_failed_run("apply_patch", "hunk #1 FAILED at 40")))
        queued = pending_jobs(self.workspace)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["source"], "repeated_failure")
        self.assertEqual(queued[0]["brief"]["family"], "code")
        # The turn only appended to the queue: no SKILL.md, no exam, no skill.
        self.assertEqual(list(self.workspace.rglob("SKILL.md")), [])
        self.assertEqual(self.live_skill_names(), set())
        self.assertFalse(jobs.runner_alive())
        # A fourth strike within the cooldown does not queue twice.
        asyncio.run(hook.after_run(_failed_run("apply_patch", "hunk #1 FAILED at 41")))
        self.assertEqual(len(pending_jobs(self.workspace)), 1)

    def test_heartbeat_and_ephemeral_turns_never_count(self) -> None:
        self.enable()
        self.assertIsNone(create_skills_evolve_hook(_turn(self.workspace, ephemeral=True)))
        self.assertIsNone(create_skills_evolve_hook(_turn(self.workspace, session_key="heartbeat")))
        self.assertIsNone(create_skills_evolve_hook(_turn(self.workspace, metadata={"heartbeat": True})))

    def test_hook_never_raises_on_odd_runs(self) -> None:
        self.enable()
        hook = create_skills_evolve_hook(_turn(self.workspace))
        odd = AgentRunHookContext(messages=[], tool_events=[{"status": "error"}, "junk", {"name": "", "status": "error"}])  # type: ignore[list-item]
        asyncio.run(hook.after_run(odd))
        asyncio.run(hook.on_error(AgentRunHookContext(messages=[], exception=RuntimeError("x"))))
        self.assertEqual(pending_jobs(self.workspace), [])

    def test_draft_stage_off_silences_the_hook_and_the_queue(self) -> None:
        self.enable(draft=False)
        self.assertIsNone(create_skills_evolve_hook(_turn(self.workspace)))
        self.assertFalse(enqueue_draft_job(self.workspace, brief_from_failure("exec", "boom", 3)))


# --------------------------------------------------------------------------
# S2.2 - the exam
# --------------------------------------------------------------------------


class BatteryTest(_Workspace):
    def test_shipped_battery_has_three_suites_and_a_content_version(self) -> None:
        battery = load_battery()
        self.assertEqual([s.id for s in battery.suites], ["code", "browser", "desk"])
        self.assertGreaterEqual(battery.total_cases, 18)
        self.assertRegex(battery.version, r"^s2-r\d+-[0-9a-f]{12}$")
        self.assertNotIn("\u2014", BATTERY_FILE.read_text(encoding="utf-8"))

    def test_changing_one_case_changes_the_version(self) -> None:
        raw = json.loads(self.battery_file.read_text(encoding="utf-8"))
        raw["suites"][0]["cases"][0]["expect_contains"].append("banana")
        self.battery_file.write_text(json.dumps(raw), encoding="utf-8")
        edited = load_battery(self.battery_file)
        self.assertNotEqual(edited.version, self.battery.version)
        self.assertEqual(edited.revision, self.battery.revision)

    def test_tampering_during_a_run_is_detected(self) -> None:
        self.battery_file.write_text(self.battery_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        # Whitespace only: canonical JSON is the same, so no false alarm.
        self.battery.verify_unchanged()
        raw = json.loads(self.battery_file.read_text(encoding="utf-8"))
        raw["suites"][1]["cases"][0]["forbid"] = ["anything"]
        self.battery_file.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(ExamTamperedError):
            self.battery.verify_unchanged()
        with self.assertRaises(ExamTamperedError):
            run_exam(self.battery, [], model_factory=echo_factory)


class ExamTest(_Workspace):
    def test_score_and_verdict(self) -> None:
        empty = run_exam(self.battery, [], model_factory=echo_factory)
        self.assertEqual(empty.score, 0)
        code = run_exam(self.battery, [_skill("c", CODE_WORDS)], model_factory=echo_factory)
        self.assertEqual(code.suite("code").passed, code.suite("code").total)
        self.assertEqual(code.suite("browser").passed, 0)
        self.assertEqual(code.score, round(20 * code.passed / code.total))
        verdict = grade(code, empty)
        self.assertEqual(verdict.overall, "up")
        self.assertEqual(verdict.suites, {"code": "up", "browser": "flat", "desk": "flat"})
        self.assertTrue(verdict.eligible)
        # A forbidden phrase in an otherwise identical layer: down, not eligible.
        worse = run_exam(self.battery, [_skill("c", CODE_WORDS), _skill("d", FORBIDDEN)], model_factory=echo_factory)
        regression = grade(worse, code)
        self.assertEqual(regression.suites["code"], "down")
        self.assertTrue(regression.regressed)
        self.assertFalse(regression.eligible)

    def test_budget_overrun_is_a_failed_exam_never_a_promotion(self) -> None:
        class SlowModel:
            def complete(self, prompt: str) -> str:
                time.sleep(0.02)
                return CODE_WORDS

        report = run_exam(self.battery, [], model_factory=lambda skills: SlowModel(), budget=ExamBudget(timeout_s=0.03))
        self.assertTrue(report.failed)
        self.assertEqual(report.score, 0)
        self.assertIn("timeout", report.reason or "")
        baseline = run_exam(self.battery, [], model_factory=echo_factory)
        verdict = grade(report, baseline)
        self.assertEqual(verdict.overall, "down")
        self.assertFalse(verdict.eligible)

    def test_broken_model_is_a_failed_exam(self) -> None:
        def broken(skills):  # type: ignore[no-untyped-def]
            raise RuntimeError("no provider")

        report = run_exam(self.battery, [], model_factory=broken)
        self.assertTrue(report.failed)
        self.assertIn("no provider", report.reason or "")

    def test_lexical_model_answers_from_the_skill_layer_only(self) -> None:
        model = LexicalSkillModel([_skill("t", "Run the tests after every change.\nTake a snapshot before clicking.")])
        self.assertIn("tests", model.complete("A unit test fails after my change."))
        self.assertNotIn("snapshot", model.complete("A unit test fails after my change."))
        self.assertEqual(model.complete(""), "")
        self.assertEqual(LexicalSkillModel([]).complete("anything at all here"), "")


# --------------------------------------------------------------------------
# S2.3 / S2.4 - correction loop, promotion, rollback
# --------------------------------------------------------------------------


class CorridorTest(_Workspace):
    def test_gate_up_without_down_promotes_without_a_click(self) -> None:
        """The S2 gate, first half: draft born, examined, in the project, no click."""
        self.enable()
        author = ScriptedAuthor("good-code", [CODE_WORDS])
        result = run_pipeline(self.workspace, DraftBrief(name="good-code", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "promoted", result.reason)
        self.assertTrue(result.promoted)
        self.assertEqual(result.baseline_score, 0)
        self.assertGreater(result.best_score, 0)
        self.assertEqual(result.attempts, 1)
        self.assertTrue(paths.project_skill_file(self.workspace, "good-code").is_file())
        self.assertIn("good-code", self.live_skill_names(), "the live loader sees the promoted skill")
        record = read_draft(self.workspace, "good-code")
        self.assertEqual(record.status, "promoted")
        self.assertIsNone(record.forced_by)
        self.assertEqual(self.events()[:3], ["created", "examined", "kept"])
        self.assertIn("promoted", self.events())

    def test_promote_stage_off_leaves_an_eligible_draft_for_the_human(self) -> None:
        self.enable(promote_project=False)
        author = ScriptedAuthor("good-code", [CODE_WORDS])
        result = run_pipeline(self.workspace, DraftBrief(name="good-code", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "eligible")
        self.assertFalse(paths.project_skill_file(self.workspace, "good-code").exists())
        self.assertNotIn("good-code", self.live_skill_names())
        payload = agi_action(self.workspace, "promote", name="good-code", actor="human")
        self.assertEqual(payload["result"]["status"], "promoted")
        self.assertIn("good-code", self.live_skill_names())

    def test_down_means_no_project_skill_and_the_draft_is_discarded(self) -> None:
        self.enable()
        self.project_skill("base", CODE_WORDS)
        author = ScriptedAuthor("bad-idea", [FORBIDDEN, FORBIDDEN + " again", FORBIDDEN + " and again"])
        result = run_pipeline(self.workspace, DraftBrief(name="bad-idea", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "discarded")
        self.assertEqual(result.attempts, 3, "K corrections were tried before giving up")
        self.assertFalse(paths.project_skill_file(self.workspace, "bad-idea").exists())
        self.assertFalse(paths.draft_dir(self.workspace, "bad-idea").exists())
        self.assertEqual(self.live_skill_names(), {"base"})
        self.assertIn("discarded", self.events())

    def test_flat_is_kept_for_a_human_and_never_promoted_alone(self) -> None:
        self.enable()
        self.project_skill("base", CODE_WORDS)
        author = ScriptedAuthor("meh", [NOISE, NOISE + " one", NOISE + " two"])
        result = run_pipeline(self.workspace, DraftBrief(name="meh", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "flat")
        self.assertEqual(read_draft(self.workspace, "meh").status, "flat")
        self.assertFalse(paths.project_skill_file(self.workspace, "meh").exists())

    def test_correction_loop_keeps_the_best_version_within_k(self) -> None:
        self.enable(max_attempts=3)
        self.project_skill("base", CODE_WORDS)
        # v1 flat, v2 regresses, v3 improves: the corridor must end on v3.
        author = ScriptedAuthor("learns", [NOISE, FORBIDDEN, BROWSER_WORDS])
        result = run_pipeline(self.workspace, DraftBrief(name="learns", description="d", family="browser"), self.deps(author))
        self.assertEqual(result.status, "promoted", result.reason)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(author.revise_calls, 2)
        promoted = paths.project_skill_file(self.workspace, "learns").read_text(encoding="utf-8")
        self.assertIn("snapshot", promoted)
        self.assertNotIn("reset --hard", promoted)
        history = read_draft(self.workspace, "learns").history
        verdicts = [h.get("verdict") for h in history if "attempt" in h]
        self.assertEqual(verdicts, ["flat", "down", "up"])

    def test_max_attempts_is_a_hard_stop(self) -> None:
        self.enable(max_attempts=2)
        self.project_skill("base", CODE_WORDS)
        author = ScriptedAuthor("stuck", [NOISE, NOISE + " a", NOISE + " b", NOISE + " c", BROWSER_WORDS])
        result = run_pipeline(self.workspace, DraftBrief(name="stuck", description="d", family="browser"), self.deps(author))
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.status, "flat")
        self.assertEqual(author.revise_calls, 1)

    def test_rewriting_the_exam_is_caught_and_nothing_is_promoted(self) -> None:
        self.enable()
        author = TamperingAuthor("cheater", self.battery_file)
        result = run_pipeline(self.workspace, DraftBrief(name="cheater", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "tampered")
        self.assertFalse(result.promoted)
        self.assertFalse(paths.project_skill_file(self.workspace, "cheater").exists())
        self.assertEqual(read_draft(self.workspace, "cheater").status, "rejected")
        self.assertIn("tampered", self.events())

    def test_post_copy_failure_rolls_the_project_skill_back(self) -> None:
        self.enable()
        author = ScriptedAuthor("fragile", [CODE_WORDS])
        with patch("navin.skills_evolve.promote.post_copy_verify", return_value="loader did not list it"):
            result = run_pipeline(self.workspace, DraftBrief(name="fragile", description="d", family="code"), self.deps(author))
        self.assertEqual(result.status, "rollback")
        self.assertIn("loader did not list it", result.reason or "")
        self.assertFalse(paths.project_skill_file(self.workspace, "fragile").exists())
        self.assertNotIn("fragile", self.live_skill_names())
        self.assertEqual(read_draft(self.workspace, "fragile").status, "rejected")
        self.assertIn("rollback", self.events())

    def test_exam_runs_in_a_sandbox_copy_not_in_the_project(self) -> None:
        self.enable(promote_project=False)
        self.project_skill("base", CODE_WORDS)
        before = {p.relative_to(self.workspace): p.read_bytes() for p in paths.project_skill_dir(self.workspace, "base").rglob("*") if p.is_file()}
        author = ScriptedAuthor("probe", [BROWSER_WORDS])
        run_pipeline(self.workspace, DraftBrief(name="probe", description="d", family="browser"), self.deps(author))
        after = {p.relative_to(self.workspace): p.read_bytes() for p in paths.project_skill_dir(self.workspace, "base").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(sorted(p.name for p in (self.workspace / ".navin" / "skills").iterdir()), ["base"])

    def test_queue_then_drain_runs_the_whole_corridor(self) -> None:
        self.enable()
        brief = DraftBrief(name="queued-one", description="d", family="code")
        self.assertTrue(enqueue_draft_job(self.workspace, brief, source="manual"))
        self.assertFalse(enqueue_draft_job(self.workspace, brief, source="manual"), "no duplicate job")
        results = drain_jobs(self.workspace, self.deps(ScriptedAuthor("queued-one", [CODE_WORDS])))
        self.assertEqual([r["status"] for r in results], ["promoted"])
        self.assertEqual(pending_jobs(self.workspace), [])
        self.assertFalse(paths.queue_path(self.workspace).exists())

    def test_promotion_is_noted_in_the_s1_journal_when_memory_is_on(self) -> None:
        import navin.cognition.registration as registration
        from navin.cognition.episodes import episodes_path, flush_episodes
        from navin.cognition.settings import clear_settings_cache as clear_cognition_cache
        from navin.cognition.settings import write_settings as write_cognition

        index_file = Path(self._tmp.name) / "machine" / "cognition-projects.json"
        with patch.object(registration, "index_path", lambda: index_file):
            write_cognition(self.workspace, enabled=True)
            self.addCleanup(clear_cognition_cache)
            self.enable()
            result = run_pipeline(
                self.workspace,
                DraftBrief(name="noted", description="d", family="code"),
                self.deps(ScriptedAuthor("noted", [CODE_WORDS])),
            )
            self.assertEqual(result.status, "promoted")
            self.assertTrue(flush_episodes(5.0))
            text = episodes_path(self.workspace).read_text(encoding="utf-8")
        self.assertIn("skill noted promoted", text)
        self.assertIn("-> ", text)


class GuardTest(_Workspace):
    def test_a_promoted_skill_that_regresses_later_is_retired(self) -> None:
        self.enable()
        self.project_skill("base", CODE_WORDS)
        result = run_pipeline(
            self.workspace,
            DraftBrief(name="drift", description="d", family="browser"),
            self.deps(ScriptedAuthor("drift", [BROWSER_WORDS])),
        )
        self.assertEqual(result.status, "promoted")
        clean = run_guard(self.workspace, self.deps(TemplateAuthor()))
        self.assertEqual(clean["retired"], [])
        self.assertEqual(clean["checked"][0]["verdict"], "up")
        # Someone edits the live skill and a forbidden practice creeps in.
        paths.project_skill_file(self.workspace, "drift").write_text(
            _skill("drift", BROWSER_WORDS + " " + FORBIDDEN), encoding="utf-8"
        )
        guarded = run_guard(self.workspace, self.deps(TemplateAuthor()))
        self.assertEqual(guarded["retired"], ["drift"])
        self.assertEqual(guarded["checked"][0]["suites"]["code"], "down")
        self.assertFalse(paths.project_skill_file(self.workspace, "drift").exists())
        self.assertNotIn("drift", self.live_skill_names())
        self.assertEqual(read_draft(self.workspace, "drift").status, "retired")
        # The text is kept in the draft folder for a human to look at.
        self.assertIn("reset --hard", paths.draft_skill_file(self.workspace, "drift").read_text(encoding="utf-8"))
        self.assertIn("retired", self.events())

    def test_guard_notices_a_skill_deleted_by_hand(self) -> None:
        self.enable()
        run_pipeline(
            self.workspace,
            DraftBrief(name="gone", description="d", family="code"),
            self.deps(ScriptedAuthor("gone", [CODE_WORDS])),
        )
        shutil.rmtree(paths.project_skill_dir(self.workspace, "gone"))
        guarded = run_guard(self.workspace, self.deps(TemplateAuthor()))
        self.assertEqual(guarded["retired"], ["gone"])
        self.assertEqual(read_draft(self.workspace, "gone").status, "retired")


# --------------------------------------------------------------------------
# S2.5 - the human is the plus, never the engine
# --------------------------------------------------------------------------


class HumanButtonsTest(_Workspace):
    def _flat_draft(self, name: str = "meh") -> None:
        self.project_skill("base", CODE_WORDS)
        result = run_pipeline(
            self.workspace,
            DraftBrief(name=name, description="d", family="code"),
            self.deps(ScriptedAuthor(name, [NOISE, NOISE + " a", NOISE + " b"])),
        )
        self.assertEqual(result.status, "flat")

    def _promoted(self, name: str = "good-code") -> None:
        result = run_pipeline(
            self.workspace,
            DraftBrief(name=name, description="d", family="code"),
            self.deps(ScriptedAuthor(name, [CODE_WORDS])),
        )
        self.assertEqual(result.status, "promoted")

    def test_forcing_a_flat_draft_needs_a_human_is_traced_and_reversible(self) -> None:
        self.enable()
        self._flat_draft()
        with self.assertRaises(HumanRequiredError):
            force_promote(self.workspace, "meh", actor="auto")
        self.assertFalse(paths.project_skill_file(self.workspace, "meh").exists())
        record = force_promote(self.workspace, "meh", actor="human")
        self.assertEqual(record.status, "promoted")
        self.assertEqual(record.forced_by, "human")
        self.assertIn("meh", self.live_skill_names())
        self.assertIn("forced", self.events())
        rollback_promotion(self.workspace, "meh", actor="human")
        self.assertNotIn("meh", self.live_skill_names())
        self.assertEqual(read_draft(self.workspace, "meh").status, "retired")
        self.assertTrue(paths.draft_skill_file(self.workspace, "meh").is_file(), "the text survives the rollback")

    def test_publishing_to_the_harness_refuses_the_engine_and_needs_the_switch(self) -> None:
        self.enable()
        self._promoted()
        with self.assertRaises(HumanRequiredError):
            publish_to_harness(self.workspace, "good-code", actor="auto")
        with self.assertRaises(PromotionError):
            publish_to_harness(self.workspace, "good-code", actor="human")
        self.assertFalse(self.harness.exists(), "harness intact without a click and a switch")
        update_settings(self.workspace, {"publish_harness": True})
        target = publish_to_harness(self.workspace, "good-code", actor="human")
        self.assertEqual(target, self.harness / "good-code" / "SKILL.md")
        self.assertTrue(target.is_file())
        self.assertIsNotNone(read_draft(self.workspace, "good-code").published_at)
        self.assertIn("published", self.events())

    def test_the_engine_never_publishes_even_with_the_switch_on(self) -> None:
        self.enable(publish_harness=True)
        self._promoted()
        for actor in ("auto", "guard", "", "engine"):
            with self.assertRaises(HumanRequiredError, msg=actor):
                publish_to_harness(self.workspace, "good-code", actor=actor)
        with self.assertRaises(AgiActionError) as caught:
            agi_action(self.workspace, "publish", name="good-code", actor="auto")
        self.assertEqual(caught.exception.status, 403)
        self.assertFalse(self.harness.exists())

    def test_actions_map_the_buttons_with_honest_statuses(self) -> None:
        self.enable()
        self._promoted()
        with self.assertRaises(AgiActionError) as missing:
            agi_action(self.workspace, "exam", name="nope")
        self.assertEqual(missing.exception.status, 404)
        with self.assertRaises(AgiActionError) as bad:
            agi_action(self.workspace, "explode")
        self.assertEqual(bad.exception.status, 400)
        with self.assertRaises(AgiActionError) as promoted:
            agi_action(self.workspace, "discard", name="good-code", actor="human")
        self.assertEqual(promoted.exception.status, 409, "a promoted skill is retired before it is discarded")
        payload = agi_action(self.workspace, "rollback", name="good-code", actor="human")
        self.assertEqual(payload["result"]["status"], "retired")
        payload = agi_action(self.workspace, "discard", name="good-code", actor="human")
        self.assertTrue(payload["result"]["removed"])
        self.assertEqual(payload["state"]["drafts"], [])

    def test_manual_draft_action_queues_outside_the_turn(self) -> None:
        self.enable()
        payload = agi_action(self.workspace, "draft", name="Hand Made", brief={"description": "x", "tool": "browser_click"}, actor="human")
        self.assertTrue(payload["result"]["queued"])
        self.assertEqual(payload["result"]["name"], "hand-made")
        self.assertEqual([j["name"] for j in payload["state"]["pending_jobs"]], ["hand-made"])
        self.assertEqual(pending_jobs(self.workspace)[0]["brief"]["family"], "browser")
        self.assertEqual(list(self.workspace.rglob("SKILL.md")), [], "queued, not written")

    def test_reexam_uses_the_same_battery_version(self) -> None:
        self.enable(promote_project=False)
        run_pipeline(
            self.workspace,
            DraftBrief(name="again", description="d", family="code"),
            self.deps(ScriptedAuthor("again", [CODE_WORDS])),
        )
        payload = agi_action(self.workspace, "exam", name="again", actor="human", deps=self.deps(TemplateAuthor()))
        self.assertEqual(payload["result"]["status"], "eligible")
        record = read_draft(self.workspace, "again")
        self.assertEqual(record.attempts, 2)
        self.assertEqual(record.battery_version, self.battery.version)

    def test_state_lists_drafts_with_scores_and_paths(self) -> None:
        self.enable()
        self._promoted()
        state = agi_state(self.workspace)
        draft = state["drafts"][0]
        self.assertEqual(draft["name"], "good-code")
        self.assertEqual(draft["status"], "promoted")
        self.assertEqual(draft["verdict"], "up")
        self.assertTrue(draft["in_project"])
        self.assertFalse(draft["in_harness"])
        self.assertEqual(draft["paths"]["project"], ".navin/skills/good-code/SKILL.md")
        self.assertGreater(draft["score"], draft["baseline_score"])


class AgiRouteTest(_Workspace):
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
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertFalse(body["enabled"])
        self.assertEqual(body["settings_file"], ".navin/skills-evolve.json")
        response = self._get(handler, '/api/sessions/websocket%3Aabc/agi?fields={"enabled":true}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.body)["enabled"])
        self.assertTrue(evolve_enabled(self.workspace, "draft"))
        response = self._get(handler, '/api/sessions/websocket%3Aabc/agi?fields={"draft":false}')
        self.assertFalse(json.loads(response.body)["draft"])
        self.assertTrue(json.loads(response.body)["enabled"])

    def test_bad_fields_answer_400_and_write_nothing(self) -> None:
        handler = self._handler()
        for query in ("fields=nope", 'fields=["x"]', 'fields={"bogus":true}', 'fields={"enabled":"yes"}'):
            response = self._get(handler, f"/api/sessions/websocket%3Aabc/agi?{query}")
            self.assertEqual(response.status_code, 400, query)
        self.assertFalse(settings_path(self.workspace).exists())

    def test_actions_off_are_409_and_publish_without_a_human_is_403(self) -> None:
        handler = self._handler()
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi/action?action=run")
        self.assertEqual(response.status_code, 409)
        self.enable(publish_harness=True)
        run_pipeline(
            self.workspace,
            DraftBrief(name="good-code", description="d", family="code"),
            self.deps(ScriptedAuthor("good-code", [CODE_WORDS])),
        )
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi/action?action=publish&name=good-code")
        self.assertEqual(response.status_code, 403, "no actor means the engine: refused")
        self.assertFalse(self.harness.exists())
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi/action?action=publish&name=good-code&actor=human")
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.harness / "good-code" / "SKILL.md").is_file())
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi/draft?name=good-code")
        self.assertEqual(response.status_code, 200)
        self.assertIn("name: good-code", json.loads(response.body)["markdown"])
        response = self._get(handler, "/api/sessions/websocket%3Aabc/agi/action")
        self.assertEqual(response.status_code, 400)

    def test_non_webui_session_is_404(self) -> None:
        response = self._get(self._handler(), "/api/sessions/telegram%3A42/agi")
        self.assertEqual(response.status_code, 404)


class CliTest(_Workspace):
    def _invoke(self, *args: str):  # type: ignore[no-untyped-def]
        from typer.testing import CliRunner

        from navin.cli.commands import app

        result = CliRunner().invoke(app, ["agi", *args, "--project", str(self.workspace)])
        return result

    def test_status_on_set_off(self) -> None:
        status = self._invoke("status", "--json")
        self.assertEqual(status.exit_code, 0, status.output)
        payload = json.loads(status.stdout)
        self.assertFalse(payload["skills_evolve"]["enabled"])
        self.assertFalse(payload["memory"]["enabled"])
        self.assertFalse((self.workspace / ".navin").exists(), "status reads, never writes")

        turned_on = self._invoke("on")
        self.assertEqual(turned_on.exit_code, 0, turned_on.output)
        self.assertTrue(evolve_enabled(self.workspace))

        changed = self._invoke("set", "publish_harness", "on")
        self.assertEqual(changed.exit_code, 0, changed.output)
        self.assertTrue(evolve_enabled(self.workspace, "publish_harness"))
        rejected = self._invoke("set", "max_attempts", "42")
        self.assertNotEqual(rejected.exit_code, 0)
        self.assertEqual(read_settings(self.workspace).max_attempts, 3)

        turned_off = self._invoke("off")
        self.assertEqual(turned_off.exit_code, 0, turned_off.output)
        self.assertFalse(evolve_enabled(self.workspace))

    def test_battery_and_drafts_commands(self) -> None:
        battery = self._invoke("battery")
        self.assertEqual(battery.exit_code, 0, battery.output)
        self.assertIn("code", battery.output)
        self.assertIn("browser", battery.output)
        self.assertIn("desk", battery.output)
        drafts = self._invoke("drafts")
        self.assertEqual(drafts.exit_code, 0, drafts.output)
        self.assertIn("off", drafts.output)
        publish = self._invoke("publish", "nothing", "--yes")
        self.assertNotEqual(publish.exit_code, 0, "flag off: refused")

    def test_memory_switches_live_in_the_same_command(self) -> None:
        import navin.cognition.registration as registration
        from navin.cognition import cognition_enabled
        from navin.cognition.settings import clear_settings_cache as clear_cognition_cache

        index_file = Path(self._tmp.name) / "machine" / "cognition-projects.json"
        with patch.object(registration, "index_path", lambda: index_file):
            self.addCleanup(clear_cognition_cache)
            result = self._invoke("memory", "on", "--recall", "off")
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(cognition_enabled(self.workspace, "episodes"))
            self.assertFalse(cognition_enabled(self.workspace, "recall"))


# --------------------------------------------------------------------------
# S2.6 - proof that nothing else moved
# --------------------------------------------------------------------------


class HotPathIsolationTest(unittest.TestCase):
    """S2 never leaks into the loop, the runner or the context builder."""

    HOT_PATH = (
        "navin/agent/loop.py",
        "navin/agent/runner.py",
        "navin/agent/context.py",
        "navin/agent/memory.py",
        "navin/agent/turn_hooks.py",
        "navin/agent/hook.py",
        "navin/agent/tools/registry.py",
        "navin/agent/tools/loader.py",
    )

    def test_hot_path_files_do_not_mention_skills_evolve(self) -> None:
        for rel in self.HOT_PATH:
            source = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("skills_evolve", source, rel)
            self.assertNotIn("skills-draft", source, rel)

    def test_loader_lists_the_draft_folder_as_non_skill(self) -> None:
        from navin.agent.skills import _NAVIN_NON_SKILL_CHILDREN

        self.assertIn("skills-draft", _NAVIN_NON_SKILL_CHILDREN)

    def test_no_dashes_in_the_new_modules(self) -> None:
        for path in sorted((REPO / "navin" / "skills_evolve").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("\u2013", text, path.name)
        cli = (REPO / "navin" / "cli" / "agi.py").read_text(encoding="utf-8")
        self.assertNotIn("\u2014", cli)
        self.assertNotIn("\u2013", cli)

    def test_guardrails_doc_says_off_auto_draft_publish_is_you(self) -> None:
        for locale in ("en", "fr"):
            data = json.loads((REPO / "webui" / "src" / "i18n" / "locales" / locale / "common.json").read_text(encoding="utf-8"))
            text = data["dev"]["guardrails"]["agiMovedDetail"]
            self.assertEqual(text.count(". "), 2, f"{locale}: three sentences expected")
            self.assertNotIn("\u2014", text)
            agi = data["dev"]["agi"]
            self.assertIn("evolveDetail", agi)
            self.assertIn("publishDetail", agi)
        self.assertNotIn("memory", json.loads((REPO / "webui" / "src" / "i18n" / "locales" / "en" / "common.json").read_text(encoding="utf-8"))["dev"]["guardrails"])


if __name__ == "__main__":
    unittest.main()
