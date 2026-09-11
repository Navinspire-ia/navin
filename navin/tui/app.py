# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The Navin terminal application (Textual)."""

from __future__ import annotations

import asyncio
import contextlib
from functools import partial
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual import events, on, work
from textual.actions import SkipAction
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.screen import Screen

from navin.optional_live import live_modules_available
from navin.tui.agi import AgiScreen
from navin.tui.evolve import EvolveScreen
from navin.tui.graph import GraphScreen
from navin.tui.hubs import (
    DOMAINS,
    AccountScreen,
    Domain,
    HubAction,
    HubRow,
    TableHub,
    config_section_rows,
    mcp_enable_preset,
    mcp_preset_fields,
    mcp_preset_rows,
    mcp_remove_server,
    mcp_rows,
    model_rows,
    provider_panel_label,
    provider_rows,
    skill_import_from_path,
    skill_rows,
    skill_set_enabled,
    tool_rows,
)
from navin.tui.modes import MODES, display_user_text, get_mode, route_text
from navin.tui.prefs import TuiPrefs
from navin.tui.runtime import (
    TuiRuntime,
    UiApprovalClosed,
    UiApprovalRequested,
    UiAssistantMessage,
    UiCheckpointSaved,
    UiChoiceClosed,
    UiChoiceRequested,
    UiContextCompacted,
    UiEngineError,
    UiEvent,
    UiFileEdit,
    UiModelUpdated,
    UiNotification,
    UiProgress,
    UiReasoning,
    UiRetryWait,
    UiStreamDelta,
    UiStreamEnd,
    UiSubagent,
    UiToolEvent,
    UiTurnEnd,
    UiTurnStarted,
)
from navin.tui.screens import (
    HELP_MARKDOWN,
    RENAME_PREFIX,
    FormField,
    FormScreen,
    MarkdownScreen,
    PickerScreen,
    PickItem,
    SettingsScreen,
    ToolsScreen,
)
from navin.tui.settings import SettingsHub
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import (
    ApprovalCard,
    AssistantMessage,
    ChoiceCard,
    Composer,
    ComposerMeta,
    ComposerShell,
    DockBar,
    FindBar,
    Sidebar,
    SlashMenu,
    SystemNote,
    TideRule,
    Transcript,
    UpdateOffer,
    UserMessage,
    account_side_text,
    split_model_slug,
)

# ---------------------------------------------------------------------------
# Command palette providers
# ---------------------------------------------------------------------------


# Slash commands the TUI answers itself (they open screens); merged into the
# engine list so `/` completion and the palette know them.
_SETTINGS_SECTIONS = frozenset(
    {
        "providers",
        "models",
        "mcp",
        "skills",
        "image",
        "video",
        "voice",
        "web",
        "system",
        "security",
        "guardrails",
        "git",
        "browser",
        "computer",
        "rules",
        "about",
    }
)

_TUI_SLASH: tuple[dict[str, Any], ...] = (
    {
        "command": "/settings",
        "title": "Settings",
        "description": "ctrl+g. Sections: providers, models, mcp, skills, image, video, voice, web, system, security, guardrails, git, browser, rules",
        "accepts_args": True,
        "arg_hint": "[section]",
    },
    {
        "command": "/mode",
        "title": "Mode",
        "description": "ctrl+t. chat, ask, plan, agent, review, security, debug",
        "accepts_args": True,
        "arg_hint": "[mode]",
    },
    {"command": "/theme", "title": "Theme", "description": "Color theme"},
    {
        "command": "/title",
        "title": "Rename chat",
        "description": "Set the name of this conversation",
        "accepts_args": True,
        "arg_hint": "<name>",
    },
    {
        "command": "/update",
        "title": "Update",
        "description": "Install the latest signed navin release",
    },
    {"command": "/graph", "title": "Graph", "description": "Project dependency graph (F2)"},
    {
        "command": "/evolve",
        "title": "Evolve",
        "description": "navin-engine proofs, diagnoses, optimizations (F3)",
    },
    {
        "command": "/agi",
        "title": "AGI",
        "description": "Skills evolution, world model, policy, transfer, memory (F4)",
    },
)


class NavinActions(Provider):
    """App actions exposed in the palette (ctrl+p)."""

    def _entries(self) -> list[tuple[str, str, str]]:
        app = self.app
        assert isinstance(app, NavinApp)
        items = [
            ("New chat", "Reset the conversation (/new)", "new_chat"),
            ("Stop turn", "Cancel the running turn (/stop)", "stop_turn"),
            ("Provider", "Open provider settings (ctrl+i)", "open_settings('providers')"),
            ("Model", "Pick the model for the next turns (ctrl+o)", "pick_model"),
            ("Mode", "chat / ask / plan / agent / review / security / debug (ctrl+t)", "pick_mode"),
            ("Sessions", "Open or resume another session (ctrl+s)", "pick_session"),
            ("Rename chat", "Change the name of this conversation (/title)", "rename_chat"),
            ("Project folder", "Change the project analysed by Graph and Evolve (ctrl+w)", "pick_project"),
            (
                "Settings",
                "Providers, models, MCP, skills, image, video, voice, web, security, git, rules...",
                "open_settings",
            ),
            ("Tools", "Browse the tools available to the agent", "open_tools"),
            (
                "Graph",
                "Project dependency graph: hubs, impact, dependencies (metagraph)",
                "open_graph",
            ),
            ("Evolve", "navin-engine proofs, diagnoses, optimizations, promotions", "open_evolve"),
            ("AGI", "Skills evolution, world model, policy, transfer, memory (F4)", "open_agi"),
            ("Theme", "Change the color theme", "pick_theme"),
            ("Toggle panel", "Show or hide the right panel (ctrl+b)", "toggle_sidebar"),
            ("Toggle reasoning", "Show or hide model reasoning", "toggle_reasoning"),
            ("Toggle tool details", "Show or hide tool call lines", "toggle_tools"),
            ("Status", "Runtime, provider and channel status (/status)", "engine_status"),
            (
                "Export transcript",
                "Save this chat as Markdown in the workspace",
                "export_transcript",
            ),
            ("Find in conversation", "Search the transcript (ctrl+f)", "find"),
            ("Copy last reply", "Copy the last assistant message (ctrl+shift+c)", "copy_reply"),
            ("Clear transcript", "Clear the screen, keep the session", "clear_transcript"),
            ("Help", "Keys, modes and slash commands", "show_help"),
            ("Update Navin", "Install the latest signed release (/update)", "update"),
            ("Quit", "Exit navin-cli (ctrl+q)", "quit"),
        ]
        # Domains that live in Settings are reached through "Settings"; only the
        # remaining hubs get their own palette entry (no duplicates).
        for domain in DOMAINS:
            if domain.id in NavinApp._DOMAIN_SECTION:
                continue
            items.append(
                (f"Configure: {domain.title}", domain.description, f"open_domain('{domain.id}')")
            )
        return items

    async def discover(self) -> Hits:
        for title, help_text, action in self._entries():
            yield DiscoveryHit(title, partial(self.app.run_action, action), help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, help_text, action in self._entries():
            score = matcher.match(f"{title} {help_text}")
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(title),
                    partial(self.app.run_action, action),
                    help=help_text,
                )


class SlashCommands(Provider):
    """Every engine slash command, searchable in the palette."""

    async def discover(self) -> Hits:
        app = self.app
        assert isinstance(app, NavinApp)
        for row in app.slash_rows[:12]:
            yield DiscoveryHit(
                str(row["command"]),
                partial(app.use_slash, row),
                help=str(row.get("description") or ""),
            )

    async def search(self, query: str) -> Hits:
        app = self.app
        assert isinstance(app, NavinApp)
        matcher = self.matcher(query)
        for row in app.slash_rows:
            text = f"{row['command']} {row.get('title') or ''} {row.get('description') or ''}"
            score = matcher.match(text)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(str(row["command"])),
                    partial(app.use_slash, row),
                    help=str(row.get("title") or row.get("description") or ""),
                )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


class NavinScreen(Screen):
    """Right-click copies without wiping the current selection.

    Textual treats every MouseDown as the start of a new selection. A
    right-click at the same cell then clears it on MouseUp, so copy ran
    against an empty selection. Swallow button 3 before that happens.
    """

    def _forward_event(self, event: events.Event) -> None:
        button = getattr(event, "button", 0)
        if isinstance(event, events.MouseEvent) and button == 3:
            if isinstance(event, events.MouseDown):
                copy = getattr(self.app, "copy_from_pointer", None)
                if callable(copy):
                    copy()
            event.stop()
            return
        super()._forward_event(event)


class NavinApp(App[None]):
    TITLE = "navin-cli"
    ALLOW_SELECT = True
    COMMANDS = {NavinActions, SlashCommands}
    CSS = """
    Screen { layout: vertical; background: $background; }
    #main { height: 1fr; }
    #column { width: 1fr; height: 1fr; background: $background; }
    #transcript { background: $background; }
    #composer-block {
        height: auto;
        padding: 2 2 0 2;
        background: $background;
    }
    #composer-shell { height: auto; width: 1fr; background: $panel; }
    #composer-meta { height: 1; }
    #dock { width: 1fr; height: 1; min-height: 1; }
    #dock.-hidden { display: none; height: 0; min-height: 0; }
    Toast { background: $panel; border-left: thick $primary; }
    UserMessage.-find, AssistantMessage.-find { background: $secondary 22%; }
    Markdown MarkdownBlock > .code_inline,
    Markdown MarkdownBlock:dark > .code_inline,
    Markdown MarkdownBlock:light > .code_inline {
        color: $foreground;
        background: transparent;
    }
    Markdown MarkdownBlock > .code_path,
    Markdown MarkdownBlock:dark > .code_path {
        color: #8FBC8F;
        background: transparent;
    }
    Markdown MarkdownBlock:light > .code_path {
        color: #2D6A4F;
        background: transparent;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New chat"),
        Binding("ctrl+i", "open_settings('providers')", "Provider"),
        Binding("ctrl+o", "pick_model", "Model"),
        Binding("ctrl+t", "pick_mode", "Mode"),
        Binding("ctrl+s", "pick_session", "Sessions"),
        Binding("ctrl+w", "pick_project", "Workspace", priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+r", "toggle_reasoning", "Reasoning", show=False),
        Binding("ctrl+l", "clear_transcript", "Clear", show=False),
        Binding("ctrl+g", "open_settings", "Settings"),
        Binding("ctrl+y", "open_settings('guardrails')", "Guardrails"),
        Binding("ctrl+k", "open_settings('skills')", "Skills"),
        Binding("ctrl+e", "open_settings('security')", "Security"),
        Binding("ctrl+u", "open_settings('mcp')", "Tools & MCP"),
        Binding("f2", "open_graph", "Graph"),
        Binding("f3", "open_evolve", "Evolve"),
        Binding("f4", "open_agi", "AGI"),
        Binding("f1", "show_help", "Help"),
        Binding("escape", "stop_turn", "Stop", show=True),
        Binding("ctrl+c", "copy_selection", "Copy", show=False, priority=True),
        Binding("super+c", "copy_selection", "Copy", show=False, priority=True),
        Binding("ctrl+insert", "copy_selection", "Copy", show=False),
        Binding("ctrl+shift+c", "copy_reply", "Copy reply", show=False),
        Binding("super+shift+c", "copy_reply", "Copy reply", show=False),
        Binding("ctrl+f", "find", "Find", show=False),
        Binding("super+f", "find", "Find", show=False),
        Binding("ctrl+v", "paste_composer", "Paste", show=False),
        Binding("super+v", "paste_composer", "Paste", show=False),
        Binding("super+shift+v", "paste_composer", "Paste", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        config: Any,
        *,
        session_id: str = "cli:direct",
        prefs: TuiPrefs | None = None,
        config_path: Path | None = None,
    ) -> None:
        super().__init__()
        for theme in NAVIN_THEMES:
            self.register_theme(theme)
        self.config = config
        self.config_path = config_path
        self.prefs = prefs or TuiPrefs.load()
        self.runtime = TuiRuntime(config, session_id=session_id, on_event=self._on_runtime_event)
        self.slash_rows: list[dict[str, Any]] = []
        self._current: AssistantMessage | None = None
        self._pending_approvals: dict[str, ApprovalCard] = {}
        self._pending_choices: dict[str, ChoiceCard] = {}
        self._activity: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""
        self._engine_ready = False
        self._update_info: dict[str, Any] = {}
        self._engine_error: str | None = None
        self._spin = 0
        self._find_hits: list[Any] = []
        self._find_index = -1
        # Project analysed by Graph / Evolve: where `navin-cli` was launched,
        # unless the prefs remember another folder that still exists.
        remembered = Path(self.prefs.project_root).expanduser() if self.prefs.project_root else None
        self.project_root: Path = remembered if remembered and remembered.is_dir() else Path.cwd()
        self._account_service: Any = None

    # -- layout -----------------------------------------------------------

    def get_default_screen(self) -> Screen:
        return NavinScreen(id="_default")

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with Vertical(id="column"):
                yield Transcript(id="transcript")
                yield SlashMenu(id="slash-menu")
                yield FindBar(id="find")
                with Vertical(id="composer-block"):
                    with ComposerShell(id="composer-shell"):
                        yield Composer(placeholder="Ask anything...")
                        yield ComposerMeta(id="composer-meta")
                        yield TideRule()
                    yield DockBar(id="dock")
            yield Sidebar(id="sidebar")

    async def on_mount(self) -> None:
        if self.prefs.theme in self.available_themes:
            self.theme = self.prefs.theme
        self.query_one(Sidebar).set_class(self.prefs.sidebar, "-visible")
        self._render_mode()
        self._set_status("starting engine…")
        self.query_one(Composer).focus()
        self.set_interval(0.12, self._tick_spinner)
        if live_modules_available():
            self._load_account(refresh=True)
            self.set_interval(60, self._load_account)
        self.run_worker(self._boot(), exclusive=True, name="boot")

    def _tick_spinner(self) -> None:
        if self.runtime.status.turn_active:
            self._spin += 1
            self._set_status()

    async def _boot(self) -> None:
        try:
            await self.runtime.start()
        except Exception as exc:  # noqa: BLE001
            self._engine_error = str(exc)
            await self._note(f"[$error]Engine failed to start:[/] {escape(str(exc))}", "error")
            self._set_status("[$error]engine offline[/]")
            return
        engine_rows = self.runtime.slash_commands()
        taken = {str(r["command"]) for r in engine_rows}
        self.slash_rows = [dict(r) for r in _TUI_SLASH if r["command"] not in taken] + engine_rows
        self._engine_ready = True
        await self._render_history()
        self._refresh_side()
        self._set_status()
        if live_modules_available():
            self._load_account(refresh=True)
        self.run_worker(self._check_updates, thread=True, name="update-notice", group="update-notice")

    def _check_updates(self) -> None:
        """Worker thread: once a day, ask whether a newer navin exists and say so.

        The daily cache in ~/.navin makes this free on most starts; when the
        server is asked, it happens off the UI thread and any failure is
        silent. A packaged CLI user has no other way to learn about a release.
        """
        try:
            from navin.update.notice import latest_update_info, update_notice

            text = update_notice()
            info = latest_update_info() or {}
        except Exception:  # noqa: BLE001 - a hint, never an error
            return
        if text:
            self.call_from_thread(
                self._show_update_notice,
                text,
                str(info.get("latestVersion") or ""),
                info,
            )

    def _show_update_notice(self, text: str, latest: str, info: dict[str, Any] | None = None) -> None:
        self._update_info = dict(info or {})
        self.notify(text, title="Update available", severity="information", timeout=12)
        with contextlib.suppress(Exception):
            self.query_one(Sidebar).set_update_available(latest)
        self.call_later(self._mount_update_offer, latest, text)

    async def _mount_update_offer(self, latest: str, detail: str) -> None:
        if not latest:
            return
        await self.transcript.add(UpdateOffer(latest, detail))

    async def on_unmount(self) -> None:
        self.prefs.last_session = self.runtime.session_key
        self.prefs.save()
        with contextlib.suppress(Exception):
            await self.runtime.close()

    # -- helpers ----------------------------------------------------------

    @property
    def transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    @property
    def composer(self) -> Composer:
        return self.query_one(Composer)

    def _set_status(self, extra: str = "") -> None:
        st = self.runtime.status
        mode = get_mode(self.prefs.mode)
        try:
            self.query_one("#composer-shell", ComposerShell).set_busy(st.turn_active)
        except Exception:  # noqa: BLE001 - shell not mounted yet
            pass
        try:
            self.query_one("#composer-meta", ComposerMeta).set_meta(
                mode=mode.label,
                model=st.model or "",
                extra=extra,
                busy=st.turn_active,
                spin=self._spin,
            )
        except Exception:  # noqa: BLE001 - meta not mounted yet
            pass
        with contextlib.suppress(Exception):
            self.query_one("#dock", DockBar).set_panel(self.prefs.sidebar)
        with contextlib.suppress(Exception):
            self.query_one(Sidebar).set_panel_label(self.prefs.sidebar)

    def _render_mode(self) -> None:
        self._refresh_side()

    def _refresh_side(self) -> None:
        with contextlib.suppress(Exception):
            self.runtime._refresh_status()
        side = self.query_one(Sidebar)
        try:
            from navin import __version__ as version
        except Exception:  # noqa: BLE001
            version = "dev"
        ver = str(version or "dev")
        side.set_version(ver)
        st = self.runtime.status
        mode = get_mode(self.prefs.mode)
        name, provider = split_model_slug(st.model)
        workspace = str(self.runtime.workspace)
        side.set_workspace(workspace)
        with contextlib.suppress(Exception):
            self.query_one("#dock", DockBar).set_path(str(self.project_root or workspace))
        try:
            data = self.config.model_dump(mode="json", by_alias=True)
        except Exception:  # noqa: BLE001
            data = {}
        side.set_control(
            "side-provider",
            provider_panel_label(data, st.provider or provider),
        )
        side.set_model(
            slug=st.model or (f"{provider}/{name}" if provider and name else name),
            tools=st.tool_count,
            tools_loaded=st.tool_registry_count,
            billed=st.billed_tokens_session,
            used=st.context_used,
            window=st.context_window,
        )
        side.set_control("side-mode", mode.label)
        turns = st.turns
        side.set_control("side-session", f"{turns} turn" if turns == 1 else f"{turns} turns")
        side.set_activity(self._activity)
        self._load_agi_side()
        self._load_git()
        self._set_status()

    @work(thread=True, exclusive=True, group="agi-side")
    def _load_agi_side(self) -> None:
        """Ladder line on the AGI card: the same four rungs as the desktop."""
        try:
            from navin.transfer.ladder import ladder

            rungs = ladder(self.project_root)
            body = "\n".join(f"{escape(r.label)} {escape(r.state)}" for r in rungs)
        except Exception as exc:  # noqa: BLE001
            body = f"[dim]{escape(str(exc))}[/dim]"
        self.app.call_from_thread(self._apply_agi_side, body)

    def _apply_agi_side(self, body: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one(Sidebar).set_agi(body)

    @work(thread=True, exclusive=True, group="git-side")
    def _load_git(self) -> None:
        """Fill the Git row with the current branch of the workspace."""
        from navin.utils.git_state import repo_state

        state = repo_state(self.runtime.workspace)
        if not state.is_repo and not state.unavailable:
            extra = Path(self.project_root)
            if extra.resolve() != Path(self.runtime.workspace).resolve():
                state = repo_state(extra)
        if state.unavailable:
            body = "unavailable"
        elif not state.is_repo:
            body = "no repo"
        elif state.detached:
            body = f"detached {state.head or 'HEAD'}"
        else:
            body = state.branch or "?"
        self.app.call_from_thread(self._apply_git, body)

    def _apply_git(self, body: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one(Sidebar).set_git(body)

    def _ensure_account_service(self) -> Any:
        if not live_modules_available():
            return None
        if self._account_service is None:
            try:
                from navin.webui.account_api import WebUIAccountService

                self._account_service = WebUIAccountService()
            except ImportError:
                return None
        return self._account_service

    def _account_detail(self, payload: dict[str, Any]) -> str:
        return account_side_text(payload)

    @work(thread=True, exclusive=True, group="account-side")
    def _load_account(self, refresh: bool = False) -> None:
        """Fill the Account card in the panel from the same payload as the desktop."""
        try:
            service = self._ensure_account_service()
            payload = service.status_payload(refresh=refresh) if service is not None else {}
        except Exception:  # noqa: BLE001
            payload = {}
        self.app.call_from_thread(self._apply_account, self._account_detail(payload))

    def _apply_account(self, detail: str = "") -> None:
        with contextlib.suppress(Exception):
            self.query_one(Sidebar).set_account(detail)

    def _on_account_payload(self, payload: dict[str, Any]) -> None:
        """Handoff / Refresh: apply navin on the live loop, then redraw chat."""
        self.runtime.apply_account_from_disk(self.config_path)
        self.config = self.runtime.config
        self._apply_account(self._account_detail(payload))
        self._set_status()

    def _after_account(self, _result: str | None) -> None:
        self.runtime.apply_account_from_disk(self.config_path)
        self.config = self.runtime.config
        self._refresh_side()
        self._load_account(refresh=True)

    async def _note(self, text: str, level: str = "info") -> None:
        await self.transcript.add(SystemNote(text, level))

    def _activity_push(self, line: str) -> None:
        self._activity.append(line)
        self._activity = self._activity[-30:]
        self.query_one(Sidebar).set_activity(self._activity)

    def _model_label(self, slug: str | None = None) -> str:
        from navin.tui.widgets import split_model_slug

        raw = slug or self.runtime.status.model or ""
        name, _provider = split_model_slug(raw)
        return name or raw or "navin"

    async def _ensure_assistant(self) -> AssistantMessage:
        if self._current is None:
            self._current = AssistantMessage(self._model_label())
            await self.transcript.add(self._current)
            return self._current
        if self._current.finished:
            self._current.finished = False
        return self._current

    async def _render_history(self) -> None:
        rows = self.runtime.history(limit=200)
        if not rows:
            return
        await self._note(f"{len(rows)} earlier messages from this session", "quiet")
        for row in rows:
            if row["role"] == "user":
                await self.transcript.add(UserMessage(row["content"]))
            else:
                meta = row.get("metadata") or {}
                block = AssistantMessage(
                    self._model_label(str(meta.get("model") or "") or None)
                )
                await self.transcript.add(block)
                await block.set_text(row["content"])
                await block.finish(
                    latency_ms=meta.get("latency_ms"),
                    model=meta.get("model"),
                    preset=meta.get("model_preset"),
                )
        self.transcript.scroll_end(animate=False)

    # -- composer ---------------------------------------------------------

    @on(Composer.Submitted)
    async def _submitted(self, event: Composer.Submitted) -> None:
        menu = self.query_one(SlashMenu)
        typed = event.text.strip().lower()
        if menu.visible_menu and menu.highlighted is not None:
            option = menu.get_option_at_index(menu.highlighted)
            row = next((r for r in self.slash_rows if r["command"] == option.id), None)
            if row is not None and typed != str(option.id).lower():
                # Complete the highlighted command; send it right away when it
                # takes no arguments, otherwise leave the cursor after it.
                menu.hide()
                self.composer.menu_open = False
                if (
                    row.get("accepts_args")
                    or row.get("arg_hint")
                    or row.get("lifecycle") in {"agent_turn", "agent_turn_with_args"}
                ):
                    self.composer.set_text(f"{option.id} ")
                    return
                await self.submit_text(str(option.id))
                return
        text = event.text.strip()
        if not text:
            return
        await self.submit_text(text)

    async def submit_text(self, text: str) -> None:
        if not self._engine_ready:
            await self._note("[$warning]engine is still starting…[/]", "warning")
            return
        self.query_one(SlashMenu).hide()
        self.composer.clear_text()
        self._history_index = None
        self.transcript.auto_follow = True
        self.prefs.remember(display_user_text(text))
        self.prefs.save()
        if text in {"exit", "quit", ":q"}:
            self.exit()
            return
        if text.startswith("/") and text.split(None, 1)[0].lower() in {"/help", "/?"}:
            await self.action_show_help()
            return
        if text.startswith("/") and await self._run_tui_slash(text):
            return
        if text.lower() in {"stop", "/stop"} and self.runtime.turn_active:
            await self.action_stop_turn()
            return
        inbound = route_text(self.prefs.mode, text)
        await self.transcript.add(UserMessage(text))
        if self.runtime.turn_active:
            await self.runtime.stop_turn()
            self.runtime.status.turn_active = False
        self._current = None
        await self.runtime.send(inbound)

    async def _run_tui_slash(self, text: str) -> bool:
        """Slash commands handled by the TUI itself (screens), not by the engine."""
        head, _, arg = text.partition(" ")
        head = head.lower()
        raw_arg = arg.strip()
        arg = raw_arg.lower()
        if head == "/settings":
            await self.action_open_settings(arg)
            return True
        if head[1:] in _SETTINGS_SECTIONS:  # /providers, /mcp, ... = /settings <section>
            await self.action_open_settings(head[1:])
            return True
        if head == "/account":
            if live_modules_available():
                await self.action_open_account()
            else:
                await self._note("Account is not part of this build. Use Settings → Providers.")
            return True
        if head == "/mode":
            if arg and any(m.id == arg for m in MODES):
                await self._apply_mode(arg)
            else:
                self.action_pick_mode()
            return True
        if head == "/theme":
            self.action_pick_theme()
            return True
        if head == "/graph":
            await self.action_open_graph()
            return True
        if head == "/evolve":
            await self.action_open_evolve()
            return True
        if head == "/agi":
            await self.action_open_agi()
            return True
        if head == "/update":
            await self.action_update()
            return True
        if head == "/title":
            if not raw_arg:
                await self._note("Usage: /title New chat name")
                return True
            await self._save_session_title(self.runtime.session_key, raw_arg)
            return True
        return False

    @on(Composer.HistoryRequested)
    def _history(self, event: Composer.HistoryRequested) -> None:
        history = self.prefs.history
        if not history:
            return
        if self._history_index is None:
            if event.direction > 0:
                return
            self._history_draft = self.composer.text
            self._history_index = len(history) - 1
        else:
            self._history_index += event.direction
        if self._history_index < 0:
            self._history_index = 0
        if self._history_index >= len(history):
            self._history_index = None
            self.composer.set_text(self._history_draft)
            return
        self.composer.set_text(display_user_text(history[self._history_index]))

    @on(Composer.SlashTyping)
    def _slash_typing(self, event: Composer.SlashTyping) -> None:
        menu = self.query_one(SlashMenu)
        if event.prefix is None:
            menu.hide()
            self.composer.menu_open = False
            return
        needle = event.prefix.lower()
        matches = [r for r in self.slash_rows if str(r["command"]).lower().startswith(needle)]
        if not matches:
            matches = [
                r
                for r in self.slash_rows
                if needle[1:] in f"{r['command']} {r.get('title', '')}".lower()
            ]
        menu.show_matches(matches)
        self.composer.menu_open = bool(matches)

    @on(SlashMenu.OptionSelected)
    async def _slash_selected(self, event: SlashMenu.OptionSelected) -> None:
        row = next((r for r in self.slash_rows if r["command"] == event.option.id), None)
        self.query_one(SlashMenu).hide()
        if row is None:
            return
        await self.use_slash(row)

    async def use_slash(self, row: dict[str, Any]) -> None:
        command = str(row["command"])
        if (
            row.get("accepts_args")
            or row.get("arg_hint")
            or row.get("lifecycle") in {"agent_turn", "agent_turn_with_args"}
        ):
            self.composer.set_text(f"{command} ")
            self.composer.focus()
            return
        await self.submit_text(command)

    @on(Composer.MenuNav)
    def _menu_nav(self, event: Composer.MenuNav) -> None:
        menu = self.query_one(SlashMenu)
        if not menu.visible_menu:
            return
        if event.key == "down":
            menu.action_cursor_down()
        elif event.key == "up":
            menu.action_cursor_up()
        elif event.key == "tab" and menu.highlighted is not None:
            option = menu.get_option_at_index(menu.highlighted)
            self.composer.set_text(f"{option.id} ")
            menu.hide()

    def _sync_shortcuts(self) -> None:
        keys: set[str] = set()
        if self._pending_approvals:
            keys |= {"y", "a", "n"}
        if self._pending_choices:
            keys |= {str(i) for i in range(1, 10)}
        self.composer.shortcut_keys = keys

    @on(Composer.Shortcut)
    def _shortcut(self, event: Composer.Shortcut) -> None:
        if event.key in {"y", "a", "n"} and self._pending_approvals:
            card = next(iter(self._pending_approvals.values()))
            card.decide(allowed=event.key != "n", remember=event.key == "a")
            return
        if event.key.isdigit() and self._pending_choices:
            card = next(iter(self._pending_choices.values()))
            card.pick_index(int(event.key) - 1)

    # -- runtime events ---------------------------------------------------

    async def _on_runtime_event(self, event: UiEvent) -> None:
        if isinstance(event, UiTurnStarted):
            self._set_status()
            return
        if isinstance(event, UiStreamDelta):
            block = await self._ensure_assistant()
            await block.delta(event.text)
            self.transcript.follow()
            return
        if isinstance(event, UiStreamEnd):
            if self._current is not None:
                await self._current.stream_end()
            return
        if isinstance(event, UiReasoning):
            if not self.prefs.show_reasoning:
                return
            block = await self._ensure_assistant()
            await block.reasoning(event.text, end=event.end)
            self.transcript.follow()
            return
        if isinstance(event, UiToolEvent):
            block = await self._ensure_assistant()
            await block.tool_event(
                event.call_id,
                event.name,
                event.phase,
                event.arguments,
                event.result,
                event.error,
                event.output,
                visible=self.prefs.show_tools,
            )
            if event.phase == "start":
                self._activity_push(f"⟳ {escape(event.name)}")
            elif event.phase == "error":
                self._activity_push(f"[$error]✗ {escape(event.name)}[/]")
            elif event.phase == "end":
                self._activity_push(f"✓ {escape(event.name)}")
            self.transcript.follow()
            return
        if isinstance(event, UiFileEdit):
            if event.path:
                self._activity_push(
                    f"✎ {escape(Path(event.path).name)} [dim]+{event.added} -{event.removed}[/dim]"
                )
            return
        if isinstance(event, UiProgress):
            if self.prefs.show_tools:
                block = await self._ensure_assistant()
                await block.progress(event.text)
                self.transcript.follow()
            return
        if isinstance(event, UiSubagent):
            block = await self._ensure_assistant()
            await block.subagent(
                event.task_id,
                event.label,
                event.phase,
                event.status_line,
                event.model,
                event.iteration,
                event.done,
                event.error,
            )
            self._activity_push(
                f"🤖 {escape(event.label)} [dim]{escape(event.status_line[:24])}[/dim]"
            )
            self.transcript.follow()
            return
        if isinstance(event, UiAssistantMessage):
            # The turn-end signal can overtake the final message. Keep writing
            # into the same bubble instead of opening a second one mid-sentence.
            block = self._current
            if block is None:
                block = await self._ensure_assistant()
            elif block.finished:
                block.finished = False
            if not (event.streamed and block.streamed) and block.text.strip() != event.text.strip():
                await block.set_text(event.text, render_as=event.render_as)
            else:
                await block.stream_end()
            if not self.runtime.turn_active and not block.finished:
                await block.finish(
                    latency_ms=event.metadata.get("latency_ms"),
                    model=event.metadata.get("model"),
                    preset=event.metadata.get("model_preset"),
                )
            self.transcript.follow()
            return
        if isinstance(event, UiTurnEnd):
            if self._current is not None and not self._current.finished:
                st = self.runtime.status
                await self._current.finish(
                    latency_ms=event.latency_ms, model=st.model, preset=st.model_preset
                )
            self._refresh_side()
            self._load_account()
            self.transcript.follow()
            return
        if isinstance(event, UiModelUpdated):
            self._refresh_side()
            if event.reason and event.model:
                await self._note(
                    f"model → [b]{escape(event.model)}[/b] [dim]({escape(event.reason)})[/dim]"
                )
            return
        if isinstance(event, UiContextCompacted):
            before = f"{event.tokens_before:,}" if event.tokens_before else "?"
            after = f"{event.tokens_after:,}" if event.tokens_after else "?"
            await self._note(
                f"context compacted ({escape(event.kind)}): {event.messages_archived} messages archived, {before} → {after} tokens"
            )
            self._refresh_side()
            return
        if isinstance(event, UiCheckpointSaved):
            self._activity_push(f"⎘ checkpoint {escape(event.name)}")
            if not event.auto:
                await self._note(f"checkpoint saved: [b]{escape(event.name)}[/b]", "success")
            return
        if isinstance(event, UiNotification):
            detail = f"\n[dim]{escape(event.detail)}[/dim]" if event.detail else ""
            await self._note(
                f"{escape(event.title)}{detail}",
                event.level if event.level in {"warning", "error", "success"} else "info",
            )
            self.notify(
                event.title,
                severity="warning" if event.level in {"warning", "error"} else "information",
            )
            return
        if isinstance(event, UiRetryWait):
            await self._note(f"[$warning]{escape(event.text)}[/]", "warning")
            return
        if isinstance(event, UiApprovalRequested):
            card = ApprovalCard(
                event.request_id,
                event.tool,
                event.action,
                event.reason,
                event.detail,
                event.consequence,
                event.scope,
                event.remember_offered,
            )
            self._pending_approvals[event.request_id] = card
            self._sync_shortcuts()
            await self.transcript.add(card)
            self.notify(f"Permission needed: {event.tool}", severity="warning")
            self._set_status("[$warning]approval pending: y / a / n[/]")
            return
        if isinstance(event, UiApprovalClosed):
            card = self._pending_approvals.pop(event.request_id, None)
            self._sync_shortcuts()
            if card is not None:
                card.close(event.allowed, event.reason)
            self._set_status()
            return
        if isinstance(event, UiChoiceRequested):
            if self._current is not None:
                await self._current.reveal()
            card = ChoiceCard(
                event.request_id,
                event.question,
                event.options,
                event.allow_skip,
                event.recommended_id,
            )
            self._pending_choices[event.request_id] = card
            self._sync_shortcuts()
            await self.transcript.add(card)
            self.notify("Navin asks a question", severity="information")
            return
        if isinstance(event, UiChoiceClosed):
            card = self._pending_choices.pop(event.request_id, None)
            self._sync_shortcuts()
            if card is not None:
                card.close(event.option_id, event.skipped)
            self.composer.focus()
            return
        if isinstance(event, UiEngineError):
            await self._note(f"[$error]{escape(event.text)}[/]", "error")
            return

    @on(ApprovalCard.Decided)
    async def _approval_decided(self, event: ApprovalCard.Decided) -> None:
        card = self._pending_approvals.pop(event.request_id, None)
        self._sync_shortcuts()
        if card is not None:
            card.close(event.allowed, "remembered" if event.remember else "")
        await self.runtime.approve(event.request_id, allowed=event.allowed, remember=event.remember)
        self._set_status()
        self.composer.focus()

    @on(ChoiceCard.Answered)
    async def _choice_answered(self, event: ChoiceCard.Answered) -> None:
        card = self._pending_choices.pop(event.request_id, None)
        self._sync_shortcuts()
        if card is not None:
            card.close(event.option_id, event.skipped, event.custom_text)
        await self.runtime.answer_choice(
            event.request_id,
            option_id=event.option_id,
            skipped=event.skipped,
            custom_text=event.custom_text,
        )
        self.composer.focus()

    # -- actions ----------------------------------------------------------

    async def action_stop_turn(self) -> None:
        menu = self.query_one(SlashMenu)
        if menu.visible_menu:
            menu.hide()
            return
        if self._pending_choices:
            card = next(iter(self._pending_choices.values()))
            if card.allow_skip:
                card.answer(skipped=True)
                return
        if self.runtime.turn_active:
            await self.runtime.stop_turn()
            await self._note("[$warning]stop requested[/]", "warning")
            return
        self.composer.focus()

    async def action_new_chat(self) -> None:
        if not self._engine_ready:
            return
        await self.submit_text("/new")
        self._current = None
        self._activity.clear()
        self._refresh_side()

    async def action_clear_transcript(self) -> None:
        await self.transcript.remove_children()
        self._current = None
        await self._note("[dim]transcript cleared (session history kept)[/dim]")

    def action_toggle_sidebar(self) -> None:
        self.prefs.sidebar = not self.prefs.sidebar
        self.prefs.sidebar_explicit = True
        self.prefs.save()
        self.query_one(Sidebar).set_class(self.prefs.sidebar, "-visible")
        self._set_status()

    def action_toggle_reasoning(self) -> None:
        self.prefs.show_reasoning = not self.prefs.show_reasoning
        self.prefs.save()
        self.notify(f"Reasoning {'shown' if self.prefs.show_reasoning else 'hidden'}", timeout=1.5)

    def action_toggle_tools(self) -> None:
        self.prefs.show_tools = not self.prefs.show_tools
        self.prefs.save()
        self.notify(f"Tool details {'shown' if self.prefs.show_tools else 'hidden'}", timeout=1.5)

    async def action_show_help(self) -> None:
        await self.push_screen(MarkdownScreen(HELP_MARKDOWN))

    # -- Graph / Evolve (desktop workbench panels) ------------------------

    def _ask_from_panel(self, text: str) -> None:
        """A panel hands a prompt to the composer; the user reviews then sends."""
        self.composer.set_text(text)
        self.composer.focus()

    def _show_markdown(self, _title: str, markdown: str) -> None:
        self.push_screen(MarkdownScreen(markdown))

    async def action_open_graph(self) -> None:
        await self.push_screen(GraphScreen(self.project_root, on_ask=self._ask_from_panel))

    async def action_open_evolve(self) -> None:
        await self.push_screen(
            EvolveScreen(
                self.project_root, on_ask=self._ask_from_panel, on_markdown=self._show_markdown
            )
        )

    async def action_open_agi(self) -> None:
        await self.push_screen(AgiScreen(self.project_root))

    def action_pick_project(self) -> None:
        self._spawn(self._pick_project())

    async def _pick_project(self) -> None:
        answer = await self.push_screen_wait(
            FormScreen(
                "Project folder",
                [
                    FormField(
                        "path",
                        "Folder analysed by Graph and Evolve",
                        placeholder=str(Path.cwd()),
                        secret=False,
                        value=str(self.project_root),
                    )
                ],
                hint="Defaults to the directory where `navin-cli` was launched.",
                submit_label="Use",
            )
        )
        if not answer:
            return
        path = Path(answer["path"]).expanduser()
        if not path.is_dir():
            self.notify(f"Not a directory: {path}", severity="error")
            return
        self.project_root = path.resolve()
        self.prefs.project_root = str(self.project_root)
        self.prefs.save()
        self._load_git()
        with contextlib.suppress(Exception):
            self.query_one("#dock", DockBar).set_path(str(self.project_root))
        self.notify(f"Project: {self.project_root}", timeout=2)

    async def action_engine_status(self) -> None:
        await self.submit_text("/status")

    async def action_update(self) -> None:
        """Install the signed release, or say why this tree cannot."""
        from navin.update import notice, service

        info = dict(self._update_info)
        if not info.get("available"):
            try:
                info = notice.latest_update_info(force=True) or {}
            except Exception:  # noqa: BLE001
                info = {}
            self._update_info = dict(info)
        latest = str(info.get("latestVersion") or "").strip()
        if not info.get("available") or not latest:
            await self._note("navin is up to date.")
            return
        kind = str(info.get("installKind") or "")
        if not info.get("supported"):
            reason = str(info.get("reason") or "").strip()
            await self._note(
                reason
                or (
                    "This session is a source checkout. "
                    "Upgrade the packaged CLI with [b]navin update[/b]."
                ),
                "warning",
            )
            return
        if kind != "cli":
            await self._note(
                f"navin {escape(latest)} is available. "
                "Open the Navin window and click Install Now, or Settings > Updates.",
                "warning",
            )
            return
        await self._note(f"Installing navin [b]{escape(latest)}[/b]…")
        try:
            result = await asyncio.to_thread(service.apply_cli_update)
        except service.UpdateError as exc:
            await self._note(f"[$error]Update failed:[/] {escape(str(exc))}", "error")
            return
        except Exception as exc:  # noqa: BLE001
            await self._note(f"[$error]Update failed:[/] {escape(str(exc))}", "error")
            return
        if result.get("deferred"):
            await self._note(
                f"navin {escape(latest)} is ready. Quit this session, wait a few seconds, "
                "then run [b]navin --version[/b].",
                "success",
            )
            return
        await self._note(
            f"Updated to navin [b]{escape(latest)}[/b]. Restart the CLI to use it.",
            "success",
        )

    def action_find(self) -> None:
        seed = ""
        with contextlib.suppress(Exception):
            seed = self.screen.get_selected_text() or ""
        if not seed:
            selected = self.composer.selected_text
            if selected:
                seed = selected
        bar = self.query_one(FindBar)
        bar.show(seed.strip())
        if seed.strip():
            self._find_move(seed.strip(), 0)

    def action_paste_composer(self) -> None:
        if self.query_one(FindBar).visible_bar and self.query_one("#find-query").has_focus:
            return
        self.composer.focus()
        self.composer.action_paste_any()

    def _find_move(self, query: str, direction: int) -> None:
        needle = query.strip().lower()
        bar = self.query_one(FindBar)
        for widget in self._find_hits:
            widget.remove_class("-find")
        self._find_hits = []
        if not needle:
            bar.set_count(0, 0)
            return
        for widget in self.transcript.children:
            text = ""
            if isinstance(widget, UserMessage):
                text = widget.raw_text
            elif isinstance(widget, AssistantMessage):
                text = widget.text
            elif isinstance(widget, SystemNote):
                text = str(widget.renderable) if hasattr(widget, "renderable") else ""
            if needle in text.lower():
                self._find_hits.append(widget)
        total = len(self._find_hits)
        if not total:
            self._find_index = -1
            bar.set_count(0, 0)
            return
        if direction == 0:
            self._find_index = 0
        else:
            self._find_index = (self._find_index + direction) % total
        hit = self._find_hits[self._find_index]
        hit.add_class("-find")
        self.transcript.scroll_to_widget(hit, animate=False)
        bar.set_count(self._find_index + 1, total)

    @on(FindBar.Moved)
    def _find_bar_moved(self, event: FindBar.Moved) -> None:
        self._find_move(event.query, event.direction)

    @on(FindBar.Closed)
    def _find_bar_closed(self) -> None:
        for widget in self._find_hits:
            widget.remove_class("-find")
        self._find_hits = []
        self._find_index = -1
        self.composer.focus()

    @on(Composer.FindRequested)
    def _composer_find(self) -> None:
        self.action_find()

    @on(Composer.PageChat)
    def _page_chat(self, event: Composer.PageChat) -> None:
        self.transcript.page(event.direction)

    @on(Composer.ChatScroll)
    def _chat_scroll(self, event: Composer.ChatScroll) -> None:
        self.transcript.nudge(event.delta)

    def copy_to_clipboard(self, text: str) -> None:
        """OSC 52 plus the OS clipboard (pbcopy / clip / wl-copy).

        Apple Terminal ignores OSC 52, so the in-app clipboard alone is not
        enough for Ctrl+C or copy-on-select.
        """
        super().copy_to_clipboard(text)
        from navin.tui.clipboard import write_clipboard

        write_clipboard(text)

    def _selected_text(self) -> str:
        selected = ""
        with contextlib.suppress(Exception):
            selected = self.screen.get_selected_text() or ""
        if not selected:
            extra = getattr(self.focused, "selected_text", None)
            if extra:
                selected = str(extra)
        return selected

    def _last_assistant_text(self) -> str:
        last: AssistantMessage | None = None
        with contextlib.suppress(Exception):
            for widget in self.transcript.children:
                if isinstance(widget, AssistantMessage) and widget.text.strip():
                    last = widget
        return last.text if last is not None else ""

    def action_copy_selection(self) -> None:
        """Copy the mouse selection, or the focused input selection."""
        selected = self._selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        raise SkipAction()

    @on(events.TextSelected)
    def _copy_on_select(self, _event: events.TextSelected) -> None:
        selected = self._selected_text()
        if selected:
            self.copy_to_clipboard(selected)

    def copy_from_pointer(self) -> None:
        """Right-click: copy the selection, or the last assistant reply."""
        from navin.tui.clipboard import pointer_copy_text

        text = pointer_copy_text(self._selected_text(), self._last_assistant_text())
        if text:
            self.copy_to_clipboard(text)

    def action_copy_reply(self) -> None:
        """Copy selected text, or the last assistant reply if nothing is selected."""
        selected = self._selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.notify("Copied", timeout=1.2)
            return
        last = self._last_assistant_text()
        if not last.strip():
            self.notify("Nothing to copy", severity="warning", timeout=1.5)
            return
        self.copy_to_clipboard(last)
        self.notify("Reply copied", timeout=1.2)

    async def action_export_transcript(self) -> None:
        lines: list[str] = [f"# Navin session {self.runtime.session_key}", ""]
        for widget in self.transcript.children:
            if isinstance(widget, UserMessage):
                lines += ["## You", "", widget.raw_text, ""]
            elif isinstance(widget, AssistantMessage):
                lines += [f"## {widget.bot_name}", "", widget.text, ""]
        target = self.runtime.workspace / "exports"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"tui-{self.runtime.chat_id}-{int(asyncio.get_running_loop().time())}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        await self._note(f"transcript exported to [b]{escape(str(path))}[/b]", "success")

    # -- pickers ----------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        """Run a coroutine that awaits ``push_screen_wait`` (needs a worker)."""
        self.run_worker(coro, exclusive=True, group="picker")

    def action_pick_model(self) -> None:
        self._spawn(self._pick_model())

    def action_pick_mode(self) -> None:
        self._spawn(self._pick_mode())

    def action_pick_theme(self) -> None:
        self._spawn(self._pick_theme())

    def action_pick_session(self) -> None:
        self._spawn(self._pick_session())

    def action_rename_chat(self) -> None:
        self._spawn(self._rename_session(self.runtime.session_key))

    async def _pick_model(self) -> None:
        if not self._engine_ready:
            return
        rows = self.runtime.preset_details()
        items = [
            PickItem(
                r["name"],
                r["name"],
                f"{r['model'] or 'not set'}" + (f"  ·  {r['provider']}" if r["provider"] else ""),
                r["label"],
            )
            for r in rows
        ]
        chosen = await self.push_screen_wait(
            PickerScreen(
                "Model preset",
                items,
                hint="Applies to the next turns of this runtime (same as /model).",
                current=self.runtime.status.model_preset,
            )
        )
        if chosen:
            await self._apply_preset(chosen)

    async def _apply_preset(self, name: str) -> None:
        try:
            self.runtime.set_model_preset(name)
        except Exception as exc:  # noqa: BLE001
            await self._note(f"[$error]{escape(str(exc))}[/]", "error")
            return
        self._refresh_side()
        await self._note(
            f"model preset → [b]{escape(name)}[/b]  ·  {escape(self.runtime.status.model)}",
            "success",
        )

    async def _pick_mode(self) -> None:
        items = [PickItem(m.id, m.label, m.description, m.command or "plain text") for m in MODES]
        chosen = await self.push_screen_wait(
            PickerScreen("Composer mode", items, current=self.prefs.mode)
        )
        if chosen:
            await self._apply_mode(chosen)

    async def _apply_mode(self, mode_id: str) -> None:
        self.prefs.mode = mode_id
        self.prefs.mode_explicit = True
        self.prefs.save()
        if self.runtime.turn_active:
            await self.runtime.stop_turn()
            self.runtime.status.turn_active = False
            self._current = None
            self._set_status()
        self._render_mode()

    async def _pick_theme(self) -> None:
        items = [PickItem(name, name, "") for name in sorted(self.available_themes)]
        chosen = await self.push_screen_wait(PickerScreen("Theme", items, current=self.theme))
        if chosen:
            self.theme = chosen
            self.prefs.theme = chosen
            self.prefs.theme_explicit = True
            self.prefs.save()

    def _session_pick_items(self) -> list[PickItem]:
        from navin.tui.session_labels import (
            format_session_when,
            session_display_title,
            session_origin,
        )

        self.runtime.ensure_session_titles()
        items = [PickItem("__new__", "New session", "", "")]
        for row in self.runtime.session_rows():
            key = str(row.get("key") or "")
            if not key:
                continue
            items.append(
                PickItem(
                    key,
                    session_display_title(row),
                    session_origin(key),
                    format_session_when(str(row.get("updated_at") or "")),
                )
            )
        return items

    async def _pick_session(self) -> None:
        if not self._engine_ready:
            return
        while True:
            chosen = await self.push_screen_wait(
                PickerScreen(
                    "Sessions",
                    self._session_pick_items(),
                    hint="Type to search. F2 renames. Enter opens.",
                    current=self.runtime.session_key,
                    renamable=True,
                )
            )
            if not chosen:
                return
            if chosen.startswith(RENAME_PREFIX):
                rest = chosen.removeprefix(RENAME_PREFIX)
                key, sep, title = rest.partition("\n")
                if sep:
                    if title.strip():
                        await self._save_session_title(key, title.strip())
                elif key:
                    await self._rename_session(key)
                continue
            if chosen == "__new__":
                import time as _time

                chosen = f"cli:{int(_time.time())}"
            await self._switch_session(chosen)
            return

    async def _rename_session(self, key: str) -> None:
        if not key or key.startswith("__"):
            return
        from navin.tui.session_labels import session_display_title

        current = ""
        for row in self.runtime.session_rows():
            if str(row.get("key") or "") == key:
                current = session_display_title(row)
                break
        values = await self.push_screen_wait(
            FormScreen(
                "Rename chat",
                [
                    FormField(
                        "title",
                        "Name",
                        value=current,
                        placeholder="Chat name",
                    )
                ],
                hint="This name stays until you change it again.",
                submit_label="Save",
            )
        )
        if not values:
            return
        await self._save_session_title(key, values.get("title") or "")

    async def _save_session_title(self, key: str, title: str) -> None:
        try:
            name = self.runtime.set_session_title(key, title)
        except Exception as exc:  # noqa: BLE001
            await self._note(f"[$error]{escape(str(exc))}[/]", "error")
            return
        self._refresh_side()
        await self._note(f"chat name -> {escape(name)}", "success")

    async def _switch_session(self, key: str) -> None:
        await self.runtime.switch_session(key)
        self.prefs.last_session = key
        self.prefs.save()
        await self.transcript.remove_children()
        self._current = None
        self._activity.clear()
        await self._render_history()
        self._refresh_side()
        await self._note(f"session → [b]{escape(key)}[/b]")

    async def action_open_tools(self) -> None:
        if not self._engine_ready:
            return
        await self.push_screen(ToolsScreen(self.runtime.tool_rows()))

    async def action_open_account(self) -> None:
        if not live_modules_available():
            await self._note("Account is not part of this build. Use Settings → Providers.")
            return
        await self.push_screen(
            AccountScreen(
                service=self._ensure_account_service(),
                on_applied=self._on_account_payload,
            ),
            self._after_account,
        )

    # -- settings ---------------------------------------------------------

    def _load_config_data(self) -> dict[str, Any]:
        from navin.config.loader import load_config

        return load_config(self.config_path).model_dump(mode="json", by_alias=True)

    def _save_config_data(self, data: dict[str, Any]) -> str | None:
        from navin.config.loader import save_config
        from navin.config.schema import Config

        try:
            cfg = Config.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            return f"invalid config: {exc}"
        try:
            save_config(cfg, self.config_path)
        except Exception as exc:  # noqa: BLE001
            return f"save failed: {exc}"
        return None

    def _config_label(self) -> str:
        try:
            from navin.config.loader import get_config_path

            return str(self.config_path or get_config_path())
        except Exception:  # noqa: BLE001
            return "config.json"

    async def action_open_settings(self, root: str = "") -> None:
        """Desktop-like settings hub. ``root`` = section id, or a dotted config path for the raw tree."""
        if root and "." in root:
            path = tuple(p for p in root.split(".") if p)
            await self.push_screen(
                SettingsScreen(
                    self._load_config_data,
                    self._save_config_data,
                    path_label=self._config_label(),
                    root=path,
                )
            )
            return
        try:
            from navin import __version__ as version
        except Exception:  # noqa: BLE001
            version = "dev"
        hub = SettingsHub(
            self._load_config_data,
            self._save_config_data,
            config_label=self._config_label(),
            project_root=self.project_root,
            workspace=str(self.runtime.workspace),
            version=str(version or "dev"),
            runtime=self.runtime,
            open_raw=lambda: self.push_screen(
                SettingsScreen(
                    self._load_config_data,
                    self._save_config_data,
                    path_label=self._config_label(),
                    title="Advanced settings",
                )
            ),
            open_account=(
                (
                    lambda: self.push_screen(
                        AccountScreen(
                            service=self._ensure_account_service(),
                            on_applied=self._on_account_payload,
                        ),
                        self._after_account,
                    )
                )
                if live_modules_available()
                else None
            ),
            apply_preset=self._apply_preset,
            start=root or "providers",
        )
        await self.push_screen(hub, self._after_settings)

    def _after_settings(self, changed: bool | None) -> None:
        self.runtime.apply_account_from_disk(self.config_path)
        self.config = self.runtime.config
        self._refresh_side()
        if live_modules_available():
            self._load_account(refresh=True)
        if changed:
            self.notify("Settings applied.", timeout=2)

    # Domains that live in the settings hub open there (one settings UI, like the desktop).
    _DOMAIN_SECTION = {
        "account": "account",
        "providers": "providers",
        "models": "models",
        "mcp": "mcp",
        "skills": "skills",
        "image": "image",
        "video": "video",
        "audio": "voice",
        "voice": "voice",
        "web": "web",
        "security": "security",
        "guardrails": "guardrails",
        "git": "git",
        "browser": "browser",
        "computer": "computer",
        "rules": "rules",
    }

    async def action_open_domain(self, domain_id: str) -> None:
        section = self._DOMAIN_SECTION.get(domain_id)
        if section == "account":
            if live_modules_available():
                await self.action_open_account()
            else:
                await self.action_open_settings("providers")
            return
        if section:
            await self.action_open_settings(section)
            return
        domain = next((d for d in DOMAINS if d.id == domain_id), None)
        if domain is None:
            return
        await self.push_screen(self._build_hub(domain))

    def _build_hub(self, domain: Domain) -> TableHub:
        data = self._load_config_data()

        def edit(hub: TableHub, row: HubRow | None) -> None:
            path = (
                row.edit_path
                if row and row.edit_path
                else (domain.roots[0] if domain.roots else ())
            )
            hub.app.push_screen(
                SettingsScreen(
                    self._load_config_data,
                    self._save_config_data,
                    path_label=self._config_label(),
                    root=path,
                    title=f"{domain.title} settings",
                )
            )

        def edit_all(hub: TableHub, row: HubRow | None) -> None:
            root = domain.roots[0] if len(domain.roots) == 1 else ()
            hub.app.push_screen(
                SettingsScreen(
                    self._load_config_data,
                    self._save_config_data,
                    path_label=self._config_label(),
                    root=root,
                    title=f"{domain.title} settings",
                )
            )

        async def run_cmd(command: str, hub: TableHub, row: HubRow | None) -> None:
            hub.dismiss(None)
            await self.submit_text(command)

        actions: list[HubAction] = []
        if domain.id == "providers":
            rows = provider_rows(data)
            actions = [
                HubAction("e", "Edit provider", edit),
                HubAction(
                    "o", "OAuth login (opens navin provider login)", self._provider_login_hint
                ),
            ]
            return TableHub(domain, ["", "Provider", "Kind", "API key", "Base URL"], rows, actions)
        if domain.id == "models":
            rows = model_rows(self.runtime.preset_details(), self.runtime.status.model_preset)

            async def use(hub: TableHub, row: HubRow | None) -> None:
                if row is None:
                    return
                await self._apply_preset(row.key)
                hub.dismiss(None)

            actions = [
                HubAction("u", "Use preset", use),
                HubAction("e", "Edit", edit),
                HubAction("a", "All model settings", edit_all),
            ]
            return TableHub(domain, ["", "Preset", "Model", "Provider", "Label"], rows, actions)
        if domain.id == "mcp":

            def mcp_all_rows() -> list[HubRow]:
                fresh = self._load_config_data()
                configured = set((fresh.get("tools") or {}).get("mcpServers") or {})
                return mcp_rows(fresh) + mcp_preset_rows(configured)

            async def mcp_enable(hub: TableHub, row: HubRow | None) -> None:
                if row is None:
                    return
                if not row.key.startswith("preset:"):
                    hub.status("Already configured. Use [b]e[/b] to edit or [b]r[/b] to remove.")
                    return
                name = row.key.removeprefix("preset:")
                values: dict[str, str] = {}
                fields = [
                    FormField(n, label, placeholder, secret, required)
                    for n, label, placeholder, secret, required in mcp_preset_fields(name)
                ]
                if fields:
                    answer = await hub.app.push_screen_wait(
                        FormScreen(
                            f"Enable MCP preset: {name}",
                            fields,
                            hint="Secrets are stored encrypted in config.json.",
                            submit_label="Enable",
                        )
                    )
                    if answer is None:
                        return
                    values = answer
                hub.status(mcp_enable_preset(name, values))
                hub.set_rows(mcp_all_rows(), keep_key=name)

            async def mcp_remove(hub: TableHub, row: HubRow | None) -> None:
                if row is None or row.key.startswith("preset:"):
                    hub.status("Select a configured server to remove.")
                    return
                hub.status(mcp_remove_server(row.key))
                hub.set_rows(mcp_all_rows())

            actions = [
                HubAction("i", "Enable preset", mcp_enable),
                HubAction("e", "Edit server", edit),
                HubAction("r", "Remove server", mcp_remove),
                HubAction(
                    "a",
                    "All MCP settings",
                    lambda h, r: h.app.push_screen(
                        SettingsScreen(
                            self._load_config_data,
                            self._save_config_data,
                            path_label=self._config_label(),
                            root=("tools", "mcpServers"),
                            title="MCP servers",
                        )
                    ),
                ),
            ]
            return TableHub(
                domain,
                ["Server", "Type", "Command / URL", "Tools", "Timeout"],
                mcp_all_rows(),
                actions,
                empty_hint="No MCP server configured. Press [b]i[/b] on a preset to enable it, or [b]a[/b] to edit tools.mcpServers.",
            )
        if domain.id == "tools":
            rows = tool_rows(self.runtime.tool_rows())
            actions = [HubAction("e", "Tool settings", edit_all)]
            return TableHub(domain, ["Tool", "Kind", "Parameters", "Description"], rows, actions)
        if domain.id == "skills":

            def disabled_set() -> set[str]:
                fresh = self._load_config_data()
                defaults = (fresh.get("agents") or {}).get("defaults") or {}
                return {
                    str(s)
                    for s in (
                        defaults.get("disabledSkills") or defaults.get("disabled_skills") or []
                    )
                }

            def all_rows() -> list[HubRow]:
                return skill_rows(self.runtime.agent_loop, self.runtime.workspace, disabled_set())

            async def toggle(hub: TableHub, row: HubRow | None) -> None:
                if row is None:
                    return
                enable = row.key in disabled_set()
                hub.status(skill_set_enabled(row.key, enable))
                hub.set_rows(all_rows(), keep_key=row.key)

            async def install(hub: TableHub, row: HubRow | None) -> None:
                answer = await hub.app.push_screen_wait(
                    FormScreen(
                        "Install skills",
                        [
                            FormField(
                                "source",
                                "Git URL or local folder",
                                placeholder="https://github.com/org/skills-pack  or  ~/my-skills",
                                secret=False,
                            )
                        ],
                        hint="A git URL installs a pack (skills + MCP servers) via /pack install. A local folder copies its SKILL.md folders into .navin/skills.",
                        submit_label="Install",
                    )
                )
                if not answer:
                    return
                source = answer["source"]
                if source.startswith(("http://", "https://", "git@", "ssh://")) or source.endswith(
                    ".git"
                ):
                    await run_cmd(f"/pack install {source}", hub, row)
                    return
                hub.status(skill_import_from_path(self.runtime.workspace, source))
                hub.set_rows(all_rows())

            actions = [
                HubAction("t", "Enable / disable", toggle),
                HubAction("i", "Install (git url / folder)", install),
                HubAction("l", "/skill (list in chat)", partial(run_cmd, "/skill")),
                HubAction("p", "/pack list", partial(run_cmd, "/pack list")),
            ]
            return TableHub(
                domain,
                ["", "Skill", "Source", "Description"],
                all_rows(),
                actions,
                empty_hint="No skills found. Press [b]i[/b] to install a pack or import a folder.",
            )
        # Generic config-backed domains.
        rows = config_section_rows(data, domain.roots)
        actions = [HubAction("e", "Edit value", edit)]
        if len(domain.roots) >= 1:
            actions.append(HubAction("a", "Open section tree", edit_all))
        for cmd in domain.commands[:3]:
            actions.append(HubAction(str(len(actions) + 1), cmd, partial(run_cmd, cmd)))
        return TableHub(
            domain,
            ["Setting", "Type", "Value"],
            rows,
            actions,
            empty_hint="Nothing configured in this section yet. Press [b]a[/b] to open the section tree.",
        )

    def _provider_login_hint(self, hub: TableHub, row: HubRow | None) -> None:
        name = row.key if row else "<provider>"
        hub.status(
            f"OAuth providers sign in from a terminal: [b]navin provider login {escape(name)}[/b]  (then restart navin-cli)"
        )

    async def action_quit(self) -> None:
        self.exit()

    # Textual system commands: keep the built-ins (theme, keys...) too.
    def get_system_commands(self, screen: Screen):  # type: ignore[override]
        yield from super().get_system_commands(screen)
        yield SystemCommand("Navin help", "Keys, modes and slash commands", self.action_show_help)
        yield SystemCommand("Update Navin", "Install the latest signed release", self.action_update)
        yield SystemCommand("AGI", "Skills evolution, world model, policy, transfer, memory", self.action_open_agi)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_tui(
    *,
    config: Any,
    session_id: str | None = None,
    config_path: Path | None = None,
    project_root: Path | None = None,
) -> None:
    prefs = TuiPrefs.load()
    if not session_id:
        # Resume where the user left off unless a session was given explicitly.
        session_id = prefs.last_session or "cli:direct"
    if project_root is not None:
        # `navin-cli [folder]`: the launch folder wins over the remembered one.
        prefs.project_root = str(project_root)
    import signal

    # Ctrl+C copies. Ctrl+Z must not background the process (it looks like a crash).
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    app = NavinApp(config, session_id=session_id, prefs=prefs, config_path=config_path)
    app.run()
