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

    async def test_switching_project_while_work_runs_asks_nothing_and_says_it_goes_on(self):
        from unittest.mock import patch

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        app, switched = await self._project_host()
        moved: list[tuple[str, str]] = []

        async def switch_workspace(path, key):
            moved.append((str(path), key))
            app.runtime.config.agents.defaults.workspace = str(path)

        asked: list[tuple] = []

        async def confirm(*args):
            asked.append(args)
            return False

        app.runtime.switch_workspace = switch_workspace
        home = str(app.runtime.workspace)
        # the project left keeps two sessions working in the background
        app.runtime.background_projects = lambda: [home]
        app.runtime.running_sessions = lambda: {home: {"cli:1", "cli:2"}}
        app._confirm = confirm
        with patch("navin.tui.projects.register_project"):
            async with app.run_test(size=(120, 36)) as pilot:
                await pilot.pause()
                app._flash = AsyncMock()
                await app._open_project_session(other.name, "cli:42")
                await pilot.pause()
                self.assertEqual(asked, [])  # no "stop it and switch" question any more
                self.assertEqual(moved, [(other.name, "cli:42")])
                self.assertEqual(switched, ["cli:42"])
                said = " ".join(str(call.args[0]) for call in app._flash.await_args_list)
                self.assertIn("keeps working in the background", said)
                self.assertIn("2 sessions", said)

    async def test_the_session_menu_offers_fork_without_a_search_field(self):
        app, _switched = await self._project_host()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            path = str(app.runtime.workspace)
            app.push_screen_wait = AsyncMock(return_value="fork")
            app._fork_session = AsyncMock()
            await app._session_menu(path, "cli:1")
            menu = app.push_screen_wait.await_args.args[0]
            self.assertIn("fork", [item.id for item in menu._items])
            self.assertFalse(menu._searchable)
            app._fork_session.assert_awaited_once_with(path, "cli:1", "", app.runtime.agent_loop.sessions)

    async def test_fork_copies_the_chat_and_opens_the_copy(self):
        from navin.session.webui_turns import WEBUI_TITLE_METADATA_KEY

        app, _switched = await self._project_host()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sessions = app.runtime.agent_loop.sessions
            source = sessions.get_or_create("cli:1")
            source.messages += [
                {"role": "user", "content": "fix the login"},
                {"role": "assistant", "content": "fixed"},
            ]
            source.metadata["turn_recovery"] = {"status": "running"}
            sessions.save(source)
            path = str(app.runtime.workspace)
            app._open_project_session = AsyncMock()
            await app._fork_session(path, "cli:1", "Login fix", sessions)
            opened_path, new_key = app._open_project_session.await_args.args
            self.assertEqual(opened_path, path)
            self.assertNotEqual(new_key, "cli:1")
            fork = sessions.get_or_create(new_key)
            self.assertEqual([m["content"] for m in fork.messages], ["fix the login", "fixed"])
            self.assertEqual(fork.metadata[WEBUI_TITLE_METADATA_KEY], "Fork: Login fix")
            # the copy never replays the source's interrupted turn
            self.assertNotIn("turn_recovery", fork.metadata)
            self.assertEqual(len(sessions.get_or_create("cli:1").messages), 2)

    async def test_a_working_session_is_not_deleted(self):
        app, _switched = await self._project_host()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            path = str(app.runtime.workspace)
            sessions = app.runtime.agent_loop.sessions
            busy_chat = sessions.get_or_create("cli:9")
            busy_chat.messages.append({"role": "user", "content": "long job"})
            sessions.save(busy_chat)
            app.runtime.session_busy = lambda folder, key: key == "cli:9"
            app.push_screen_wait = AsyncMock(return_value="delete")
            app._confirm = AsyncMock(return_value=True)
            app._note = AsyncMock()
            await app._session_menu(path, "cli:9")
            app._confirm.assert_not_awaited()
            self.assertIn("still working", app._note.await_args.args[0])
            self.assertIn("cli:9", {row["key"] for row in sessions.list_sessions()})

    async def test_quitting_asks_first_when_other_projects_still_work(self):
        app, _switched = await self._project_host()
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            app.runtime.background_work = lambda: 3
            app.runtime.background_projects = lambda: ["/work/beta"]
            asked: list[tuple] = []
            answer = {"value": False}

            async def confirm(*args):
                asked.append(args)
                return answer["value"]

            app._confirm = confirm
            app.exit = Mock()
            app.runtime.close = AsyncMock()
            await app.action_quit()
            await pilot.pause()
            await pilot.pause()
            self.assertEqual(len(asked), 1)
            self.assertIn("3 tasks running in beta", asked[0][2])
            app.exit.assert_not_called()  # cancelled: the CLI and the work go on
            answer["value"] = True
            await app.action_quit()
            for _ in range(10):
                if app.exit.called:
                    break
                await pilot.pause()
            app.exit.assert_called_once()
            self.assertEqual(len(asked), 2)
            if app._runtime_close_task is not None:
                await app._runtime_close_task


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

    async def _task(self):
        import asyncio

        task = asyncio.create_task(asyncio.sleep(3600))
        self.addAsyncCleanup(self._cancel, task)
        return task

    @staticmethod
    async def _cancel(task):
        import asyncio
        import contextlib

        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _stop_reaper(self):
        await self._cancel(self.runtime._reaper_task)

    async def test_a_working_project_goes_on_in_the_background_and_comes_back_as_it_is(self):
        import os

        working = self.runtime.agent_loop
        turn = await self._task()
        working._active_tasks = {"cli:direct": [turn]}
        self.runtime.bus = SimpleNamespace(name="home bus")
        self.runtime._loop_task = await self._task()
        self.runtime._consumer_task = await self._task()
        self.runtime.start = self._start()
        await self.runtime.switch_workspace(self.other.name, "cli:7")
        self.addAsyncCleanup(self._stop_reaper)
        home = Path(self.home.name).resolve()
        # nothing was stopped: the turn still runs, the CLI shows the other project
        self.runtime.close.assert_not_awaited()
        self.assertFalse(turn.done())
        self.assertEqual(Path(self.runtime.workspace), Path(self.other.name).resolve())
        self.assertEqual([Path(p).resolve() for p in self.runtime.background_projects()], [home])
        busy = {Path(path).resolve(): keys for path, keys in self.runtime.running_sessions().items()}
        self.assertEqual(busy, {home: {"cli:direct"}})
        self.assertIs(self.runtime.sessions_for(self.home.name), working.sessions)
        parked = next(iter(self.runtime._parked.values()))
        self.assertEqual(Path(parked.config.workspace_path).resolve(), home)  # keeps its own folder
        # back on it: the same engine, not a new one, and the idle project is closed
        await self.runtime.switch_workspace(self.home.name, "cli:direct")
        self.assertIs(self.runtime.agent_loop, working)
        self.assertEqual(len(self.starts), 1)
        self.runtime.close.assert_awaited_once()
        self.assertEqual(self.runtime.background_projects(), [])
        self.assertEqual(Path(os.getcwd()).resolve(), home)
        self.assertEqual(self.runtime.session_key, "cli:direct")
        self.assertEqual(self.runtime.agent_loop.model_preset, "claude")

    async def test_a_background_project_closes_once_idle_for_a_while(self):
        from navin.tui import runtime as rt

        work = await self._task()
        loop = SimpleNamespace(_active_tasks={"cli:1": [work]}, sessions=SessionManager(Path(self.other.name)))
        self.runtime._parked[rt._workspace_id(self.other.name)] = rt._ParkedEngine(
            workspace=self.other.name, config=self.config, bus=None, agent_loop=loop,
            loop_task=None, consumer_task=None, unsubscribe=None,
        )
        self.assertEqual(await self.runtime.reap_idle_parked(now=0.0), [])  # still working
        await self._cancel(work)
        self.assertEqual(await self.runtime.reap_idle_parked(now=1.0), [])  # idle from now
        self.assertEqual(await self.runtime.reap_idle_parked(now=rt.PARKED_IDLE_CLOSE_S), [])
        self.assertEqual(await self.runtime.reap_idle_parked(now=1.0 + rt.PARKED_IDLE_CLOSE_S), [self.other.name])
        self.assertEqual(self.runtime.background_projects(), [])

    def test_busy_sessions_count_turns_running_and_queued_subagents(self):
        from navin.tui.runtime import busy_session_keys

        done, live = Mock(), Mock()
        done.done.return_value, live.done.return_value = True, False
        loop = SimpleNamespace(
            _active_tasks={"cli:1": [live], "cli:2": [done]},
            subagents=SimpleNamespace(
                _running_tasks={"a": live, "b": done},
                _session_tasks={"cli:3": {"a"}, "cli:4": {"b"}},
                _queued={"cli:5": [object()], "cli:6": []},
            ),
        )
        self.assertEqual(busy_session_keys(loop), {"cli:1", "cli:3", "cli:5"})
        self.assertEqual(busy_session_keys(None), set())

    async def test_a_session_off_screen_reports_its_card_and_its_end(self):
        import asyncio

        from navin.bus.events import OutboundMessage
        from navin.bus.outbound_events import ApprovalRequestedEvent
        from navin.bus.queue import MessageBus
        from navin.bus.runtime_events import RuntimeEventContext, TurnCompleted
        from navin.tui.runtime import UiBackgroundSession

        events: list = []
        self.runtime._on_event = events.append
        handlers: list = []
        self.runtime.agent_loop.runtime_events = SimpleNamespace(
            subscribe=lambda handler: handlers.append(handler) or (lambda: None)
        )
        self.runtime._subscribe_runtime_events()
        await handlers[0](TurnCompleted(RuntimeEventContext("cli", "9", "cli:9"), latency_ms=5))
        workspace = str(self.runtime.workspace)
        self.assertEqual(events, [UiBackgroundSession(workspace, "cli:9", "done")])
        # a project working in the background asks for an approval
        self.runtime.bus = MessageBus()
        parked = MessageBus()
        consumer = asyncio.create_task(self.runtime._consume_outbound(parked, "/work/beta"))
        self.addAsyncCleanup(self._cancel, consumer)
        await parked.publish_outbound(OutboundMessage(
            channel="cli", chat_id="3", content="",
            event=ApprovalRequestedEvent(request_id="r1", tool="exec", action="run tests", reason="why"),
        ))
        for _ in range(20):
            if len(events) > 1:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(events[1:], [UiBackgroundSession("/work/beta", "cli:3", "attention")])

    async def test_cards_still_waiting_are_drawn_again_when_their_session_opens(self):
        from navin.tui.runtime import UiApprovalRequested

        events: list = []
        self.runtime._on_event = events.append
        row = {
            "request_id": "r1", "tool": "exec", "action": "run tests", "reason": "why", "detail": "pytest",
            "consequence": "", "scope": "exec", "expires_at_ms": None, "remember_offered": True,
        }
        key = self.runtime.session_key
        self.runtime.agent_loop.approvals = SimpleNamespace(
            open_requests=lambda session_key=None: [row] if session_key in (None, key) else []
        )
        await self.runtime.replay_open_requests()
        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], UiApprovalRequested)
        self.assertEqual((events[0].request_id, events[0].detail), ("r1", "pytest"))
        self.assertTrue(events[0].remember_offered)

    def test_a_settings_reload_keeps_the_project_open_here(self):
        from unittest.mock import patch

        self.runtime.config.agents.defaults.workspace = self.other.name
        on_disk = model_config(Path(self.home.name))
        with patch("navin.config.loader.load_config", return_value=on_disk):
            self.runtime.reload_from_disk()
        self.assertEqual(Path(self.runtime.workspace), Path(self.other.name))
        with patch("navin.config.loader.load_config", return_value=model_config(Path(self.home.name))):
            self.runtime.apply_account_from_disk()
        self.assertEqual(Path(self.runtime.workspace), Path(self.other.name))

    async def test_a_project_switch_keeps_the_process_wide_services(self):
        import navin.license_sync as license_sync
        from navin.tui.runtime import TuiRuntime

        runtime = TuiRuntime(self.config, on_event=AsyncMock())
        runtime.agent_loop = SimpleNamespace(sessions=None)
        lag = SimpleNamespace(stop=AsyncMock())
        runtime._loop_lag, runtime._license_sync = lag, object()
        stop = Mock()
        original = license_sync.stop_license_sync
        license_sync.stop_license_sync = stop
        try:
            await runtime.close(keep_parked=True)
            lag.stop.assert_not_awaited()
            stop.assert_not_called()
            self.assertIs(runtime._loop_lag, lag)
            runtime._closed = False
            await runtime.close()  # quitting stops them
            lag.stop.assert_awaited_once()
            stop.assert_called_once()
        finally:
            license_sync.stop_license_sync = original

    async def test_the_turn_log_merges_deltas_and_never_changes_what_was_drawn(self):
        from navin.bus.events import OutboundMessage
        from navin.bus.outbound_events import StreamDeltaEvent
        from navin.tui import runtime as rt

        def delta(text):
            return OutboundMessage(channel="cli", chat_id="1", content=text, event=StreamDeltaEvent(text, "s"))

        workspace = self.home.name
        key = (rt._workspace_id(workspace), "cli:1")
        self.runtime._log_turn(delta("Hel"), workspace)
        self.runtime._log_turn(delta("lo"), workspace)
        self.assertEqual([m.content for m in self.runtime._replay[key]], ["Hello"])
        self.runtime._sealed[key] = 1  # "Hello" is being drawn
        self.runtime._log_turn(delta(" world"), workspace)
        self.assertEqual([m.content for m in self.runtime._replay[key]], ["Hello", " world"])
        self.runtime._sealed.pop(key)
        self.runtime._log_turn(OutboundMessage(channel="system", chat_id="x", content="ignored"), workspace)
        self.assertEqual(len(self.runtime._replay), 1)
