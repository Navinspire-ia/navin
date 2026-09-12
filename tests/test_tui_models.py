# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exercise model selection, native effort and persisted task routing."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from textual.app import App
from textual.widgets import Input, OptionList, Select

from navin.agent.adaptive_reasoning import adaptive_reasoning_effort
from navin.config.schema import Config, ModelPresetConfig
from navin.session.manager import SessionManager
from navin.tui.app import NavinApp
from navin.tui.models import ModelFormScreen, ModelPickerScreen, model_pick_items, reasoning_options
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import TuiRuntime
from navin.tui.screens import _SELECT_BLANK, FormField, PickerScreen
from navin.tui.settings import SettingsHub, _route_options
from navin.tui.theme import NAVIN_DARK


def model_config(workspace: Path) -> Config:
    config = Config()
    config.agents.defaults.workspace = str(workspace)
    config.agents.defaults.provider = "openai"
    config.agents.defaults.model = "gpt-5.4"
    config.model_presets = {
        "claude": ModelPresetConfig(model="claude-sonnet-4-6", provider="anthropic", label="Sonnet"),
        "gpt": ModelPresetConfig(model="gpt-5.4", provider="openai", label="GPT"),
        "gateway": ModelPresetConfig(model="gpt-5.4", provider="openrouter", label="GPT via OpenRouter"),
        "minimax": ModelPresetConfig(model="MiniMax-M3", provider="minimax", label="MiniMax"),
        "image": ModelPresetConfig(model="gpt-image-1", provider="openai", modality="image"),
        "video": ModelPresetConfig(model="veo-3", provider="gemini", modality="video"),
        "audio": ModelPresetConfig(model="tts-1", provider="openai", modality="audio"),
        "hidden": ModelPresetConfig(model="hidden-model", enabled=False),
    }
    return config


def runtime_for(config: Config) -> TuiRuntime:
    runtime = TuiRuntime(config, on_event=AsyncMock())
    runtime.agent_loop = SimpleNamespace(
        sessions=SessionManager(config.workspace_path), model_presets=config.model_presets,
        model_preset=None, model=config.agents.defaults.model,
    )
    return runtime


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self.initial_screen = screen
        self.result = None
        self.register_theme(NAVIN_DARK)
        self.theme = "navin"

    def on_mount(self):
        self.push_screen(self.initial_screen, self.received)

    def received(self, result):
        self.result = result


class ChatHost(NavinApp):
    """Real application chrome with a local session store and no network startup."""

    async def on_mount(self, event):
        event.prevent_default()
        self.runtime.agent_loop = runtime_for(self.config).agent_loop
        self._engine_ready = True
        self.runtime._refresh_status()
        self._set_status()
        self.composer.focus()

    async def on_unmount(self, event):
        event.prevent_default()
        self.runtime._closed = True


class ModelRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = model_config(Path(self.directory.name))
        self.runtime = runtime_for(self.config)

    async def test_effort_is_sent_and_restored_per_chat_and_model(self):
        runtime = self.runtime
        runtime.set_reasoning_effort("xhigh")
        runtime.bus = SimpleNamespace(publish_inbound=AsyncMock())
        runtime._ensure_tasks = Mock()
        await runtime.send("Explain this code")
        message = runtime.bus.publish_inbound.call_args.args[0]
        self.assertEqual(message.metadata["reasoning_effort"], "xhigh")
        self.assertEqual(adaptive_reasoning_effort(None, override=message.metadata["reasoning_effort"]), "xhigh")
        restored = runtime_for(self.config)
        self.assertEqual(restored.reasoning_details()[0], "xhigh")
        await restored.switch_session("cli:another")
        self.assertEqual(restored.reasoning_details()[0], "")
        await restored.switch_session("cli:direct")
        restored.set_model_preset("minimax")
        self.assertEqual(restored.reasoning_details(), ("", (("", "Auto"),)))
        with self.assertRaises(ValueError):
            restored.set_reasoning_effort("high")
        restored.set_model_preset("default")
        self.assertEqual(restored.reasoning_details()[0], "xhigh")
        restored.set_reasoning_effort("")
        self.assertEqual(restored.reasoning_details()[0], "")
        self.assertEqual(adaptive_reasoning_effort("high", override=""), "none")

    async def test_media_and_hidden_models_cannot_replace_chat(self):
        for name in ("image", "video", "audio", "hidden", "missing"):
            with self.assertRaises(ValueError):
                self.runtime.set_model_preset(name)

    async def test_provider_groups_capabilities_and_media_order(self):
        items = model_pick_items(self.runtime.preset_details())
        self.assertNotIn("hidden", [item.id for item in items])
        self.assertEqual([item.id for item in items][-3:], ["image", "video", "audio"])
        by_id = {item.id: item for item in items}
        self.assertIn("Multimodal", by_id["claude"].badge)
        self.assertIn("OpenAI", by_id["gpt"].group)
        self.assertIn("OpenRouter", by_id["gateway"].group)
        self.assertNotEqual(by_id["gpt"].group, by_id["gateway"].group)

    async def test_grouped_picker_search_and_enter_select_the_correct_provider(self):
        screen = ModelPickerScreen("Models", model_pick_items(self.runtime.preset_details()), current="gpt")
        app = Host(screen)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            self.assertEqual(screen._highlighted_item().id, "gpt")
            await pilot.press(*"openrouter")
            self.assertEqual(screen._highlighted_item().id, "gateway")
            options = screen.query_one("#options", OptionList)
            self.assertTrue(options.get_option_at_index(0).disabled)
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.result, "gateway")

    async def test_picker_routing_button_works(self):
        screen = ModelPickerScreen("Models", model_pick_items(self.runtime.preset_details()))
        app = Host(screen)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.click("#model-routing")
            self.assertEqual(app.result, "__settings__:routing")

    async def test_model_form_refreshes_native_effort_when_model_changes(self):
        screen = ModelFormScreen("Model", [
            FormField("model", "Model", value="gpt-5.4"),
            FormField("provider", "Provider", kind="select", value="openai",
                      options=(("openai", "OpenAI"), ("minimax", "MiniMax"))),
            FormField("reasoning_effort", "Reasoning", kind="select", value="xhigh",
                      options=reasoning_options("openai", "gpt-5.4")),
        ])
        app = Host(screen)
        async with app.run_test(size=(80, 30)) as pilot:
            effort = screen.query_one("#f-reasoning_effort", Select)
            self.assertEqual(effort.value, "xhigh")
            screen.query_one("#f-model", Input).value = "MiniMax-M3"
            screen.query_one("#f-provider", Select).value = "minimax"
            await pilot.pause()
            self.assertEqual(effort.value, _SELECT_BLANK)
            await pilot.click("#ok")
            self.assertEqual(app.result["reasoning_effort"], "")

    async def test_routing_is_visible_selectable_and_saved(self):
        data = self.config.model_dump(mode="json", by_alias=True)
        saved = []

        def save(value):
            saved.append(Config.model_validate(copy.deepcopy(value)))

        screen = SettingsHub(lambda: copy.deepcopy(data), save,
                             config_label="test config", project_root=Path(self.directory.name),
                             workspace=self.directory.name, version="test", start="routing")
        app = Host(screen)
        async with app.run_test(size=(110, 38)) as pilot:
            await pilot.pause()
            self.assertEqual(screen._section.id, "routing")
            self.assertNotIn("image", dict(_route_options(data)))
            self.assertNotIn("hidden", dict(_route_options(data)))
            screen.query_one("#rows", OptionList).focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertIsInstance(app.screen, PickerScreen)
            await pilot.press(*"Sonnet", "enter")
            await pilot.pause()
            await pilot.press("ctrl+s")
            self.assertEqual(saved[-1].model_routes["deep"], "claude")

    async def test_chat_reasoning_click_model_shortcut_and_routing_navigation(self):
        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.click("#meta-reasoning")
            await pilot.pause()
            self.assertIsInstance(app.screen, PickerScreen)
            await pilot.press(*"medium", "enter")
            await pilot.pause()
            self.assertEqual(app.runtime.reasoning_details()[0], "medium")
            await pilot.press("ctrl+o")
            await pilot.pause()
            self.assertIsInstance(app.screen, ModelPickerScreen)
            await pilot.click("#model-routing")
            await pilot.pause()
            self.assertIsInstance(app.screen, SettingsHub)
            self.assertEqual(app.screen._section.id, "routing")
