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
        self.assertEqual(restored.reasoning_details()[0], "low")  # the default for a chat that chose none
        await restored.switch_session("cli:direct")
        restored.set_model_preset("minimax")
        self.assertEqual(restored.reasoning_details(), ("", (("", "Auto"),)))
        with self.assertRaises(ValueError):
            restored.set_reasoning_effort("high")
        restored.set_model_preset("default")
        self.assertEqual(restored.reasoning_details()[0], "xhigh")
        restored.set_reasoning_effort("")
        self.assertEqual(restored.reasoning_details()[0], "")
        self.assertEqual(adaptive_reasoning_effort("high", override=""), "medium")

    async def test_the_picked_model_survives_a_session_switch_and_a_reopen(self):
        runtime = self.runtime
        runtime.set_model_preset("minimax")
        await runtime.switch_session("cli:another")
        self.assertEqual(runtime.status.model_preset, "minimax", "a chat without its own pick keeps the model")
        await runtime.switch_session("cli:direct")
        self.assertEqual(runtime.status.model_preset, "minimax")
        reopened = runtime_for(self.config)
        reopened._adopt_session_model_pin()
        reopened._refresh_status()
        self.assertEqual(reopened.status.model_preset, "minimax", "closing and reopening the CLI keeps it")

    async def test_back_to_default_drops_the_chat_pin_and_a_stale_pin_is_ignored(self):
        from navin.agent.loop import SESSION_MODEL_PIN_KEY

        runtime = self.runtime
        runtime.set_model_preset("minimax")
        runtime.set_model_preset("default")
        session = runtime.agent_loop.sessions.get_or_create(runtime.session_key)
        self.assertNotIn(SESSION_MODEL_PIN_KEY, session.metadata)
        session.metadata[SESSION_MODEL_PIN_KEY] = "image"  # a media model cannot drive the chat
        runtime._adopt_session_model_pin()
        runtime._refresh_status()
        self.assertEqual(runtime.status.model_preset, "default")

    async def test_media_and_hidden_models_cannot_replace_chat(self):
        for name in ("image", "video", "audio", "hidden", "missing"):
            with self.assertRaises(ValueError):
                self.runtime.set_model_preset(name)

    async def test_default_is_listed_once_even_when_the_agent_carries_it(self):
        from navin.agent.model_presets import configured_model_presets

        self.runtime.agent_loop.model_presets = configured_model_presets(self.runtime.config)
        names = [row["name"] for row in self.runtime.preset_details()]
        self.assertEqual(names.count("default"), 1)

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

    async def test_applying_a_preset_adds_no_model_preset_note(self):
        # Regression: changing the model used to append a persistent
        # "model preset → ..." note at the bottom of the transcript.
        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause()
            await app._apply_preset("minimax")
            await pilot.pause()
            self.assertEqual(app.runtime.status.model_preset, "minimax")
            texts = " ".join(
                str(getattr(widget, "renderable", "") or "")
                for widget in app.query("SystemNote")
            )
            self.assertNotIn("model preset", texts)

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

    async def test_ctrl_s_and_the_sessions_bar_open_and_close_the_projects_column(self):
        from navin.tui.widgets import ProjectsSidebar

        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None  # no disk scan in this test
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            column = app.query_one(ProjectsSidebar)
            # wide enough for two columns and never chosen: open by default
            self.assertTrue(column.has_class("-visible"))
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertFalse(column.has_class("-visible"))
            self.assertTrue(prefs.projects_panel_explicit)
            self.assertFalse(prefs.projects_panel)
            # the button sits in the bottom line next to the path, not above the chat
            self.assertFalse(app.query("#sessions-bar"))
            button = app.query_one("#dock #dock-sessions")
            self.assertIn("Sessions", str(button.content))
            await pilot.click("#dock-sessions")
            await pilot.pause()
            self.assertTrue(column.has_class("-visible"))
            self.assertTrue(prefs.projects_panel)
            self.assertIn("hide sessions", str(button.content))
            # once chosen, a resize no longer overrides the user's choice
            await pilot.resize_terminal(90, 36)
            await pilot.pause()
            self.assertTrue(column.has_class("-visible"))

    async def test_ctrl_s_and_ctrl_p_share_the_right_side_one_column_at_a_time(self):
        from navin.tui.widgets import ProjectsSidebar, Sidebar

        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None
        self.assertEqual(app.COMMAND_PALETTE_BINDING, "ctrl+shift+p")
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sessions = app.query_one(ProjectsSidebar)
            panel = app.query_one(Sidebar)
            # sessions open by default, on the right of the chat
            row = list(app.query_one("#main").children)
            self.assertLess(row.index(app.query_one("#column")), row.index(sessions))
            self.assertTrue(sessions.has_class("-visible"))
            self.assertFalse(panel.has_class("-visible"))
            # ctrl+p: the panel takes the sessions' place
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertTrue(panel.has_class("-visible"))
            self.assertFalse(sessions.has_class("-visible"))
            self.assertTrue(prefs.sidebar)
            self.assertFalse(prefs.projects_panel)
            # ctrl+s: back to the sessions, the panel closes
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertTrue(sessions.has_class("-visible"))
            self.assertFalse(panel.has_class("-visible"))
            self.assertFalse(prefs.sidebar)
            # the hide button of the sessions column hides it
            await pilot.click("#projects-hide")
            await pilot.pause()
            self.assertFalse(sessions.has_class("-visible"))
            self.assertFalse(panel.has_class("-visible"))
            # ctrl+b still opens the panel, and ctrl+p hides it again
            await pilot.press("ctrl+b")
            await pilot.pause()
            self.assertTrue(panel.has_class("-visible"))
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertFalse(panel.has_class("-visible"))
            self.assertFalse(sessions.has_class("-visible"))

    async def _reopen(self, prefs):
        from navin.tui.widgets import ProjectsSidebar, Sidebar

        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None
        async with app.run_test(size=(90, 36)) as pilot:
            await pilot.pause()
            return app.query_one(ProjectsSidebar).has_class("-visible"), app.query_one(Sidebar).has_class("-visible")

    async def test_a_reopened_cli_always_shows_the_sessions(self):
        # Closed with the sessions hidden, or with the panel open: the next
        # start shows the sessions again, and the panel waits for ctrl+p.
        for saved in (
            TuiPrefs(sidebar=False, projects_panel=False, projects_panel_explicit=True),
            TuiPrefs(sidebar=True, sidebar_explicit=True, projects_panel=False, projects_panel_explicit=True),
        ):
            saved.save = lambda: None
            with self.subTest(sidebar=saved.sidebar):
                self.assertEqual(await self._reopen(saved), (True, False))
                self.assertTrue(saved.projects_panel)
                self.assertFalse(saved.sidebar)

    async def test_a_terminal_too_narrow_for_two_columns_keeps_the_chat_whole(self):
        from navin.tui.widgets import ProjectsSidebar

        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None
        async with app.run_test(size=(72, 30)) as pilot:
            await pilot.pause()
            column = app.query_one(ProjectsSidebar)
            # 72 columns: the sessions would leave the chat 35 and push its buttons off
            self.assertFalse(column.has_class("-visible"))
            await pilot.resize_terminal(120, 30)
            await pilot.pause()
            self.assertTrue(column.has_class("-visible"))
            await pilot.resize_terminal(72, 30)
            await pilot.pause()
            self.assertFalse(column.has_class("-visible"))
            # ctrl+s still opens them on demand, and a resize then leaves them
            await pilot.press("ctrl+s")
            await pilot.resize_terminal(80, 30)
            await pilot.pause()
            self.assertTrue(column.has_class("-visible"))

    async def test_sessions_show_by_default_even_on_a_narrow_terminal(self):
        from navin.tui.widgets import ProjectsSidebar

        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None
        async with app.run_test(size=(90, 36)) as pilot:
            await pilot.pause()
            column = app.query_one(ProjectsSidebar)
            self.assertTrue(column.has_class("-visible"))
            # hidden by the user: it stays hidden for this run, whatever the width
            await pilot.press("ctrl+s")
            await pilot.resize_terminal(130, 36)
            await pilot.pause()
            self.assertFalse(column.has_class("-visible"))

    async def _project_host(self):
        prefs = TuiPrefs(sidebar=False)
        prefs.save = lambda: None
        app = ChatHost(self.config, prefs=prefs)
        app._load_config_data = lambda: self.config.model_dump(mode="json", by_alias=True)
        app._reload_projects = lambda *args, **kwargs: None
        app._load_engine_commands = lambda: None
        switched: list[str] = []

        async def switch_session(key):
            switched.append(key)

        app._switch_session = switch_session
        return app, switched

    async def test_opening_a_session_of_another_project_keeps_the_cli_open(self):
        from unittest.mock import patch

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        app, switched = await self._project_host()
        moved: list[tuple[str, str]] = []

        async def switch_workspace(path, key):
            moved.append((str(path), key))
            app.runtime.config.agents.defaults.workspace = str(path)

        app.runtime.switch_workspace = switch_workspace
        with patch("navin.tui.projects.register_project") as register:
            async with app.run_test(size=(120, 36)) as pilot:
                await pilot.pause()
                await app._open_project_session(other.name, "cli:42")
                await pilot.pause()
                # the engine moved, the CLI never exited
                self.assertEqual(moved, [(other.name, "cli:42")])
                self.assertEqual(switched, ["cli:42"])
                self.assertTrue(app.is_running)
                self.assertTrue(app._engine_ready)
                self.assertEqual(Path(app.project_root), Path(other.name))
                self.assertEqual(app.prefs.project_root, str(app.project_root))
                register.assert_called_once_with(str(app.project_root))
                # another session of the same project: no engine move at all
                await app._open_project_session(other.name, "cli:43")
                self.assertEqual(len(moved), 1)
                self.assertEqual(switched, ["cli:42", "cli:43"])

    async def test_a_project_that_fails_to_open_leaves_the_cli_on_the_current_one(self):
        from navin.tui.runtime import WorkspaceSwitchError
        from navin.tui.widgets import SystemNote

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        app, switched = await self._project_host()

        async def switch_workspace(path, key):
            raise WorkspaceSwitchError(f"Could not open {path}: boom")

        app.runtime.switch_workspace = switch_workspace
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            before = Path(app.project_root)
            await app._open_project_session(other.name, "cli:42")
            await pilot.pause()
            self.assertTrue(app.is_running)
            self.assertTrue(app._engine_ready)
            self.assertEqual(Path(app.project_root), before)
            self.assertEqual(switched, [])
            notes = " ".join(str(note.content) for note in app.query(SystemNote))
            self.assertIn("boom", notes)


class WorkspaceSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os

        self.home = tempfile.TemporaryDirectory()
        self.other = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.addCleanup(self.other.cleanup)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        self.config = model_config(Path(self.home.name))
        self.runtime = runtime_for(self.config)
        self.runtime.agent_loop.model_preset = "claude"
        self.runtime.close = AsyncMock()
        self.runtime._refresh_status = lambda: None
        self.starts: list[str] = []

    def _start(self, fail_on: str | None = None):
        async def start():
            folder = str(self.runtime.config.agents.defaults.workspace)
            self.starts.append(folder)
            if fail_on and Path(folder) == Path(fail_on).resolve():
                raise ImportError("engine build failed")
            self.runtime.agent_loop = SimpleNamespace(model_preset=None)

        return start

    async def test_the_engine_is_rebuilt_on_the_new_folder_in_this_process(self):
        import os

        self.runtime.start = self._start()
        await self.runtime.switch_workspace(self.other.name, "cli:7")
        target = Path(self.other.name).resolve()
        self.assertEqual(self.starts, [str(target)])
        self.runtime.close.assert_awaited_once()
        self.assertEqual(Path(self.runtime.workspace), target)
        self.assertEqual(Path(os.getcwd()), target)
        self.assertEqual(self.runtime.session_key, "cli:7")
        self.assertEqual(self.runtime.status.session_key, "cli:7")
        self.assertEqual(self.runtime.agent_loop.model_preset, "claude")  # the chosen model follows

    async def test_a_failing_folder_brings_the_engine_back_on_the_previous_one(self):
        import os

        from navin.tui.runtime import WorkspaceSwitchError

        self.runtime.start = self._start(fail_on=self.other.name)
        before = self.runtime.session_key
        with self.assertRaises(WorkspaceSwitchError) as caught:
            await self.runtime.switch_workspace(self.other.name, "cli:7")
        self.assertIn("engine build failed", str(caught.exception))
        self.assertEqual(Path(self.starts[-1]), Path(self.home.name))
        self.assertEqual(Path(self.runtime.workspace).resolve(), Path(self.home.name).resolve())
        self.assertEqual(Path(os.getcwd()).resolve(), Path(self.home.name).resolve())
        self.assertEqual(self.runtime.session_key, before)
        self.assertIsNotNone(self.runtime.agent_loop)

    async def test_a_missing_folder_is_refused_before_the_engine_stops(self):
        from navin.tui.runtime import WorkspaceSwitchError

        self.runtime.start = self._start()
        with self.assertRaises(WorkspaceSwitchError):
            await self.runtime.switch_workspace(Path(self.other.name) / "gone", "cli:7")
        self.runtime.close.assert_not_awaited()
        self.assertEqual(self.starts, [])

    def test_running_work_counts_turns_and_subagents(self):
        done, live = Mock(), Mock()
        done.done.return_value, live.done.return_value = True, False
        self.runtime.agent_loop = SimpleNamespace(
            _active_tasks={"cli:1": [live, done]},
            subagents=SimpleNamespace(_running_tasks={"a": live, "b": done}),
        )
        self.assertEqual(self.runtime.running_work(), 2)
        self.runtime.agent_loop = SimpleNamespace()
        self.assertEqual(self.runtime.running_work(), 0)
