"""Settings: the Navin Desktop settings page, in the terminal.

Same sections, same fields, same order of importance as the desktop app
(Providers and Models first). Only what the desktop exposes is shown: no raw
``api`` / ``updates`` / ``license`` subtrees, no internal knobs. Every write goes
through the schema (``Config.model_validate``) or through the very same
``settings_api`` helpers the desktop calls, so the TUI can never produce a
``config.json`` the desktop would reject.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from navin.tui.hubs import (
    HubRow,
    mcp_enable_preset,
    mcp_preset_fields,
    mcp_preset_rows,
    mcp_remove_server,
    mcp_rows,
    provider_rows,
    skill_rows,
    skill_set_enabled,
)
from navin.tui.screens import FormField, FormScreen, PickerScreen, PickItem

# ---------------------------------------------------------------------------
# Field / section model
# ---------------------------------------------------------------------------

Path_ = tuple[str, ...]

# Field kinds: text, secret, number, toggle, tristate (auto/on/off), select,
# list (strings), info (read-only line), action (Enter runs a handler).


@dataclass(frozen=True)
class Field:
    label: str
    path: Path_ = ()
    kind: str = "text"
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()  # (value, label)
    minimum: float | None = None
    maximum: float | None = None
    placeholder: str = ""
    restart: bool = False
    invert: bool = False  # toggle displayed inverted (headless -> "show window")
    scope: str = "config"  # config | autonomy | info | action
    action: str = ""  # for kind == "action" / dynamic rows
    text: str = ""  # info rows
    key: str = ""  # stable id for dynamic rows
    nullable: bool = False  # empty text / "" choice is stored as null
    default: Any = None  # written back when the user clears the value

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    description: str
    kind: str = "form"  # form | providers | models | mcp | skills | rules | account | about
    fields: tuple[Field, ...] = ()
    keys: tuple[tuple[str, str], ...] = dc_field(
        default_factory=tuple
    )  # (key, label) shown in the footer


CONTEXT_WINDOWS: tuple[tuple[str, str], ...] = tuple(
    (v, f"{int(v):,}")
    for v in ("65536", "131072", "200000", "262144", "400000", "1000000", "2000000")
)
REASONING: tuple[tuple[str, str], ...] = (
    ("", "auto"),
    ("low", "low"),
    ("medium", "medium"),
    ("high", "high"),
)
ROUTE_ROLES: tuple[tuple[str, str], ...] = (
    ("deep", "Deep reasoning"),
    ("dev", "Development"),
    ("fast", "Fast answers"),
    ("code", "Code edits"),
    ("vision", "Vision"),
    ("search", "Search"),
    ("plan", "Planning"),
    ("review", "Review"),
    ("security", "Security"),
    ("docs", "Documentation"),
)
IMAGE_ASPECTS = tuple((v, v) for v in ("1:1", "3:4", "9:16", "4:3", "16:9", "3:2", "2:3", "21:9"))
IMAGE_SIZES = tuple((v, v) for v in ("1K", "2K", "4K", "1024x1024", "1536x1024", "1024x1536"))
VIDEO_ASPECTS = tuple((v, v) for v in ("16:9", "9:16", "1:1"))
VIDEO_RESOLUTIONS = (("", "auto"), ("720p", "720p"), ("1080p", "1080p"))
MIN_CPU = 4
MIN_RAM_GB = 8


def _get(data: Any, path: Path_, default: Any = None) -> Any:
    cur = data
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def _set(data: dict[str, Any], path: Path_, value: Any) -> None:
    cur: Any = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def _mask(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "[dim]not set[/dim]"
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def _provider_choices(
    data: dict[str, Any], *, auto_label: str = "auto"
) -> tuple[tuple[str, str], ...]:
    """Configured providers (a key, a local base URL or a leftover custom slot)."""
    specs: tuple[Any, ...] = ()
    try:
        from navin.providers.registry import PROVIDERS

        specs = tuple(PROVIDERS)
    except Exception:  # noqa: BLE001
        specs = ()
    try:
        from navin.providers.settings_order import is_retired_llm_provider
    except Exception:  # noqa: BLE001
        def is_retired_llm_provider(_name: str) -> bool:
            return False
    providers = data.get("providers") or {}
    out: list[tuple[str, str]] = [("", auto_label)]
    seen: set[str] = set()
    for spec in specs:
        if is_retired_llm_provider(spec.name):
            continue
        alias = _alias(spec.name)
        section = providers.get(alias) or providers.get(spec.name) or {}
        if not isinstance(section, dict):
            continue
        configured = (
            bool(section.get("apiKey"))
            or bool(section.get("oauthKey"))
            or (spec.is_local and section.get("apiBase"))
            or spec.name == "navin"
        )
        if configured:
            out.append((spec.name, spec.display_name or spec.name))
            seen.add(spec.name)
    for alias, section in sorted(providers.items()):
        if alias in seen or not isinstance(section, dict) or not section.get("apiKey"):
            continue
        if is_retired_llm_provider(str(alias)):
            continue
        out.append((alias, alias))
    return tuple(out)


def _alias(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _machine_line() -> tuple[str, bool]:
    """Detected cores / RAM and whether the machine meets the minimum."""
    cores = os.cpu_count() or 0
    ram_gb = 0.0
    try:
        from navin.agent.resources import usable_cores, usable_memory_bytes

        cores = usable_cores() or cores
        mem = usable_memory_bytes()
        if mem:
            ram_gb = mem / (1024**3)
    except Exception:  # noqa: BLE001
        pass
    ok = cores >= MIN_CPU and (ram_gb == 0 or ram_gb >= MIN_RAM_GB)
    ram = f"{ram_gb:.0f} GB" if ram_gb else "unknown"
    mark = "[$success]✓[/]" if ok else "[$warning]![/]"
    return (
        f"{mark} {cores} CPU · {ram} RAM  [dim](minimum {MIN_CPU} CPU / {MIN_RAM_GB} GB)[/dim]",
        ok,
    )


def build_sections(
    data: dict[str, Any], *, project_root: Path, version: str, config_label: str, workspace: str
) -> tuple[Section, ...]:
    """Sections with their options resolved against the current config."""
    providers = _provider_choices(data)
    media_providers = _provider_choices(data, auto_label="none (disabled)")
    try:
        from navin.agent.tools.web import SEARCH_PROVIDER_OPTIONS

        search_options = tuple((str(o["name"]), str(o["label"])) for o in SEARCH_PROVIDER_OPTIONS)
    except Exception:  # noqa: BLE001
        search_options = (
            ("duckduckgo", "DuckDuckGo"),
            ("brave", "Brave Search"),
            ("exa", "Exa"),
            ("tavily", "Tavily"),
        )
    from navin.optional_live import live_modules_available

    machine, _ = _machine_line()
    kill = _get(data, ("tools", "boardGit"), {}) or {}
    kill_line = (
        f"global switches: auto-branch {'on' if kill.get('autoBranchEnabled', True) else 'off'} · "
        f"pull requests {'on' if kill.get('openPrEnabled', True) else 'off'}  [dim](Git section)[/dim]"
    )
    return (
        Section(
            "providers",
            "Providers",
            "API keys, endpoints and connection options for every model provider.",
            kind="providers",
            keys=(("enter", "configure"), ("t", "test"), ("d", "disconnect")),
        ),
        Section(
            "models",
            "Models",
            "Model configurations, the active default and task routing.",
            kind="models",
            keys=(("enter", "edit"), ("u", "use"), ("a", "add"), ("d", "delete"), ("r", "routing")),
        ),
        Section(
            "mcp",
            "Tools & MCP",
            "MCP servers and presets that extend the agent's tools.",
            kind="mcp",
            keys=(
                ("enter", "edit"),
                ("i", "enable preset"),
                ("c", "custom server"),
                ("r", "remove"),
            ),
        ),
        Section(
            "skills",
            "Skills",
            "Skills available to the agent; install packs from git, npm or a folder.",
            kind="skills",
            keys=(("enter", "enable/disable"), ("i", "install skill")),
        ),
        Section(
            "image",
            "Image",
            "Image generation defaults.",
            fields=(
                Field(
                    "Image generation",
                    ("tools", "imageGeneration", "enabled"),
                    "tristate",
                    "auto: on as soon as the provider has a credential.",
                ),
                Field(
                    "Image provider",
                    ("tools", "imageGeneration", "provider"),
                    "select",
                    options=media_providers,
                ),
                Field(
                    "Image model",
                    ("tools", "imageGeneration", "model"),
                    "text",
                    placeholder="google/gemini-3.1-flash-image",
                ),
                Field(
                    "Default aspect",
                    ("tools", "imageGeneration", "defaultAspectRatio"),
                    "select",
                    options=IMAGE_ASPECTS,
                ),
                Field(
                    "Default size",
                    ("tools", "imageGeneration", "defaultImageSize"),
                    "select",
                    options=IMAGE_SIZES,
                ),
                Field(
                    "Max images per turn",
                    ("tools", "imageGeneration", "maxImagesPerTurn"),
                    "number",
                    minimum=1,
                    maximum=8,
                ),
            ),
        ),
        Section(
            "video",
            "Video",
            "Video generation defaults.",
            fields=(
                Field(
                    "Video generation",
                    ("tools", "videoGeneration", "enabled"),
                    "tristate",
                    "auto: on as soon as the provider has a credential.",
                ),
                Field(
                    "Video provider",
                    ("tools", "videoGeneration", "provider"),
                    "select",
                    options=media_providers,
                ),
                Field(
                    "Video model",
                    ("tools", "videoGeneration", "model"),
                    "text",
                    placeholder="google/veo-3.1-fast",
                ),
                Field(
                    "Default aspect",
                    ("tools", "videoGeneration", "defaultAspectRatio"),
                    "select",
                    options=VIDEO_ASPECTS,
                ),
                Field(
                    "Default duration (s)",
                    ("tools", "videoGeneration", "defaultDurationSeconds"),
                    "number",
                    minimum=1,
                    maximum=60,
                ),
                Field(
                    "Default resolution",
                    ("tools", "videoGeneration", "defaultResolution"),
                    "select",
                    options=VIDEO_RESOLUTIONS,
                ),
            ),
        ),
        Section(
            "voice",
            "Voice",
            "Voice input (transcription), realtime voice, text-to-speech and music.",
            fields=(
                Field(
                    "Voice input",
                    ("transcription", "enabled"),
                    "toggle",
                    "Transcribe audio messages.",
                ),
                Field(
                    "Transcription provider",
                    ("transcription", "provider"),
                    "select",
                    options=providers,
                ),
                Field(
                    "Transcription model",
                    ("transcription", "model"),
                    "text",
                    placeholder="nvidia/parakeet-tdt-0.6b-v3",
                ),
                Field(
                    "Transcription language",
                    ("transcription", "language"),
                    "text",
                    "Empty = auto-detect. ISO code such as en or fr.",
                    placeholder="auto",
                    nullable=True,
                ),
                Field(
                    "Max duration (s)",
                    ("transcription", "maxDurationSec"),
                    "number",
                    minimum=1,
                    maximum=600,
                ),
                Field(
                    "Max upload (MB)",
                    ("transcription", "maxUploadMb"),
                    "number",
                    minimum=1,
                    maximum=100,
                ),
                Field(
                    "Realtime voice",
                    ("voice", "realtimeEnabled"),
                    "tristate",
                    "auto: on when the provider supports it.",
                ),
                Field("TTS provider", ("voice", "ttsProvider"), "select", options=providers),
                Field(
                    "TTS model",
                    ("voice", "ttsModel"),
                    "text",
                    placeholder="x-ai/grok-voice-tts-1.0",
                ),
                Field("Voice", ("voice", "voice"), "text", placeholder="eve"),
                Field("Auto-speak replies", ("voice", "autoSpeak"), "toggle"),
                Field("Music generation", ("tools", "musicGeneration", "enabled"), "tristate"),
                Field(
                    "Music provider",
                    ("tools", "musicGeneration", "provider"),
                    "select",
                    options=media_providers,
                ),
                Field(
                    "Music model",
                    ("tools", "musicGeneration", "model"),
                    "text",
                    placeholder="google/lyria-3-clip-preview",
                ),
            ),
        ),
        Section(
            "web",
            "Web",
            "Web search and page fetching used by the agent.",
            fields=(
                Field(
                    "Search provider",
                    ("tools", "web", "search", "provider"),
                    "select",
                    options=search_options,
                ),
                Field(
                    "Search API key",
                    ("tools", "web", "search", "apiKey"),
                    "secret",
                    "Needed for Brave, Exa and Tavily.",
                ),
                Field(
                    "Search base URL",
                    ("tools", "web", "search", "baseUrl"),
                    "text",
                    "Optional self-hosted endpoint.",
                    placeholder="default",
                ),
                Field(
                    "Max results",
                    ("tools", "web", "search", "maxResults"),
                    "number",
                    minimum=1,
                    maximum=10,
                ),
                Field(
                    "Timeout (s)",
                    ("tools", "web", "search", "timeout"),
                    "number",
                    minimum=1,
                    maximum=120,
                ),
                Field(
                    "Jina reader for pages",
                    ("tools", "web", "fetch", "useJinaReader"),
                    "toggle",
                    "Cleaner article text when fetching pages.",
                    restart=True,
                ),
            ),
        ),
        Section(
            "system",
            "System",
            "Language, time zone, identity and machine resources.",
            fields=(
                Field(
                    "Language",
                    kind="info",
                    scope="info",
                    text="English  [dim](terminal UI; the assistant answers in the language you write)[/dim]",
                ),
                Field(
                    "Time zone",
                    ("agents", "defaults", "timezone"),
                    "text",
                    "IANA name, e.g. Europe/Paris.",
                    placeholder="UTC",
                    restart=True,
                    default="UTC",
                ),
                Field(
                    "Assistant name",
                    ("agents", "defaults", "botName"),
                    "text",
                    placeholder="navin",
                    default="navin",
                ),
                Field("Machine", kind="info", scope="info", text=machine),
                Field(
                    "Size agents from the machine",
                    ("resources", "enabled"),
                    "toggle",
                    "Off keeps the fixed concurrency limit.",
                ),
                Field(
                    "Max RAM share for agents",
                    ("resources", "maxUtilisation"),
                    "number",
                    "0.10 to 0.95 of the measured memory.",
                    minimum=0.1,
                    maximum=0.95,
                ),
                Field(
                    "Agents per core",
                    ("resources", "agentsPerCore"),
                    "number",
                    minimum=1,
                    maximum=256,
                ),
                Field(
                    "RAM per agent (MB)",
                    ("resources", "memoryPerAgentMb"),
                    "number",
                    minimum=8,
                    maximum=4096,
                ),
            ),
        ),
        Section(
            "security",
            "Security",
            "Command confirmation, shell policy and local network access.",
            fields=(
                Field(
                    "Command confirmation",
                    ("tools", "approvals", "execAsk"),
                    "select",
                    "destructive: ask before risky commands only.",
                    options=(("destructive", "risky commands"), ("always", "every command")),
                ),
                Field(
                    "Ask for permissions",
                    ("tools", "approvals", "enabled"),
                    "toggle",
                    "Off = autonomous, never asks.",
                ),
                Field("Remember decisions", ("tools", "approvals", "remember"), "toggle"),
                Field(
                    "Security profile",
                    ("tools", "securityProfile"),
                    "select",
                    options=(
                        ("", "auto"),
                        ("autonomous", "autonomous"),
                        ("assisted", "assisted"),
                        ("strict", "strict"),
                    ),
                    nullable=True,
                ),
                Field("Shell execution", ("tools", "exec", "enable"), "toggle"),
                Field(
                    "Restrict to workspace",
                    ("tools", "restrictToWorkspace"),
                    "toggle",
                    "Block file access outside the workspace.",
                ),
                Field(
                    "Built-in protections",
                    ("tools", "exec", "builtinDenyRules"),
                    "toggle",
                    "Refuse rm -rf /, fork bombs, disk wipes...",
                ),
                Field(
                    "Blocked commands",
                    ("tools", "exec", "denyPatterns"),
                    "list",
                    "Regex patterns, comma separated.",
                ),
                Field(
                    "Allowed exceptions",
                    ("tools", "exec", "allowPatterns"),
                    "list",
                    "Regex patterns, comma separated.",
                ),
                Field(
                    "Local service access",
                    ("tools", "webuiAllowLocalServiceAccess"),
                    "toggle",
                    "Let the agent reach localhost services.",
                ),
            ),
        ),
        Section(
            "guardrails",
            "Guardrails",
            f"Board autonomy for this project: {project_root}",
            fields=(
                Field(
                    "Project",
                    kind="info",
                    scope="info",
                    text=escape(str(project_root)),
                    help="Change it from the palette (ctrl+p): Project folder.",
                ),
                Field(
                    "Autonomy",
                    ("enabled",),
                    "toggle",
                    "Consent for the board to act on this project.",
                    scope="autonomy",
                ),
                Field(
                    "Task auto-branch",
                    ("auto_branch",),
                    "toggle",
                    "One git branch per task.",
                    scope="autonomy",
                ),
                Field("Pull request when done", ("open_pr_on_done",), "toggle", scope="autonomy"),
                Field("Sync GitHub issues", ("sync_github_issues",), "toggle", scope="autonomy"),
                Field("Fix issues automatically", ("fix_issues",), "toggle", scope="autonomy"),
                Field("Autopilot loop", ("autopilot_loop",), "toggle", scope="autonomy"),
                Field(
                    "Issues repository",
                    ("issues_repo",),
                    "text",
                    "owner/name on GitHub.",
                    placeholder="owner/name",
                    scope="autonomy",
                    nullable=True,
                ),
                Field("Kill switches", kind="info", scope="info", text=kill_line),
            ),
            keys=(("enter", "toggle / edit"),),
        ),
        Section(
            "git",
            "Git",
            "Global git switches and forge tokens (GitHub, GitLab, Forgejo).",
            fields=(
                Field(
                    "Task auto-branch",
                    ("tools", "boardGit", "autoBranchEnabled"),
                    "toggle",
                    "Global switch: off disables it for every project.",
                ),
                Field(
                    "Pull request on task done",
                    ("tools", "boardGit", "openPrEnabled"),
                    "toggle",
                    "Global switch.",
                ),
                Field(
                    "Add forge token",
                    kind="action",
                    scope="action",
                    action="forge_add",
                    help="Host + token + kind (auto, github, gitlab, forgejo).",
                ),
            ),
            keys=(("enter", "toggle / edit"), ("d", "remove token")),
        ),
        Section(
            "browser",
            "Browser",
            "The agent's browser (Playwright).",
            fields=(
                Field(
                    "Show a real window",
                    ("tools", "browser", "headless"),
                    "toggle",
                    "Off = headless.",
                    invert=True,
                ),
                Field(
                    "Mirror it in the editor",
                    ("tools", "browser", "liveView"),
                    "toggle",
                    "Live view of what the agent does.",
                ),
            ),
        ),
        Section(
            "rules",
            "Rules",
            f"Project rules in .navin/rules/*.md for {project_root}",
            kind="rules",
            keys=(("enter", "edit"), ("n", "new rule")),
        ),
        *(
            (
                Section(
                    "account",
                    "Account",
                    "navin.live subscription, managed models and usage.",
                    kind="account",
                    keys=(("enter", "open"),),
                ),
            )
            if live_modules_available()
            else ()
        ),
        Section(
            "about",
            "About",
            "Navin CLI.",
            kind="about",
            fields=(
                Field("Version", kind="info", scope="info", text=f"navin v{escape(version)}"),
                Field("Config", kind="info", scope="info", text=escape(config_label)),
                Field("Workspace", kind="info", scope="info", text=escape(workspace)),
                Field("Docs", kind="info", scope="info", text="https://navin.live/docs"),
                Field(
                    "Advanced: every config key",
                    kind="action",
                    scope="action",
                    action="raw_config",
                    help="Schema-validated tree of config.json.",
                ),
            ),
            keys=(("enter", "open"),),
        ),
    )


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class RuleEditor(ModalScreen[str | None]):
    """Edit one rule file; dismisses with the new content (or None)."""

    DEFAULT_CSS = """
    RuleEditor { align: center middle; }
    RuleEditor > Vertical { width: 90%; height: 88%; border: round $primary; background: $surface; padding: 1 2; }
    RuleEditor .picker-title { text-style: bold; color: $primary; }
    RuleEditor TextArea { height: 1fr; margin: 1 0; }
    RuleEditor Horizontal { height: auto; }
    RuleEditor Button { margin: 0 1 0 0; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, title: str, content: str) -> None:
        super().__init__()
        self._title = title
        self._content = content

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="picker-title", markup=True)
            yield TextArea(
                self._content, language="markdown", soft_wrap=True, show_line_numbers=True
            )
            with Horizontal():
                yield Button("Save  ctrl+s", variant="primary", id="save")
                yield Button("Cancel  esc", id="cancel")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        self.dismiss(self.query_one(TextArea).text)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsHub(ModalScreen[bool]):
    """Two panes: sections on the left, the selected section on the right."""

    DEFAULT_CSS = """
    SettingsHub { align: center middle; }
    SettingsHub > Vertical {
        width: 96%;
        height: 92%;
        border: round $primary;
        background: $surface;
        padding: 0 1 1 1;
    }
    SettingsHub #head { height: 1; margin: 0 1; }
    SettingsHub #head .title { width: auto; text-style: bold; color: $primary; }
    SettingsHub #head .path { width: 1fr; text-align: right; color: $text-muted; }
    SettingsHub #body { height: 1fr; }
    SettingsHub #nav { width: 18; height: 1fr; background: transparent; border: none; border-right: tall $panel; padding: 0; scrollbar-size-vertical: 1; }
    SettingsHub #nav:focus { border: none; border-right: tall $primary 60%; }
    SettingsHub #rows { border: none; }
    SettingsHub #rows:focus { border: none; }
    SettingsHub #nav > .option-list--option { padding: 0 1; }
    SettingsHub #nav > .option-list--option-highlighted { background: $primary 25%; }
    SettingsHub #pane { width: 1fr; height: 1fr; padding: 0 0 0 2; }
    SettingsHub #pane-title { text-style: bold; color: $foreground; height: 1; }
    SettingsHub #pane-desc { color: $text-muted; height: auto; margin: 0 0 1 0; }
    SettingsHub #rows { height: 1fr; background: transparent; scrollbar-size-vertical: 1; }
    SettingsHub #rows > .option-list--option-highlighted { background: $secondary 30%; text-style: bold; }
    SettingsHub #table { height: 1fr; display: none; }
    SettingsHub #table.-visible { display: block; }
    SettingsHub #rows.-hidden { display: none; }
    SettingsHub #help { height: auto; color: $text-muted; margin: 1 0 0 0; }
    SettingsHub #value { display: none; margin: 1 0 0 0; }
    SettingsHub #value.-visible { display: block; }
    SettingsHub #foot { height: 1; margin: 1 1 0 1; }
    SettingsHub #foot .status { width: 1fr; color: $text-muted; }
    SettingsHub #foot .keys { width: auto; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+s", "save", "Save"),
        Binding("tab", "focus_next", "Next", show=False),
    ]

    def __init__(
        self,
        load: Callable[[], dict[str, Any]],
        save: Callable[[dict[str, Any]], str | None],
        *,
        config_label: str,
        project_root: Path,
        workspace: str,
        version: str,
        runtime: Any = None,
        open_raw: Callable[[], Any] | None = None,
        open_account: Callable[[], Any] | None = None,
        apply_preset: Callable[[str], Any] | None = None,
        run_command: Callable[[str], Any] | None = None,
        start: str = "providers",
    ) -> None:
        super().__init__()
        self._load = load
        self._save = save
        self._config_label = config_label
        self._project_root = project_root
        self._workspace = workspace
        self._version = version
        self._runtime = runtime
        self._open_raw = open_raw
        self._open_account = open_account
        self._apply_preset = apply_preset
        self._run_command = run_command
        self._start = start
        self._data: dict[str, Any] = {}
        self._autonomy: dict[str, Any] = {}
        self._sections: tuple[Section, ...] = ()
        self._section: Section | None = None
        self._rows: list[Field] = []
        self._table_rows: list[HubRow] = []
        self._dirty = False
        self._discard_armed = False
        self._editing: Field | None = None

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="head"):
                yield Static("Settings", classes="title", markup=True)
                yield Static(escape(self._config_label), classes="path", markup=True)
            with Horizontal(id="body"):
                yield OptionList(id="nav")
                with Vertical(id="pane"):
                    yield Static("", id="pane-title", markup=True)
                    yield Static("", id="pane-desc", markup=True)
                    yield OptionList(id="rows")
                    yield DataTable(id="table", zebra_stripes=True, cursor_type="row")
                    yield Static("", id="help", markup=True)
                    yield Input(id="value")
            with Horizontal(id="foot"):
                yield Static("", classes="status", markup=True)
                yield Static("", classes="keys", markup=True)

    def on_mount(self) -> None:
        self._reload()
        nav = self.query_one("#nav", OptionList)
        idx = next((i for i, s in enumerate(self._sections) if s.id == self._start), 0)
        nav.highlighted = idx
        self._show_section(self._sections[idx])
        nav.focus()

    def _reload(self) -> None:
        try:
            self._data = self._load()
        except Exception as exc:  # noqa: BLE001
            self._data = {}
            self._status(f"[$error]failed to load config: {escape(str(exc))}[/]")
        try:
            from navin.board.autonomy import read_autonomy

            self._autonomy = read_autonomy(self._project_root)
        except Exception:  # noqa: BLE001
            self._autonomy = {}
        self._dirty = False
        self._sections = build_sections(
            self._data,
            project_root=self._project_root,
            version=self._version,
            config_label=self._config_label,
            workspace=self._workspace,
        )
        nav = self.query_one("#nav", OptionList)
        keep = nav.highlighted
        nav.clear_options()
        for section in self._sections:
            nav.add_option(Option(section.title, id=section.id))
        if keep is not None and 0 <= keep < len(self._sections):
            nav.highlighted = keep

    # -- navigation -------------------------------------------------------

    @on(OptionList.OptionHighlighted, "#nav")
    def _nav_highlight(self, event: OptionList.OptionHighlighted) -> None:
        section = next((s for s in self._sections if s.id == event.option.id), None)
        if section is not None and section is not self._section:
            self._show_section(section)

    @on(OptionList.OptionSelected, "#nav")
    def _nav_select(self, event: OptionList.OptionSelected) -> None:
        section = next((s for s in self._sections if s.id == event.option.id), None)
        if section is None:
            return
        if section.kind == "account":
            if self._open_account:
                self._open_account()
            return
        target = (
            self.query_one("#table", DataTable)
            if section.kind in {"providers", "models", "mcp", "skills", "rules"}
            else self.query_one("#rows", OptionList)
        )
        target.focus()

    def _show_section(self, section: Section) -> None:
        self._section = section
        self._editing = None
        self.query_one("#value", Input).remove_class("-visible")
        self.query_one("#pane-title", Static).update(escape(section.title))
        self.query_one("#pane-desc", Static).update(
            section.description
            if section.id in {"guardrails", "rules"}
            else escape(section.description)
        )
        keys = list(section.keys) + [("ctrl+s", "save"), ("esc", "close")]
        self.query_one("#foot .keys", Static).update(
            "  ".join(f"[b $primary]{k}[/] {label}" for k, label in keys)
        )
        table = self.query_one("#table", DataTable)
        rows = self.query_one("#rows", OptionList)
        if section.kind in {"providers", "models", "mcp", "skills", "rules"}:
            rows.add_class("-hidden")
            table.add_class("-visible")
            self._fill_table(section)
        else:
            table.remove_class("-visible")
            rows.remove_class("-hidden")
            self._fill_rows(section)
        self.query_one("#help", Static).update("")

    # -- form rows --------------------------------------------------------

    def _current_value(self, field: Field) -> Any:
        if field.scope == "autonomy":
            return self._autonomy.get(field.path[0])
        return _get(self._data, field.path)

    def _format(self, field: Field) -> str:
        if field.kind == "info":
            return field.text
        if field.kind == "action":
            return f"[b $primary]›[/] {escape(field.label)}"
        value = self._current_value(field)
        if field.kind == "toggle":
            shown = bool(value) if value is not None else False
            if field.invert:
                shown = not shown
            return "[$success]● on[/]" if shown else "[dim]○ off[/dim]"
        if field.kind == "tristate":
            if value is None:
                return "[dim]◐ auto[/dim]"
            return "[$success]● on[/]" if value else "[dim]○ off[/dim]"
        if field.kind == "secret":
            return _mask(value)
        if field.kind == "select":
            label = next(
                (lab for v, lab in field.options if v == ("" if value is None else str(value))),
                None,
            )
            if label is not None:
                return escape(label)
            return escape(str(value)) if value not in (None, "") else "[dim]not set[/dim]"
        if field.kind == "list":
            items = value if isinstance(value, list) else []
            return escape(", ".join(map(str, items)))[:60] if items else "[dim]none[/dim]"
        if value in (None, ""):
            return "[dim]not set[/dim]"
        return escape(str(value))

    def _fill_rows(self, section: Section) -> None:
        rows = self.query_one("#rows", OptionList)
        keep = rows.highlighted
        rows.clear_options()
        self._rows = list(section.fields)
        if section.id == "git":
            tokens = _get(self._data, ("tools", "forge", "tokens"), {}) or {}
            hosts = _get(self._data, ("tools", "forge", "hosts"), {}) or {}
            for host in sorted(tokens):
                kind = hosts.get(host) or "auto"
                self._rows.append(
                    Field(
                        host,
                        kind="forge",
                        scope="action",
                        action="forge_edit",
                        key=host,
                        text=f"{_mask(tokens[host])}  [dim]{escape(str(kind))}[/dim]",
                        help="Enter to replace the token, d to remove it.",
                    )
                )
        width = 32
        for field in self._rows:
            if field.kind == "info":
                line = f"[dim]{escape(field.label):<{width}}[/dim]{field.text}"
            elif field.kind == "forge":
                line = f"  {escape(field.label):<{width - 2}}{field.text}"
            elif field.kind == "action":
                line = self._format(field)
            else:
                line = f"{escape(field.label):<{width}}{self._format(field)}"
            rows.add_option(Option(line, id=None))
        if self._rows:
            rows.highlighted = keep if keep is not None and 0 <= keep < len(self._rows) else 0

    def _refresh_row(self, index: int) -> None:
        if self._section is not None:
            self._fill_rows(self._section)
            self.query_one("#rows", OptionList).highlighted = index

    @on(OptionList.OptionHighlighted, "#rows")
    def _row_highlight(self, event: OptionList.OptionHighlighted) -> None:
        idx = event.option_index
        if 0 <= idx < len(self._rows):
            field = self._rows[idx]
            bits: list[str] = []
            if field.help:
                bits.append(field.help)
            if field.path and field.scope == "config":
                bits.append(f"[dim]{escape(field.dotted)}[/dim]")
            if field.minimum is not None or field.maximum is not None:
                bits.append(f"[dim]{field.minimum} to {field.maximum}[/dim]")
            if field.restart:
                bits.append("[dim]restart navin-cli to apply[/dim]")
            self.query_one("#help", Static).update("  ·  ".join(bits))

    @on(OptionList.OptionSelected, "#rows")
    def _row_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if 0 <= idx < len(self._rows):
            self._activate(self._rows[idx], idx)

    def _activate(self, field: Field, index: int) -> None:
        if field.kind == "info":
            return
        if field.kind in {"action", "forge"}:
            self.run_worker(self._run_action(field), exclusive=False)
            return
        if field.kind == "toggle":
            current = self._current_value(field)
            self._write(field, not bool(current))
            self._refresh_row(index)
            return
        if field.kind == "tristate":
            current = self._current_value(field)
            nxt = True if current is None else (False if current is True else None)
            self._write(field, nxt)
            self._refresh_row(index)
            return
        if field.kind == "select":
            self.run_worker(self._pick(field, index), exclusive=False)
            return
        # text / secret / number / list: inline input
        self._editing = field
        inp = self.query_one("#value", Input)
        value = self._current_value(field)
        if field.kind == "list":
            inp.value = ", ".join(map(str, value)) if isinstance(value, list) else ""
        else:
            inp.value = "" if value is None else str(value)
        inp.password = field.kind == "secret"
        inp.placeholder = field.placeholder or ("value" if field.kind != "number" else "number")
        inp.add_class("-visible")
        inp.focus()

    async def _pick(self, field: Field, index: int) -> None:
        current = self._current_value(field)
        cur = "" if current is None else str(current)
        items = [PickItem(v, label) for v, label in field.options]
        chosen = await self.app.push_screen_wait(PickerScreen(field.label, items, current=cur))
        if chosen is None:
            return
        self._write(field, None if chosen == "" and field.nullable else chosen)
        self._refresh_row(index)
        self.query_one("#rows", OptionList).focus()

    @on(Input.Submitted, "#value")
    def _value_submitted(self, event: Input.Submitted) -> None:
        field = self._editing
        if field is None:
            return
        raw = event.value.strip()
        value: Any
        if field.kind == "number":
            try:
                value = float(raw) if "." in raw else int(raw)
            except ValueError:
                self._status(f"[$error]{escape(field.label)}: enter a number[/]")
                return
            if (
                field.minimum is not None
                and value < field.minimum
                or field.maximum is not None
                and value > field.maximum
            ):
                self._status(
                    f"[$error]{escape(field.label)}: {field.minimum} to {field.maximum}[/]"
                )
                return
        elif field.kind == "list":
            value = [p.strip() for p in raw.split(",") if p.strip()]
        elif raw:
            value = raw
        elif field.default is not None:
            value = field.default
        else:
            value = None if field.nullable else ""
        self._write(field, value)
        self._editing = None
        inp = self.query_one("#value", Input)
        inp.remove_class("-visible")
        rows = self.query_one("#rows", OptionList)
        idx = rows.highlighted or 0
        self._refresh_row(idx)
        rows.focus()

    def _write(self, field: Field, value: Any) -> None:
        if field.scope == "autonomy":
            self._autonomy[field.path[0]] = value
        else:
            if field.invert and field.kind == "toggle":
                value = not value
            _set(self._data, field.path, value)
        self._dirty = True
        self._discard_armed = False
        self._status(f"{escape(field.label)} changed  [dim]ctrl+s to save[/dim]")

    # -- actions (git tokens, raw config) ---------------------------------

    async def _run_action(self, field: Field) -> None:
        if field.action == "raw_config" and self._open_raw:
            self._open_raw()
            return
        if field.action in {"forge_add", "forge_edit"}:
            fields = [
                FormField("host", "Forge host", placeholder="github.com", value=field.key),
                FormField("token", "Access token", secret=True, placeholder="ghp_..., glpat-..."),
                FormField(
                    "kind",
                    "Kind",
                    kind="select",
                    options=(
                        ("auto", "auto"),
                        ("github", "GitHub"),
                        ("gitlab", "GitLab"),
                        ("forgejo", "Forgejo"),
                    ),
                    value=str(
                        _get(self._data, ("tools", "forge", "hosts", field.key), "auto") or "auto"
                    ),
                ),
            ]
            answer = await self.app.push_screen_wait(
                FormScreen(
                    "Forge token",
                    fields,
                    hint="Used for pull requests and issue sync.",
                    submit_label="Save token",
                )
            )
            if not answer:
                return
            host = (
                answer["host"]
                .strip()
                .lower()
                .removeprefix("https://")
                .removeprefix("http://")
                .strip("/")
            )
            if not host:
                self._status("[$error]host is required[/]")
                return
            _set(self._data, ("tools", "forge", "tokens", host), answer["token"])
            _set(self._data, ("tools", "forge", "hosts", host), answer["kind"] or "auto")
            self._dirty = True
            self._status(f"token for {escape(host)} set  [dim]ctrl+s to save[/dim]")
            if self._section:
                self._fill_rows(self._section)

    def _remove_forge(self) -> None:
        rows = self.query_one("#rows", OptionList)
        idx = rows.highlighted
        if idx is None or not (0 <= idx < len(self._rows)):
            return
        field = self._rows[idx]
        if field.kind != "forge":
            self._status("select a forge token row first")
            return
        tokens = _get(self._data, ("tools", "forge", "tokens"), {}) or {}
        hosts = _get(self._data, ("tools", "forge", "hosts"), {}) or {}
        tokens.pop(field.key, None)
        hosts.pop(field.key, None)
        self._dirty = True
        self._status(f"token for {escape(field.key)} removed  [dim]ctrl+s to save[/dim]")
        if self._section:
            self._fill_rows(self._section)

    # -- tables (providers, models, mcp, skills, rules) --------------------

    def _fill_table(self, section: Section, *, keep_key: str | None = None) -> None:
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        rows: list[HubRow] = []
        if section.kind == "providers":
            table.add_columns("", "Provider", "Kind", "API key", "Endpoint")
            rows = provider_rows(self._data)
        elif section.kind == "models":
            table.add_columns(
                "", "Configuration", "Model", "Provider", "Kind", "Context", "Thinking"
            )
            rows = self._model_rows()
        elif section.kind == "mcp":
            table.add_columns("Server", "Type", "Command / URL", "Tools", "Timeout")
            configured = set(_get(self._data, ("tools", "mcpServers"), {}) or {})
            rows = mcp_rows(self._data) + mcp_preset_rows(configured)
        elif section.kind == "skills":
            table.add_columns("", "Skill", "Source", "Description")
            disabled = {
                str(s)
                for s in (_get(self._data, ("agents", "defaults", "disabledSkills"), []) or [])
            }
            agent_loop = getattr(self._runtime, "agent_loop", None)
            rows = skill_rows(agent_loop, self._workspace, disabled)
        elif section.kind == "rules":
            table.add_columns("Rule", "Path", "Size")
            rows = self._rule_rows()
        self._table_rows = rows
        for row in rows:
            table.add_row(*row.cells, key=row.key)
        if rows:
            target = (
                next((i for i, r in enumerate(rows) if r.key == keep_key), 0) if keep_key else 0
            )
            table.move_cursor(row=target)
            self.query_one("#help", Static).update(rows[target].detail)
        else:
            empty = {
                "mcp": "No MCP server yet. Press [b]i[/b] on a preset or [b]c[/b] for a custom server.",
                "skills": "No skill found. Press [b]i[/b] to install one.",
                "rules": "No rule yet. Press [b]n[/b] to create .navin/rules/<name>.md.",
            }.get(section.kind, "")
            self.query_one("#help", Static).update(empty)

    def _model_rows(self) -> list[HubRow]:
        defaults = _get(self._data, ("agents", "defaults"), {}) or {}
        active = defaults.get("modelPreset") or "default"
        presets = _get(self._data, ("modelPresets",), {}) or {}
        rows = [
            HubRow(
                key="default",
                cells=[
                    "●" if active == "default" else "○",
                    "default",
                    str(defaults.get("model") or "-"),
                    str(defaults.get("provider") or "auto"),
                    "text",
                    f"{int(defaults.get('contextWindowTokens') or 0):,}",
                    str(defaults.get("reasoningEffort") or "auto"),
                ],
                detail="[b]default[/b]  agents.defaults: the model used when no configuration is selected.",
            )
        ]
        for name in sorted(presets):
            p = presets[name]
            if not isinstance(p, dict):
                continue
            modality = str(p.get("modality") or "text")
            enabled = p.get("enabled", True)
            mark = "●" if name == active else ("○" if enabled else "·")
            rows.append(
                HubRow(
                    key=name,
                    cells=[
                        mark,
                        str(p.get("label") or name),
                        str(p.get("model") or "-"),
                        str(p.get("provider") or "auto"),
                        modality,
                        f"{int(p.get('contextWindowTokens') or 0):,}",
                        str(p.get("reasoningEffort") or "auto"),
                    ],
                    detail=f"[b]{escape(str(p.get('label') or name))}[/b]  [dim]{escape(name)} · {escape(modality)}{'' if enabled else ' · hidden'}[/dim]",
                )
            )
        # default, then the active one, then text models, then image / video / audio / speech.
        rows.sort(
            key=lambda r: (
                r.key != "default",
                r.cells[0] != "●",
                r.cells[4] != "text",
                r.cells[4],
                r.cells[1].lower(),
            )
        )
        return rows

    def _rule_rows(self) -> list[HubRow]:
        try:
            from navin.agent.project_rules import list_navin_rules

            rules = list_navin_rules(self._project_root)
        except Exception as exc:  # noqa: BLE001
            self._status(f"[$error]{escape(str(exc))}[/]")
            return []
        rows: list[HubRow] = []
        for rule in rules:
            content = str(rule.get("content") or "")
            first = next((line.strip() for line in content.splitlines() if line.strip()), "")
            rows.append(
                HubRow(
                    key=str(rule["name"]),
                    cells=[
                        str(rule["name"]),
                        str(rule["path"]),
                        f"{int(rule.get('bytes') or 0):,} B",
                    ],
                    detail=f"[b]{escape(str(rule['name']))}[/b]  {escape(first[:120])}",
                )
            )
        return rows

    def _table_row(self) -> HubRow | None:
        table = self.query_one("#table", DataTable)
        if not self._table_rows or table.cursor_row is None:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001
            return None
        return next((r for r in self._table_rows if r.key == key), None)

    @on(DataTable.RowHighlighted, "#table")
    def _table_highlight(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        row = next((r for r in self._table_rows if r.key == key), None)
        if row:
            self.query_one("#help", Static).update(row.detail)

    @on(DataTable.RowSelected, "#table")
    def _table_selected(self) -> None:
        self._table_action("enter")

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._section is None or self.query_one("#value", Input).has_focus:
            return
        if (
            self._section.kind in {"providers", "models", "mcp", "skills", "rules"}
            and self.query_one("#table", DataTable).has_focus
        ):
            if event.key in {"t", "d", "u", "a", "r", "i", "c", "n", "e"}:
                event.stop()
                self._table_action(event.key)
        elif (
            self._section.id == "git"
            and event.key == "d"
            and self.query_one("#rows", OptionList).has_focus
        ):
            event.stop()
            self._remove_forge()

    def _table_action(self, key: str) -> None:
        section = self._section
        if section is None:
            return
        row = self._table_row()
        handler = {
            "providers": self._provider_action,
            "models": self._model_action,
            "mcp": self._mcp_action,
            "skills": self._skill_action,
            "rules": self._rule_action,
        }.get(section.kind)
        if handler is not None:
            self.run_worker(handler(key, row), exclusive=False)

    # providers -------------------------------------------------------------

    async def _provider_action(self, key: str, row: HubRow | None) -> None:
        if row is None:
            return
        name = row.key
        if key in {"enter", "e"}:
            await self._provider_form(name)
        elif key == "t":
            self._status(f"testing {escape(name)}…")
            self._provider_test(name)
        elif key == "d":
            self._provider_update(
                name,
                {"provider": [name], "api_key": [""], "api_base": [""]},
                f"{name} disconnected",
            )

    async def _provider_form(self, name: str) -> None:
        try:
            from navin.config.schema import provider_supports_auth_mode
            from navin.providers.connection_presets import CONNECTION_PRESETS
            from navin.providers.registry import PROVIDERS
        except Exception as exc:  # noqa: BLE001
            self._status(f"[$error]{escape(str(exc))}[/]")
            return
        spec = next((s for s in PROVIDERS if s.name == name or _alias(s.name) == name), None)
        section = (
            (self._data.get("providers") or {}).get(name)
            or (self._data.get("providers") or {}).get(_alias(name))
            or {}
        )
        if spec is not None and spec.is_oauth:
            self._status(
                f"OAuth provider: run [b]navin provider login {escape(spec.name)}[/b] in a terminal, then restart navin-cli."
            )
            return
        if name == "navin" and _get(self._data, ("license", "managedApiKey")):
            self._status("The Navin plan provider is managed by your subscription (Account).")
            return
        display = (spec.display_name if spec else name) or name
        fields = [
            FormField(
                "api_key",
                "API key",
                secret=True,
                required=False,
                placeholder="paste a key (leave empty to keep the current one)",
                help=f"current: {_mask(section.get('apiKey'))}",
            ),
            FormField(
                "api_base",
                "API base URL",
                required=False,
                value=str(section.get("apiBase") or ""),
                placeholder=(spec.default_api_base if spec else "") or "default",
            ),
        ]
        spec_name = spec.name if spec else name
        if provider_supports_auth_mode(spec_name):
            fields.append(
                FormField(
                    "auth_mode",
                    "Authentication",
                    kind="select",
                    options=(("bearer", "Bearer token"), ("none", "None (local server)")),
                    value=str(section.get("authMode") or "bearer"),
                )
            )
        if spec_name == "openai":
            fields.append(
                FormField(
                    "api_type",
                    "API type",
                    kind="select",
                    options=(
                        ("auto", "auto"),
                        ("chat_completions", "Chat completions"),
                        ("responses", "Responses"),
                    ),
                    value=str(section.get("apiType") or "auto"),
                )
            )
        preset = CONNECTION_PRESETS.get(spec_name)
        if preset is not None:
            fields.append(
                FormField(
                    "endpoint_region",
                    "Region",
                    kind="select",
                    options=tuple((c.id, c.label) for c in preset.regions),
                    value=str(section.get("endpointRegion") or preset.default_region),
                )
            )
            fields.append(
                FormField(
                    "access_plan",
                    "Plan",
                    kind="select",
                    options=tuple((c.id, c.label) for c in preset.plans),
                    value=str(section.get("accessPlan") or preset.default_plan),
                )
            )
            fields.append(
                FormField(
                    "wire_protocol",
                    "Protocol",
                    kind="select",
                    options=tuple((c.id, c.label) for c in preset.protocols),
                    value=str(section.get("wireProtocol") or preset.default_protocol),
                )
            )
        answer = await self.app.push_screen_wait(
            FormScreen(
                f"{display}",
                fields,
                hint="Saved immediately to config.json (secrets encrypted).",
                submit_label="Save provider",
            )
        )
        if answer is None:
            return
        query: dict[str, list[str]] = {"provider": [spec_name]}
        if answer.get("api_key"):
            query["api_key"] = [answer["api_key"]]
        query["api_base"] = [answer.get("api_base", "")]
        for k in ("auth_mode", "api_type", "endpoint_region", "access_plan", "wire_protocol"):
            if k in answer:
                query[k] = [answer[k]]
        self._provider_update(spec_name, query, f"{display} saved")

    @work(thread=True, exclusive=True, group="provider")
    def _provider_update(self, name: str, query: dict[str, list[str]], ok_message: str) -> None:
        try:
            from navin.webui.settings_api import update_provider_settings

            update_provider_settings(query)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self._status, f"[$error]{escape(str(getattr(exc, 'message', exc)))}[/]"
            )
            return
        self.app.call_from_thread(
            self._after_external_write,
            f"[$success]{escape(ok_message)}[/]  [dim]restart navin-cli to use it[/dim]",
            name,
        )

    @work(thread=True, exclusive=True, group="provider-test")
    def _provider_test(self, name: str) -> None:
        try:
            from navin.webui.settings_api import test_provider_connection

            result = test_provider_connection({"provider": [name]})
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self._status, f"[$error]{escape(str(getattr(exc, 'message', exc)))}[/]"
            )
            return
        ok = bool(result.get("ok"))
        msg = str(result.get("message") or result.get("status") or "")
        count = int(result.get("model_count") or 0)
        latency = int(result.get("latency_ms") or 0)
        text = (
            f"[$success]✓ {escape(name)}: {count} models · {latency} ms[/]"
            if ok
            else f"[$warning]{escape(name)}: {escape(msg)}[/]"
        )
        self.app.call_from_thread(self._status, text)

    def _after_external_write(self, message: str, keep_key: str | None = None) -> None:
        """A settings_api helper saved config.json: reload and keep the cursor."""
        self._reload()
        if self._section is not None:
            if self._section.kind in {"providers", "models", "mcp", "skills", "rules"}:
                self._fill_table(self._section, keep_key=keep_key)
            else:
                self._fill_rows(self._section)
        self._status(message)

    # models ----------------------------------------------------------------

    async def _model_action(self, key: str, row: HubRow | None) -> None:
        if key == "a":
            await self._model_form(None)
            return
        if key == "r":
            await self._routing()
            return
        if row is None:
            return
        if key in {"enter", "e"}:
            await self._model_form(row.key)
        elif key == "u":
            if self._apply_preset is not None:
                result = self._apply_preset(row.key)
                if hasattr(result, "__await__"):
                    await result
            self._model_update(
                {"model_preset": [row.key]},
                "update_agent_settings",
                f"{row.key} is now the default",
            )
        elif key == "d":
            if row.key == "default":
                self._status("The default configuration cannot be deleted.")
                return
            self._model_update(
                {"name": [row.key]}, "delete_model_configuration", f"{row.key} deleted"
            )

    async def _model_form(self, name: str | None) -> None:
        providers = _provider_choices(self._data)
        if name == "default":
            defaults = _get(self._data, ("agents", "defaults"), {}) or {}
            fields = [
                FormField(
                    "model",
                    "Model",
                    value=str(defaults.get("model") or ""),
                    placeholder="provider/model-id",
                ),
                FormField(
                    "provider",
                    "Provider",
                    kind="select",
                    options=providers,
                    value=str(defaults.get("provider") or ""),
                ),
                FormField(
                    "context_window_tokens",
                    "Context window",
                    kind="select",
                    options=CONTEXT_WINDOWS,
                    value=str(defaults.get("contextWindowTokens") or "200000"),
                ),
                FormField(
                    "reasoning_effort",
                    "Thinking",
                    kind="select",
                    options=REASONING,
                    value=str(defaults.get("reasoningEffort") or ""),
                ),
            ]
            answer = await self.app.push_screen_wait(
                FormScreen("Default model (agents.defaults)", fields, submit_label="Save")
            )
            if answer is None:
                return
            query = {k: [v] for k, v in answer.items()}
            self._model_update(query, "update_agent_settings", "default model saved")
            return
        preset = (_get(self._data, ("modelPresets", name), {}) if name else {}) or {}
        fields = [
            FormField(
                "label",
                "Configuration name",
                value=str(preset.get("label") or name or ""),
                placeholder="My fast model",
            ),
            FormField(
                "model",
                "Model",
                value=str(preset.get("model") or ""),
                placeholder="provider/model-id",
            ),
            FormField(
                "provider",
                "Provider",
                kind="select",
                options=providers,
                value=str(preset.get("provider") or ""),
            ),
            FormField(
                "context_window_tokens",
                "Context window",
                kind="select",
                options=CONTEXT_WINDOWS,
                value=str(preset.get("contextWindowTokens") or "200000"),
            ),
            FormField(
                "reasoning_effort",
                "Thinking",
                kind="select",
                options=REASONING,
                value=str(preset.get("reasoningEffort") or ""),
            ),
        ]
        if name:
            fields.append(
                FormField(
                    "enabled",
                    "Visible in pickers",
                    kind="toggle",
                    value="true" if preset.get("enabled", True) else "false",
                )
            )
        answer = await self.app.push_screen_wait(
            FormScreen(
                "Edit model configuration" if name else "Add model configuration",
                fields,
                submit_label="Save",
            )
        )
        if answer is None:
            return
        query = {k: [v] for k, v in answer.items() if k != "provider" or v}
        if not answer.get("provider"):
            query["provider"] = ["auto"]
        if name:
            query["name"] = [name]
            self._model_update(
                query, "update_model_configuration", f"{answer.get('label') or name} saved"
            )
        else:
            self._model_update(query, "create_model_configuration", f"{answer.get('label')} added")

    async def _routing(self) -> None:
        presets = _get(self._data, ("modelPresets",), {}) or {}
        routes = _get(self._data, ("modelRoutes",), {}) or {}
        options: tuple[tuple[str, str], ...] = (("", "default model"),) + tuple(
            (n, str(presets[n].get("label") or n))
            for n in sorted(presets)
            if isinstance(presets[n], dict)
            and presets[n].get("enabled", True)
            and str(presets[n].get("modality") or "text") == "text"
        )
        fields = [
            FormField(
                role, label, kind="select", options=options, value=str(routes.get(role) or "")
            )
            for role, label in ROUTE_ROLES
        ]
        answer = await self.app.push_screen_wait(
            FormScreen(
                "Task routing",
                fields,
                hint="Which configuration handles each kind of task.",
                submit_label="Save routing",
            )
        )
        if answer is None:
            return
        for role, _ in ROUTE_ROLES:
            if role in answer:
                _set(self._data, ("modelRoutes", role), answer[role]) if answer[role] else (
                    _get(self._data, ("modelRoutes",), {}) or {}
                ).pop(role, None)
        self._dirty = True
        self.action_save()

    @work(thread=True, exclusive=True, group="models")
    def _model_update(self, query: dict[str, list[str]], func: str, ok_message: str) -> None:
        try:
            from navin.webui import settings_api

            getattr(settings_api, func)(query)
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self._status, f"[$error]{escape(str(getattr(exc, 'message', exc)))}[/]"
            )
            return
        keep = query.get("name", query.get("model_preset", [None]))[0]
        self.app.call_from_thread(
            self._after_external_write, f"[$success]{escape(ok_message)}[/]", keep
        )

    # mcp -------------------------------------------------------------------

    async def _mcp_action(self, key: str, row: HubRow | None) -> None:
        if key == "c":
            await self._mcp_custom()
            return
        if row is None:
            return
        if row.key.startswith("preset:"):
            if key in {"enter", "i"}:
                name = row.key.removeprefix("preset:")
                values: dict[str, str] = {}
                fields = [
                    FormField(n, label, placeholder, secret, required)
                    for n, label, placeholder, secret, required in mcp_preset_fields(name)
                ]
                if fields:
                    answer = await self.app.push_screen_wait(
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
                self._after_external_write(mcp_enable_preset(name, values), name)
            else:
                self._status("Press [b]i[/b] or Enter to enable this preset.")
            return
        if key in {"enter", "e"}:
            await self._mcp_custom(row.key)
        elif key == "r":
            self._after_external_write(mcp_remove_server(row.key))
        elif key == "i":
            self._status("Already configured. Enter edits it, [b]r[/b] removes it.")

    async def _mcp_custom(self, name: str | None = None) -> None:
        cfg = (_get(self._data, ("tools", "mcpServers", name), {}) if name else {}) or {}
        transport = str(
            cfg.get("type") or ("stdio" if cfg.get("command") or not name else "streamableHttp")
        )
        fields = [
            FormField("name", "Server name", value=name or "", placeholder="my-server"),
            FormField(
                "type",
                "Transport",
                kind="select",
                options=(
                    ("stdio", "stdio (command)"),
                    ("streamableHttp", "Streamable HTTP"),
                    ("sse", "SSE"),
                ),
                value=transport,
            ),
            FormField(
                "command",
                "Command",
                required=False,
                value=str(cfg.get("command") or ""),
                placeholder="npx",
            ),
            FormField(
                "args",
                "Arguments",
                required=False,
                value=" ".join(map(str, cfg.get("args") or [])),
                placeholder="-y @scope/server --flag",
            ),
            FormField(
                "url",
                "URL",
                required=False,
                value=str(cfg.get("url") or ""),
                placeholder="https://host/mcp",
            ),
            FormField(
                "env",
                "Environment (KEY=value, comma separated)",
                required=False,
                value=", ".join(f"{k}={v}" for k, v in (cfg.get("env") or {}).items()),
                secret=False,
            ),
            FormField(
                "headers",
                "Headers (Name=value, comma separated)",
                required=False,
                value=", ".join(f"{k}={v}" for k, v in (cfg.get("headers") or {}).items()),
            ),
            FormField(
                "tool_timeout",
                "Tool timeout (s)",
                required=False,
                value=str(cfg.get("toolTimeout") or 30),
            ),
            FormField(
                "enabled_tools",
                "Enabled tools",
                required=False,
                value=", ".join(map(str, cfg.get("enabledTools") or ["*"])),
                help="* = every tool",
            ),
        ]
        answer = await self.app.push_screen_wait(
            FormScreen(
                "Edit MCP server" if name else "Custom MCP server",
                fields,
                submit_label="Save server",
            )
        )
        if answer is None:
            return
        new_name = answer["name"].strip()
        if not new_name:
            self._status("[$error]server name is required[/]")
            return

        def pairs(raw: str) -> dict[str, str]:
            out: dict[str, str] = {}
            for part in raw.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k.strip():
                        out[k.strip()] = v.strip()
            return out

        server: dict[str, Any] = {"type": answer["type"]}
        if answer["type"] == "stdio":
            if not answer.get("command"):
                self._status("[$error]command is required for stdio servers[/]")
                return
            server["command"] = answer["command"]
            server["args"] = answer.get("args", "").split()
            if pairs(answer.get("env", "")):
                server["env"] = pairs(answer["env"])
        else:
            if not answer.get("url"):
                self._status("[$error]URL is required for HTTP servers[/]")
                return
            server["url"] = answer["url"]
            if pairs(answer.get("headers", "")):
                server["headers"] = pairs(answer["headers"])
        try:
            server["toolTimeout"] = int(answer.get("tool_timeout") or 30)
        except ValueError:
            server["toolTimeout"] = 30
        tools = [t.strip() for t in answer.get("enabled_tools", "").split(",") if t.strip()]
        server["enabledTools"] = tools or ["*"]
        servers = _get(self._data, ("tools", "mcpServers"), None)
        if not isinstance(servers, dict):
            servers = {}
            _set(self._data, ("tools", "mcpServers"), servers)
        if name and name != new_name:
            servers.pop(name, None)
        servers[new_name] = server
        self._dirty = True
        self.action_save()
        if self._section:
            self._fill_table(self._section, keep_key=new_name)

    # skills ----------------------------------------------------------------

    async def _skill_action(self, key: str, row: HubRow | None) -> None:
        if key == "i":
            await self._install_skill()
            return
        if row is None:
            return
        if key in {"enter", "e", "t"}:
            disabled = {
                str(s)
                for s in (_get(self._data, ("agents", "defaults", "disabledSkills"), []) or [])
            }
            self._after_external_write(skill_set_enabled(row.key, row.key in disabled), row.key)

    async def _install_skill(self) -> None:
        fields = [
            FormField(
                "source",
                "Source",
                kind="select",
                options=(
                    ("git", "Git repository"),
                    ("npx", "npm package (npx)"),
                    ("path", "Local folder"),
                ),
            ),
            FormField(
                "location",
                "URL, package or folder",
                placeholder="https://github.com/org/skills  ·  @scope/pack  ·  ~/my-skills",
            ),
            FormField(
                "name", "Name (optional)", required=False, placeholder="defaults to the pack name"
            ),
        ]
        answer = await self.app.push_screen_wait(
            FormScreen(
                "Install skill",
                fields,
                hint="Installs a pack (skills + MCP servers) and publishes its skills to the workspace.",
                submit_label="Install",
            )
        )
        if answer is None:
            return
        self._status("installing…")
        self._install_worker(answer["source"], answer["location"], answer.get("name") or None)

    @work(thread=True, exclusive=True, group="skills")
    def _install_worker(self, source: str, location: str, name: str | None) -> None:
        try:
            from navin.webui.plugins_api import install_plugin

            result = install_plugin(
                source=source,
                location=location,
                name=name,
                scope="workspace",
                workspace_path=Path(self._workspace),
            )
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(
                self._status, f"[$error]{escape(str(getattr(exc, 'message', exc)))}[/]"
            )
            return
        plugin = result.get("plugin") or {}
        copied = result.get("copied") or []
        text = f"[$success]installed {escape(str(plugin.get('name') or location))}[/]  [dim]{len(copied)} skills · restart navin-cli to load them[/dim]"
        self.app.call_from_thread(self._after_external_write, text, None)

    # rules -----------------------------------------------------------------

    async def _rule_action(self, key: str, row: HubRow | None) -> None:
        from navin.agent.project_rules import write_navin_rule

        if key == "n":
            answer = await self.app.push_screen_wait(
                FormScreen(
                    "New rule",
                    [FormField("name", "Rule name", placeholder="coding-style")],
                    hint=f"Creates .navin/rules/<name>.md in {escape(str(self._project_root))}",
                    submit_label="Create",
                )
            )
            if answer is None:
                return
            name = answer["name"]
            content = await self.app.push_screen_wait(
                RuleEditor(f"Rule: {escape(name)}", f"# {name}\n\n- \n")
            )
            if content is None:
                return
            try:
                write_navin_rule(self._project_root, name=name, content=content)
            except ValueError as exc:
                self._status(f"[$error]{escape(str(exc))}[/]")
                return
            self._after_external_write(f"[$success]rule {escape(name)} saved[/]", name)
            return
        if row is None or key not in {"enter", "e"}:
            return
        try:
            from navin.agent.project_rules import list_navin_rules

            current = next(
                (r for r in list_navin_rules(self._project_root) if r["name"] == row.key), None
            )
        except Exception:  # noqa: BLE001
            current = None
        content = await self.app.push_screen_wait(
            RuleEditor(f"Rule: {escape(row.key)}", str((current or {}).get("content") or ""))
        )
        if content is None:
            return
        try:
            write_navin_rule(self._project_root, name=row.key, content=content)
        except ValueError as exc:
            self._status(f"[$error]{escape(str(exc))}[/]")
            return
        self._after_external_write(f"[$success]rule {escape(row.key)} saved[/]", row.key)

    # -- save / close -----------------------------------------------------

    def action_save(self) -> None:
        errors: list[str] = []
        try:
            from navin.board.autonomy import read_autonomy, write_autonomy

            current = read_autonomy(self._project_root)
            changed = {
                k: v
                for k, v in self._autonomy.items()
                if k
                in {
                    "enabled",
                    "auto_branch",
                    "open_pr_on_done",
                    "sync_github_issues",
                    "fix_issues",
                    "autopilot_loop",
                    "issues_repo",
                }
                and current.get(k) != v
            }
            if changed:
                write_autonomy(self._project_root, changed, actor="tui")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"guardrails: {exc}")
        error = self._save(self._data)
        if error:
            errors.append(error)
        if errors:
            self._status("[$error]" + escape(" · ".join(errors)) + "[/]")
            return
        self._dirty = False
        self._discard_armed = False
        self._status("[$success]saved[/]  [dim]some changes need a restart of navin-cli[/dim]")

    def action_close(self) -> None:
        inp = self.query_one("#value", Input)
        if inp.has_focus:
            inp.remove_class("-visible")
            self._editing = None
            self.query_one("#rows", OptionList).focus()
            return
        if self._dirty and not self._discard_armed:
            self._discard_armed = True
            self._status("[$warning]unsaved changes[/]  ctrl+s saves · esc again discards")
            return
        self.dismiss(self._dirty)

    def action_focus_next(self) -> None:
        nav = self.query_one("#nav", OptionList)
        if nav.has_focus and self._section is not None:
            target = (
                self.query_one("#table", DataTable)
                if self._section.kind in {"providers", "models", "mcp", "skills", "rules"}
                else self.query_one("#rows", OptionList)
            )
            target.focus()
        else:
            nav.focus()

    def _status(self, text: str) -> None:
        self.query_one("#foot .status", Static).update(text)


__all__ = ["Field", "Section", "SettingsHub", "build_sections"]
