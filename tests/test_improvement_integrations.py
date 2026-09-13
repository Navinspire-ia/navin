# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from navin.agent.code_validation import CodeValidationState
from navin.agent.runner import AgentRunner
from navin.agent.tools.improvement import ImprovementTool
from navin.agent.tools.quality import VerifyTool
from navin.career.prospecting import handle_prospecting, prospecting_snapshot
from navin.career.store import CareerStore
from navin.cli.improvement import create_improvement_app
from navin.evals.agent_loop import AgentLoopCase, ScriptedProvider, _spec_for_case
from navin.improvement.code import begin, experiment_context, finish
from navin.improvement.engine import BLOCK_SIZE, MIN_BLOCKS, WARMUP, ImprovementEngine, Observation
from navin.improvement.search import career_quality, search_criteria, tender_brief
from navin.quality.evidence import VerificationEvidence
from navin.tenders.brief import build_brief


def seed_proposal(engine, context):
    for _ in range(WARMUP):
        engine.observe(engine.choose(context), Observation(.1, False))


class CapturingProvider(ScriptedProvider):
    def __init__(self, script):
        super().__init__(script)
        self.prompts = []

    async def chat_with_retry(self, **kwargs):
        self.prompts.append(str(kwargs.get("messages", [])))
        return await super().chat_with_retry(**kwargs)


@pytest.mark.asyncio
async def test_real_code_runner_applies_strategy_and_records_only_verified_work(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    case = AgentLoopCase(id="improving-code", prompt="Fix addition in calc.py", script=(
        {"tool": "read_file", "args": {"path": "calc.py"}},
        {"tool": "edit_file", "args": {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}},
        {"tool": "verify", "args": {"action": "check", "paths": ["calc.py"], "test_target": "test_calc.py"}},
        {"final": "Fixed and verified."},
    ))
    spec = _spec_for_case(case, tmp_path)
    spec.workspace = tmp_path
    spec.validate_code_changes = spec.requires_verify_before_done = True
    spec.verify_fail_nudge_limit = 1
    spec.tools.register(VerifyTool(workspace=tmp_path))
    provider = CapturingProvider(case.script)
    spec.runtime = SimpleNamespace(provider=provider, model=spec.runtime.model, generation=spec.runtime.generation,
                                   context_window_tokens=spec.runtime.context_window_tokens)
    engine = ImprovementEngine(tmp_path / ".navin" / "improvement", "code")
    monkeypatch.setattr("navin.improvement.engine.secrets.randbelow", lambda _: 0)
    seed_proposal(engine, experiment_context(spec))
    result = await AgentRunner().run(spec)
    assert result.stop_reason == "completed", result.final_content
    assert spec.loop_guard.validation.revision > 0 and not spec.loop_guard.validation.pending
    assert any("Execution strategy from locally evaluated outcomes" in prompt for prompt in provider.prompts)
    assert not any("Execution strategy from locally evaluated outcomes" in str(message) for message in result.messages)
    state = engine._read()
    current = next(iter(state["contexts"].values()))
    observation = next(iter(current["blocks"].values()))["candidate"][0]
    assert observation["success"] and observation["score"] > .8
    assert not state["pending"]
    assert "a + b" in (tmp_path / "calc.py").read_text()


@pytest.mark.asyncio
async def test_code_cancellation_unverified_claims_and_read_only_are_not_successes(tmp_path):
    spec = _spec_for_case(AgentLoopCase(id="cancel", prompt="Fix it"), tmp_path)
    spec.workspace = tmp_path
    spec.validate_code_changes = True
    spec.loop_guard = SimpleNamespace(validation=CodeValidationState())
    experiment = await begin(spec, [])
    assert experiment is not None
    await finish(experiment, spec)
    assert next(iter(experiment.engine.status()["contexts"].values()))["observations"] == 0
    experiment = await begin(spec, [])
    spec.loop_guard.validation.edited({"calc.py"}, require_tests=True)
    result = SimpleNamespace(stop_reason="completed", had_injections=False, tool_events=[], usage={}, final_content="Everything is perfect.")
    await finish(experiment, spec, result)
    state = experiment.engine._read()
    assert next(iter(state["contexts"].values()))["warmup"][-1]["success"] is False
    spec.read_only_tools = True
    assert await begin(spec, []) is None
    spec.read_only_tools, spec.plan_read_only = False, True
    assert await begin(spec, []) is None


@pytest.mark.asyncio
async def test_old_test_evidence_cannot_reward_new_edits(tmp_path):
    spec = _spec_for_case(AgentLoopCase(id="stale", prompt="Fix it"), tmp_path)
    spec.workspace, spec.validate_code_changes = tmp_path, True
    validation = CodeValidationState()
    spec.loop_guard = SimpleNamespace(validation=validation)
    experiment = await begin(spec, [])
    validation.edited({"calc.py"}, require_tests=True)
    validation.record(VerificationEvidence(checks_ok=True, tests_ok=True))
    validation.edited({"calc.py"}, require_tests=True)
    await finish(experiment, spec, SimpleNamespace(stop_reason="completed", had_injections=False, tool_events=[], usage={}))
    assert next(iter(experiment.engine._read()["contexts"].values()))["warmup"][-1]["score"] == 0


@pytest.mark.asyncio
async def test_code_rejects_boundary_violations_and_discards_changed_execution_context(tmp_path, monkeypatch):
    spec = _spec_for_case(AgentLoopCase(id="boundaries", prompt="Fix it"), tmp_path)
    spec.workspace, spec.validate_code_changes = tmp_path, True
    spec.loop_guard = SimpleNamespace(validation=CodeValidationState())
    engine = ImprovementEngine(tmp_path / ".navin" / "improvement", "code")
    monkeypatch.setattr("navin.improvement.engine.secrets.randbelow", lambda _: 0)
    seed_proposal(engine, experiment_context(spec))
    experiment = await begin(spec, [])
    validation = spec.loop_guard.validation
    validation.edited({"calc.py"}, require_tests=True)
    validation.record(VerificationEvidence(checks_ok=True, tests_ok=True))
    result = SimpleNamespace(stop_reason="completed", had_injections=False, usage={}, tool_events=[
        {"name": "exec", "status": "error", "detail": "workspace_violation: outside allowed directory"}])
    await finish(experiment, spec, result)
    assert engine.status()["history"][-1]["comparison"]["reason"] == "safety_failure"
    before = next(iter(engine.status()["contexts"].values()))["observations"]
    experiment = await begin(spec, [])
    validation.edited({"calc.py"}, require_tests=True)
    validation.record(VerificationEvidence(checks_ok=True, tests_ok=True))
    spec.composer_mode = "review"
    await finish(experiment, spec, result)
    assert next(iter(engine.status()["contexts"].values()))["observations"] == before
    assert not engine._read()["pending"]


def test_career_uses_live_search_outcomes_to_promote_without_changing_priorities(tmp_path):
    store = CareerStore(tmp_path / "career")
    role = "Generative AI engineer"
    criteria = {"roles": [role], "skills": ["Python", "RAG", "Atlas"], "role_skills": {role: ["Python", "RAG"]},
                "role_priorities": {role: 100}, "sources": ["public_jobs"], "countries": ["FR"], "mode": "missions", "track": "both"}
    handle_prospecting(store, "prospecting_config", {"criteria": criteria})
    seen = []
    def source(_store, _source, query):
        seen.append(query)
        if query["skills"] != ["Atlas"]:
            return []
        return [{"id": f"offer-{i}", "title": role, "description": "Python RAG Atlas", "country": "FR",
                 "remote": "remote", "posted_at": datetime.now(timezone.utc).date().isoformat(),
                 "url": f"https://example.com/jobs/{i}", "source": "public_jobs"} for i in range(5)]
    with patch("navin.career.prospecting._mission_source", side_effect=source):
        for _ in range(WARMUP + MIN_BLOCKS * BLOCK_SIZE):
            handle_prospecting(store, "prospecting_search", {})
    engine = ImprovementEngine(store.root / "improvement", "career")
    state = next(iter(engine.status()["contexts"].values()))
    assert state["generation"] == 1 and state["champion"] == {"query": "role_only"}
    assert len(store.load_opportunities()) == 5
    assert prospecting_snapshot(store)["criteria"]["role_priorities"] == {role: 100}
    assert all(query["countries"] == ["FR"] and "Atlas" in query["skills"] for query in seen)


def test_learned_searches_keep_explicit_skills_markets_and_the_tender_constraints():
    criteria = {"roles": ["AI engineer"], "skills": ["RAG", "Client-specific requirement"],
                "role_skills": {"AI engineer": ["RAG"]}, "countries": ["SA", "MA"], "signal_only": True, "buy_rate_max": 500}
    scoped = search_criteria(criteria, {"query": "role_only"})
    assert scoped["skills"] == ["Client-specific requirement"]
    assert scoped["countries"] == criteria["countries"] and scoped["signal_only"] and scoped["buy_rate_max"] == 500
    assert criteria["skills"] == ["RAG", "Client-specific requirement"]
    brief = build_brief(countries=["FR", "MA"], crafts=["cloud", "data"], days=14)
    for terms in ["configured", "specific_first", "bilingual_first"]:
        changed = tender_brief(brief, {"terms": terms})
        assert set(changed.terms) == set(brief.terms)
        assert changed.cpv == brief.cpv and changed.countries == brief.countries and changed.days == brief.days


def test_career_quality_requires_original_skills_and_respects_candidate_budget():
    criteria = {"roles": ["Generative AI engineer"], "skills": ["RAG", "Python"], "countries": ["FR"],
                "track": "both", "profile_countries": [], "profile_city": "", "city": "", "currency": "EUR", "buy_rate_max": 500}
    mission = {"id": "one", "title": "Generative AI engineer", "country": "FR", "description": "RAG Python platform"}
    assert career_quality([mission], criteria, "missions")[1]
    assert career_quality([{**mission, "description": "Unrelated duties"}], criteria, "missions") == (0, False)
    candidate = {"id": "person", "headline": "Generative AI engineer", "skills": ["RAG", "Python"],
                 "country": "FR", "currency": "EUR", "daily_rate": 600}
    assert career_quality([candidate], criteria, "profiles") == (0, False)
    assert career_quality([{**candidate, "daily_rate": 450}], criteria, "profiles")[1]


def test_tenders_real_collection_records_scored_notices_and_deduplicates_reward(tmp_path):
    from navin.improvement import tenders as improvement
    from navin.tenders.desk import run_collect
    from navin.tenders.store import TenderStore

    store = TenderStore(tmp_path / "tenders")
    store.save_profile({"company": "Test", "countries": ["FR"], "crafts": ["data"], "send_mode": "approval"})
    notice = {"id": "data-project", "title": "Data engineering platform", "description": "Data platform and ETL services",
              "country": "FR", "source_id": "boamp", "source_url": "https://example.gov/notice", "deadline": "2099-12-31", "budget": 100000}
    with patch("navin.tenders.desk.collect", return_value={"tenders": [notice], "reports": [{"kind": "fetch", "count": 1, "ok": True}]}), \
         patch("navin.tenders.notify.deliver_alert", return_value={}):
        run_collect(store)
    engine = ImprovementEngine(store.root / "improvement", "tenders")
    state = next(iter(engine._read()["contexts"].values()))
    assert state["observations"] == 1
    stored = store.load_tenders()[0]
    assert state["warmup"][0]["success"] == (bool(stored["go"]) and stored["score"] >= 60)
    assert state["warmup"][0]["score"] <= .2
    # A repeated row or a catalogue-only report cannot manufacture more evidence.
    profile = store.load_profile()
    experiment, _ = improvement.begin(store, profile, build_brief(countries=["FR"], crafts=["data"]))
    improvement.finish(experiment, [{"id": "one", "go": True, "score": 100}] * 20, [{"kind": "fetch"}])
    assert next(iter(engine._read()["contexts"].values()))["warmup"][-1]["score"] == .2
    experiment, _ = improvement.begin(store, profile, build_brief(countries=["FR"], crafts=["data"]))
    improvement.finish(experiment, [], [{"kind": "catalog"}])
    assert next(iter(engine._read()["contexts"].values()))["observations"] == 2


def test_cli_and_agent_inspect_control_the_same_state_without_score_submission(tmp_path):
    app = create_improvement_app(console=Console(no_color=True))
    runner = CliRunner()
    result = runner.invoke(app, ["status", "--module", "code", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["code"]["contexts"] == {}
    assert not (tmp_path / ".navin").exists()
    result = runner.invoke(app, ["pause", "--module", "code", "--project", str(tmp_path)])
    assert result.exit_code == 0, result.output
    tool = ImprovementTool(tmp_path)
    assert tool.read_only and "score" not in tool.parameters.get("properties", {})
    status = json.loads(asyncio.run(tool.execute(module="code")))
    assert status["code"]["enabled"] is False
    assert runner.invoke(app, ["enable", "--module", "code", "--project", str(tmp_path)]).exit_code == 0
    assert json.loads(asyncio.run(tool.execute(module="code")))["code"]["enabled"] is True


def test_invalid_learning_state_preserves_status_and_the_original_search(tmp_path):
    from navin.improvement.control import control
    from navin.improvement.search import career_search

    root = tmp_path / ".navin" / "improvement"
    root.mkdir(parents=True)
    (root / "code.json").write_text("[]")
    status = control(tmp_path, "code")["code"]
    assert status["available"] is False and "Original strategy" in status["error"]
    store = CareerStore(tmp_path / "career")
    engine = ImprovementEngine(store.root / "improvement", "career")
    engine.root.mkdir()
    engine.path.write_text('{"version": 999}')
    configured = {"roles": ["Data engineer"], "skills": ["Python"], "countries": ["FR"]}
    seen = []
    def source(_store, _source, criteria):
        seen.append(criteria)
        return [{"title": "Data engineer"}]
    assert career_search(store, "missions", "public_jobs", configured, configured, source)
    assert seen == [configured]
