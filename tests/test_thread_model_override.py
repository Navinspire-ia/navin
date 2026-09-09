# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A conversation can pin its own model without touching the global default.

The webui composer sends the pinned preset name in the message metadata
(``INBOUND_META_MODEL_PRESET``); the loop resolves it per turn so two open
chats can run on different providers at the same time, Cursor-style.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.agent.loop import AgentLoop
from navin.bus.events import INBOUND_META_MODEL_PRESET, InboundMessage
from navin.providers.base import GenerationSettings
from navin.utils.llm_runtime import LLMRuntime


def _runtime(model: str, preset: str | None = None) -> LLMRuntime:
    return LLMRuntime(
        provider=SimpleNamespace(),
        model=model,
        generation=GenerationSettings(),
        context_window_tokens=128_000,
        model_preset=preset,
    )


def _msg(metadata: dict | None = None) -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-1",
        content="hello",
        metadata=dict(metadata or {}),
    )


class _Resolver:
    def __init__(self, presets: dict[str, LLMRuntime]) -> None:
        self._presets = presets
        self.requested: list[str] = []

    @property
    def model_presets(self) -> dict[str, LLMRuntime]:
        return dict(self._presets)

    def resolve_preset(self, name: str) -> LLMRuntime:
        self.requested.append(name)
        try:
            return self._presets[name]
        except KeyError:
            raise KeyError(name) from None


def _stub_loop(default: LLMRuntime, presets: dict[str, LLMRuntime]) -> SimpleNamespace:
    loop = SimpleNamespace(
        llm_runtime=lambda: default,
        runtime_resolver=_Resolver(presets),
        model_presets=tuple(presets),
        _unified_session=False,
    )
    # Real session plumbing, exercised through the actual method: sticky pins
    # read the chat's persisted model choice from session metadata.
    session = SimpleNamespace(metadata={})
    loop.session = session
    loop.sessions = SimpleNamespace(get_or_create=lambda key: session)
    loop._effective_session_key = lambda msg: msg.session_key
    loop._session_pinned_preset = (
        lambda msg: AgentLoop._session_pinned_preset(loop, msg)
    )
    return loop


class RuntimeForInboundTest(unittest.TestCase):
    def test_no_metadata_uses_the_default(self) -> None:
        default = _runtime("gpt-5", preset="main")
        loop = _stub_loop(default, {})
        self.assertIs(AgentLoop.runtime_for_inbound(loop, _msg()), default)
        self.assertEqual(loop.runtime_resolver.requested, [])

    def test_a_pinned_preset_shapes_the_turn(self) -> None:
        default = _runtime("gpt-5", preset="main")
        pinned = _runtime("claude-sonnet-4", preset="sonnet")
        loop = _stub_loop(default, {"sonnet": pinned})
        result = AgentLoop.runtime_for_inbound(
            loop, _msg({INBOUND_META_MODEL_PRESET: "sonnet"})
        )
        self.assertIs(result, pinned)

    def test_pinning_the_active_default_skips_re_resolution(self) -> None:
        default = _runtime("gpt-5", preset="main")
        loop = _stub_loop(default, {})
        result = AgentLoop.runtime_for_inbound(
            loop, _msg({INBOUND_META_MODEL_PRESET: "main"})
        )
        self.assertIs(result, default)
        self.assertEqual(loop.runtime_resolver.requested, [])

    def test_an_unknown_preset_falls_back_to_the_default(self) -> None:
        # A preset deleted in Settings must not fail the turn of a chat that
        # still pins it: the default carries the message instead.
        default = _runtime("gpt-5", preset="main")
        loop = _stub_loop(default, {})
        result = AgentLoop.runtime_for_inbound(
            loop, _msg({INBOUND_META_MODEL_PRESET: "deleted"})
        )
        self.assertIs(result, default)

    def test_a_media_preset_never_runs_the_chat_turn(self) -> None:
        # A music / image / STT preset pinned by a stale thread or a bad
        # route must not carry a conversation: the default runtime does.
        default = _runtime("gpt-5", preset="main")
        lyria = _runtime("google/lyria-3-clip-preview", preset="lyria-3-clip-preview")
        loop = _stub_loop(default, {"lyria-3-clip-preview": lyria})
        loop.model_presets = {
            "lyria-3-clip-preview": SimpleNamespace(modality="music"),
        }
        result = AgentLoop.runtime_for_inbound(
            loop, _msg({INBOUND_META_MODEL_PRESET: "lyria-3-clip-preview"})
        )
        self.assertIs(result, default)
        self.assertEqual(loop.runtime_resolver.requested, [])

    def test_blank_or_non_string_values_are_ignored(self) -> None:
        default = _runtime("gpt-5")
        loop = _stub_loop(default, {})
        for junk in ("", "   ", 42, None, ["sonnet"]):
            result = AgentLoop.runtime_for_inbound(
                loop, _msg({INBOUND_META_MODEL_PRESET: junk})
            )
            self.assertIs(result, default)
        self.assertEqual(loop.runtime_resolver.requested, [])


class StickySessionModelPinTest(unittest.TestCase):
    """The model picked for a chat survives reloads and gateway restarts.

    Regression: a Kimi K3 conversation answered with the global default
    (DeepSeek) after a restart because the per-message pin was lost with the
    in-flight turn. The session remembers the explicit choice server-side.
    """

    def test_a_message_without_pin_reuses_the_session_model(self) -> None:
        default = _runtime("deepseek", preset="deepseek-v4-flash")
        kimi = _runtime("moonshotai/kimi-k3", preset="kimi-k3")
        loop = _stub_loop(default, {"kimi-k3": kimi})
        loop.session.metadata[AgentLoop._SESSION_MODEL_PIN_KEY] = "kimi-k3"
        result = AgentLoop.runtime_for_inbound(loop, _msg())
        self.assertIs(result, kimi)

    def test_an_explicit_pin_beats_the_remembered_one(self) -> None:
        default = _runtime("deepseek", preset="deepseek-v4-flash")
        kimi = _runtime("moonshotai/kimi-k3", preset="kimi-k3")
        glm = _runtime("z-ai/glm-5.2", preset="glm-5-2")
        loop = _stub_loop(default, {"kimi-k3": kimi, "glm-5-2": glm})
        loop.session.metadata[AgentLoop._SESSION_MODEL_PIN_KEY] = "kimi-k3"
        result = AgentLoop.runtime_for_inbound(
            loop, _msg({INBOUND_META_MODEL_PRESET: "glm-5-2"})
        )
        self.assertIs(result, glm)

    def test_a_user_pin_is_persisted_on_the_session(self) -> None:
        session = SimpleNamespace(metadata={})
        loop = SimpleNamespace()
        AgentLoop._persist_model_pin(
            loop, session, _msg({INBOUND_META_MODEL_PRESET: "kimi-k3"})
        )
        self.assertEqual(
            session.metadata[AgentLoop._SESSION_MODEL_PIN_KEY], "kimi-k3"
        )

    def test_internal_continuations_never_rewrite_the_pin(self) -> None:
        from navin.session import turn_continuation as tc

        session = SimpleNamespace(metadata={})
        loop = SimpleNamespace()
        AgentLoop._persist_model_pin(
            loop,
            session,
            _msg(
                {
                    INBOUND_META_MODEL_PRESET: "glm-5-2",
                    tc.INTERNAL_CONTINUATION_META: True,
                }
            ),
        )
        self.assertNotIn(AgentLoop._SESSION_MODEL_PIN_KEY, session.metadata)


class TaskRouteIsNotAPinTest(unittest.TestCase):
    """A model chosen by Task routing must not become the chat's sticky pin.

    Regression: the runtime stamp rewrites ``model_preset`` with the model that
    ran, and the pin persister used to read that key. One "dev" message routed
    to Nemotron then pinned Nemotron for every later message of the chat, even
    plain conversation, while the composer kept showing the default model.
    """

    def _loop(self):
        default = _runtime("z-ai/glm-4.7-flash", preset="default")
        nemotron = _runtime("nvidia/nemotron-3-ultra", preset="nemotron")
        loop = _stub_loop(default, {"nemotron": nemotron})
        loop._runtime_events = lambda: SimpleNamespace(
            runtime_model_changed=lambda *a, **k: loop.events.append(k)
        )
        loop.events = []
        return loop, default, nemotron

    def test_a_routed_model_is_never_persisted_as_the_pin(self) -> None:
        from unittest.mock import patch

        loop, _default, nemotron = self._loop()
        msg = _msg({"composer_mode": "agent"})
        msg.content = "corrige le bug dans app.py"
        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "nemotron"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)
        self.assertIs(result, nemotron)
        self.assertEqual(msg.metadata.get("model_preset"), "nemotron")
        AgentLoop._persist_model_pin(loop, loop.session, msg)
        self.assertNotIn(AgentLoop._SESSION_MODEL_PIN_KEY, loop.session.metadata)
        # The UI learns that routing, not the user, swapped the model.
        self.assertEqual(loop.events[-1]["reason"], "route:dev")
        self.assertEqual(loop.events[-1]["previous_model"], "z-ai/glm-4.7-flash")

    def test_a_user_pin_survives_the_runtime_stamp(self) -> None:
        loop, _default, nemotron = self._loop()
        msg = _msg({INBOUND_META_MODEL_PRESET: "nemotron"})
        AgentLoop.runtime_for_inbound(loop, msg)
        AgentLoop._persist_model_pin(loop, loop.session, msg)
        self.assertEqual(
            loop.session.metadata[AgentLoop._SESSION_MODEL_PIN_KEY], "nemotron"
        )
        self.assertEqual(loop.events[-1]["reason"].split(":")[0], "task")

    def test_auto_clears_the_sticky_pin_and_lets_routing_choose(self) -> None:
        from unittest.mock import patch

        from navin.agent.loop import AUTO_MODEL_PIN

        loop, default, _nemotron = self._loop()
        loop.session.metadata[AgentLoop._SESSION_MODEL_PIN_KEY] = "nemotron"
        msg = _msg({INBOUND_META_MODEL_PRESET: AUTO_MODEL_PIN})
        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "nemotron"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)
        self.assertIs(result, default)
        AgentLoop._persist_model_pin(loop, loop.session, msg)
        self.assertNotIn(AgentLoop._SESSION_MODEL_PIN_KEY, loop.session.metadata)
