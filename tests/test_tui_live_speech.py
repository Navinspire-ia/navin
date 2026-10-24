# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The terminal Live answers out loud: a noise while the reply is being
synthesized never drops it; talking over the voice still cuts it."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from navin.config.schema import Config
from navin.tui.live_voice import LiveVoiceSession, speech_chunks


class StubDetector:
    playing = False
    level = 0.0

    def __init__(self) -> None:
        self.speaking = False

    def feed(self, frame: bytes) -> None:
        self.speaking = True  # the user (or the room) starts making sound
        return None

    def reset(self) -> None:
        self.speaking = False


def _session() -> LiveVoiceSession:
    session = LiveVoiceSession(Config(), on_transcript=lambda _t: None, on_state=lambda: None, on_error=None)
    session.detector = StubDetector()
    session.active = True
    return session


def test_a_sound_alone_never_cuts_the_voice():
    async def scenario() -> int:
        session = _session()
        session._loop = asyncio.get_running_loop()
        epoch = session._epoch
        session.speaking = True  # the voice is playing; the mic hears the room or the speakers
        session._frame(b"\x00\x00" * 160)
        await asyncio.sleep(0)
        return session._epoch - epoch

    assert asyncio.run(scenario()) == 0


def _heard(session: LiveVoiceSession, text: str) -> list[str]:
    delivered: list[str] = []

    async def on_transcript(value: str) -> None:
        delivered.append(value)

    session.on_transcript = on_transcript

    async def scenario() -> None:
        with patch("navin.audio.transcription.transcribe_audio_file", AsyncMock(return_value=text)):
            await session._transcribe(b"\x00\x00" * 1600)

    asyncio.run(scenario())
    return delivered


def test_the_agents_own_voice_caught_by_the_mic_is_not_a_new_message():
    session = _session()
    session.speaker = SimpleNamespace(stop=lambda: None)
    session._recent_speech.append(("Salut ! Sur quoi on travaille aujourd'hui ? On continue les traductions ?", time.monotonic()))
    assert _heard(session, "Salut, sur quoi on travaille aujourd'hui ? On continue les") == []
    assert _heard(session, "Lance les tests du backend") == ["Lance les tests du backend"]


def test_real_words_over_the_voice_cut_it():
    session = _session()
    session.speaker = SimpleNamespace(stop=lambda: None)
    session.speaking = True
    epoch = session._epoch
    assert _heard(session, "Attends, arrête de lire") == ["Attends, arrête de lire"]
    assert session._epoch == epoch + 1


def test_long_replies_start_with_a_short_clip_and_prefetch_the_next():
    first, *rest = speech_chunks("Premier point court. " + "Une phrase un peu plus longue pour la suite. " * 8)
    assert len(first) <= 160 and rest

    order: list[str] = []

    async def scenario() -> None:
        session = _session()

        async def synth(text, cfg):
            order.append(f"synth:{text[:12]}")
            await asyncio.sleep(0.01)
            return b"RIFF"

        async def play(audio, mime):
            order.append("play-start")
            await asyncio.sleep(0.05)
            order.append("play-end")

        session.speaker = SimpleNamespace(play=play, stop=lambda: None)
        with patch("navin.audio.tts.synthesize_speech_with_config", synth):
            task = asyncio.create_task(session._speak_loop())
            session.speak("Bonjour. " + "Deuxième morceau assez long pour faire un autre clip. " * 6)
            await asyncio.sleep(0.6)
            task.cancel()

    asyncio.run(scenario())
    # The second clip is synthesized while the first one plays.
    synths = [index for index, event in enumerate(order) if event.startswith("synth:")]
    assert order.index("play-start") < synths[1] < order.index("play-end")
    assert order.count("play-start") >= 2


def test_work_from_the_microphone_thread_runs_in_the_app_context():
    import contextvars
    import threading

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker")
    seen: list[str] = []

    async def scenario() -> None:
        marker.set("app")
        session = _session()
        session.on_state = lambda: seen.append(marker.get("missing"))
        session._loop = asyncio.get_running_loop()
        session._context = contextvars.copy_context()
        session.detector = StubDetector()  # its first frame opens an utterance

        def from_thread() -> None:
            session._frame(b"\x00\x00" * 160)  # the real microphone callback

        thread = threading.Thread(target=from_thread)
        thread.start()
        thread.join()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert seen == ["app"]


def test_a_card_already_waiting_is_read_when_the_call_starts():
    from navin.tui.app import NavinApp

    said: list[str] = []
    rang: list[bool] = []
    choice = SimpleNamespace(question="Quelle base de données ?", options=[{"id": "1", "label": "Postgres"},
                                                                           {"id": "2", "label": "SQLite"}])
    host = SimpleNamespace(
        _pending_approvals={}, _pending_choices={"r1": choice},
        _live_say=said.append, _live_call=lambda: rang.append(True),
        _live_text=lambda key: {"other": "Tu peux aussi répondre librement."}.get(key, key),
    )
    NavinApp._live_announce_pending(host)
    assert said and "Quelle base de données" in said[0] and "Postgres" in said[0]
    assert rang == [True]


PLAN = "## Plan de migration\nVoici le plan.\n" + "\n".join(f"{i}. étape {i}" for i in range(1, 7))


def _offer_host(said: list[str], **extra):
    from navin.tui.app import NavinApp

    host = SimpleNamespace(
        _live_lang="fr", _live_say=said.append, _live_offer=None,
        runtime=SimpleNamespace(
            config=SimpleNamespace(license=SimpleNamespace(activated=True, account_name="aymen ghad")),
            turn_active=False,
        ),
        **extra,
    )
    host._live_text = lambda key: NavinApp._LIVE_TEXT["fr"][key]
    host._live_offer_text = lambda when, digest: NavinApp._live_offer_text(host, when, digest)
    return host


def test_the_call_opens_by_name_and_offers_to_sum_up_the_plan_on_screen():
    from navin.tui.app import NavinApp

    said: list[str] = []
    host = _offer_host(said, _live_last_agent_text=lambda: PLAN)
    NavinApp._live_greet(host)
    assert said == ["Salut Aymen, je suis là et je t'écoute. On travaille sur quoi ? "
                    "Le dernier message est un plan en 6 étapes. Je te le résume ?"]
    assert host._live_offer == ("plan", 6)


def test_yes_asks_the_agent_for_a_spoken_summary_no_moves_on():
    from navin.tui.app import NavinApp

    async def heard(text: str) -> tuple[list[str], list[str]]:
        said: list[str] = []
        sent: list[str] = []

        async def submit_text(value: str, send_now: bool = False) -> None:
            sent.append(value)

        host = _offer_host(
            said, _live=object(), _pending_approvals={}, _pending_choices={}, _live_last_question=False,
            _FRENCH_HINT=NavinApp._FRENCH_HINT, _ENGLISH_HINT=NavinApp._ENGLISH_HINT,
            submit_text=submit_text, _flash=AsyncMock(), _request_stop=AsyncMock(),
            _live_summary_asked=False,
        )
        host._live_offer = ("plan", 6)
        await NavinApp._live_heard(host, text)
        return said, sent

    said, sent = asyncio.run(heard("oui vas-y"))
    assert len(sent) == 1 and sent[0].startswith("Résume-moi à l'oral le plan ci-dessus")
    said, sent = asyncio.run(heard("non"))
    assert (said, sent) == (["D'accord."], [])
    said, sent = asyncio.run(heard("lance plutôt les tests du backend"))
    assert sent == ["lance plutôt les tests du backend"]


def test_the_summary_it_asked_for_is_read_not_offered_again():
    from navin.tui.app import NavinApp
    from navin.tui.runtime import UiAssistantMessage

    said: list[str] = []
    host = _offer_host(said, _live_summary_asked=True, _live_last_question=False, _live_call=lambda: None)
    host._live_spoken = []
    host._live = SimpleNamespace(speak=said.append)
    host._live_say = lambda text: NavinApp._live_say(host, text)
    long_summary = "Le plan tient en trois idées. " * 40 + "Je corrige les deux points critiques ?"
    NavinApp._live_observe(host, UiAssistantMessage(text=long_summary))
    assert said and said[0].startswith("Le plan tient en trois idées.")
    assert host._live_offer is None and host._live_summary_asked is False
