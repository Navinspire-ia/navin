# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise real prompt construction with local playbooks and fake providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.agent.context import ContextBuilder
from navin.agent.skills import SkillsLoader
from navin.agent.tools.context import RequestContext, request_context


def _write_skill(root: Path, name: str, body: str, *, requires_env: str = "") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {"navin": {"requires": {"env": [requires_env]}}} if requires_env else {}
    path.write_text(
        f"---\nname: {name}\ndescription: Local test playbook\nmetadata: {json.dumps(metadata)}\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def skill_workspace(tmp_path, monkeypatch):
    from navin.agent import skills
    from navin.config import loader

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    workspace = tmp_path / "configured"
    workspace.mkdir()
    names = (
        "tender-agent", "rfp-writer", "career-agent", "cv-tailoring", "ats-analyzer",
        "montage-studio", "translation-localization", "meeting-studio", "meeting-followup",
        "fact-checker", "studio-expert-contract", "marketing-strategist", "email-marketing",
        "copywriting-agent", "brand-voice-manager", "social-media-manager", "blog-writer",
        "seo-content-writer", "market-research", "customer-persona-builder",
        "growth-marketing", "competitor-intelligence", "go-to-market-planner",
        "proofreader",
    )
    for name in names:
        _write_skill(builtin, name, f"BUILTIN BODY {name}.")
    monkeypatch.setattr(skills, "BUILTIN_SKILLS_DIR", builtin)
    monkeypatch.setattr(skills, "_home_skill_dirs", lambda: [])
    monkeypatch.setattr(SkillsLoader, "_plugin_skill_dirs", staticmethod(lambda: []))
    config = SimpleNamespace(
        workspace_path=workspace,
        agents=SimpleNamespace(defaults=SimpleNamespace(disabled_skills=[])),
    )
    monkeypatch.setattr(loader, "load_config", lambda: config)
    return workspace, config


@pytest.mark.parametrize(
    ("module", "action", "specialist", "primary", "other"),
    [
        ("tenders", "write", "rfp-writer", "tender-agent", "career-agent"),
        ("career", "prepare", "cv-tailoring", "career-agent", "tender-agent"),
        ("montage", "translate", "translation-localization", "montage-studio", "marketing-strategist"),
        ("marketing", "email", "email-marketing", "marketing-strategist", "montage-studio"),
        ("meeting", "report", "meeting-followup", "meeting-studio", "tender-agent"),
    ],
)
def test_agent_messages_use_full_project_action_playbooks(
    skill_workspace, tmp_path, module, action, specialist, primary, other,
):
    configured, _config = skill_workspace
    project = tmp_path / "linked-project"
    body = "Follow every documented step.\n" * 400 + f"FINAL SAFEGUARD {specialist}"
    path = _write_skill(project / ".navin" / "skills", specialist, body)
    messages = ContextBuilder(configured).build_messages(
        history=[], current_message="Execute this action.",
        session_metadata={"product_module": module, "skill_action": action},
        workspace=project, include_memory_recent_history=False, slim_skill_preload=True,
    )
    system = messages[0]["content"]
    assert body in system
    assert system.count(f"FINAL SAFEGUARD {specialist}") == 1
    assert f"BUILTIN BODY {primary}." in system
    assert f"BUILTIN BODY {other}." not in system
    assert f"BUILTIN BODY {specialist}." not in system
    metadata = messages[0]["_meta"]["skill_context"]
    assert metadata["workspace"] == str(project)
    assert specialist in metadata["loaded"]
    assert {"name": specialist, "path": str(path), "source": "workspace"} in metadata["sources"]


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"product_module": "montage"}, "Traduis les sous-titres."),
        ({"product_module": "marketing", "composer_mode": "montage"}, "Translate the subtitles."),
        ({}, "/montage translate the captions"),
    ],
)
def test_montage_legacy_scope_keeps_its_translation_body(skill_workspace, metadata, message):
    workspace, _config = skill_workspace
    messages = ContextBuilder(workspace).build_messages(
        history=[], current_message=message, session_metadata=metadata,
        include_memory_recent_history=False,
    )
    system = messages[0]["content"]
    assert "BUILTIN BODY montage-studio." in system
    assert "BUILTIN BODY translation-localization." in system
    assert "BUILTIN BODY marketing-strategist." not in system
    assert messages[0]["_meta"]["skill_context"]["action"] == "localize"


def test_current_request_intent_wins_over_the_previous_desk(skill_workspace):
    workspace, _config = skill_workspace
    context = RequestContext(
        channel="webui", chat_id="test", workspace=workspace,
        original_user_text="Analyse mon CV pour ATS.", metadata={"product_module": "career"},
    )
    with request_context(context):
        messages = ContextBuilder(workspace, disabled_skills=["cv-tailoring"]).build_messages(
            history=[], current_message="An expanded workflow mentioning mail and launch.",
            session_metadata={
                "product_module": "marketing", "skill_action": "copy",
                "original_content": "Write marketing posts.",
            },
            include_memory_recent_history=False,
        )
    assert "BUILTIN BODY ats-analyzer." in messages[0]["content"]
    assert "BUILTIN BODY cv-tailoring." not in messages[0]["content"]
    metadata = messages[0]["_meta"]["skill_context"]
    assert metadata["action"] == "analyze-ats"
    assert next(row for row in metadata["unavailable"] if row["name"] == "cv-tailoring")["status"] == "disabled"


def _capture_marketing_provider(monkeypatch, response):
    from navin import desk_ai
    from navin.providers import factory

    calls = []

    async def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(content=response)

    snapshot = SimpleNamespace(provider=SimpleNamespace(chat_with_retry=chat), model="fake-model")
    monkeypatch.setattr(factory, "load_provider_snapshot", lambda **kwargs: snapshot)
    monkeypatch.setattr(desk_ai, "route_presets", lambda role: ["test-preset"])
    monkeypatch.setattr(desk_ai, "preset_model", lambda preset: "fake-model")
    monkeypatch.setenv("NAVIN_MARKETING_AI", "1")
    return calls


@pytest.mark.parametrize(
    ("function", "specialist", "response"),
    [
        ("product", "market-research", '{"category":"project tracker"}'),
        ("positioning", "customer-persona-builder", '{"statement":"Acme tracks projects."}'),
        ("posts", "email-marketing", '[{"body":"Acme tracks projects."}]'),
        ("channels", "social-media-manager", '[{"channel":"linkedin","body":"Acme tracks projects."}]'),
        ("variants", "growth-marketing", '[{"body":"Acme tracks projects."}]'),
        ("research", "competitor-intelligence", '{"competitors":[],"trends":[],"keywords":[]}'),
        ("launch", "go-to-market-planner", "Acme helps teams track their projects."),
        ("kit", "go-to-market-planner", '{"landing":"Acme helps teams track their projects."}'),
    ],
)
def test_every_marketing_text_call_sends_specialist_bodies(
    skill_workspace, monkeypatch, function, specialist, response,
):
    from navin.marketing import ai

    workspace, _config = skill_workspace
    body = f"PROJECT WORKFLOW {specialist}\n" + "Keep the supplied product facts.\n" * 300 + "END WORKFLOW"
    _write_skill(workspace / ".navin" / "skills", specialist, body)
    calls = _capture_marketing_provider(monkeypatch, response)
    product = {"name": "Acme"}
    brand = {"languages": ["fr"]}
    invocations = {
        "product": lambda: ai.write_product_brief(product, brand),
        "positioning": lambda: ai.write_positioning(product, brand),
        "posts": lambda: ai.write_posts("email", product, brand, {}),
        "channels": lambda: ai.write_channel_set(["linkedin"], product, brand, {}),
        "variants": lambda: ai.write_variants({"body": "Acme tracks projects."}, product, brand, {}),
        "research": lambda: ai.extract_research(product, brand, [{"title": "Observed result"}]),
        "launch": lambda: ai.write_launch_asset("landing", "Landing page", product, brand, {}),
        "kit": lambda: ai.write_launch_kit([("landing", "Landing page")], product, brand, {}),
    }
    assert invocations[function]()
    assert len(calls) == 1
    messages, kwargs = calls[0]
    assert body in messages[0]["content"]
    assert "# Current action contract" in messages[0]["content"]
    assert "Acme" in messages[1]["content"]
    assert kwargs["model"] == "fake-model"


def test_marketing_worker_uses_bound_project_and_reports_unloaded_skills(
    skill_workspace, tmp_path, monkeypatch,
):
    from navin.marketing import ai

    configured, config = skill_workspace
    config.agents.defaults.disabled_skills = ["brand-voice-manager"]
    linked = tmp_path / "session-project"
    foreign = tmp_path / "other-product"
    _write_skill(foreign / ".navin" / "skills", "seo-content-writer", "FOREIGN PROJECT RULE")
    _write_skill(linked / ".navin" / "skills", "seo-content-writer", "LINKED SEO WORKFLOW")
    _write_skill(
        linked / ".navin" / "skills", "blog-writer", "UNAVAILABLE BLOG RULE",
        requires_env="NAVIN_TEST_SKILL_MISSING_REQUIREMENT",
    )
    monkeypatch.delenv("NAVIN_TEST_SKILL_MISSING_REQUIREMENT", raising=False)
    calls = _capture_marketing_provider(
        monkeypatch, '[{"channel":"blog","body":"Acme tracks projects."}]',
    )

    async def run():
        with request_context(RequestContext(
            channel="webui", chat_id="bound", workspace=linked,
            metadata={"disabled_skills": ["email-marketing"]},
        )):
            return await asyncio.to_thread(
                ai.write_channel_set, ["linkedin", "blog", "email"],
                {"name": "Acme", "workspace": str(foreign)}, {}, {},
            )

    result = asyncio.run(run())
    assert configured != linked
    system = calls[0][0][0]["content"]
    assert "LINKED SEO WORKFLOW" in system
    for excluded in (
        "FOREIGN PROJECT RULE", "UNAVAILABLE BLOG RULE",
        "BUILTIN BODY email-marketing.", "BUILTIN BODY brand-voice-manager.",
    ):
        assert excluded not in system
    metadata = result["blog"]["skill_context"]
    assert metadata["workspace"] == str(linked)
    reasons = {row["name"]: row["status"] for row in metadata["unavailable"]}
    assert reasons["brand-voice-manager"] == "disabled"
    assert reasons["email-marketing"] == "disabled"
    assert reasons["blog-writer"] == "unavailable"
    assert reasons["email-writer"] == "missing"
    assert set(reasons).isdisjoint(metadata["loaded"])


@pytest.mark.parametrize("action", ["report", "speakers", "answer", "translate", "cleanup"])
def test_meeting_provider_gets_project_playbooks_and_the_action_contract(
    skill_workspace, tmp_path, monkeypatch, action,
):
    from navin.meetings import services
    from navin.webui import meeting_api

    _configured, config = skill_workspace
    config.agents.defaults.disabled_skills = ["fact-checker"]
    linked = tmp_path / "meeting-project"
    body = "PROJECT MEETING WORKFLOW\n" + "Only name speakers supported by the transcript.\n" * 300 + "FINAL EVIDENCE RULE"
    _write_skill(linked / ".navin" / "skills", "meeting-studio", body)
    calls = []

    async def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return SimpleNamespace(content="Ana: We will ship on Friday.")

    snapshot = SimpleNamespace(provider=SimpleNamespace(chat_with_retry=chat), model="meeting-test")
    monkeypatch.setattr(meeting_api, "_load_snapshot", lambda preset: snapshot)
    monkeypatch.setattr(meeting_api, "_meeting_preset", lambda: "test-docs")

    async def run():
        with request_context(RequestContext(channel="webui", chat_id="meeting", workspace=linked)):
            if action == "translate":
                return await services.translate_payload(text="Ana: We will ship on Friday.", target_language="fr")
            if action == "cleanup":
                return await services.cleanup_payload(transcript="Ana: We will ship on Friday.")
            if action == "answer":
                return await meeting_api.answer_payload(question="When?", transcript="Ana: We will ship on Friday.")
            if action == "speakers":
                return await meeting_api.speakers_payload(transcript="Ana: We will ship on Friday.")
            return await meeting_api.report_payload(transcript="Ana: We will ship on Friday.")

    result = asyncio.run(run())
    assert result["skill_context"]["workspace"] == str(linked)
    assert result["skill_context"]["action"] == action
    assert len(calls) == 1
    system = calls[0][0][0]["content"]
    assert body in system
    assert "BUILTIN BODY fact-checker." not in system
    if action == "translate":
        assert "BUILTIN BODY translation-localization." in system
        assert "Output only the translation." in system
    if action == "cleanup":
        assert "BUILTIN BODY proofreader." in system
        assert "Do not summarize, translate or claim acoustic speaker identification." in system
    assert system.index("# Current action contract") > system.index("FINAL EVIDENCE RULE")
    assert "Ana: We will ship on Friday." in calls[0][0][1]["content"]


def test_marketing_visual_qa_routes_instructions_without_changing_image_inputs(
    skill_workspace, tmp_path, monkeypatch,
):
    from PIL import Image, ImageDraw

    from navin.agent.tools.visual_qa import VisualQAToolConfig
    from navin.providers import factory
    from navin.providers.base import LLMResponse
    from navin.providers.image_generation import image_path_to_data_url
    from navin.providers.visual_qa import ChatVisualQAProvider
    from navin.utils.llm_runtime import runtime_from_provider_snapshot
    from navin.webui.marketing_api import run_marketing_qa

    _configured, config = skill_workspace
    config.agents.defaults.disabled_skills = ["fact-checker"]
    config.tools = SimpleNamespace(visual_qa=VisualQAToolConfig(min_sharpness=1, min_contrast=1))
    project = tmp_path / "visual-project"
    critic = "PROJECT VISUAL REVIEW\n" + "Require concrete visible evidence.\n" * 300 + "FINAL VISUAL SAFEGUARD"
    _write_skill(project / ".navin" / "skills", "critic-reviewer", critic)
    _write_skill(project / ".navin" / "skills", "product-visuals", "COMPARE ONLY SUPPLIED IMAGE REFERENCES")
    candidate = project / "candidate.png"
    image = Image.new("RGB", (128, 128), "white")
    ImageDraw.Draw(image).rectangle((24, 24, 104, 104), fill="blue")
    image.save(candidate)
    analysis = {"dimensions": {
        name: {"status": "PASS", "score": 95, "evidence": ["Blue central rectangle matches supplied reference."]}
        for name in ("product_fidelity", "logo_fidelity", "text_accuracy", "color_fidelity", "composition")
    }}
    calls = []

    async def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return LLMResponse(content=json.dumps(analysis))

    snapshot = SimpleNamespace(
        provider=SimpleNamespace(chat_with_retry=chat), model="visual-test", generation=None,
        context_window_tokens=128000, signature=("visual-test",),
    )
    monkeypatch.setattr(factory, "load_provider_snapshot", lambda **kwargs: snapshot)

    async def run():
        with request_context(RequestContext(channel="webui", chat_id="qa", workspace=project)):
            report = await run_marketing_qa(
                project, candidate="candidate.png", references=["candidate.png"],
                requirements={"aspect_ratio": "1:1"},
            )
        # Calling the shared adapter directly keeps its original prompt contract.
        generic = ChatVisualQAProvider(runtime=runtime_from_provider_snapshot(snapshot))
        await generic.analyze(candidate=candidate, references=[], claims=[], context={"scope": "another consumer"})
        return report

    report = asyncio.run(run())
    assert report["skill_context"]["action"] == "visual-qa"
    assert report["skill_context"]["workspace"] == str(project)
    assert report["requirements"] == {"aspect_ratio": "1:1"}
    assert "fact-checker" not in report["skill_context"]["loaded"]
    messages = calls[0][0]
    assert critic in messages[0]["content"]
    assert "COMPARE ONLY SUPPLIED IMAGE REFERENCES" in messages[0]["content"]
    assert "Return JSON only with this exact shape" in messages[0]["content"]
    images = [part["image_url"]["url"] for part in messages[1]["content"] if part["type"] == "image_url"]
    assert images == [image_path_to_data_url(candidate)] * 2
    assert [message["role"] for message in calls[1][0]] == ["user"]
    assert "PROJECT VISUAL REVIEW" not in str(calls[1][0])
