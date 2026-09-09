"""Configuration hubs: one screen per domain, mirroring Navin Desktop settings.

Each hub is a table of the current state plus keyboard actions. Editing always
goes through the schema-validated ``SettingsScreen`` (scoped to the domain
subtree) so no hub can write an invalid ``config.json``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

# ---------------------------------------------------------------------------
# Domain catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Domain:
    id: str
    title: str
    description: str
    roots: tuple[tuple[str, ...], ...]  # config subtrees (by_alias keys)
    commands: tuple[str, ...] = ()  # related slash commands
    icon: str = "⚙"


DOMAINS: tuple[Domain, ...] = (
    Domain(
        "account",
        "Account",
        "navin.live subscription, managed models, usage.",
        (("license",),),
        (),
        "👤",
    ),
    Domain(
        "providers",
        "Providers",
        "API keys, base URLs and OAuth providers.",
        (("providers",),),
        (),
        "🔑",
    ),
    Domain(
        "models",
        "Models",
        "Active model, presets, routes and catalog.",
        (("agents", "defaults"), ("modelPresets",), ("modelRoutes",), ("modelCatalog",)),
        ("/model",),
        "🧠",
    ),
    Domain(
        "mcp",
        "MCP servers",
        "Model Context Protocol servers and presets.",
        (("tools", "mcpServers"), ("tools", "autoEnableMcpPresets")),
        (),
        "🔌",
    ),
    Domain(
        "tools",
        "Tools",
        "Every tool available to the agent and its options.",
        (("tools",),),
        (),
        "🛠",
    ),
    Domain(
        "skills", "Skills", "Installed skills, sources and availability.", (), ("/skill",), "📚"
    ),
    Domain(
        "loop",
        "Agent loop",
        "Iterations, retries, guardrails, tool result clearing.",
        (("agents", "defaults"), ("loops",), ("resources",)),
        ("/goal", "/status", "/pilot"),
        "🔁",
    ),
    Domain(
        "image",
        "Image",
        "Image generation and visual QA providers.",
        (("tools", "imageGeneration"), ("tools", "visualQA")),
        (),
        "🖼",
    ),
    Domain(
        "video",
        "Video",
        "Video generation and montage.",
        (("tools", "videoGeneration"), ("tools", "montage")),
        ("/montage",),
        "🎬",
    ),
    Domain(
        "audio",
        "Audio",
        "Speech and music generation, transcription.",
        (("tools", "speechGeneration"), ("tools", "musicGeneration"), ("transcription",)),
        (),
        "🎧",
    ),
    Domain(
        "voice", "Voice", "Realtime voice, TTS voice, LiveKit.", (("voice",), ("livekit",)), (), "🎙"
    ),
    Domain(
        "web",
        "Web",
        "Web search, fetch, browser automation, scraping.",
        (("tools", "web"), ("tools", "browser"), ("tools", "scrape"), ("tools", "semanticSearch")),
        (),
        "🌐",
    ),
    Domain(
        "security",
        "Security",
        "Approvals, exec policy, workspace restriction, SSRF.",
        (
            ("tools", "approvals"),
            ("tools", "exec"),
            ("tools", "securityProfile"),
            ("tools", "restrictToWorkspace"),
            ("tools", "ssrfProtection"),
            ("tools", "ssrfWhitelist"),
            ("tools", "file"),
        ),
        ("/fortify",),
        "🛡",
    ),
    Domain(
        "guardrails",
        "Guardrails",
        "Board autonomy for the current project.",
        (),
        (),
        "⚖",
    ),
    Domain(
        "git",
        "Git",
        "Global git switches and forge tokens.",
        (("tools", "boardGit"), ("tools", "forge")),
        (),
        "⎇",
    ),
    Domain(
        "browser",
        "Browser",
        "The agent's Playwright browser.",
        (("tools", "browser"),),
        (),
        "◎",
    ),
    Domain(
        "computer",
        "Computer",
        "Desktop control: screen, mouse and keyboard in any app.",
        (("tools", "computer"),),
        (),
        "▣",
    ),
    Domain(
        "rules",
        "Rules",
        "Project rules in .navin/rules/*.md.",
        (),
        (),
        "☰",
    ),
    Domain(
        "channels",
        "Channels",
        "Telegram, WhatsApp, Discord... and message rendering.",
        (("channels",),),
        (),
        "💬",
    ),
    Domain(
        "memory",
        "Memory & checkpoints",
        "Long-term memory (Dream), checkpoints, history.",
        (("agents", "defaults"),),
        ("/dream", "/dream-log", "/checkpoint", "/history"),
        "🧷",
    ),
    Domain(
        "automation",
        "Automation & tasks",
        "Board tasks, triggers, goals, heartbeat, gateway.",
        (("gateway",),),
        ("/board", "/trigger", "/goal"),
        "⏰",
    ),
    Domain(
        "plugins",
        "Packs & plugins",
        "Plugin packs (skills + MCP servers) and app templates.",
        (),
        ("/pack",),
        "📦",
    ),
)

DOMAIN_BY_ID = {d.id: d for d in DOMAINS}


# ---------------------------------------------------------------------------
# Generic table hub
# ---------------------------------------------------------------------------


@dataclass
class HubRow:
    key: str
    cells: list[str]
    detail: str = ""
    edit_path: tuple[str, ...] | None = None


@dataclass
class HubAction:
    key: str  # keyboard key
    label: str
    handler: Callable[["TableHub", HubRow | None], Any]


class TableHub(ModalScreen[Any]):
    """Table of items with contextual actions; ``dismiss`` returns an action tuple."""

    DEFAULT_CSS = """
    TableHub { align: center middle; }
    TableHub > Vertical {
        width: 96%;
        height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    TableHub .hub-title { text-style: bold; color: $primary; }
    TableHub .hub-desc { color: $text-muted; margin: 0 0 1 0; }
    TableHub DataTable { height: 1fr; }
    TableHub #hub-detail { height: auto; max-height: 8; color: $text-muted; border-top: tall $panel; padding: 0 1; }
    TableHub #hub-actions { height: auto; margin: 1 0 0 0; }
    TableHub #hub-actions Button { margin: 0 1 0 0; }
    TableHub #hub-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(
        self,
        domain: Domain,
        columns: list[str],
        rows: list[HubRow],
        actions: list[HubAction],
        *,
        empty_hint: str = "",
    ) -> None:
        super().__init__()
        self.domain = domain
        self._columns = columns
        self._rows = rows
        self._actions = actions
        self._empty_hint = empty_hint

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"{self.domain.icon} {escape(self.domain.title)}", classes="hub-title", markup=True
            )
            cmds = (
                f"  [dim]commands: {' '.join(self.domain.commands)}[/dim]"
                if self.domain.commands
                else ""
            )
            yield Static(
                f"{escape(self.domain.description)}{cmds}", classes="hub-desc", markup=True
            )
            table = DataTable(zebra_stripes=True, cursor_type="row")
            table.add_columns(*self._columns)
            yield table
            yield Static(self._empty_hint if not self._rows else "", id="hub-detail", markup=True)
            with Horizontal(id="hub-actions"):
                for action in self._actions:
                    yield Button(
                        f"{action.label}  [{action.key}]", id=f"act-{action.key}", compact=True
                    )
                yield Button("Close  [esc]", id="act-close", compact=True)
            yield Static("", id="hub-status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for row in self._rows:
            table.add_row(*row.cells, key=row.key)
        table.focus()

    def current_row(self) -> HubRow | None:
        table = self.query_one(DataTable)
        if not self._rows or table.cursor_row is None:
            return None
        try:
            key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        except Exception:  # noqa: BLE001
            return None
        return next((r for r in self._rows if r.key == key), None)

    @on(DataTable.RowHighlighted)
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        row = next((r for r in self._rows if r.key == key), None)
        if row:
            self.query_one("#hub-detail", Static).update(row.detail)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        if self._actions:
            self._run(self._actions[0])

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "act-close":
            self.action_close()
            return
        key = bid.removeprefix("act-")
        action = next((a for a in self._actions if a.key == key), None)
        if action:
            self._run(action)

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        for action in self._actions:
            if event.key == action.key:
                event.stop()
                self._run(action)
                return

    def _run(self, action: HubAction) -> None:
        result = action.handler(self, self.current_row())
        if asyncio.iscoroutine(result):
            self.run_worker(result, exclusive=False)

    def status(self, text: str) -> None:
        self.query_one("#hub-status", Static).update(text)

    def set_rows(self, rows: list[HubRow], *, keep_key: str | None = None) -> None:
        """Replace the table content (after an action mutated config)."""
        self._rows = rows
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row.cells, key=row.key)
        if not rows:
            self.query_one("#hub-detail", Static).update(self._empty_hint)
            return
        target = 0
        if keep_key is not None:
            target = next((i for i, r in enumerate(rows) if r.key == keep_key), 0)
        table.move_cursor(row=target)
        self.query_one("#hub-detail", Static).update(rows[target].detail)

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Row builders (pure functions over config / runtime)
# ---------------------------------------------------------------------------


def _mask(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "-"
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def provider_rows(config_data: dict[str, Any]) -> list[HubRow]:
    from navin.providers.registry import PROVIDERS
    from navin.providers.settings_order import is_retired_llm_provider

    providers = config_data.get("providers") or {}
    rows: list[HubRow] = []
    seen: set[str] = set()
    for spec in PROVIDERS:
        if is_retired_llm_provider(spec.name):
            continue
        alias = _alias(spec.name)
        section = providers.get(alias) or providers.get(spec.name) or {}
        seen.add(alias)
        key = section.get("apiKey") if isinstance(section, dict) else None
        own_base = (section.get("apiBase") if isinstance(section, dict) else None) or ""
        oauth = bool(section.get("oauthKey")) if isinstance(section, dict) else False
        base = own_base or spec.default_api_base or ""
        kind = (
            "oauth"
            if spec.is_oauth
            else ("local" if spec.is_local else ("gateway" if spec.is_gateway else "api"))
        )
        # Configured = the user set something (a key, an OAuth token or their own endpoint), like the desktop.
        configured = "✓" if key or oauth or (spec.is_local and own_base) else "·"
        rows.append(
            HubRow(
                key=alias,
                cells=[
                    configured,
                    spec.display_name or spec.name,
                    kind,
                    _mask(key) if key else "-",
                    base[:48],
                ],
                detail=(
                    f"[b]{escape(spec.display_name or spec.name)}[/b]  env: {escape(spec.env_key)}\n"
                    f"models: {escape(', '.join(spec.keywords[:8]))}"
                ),
                edit_path=("providers", alias),
            )
        )
    for alias, section in sorted(providers.items()):
        if alias in seen or not isinstance(section, dict):
            continue
        if is_retired_llm_provider(str(alias)):
            continue
        rows.append(
            HubRow(
                key=alias,
                cells=[
                    "✓" if section.get("apiKey") else "·",
                    alias,
                    "custom",
                    _mask(section.get("apiKey")),
                    str(section.get("apiBase") or "")[:48],
                ],
                detail=f"custom provider [b]{escape(alias)}[/b]",
                edit_path=("providers", alias),
            )
        )
    rows.sort(key=lambda r: (r.cells[0] != "✓", r.cells[1].lower()))
    return rows


def provider_panel_label(config_data: dict[str, Any], active: str) -> str:
    """Panel chip: real provider name (not the model prefix) plus configured count."""
    rows = provider_rows(config_data)
    configured = sum(1 for row in rows if row.cells and row.cells[0] == "✓")
    name = (active or "").strip()
    key = name.lower()
    for row in rows:
        alias = str(row.key or "").lower()
        display = str(row.cells[1] if len(row.cells) > 1 else "").strip()
        if alias == key or display.lower() == key:
            name = display
            break
    if name and configured:
        return f"{name}  {configured}"
    if configured:
        return str(configured)
    return name


def _alias(snake: str) -> str:
    parts = snake.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def model_rows(presets: list[dict[str, str]], active: str) -> list[HubRow]:
    rows: list[HubRow] = []
    for preset in presets:
        name = preset["name"]
        rows.append(
            HubRow(
                key=name,
                cells=[
                    "●" if name == active else "○",
                    name,
                    preset.get("model") or "-",
                    preset.get("provider") or "auto",
                    preset.get("label") or "",
                ],
                detail=f"[b]{escape(name)}[/b]  model {escape(preset.get('model') or '-')}  provider {escape(preset.get('provider') or 'auto')}",
                edit_path=("agents", "defaults") if name == "default" else ("modelPresets", name),
            )
        )
    return rows


def mcp_rows(config_data: dict[str, Any]) -> list[HubRow]:
    servers = ((config_data.get("tools") or {}).get("mcpServers")) or {}
    rows: list[HubRow] = []
    for name, cfg in sorted(servers.items()):
        if not isinstance(cfg, dict):
            continue
        kind = cfg.get("type") or ("stdio" if cfg.get("command") else "http")
        target = cfg.get("command") or cfg.get("url") or ""
        if cfg.get("args"):
            target = f"{target} {' '.join(map(str, cfg['args']))}"
        tools = cfg.get("enabledTools") or ["*"]
        rows.append(
            HubRow(
                key=name,
                cells=[
                    name,
                    str(kind),
                    target[:60],
                    ", ".join(map(str, tools))[:30],
                    str(cfg.get("toolTimeout") or 30),
                ],
                detail=f"[b]{escape(name)}[/b]\n{escape(json.dumps(cfg, ensure_ascii=False)[:600])}",
                edit_path=("tools", "mcpServers", name),
            )
        )
    return rows


def mcp_preset_rows(configured: set[str]) -> list[HubRow]:
    try:
        from navin.webui.mcp_presets_api import MCP_PRESETS
    except Exception:  # noqa: BLE001
        return []
    rows: list[HubRow] = []
    for preset in MCP_PRESETS:
        name = str(getattr(preset, "name", "") or "")
        if not name or name in configured:
            continue
        supported = bool(getattr(preset, "install_supported", False))
        fields = [f.label for f in getattr(preset, "fields", ()) if getattr(f, "required", True)]
        needs = f"needs: {', '.join(fields)}" if fields else "no credentials needed"
        rows.append(
            HubRow(
                key=f"preset:{name}",
                cells=[
                    str(getattr(preset, "display_name", "") or name),
                    "preset" if supported else "preset (manual)",
                    str(getattr(preset, "category", "") or "")[:30],
                    needs[:30],
                    str(getattr(preset, "transport", "") or ""),
                ],
                detail=(
                    f"[b]{escape(str(getattr(preset, 'display_name', '') or name))}[/b]  {escape(str(getattr(preset, 'description', '') or '')[:400])}\n"
                    f"[dim]{escape(needs)}  ·  press [b]i[/b] to enable{'' if supported else ' (not installable from here, see docs: ' + escape(str(getattr(preset, 'docs_url', '') or '')) + ')'}[/dim]"
                ),
            )
        )
    return rows


def mcp_enable_preset(name: str, values: dict[str, str]) -> str:
    """Enable a catalog preset (writes tools.mcpServers). Returns a status line."""
    from navin.webui.mcp_presets_api import McpPresetError, mcp_presets_action

    query: dict[str, list[str]] = {"name": [name]}
    for key, value in values.items():
        if value:
            query[key] = [value]
    try:
        payload = mcp_presets_action("enable", query)
    except McpPresetError as exc:
        return f"[$error]{escape(str(exc))}[/]"
    message = (payload.get("last_action") or {}).get("message") or f"{name} enabled"
    return f"[$success]{escape(str(message))}[/]  [dim]restart navin-cli to connect it[/dim]"


def mcp_remove_server(name: str) -> str:
    from navin.webui.mcp_presets_api import McpPresetError, mcp_presets_action

    try:
        payload = mcp_presets_action("remove", {"name": [name]})
    except McpPresetError as exc:
        return f"[$error]{escape(str(exc))}[/]"
    message = (payload.get("last_action") or {}).get("message") or f"{name} removed"
    return f"[$success]{escape(str(message))}[/]"


def mcp_preset_fields(name: str) -> list[tuple[str, str, str, bool, bool]]:
    """(name, label, placeholder, secret, required) for a preset's inputs."""
    try:
        from navin.webui.mcp_presets_api import MCP_PRESETS
    except Exception:  # noqa: BLE001
        return []
    for preset in MCP_PRESETS:
        if getattr(preset, "name", "") == name:
            return [
                (
                    f.name,
                    f.label,
                    getattr(f, "placeholder", "") or "",
                    bool(getattr(f, "secret", True)),
                    bool(getattr(f, "required", True)),
                )
                for f in getattr(preset, "fields", ())
            ]
    return []


def skill_set_enabled(name: str, enabled: bool) -> str:
    """Toggle a skill through agents.defaults.disabled_skills in config.json."""
    from navin.config.loader import load_config, save_config

    config = load_config()
    disabled = list(config.agents.defaults.disabled_skills or [])
    if enabled and name in disabled:
        disabled.remove(name)
    elif not enabled and name not in disabled:
        disabled.append(name)
    config.agents.defaults.disabled_skills = disabled
    save_config(config)
    state = "enabled" if enabled else "disabled"
    return f"[$success]{escape(name)} {state}[/]  [dim]restart navin-cli to apply[/dim]"


def skill_import_from_path(workspace: Any, scan_root: str) -> str:
    from pathlib import Path as _Path

    from navin.webui.skills_api import SkillsApiError, import_workspace_skills

    root = _Path(scan_root).expanduser()
    if not root.exists():
        return f"[$error]path not found: {escape(scan_root)}[/]"
    try:
        result = import_workspace_skills(
            _Path(workspace), scan_root=root, apply_root=_Path(workspace)
        )
    except SkillsApiError as exc:
        return f"[$error]{escape(exc.message)}[/]"
    except Exception as exc:  # noqa: BLE001
        return f"[$error]{escape(str(exc))}[/]"
    imported = result.get("imported") or []
    skipped = result.get("skipped") or []
    if not imported:
        return f"[$warning]no skill imported[/]  [dim]{len(skipped)} skipped[/dim]"
    return f"[$success]imported: {escape(', '.join(map(str, imported)))}[/]  [dim]{len(skipped)} skipped · restart navin-cli to load them[/dim]"


def skill_rows(agent_loop: Any, workspace: Any, disabled: set[str]) -> list[HubRow]:
    """All skills (including disabled ones) with state: ✓ enabled, ○ disabled, ✗ unavailable."""
    loader = None
    try:
        from pathlib import Path as _Path

        from navin.agent.skills import SkillsLoader

        loader = SkillsLoader(_Path(workspace))  # no exclusions: we want disabled ones too
    except Exception:  # noqa: BLE001
        loader = getattr(getattr(agent_loop, "context", None), "skills", None)
    if loader is None:
        return []
    try:
        skills = loader.list_skills(filter_unavailable=False)
    except TypeError:
        skills = loader.list_skills()
    except Exception:  # noqa: BLE001
        return []
    rows: list[HubRow] = []
    for skill in skills:
        name = str(skill.get("name") or "")
        if not name:
            continue
        available, reason = True, ""
        try:
            available, reason = loader.get_skill_availability(name)
        except Exception:  # noqa: BLE001
            pass
        desc = str(skill.get("description") or "")
        if not desc:
            try:
                desc = loader._get_skill_description(name)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                desc = ""
        if name in disabled:
            mark, state = "○", "[$warning]disabled[/] (agents.defaults.disabledSkills)"
        elif available:
            mark, state = "✓", "[$success]enabled[/]"
        else:
            mark, state = "✗", f"[$error]unavailable: {escape(reason)}[/]"
        rows.append(
            HubRow(
                key=name,
                cells=[mark, name, str(skill.get("source") or ""), desc[:70]],
                detail=f"[b]{escape(name)}[/b]  {escape(str(skill.get('source') or ''))}  {state}\n{escape(desc[:500])}",
            )
        )
    rows.sort(key=lambda r: ({"✓": 0, "○": 1, "✗": 2}.get(r.cells[0], 3), r.cells[1]))
    return rows


def tool_rows(rows: list[dict[str, Any]]) -> list[HubRow]:
    out: list[HubRow] = []
    for row in rows:
        desc = row["description"].splitlines()[0] if row["description"] else ""
        out.append(
            HubRow(
                key=row["name"],
                cells=[
                    row["name"],
                    "MCP" if row["mcp"] else "builtin",
                    ", ".join(row["params"][:5])[:40],
                    desc[:70],
                ],
                detail=f"[b]{escape(row['name'])}[/b]\n{escape(row['description'][:600])}",
            )
        )
    return out


def config_section_rows(
    config_data: dict[str, Any], roots: tuple[tuple[str, ...], ...]
) -> list[HubRow]:
    """Flat rows for the leaves of the given subtrees (scalar values only)."""
    rows: list[HubRow] = []

    def walk(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                walk(value[key], path + (str(key),))
        elif isinstance(value, list):
            rows.append(
                HubRow(
                    key=".".join(path),
                    cells=[
                        ".".join(path),
                        f"list ({len(value)})",
                        json.dumps(value, ensure_ascii=False)[:60],
                    ],
                    detail=escape(json.dumps(value, ensure_ascii=False)[:600]),
                    edit_path=path,
                )
            )
        else:
            secret = any(h in path[-1].lower() for h in ("key", "token", "secret", "password"))
            shown = _mask(value) if secret and value else json.dumps(value, ensure_ascii=False)
            rows.append(
                HubRow(
                    key=".".join(path),
                    cells=[".".join(path), type(value).__name__, shown[:60]],
                    detail=f"[b]{escape('.'.join(path))}[/b] = {escape(shown)}",
                    edit_path=path,
                )
            )

    for root in roots:
        cur: Any = config_data
        try:
            for key in root:
                cur = cur[key]
        except (KeyError, TypeError):
            continue
        if isinstance(cur, (dict, list)):
            walk(cur, root)
        else:
            walk(cur, root)
    return rows


# ---------------------------------------------------------------------------
# Account screen (navin.live)
# ---------------------------------------------------------------------------


class AccountScreen(ModalScreen[str | None]):
    """Sign in with navin.live (browser handoff), refresh usage, disconnect."""

    DEFAULT_CSS = """
    AccountScreen { align: center middle; }
    AccountScreen > Vertical {
        width: 80;
        max-width: 96%;
        height: auto;
        max-height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    AccountScreen .hub-title { text-style: bold; color: $primary; }
    AccountScreen #account-body { height: auto; margin: 1 0; }
    AccountScreen Horizontal { height: auto; margin: 1 0 0 0; }
    AccountScreen Button { margin: 0 1 0 0; }
    AccountScreen #account-status { color: $text-muted; height: auto; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("c", "copy_link", "Copy link"),
    ]

    def __init__(self, service: Any = None, on_applied: Any = None) -> None:
        super().__init__()
        self._service: Any = service
        self._on_applied = on_applied
        self._state: str = ""
        self._connect_url: str = ""
        self._applied_key: tuple[Any, ...] | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("👤 Account  [dim]navin.live[/dim]", classes="hub-title", markup=True)
            yield Static("Loading…", id="account-body", markup=True)
            with Horizontal():
                yield Button("Sign in with navin.live", variant="primary", id="signin")
                yield Button("Copy link", id="copylink")
                yield Button("Refresh", id="refresh")
                yield Button("Disconnect", variant="error", id="disconnect")
                yield Button("Close", id="close")
            yield Static("", id="account-status", markup=True)

    def on_mount(self) -> None:
        if self._service is None:
            try:
                from navin.webui.account_api import WebUIAccountService

                self._service = WebUIAccountService()
            except Exception as exc:  # noqa: BLE001
                self._status(f"[$error]account service unavailable: {escape(str(exc))}[/]")
                return
        self._load_status(refresh=False)

    @work(thread=True, exclusive=True, group="account")
    def _load_status(self, *, refresh: bool) -> None:
        payload = self._service.status_payload(refresh=refresh)
        self.app.call_from_thread(self._show, payload)

    def _show(self, payload: dict[str, Any]) -> None:
        lines: list[str] = []
        if payload.get("connected"):
            who = payload.get("name") or payload.get("email") or "connected"
            plan = payload.get("plan_label") or payload.get("plan") or "free"
            price = payload.get("plan_price_usd")
            price_s = f"  [dim]${price}/month[/dim]" if isinstance(price, int) and price > 0 else ""
            lines.append(
                f"[$success]● Connected[/]  [b]{escape(str(who))}[/b]  plan [b]{escape(str(plan))}[/b]{price_s}"
            )
            if payload.get("email"):
                lines.append(f"[dim]{escape(str(payload['email']))}[/dim]")
            if payload.get("managed_key_active"):
                lines.append(
                    "[$success]✓ Managed models ready[/] [dim](calls billed on the plan)[/dim]"
                )
            else:
                lines.append("[dim]BYOK: your own provider keys are used.[/dim]")
            usage = payload.get("usage")
            if isinstance(usage, dict):
                pct = int(usage.get("used_percent") or 0)
                filled = int(pct / 5)
                bar = "█" * filled + "░" * (20 - filled)
                lines.append(f"usage {bar} {pct}%")
            if payload.get("org_id"):
                lines.append(
                    f"[dim]org {escape(str(payload['org_id']))}  role {escape(str(payload.get('org_role') or ''))}  seats {payload.get('seat_count') or '-'}[/dim]"
                )
            if payload.get("status") and payload["status"] not in {"active", "ok"}:
                lines.append(f"[$warning]status: {escape(str(payload['status']))}[/]")
        else:
            lines.append("[$warning]○ Not connected[/]")
            lines.append(
                "Sign in opens navin.live in your browser; this device is linked automatically once you confirm."
            )
        lines.append(f"[dim]server {escape(str(payload.get('server_url') or ''))}[/dim]")
        self.query_one("#account-body", Static).update("\n".join(lines))
        key = (
            bool(payload.get("connected")),
            str(payload.get("plan") or ""),
            bool(payload.get("managed_key_active")),
        )
        if key != self._applied_key:
            self._applied_key = key
            if callable(self._on_applied):
                self._on_applied(payload)

    def _ensure_connect_url(self) -> str:
        if self._service is None:
            raise RuntimeError("account service unavailable")
        if self._connect_url:
            return self._connect_url
        url = self._service.connect_url(locale="en")
        self._connect_url = url
        self._state = self._service.connect_state_from_url(url)
        self._service.start_background_claim(self._state)
        self.set_interval(3.0, self._poll_connected, name="account-poll")
        return url

    def _copy_connect_url(self, url: str) -> bool:
        copied = False
        with contextlib.suppress(Exception):
            self.app.copy_to_clipboard(url)
            copied = True
        from navin.tui.clipboard import write_clipboard

        if write_clipboard(url):
            copied = True
        return copied

    @on(Button.Pressed, "#signin")
    def _signin(self) -> None:
        try:
            url = self._ensure_connect_url()
        except Exception as exc:  # noqa: BLE001
            self._status(f"[$error]{escape(str(exc))}[/]")
            return
        opened = False
        try:
            opened = bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001
            opened = False
        hint = "Browser opened." if opened else "Could not open the browser."
        self._status(
            f"{hint}\n[b]{escape(url)}[/b]\n"
            "If the browser did not open: press C or Copy link.\n"
            "Waiting for confirmation... this screen refreshes automatically."
        )

    @on(Button.Pressed, "#copylink")
    def action_copy_link(self) -> None:
        try:
            url = self._ensure_connect_url()
        except Exception as exc:  # noqa: BLE001
            self._status(f"[$error]{escape(str(exc))}[/]")
            return
        if self._copy_connect_url(url):
            self._status(
                f"Link copied.\n[b]{escape(url)}[/b]\n"
                "Paste it in a browser if it did not open.\n"
                "Waiting for confirmation... this screen refreshes automatically."
            )
        else:
            self._status(
                f"Could not copy automatically. Select this URL:\n[b]{escape(url)}[/b]\n"
                "Waiting for confirmation... this screen refreshes automatically."
            )

    def _poll_connected(self) -> None:
        if self._service is None:
            return
        self._load_status(refresh=False)

    @on(Button.Pressed, "#refresh")
    def _refresh(self) -> None:
        self._status("refreshing…")
        self._load_status(refresh=True)

    @on(Button.Pressed, "#disconnect")
    def _disconnect(self) -> None:
        self._status("disconnecting…")
        self._disconnect_worker()

    @work(thread=True, exclusive=True, group="account")
    def _disconnect_worker(self) -> None:
        try:
            payload = self._service.disconnect()
        except Exception as exc:  # noqa: BLE001
            self.app.call_from_thread(self._status, f"[$error]{escape(str(exc))}[/]")
            return
        self.app.call_from_thread(self._show, payload)
        self.app.call_from_thread(self._status, "disconnected")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(None)

    def _status(self, text: str) -> None:
        self.query_one("#account-status", Static).update(text)
