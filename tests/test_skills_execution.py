# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral regression tests: real AgentRunner, file tools and isolated Python.

Scripted providers test the implementation deterministically. These tests
alone make no claim about the quality of a production model's proposed skills.
"""

import asyncio
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from navin.agent.hook import AgentRunHookContext, AgentTurnHookContext
from navin.command.modules import VALID_PRODUCT_MODULES
from navin.evals.agent_loop import ScriptedProvider
from navin.improvement.skills import applicable_skills, excluded_skills
from navin.providers.base import GenerationSettings
from navin.providers.factory import ProviderSnapshot
from navin.skills_evolve import jobs
from navin.skills_evolve.author import DraftBrief
from navin.skills_evolve.battery import load_battery
from navin.skills_evolve.drafts import read_draft
from navin.skills_evolve.exam import ExamReport, lexical_model_factory
from navin.skills_evolve.execution import ExecutionEvaluator, compare_execution
from navin.skills_evolve.execution_pipeline import execution_guard, validate_promotion
from navin.skills_evolve.execution_tasks import task_fingerprint, tasks
from navin.skills_evolve.execution_tools import run_solution
from navin.skills_evolve.hook import create_skills_evolve_hook, reset_strikes
from navin.skills_evolve.paths import project_skill_file
from navin.skills_evolve.pipeline import PipelineDeps, PipelineResult, run_pipeline
from navin.skills_evolve.promote import PromotionError
from navin.skills_evolve.settings import update_settings
from navin.skills_evolve.state import agi_state


def snapshot():
    return ProviderSnapshot(ScriptedProvider([]), "scripted-execution-test", 32000, ("test",), GenerationSettings())


def good_script(case):
    source = "solution.py" if case.suite == "code" else "task.json"
    steps = [{"tool": "read_file", "args": {"path": source}}]
    if case.suite == "code":
        expression = "list(dict.fromkeys(values))" if case.id.startswith("dedupe") else "sum(v for v in values if v > 0)"
        steps += [{"tool": "write_file", "args": {"path": source, "content": f"def solve(values):\n    return {expression}\n"}},
                  {"tool": "verify", "args": {}}]
    else:
        if case.suite in {"career", "tenders"}:
            data = json.loads(case.files[source])
            for row in data.get("candidates", data.get("notices", [])):
                steps.append({"tool": case.suite, "args": {"id": row["id"]}})
        steps.append({"tool": "write_file", "args": {"path": "result.json", "content": json.dumps(case.expected)}})
    return [*steps, {"final": "Completed."}]


def evaluator(factory=None, **kwargs):
    return ExecutionEvaluator(snapshot(), seed="a123", repetitions=1,
                              provider_factory=factory or (lambda case, skills: ScriptedProvider(good_script(case))), **kwargs)


@pytest.fixture(autouse=True)
def no_background_jobs():
    runner = jobs._Runner()
    with patch.object(jobs, "_RUNNER", runner):
        jobs.configure(auto_thread=False)
        reset_strikes()
        yield
        jobs.configure(auto_thread=False)
        runner._queue.put(None)
        if runner._thread:
            runner._thread.join(timeout=2)
        reset_strikes()


def test_every_module_has_distinct_execution_coverage():
    training = tasks("train")
    assert {row.suite for row in training} == VALID_PRODUCT_MODULES
    assert task_fingerprint(training) != task_fingerprint(tasks("holdout"))
    report = evaluator().evaluate([])
    assert not report.failed, report.reason
    assert report.passed == report.total == 16, report.as_dict(with_outcomes=True)
    assert {row.id for row in report.suites} == VALID_PRODUCT_MODULES
    assert all(row["tools"] >= 2 for row in report.metrics)
    restored = ExamReport.from_dict(report.as_dict(with_outcomes=True))
    assert restored.outcomes == report.outcomes
    assert restored.metrics == report.metrics


def test_claiming_success_without_tools_does_not_pass():
    report = evaluator(lambda case, skills: ScriptedProvider([{"final": "Everything verified, 100 percent perfect."}])).evaluate([])
    assert not report.failed
    assert report.passed == 0


def test_final_revision_requires_new_verification():
    def factory(case, skills):
        script = good_script(case)[:-1]
        script.append({"tool": "write_file", "args": {"path": "solution.py", "content": "def solve(values):\n    return []\n"}})
        return ScriptedProvider([*script, {"final": "Already verified."}])
    report = evaluator(factory, modules=("code",)).evaluate([])
    assert not report.failed, report.reason
    assert report.passed == 0


def test_modified_input_or_outside_access_invalidates_artifact(tmp_path):
    secret = tmp_path / "unrelated.txt"
    secret.write_text("private data")
    for forbidden in [
        {"tool": "read_file", "args": {"path": str(secret)}},
        {"tool": "write_file", "args": {"path": "task.json", "content": "{}"}},
    ]:
        def factory(case, skills):
            return ScriptedProvider([*good_script(case)[:-1], forbidden, {"final": "Done."}])
        report = evaluator(factory, modules=("notes",)).evaluate([])
        assert not report.failed, report.reason
        assert report.passed == 0, report.as_dict(with_outcomes=True)
    assert secret.read_text() == "private data"


def test_isolated_code_cannot_read_host_files_or_fake_success(tmp_path):
    root = tmp_path / "task"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    (root / "solution.py").write_text(f"def solve(value):\n    return open({str(secret)!r}).read()\n")
    assert "execution_error" in run_solution(root, [1])
    (root / "solution.py").write_text("raise SystemExit(0)\n")
    assert "execution_error" in run_solution(root, [1])
    (root / "solution.py").write_text("import socket\ndef solve(value):\n    socket.create_connection(('1.1.1.1', 443), timeout=.1)\n    return True\n")
    assert "execution_error" in run_solution(root, [1])


class Author:
    def __init__(self, stage=1):
        self.stage = stage
        self.parents = []

    def draft(self, brief):
        return f"---\nname: {brief.name}\ndescription: Correct tool workflow\n---\n\nUse behavioral strategy {self.stage}.\n"

    def revise(self, markdown, feedback, attempt):
        self.parents.append(markdown)
        return markdown.replace("strategy 1", "strategy 2")


def stage_provider(case, skills):
    blob = "\n".join(skills)
    correct = "strategy 2" in blob or ("strategy 1" in blob and case.id.startswith("dedupe"))
    return ScriptedProvider(good_script(case) if correct else [{"final": "No repair."}])


def deps(factory=stage_provider, author=None):
    return PipelineDeps(author or Author(), lexical_model_factory, load_battery(), execution=evaluator(factory))


def test_two_recursive_generations_are_adopted_and_reused(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    brief = DraftBrief("repair-code", "Improve verified code repair", family="code", module="code")
    dependencies = deps()
    first = run_pipeline(tmp_path, brief, dependencies, max_attempts=1)
    assert first.status == "promoted", first.as_dict()
    first_text = project_skill_file(tmp_path, brief.name).read_text()
    assert read_draft(tmp_path, brief.name).generation == 1
    second = run_pipeline(tmp_path, brief, dependencies, max_attempts=1)
    assert second.status == "promoted", second.as_dict()
    record = read_draft(tmp_path, brief.name)
    assert record.generation == 2
    assert first_text in dependencies.author.parents
    assert record.previous_markdown == first_text
    assert record.execution_evidence["modules"] == ["code"]
    assert record.execution_evidence["holdout_passed"]
    assert applicable_skills(tmp_path, "continue", {"product_module": "code"}) == [brief.name]
    assert applicable_skills(tmp_path, "continue", {"product_module": "career"}) == []
    assert brief.name in excluded_skills(tmp_path, {"product_module": "career"})
    from navin.agent.context import ContextBuilder
    builder = ContextBuilder(tmp_path)
    for slim in (True, False):
        context = builder.build_system_prompt(current_message="continue", session_metadata={"product_module": "code"}, slim_skill_preload=slim)
        assert "Use behavioral strategy 2." in context
        unrelated = builder.build_system_prompt(current_message="continue", session_metadata={"product_module": "career"}, slim_skill_preload=slim)
        assert "Use behavioral strategy 2." not in unrelated


def test_training_gain_that_fails_holdout_is_not_promoted(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    train = {task_fingerprint([case]) for case in evaluator().cases()}
    def factory(case, skills):
        correct = bool(skills) and task_fingerprint([case]) in train
        return ScriptedProvider(good_script(case) if correct else [{"final": "No result."}])
    brief = DraftBrief("overfit", "Try repair", family="code", module="code")
    result = run_pipeline(tmp_path, brief, deps(factory), max_attempts=1)
    assert result.status == "rejected", result.as_dict()
    assert "held-out" in result.reason
    assert not project_skill_file(tmp_path, brief.name).exists()


def test_comparison_refuses_regressions_and_different_tasks():
    good = evaluator(modules=("code",)).evaluate([])
    weaker = replace(good, passed=1, outcomes=(replace(good.outcomes[0], passed=False), good.outcomes[1]))
    assert compare_execution(weaker, good).regressed
    assert not compare_execution(replace(good, battery_version="other"), good).eligible


def test_failed_infrastructure_retries_without_losing_pending_job(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    brief = DraftBrief("retry", "Repair code", family="code")
    assert jobs.enqueue_draft_job(tmp_path, brief)
    dependencies = deps()
    dependencies.execution = replace(dependencies.execution, snapshot_loader=lambda: replace(snapshot(), model="changed"))
    results = jobs.drain_jobs(tmp_path, dependencies)
    assert results[0]["status"] == "retry", results
    pending = jobs.pending_jobs(tmp_path)
    assert len(pending) == 1 and pending[0]["retries"] == 1
    assert not project_skill_file(tmp_path, brief.name).exists()


def test_queue_keeps_concurrent_append_and_worker_restarts(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    jobs.enqueue_draft_job(tmp_path, DraftBrief("first", "First"))
    def run(workspace, brief, dependencies):
        jobs.enqueue_draft_job(workspace, DraftBrief("second", "Second"))
        return PipelineResult(brief["name"], "flat")
    with patch("navin.skills_evolve.pipeline.run_pipeline", side_effect=run):
        jobs.drain_jobs(tmp_path)
    assert [job["brief"]["name"] for job in jobs.pending_jobs(tmp_path)] == ["second"]
    with patch("navin.skills_evolve.pipeline.run_pipeline", return_value=PipelineResult("second", "flat")) as run:
        jobs.configure(auto_thread=True)
        jobs.resume_jobs(tmp_path)
        assert jobs.wait_idle(10)
        assert run.call_count == 1
    assert jobs.pending_jobs(tmp_path) == []


@pytest.mark.parametrize("module", sorted(VALID_PRODUCT_MODULES))
def test_all_module_hooks_learn_and_survive_memory_reset(tmp_path, module):
    update_settings(tmp_path, {"enabled": True, "failure_threshold": 2})
    context = AgentTurnHookContext(workspace=tmp_path, metadata={"product_module": module})
    hook = create_skills_evolve_hook(context)
    failed = AgentRunHookContext(messages=[], stop_reason="completed", tool_events=[
        {"name": "read_file", "status": "error", "detail": "Missing source file"}])
    asyncio.run(hook.after_run(failed))
    assert jobs.pending_jobs(tmp_path) == []
    reset_strikes()
    asyncio.run(create_skills_evolve_hook(context).after_run(failed))
    pending = jobs.pending_jobs(tmp_path)
    assert len(pending) == 1
    assert pending[0]["brief"]["module"] == module
    assert module in pending[0]["brief"]["name"]


def test_redacted_recovery_is_remembered_without_false_recovery(tmp_path):
    update_settings(tmp_path, {"enabled": True, "failure_threshold": 3})
    from navin.cognition.settings import write_settings
    write_settings(tmp_path, enabled=True)
    context = AgentTurnHookContext(workspace=tmp_path, metadata={"product_module": "career"})
    hook = create_skills_evolve_hook(context)
    error = {"name": "career", "status": "error", "detail": "Timeout token=topsecret person@example.test"}
    run = AgentRunHookContext(messages=[], stop_reason="completed", tool_events=[error, {"name": "read_file", "status": "ok"}])
    asyncio.run(hook.after_run(run))
    assert not jobs.pending_jobs(tmp_path)
    run.tool_events.append({"name": "career", "status": "ok"})
    asyncio.run(hook.after_run(run))
    pending = jobs.pending_jobs(tmp_path)
    assert pending[0]["brief"]["kind"] == "observed_recovery"
    assert "topsecret" not in json.dumps(pending)
    assert "person@example.test" not in json.dumps(pending)
    experience = (tmp_path / ".navin/skills-draft/experience.json").read_text()
    assert "topsecret" not in experience
    from navin.cognition.episodes import flush_episodes, iter_episodes
    assert flush_episodes()
    memories = iter_episodes(tmp_path)
    assert any("observed_recovery" in row["reply"] for row in memories)
    assert "topsecret" not in json.dumps(memories)


def test_auto_guard_rolls_back_a_real_execution_regression(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    brief = DraftBrief("guarded", "Code correction", family="code", module="code")
    assert run_pipeline(tmp_path, brief, deps(), max_attempts=1).promoted
    def regressed(case, skills):
        return ScriptedProvider([{"final": "Wrong."}] if skills else good_script(case))
    result = execution_guard(tmp_path, deps(regressed))
    assert result["retired"] == [brief.name]
    assert not project_skill_file(tmp_path, brief.name).exists()


def test_candidate_and_baseline_tampering_fail_validation(tmp_path):
    update_settings(tmp_path, {"enabled": True, "promote_project": False})
    brief = DraftBrief("review", "Code correction", family="code", module="code")
    result = run_pipeline(tmp_path, brief, deps(), max_attempts=1)
    assert result.status == "eligible", result.as_dict()
    record = read_draft(tmp_path, brief.name)
    with pytest.raises(PromotionError):
        validate_promotion(tmp_path, record, "changed")
    from navin.skills_evolve.drafts import read_draft_markdown
    markdown = read_draft_markdown(tmp_path, brief.name)
    with pytest.raises(PromotionError, match="paused"):
        validate_promotion(tmp_path, record, markdown)
    update_settings(tmp_path, {"promote_project": True})
    validate_promotion(tmp_path, record, markdown)
    path = project_skill_file(tmp_path, "new-user-skill")
    path.parent.mkdir(parents=True)
    path.write_text("User edit")
    with pytest.raises(PromotionError, match="changed"):
        validate_promotion(tmp_path, record, markdown)


def test_state_reports_actual_coverage_and_migrates_old_text_evaluator(tmp_path):
    (tmp_path / ".navin").mkdir()
    (tmp_path / ".navin/skills-evolve.json").write_text('{"enabled":true,"exam_model":"lexical"}')
    state = agi_state(tmp_path)
    assert state["exam_model"] == "execution"
    assert {suite["id"] for suite in state["battery"]["suites"]} == VALID_PRODUCT_MODULES


def test_successful_but_repetitive_runs_propose_efficiency_improvements(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    hook = create_skills_evolve_hook(AgentTurnHookContext(workspace=tmp_path, metadata={"product_module": "crm"}))
    run = AgentRunHookContext(messages=[], stop_reason="completed", tool_events=[{"name": "read_file", "status": "ok"}] * 12)
    asyncio.run(hook.after_run(run))
    pending = jobs.pending_jobs(tmp_path)
    assert pending[0]["brief"]["kind"] == "observed_efficiency"
    assert pending[0]["brief"]["module"] == "crm"
    asyncio.run(hook.after_run(run))
    assert len(jobs.pending_jobs(tmp_path)) == 1


def test_resume_known_projects_after_restart(tmp_path):
    from navin.improvement.skills import resume_learning
    from navin.session.manager import SessionManager
    project = tmp_path / "project"
    project.mkdir()
    update_settings(project, {"enabled": True})
    jobs.enqueue_draft_job(project, DraftBrief("resume", "Resume saved work"))
    sessions = SessionManager(tmp_path)
    session = sessions.get_or_create("websocket:learning-test")
    session.metadata["workspace_scope"] = {"project_path": str(project)}
    sessions.save(session)
    with patch.object(jobs._RUNNER, "kick") as kick:
        resume_learning(tmp_path, sessions)
    kick.assert_called_once_with(project)


def test_pausing_during_comparison_keeps_the_active_skill(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    brief = DraftBrief("pause", "Repair", family="code", module="code")
    def factory(case, skills):
        if skills:
            update_settings(tmp_path, {"enabled": False})
        return ScriptedProvider(good_script(case) if skills else [{"final": "No repair."}])
    result = run_pipeline(tmp_path, brief, deps(factory), max_attempts=1)
    assert not result.promoted
    assert not project_skill_file(tmp_path, brief.name).exists()


def test_broken_experience_file_does_not_break_the_user_turn(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    folder = tmp_path / ".navin/skills-draft"
    folder.mkdir()
    (folder / "experience.json").write_text('{"bad":{"strikes":["invalid"],"proposed_at":"invalid"}}')
    hook = create_skills_evolve_hook(AgentTurnHookContext(workspace=tmp_path))
    asyncio.run(hook.after_run(AgentRunHookContext(messages=[], tool_events=[{"name": "read_file", "status": "error", "detail": "Missing"}])))
    assert json.loads((folder / "experience.json").read_text())


def test_guard_preserves_edits_made_during_its_comparison(tmp_path):
    update_settings(tmp_path, {"enabled": True})
    brief = DraftBrief("edited", "Code correction", family="code", module="code")
    assert run_pipeline(tmp_path, brief, deps(), max_attempts=1).promoted
    path = project_skill_file(tmp_path, brief.name)
    def concurrent_edit(case, skills):
        path.write_text("User's newer version")
        return ScriptedProvider([{"final": "Wrong."}] if skills else good_script(case))
    result = execution_guard(tmp_path, deps(concurrent_edit))
    assert result["retired"] == []
    assert path.read_text() == "User's newer version"
