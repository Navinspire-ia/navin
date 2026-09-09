"""Phase 4 of desktop control: grounding capability, model route, native tool.

- ``supports_grounding`` separates "can read an image" from "can place a click".
- A turn asking to drive the desktop routes to the ``computer`` preset when
  the tool is on, without touching plain chat or the vision route.
- On the direct Anthropic provider the tool goes out as Claude's server-side
  ``computer`` primitive with its beta flag; every other provider keeps the
  JSON schema.
"""

from __future__ import annotations

import io
import unittest
from typing import Any

from navin.agent.model_routes import (
    resolve_computer_route,
    resolve_vision_route,
    text_needs_computer,
)
from navin.agent.tools.computer import (
    _SESSIONS,
    ComputerTool,
    ComputerToolConfig,
)
from navin.agent.tools.context import RequestContext, request_context
from navin.computer.base import ComputerBackend, ScreenInfo, Screenshot, WindowInfo
from navin.providers.model_capabilities import supports_grounding, supports_vision
from navin.providers.native_tools import (
    NativeToolSpec,
    merge_beta_header,
    native_betas,
    native_tool_spec,
    register_native_tool,
    unregister_native_tool,
)


class GroundingCapabilityTest(unittest.TestCase):
    def test_grounding_models_are_recognised(self) -> None:
        for slug in (
            "anthropic/claude-sonnet-4.5",
            "claude-opus-4-1-20250805",
            "openai/gpt-5",
            "gpt-4o-2024-11-20",
            "openai/computer-use-preview",
            "google/gemini-2.5-pro",
            "gemini-3-flash",
            "qwen/qwen2.5-vl-72b-instruct",
            "bytedance/ui-tars-1.5-7b",
            "zai-org/glm-4.5v",
        ):
            with self.subTest(slug=slug):
                self.assertTrue(supports_vision(slug))
                self.assertTrue(supports_grounding(slug))

    def test_vision_without_grounding(self) -> None:
        # Reads images, but no coordinate training we can rely on.
        for slug in ("meta-llama/llama-3.2-11b-vision-instruct", "xiaomi/mimo-v2.5-omni"):
            with self.subTest(slug=slug):
                self.assertTrue(supports_vision(slug))
                self.assertFalse(supports_grounding(slug))

    def test_text_only_models_never_ground(self) -> None:
        for slug in ("deepseek/deepseek-chat", "gpt-oss-120b", "mistral-large", ""):
            with self.subTest(slug=slug):
                self.assertFalse(supports_grounding(slug))

    def test_declared_modalities_win_over_the_family_list(self) -> None:
        # A catalog row that says "text only" is believed even for a Claude slug.
        self.assertFalse(supports_grounding("claude-sonnet-4.5", input_modalities=["text"]))
        self.assertTrue(
            supports_grounding("claude-sonnet-4.5", input_modalities=["text", "image"])
        )
        # Declared vision does not promote an unknown family to grounding.
        self.assertFalse(supports_grounding("acme/foo-1", input_modalities=["text", "image"]))
        self.assertTrue(supports_vision("acme/foo-1", input_modalities=["text", "image"]))


class ComputerRouteTest(unittest.TestCase):
    def test_desktop_phrases_in_both_languages(self) -> None:
        for text in (
            "Prends le contrôle de mon ordinateur et ouvre le logiciel de compta",
            "clique sur le bouton Enregistrer dans Excel",
            "Sur mon écran, ferme la fenêtre de mise à jour",
            "ouvre l'application Photoshop et exporte en PNG",
            "Open the app Blender and render the scene",
            "click on the Save button in Word",
            "take control of my computer and install the update",
            "use my mouse to drag the file into the folder",
            "in Outlook, archive everything from last week",
            "dans les paramètres Windows, active le mode sombre",
        ):
            with self.subTest(text=text):
                self.assertTrue(text_needs_computer(text))

    def test_plain_chat_and_browsing_do_not_route(self) -> None:
        for text in (
            "résume ce PDF",
            "écris une fonction Python qui trie une liste",
            "cherche sur le web les horaires du train",
            "explique-moi comment fonctionne un ordinateur quantique",
            "open the docs at https://example.com and summarise",
            "What is the weather in Paris?",
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertFalse(text_needs_computer(text))

    def test_very_long_prompts_are_not_scanned(self) -> None:
        self.assertFalse(text_needs_computer("clique sur le bouton " * 400))

    def test_route_resolves_only_the_computer_role(self) -> None:
        routes = {"computer": "claude-grounding", "vision": "cheap-vlm"}
        presets = {"claude-grounding", "cheap-vlm"}
        self.assertEqual(
            resolve_computer_route(
                "clique sur le bouton OK", routes=routes, known_presets=presets
            ),
            "claude-grounding",
        )
        # No computer route: nothing, even though a vision route exists.
        self.assertIsNone(
            resolve_computer_route(
                "clique sur le bouton OK", routes={"vision": "cheap-vlm"}, known_presets=presets
            )
        )
        # Not a desktop turn: nothing.
        self.assertIsNone(
            resolve_computer_route("résume ce PDF", routes=routes, known_presets=presets)
        )
        # And the vision route stays untouched by desktop wording.
        self.assertIsNone(
            resolve_vision_route([], text="clique sur le bouton OK", routes=routes)
        )

    def test_unknown_preset_is_ignored(self) -> None:
        self.assertIsNone(
            resolve_computer_route(
                "click on the Save button in Word",
                routes={"computer": "gone"},
                known_presets={"other"},
            )
        )


class LoopComputerRouteTest(unittest.TestCase):
    """``runtime_for_inbound`` picks the computer preset only when the tool is on."""

    @staticmethod
    def _loop(*, tool_on: bool) -> tuple[Any, Any, Any]:
        from unittest.mock import MagicMock

        loop = MagicMock()
        loop._computer_config_loader = None
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "claude-grounding"
        routed.model = "anthropic/claude-sonnet-4.5"
        loop.llm_runtime.return_value = default
        loop.model_presets = {"claude-grounding": object(), "flash": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None
        loop.tools = {"computer": object()} if tool_on else {}
        return loop, default, routed

    def _msg(self, content: str) -> Any:
        from navin.bus.events import InboundMessage

        return InboundMessage(
            channel="websocket", sender_id="u", chat_id="c", content=content, metadata={}
        )

    def test_desktop_turn_routes_to_the_computer_preset(self) -> None:
        from unittest.mock import patch

        from navin.agent.loop import AgentLoop

        loop, _default, routed = self._loop(tool_on=True)
        msg = self._msg("clique sur le bouton Enregistrer dans Excel")
        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"computer": "claude-grounding", "fast": "flash"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)
        loop.runtime_resolver.resolve_preset.assert_called_once_with("claude-grounding")
        self.assertIs(result, routed)
        self.assertEqual(msg.metadata.get("model_route_role"), "computer")

    def test_without_the_tool_the_same_words_do_not_route(self) -> None:
        from unittest.mock import patch

        from navin.agent.loop import AgentLoop

        loop, _default, _routed = self._loop(tool_on=False)
        msg = self._msg("clique sur le bouton Enregistrer dans Excel")
        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"computer": "claude-grounding"},
        ):
            AgentLoop.runtime_for_inbound(loop, msg)
        for call in loop.runtime_resolver.resolve_preset.call_args_list:
            self.assertNotEqual(call.args[0], "claude-grounding")

    def test_computer_wins_over_vision_for_a_desktop_turn_with_a_screenshot(self) -> None:
        from unittest.mock import patch

        from navin.agent.loop import AgentLoop
        from navin.bus.events import InboundMessage

        loop, _default, _routed = self._loop(tool_on=True)
        loop.model_presets["cheap-vlm"] = object()
        msg = InboundMessage(
            channel="websocket",
            sender_id="u",
            chat_id="c",
            content="click on the Save button in Word",
            media=["/tmp/shot.png"],
            metadata={},
        )
        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"computer": "claude-grounding", "vision": "cheap-vlm"},
        ):
            AgentLoop.runtime_for_inbound(loop, msg)
        loop.runtime_resolver.resolve_preset.assert_called_once_with("claude-grounding")


class NativeToolRegistryTest(unittest.TestCase):
    def tearDown(self) -> None:
        unregister_native_tool("anthropic", "widget")

    def test_register_lookup_and_betas(self) -> None:
        self.assertIsNone(native_tool_spec("anthropic", "widget"))
        register_native_tool(
            "Anthropic",
            "widget",
            lambda: NativeToolSpec({"type": "widget_1", "name": "widget"}, beta="widget-beta"),
        )
        spec = native_tool_spec("anthropic", "widget")
        assert spec is not None
        self.assertEqual(spec.definition["type"], "widget_1")
        tools = [
            {"type": "function", "function": {"name": "widget", "parameters": {}}},
            {"type": "function", "function": {"name": "other", "parameters": {}}},
            {"type": "function", "function": {"name": "widget", "parameters": {}}},
        ]
        self.assertEqual(native_betas("anthropic", tools), ["widget-beta"])
        self.assertEqual(native_betas("openai", tools), [])
        self.assertTrue(unregister_native_tool("anthropic", "widget"))
        self.assertFalse(unregister_native_tool("anthropic", "widget"))

    def test_broken_factory_falls_back_to_schema(self) -> None:
        def boom() -> NativeToolSpec:
            raise RuntimeError("no display")

        register_native_tool("anthropic", "widget", boom)
        self.assertIsNone(native_tool_spec("anthropic", "widget"))

    def test_merge_beta_header(self) -> None:
        self.assertEqual(merge_beta_header(None, ["a"]), "a")
        self.assertEqual(merge_beta_header("x, y", ["y", "z"]), "x,y,z")
        self.assertEqual(merge_beta_header("", []), "")


def _png(width: int, height: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Backend(ComputerBackend):
    name = "fake"
    label = "Fake"

    def __init__(self, width: int = 2560, height: int = 1440) -> None:
        self.width, self.height = width, height
        self._png = _png(width, height)

    def screen(self) -> ScreenInfo:
        return ScreenInfo(width=self.width, height=self.height)

    def screenshot(self) -> Screenshot:
        return Screenshot(png=self._png, width=self.width, height=self.height)

    def cursor_position(self) -> tuple[int, int]:
        return (0, 0)

    def move(self, x: int, y: int) -> None:
        pass

    def button(self, x: int, y: int, button: str, *, down: bool) -> None:
        pass

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        pass

    def scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        pass

    def type_text(self, text: str, *, delay_ms: int = 8) -> None:
        pass

    def key(self, combo: Any, *, down: bool | None = None) -> None:
        pass

    def windows(self) -> list[WindowInfo]:
        return []

    def focus_window(self, window_id: str) -> bool:
        return False

    def snapshot(self, window_id: str | None = None, *, limit: int = 300) -> list[Any]:
        return []


def _ctx(model: str) -> RequestContext:
    class _Runtime:
        def __init__(self) -> None:
            self.model = model

    return RequestContext(
        channel="test", chat_id="c1", session_key="test:native", runtime=_Runtime(), turn_id="t"
    )


def _config(**overrides: Any) -> ComputerToolConfig:
    base: dict[str, Any] = dict(
        enabled=True,
        ask="never",
        settle_ms=0,
        audit_log=False,
        audit_screenshots=False,
        live_view=False,
    )
    base.update(overrides)
    return ComputerToolConfig(**base)


class AnthropicNativeToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _SESSIONS.clear()
        unregister_native_tool("anthropic", "computer")

    def tearDown(self) -> None:
        _SESSIONS.clear()
        unregister_native_tool("anthropic", "computer")

    def test_off_by_default_keeps_the_json_schema(self) -> None:
        from navin.providers.anthropic_provider import AnthropicProvider

        tool = ComputerTool(config=_config(), backend_factory=_Backend)
        self.assertIsNone(native_tool_spec("anthropic", "computer"))
        converted = AnthropicProvider._convert_tools([tool.to_schema()])
        assert converted is not None
        self.assertEqual(converted[0]["name"], "computer")
        self.assertIn("input_schema", converted[0])
        self.assertNotIn("display_width_px", converted[0])
        self.assertEqual(native_betas("anthropic", [tool.to_schema()]), [])

    async def test_native_spec_declares_the_scaled_display(self) -> None:
        from navin.providers.anthropic_provider import AnthropicProvider

        tool = ComputerTool(
            config=_config(anthropic_native=True, display=":7"), backend_factory=_Backend
        )
        with request_context(_ctx("claude-sonnet-4.5")):
            # Before any screenshot the configured box is declared.
            spec = native_tool_spec("anthropic", "computer")
            assert spec is not None
            self.assertEqual(spec.definition["type"], "computer_20250124")
            self.assertEqual(spec.beta, "computer-use-2025-01-24")
            self.assertEqual(
                (spec.definition["display_width_px"], spec.definition["display_height_px"]),
                (1366, 768),
            )
            # After a screenshot the exact model-space size is declared.
            await tool.execute(action="screenshot")
            spec = native_tool_spec("anthropic", "computer")
            assert spec is not None
            self.assertEqual(
                (spec.definition["display_width_px"], spec.definition["display_height_px"]),
                (1365, 768),  # 2560x1440 scaled by 768/1440, width rounds down
            )
            converted = AnthropicProvider._convert_tools(
                [{**tool.to_schema(), "cache_control": {"type": "ephemeral"}}]
            )
            assert converted is not None
            entry = converted[0]
            self.assertEqual(entry["type"], "computer_20250124")
            self.assertEqual(entry["name"], "computer")
            self.assertNotIn("input_schema", entry)
            self.assertEqual(entry["cache_control"], {"type": "ephemeral"})
            if entry.get("display_number") is not None:
                self.assertEqual(entry["display_number"], 7)
            self.assertEqual(
                native_betas("anthropic", [tool.to_schema()]), ["computer-use-2025-01-24"]
            )

    async def test_native_display_tracks_a_non_16_9_screen(self) -> None:
        tool = ComputerTool(
            config=_config(anthropic_native=True),
            backend_factory=lambda: _Backend(2000, 1000),
        )
        with request_context(_ctx("claude-sonnet-4.5")):
            await tool.execute(action="screenshot")
            spec = native_tool_spec("anthropic", "computer")
            assert spec is not None
            self.assertEqual(
                (spec.definition["display_width_px"], spec.definition["display_height_px"]),
                (1366, 683),
            )

    def test_build_kwargs_adds_the_beta_header_next_to_existing_ones(self) -> None:
        from navin.providers.anthropic_provider import AnthropicProvider

        tool = ComputerTool(config=_config(anthropic_native=True), backend_factory=_Backend)
        provider = AnthropicProvider(
            api_key="sk-test", extra_headers={"anthropic-beta": "already-there"}
        )
        kwargs = provider._build_kwargs(
            messages=[{"role": "user", "content": "clique sur OK"}],
            tools=[tool.to_schema()],
            model="claude-sonnet-4.5",
            max_tokens=512,
            temperature=0.2,
            reasoning_effort=None,
            tool_choice=None,
            supports_caching=False,
        )
        self.assertEqual(kwargs["tools"][0]["type"], "computer_20250124")
        self.assertEqual(
            kwargs["extra_headers"]["anthropic-beta"],
            "already-there,computer-use-2025-01-24",
        )

    def test_toggling_native_off_unregisters(self) -> None:
        ComputerTool(config=_config(anthropic_native=True), backend_factory=_Backend)
        self.assertIsNotNone(native_tool_spec("anthropic", "computer"))
        ComputerTool(config=_config(anthropic_native=False), backend_factory=_Backend)
        self.assertIsNone(native_tool_spec("anthropic", "computer"))


class SettingsApiComputerTest(unittest.TestCase):
    """Settings > Agent desktop reads and writes ``tools.computer``."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.config.loader import get_config_path, set_config_path

        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")

    def tearDown(self) -> None:
        from navin.config.loader import set_config_path

        set_config_path(self._previous_path)
        self._tmp.cleanup()

    def test_payload_and_update_round_trip(self) -> None:
        from navin.config.loader import load_config, save_config
        from navin.config.schema import Config
        from navin.webui.settings_api import (
            WebUISettingsError,
            settings_payload,
            update_agent_settings,
        )

        save_config(Config())
        section = settings_payload()["computer"]
        self.assertTrue(section["enabled"])
        self.assertEqual(section["ask"], "never")
        self.assertEqual(section["session_mode"], "shared")
        self.assertIn("1Password", section["protected_apps"])
        self.assertEqual(section["route_preset"], "")

        payload = update_agent_settings(
            {
                "computer_enabled": ["true"],
                "computer_ask": ["always"],
                "computer_session_mode": ["dedicated"],
                "computer_live_view": ["false"],
                "computer_audit_log": ["false"],
                "computer_anthropic_native": ["true"],
            }
        )
        # Registration is refreshed before the next agent turn.
        self.assertFalse(payload.get("requires_restart"))
        reloaded = load_config().tools.computer
        self.assertTrue(reloaded.enabled)
        self.assertEqual(reloaded.ask, "always")
        self.assertEqual(reloaded.session_mode, "dedicated")
        self.assertFalse(reloaded.live_view)
        self.assertFalse(reloaded.audit_log)
        self.assertTrue(reloaded.anthropic_native)
        self.assertTrue(payload["computer"]["anthropic_native"])

        # A policy knob alone does not need a restart.
        payload = update_agent_settings({"computer_ask": ["never"]})
        self.assertFalse(payload.get("requires_restart"))
        self.assertEqual(load_config().tools.computer.ask, "never")

        with self.assertRaises(WebUISettingsError):
            update_agent_settings({"computer_ask": ["sometimes"]})
        with self.assertRaises(WebUISettingsError):
            update_agent_settings({"computer_session_mode": ["shared-ish"]})


class GroundingWarningTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _SESSIONS.clear()
        self.tool = ComputerTool(config=_config(), backend_factory=_Backend)

    def tearDown(self) -> None:
        _SESSIONS.clear()

    @staticmethod
    def _text(result: Any) -> str:
        assert isinstance(result, list), result
        return " ".join(b.get("text", "") for b in result if b.get("type") == "text")

    async def test_grounding_model_gets_no_warning(self) -> None:
        with request_context(_ctx("anthropic/claude-sonnet-4.5")):
            text = self._text(await self.tool.execute(action="screenshot"))
        self.assertNotIn("If no image appears", text)
        self.assertNotIn("coordinate accuracy is unverified", text)

    async def test_vision_only_model_is_told_to_prefer_refs(self) -> None:
        with request_context(_ctx("meta-llama/llama-3.2-11b-vision-instruct")):
            text = self._text(await self.tool.execute(action="screenshot"))
        self.assertIn("coordinate accuracy is unverified", text)
        self.assertNotIn("If no image appears", text)

    async def test_status_reports_model_capability(self) -> None:
        with request_context(_ctx("deepseek/deepseek-chat")):
            status = await self.tool.execute(action="status")
        self.assertIn("no vision", str(status))
        with request_context(_ctx("openai/gpt-5")):
            status = await self.tool.execute(action="status")
        self.assertIn("vision + grounding", str(status))


if __name__ == "__main__":
    unittest.main()
