# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live voice: the spoken utterance becomes the message the user would have typed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from navin.audio.voice_prompt import (
    build_rewrite_messages,
    clean_rewrite_output,
    normalize_transcript,
    rewrite_is_faithful,
    rewrite_voice_transcript,
    rewrite_worth_it,
)
from navin.webui.voice_session_ws import (
    clear_voice_sessions_for_tests,
    webui_voice_session_events,
)


class FakeProvider:
    def __init__(
        self,
        content: str | None,
        *,
        delay: float = 0.0,
        fail: bool = False,
        finish_reason: str = "stop",
        fail_models: frozenset[str] = frozenset(),
    ):
        self.content = content
        self.delay = delay
        self.fail = fail
        self.finish_reason = finish_reason
        self.fail_models = fail_models
        self.calls: list[dict] = []

    async def chat_with_retry(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail or kwargs.get("model") in self.fail_models:
            raise RuntimeError("provider down")
        return SimpleNamespace(content=self.content, finish_reason=self.finish_reason)


def test_normalize_and_worth_it():
    assert normalize_transcript("  euh   bonjour \n toi ") == "euh bonjour toi"
    assert normalize_transcript(None) == ""
    assert rewrite_worth_it("oui vas-y") is False
    assert rewrite_worth_it("oui vas y fais le") is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Ajoute un dark mode."', "Ajoute un dark mode."),
        ("Rewritten message: Ajoute un dark mode.", "Ajoute un dark mode."),
        ("```\nAjoute un dark mode.\n```", "Ajoute un dark mode."),
        ("« Ajoute un dark mode. »", "Ajoute un dark mode."),
        (None, ""),
    ],
)
def test_clean_rewrite_output(raw, expected):
    assert clean_rewrite_output(raw) == expected


def test_rewrite_is_faithful_rejects_answers_and_empty():
    transcript = "euh est ce que tu peux ajouter un dark mode au site"
    assert rewrite_is_faithful(transcript, "Peux-tu ajouter un dark mode au site ?") is True
    assert rewrite_is_faithful(transcript, "") is False
    ballooned = "Bien sur ! Voici comment ajouter un dark mode : " + "x" * 200
    assert rewrite_is_faithful(transcript, ballooned) is False


def test_build_rewrite_messages_carries_context_without_answering_it():
    messages = build_rewrite_messages("fais le", "Tu veux le bouton en haut ou en bas ?")
    assert messages[0]["role"] == "system"
    assert "do not answer it" in messages[1]["content"]
    assert "Tu veux le bouton" in messages[1]["content"]
    assert messages[1]["content"].rstrip().endswith("fais le")


@pytest.mark.asyncio
async def test_rewrite_uses_provider_and_cleans_output():
    provider = FakeProvider('"Peux-tu ajouter un dark mode au site tickets-resto ?"')
    result = await rewrite_voice_transcript(
        "euh est ce que tu peux euh ajouter un dark mode au site tickets resto",
        provider=provider,
    )
    assert result == "Peux-tu ajouter un dark mode au site tickets-resto ?"
    call = provider.calls[0]
    assert call["temperature"] == 0.0
    assert call["tools"] is None
    assert "Transcript:" in call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_short_utterances_skip_the_model():
    provider = FakeProvider("Should not be used")
    assert await rewrite_voice_transcript("oui vas-y", provider=provider) == "oui vas-y"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_rewrite_walks_fast_models_then_default():
    transcript = "euh est ce que tu peux euh ajouter un dark mode au site"
    provider = FakeProvider(
        "Peux-tu ajouter un dark mode au site ?",
        fail_models=frozenset({"deepseek/deepseek-v4-flash"}),
    )
    result = await rewrite_voice_transcript(
        transcript,
        provider=provider,
        models=["deepseek/deepseek-v4-flash", "google/gemini-3.7-flash", None],
    )
    assert result == "Peux-tu ajouter un dark mode au site ?"
    assert [call.get("model") for call in provider.calls] == [
        "deepseek/deepseek-v4-flash",
        "google/gemini-3.7-flash",
    ]


class SlowFirstProvider(FakeProvider):
    """The first candidate hangs; the second answers at once."""

    def __init__(self, content: str, *, slow_model: str, slow_for: float):
        super().__init__(content)
        self.slow_model = slow_model
        self.slow_for = slow_for

    async def chat_with_retry(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if kwargs.get("model") == self.slow_model:
            await asyncio.sleep(self.slow_for)
        return SimpleNamespace(content=self.content, finish_reason="stop")


@pytest.mark.asyncio
async def test_slow_candidate_is_hedged_by_the_next_one():
    transcript = "euh est ce que tu peux euh ajouter un dark mode au site"
    provider = SlowFirstProvider(
        "Peux-tu ajouter un dark mode au site ?",
        slow_model="deepseek/deepseek-v4-flash",
        slow_for=5.0,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await rewrite_voice_transcript(
        transcript,
        provider=provider,
        models=["deepseek/deepseek-v4-flash", "google/gemini-3.7-flash", None],
        timeout_s=4.0,
        hedge_delay_s=0.05,
    )
    assert result == "Peux-tu ajouter un dark mode au site ?"
    # Answered by the second model right after the hedge delay, not after the
    # first one gave up.
    assert loop.time() - started < 1.0
    assert [call.get("model") for call in provider.calls] == [
        "deepseek/deepseek-v4-flash",
        "google/gemini-3.7-flash",
    ]


@pytest.mark.asyncio
async def test_fast_candidates_start_together_and_the_default_waits():
    transcript = "euh est ce que tu peux euh ajouter un dark mode au site"
    provider = SlowFirstProvider(
        "Peux-tu ajouter un dark mode au site ?",
        slow_model="google/gemini-3.7-flash",
        slow_for=0.3,
    )
    # The second fast model answers at once: the default model never starts.
    result = await rewrite_voice_transcript(
        transcript,
        provider=provider,
        models=["google/gemini-3.7-flash", "deepseek/deepseek-v4-flash", None],
        hedge_delay_s=1.0,
    )
    assert result == "Peux-tu ajouter un dark mode au site ?"
    assert [call.get("model") for call in provider.calls] == [
        "google/gemini-3.7-flash",
        "deepseek/deepseek-v4-flash",
    ]


@pytest.mark.asyncio
async def test_truncated_reasoning_output_is_skipped():
    transcript = "euh est ce que tu peux euh ajouter un dark mode au site"
    provider = FakeProvider("Je voudrais que tu", finish_reason="length")
    assert await rewrite_voice_transcript(transcript, provider=provider, models=[None]) == transcript


def test_rewrite_model_candidates_prefers_pinned_then_fast_models():
    from navin.audio.voice_prompt import rewrite_model_candidates

    pinned = SimpleNamespace(voice=SimpleNamespace(prompt_model="openai/gpt-5-mini"))
    assert rewrite_model_candidates(pinned) == ["openai/gpt-5-mini"]

    class Cfg:
        voice = SimpleNamespace(prompt_model="")

        def resolve_preset(self):
            return SimpleNamespace(model="z-ai/glm-5.3-flash")

        def get_provider_name(self, model, *, preset=None):
            return "navin"

    candidates = rewrite_model_candidates(Cfg())
    assert candidates[0] == "google/gemini-3.7-flash"
    assert candidates[-1] is None

    class Local(Cfg):
        def get_provider_name(self, model, *, preset=None):
            return "ollama"

    assert rewrite_model_candidates(Local()) == [None]


@pytest.mark.asyncio
async def test_rewrite_falls_back_on_failure_timeout_and_unfaithful_output():
    transcript = "alors euh je voudrais que tu regardes le fichier config point json"
    assert await rewrite_voice_transcript(transcript, provider=FakeProvider(None, fail=True)) == transcript
    assert (
        await rewrite_voice_transcript(transcript, provider=FakeProvider("x", delay=0.2), timeout_s=0.01)
        == transcript
    )
    answer = "Voici le contenu de config.json : " + "{...} " * 40
    assert await rewrite_voice_transcript(transcript, provider=FakeProvider(answer)) == transcript


@pytest.mark.asyncio
async def test_voice_prompt_envelope_returns_raw_and_rewritten_text():
    clear_voice_sessions_for_tests()
    config = SimpleNamespace(
        license=SimpleNamespace(plan="pro", managed_api_key="k"),
        voice=SimpleNamespace(realtime_enabled=True),
        transcription=SimpleNamespace(provider="openrouter"),
    )
    with (
        patch("navin.webui.voice_session_ws.load_config", return_value=config),
        patch(
            "navin.webui.voice_session_ws.resolve_tts_config",
            return_value=SimpleNamespace(
                auto_speak=False, provider="navin", model="google/gemini-3.1-flash-tts-preview",
                configured=True, voice="Kore", response_format="wav"
            ),
        ),
        patch(
            "navin.webui.voice_session_ws.resolve_transcription_config",
            return_value=SimpleNamespace(enabled=True, configured=True, provider="openrouter"),
        ),
        patch(
            "navin.audio.voice_prompt.rewrite_voice_transcript",
            return_value="Peux-tu ajouter un dark mode ?",
        ) as rewrite,
    ):
        started = await webui_voice_session_events({"type": "voice_session_start", "request_id": "r1"})
        session_id = started[0][1]["session_id"]
        events = await webui_voice_session_events(
            {
                "type": "voice_prompt",
                "session_id": session_id,
                "request_id": "r2",
                "text": "euh est ce que tu peux  euh ajouter un dark mode",
                "context": "Tu veux quoi ensuite ?",
            }
        )
        rejected = await webui_voice_session_events(
            {"type": "voice_prompt", "session_id": "nope", "request_id": "r3", "text": "x"}
        )
        empty = await webui_voice_session_events(
            {"type": "voice_prompt", "session_id": session_id, "request_id": "r4", "text": "   "}
        )
    assert events[0][0] == "voice_prompt_ready"
    payload = events[0][1]
    assert payload["text"] == "Peux-tu ajouter un dark mode ?"
    assert payload["raw_text"] == "euh est ce que tu peux euh ajouter un dark mode"
    assert payload["rewritten"] is True
    assert payload["request_id"] == "r2"
    assert rewrite.call_args.kwargs["context"] == "Tu veux quoi ensuite ?"
    assert rejected[0] == ("voice_session_error", {"detail": "invalid_session", "session_id": "nope", "request_id": "r3"})
    assert empty[0][1]["detail"] == "empty_prompt"
