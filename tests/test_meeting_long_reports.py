# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Long meeting input must be covered or explicitly reported as incomplete."""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from navin.providers.base import LLMResponse
from navin.webui import meeting_api

DECISION = "[65:00] Ana: Decision: le lancement est fixe au 19 octobre; Malik confirme avant vendredi."


@pytest.fixture
def source():
    context = "[00:00] Ana: Follow the agenda.\n" * 2200
    return context + DECISION + "\n" + context


@pytest.fixture
def model(monkeypatch, tmp_path):
    from navin.agent import skills
    from navin.config import loader

    path = tmp_path / ".navin/skills/meeting-studio/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: meeting-studio\n---\nPROJECT MEETING EVIDENCE RULE\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_home_skill_dirs", lambda: [])
    monkeypatch.setattr(skills.SkillsLoader, "_plugin_skill_dirs", staticmethod(lambda: []))
    monkeypatch.setattr(skills, "BUILTIN_SKILLS_DIR", tmp_path / "no-builtins")
    config = SimpleNamespace(workspace_path=tmp_path, agents=SimpleNamespace(defaults=SimpleNamespace(disabled_skills=[])))
    monkeypatch.setattr(loader, "load_config", lambda: config)
    monkeypatch.setattr(meeting_api, "_meeting_preset", lambda: "test-docs")

    def install(*, fail_middle=False, stall_middle=False, fail_all=False, invalid_middle=False, fail_synthesis=False, finish_reason="stop"):
        calls = []

        async def chat(messages, **kwargs):
            user = messages[1]["content"]
            calls.append({"system": messages[0]["content"], "user": user, "kwargs": kwargs})
            if "<transcript_segment>" in user:
                segment = user.split("<transcript_segment>\n", 1)[1].rsplit("\n</transcript_segment>", 1)[0]
                if fail_all:
                    raise RuntimeError("all segment provider calls failed")
                if DECISION in segment and stall_middle:
                    await asyncio.Future()
                if DECISION in segment and fail_middle:
                    raise RuntimeError("middle segment provider failure")
                if DECISION in segment and invalid_middle:
                    items = [{"kind": "decision", "quote": "An invented decision outside the source."}]
                elif DECISION in segment:
                    items = [{"kind": "decision", "quote": DECISION}]
                else:
                    items = [{"kind": "topic", "quote": "Ana: Follow the agenda."}]
                return LLMResponse(content=json.dumps({"complete": True, "items": items}), finish_reason=finish_reason)
            if fail_synthesis:
                raise RuntimeError("final synthesis provider failure")
            content = "## Decisions\n" + DECISION if DECISION in user else "## Summary\nAvailable discussion evidence."
            return LLMResponse(content=content, finish_reason=finish_reason)

        snapshot = SimpleNamespace(provider=SimpleNamespace(chat_with_retry=chat), model="test-docs")
        monkeypatch.setattr(meeting_api, "_load_snapshot", lambda preset: snapshot)
        return calls

    return install


def test_middle_decision_reaches_segment_analysis_and_final_report(source, model):
    calls = model()
    # This decision was in the exact middle region discarded by the old clip.
    assert DECISION not in meeting_api._clip(source, meeting_api._MAX_TRANSCRIPT_CHARS)
    result = asyncio.run(meeting_api.report_payload(transcript=source, language="fr"))
    segment_calls = [call for call in calls if "<transcript_segment>" in call["user"]]
    segment_calls.sort(key=lambda call: int(re.search(r"Segment (\d+)/", call["user"])[1]))
    pieces = [call["user"].split("<transcript_segment>\n", 1)[1].rsplit("\n</transcript_segment>", 1)[0] for call in segment_calls]
    assert "".join(pieces) == source.strip()
    assert all(len(piece) <= meeting_api._REPORT_SEGMENT_CHARS for piece in pieces)
    assert all("PROJECT MEETING EVIDENCE RULE" in call["system"] for call in calls)
    assert DECISION in calls[-1]["user"]
    assert DECISION in result["markdown"]
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["processed_chars"] == len(source.strip())
    assert result["coverage"]["processed_segments"] == len(pieces)
    assert "transcript truncated" not in calls[-1]["user"]


def test_failed_middle_segment_is_visible_in_markdown_and_coverage(source, model):
    model(fail_middle=True)
    result = asyncio.run(meeting_api.report_payload(transcript=source, language="fr"))
    coverage = result["coverage"]
    assert coverage["complete"] is False
    assert coverage["processed_chars"] < coverage["input_chars"]
    failed = [segment for segment in coverage["segments"] if segment["status"] == "failed"]
    assert len(failed) == 1
    assert result["markdown"].startswith("> **Compte rendu partiel:")
    assert str(failed[0]["index"]) in result["markdown"]
    assert "middle segment provider failure" in failed[0]["error"]


def test_declared_maximum_covers_every_character_within_eight_segments(source, model):
    calls = model()
    limit = meeting_api._REPORT_SEGMENT_CHARS * meeting_api._MAX_REPORT_SEGMENTS
    transcript = (source * 3)[:limit - 1] + "."
    assert len(transcript) == limit
    result = asyncio.run(meeting_api.report_payload(transcript=transcript))
    segment_calls = [call for call in calls if "<transcript_segment>" in call["user"]]
    pieces = [call["user"].split("<transcript_segment>\n", 1)[1].rsplit("\n</transcript_segment>", 1)[0] for call in segment_calls]
    assert "".join(pieces) == transcript
    assert len(pieces) == meeting_api._MAX_REPORT_SEGMENTS
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["processed_chars"] == limit


def test_stalled_segment_reserves_time_for_a_visible_partial_synthesis(source, model, monkeypatch):
    calls = model(stall_middle=True)
    monkeypatch.setattr(meeting_api, "_REPORT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(meeting_api, "_REPORT_SYNTHESIS_RESERVE_S", 0.1)

    async def run():
        return await asyncio.wait_for(meeting_api.report_payload(transcript=source, language="fr"), timeout=2)

    result = asyncio.run(run())
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["synthesis_complete"] is True
    assert result["markdown"].startswith("> **Compte rendu partiel:")
    assert "<transcript_segment>" not in calls[-1]["user"]


def test_no_report_is_fabricated_when_all_segment_calls_fail(source, model):
    calls = model(fail_all=True)
    with pytest.raises(meeting_api.MeetingError, match="no transcript segment could be analyzed; no report was generated"):
        asyncio.run(meeting_api.report_payload(transcript=source))
    assert calls and all("<transcript_segment>" in call["user"] for call in calls)


def test_invented_extract_is_rejected_before_final_synthesis(source, model):
    calls = model(invalid_middle=True)
    result = asyncio.run(meeting_api.report_payload(transcript=source, language="fr"))
    assert "An invented decision outside the source." not in calls[-1]["user"]
    assert result["coverage"]["complete"] is False
    assert any(segment["status"] == "partial" for segment in result["coverage"]["segments"])
    assert result["markdown"].startswith("> **Compte rendu partiel:")


def test_failed_final_synthesis_returns_source_evidence_with_a_visible_limit(source, model):
    model(fail_synthesis=True)
    result = asyncio.run(meeting_api.report_payload(transcript=source, language="fr"))
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["processed_chars"] == result["coverage"]["input_chars"]
    assert result["coverage"]["synthesis_complete"] is False
    assert "Synthèse finale indisponible" in result["markdown"]
    assert "## Extraits sources disponibles" in result["markdown"]
    assert DECISION in result["markdown"]


@pytest.mark.parametrize("operation", ["report", "translate", "cleanup"])
def test_provider_length_stop_is_never_returned_as_a_complete_document(model, operation):
    from navin.meetings import services

    calls = model(finish_reason="length")

    async def run():
        if operation == "translate":
            return await services.translate_payload(text="A short source.", target_language="fr")
        if operation == "cleanup":
            return await services.cleanup_payload(transcript="A short source.")
        return await meeting_api.report_payload(transcript="A short source.")

    with pytest.raises(meeting_api.MeetingError, match="output was truncated"):
        asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["report", "notes", "translate", "cleanup"])
def test_oversized_inputs_are_rejected_before_any_provider_call(model, operation):
    from navin.meetings import services

    calls = model()

    async def run():
        if operation == "translate":
            return await services.translate_payload(text="X" * (services._MAX_TEXT_TRANSFORM_CHARS + 1), target_language="fr")
        if operation == "cleanup":
            return await services.cleanup_payload(transcript="X" * (services._MAX_TEXT_TRANSFORM_CHARS + 1))
        if operation == "notes":
            return await meeting_api.report_payload(notes="X" * (meeting_api._MAX_NOTES_CHARS + 1))
        return await meeting_api.report_payload(transcript="X" * (meeting_api._REPORT_SEGMENT_CHARS * meeting_api._MAX_REPORT_SEGMENTS + 1))

    with pytest.raises(meeting_api.MeetingError) as error:
        asyncio.run(run())
    assert error.value.status == 413
    assert calls == []
