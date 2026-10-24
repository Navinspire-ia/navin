# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Live voice in the terminal: the same call as the desktop Live mode."""

from __future__ import annotations

import asyncio
import math
import struct
from unittest.mock import AsyncMock, patch

from navin.tui import live_voice
from navin.tui.live_voice import (
    FRAME_SAMPLES,
    LiveReady,
    VoiceDetector,
    approval_from_speech,
    choice_from_speech,
    is_spoken_stop,
    pcm_to_wav,
    presence_mode,
    speech_text,
)
from navin.tui.runtime import UiApprovalRequested, UiAssistantMessage, UiProgress
from navin.tui.widgets import ApprovalCard, LivePresenceLine
from tests.test_tui_queue import make_app

# -- what is said and how answers are read ------------------------------------


def test_spoken_text_drops_code_links_and_markdown():
    reply = (
        "## Fait\n\nJ'ai corrigé **le bug** dans `auth.py`, voir [la doc](https://x.io).\n\n"
        "```python\nprint('x')\n```\n| a | b |\n|---|---|\n- reste : les tests"
    )
    spoken = speech_text(reply, code_note="Le code est dans le terminal.")
    assert spoken == (
        "Fait J'ai corrigé le bug dans auth.py, voir la doc. reste : les tests "
        "Le code est dans le terminal."
    )
    assert "```" not in spoken and "https" not in spoken and "|" not in spoken
    assert len(speech_text("Une phrase. " * 200)) <= live_voice.MAX_SPOKEN_CHARS


def test_spoken_yes_no_matches_the_desktop_rules():
    for said in ["Oui", "vas-y", "d'accord", "c'est bon", "pas de souci", "go ahead", "no problem"]:
        assert approval_from_speech(said) == "allow", said
    for said in ["Non", "surtout pas", "oui mais attends", "wait", "don't"]:
        assert approval_from_speech(said) == "deny", said
    assert approval_from_speech("quelle branche exactement") is None


def test_spoken_choices_and_stop():
    options = [{"id": "mui", "label": "Google Material"}, {"id": "fluent", "label": "Microsoft Fluent"}]
    assert choice_from_speech("B", options) == ("option", "fluent")
    assert choice_from_speech("la deuxième", options) == ("option", "fluent")
    assert choice_from_speech("on part sur Microsoft Fluent", options) == ("option", "fluent")
    assert choice_from_speech("comme tu veux", options) == ("skip", "")
    assert choice_from_speech("plutôt Carbon d'IBM", options) == ("other", "plutôt Carbon d'IBM")
    assert is_spoken_stop("Arrête !") and is_spoken_stop("stop please")
    assert not is_spoken_stop("stop le serveur de dev puis relance")


# -- voice detection ----------------------------------------------------------


def _frame(amplitude: float) -> bytes:
    value = int(amplitude * 32767)
    return b"".join(
        struct.pack("<h", int(value * math.sin(index / 3))) for index in range(FRAME_SAMPLES)
    )


def test_detector_sends_nothing_on_silence_and_one_utterance_on_speech():
    detector = VoiceDetector()
    assert all(detector.feed(_frame(0.001)) is None for _ in range(100))  # silence costs nothing
    utterances = [detector.feed(_frame(0.2)) for _ in range(30)]  # ~0.9 s of voice
    utterances += [detector.feed(_frame(0.001)) for _ in range(40)]  # then quiet
    finished = [pcm for pcm in utterances if pcm]
    assert len(finished) == 1
    assert pcm_to_wav(finished[0])[:4] == b"RIFF"


def test_detector_learns_a_noisy_room_before_listening():
    # A constant line noise (WSLg reads ~0.07 in silence) must never become
    # an utterance, a transcription and a turn the user did not say.
    detector = VoiceDetector()
    assert not any(detector.feed(_frame(0.09)) for _ in range(300))
    speech = [detector.feed(_frame(0.6)) for _ in range(30)] + [detector.feed(_frame(0.09)) for _ in range(40)]
    assert sum(1 for pcm in speech if pcm) == 1


def test_detector_ignores_a_click_and_its_own_voice_on_the_speaker():
    detector = VoiceDetector()
    for _ in range(50):
        detector.feed(_frame(0.001))
    clicks = [detector.feed(_frame(0.2)) for _ in range(4)] + [detector.feed(_frame(0.001)) for _ in range(40)]
    assert not any(clicks)  # too short to be speech
    detector.playing = True  # the agent's voice comes back through the mic
    echo = [detector.feed(_frame(0.02)) for _ in range(30)] + [detector.feed(_frame(0.001)) for _ in range(40)]
    assert not any(echo)


# -- the call inside the TUI ----------------------------------------------------


class _FakeCall:
    """A live session without microphone or speaker."""

    def __init__(self) -> None:
        self.active = True
        self.muted = False
        self.speaking = False
        self.transcribing = False
        self.hearing = False
        self.level = 0.0
        self.said: list[str] = []
        self.microphone = None

    def speak(self, text: str) -> None:
        self.said.append(text)

    async def stop(self) -> None:
        self.active = False


def test_presence_follows_the_call():
    call = _FakeCall()
    assert presence_mode(None, False) is None
    assert presence_mode(call, False) == "listening"  # type: ignore[arg-type]
    call.hearing = True
    assert presence_mode(call, False) == "hearing"  # type: ignore[arg-type]
    call.hearing, call.speaking = False, True
    assert presence_mode(call, True) == "speaking"  # type: ignore[arg-type]
    call.speaking = False
    assert presence_mode(call, True) == "thinking"  # type: ignore[arg-type]
    call.muted = True
    assert presence_mode(call, True) == "muted"  # type: ignore[arg-type]


def test_spoken_words_become_a_voice_turn_and_the_answer_is_spoken_once(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            call = _FakeCall()
            app._live = call
            app.runtime.voice_mode = True
            app._refresh_live_presence()
            await pilot.pause()
            line = app.query_one("#live-presence", LivePresenceLine)
            assert line.display and app._live_text("listening") in str(line.content)

            await app._live_heard("ajoute un test pour le parseur")
            inbound = await app.runtime.bus.consume_inbound()
            assert inbound.content == "ajoute un test pour le parseur"
            assert inbound.metadata.get("voice_mode") is True  # the expert-on-a-call brief
            assert app._live_lang == "fr"

            await app._on_runtime_event(UiProgress("Je regarde le parseur."))
            await app._on_runtime_event(UiProgress("read_file(parser.py)", tool_hint=True))
            await app._on_runtime_event(UiAssistantMessage("C'est fait, **un test** ajouté. Tu veux que je lance la suite ?"))
            await app._on_runtime_event(UiAssistantMessage("C'est fait, **un test** ajouté. Tu veux que je lance la suite ?"))
            assert call.said == [
                "Je regarde le parseur.",
                "C'est fait, un test ajouté. Tu veux que je lance la suite ?",
            ]
    asyncio.run(run())


def test_an_approval_is_read_aloud_and_answered_by_voice(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            call = _FakeCall()
            app._live = call
            app._live_lang = "fr"
            app.runtime.approve = AsyncMock()
            with patch.object(app, "bell") as bell:
                await app._on_runtime_event(UiApprovalRequested(
                    request_id="r1", tool="git", action="Push the branch", reason="", detail="git push origin main",
                    consequence="", scope="", remember_offered=False, expires_at_ms=None,
                ))
                bell.assert_called()  # the call: ring when the agent needs the user
            assert call.said[-1].startswith("Autorisation demandée: Push the branch.")
            await app._live_heard("euh quelle branche")
            assert call.said[-1] == "Je n'ai pas compris. Oui ou non ?"
            assert app.query(ApprovalCard)
            await app._live_heard("oui vas-y")
            await pilot.pause()
            app.runtime.approve.assert_awaited_once_with("r1", allowed=True, remember=False)
            assert not app._pending_approvals
    asyncio.run(run())


def test_live_refuses_to_start_without_speech_engines(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app._engine_ready = True
            with patch.object(live_voice, "CLI_LIVE_ENABLED", True), \
                    patch.object(live_voice, "live_ready", return_value=LiveReady(False, "tts_not_configured")):
                await app.action_toggle_live()
            await pilot.pause()
            assert app._live is None and app.runtime.voice_mode is False
            assert any("Live voice is not ready" in str(note.content) for note in app.query("SystemNote"))
    asyncio.run(run())


def test_noise_and_bare_acknowledgements_never_become_a_turn(tmp_path):
    from navin.tui.live_voice import transcript_is_meaningful

    assert not transcript_is_meaningful("Sous-titres réalisés par la communauté d'Amara.org")
    assert not transcript_is_meaningful("Thank you.")
    assert not transcript_is_meaningful("euh")
    assert not transcript_is_meaningful("hmm ok")
    assert transcript_is_meaningful("oui", answer_expected=True)
    assert transcript_is_meaningful("lance les tests")

    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)):
            app._live = _FakeCall()
            await app._live_heard("hmm")
            assert app.runtime.bus.inbound_size == 0
            # After a spoken question, a bare yes is the answer.
            await app._on_runtime_event(UiAssistantMessage("Je lance la suite de tests ?"))
            await app._live_heard("oui")
            assert (await app.runtime.bus.consume_inbound()).content == "oui"
    asyncio.run(run())


def test_the_terminal_live_is_switched_off_for_now(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(100, 32)) as pilot:
            app._engine_ready = True
            await app.action_toggle_live()
            await pilot.pause()
            assert app._live is None
            assert any("not available in the terminal" in str(note.content) for note in app.query("SystemNote"))
            assert not app.query("#meta-live")
            assert all(row["command"] != "/live" for row in app.slash_rows)
    asyncio.run(run())
