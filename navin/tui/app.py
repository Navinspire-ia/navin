# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The Navin terminal application (Textual)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from textual import events, on, work
from textual.actions import SkipAction
from textual.app import App, ComposeResult, SystemCommand
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.geometry import Offset
from textual.notifications import SeverityLevel
from textual.screen import Screen
from textual.selection import SELECT_ALL, Selection
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea

from navin.optional_live import live_modules_available
from navin.tui.agi import AgiScreen
from navin.tui.evolve import EvolveScreen
from navin.tui.frames import background_lines, terminal_gc_policy
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
from navin.tui.models import ModelPickerScreen, model_pick_items
from navin.tui.modes import MODES, ROUTING_COMMANDS, display_user_text, get_mode, inbound_for_submit
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
    UiDisplayError,
    UiEngineError,
    UiEvent,
    UiFileEdit,
    UiFilePreview,
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
from navin.tui.textmarkup import escape
from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import (
    AgentsPanel,
    ApprovalCard,
    AssistantMessage,
    ChatColumn,
    ChoiceCard,
    Composer,
    ComposerMeta,
    ComposerShell,
    DockBar,
    FindBar,
    PromptQueue,
    QueuedPromptRow,
    Sidebar,
    SlashMenu,
    SystemNote,
    Transcript,
    UpdateOffer,
    UserMessage,
    WorkingLine,
    account_side_text,
    format_elapsed,
    format_working_line,
    running_exec_count,
    split_model_slug,
)
from navin.utils.tool_hints import extract_line_diff

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
    {"command": "/import", "title": "Import chats", "description": "Import Claude Code, Codex, OpenCode, OMP and Cursor chats"},
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
        "command": "/paste",
        "title": "Paste",
        "description": "Paste into the prompt (ctrl+v / cmd+v)",
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
    {
        "command": "/ps",
        "title": "Background terminals",
        "description": "List live exec sessions started in the background",
    },
    *(
        {
            "command": f"/{section}",
            "title": f"{section.title()} settings",
            "description": f"Open {section} configuration",
        }
        for section in sorted(_SETTINGS_SECTIONS)
    ),
)


WidgetT = TypeVar("WidgetT")


class NavinActions(Provider):
    """App actions exposed in the palette (ctrl+p)."""

    def _entries(self) -> list[tuple[str, str, str]]:
        app = self.app
        assert isinstance(app, NavinApp)
        items = [
            ("New chat", "Reset the conversation (/new)", "new_chat"),
            ("Stop turn", "Cancel the running turn", "request_stop"),
            ("Background terminals", "List live exec sessions (/ps)", "list_processes"),
            ("Provider", "Open provider settings (ctrl+i)", "open_settings('providers')"),
            ("Model", "Pick the model for the next turns (ctrl+o)", "pick_model"),
            ("Reasoning effort", "Choose native reasoning effort (ctrl+shift+r)", "pick_reasoning"),
            ("Model routing", "Choose model configurations by task", "open_settings('routing')"),
            ("Mode", "chat / ask / plan / agent / review / security / debug (ctrl+t)", "pick_mode"),
            ("Sessions", "Open or resume another session (ctrl+s)", "pick_session"),
            ("Import chats", "Recover Claude Code, Codex, OpenCode, OMP and Cursor chats", "import_sessions"),
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
            ("Paste", "Paste into the prompt (ctrl+v / cmd+v)", "paste_composer"),
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

    @property
    def is_current(self) -> bool:
        # Textual also updates screens in the background stack. An opaque
        # modal covers this one completely, so defer its pending layout and
        # repaint until it is visible again. The engine keeps processing.
        return super().is_current and (
            self.app.screen is self or self.app.screen.styles.background.a < 1
        )

    def render_lines(self, crop):
        if self.app.is_inline or self.styles.background.a < 1:
            return super().render_lines(crop)
        return background_lines(self, crop)

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
        if isinstance(event, events.MouseMove) and self._selecting:
            self._track_selection_edge(event.screen_x, event.screen_y)
        elif isinstance(event, events.MouseUp):
            self._stop_selection_scroll()

    # -- selection past the visible chat -----------------------------------
    #
    # Textual stops a drag selection at the edge of the screen and only
    # selects widgets that are on screen. Holding the button above or below
    # the chat now scrolls it, and the selection follows reading order, so a
    # reply longer than the window copies whole.

    SELECTION_SCROLL_S = 0.05
    SELECTION_SCROLL_LINES = 2

    def _chat(self) -> Transcript | None:
        try:
            return self.query_one("#transcript", Transcript)
        except Exception:  # noqa: BLE001 - modal screens have no transcript
            return None

    def _track_selection_edge(self, x: int, y: int) -> None:
        transcript = self._chat()
        if transcript is None or self._select_start is None or not self._in_chat(self._select_start[0], transcript):
            self._stop_selection_scroll()
            return
        region = transcript.region
        direction = -1 if y <= region.y else 1 if y >= region.bottom - 1 else 0
        self._selection_pointer_x = min(max(x, region.x), region.right - 1)
        self._selection_direction = direction
        if direction == 0:
            self._stop_selection_scroll()
        elif getattr(self, "_selection_timer", None) is None:
            self._selection_timer = self.set_interval(self.SELECTION_SCROLL_S, self._selection_scroll_tick)

    def _stop_selection_scroll(self) -> None:
        timer = getattr(self, "_selection_timer", None)
        if timer is not None:
            timer.stop()
        self._selection_timer = None

    def _selection_scroll_tick(self) -> None:
        transcript = self._chat()
        if transcript is None or not self._selecting:
            self._stop_selection_scroll()
            return
        direction = self._selection_direction
        if direction < 0 and transcript.scroll_y <= 0 and not transcript.windowed_count and not transcript.has_older:
            return
        transcript.scroll_relative(y=direction * self.SELECTION_SCROLL_LINES, animate=False, immediate=True)
        self.call_after_refresh(self._extend_selection_to_edge)

    def _extend_selection_to_edge(self) -> None:
        transcript = self._chat()
        if transcript is None or not self._selecting:
            return
        region = transcript.region
        edge = region.y if self._selection_direction < 0 else region.bottom - 1
        step = 1 if self._selection_direction < 0 else -1
        x = self._selection_pointer_x
        # The edge row is often a blank gap between blocks; take the nearest
        # row with text inside the chat.
        for y in range(edge, edge + step * 6, step):
            widget, offset = self.get_widget_and_offset_at(x, y)
            if widget is not None and offset is not None and widget.allow_select:
                self._select_end = (widget, Offset(x, y), offset)
                return

    @staticmethod
    def _in_chat(widget: Widget, transcript: Transcript) -> bool:
        return transcript in widget.ancestors

    @staticmethod
    def _selectable_in_order(root: Widget) -> list[Widget]:
        found: list[Widget] = []
        stack = list(reversed(root.children))
        while stack:
            node = stack.pop()
            if not node.display:
                continue
            if node.allow_select:
                found.append(node)
            stack.extend(reversed(node.children))
        return found

    def _watch__select_end(self, select_end: Any) -> None:
        start = self._select_start
        transcript = self._chat()
        if (
            select_end is None or start is None or transcript is None or self._box_select
            or start[0] is select_end[0]
            or not (self._in_chat(start[0], transcript) and self._in_chat(select_end[0], transcript))
        ):
            super()._watch__select_end(select_end)
            return
        order = self._selectable_in_order(transcript)
        index = {widget: position for position, widget in enumerate(order)}
        if start[0] not in index or select_end[0] not in index:
            super()._watch__select_end(select_end)
            return
        first, last = sorted((start, select_end), key=lambda end: index[end[0]])
        self.selections = {
            first[0]: Selection(first[2], None),
            **{widget: SELECT_ALL for widget in order[index[first[0]] + 1 : index[last[0]]]},
            last[0]: Selection(None, last[2]),
        }


@dataclass(frozen=True)
class QueuedPrompt:
    id: int
    text: str
    inbound: str


class NavinApp(App[None]):
    """Textual app hosting the chat transcript and the agent runtime."""

    async def _process_messages(self, *args, **kwargs) -> None:
        with terminal_gc_policy():
            await super()._process_messages(*args, **kwargs)

    #: How long a turn-end signal waits for an overtaking final answer before
    #: the prompt queue is unblocked (see UiTurnEnd handling).
    AWAITING_REPLY_GRACE_S = 1.5

    TITLE = "navin-cli"
    ALLOW_SELECT = True
    COMMANDS = {NavinActions, SlashCommands}

    def get_driver_class(self):
        driver = super().get_driver_class()
        if driver.__module__ == "textual.drivers.linux_driver":
            from navin.tui.driver import NavinLinuxDriver

            return NavinLinuxDriver
        return driver

    CSS = """
    Screen { layout: vertical; background: $background; }
    #main { height: 1fr; }
    #column { width: 1fr; height: 1fr; background: $background; }
    #transcript { background: $background; }
    #composer-block {
        height: auto;
        padding: 1 0 0 0;
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
        Binding("ctrl+shift+r", "pick_reasoning", "Reasoning effort", show=False),
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
        Binding("ctrl+c", "interrupt_or_clear", "Copy / clear", show=False, priority=True),
        Binding("super+c", "copy_selection", "Copy", show=False, priority=True),
        Binding("ctrl+insert", "copy_selection", "Copy", show=False),
        Binding("ctrl+shift+c", "copy_reply", "Copy reply", show=False),
        Binding("super+shift+c", "copy_reply", "Copy reply", show=False),
        Binding("ctrl+f", "find", "Find", show=False),
        Binding("super+f", "find", "Find", show=False),
        Binding("ctrl+up", "page_transcript(-1)", show=False),
        Binding("ctrl+down", "page_transcript(1)", show=False),
        Binding("ctrl+v", "paste_composer", "Paste", show=False, priority=True),
        Binding("super+v", "paste_composer", "Paste", show=False, priority=True),
        Binding("super+shift+v", "paste_composer", "Paste", show=False),
        Binding("ctrl+alt+v", "paste_composer", "Paste", show=False),
        Binding("super+alt+v", "paste_composer", "Paste", show=False),
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
        self.slash_rows: list[dict[str, Any]] = [dict(row) for row in _TUI_SLASH]
        self._current: AssistantMessage | None = None
        # Bubbles closed early by a mid-turn "Send now" message. They still
        # own earlier activity. Live tool cards move to the current bubble
        # when another event arrives, and all bubbles finish at turn end.
        self._parked_bubbles: list[AssistantMessage] = []
        self._last_speaker: str | None = None
        self._render_token = 0
        self._history_lock = asyncio.Lock()
        self._history_snapshot: list[Any] = []
        self._history_cursor: int | None = None
        self._pending_approvals: dict[str, ApprovalCard] = {}
        self._pending_choices: dict[str, ChoiceCard] = {}
        self._retry_wait_note: SystemNote | None = None
        self._display_error_note: SystemNote | None = None
        self._display_error_timer = None
        self._pending_copy: tuple[str, bool] | None = None
        self._copy_running = False
        self._activity: list[str] = []
        self._history_index: int | None = None
        self._history_draft = ""
        self._engine_ready = False
        self._quitting = False
        self._quit_armed_at = 0.0
        self._runtime_close_task: asyncio.Task | None = None
        self._update_info: dict[str, Any] = {}
        self._engine_error: str | None = None
        self._spin = 0
        self._navigation_closed_at = 0.0
        self._stop_pending = False
        self._queued_prompts: dict[str, list[QueuedPrompt]] = {}
        self._queue_paused: set[str] = set()
        self._queue_serial = 0
        self._queue_sending = False
        self._queue_visible_session = self.runtime.session_key
        from navin.tui.session_state import TuiSessionStore

        self._session_store = TuiSessionStore(
            self.prefs.path().parent / "tui-sessions", Path(config.workspace_path),
        )
        self._drafts: dict[str, str] = {}
        self._saved_session_states: dict[str, Any] = {}
        self._requested_session_states: dict[str, Any] = {}
        self._pending_session_states: dict[str, Any] = {}
        self._session_save_task: asyncio.Task | None = None
        self._restore_unsent_work(self.runtime.session_key)
        self._awaiting_reply = False
        self._awaiting_grace_timer: Any = None
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

    def pop_screen(self) -> AwaitComplete:
        popped = super().pop_screen()
        self._navigation_closed_at = time.monotonic()
        return popped

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            with ChatColumn(id="column"):
                yield Transcript(id="transcript")
                yield AgentsPanel(id="agents")
                yield WorkingLine(id="working")
                yield PromptQueue(id="prompt-queue")
                yield SlashMenu(id="slash-menu")
                yield FindBar(id="find")
                with Vertical(id="composer-block"):
                    with ComposerShell(id="composer-shell"):
                        with Horizontal(classes="composer-input"):
                            yield Static("›", id="composer-prompt", markup=False)
                            composer = Composer(placeholder="Ask anything...")
                            composer.set_text(self._drafts.get(self.runtime.session_key, ""))
                            yield composer
                    yield DockBar(id="dock")
            yield Sidebar(id="sidebar")

    async def on_mount(self) -> None:
        if self.prefs.theme in self.available_themes:
            self.theme = self.prefs.theme
        self.watch(self, "theme", self._sync_terminal_background)
        self._one(Sidebar).set_class(self.prefs.sidebar, "-visible")
        self._render_mode()
        self._set_status("starting engine…")
        self._one(Composer).focus()
        self.set_interval(0.12, self._tick_spinner)
        self.set_interval(2, self._refresh_context_status)
        if live_modules_available():
            self._load_account(refresh=True)
            self.set_interval(60, self._load_account)
        self.run_worker(self._boot(), exclusive=True, name="boot")

    def _agents_panel(self) -> AgentsPanel | None:
        try:
            return self.query_one("#agents", AgentsPanel)
        except Exception:  # noqa: BLE001 - not composed yet
            return None

    def _tick_spinner(self) -> None:
        if not self.screen_stack:
            return
        visible = isinstance(self.screen, NavinScreen)
        panel = self._agents_panel()
        if visible and panel is not None and panel.active:
            panel.tick(self.runtime.status.turn_active, self.runtime.turn_elapsed_s)
        if visible and self.runtime.status.turn_active:
            self._spin += 1
            self._set_status(animation_only=True)
        if visible:
            self._refresh_working_line()
        key = self.runtime.session_key
        if self._queue_visible_session != key:
            self._save_unsent_work()
            self._queue_visible_session = key
            if key not in self._drafts:
                self._restore_unsent_work(key)
            self.composer.set_text(self._drafts.get(key, ""))
            self._awaiting_reply = self.runtime.turn_active
            self.call_later(self._refresh_queue)
        self._maybe_kick_queue()

    def _restore_unsent_work(self, key: str) -> None:
        state = self._session_store.load(key)
        self._drafts[key] = state.get("draft") if isinstance(state.get("draft"), str) else ""
        items = state.get("queue", [])
        self._queued_prompts[key] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("text", "inbound")):
                continue
            self._queue_serial += 1
            self._queued_prompts[key].append(QueuedPrompt(self._queue_serial, item["text"], item["inbound"]))
        # Restore work for review. The existing Resume control starts it.
        if self._queued_prompts[key] or state.get("paused"):
            self._queue_paused.add(key)

    def _save_unsent_work(self) -> None:
        key = self._queue_visible_session
        try:
            draft = self.composer.expand_for_submit()
        except Exception:
            draft = self._drafts.get(key, "")
        self._drafts[key] = draft
        queue = [{"text": item.text, "inbound": item.inbound} for item in self._queued_prompts.get(key, [])]
        state = (draft, queue, key in self._queue_paused)
        if self._requested_session_states.get(key) == state:
            return
        self._requested_session_states[key] = state
        self._pending_session_states[key] = state
        if self._session_save_task is None or self._session_save_task.done():
            self._session_save_task = asyncio.create_task(self._write_unsent_work())

    async def _write_unsent_work(self) -> None:
        # One ordered writer coalesces bursts of typing without an fsync on
        # the input loop or an older draft overwriting a newer one.
        while self._pending_session_states:
            key = next(iter(self._pending_session_states))
            state = self._pending_session_states.pop(key)
            draft, queue, paused = state
            try:
                await asyncio.to_thread(self._session_store.save, key, draft=draft, queue=queue, paused=paused)
                self._saved_session_states[key] = state
                if self.prefs.last_session != self.runtime.session_key:
                    self.prefs.last_session = self.runtime.session_key
                    await asyncio.to_thread(self.prefs.save)
            except OSError as exc:
                if self._requested_session_states.get(key) == state:
                    self._requested_session_states.pop(key, None)
                self.notify(f"Could not save unsent messages: {exc}", severity="error")

    async def _flush_unsent_work(self) -> None:
        self._save_unsent_work()
        if self._session_save_task is not None:
            await asyncio.shield(self._session_save_task)

    @on(TextArea.Changed, "Composer")
    def _save_composer_draft(self, _event: TextArea.Changed) -> None:
        self._save_unsent_work()

    @work(group="context-status", exclusive=True)
    async def _refresh_context_status(self) -> None:
        if self.runtime.turn_active:
            await asyncio.to_thread(self.runtime._refresh_status)
            self._set_status()

    def _queue_ready(self) -> bool:
        if not self._engine_ready or self.runtime.turn_active or self._awaiting_reply or self._pending_approvals or self._pending_choices:
            return False
        tasks = getattr(self.runtime.agent_loop, "_active_tasks", {}).get(self.runtime.session_key, [])
        if any(not task.done() for task in tasks):
            return False
        if self.runtime.bus is not None and self.runtime.bus.outbound_size:
            return False
        return self._current is None or self._current.finished

    def _stop_awaiting_grace_timer(self) -> None:
        timer = getattr(self, "_awaiting_grace_timer", None)
        if timer is not None:
            timer.stop()
            self._awaiting_grace_timer = None

    def _start_awaiting_grace_timer(self) -> None:
        self._stop_awaiting_grace_timer()
        self._awaiting_grace_timer = self.set_timer(
            self.AWAITING_REPLY_GRACE_S, self._clear_awaiting_reply
        )

    def _clear_awaiting_reply(self) -> None:
        """Grace period elapsed with no final answer: unblock the queue."""
        self._awaiting_grace_timer = None
        if not self._awaiting_reply:
            return
        self._awaiting_reply = False
        self._maybe_kick_queue()

    def _maybe_kick_queue(self) -> None:
        """Auto-send the next queued prompt once the session is idle."""
        key = self.runtime.session_key
        if (
            self._queue_ready()
            and self._queued_prompts.get(key)
            and key not in self._queue_paused
            and not self._queue_sending
        ):
            self._queue_sending = True
            self.call_later(self._send_next_queued)

    async def _refresh_queue(self) -> None:
        key = self.runtime.session_key
        queue = next(iter(self.query(PromptQueue)), None)
        await self._flush_unsent_work()
        # A durable write may finish after shutdown or a session switch.
        if queue is None or not queue.is_attached or key != self.runtime.session_key:
            return
        await queue.set_items(
            [(item.id, item.text) for item in self._queued_prompts.get(key, [])],
            paused=key in self._queue_paused,
        )

    async def _send_next_queued(self) -> None:
        key = self.runtime.session_key
        try:
            items = self._queued_prompts.get(key, [])
            if not items or key in self._queue_paused or not self._queue_ready():
                return
            item = items[0]
            if await self._send_prompt(item.text, item.inbound, restore_input=False):
                self._queued_prompts[key] = [entry for entry in self._queued_prompts.get(key, []) if entry.id != item.id]
            else:
                self._queue_paused.add(key)
        finally:
            self._queue_sending = False
            await self._refresh_queue()

    @on(QueuedPromptRow.Removed)
    async def _remove_queued_prompt(self, event: QueuedPromptRow.Removed) -> None:
        if self._queue_sending:
            return
        key = self.runtime.session_key
        self._queued_prompts[key] = [item for item in self._queued_prompts.get(key, []) if item.id != event.prompt_id]
        await self._refresh_queue()

    @on(QueuedPromptRow.Edited)
    async def _edit_queued_prompt(self, event: QueuedPromptRow.Edited) -> None:
        if self._queue_sending:
            return
        key = self.runtime.session_key
        item = next((row for row in self._queued_prompts.get(key, []) if row.id == event.prompt_id), None)
        if item is None:
            return
        draft = self.composer.expand_for_submit().strip()
        self._queued_prompts[key] = [row for row in self._queued_prompts.get(key, []) if row.id != event.prompt_id]
        if draft:
            self._queue_serial += 1
            inbound, _ = inbound_for_submit(self.prefs.mode, draft, turn_active=False)
            self._queued_prompts.setdefault(key, []).append(
                QueuedPrompt(self._queue_serial, draft, inbound))
        self.composer.set_text(item.text)
        await self._refresh_queue()
        self.composer.focus()

    @on(QueuedPromptRow.Sent)
    async def _send_queued_prompt_now(self, event: QueuedPromptRow.Sent) -> None:
        key = self.runtime.session_key
        item = next((row for row in self._queued_prompts.get(key, []) if row.id == event.prompt_id), None)
        if item is None or self._queue_sending or not self._engine_ready:
            return
        active = self.runtime.turn_active
        if not active and not self._queue_ready():
            # The reply is finishing: send this one first, as soon as it can go.
            items = self._queued_prompts.get(key, [])
            self._queued_prompts[key] = [item] + [row for row in items if row.id != item.id]
            self._queue_paused.discard(key)
            await self._refresh_queue()
            self._maybe_kick_queue()
            return
        self._queue_sending = True
        try:
            inbound = display_user_text(item.inbound) if active else item.inbound
            if await self._send_prompt(item.text, inbound, followup=active, restore_input=False):
                self._queued_prompts[key] = [row for row in self._queued_prompts.get(key, []) if row.id != item.id]
        finally:
            self._queue_sending = False
            await self._refresh_queue()
            self.composer.focus()

    @on(PromptQueue.Resumed)
    async def _resume_queued_prompts(self) -> None:
        self._queue_paused.discard(self.runtime.session_key)
        await self._refresh_queue()

    def _refresh_working_line(self) -> None:
        try:
            line = self.query_one("#working", WorkingLine)
        except Exception:  # noqa: BLE001
            return
        if not self.runtime.status.turn_active:
            line.set_line("")
            return
        line.set_line(
            format_working_line(
                elapsed_s=self.runtime.turn_elapsed_s,
                background=running_exec_count(),
            )
        )

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
        await self._refresh_queue()
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
        # The panel and the offer row below carry it; no toast over the prompt.
        with contextlib.suppress(Exception):
            self._one(Sidebar).set_update_available(latest)
        self.call_later(self._mount_update_offer, latest, text)

    async def _mount_update_offer(self, latest: str, detail: str) -> None:
        if not latest:
            return
        await self.transcript.add(UpdateOffer(latest, detail))

    # Textual restores the terminal only after unmount. A stuck MCP server,
    # subprocess or disk must never leave the shell without echo.
    UNMOUNT_WAIT_S = 3.0

    async def on_unmount(self) -> None:
        self._restore_terminal_background()
        # Also covers driver shutdown paths which do not call action_quit.
        if self._runtime_close_task is None:
            self._runtime_close_task = asyncio.create_task(self.runtime.close())
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._flush_unsent_work(), self.UNMOUNT_WAIT_S)
        self.prefs.last_session = self.runtime.session_key
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.to_thread(self.prefs.save), self.UNMOUNT_WAIT_S)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self._runtime_close_task), self.UNMOUNT_WAIT_S)

    # -- helpers ----------------------------------------------------------

    def _sync_terminal_background(self, _theme: str) -> None:
        # Cell backgrounds cannot paint the terminal's padding at the right
        # edge. OSC 11 aligns it with the app; OSC 111 restores it on exit.
        driver = self._driver
        if driver is None or driver.is_headless or driver.is_inline:
            return
        if self.ansi_color or self.no_color:
            self._restore_terminal_background()
            return
        background = self.current_theme.background
        if background:
            from textual.color import Color

            color = Color.parse(background).hex
            driver.write(f"\x1b]11;{color}\x1b\\")
            self._terminal_background_set = True

    def _restore_terminal_background(self) -> None:
        if getattr(self, "_terminal_background_set", False) and self._driver is not None:
            self._driver.write("\x1b]111\x1b\\")
            self._driver.flush()
            self._terminal_background_set = False

    # Toasts sit above the prompt; none may linger there.
    TOAST_MAX_S = {"information": 2.0, "warning": 3.0, "error": 4.0}

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
        markup: bool = False,
    ) -> None:
        # Messages carry paths and model text: literal by default.
        cap = self.TOAST_MAX_S.get(severity, 2.0)
        timeout = cap if timeout is None else min(timeout, cap)
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    def _one(self, widget_type: type[WidgetT]) -> WidgetT:
        """query_one by type, cached.

        A type query walks the DOM in order, through every transcript block
        before it reaches the prompt or the panel. The cache keeps the exact
        semantics: it only answers for the screen currently on top.
        """
        cache: dict[type, Any] = self.__dict__.setdefault("_widget_cache", {})
        widget = cache.get(widget_type)
        if widget is not None and widget.is_attached and widget.screen is self.screen:
            return widget
        widget = self.query_one(widget_type)
        cache[widget_type] = widget
        return widget

    @property
    def transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    @property
    def composer(self) -> Composer:
        return self._one(Composer)

    def _set_status(self, extra: str = "", *, animation_only: bool = False) -> None:
        st = self.runtime.status
        mode = get_mode(self.prefs.mode)
        reasoning = getattr(self, "_reasoning_label", "")
        if self._engine_ready and not animation_only:
            with contextlib.suppress(Exception):
                reasoning = self.runtime.reasoning_details()[0]
                self._reasoning_label = reasoning
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
                provider=st.provider,
                context_used=st.context_used,
                context_window=st.context_window,
                reasoning=reasoning,
            )
        except Exception:  # noqa: BLE001 - meta not mounted yet
            pass
        if not animation_only:
            with contextlib.suppress(Exception):
                self.query_one("#dock", DockBar).set_panel(self.prefs.sidebar)
            with contextlib.suppress(Exception):
                self._one(Sidebar).set_panel_label(self.prefs.sidebar)

    def _render_mode(self) -> None:
        self._refresh_side()

    def _refresh_side(self) -> None:
        with contextlib.suppress(Exception):
            self.runtime._refresh_status()
        side = self._one(Sidebar)
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
            self._one(Sidebar).set_agi(body)

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
            self._one(Sidebar).set_git(body)

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
            self._one(Sidebar).set_account(detail)

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

    NOTE_TTL_S = 6.0

    async def _flash(self, text: str, level: str = "info") -> None:
        """A notification that never stays in the chat.

        Engine events (checkpoint, model switch, compaction, stop request)
        are status, not conversation. They show briefly, leave as soon as
        the answer continues, and otherwise expire on their own.
        """
        note = SystemNote(text, level)
        note.transient = True
        await self.transcript.add(note)
        note.set_timer(self.NOTE_TTL_S, lambda: self._drop_note(note))

    def _drop_note(self, note: SystemNote) -> None:
        if note.is_attached:
            note.display = False
            note.remove()

    def _drop_trailing_flashes(self) -> None:
        for child in reversed(list(self.transcript.children)):
            if not getattr(child, "transient", False):
                break
            self._drop_note(child)

    async def _retire_display_error_note(self) -> None:
        if self._display_error_timer is not None:
            self._display_error_timer.stop()
            self._display_error_timer = None
        note, self._display_error_note = self._display_error_note, None
        if note is not None and note.is_attached:
            note.display = False
            await note.remove()

    async def _expire_display_error_note(self) -> None:
        # The timer is executing this callback. Stopping it here would cancel
        # this task while Textual is still removing the note.
        self._display_error_timer = None
        await self._retire_display_error_note()

    async def _retire_retry_wait_note(self) -> None:
        """Drop the stale "retrying" note once a turn runs again.

        The retry-wait note describes a pause, not a result. When the recovered
        turn starts, leaving it in the transcript reads as if the connection is
        still broken while the work has already resumed.
        """
        note = self._retry_wait_note
        if note is None:
            return
        self._retry_wait_note = None
        # Hide immediately; the DOM detach follows on the widget's own pump.
        note.display = False
        try:
            await note.remove()
        except Exception:
            pass

    def _activity_push(self, line: str) -> None:
        self._activity.append(line)
        self._activity = self._activity[-30:]
        self._one(Sidebar).set_activity(self._activity)

    def _model_label(self, slug: str | None = None) -> str:
        from navin.tui.widgets import split_model_slug

        raw = slug or self.runtime.status.model or ""
        name, _provider = split_model_slug(raw)
        return name or raw or "navin"

    async def _ensure_assistant(self) -> AssistantMessage:
        # Notes, prompts and update notices must not stay below live output.
        # Resume after them, just as we do after a mid-turn user message.
        # Live output resumes: brief notifications below it are stale.
        self._drop_trailing_flashes()
        # A temporary display warning disappears on recovery. It must not
        # leave an empty assistant segment behind when the next update works.
        tail = next((
            child for child in reversed(self.transcript.children)
            if child is not self._display_error_note and not getattr(child, "transient", False)
        ), None)
        if self._current is not None and (
            not self._current.is_attached
            or (tail is not None and tail is not self._current)
        ):
            await self._park_current_bubble()
        if self._current is None:
            self._current = AssistantMessage(
                self._model_label(), show_head=self._last_speaker != "assistant"
            )
            self._last_speaker = "assistant"
            block = self._current
            await self.transcript.add(block)
            return block
        if self._current.finished:
            self._current.finished = False
        return self._current

    async def _park_current_bubble(self) -> None:
        """Close the running assistant bubble when a mid-turn message lands.

        The follow-up then reads like a normal chat exchange: partial reply,
        user message, then the agent continues in a fresh bubble below it.
        Completed activity stays here; live tool cards move below the message
        when their next event arrives.
        """
        bubble = self._current
        self._current = None
        if bubble is None or bubble.finished:
            return
        with contextlib.suppress(Exception):
            await bubble.stream_end()
        self._parked_bubbles.append(bubble)

    def _bubble_for_tool(self, call_id: str) -> AssistantMessage | None:
        """The bubble owning a tool call card, current or parked."""
        if not call_id:
            return None
        if self._current is not None and self._current.has_tool(call_id):
            return self._current
        for bubble in self._parked_bubbles:
            if bubble.has_tool(call_id):
                return bubble
        return None

    async def _continue_tool_bubble(self, call_id: str) -> AssistantMessage:
        owner = self._bubble_for_tool(call_id)
        current = await self._ensure_assistant()
        if owner is not None and owner is not current:
            await owner.continue_tool_in(call_id, current)
            return current if current.has_tool(call_id) else owner
        return current

    async def _finish_parked_bubbles(
        self, *, latency_ms: int | None, model: str | None, preset: str | None
    ) -> None:
        bubbles, self._parked_bubbles = self._parked_bubbles, []
        for bubble in bubbles:
            if not bubble.is_attached or bubble.finished:
                continue
            with contextlib.suppress(Exception):
                await bubble.finish(
                    latency_ms=latency_ms, model=model, preset=preset
                )

    def _invalidate_history(self) -> int:
        """Stop any in-flight history paint (session switch, clear, reload)."""
        self._render_token += 1
        self._history_cursor = None
        self._history_snapshot = []
        for transcript in self.query(Transcript):
            transcript.has_older = False
            transcript.loading_history = False
            transcript.history_generation = self._render_token
        return self._render_token

    async def _render_history(self) -> None:
        token = self._invalidate_history()
        async with self._history_lock:
            if token != self._render_token:
                return
            await self._paint_history(token)

    async def _paint_history(self, token: int) -> None:
        from navin.tui.history import visible_chat_page

        source = await asyncio.to_thread(self.runtime.history_snapshot)
        rows, cursor = await asyncio.to_thread(visible_chat_page, source)
        if token != self._render_token:
            return
        self._history_snapshot, self._history_cursor = source, cursor
        transcript = self.transcript
        transcript.has_older = cursor is not None
        transcript.loading_history = True
        transcript.auto_follow = False
        was_visible = transcript.styles.visibility
        transcript.styles.visibility = "hidden"
        try:
            # Hide intermediate transcript mounts, not the whole application.
            # An app-wide batch across awaits also suppresses the composer,
            # navigation and stop feedback while a large page is restored.
            await self._paint_history_rows(token, rows)
            if token == self._render_token:
                with self.batch_update():
                    transcript.styles.visibility = was_visible
                    transcript.screen._refresh_layout()
                    transcript.scroll_end(animate=False, immediate=True)
                    transcript.screen.refresh(layout=True)
        finally:
            transcript.styles.visibility = was_visible
            if token == self._render_token:
                transcript.loading_history = False
                transcript.auto_follow = True

    @on(Transcript.OlderRequested)
    def _history_older_requested(self, event: Transcript.OlderRequested) -> None:
        if event.generation == self._render_token:
            self.run_worker(self._load_older_history(), group="older-history", exclusive=True)

    async def _load_older_history(self) -> None:
        from navin.tui.history import visible_chat_page

        token = self._render_token
        async with self._history_lock:
            if token != self._render_token:
                return
            if self._history_cursor is None:
                self.transcript.loading_history = False
                return
            transcript = self.transcript
            anchor = next(iter(transcript.children), None)
            old_y = anchor.virtual_region.y if anchor is not None else 0
            was_visible = transcript.styles.visibility
            try:
                rows, cursor = await asyncio.to_thread(
                    visible_chat_page, self._history_snapshot, before=self._history_cursor,
                )
                if token != self._render_token:
                    return
                transcript.styles.visibility = "hidden"
                await self._paint_history_rows(token, rows, before=anchor)
                if token != self._render_token:
                    return
                with self.batch_update():
                    self._history_cursor = cursor
                    transcript.has_older = cursor is not None
                    scroll_y = transcript.scroll_y
                    transcript.styles.visibility = was_visible
                    transcript.screen._refresh_layout()
                    if anchor is not None and anchor.is_attached:
                        transcript.scroll_to(y=scroll_y + anchor.virtual_region.y - old_y, animate=False, immediate=True)
                    transcript.auto_follow = False
                    transcript.screen.refresh(layout=True)
            finally:
                transcript.styles.visibility = was_visible
                if token == self._render_token:
                    transcript.loading_history = False

    async def _paint_history_rows(
        self, token: int, rows: list[dict[str, Any]], *, before: Any = None,
    ) -> None:
        # Rows are built hidden: a hidden block is not arranged, so the page
        # costs one layout when it is revealed instead of one per mount.
        painted: list[Any] = []
        try:
            await self._build_history_rows(token, rows, painted, before=before)
        finally:
            with self.batch_update():
                for widget in painted:
                    if widget.is_attached:
                        widget.display = True

    async def _build_history_rows(
        self, token: int, rows: list[dict[str, Any]], painted: list[Any], *, before: Any = None,
    ) -> None:
        def stale() -> bool:
            return token != self._render_token

        if stale():
            return
        if not rows:
            return
        show_tools = self.prefs.show_tools
        prev_role: str | None = None

        for row in rows:
            if stale():
                return
            if row["role"] == "user":
                message = UserMessage(row["content"], show_head=prev_role != "user")
                message.display = False
                painted.append(message)
                await self.transcript.mount(message, before=before)
                if stale():
                    return
                prev_role = "user"
                continue
            meta = row.get("metadata") or {}
            block = AssistantMessage(
                self._model_label(str(meta.get("model") or "") or None),
                show_head=prev_role != "assistant",
            )
            prev_role = "assistant"
            # Open before mounting: toggling -open later restyles the whole
            # message, tool cards and markdown included.
            block._open = True
            block.add_class("-open")
            block.display = False
            painted.append(block)
            await self.transcript.mount(block, before=before)
            if stale() or not block.is_attached:
                return
            await block.set_text(row.get("content") or "")
            for tool in row.get("tools") or []:
                if stale() or not block.is_attached:
                    return
                result = tool.get("result")
                name = str(tool.get("name") or "tool")
                args = tool.get("arguments") or {}
                await block.tool_event(
                    str(tool.get("id") or name),
                    name,
                    str(tool.get("phase") or "end"),
                    args,
                    result,
                    str(result) if tool.get("phase") == "error" else None,
                    None,
                    visible=show_tools,
                )
                edits = tool.get("file_edits") or []
                for payload in edits if show_tools else []:
                    if stale() or not block.is_attached:
                        return
                    event = UiFileEdit.from_payload(payload)
                    await block.note_file_edit(
                        event.path, event.added, event.removed, diff=event.diff,
                        call_id=event.call_id, kind=event.kind, phase=event.phase,
                        tool=event.tool, error=event.error, truncated=event.truncated,
                        binary=event.binary,
                    )
                plus, minus = extract_line_diff(result)
                if show_tools and not edits and (plus or minus) and tool.get("phase") != "error":
                    path = ""
                    if isinstance(args, dict):
                        path = str(args.get("path") or args.get("file_path") or "")
                    await block.note_file_edit(
                        path, plus, minus, call_id=str(tool.get("id") or name), kind="edit",
                    )
            if stale() or not block.is_attached:
                return
            await block.finish(
                latency_ms=meta.get("latency_ms"),
                model=meta.get("model"),
                preset=meta.get("model_preset"),
            )
        if stale():
            return

    # -- composer ---------------------------------------------------------

    @on(Composer.Submitted)
    async def _submitted(self, event: Composer.Submitted) -> None:
        menu = self._one(SlashMenu)
        typed = event.text.strip().lower()
        exact_command = any(str(row["command"]).lower() == typed for row in self.slash_rows)
        if menu.visible_menu and menu.highlighted is not None and not exact_command:
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
        await self.submit_text(text, send_now=event.send_now)

    async def submit_text(self, text: str, *, send_now: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        self._one(SlashMenu).hide()
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
        if not self._engine_ready:
            self.composer.set_text(text)
            await self._note("[$warning]engine is still starting…[/]", "warning")
            return
        if text.lower() in {"stop", "/stop"} and self.runtime.turn_active:
            await self._request_stop("/stop")
            return
        key = self.runtime.session_key
        ordinary_prompt = not text.startswith("/") or text.split(None, 1)[0].lower() in ROUTING_COMMANDS
        send_to_active = send_now and self.runtime.turn_active
        if ordinary_prompt and not send_to_active and (not self._queue_ready() or self._queued_prompts.get(key)):
            self._queue_serial += 1
            inbound, _ = inbound_for_submit(self.prefs.mode, text, turn_active=False)
            self._queued_prompts.setdefault(key, []).append(QueuedPrompt(self._queue_serial, text, inbound))
            await self._refresh_queue()
            return
        inbound, followup = inbound_for_submit(self.prefs.mode, text, turn_active=self.runtime.turn_active)
        if ordinary_prompt:
            self._queue_paused.discard(key)
        await self._send_prompt(text, inbound, followup=followup)

    async def _send_prompt(self, text: str, inbound: str, *, followup: bool = False, restore_input: bool = True) -> bool:
        user = UserMessage(text, show_head=self._last_speaker != "user")
        self._last_speaker = "user"
        await self.transcript.add(user)
        # A mid-turn follow-up is a normal chat exchange: the running bubble
        # is closed where it stands, the user message lands below it, and the
        # agent keeps answering in a fresh bubble below the message. Streaming
        # into the old bubble would pin the message at the very bottom of the
        # screen for the rest of the turn.
        if not followup:
            self._current = None
        else:
            await self._park_current_bubble()
        try:
            await self.runtime.send(inbound, followup=followup)
        except Exception as exc:  # noqa: BLE001 - retain the prompt and keep the chat open
            if not followup:
                self._awaiting_reply = False
                self.runtime._finish_turn({})
            await user.remove()
            if restore_input:
                self.composer.set_text(text)
            await self._note(f"[$error]Message not sent:[/] {escape(str(exc))}", "error")
            return False
        self._refresh_working_line()
        return True

    async def _run_tui_slash(self, text: str) -> bool:
        """Slash commands handled by the TUI itself (screens), not by the engine."""
        parts = text.split(maxsplit=1)
        head = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        raw_arg = arg.strip()
        arg = raw_arg.lower()
        if head in {"/quit", "/exit"}:
            await self.action_quit()
            return True
        if head == "/permission" and self._engine_ready:
            await self.runtime.send_command(text)
            return True
        if head == "/settings":
            await self.action_open_settings(arg)
            return True
        if head == "/import":
            await self.action_import_sessions()
            return True
        if head == "/model" and not arg:
            await self.action_open_settings("models")
            return True
        if head[1:] in _SETTINGS_SECTIONS and not arg:
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
        if head == "/paste":
            self.action_paste_composer()
            return True
        if head == "/ps":
            await self.action_list_processes()
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
        menu = self._one(SlashMenu)
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
        self._one(SlashMenu).hide()
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
        menu = self._one(SlashMenu)
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
        await self._render_runtime_event(event)
        if not isinstance(event, UiDisplayError):
            await self._retire_display_error_note()

    async def _render_runtime_event(self, event: UiEvent) -> None:
        if isinstance(event, UiDisplayError):
            if self._display_error_note is None or not self._display_error_note.is_attached:
                note = SystemNote(escape(event.text), "quiet")
                self._display_error_note = note
                await self.transcript.add(note)
                self._display_error_timer = self.set_timer(5, self._expire_display_error_note)
            return
        if isinstance(event, UiTurnStarted):
            self._stop_pending = False
            self._awaiting_reply = True
            self._stop_awaiting_grace_timer()
            await self._retire_retry_wait_note()
            self._set_status()
            self._refresh_working_line()
            return
        if isinstance(event, UiStreamDelta):
            block = await self._ensure_assistant()
            await block.delta(event.text)
            return
        if isinstance(event, UiStreamEnd):
            if self._current is not None:
                await self._current.stream_end(resuming=event.resuming)
                self.transcript.follow()
            return
        if isinstance(event, UiReasoning):
            if not self.prefs.show_reasoning:
                return
            block = await self._ensure_assistant()
            await block.reasoning(event.text, end=event.end)
            self.transcript.follow()
            return
        if isinstance(event, UiToolEvent):
            block = await self._continue_tool_bubble(event.call_id)
            await block.tool_event(
                event.call_id,
                event.name,
                event.phase,
                event.arguments,
                event.result,
                event.error,
                event.output,
                visible=self.prefs.show_tools,
                percent=event.percent,
                output_mode=event.output_mode,
            )
            if event.phase == "start":
                self._activity_push(f"⟳ {escape(event.name)}")
            elif event.phase == "error":
                self._activity_push(f"[$error]✗ {escape(event.name)}[/]")
            elif event.phase == "end":
                self._activity_push(f"✓ {escape(event.name)}")
            self.transcript.follow()
            return
        if isinstance(event, UiFilePreview):
            self._open_file_preview(event.path)
            return
        if isinstance(event, UiFileEdit):
            if self.prefs.show_tools:
                block = await self._continue_tool_bubble(event.call_id)
                await block.note_file_edit(
                    event.path, event.added, event.removed, diff=event.diff,
                    call_id=event.call_id, kind=event.kind, phase=event.phase,
                    tool=event.tool, error=event.error, truncated=event.truncated,
                    binary=event.binary,
                )
            if event.path:
                from navin.utils.tool_hints import activity_label

                self._activity_push(
                    escape(activity_label(
                        event.tool, {}, path=event.path, operation=event.kind,
                        phase=event.phase, added=event.added, removed=event.removed,
                        counts_known=not event.binary,
                    ))
                )
            self.transcript.follow()
            return
        if isinstance(event, UiProgress):
            if self.prefs.show_tools:
                block = await self._ensure_assistant()
                await block.progress(event.text)
                self.transcript.follow()
            return
        if isinstance(event, UiSubagent):
            # Agents often outlive the turn that started them. Their live state
            # belongs in the panel under the chat, not in new chat bubbles;
            # each one's result still arrives as a normal reply.
            panel = self._agents_panel()
            first = panel is not None and event.task_id not in panel._agents
            if panel is not None:
                panel.upsert(
                    event.task_id, label=event.label, status_line=event.status_line,
                    phase=event.phase, done=event.done, error=event.error,
                    started_ms_ago=event.started_ms_ago, tokens=event.tokens,
                    task=event.task_description,
                )
            if first or event.done:
                mark = "✗" if event.error else "✓" if event.done else "◯"
                self._activity_push(f"{mark} {escape(event.label)}")
            return
        if isinstance(event, UiAssistantMessage):
            self._awaiting_reply = False
            # The turn-end signal can overtake the final message. Keep writing
            # into the same bubble instead of opening a second one mid-sentence.
            block = await self._ensure_assistant()
            if block.text.strip() != event.text.strip():
                await block.set_text(event.text, render_as=event.render_as)
            else:
                await block.stream_end()
            if not self.runtime.turn_active and not block.finished:
                await block.finish(
                    latency_ms=event.metadata.get("latency_ms"),
                    model=event.metadata.get("model"),
                    preset=event.metadata.get("model_preset"),
                )
            if not self.runtime.turn_active:
                await self._finish_parked_bubbles(
                    latency_ms=event.metadata.get("latency_ms"),
                    model=event.metadata.get("model"),
                    preset=event.metadata.get("model_preset"),
                )
            self.transcript.follow()
            return
        if isinstance(event, UiTurnEnd):
            self._stop_pending = False
            self._refresh_working_line()
            if self._awaiting_reply:
                # The turn ended but no final assistant message arrived (stop,
                # error path, empty answer). Give the answer a short grace
                # period to overtake the turn-end signal, then unblock the
                # queue so a queued prompt still goes out automatically.
                self._start_awaiting_grace_timer()
            st = self.runtime.status
            if self._current is not None and not self._current.finished:
                await self._current.finish(
                    latency_ms=event.latency_ms, model=st.model, preset=st.model_preset
                )
            await self._finish_parked_bubbles(
                latency_ms=event.latency_ms, model=st.model, preset=st.model_preset
            )
            self._refresh_side()
            self._load_account()
            self.transcript.follow()
            return
        if isinstance(event, UiModelUpdated):
            self._refresh_side()
            if event.reason and event.model:
                await self._flash(
                    f"model → [b]{escape(event.model)}[/b] [dim]({escape(event.reason)})[/dim]"
                )
            return
        if isinstance(event, UiContextCompacted):
            before = f"{event.tokens_before:,}" if event.tokens_before else "?"
            after = f"{event.tokens_after:,}" if event.tokens_after else "?"
            await self._flash(
                f"context compacted ({escape(event.kind)}): {event.messages_archived} messages archived, {before} → {after} tokens"
            )
            self._refresh_side()
            return
        if isinstance(event, UiCheckpointSaved):
            self._activity_push(f"⎘ checkpoint {escape(event.name)}")
            if not event.auto:
                await self._flash(f"checkpoint saved: [b]{escape(event.name)}[/b]", "success")
            return
        if isinstance(event, UiNotification):
            detail = f"\n[dim]{escape(event.detail)}[/dim]" if event.detail else ""
            level = event.level if event.level in {"warning", "error", "success"} else "info"
            # Errors stay in the chat; everything else is a passing status.
            show = self._note if level == "error" else self._flash
            await show(f"{escape(event.title)}{detail}", level)
            return
        if isinstance(event, UiRetryWait):
            await self._retire_retry_wait_note()
            note = SystemNote(f"[$warning]{escape(event.text)}[/]", "warning")
            self._retry_wait_note = note
            await self.transcript.add(note)
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
            self._set_status("[$warning]approval pending: y / a / n[/]")
            return
        if isinstance(event, UiApprovalClosed):
            card = self._pending_approvals.pop(event.request_id, None)
            self._sync_shortcuts()
            if card is not None:
                card.close(event.allowed, event.reason)
                await self._remove_prompt(card)
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
            return
        if isinstance(event, UiChoiceClosed):
            card = self._pending_choices.pop(event.request_id, None)
            self._sync_shortcuts()
            if card is not None:
                card.close(event.option_id, event.skipped)
                await self._remove_prompt(card)
            return
        if isinstance(event, UiEngineError):
            self._awaiting_reply = False
            self._queue_paused.add(self.runtime.session_key)
            await self._refresh_queue()
            await self._note(f"[$error]{escape(event.text)}[/]", "error")
            return

    async def _remove_prompt(
        self, card: ApprovalCard | ChoiceCard, *, resume_follow: bool = False
    ) -> None:
        # A resolved card below the active assistant hides its continuing output.
        await card.remove()
        self.composer.focus()
        if resume_follow:
            self.transcript.auto_follow = True
            self.transcript.scroll_end(animate=False)
        else:
            self.transcript.follow()

    @on(ApprovalCard.Decided)
    async def _approval_decided(self, event: ApprovalCard.Decided) -> None:
        card = self._pending_approvals.pop(event.request_id, None)
        if card is None:
            return
        self._sync_shortcuts()
        card.close(event.allowed, "remembered" if event.remember else "")
        await self._remove_prompt(card, resume_follow=True)
        await self.runtime.approve(event.request_id, allowed=event.allowed, remember=event.remember)
        self._set_status()

    @on(ChoiceCard.Answered)
    async def _choice_answered(self, event: ChoiceCard.Answered) -> None:
        card = self._pending_choices.pop(event.request_id, None)
        if card is None:
            return
        self._sync_shortcuts()
        card.close(event.option_id, event.skipped, event.custom_text)
        await self._remove_prompt(card, resume_follow=True)
        await self.runtime.answer_choice(
            event.request_id,
            option_id=event.option_id,
            skipped=event.skipped,
            custom_text=event.custom_text,
        )

    # -- actions ----------------------------------------------------------

    async def action_list_processes(self) -> None:
        try:
            from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

            rows = DEFAULT_EXEC_SESSION_MANAGER.running_snapshot()
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            await self._note("No background terminals.")
            return
        lines = ["background terminals"]
        for info in rows:
            command = " ".join(info.command.split())
            if len(command) > 80:
                command = command[:79] + "…"
            lines.append(
                f"{info.session_id}  {format_elapsed(info.elapsed_s)}  {command}"
            )
        await self._note("\n".join(lines))

    async def action_stop_turn(self) -> None:
        if len(self.screen_stack) > 1:
            return
        menu = self._one(SlashMenu)
        if menu.visible_menu:
            menu.hide()
            self.composer.menu_open = False
            self._navigation_closed_at = time.monotonic()
            return
        bar = self._one(FindBar)
        if bar.display:
            bar.hide()
            self.composer.focus()
            self._navigation_closed_at = time.monotonic()
            return
        if self._pending_choices:
            card = next(iter(self._pending_choices.values()))
            if card.allow_skip:
                card.answer(skipped=True)
                return
        if time.monotonic() - self._navigation_closed_at < 0.5:
            return
        await self._request_stop("Esc")

    async def action_request_stop(self) -> None:
        await self._request_stop("Stop turn")

    async def _request_stop(self, source: str) -> None:
        if self.runtime.turn_active:
            if self._stop_pending:
                return
            self._stop_pending = True
            self._queue_paused.add(self.runtime.session_key)
            self._save_unsent_work()
            try:
                await self.runtime.stop_turn()
            except Exception:
                self._stop_pending = False
                raise
            self.run_worker(self._refresh_queue(), group="stop-queue", exclusive=True)
            self._refresh_working_line()
            await self._flash(f"[$warning]Stop requested ({escape(source)})[/]", "warning")
            return
        self.composer.focus()

    QUIT_PRESS_WINDOW_S = 1.5

    async def action_interrupt_or_clear(self) -> None:
        """Copy, clear input, or quit on a second press with an empty prompt.

        Escape and /stop interrupt the agent; Ctrl+C never does.
        """
        selected = self._selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        if len(self.screen_stack) > 1 or isinstance(self.focused, Input):
            raise SkipAction()
        if isinstance(self.focused, TextArea) and not isinstance(self.focused, Composer):
            raise SkipAction()
        now = time.monotonic()
        if not self.composer.text:
            # Still cancels a paste in flight, so it cannot land afterwards.
            self.composer.clear_text()
            if now - self._quit_armed_at <= self.QUIT_PRESS_WINDOW_S:
                await self.action_quit()
                return
            self._quit_armed_at = now
            self.notify("Press Ctrl+C again to quit", timeout=self.QUIT_PRESS_WINDOW_S)
            return
        self._quit_armed_at = 0.0
        self.composer.clear_text()
        self._one(SlashMenu).hide()
        self.composer.menu_open = False
        self._refresh_working_line()
        self.composer.focus()

    async def action_new_chat(self) -> None:
        if not self._engine_ready:
            return
        await self.submit_text("/new")
        self._current = None
        self._parked_bubbles = []
        self._last_speaker = None
        self._activity.clear()
        self._refresh_side()

    async def action_clear_transcript(self) -> None:
        self._invalidate_history()
        async with self._history_lock:
            had_chat = any(
                isinstance(widget, (UserMessage, AssistantMessage))
                for widget in self.transcript.children
            )
            await self.transcript.remove_children()
            self._current = None
            self._parked_bubbles = []
            self._last_speaker = None
            if had_chat:
                await self._note(
                    "Screen cleared. Ctrl+L again reloads this chat.",
                    "quiet",
                )
                return
            token = self._invalidate_history()
            await self._paint_history(token)

    def action_toggle_sidebar(self) -> None:
        self.prefs.sidebar = not self.prefs.sidebar
        self.prefs.sidebar_explicit = True
        self.prefs.save()
        self._one(Sidebar).set_class(self.prefs.sidebar, "-visible")
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
        from navin.update import service

        try:
            info = await asyncio.to_thread(service.check_for_update, force=True)
        except Exception as exc:  # noqa: BLE001 - an unavailable server is not "up to date"
            await self._note(f"[$error]Update check failed:[/] {escape(str(exc))}", "error")
            return
        self._update_info = dict(info)
        if not info.get("configured"):
            await self._note("This build has no update server configured.", "warning")
            return
        latest = str(info.get("latestVersion") or "").strip()
        if not info.get("available") or not latest:
            if info.get("reason"):
                await self._note(f"navin {escape(latest)}: {escape(str(info['reason']))}", "warning")
                return
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
        bar = self._one(FindBar)
        bar.show(seed.strip())
        if seed.strip():
            self._find_move(seed.strip(), 0)

    def action_paste_composer(self) -> None:
        if isinstance(self.focused, (Input, TextArea)) and not isinstance(self.focused, Composer):
            self.run_worker(self._paste_into_field(self.focused), group="clipboard", exclusive=True)
            return
        self.composer.focus()
        self.composer.action_paste_any()

    async def _paste_into_field(self, field: Input | TextArea) -> None:
        from navin.tui.clipboard import pick_paste_text, read_clipboard

        try:
            os_text = await asyncio.to_thread(read_clipboard)
        except Exception:  # noqa: BLE001 - keep modal inputs usable on clipboard errors
            os_text = ""
        text = pick_paste_text(self.clipboard, os_text)
        if text and field.is_mounted and field is self.focused:
            field.post_message(events.Paste(text))

    async def on_event(self, event: events.Event) -> None:
        # Terminal-managed Ctrl+V arrives as Paste even when the transcript
        # has focus. Route it to the composer instead of dropping the payload.
        if not isinstance(event, events.Paste) or isinstance(self.focused, (Input, TextArea)):
            await super().on_event(event)
            return
        event.prevent_default()
        event.stop()
        self.composer.focus()
        self.composer._insert_paste(event.text or "", source="terminal")

    def _find_move(self, query: str, direction: int) -> None:
        needle = query.strip().lower()
        bar = self._one(FindBar)
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
        if not hit.display:
            # Old turns leave layout while the chat follows the bottom.
            self.transcript.reveal_windowed(through=hit)
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

    def action_page_transcript(self, direction: int = -1) -> None:
        """Page the conversation. Works while the prompt has focus."""
        self.transcript.page(int(direction))

    @on(Composer.PageChat)
    def _page_chat(self, event: Composer.PageChat) -> None:
        self.action_page_transcript(event.direction)

    @on(Composer.ChatScroll)
    def _chat_scroll(self, event: Composer.ChatScroll) -> None:
        self.transcript.nudge(event.delta)

    def _open_file_preview(self, raw_path: str) -> None:
        """Show a file the agent asked to preview (open_file_preview tool)."""
        from pathlib import Path as _Path

        path = _Path(raw_path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.notify(f"Cannot preview {path.name}: {exc}", severity="error", timeout=3)
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.notify(
                f"{path} is binary - open it with a desktop viewer.",
                timeout=3,
            )
            return
        if len(text) > 60000:
            text = text[:60000] + "\n\n... (truncated)"
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            body = text
        else:
            body = f"**{path}**\n\n```\n{text}\n```"
        self.call_later(self._push_preview_screen, body)

    def _push_preview_screen(self, markdown: str) -> None:
        self.run_worker(self._push_preview_wait(markdown), exclusive=True, group="picker")

    async def _push_preview_wait(self, markdown: str) -> None:
        await self.push_screen(MarkdownScreen(markdown))

    def _write_last_copy(self, text: str) -> str:
        root = Path(getattr(self.runtime.status, "workspace", "") or self.project_root)
        path = root / ".navin" / "last-copy.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            return ""
        return str(path)

    def copy_to_clipboard(self, text: str, *, to_os: bool = True, quiet: bool = False) -> bool:
        """Keep the in-app copy always. Paste with Ctrl+V / Cmd+V."""
        from navin.tui.clipboard import osc52_allowed
        from navin.utils.tool_hints import clip_transcript

        payload = clip_transcript(text)
        if not payload:
            return False
        self._clipboard = payload
        if to_os and osc52_allowed(payload):
            # Instant, and the only path over SSH; the host write follows.
            super().copy_to_clipboard(payload)
        self._pending_copy = (payload, to_os)
        if not self._copy_running:
            self._copy_running = True
            self.run_worker(self._flush_clipboard(), group="clipboard-write")
        if not quiet:
            lines = payload.count("\n") + 1
            self.notify(f"Copied {lines} lines" if lines > 1 else "Copied", timeout=1.5)
        return True

    async def _flush_clipboard(self) -> None:
        """Serialize host writes and coalesce selection changes off the UI thread."""
        from navin.tui.clipboard import osc52_allowed, write_os_clipboard

        def write(payload: str, to_os: bool) -> None:
            # A missing codec or unavailable host backend must not stop typing.
            written = False
            with contextlib.suppress(Exception):
                if to_os:
                    written = write_os_clipboard(payload)
            if to_os and not written and not osc52_allowed(payload):
                # No host backend: leave a file the user can open.
                self._write_last_copy(payload)

        try:
            while self._pending_copy is not None:
                await asyncio.sleep(0.08)
                pending, self._pending_copy = self._pending_copy, None
                if pending is not None:
                    await asyncio.to_thread(write, *pending)
        finally:
            self._copy_running = False

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
                if isinstance(widget, AssistantMessage):
                    last = widget
        if last is None:
            return ""
        return last.copy_text()

    def _last_copyable_text(self) -> str:
        # Newest message first; passing notifications are not content.
        with contextlib.suppress(Exception):
            for widget in reversed(self.transcript.children):
                if getattr(widget, "transient", False):
                    continue
                copy = getattr(widget, "copy_text", None)
                if callable(copy):
                    text = copy()
                    if text.strip():
                        return text
        return ""

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
        if not selected:
            return
        # Selecting copies, like a terminal. Writes are coalesced off the UI
        # thread, so dragging stays smooth.
        self.copy_to_clipboard(selected, quiet=True)

    def copy_from_pointer(self) -> None:
        """Right-click: copy the selection or the last message."""
        from navin.tui.clipboard import pointer_copy_text

        text = pointer_copy_text(self._selected_text(), self._last_copyable_text())
        if not text:
            return
        self.copy_to_clipboard(text)

    def action_copy_reply(self) -> None:
        """Copy selected text, or the last message (up to 5000 lines)."""
        selected = self._selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        last = self._last_copyable_text() or self._last_assistant_text()
        if not last.strip():
            self.notify("Nothing to copy", severity="warning", timeout=1.5)
            return
        self.copy_to_clipboard(last)

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
        await self._flash(f"transcript exported to [b]{escape(str(path))}[/b]", "success")

    # -- pickers ----------------------------------------------------------

    def _spawn(self, coro: Any) -> None:
        """Run a coroutine that awaits ``push_screen_wait`` (needs a worker)."""
        self.run_worker(coro, exclusive=True, group="picker")

    def action_pick_model(self) -> None:
        self._spawn(self._pick_model())

    def action_pick_reasoning(self) -> None:
        self._spawn(self._pick_reasoning())

    async def _pick_reasoning(self) -> None:
        if not self._engine_ready:
            return
        current, options = self.runtime.reasoning_details()
        chosen = await self.push_screen_wait(PickerScreen(
            "Reasoning effort",
            [PickItem(value, label) for value, label in options],
            current=current,
            hint=("Native reasoning level for the next turns of this chat."
                  if len(options) > 1 else "This model manages reasoning automatically."),
        ))
        if chosen is not None:
            try:
                self.runtime.set_reasoning_effort(chosen)
            except Exception as exc:  # noqa: BLE001
                await self._note(escape(str(exc)), "error")
                return
            self._set_status()

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
        rows = await asyncio.to_thread(self.runtime.preset_details)
        items = model_pick_items(rows)
        chosen = await self.push_screen_wait(
            ModelPickerScreen(
                "Models",
                items,
                hint="Applies to the next turns of this runtime (same as /model).",
                current=self.runtime.status.model_preset,
            )
        )
        if chosen:
            if chosen.startswith("__settings__:"):
                await self.action_open_settings(chosen.split(":", 1)[1])
                return
            row = next(row for row in rows if row["name"] == chosen)
            modality = row.get("modality", "text")
            if modality != "text":
                await self.action_open_settings({"audio": "voice", "music": "voice", "stt": "voice"}.get(modality, modality))
            else:
                await self._apply_preset(chosen)

    async def _apply_preset(self, name: str) -> None:
        try:
            self.runtime.set_model_preset(name)
        except Exception as exc:  # noqa: BLE001
            await self._note(f"[$error]{escape(str(exc))}[/]", "error")
            return
        self._refresh_side()

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
        # Routing applies when submitting the next prompt. Keep current work alive.
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
        items = [PickItem("__new__", "New session", "", ""),
                 PickItem("__import__", "Import chats", "Claude Code, Codex, OpenCode, OMP, Cursor")]
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

    async def action_import_sessions(self) -> None:
        from navin.tui.session_import import SessionImportScreen

        await self.push_screen(
            SessionImportScreen(self.runtime.workspace),
            lambda imported: self._refresh_side() if imported else None,
        )

    async def _pick_session(self) -> None:
        if not self._engine_ready:
            return
        while True:
            items = await asyncio.to_thread(self._session_pick_items)
            chosen = await self.push_screen_wait(
                PickerScreen(
                    "Sessions",
                    items,
                    hint="Type to search. F2 renames. Enter opens.",
                    current=self.runtime.session_key,
                    renamable=True,
                )
            )
            if not chosen:
                return
            if chosen == "__import__":
                from navin.tui.session_import import SessionImportScreen

                await self.push_screen_wait(SessionImportScreen(self.runtime.workspace))
                self._refresh_side()
                continue
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
        await self._flash(f"chat name -> {escape(name)}", "success")

    async def _switch_session(self, key: str) -> None:
        # Abort the boot (or previous) history paint before tearing widgets down.
        # Opening another session while the first one is still loading used to
        # crash with NoMatches on .assistant-preview / .assistant-foot.
        self._invalidate_history()
        async with self._history_lock:
            await self.runtime.switch_session(key)
            self.prefs.last_session = key
            self.prefs.save()
            await self.transcript.remove_children()
            self._current = None
            self._parked_bubbles = []
            self._last_speaker = None
            self._activity.clear()
            token = self._invalidate_history()
            await self._paint_history(token)
            self._refresh_side()
            await self._flash(f"session → [b]{escape(key)}[/b]")

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
        if self._quitting:
            return
        self._quitting = True
        # Capture the composer while it is still mounted and pause the queue
        # before Textual disables input and starts tearing down the transcript.
        self._queue_paused.update(self._queued_prompts)
        self._save_unsent_work()
        self._runtime_close_task = asyncio.create_task(self.runtime.close())
        self.exit()

    # Textual system commands: keep the built-ins (theme, keys...) too.
    def get_system_commands(self, screen: Screen):  # type: ignore[override]
        yield from super().get_system_commands(screen)
        yield SystemCommand("Navin help", "Keys, modes and slash commands", self.action_show_help)
        yield SystemCommand("Update Navin", "Install the latest signed release", self.action_update)
        yield SystemCommand("Import chats", "Claude Code, Codex, OpenCode, OMP and Cursor", self.action_import_sessions)
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
    cli_executable: Path | None = None,
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
    previous_signals = {}
    for name in ("SIGINT", "SIGTSTP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            with contextlib.suppress(ValueError, OSError):
                previous_signals[signum] = signal.signal(signum, signal.SIG_IGN)
    app = NavinApp(config, session_id=session_id, prefs=prefs, config_path=config_path)
    from navin.tui.shutdown import TerminalGuard, run_app_fast_exit

    guard = TerminalGuard()
    try:
        run_app_fast_exit(app)
    finally:
        guard.restore()
        for signum, handler in previous_signals.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(signum, handler)
    from navin.tui.exit_summary import UsageTotals, print_exit_summary

    runtime = app.runtime
    title = ""
    sessions = getattr(runtime.agent_loop, "sessions", None)
    if sessions is not None:
        # No disk access on the way out: use the session already in memory.
        session = sessions._cache.get(runtime.session_key)
        if session is not None:
            title = str(session.metadata.get("title") or session.metadata.get("webui_title") or "")
    print_exit_summary(
        session_key=runtime.session_key, workspace=Path(runtime.config.workspace_path),
        elapsed=runtime.worked_seconds, usage=runtime.usage.sessions.get(runtime.session_key, UsageTotals()),
        title=title, config_path=config_path, warnings=runtime.shutdown_warnings, executable=cli_executable,
    )
    from navin.tui.shutdown import exit_if_threads_linger

    exit_if_threads_linger()
