# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Modal screens: pickers, tools browser, settings editor, help."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    Tree,
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

# ---------------------------------------------------------------------------
# Generic filterable picker
# ---------------------------------------------------------------------------

RENAME_PREFIX = "__rename__:"


@dataclass(frozen=True)
class PickItem:
    id: str
    title: str
    subtitle: str = ""
    badge: str = ""


class PickerScreen(ModalScreen[str | None]):
    """A searchable list; dismisses with the selected id (or None)."""

    DEFAULT_CSS = """
    PickerScreen { align: center middle; }
    PickerScreen * { text-style: none; }
    PickerScreen > Vertical {
        width: 76;
        max-width: 96%;
        height: 80%;
        max-height: 28;
        min-height: 14;
        border: solid #5A7A9A;
        background: $surface;
        padding: 1 2;
        overflow: hidden;
    }
    PickerScreen .picker-title { height: 1; color: $foreground; text-style: none; }
    PickerScreen .picker-hint { height: 1; color: #9A9A9A; text-style: none; margin: 0 0 1 0; }
    PickerScreen #filter {
        height: 1;
        margin: 0 0 1 0;
        background: #2A2A2A;
        color: $foreground;
        padding: 0 1;
        text-style: none;
    }
    PickerScreen OptionList {
        height: 1fr;
        min-height: 8;
        border: none;
        background: transparent;
        scrollbar-size-vertical: 1;
        text-style: none;
    }
    PickerScreen OptionList > .option-list--option,
    PickerScreen OptionList > .option-list--option-highlighted {
        text-style: none;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", priority=True),
        Binding("f2", "rename", "Rename", priority=True),
        Binding("enter", "confirm", "Open", priority=True),
    ]

    def __init__(
        self,
        title: str,
        items: list[PickItem],
        *,
        hint: str = "",
        current: str | None = None,
        renamable: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._items = items
        self._hint = hint
        self._current = current
        self._renamable = renamable
        self._filtered: list[PickItem] = items
        self._query = ""
        self._mode = "search"
        self._rename_key: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="picker-title", markup=False)
            if self._hint:
                yield Static(self._hint, classes="picker-hint", markup=False)
            yield Static("Search  name or date", id="filter", markup=False)
            yield OptionList(id="options")

    def on_mount(self) -> None:
        self._fill(self._items)
        self.query_one("#options", OptionList).focus()

    def _paint_field(self) -> None:
        if not self.is_mounted:
            return
        label = "Rename" if self._mode == "rename" else "Search"
        shown = self._query if self._query else ("type a name" if self._mode == "rename" else "name or date")
        caret = "_" if self._query else ""
        try:
            self.query_one("#filter", Static).update(f"{label}  {shown}{caret}")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        compact = " ".join((text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[: max(limit - 1, 1)] + "…"

    def _option_line(self, item: PickItem, mark: str) -> Text:
        # Plain Rich Text: bold / dim / wide glyphs paint twice on Windows Terminal.
        title = self._clip(item.title, 44)
        badge = self._clip(item.badge, 14) if item.badge else ""
        extra = self._clip(item.subtitle, 18) if item.subtitle else ""
        tail = "  ".join(part for part in (extra, badge) if part)
        line = Text()
        line.append(f"{mark} {title}")
        if tail:
            line.append(f"  {tail}", style="#9A9A9A")
        return line

    def _fill(self, items: list[PickItem]) -> None:
        self._filtered = items
        options = self.query_one("#options", OptionList)
        options.clear_options()
        highlight = 0
        for idx, item in enumerate(items):
            mark = ">" if item.id == self._current else "-"
            if item.id == self._current:
                highlight = idx
            options.add_option(Option(self._option_line(item, mark), id=f"{idx}"))
        if items:
            options.highlighted = highlight

    def _apply_filter(self, raw: str) -> None:
        self._query = raw
        self._sync_query()

    def _sync_query(self) -> None:
        self._paint_field()
        if self._mode == "rename":
            return
        needle = self._query.strip().lower()
        if not needle:
            self._fill(self._items)
            return
        words = needle.split()
        matched = [
            item
            for item in self._items
            if all(
                w in f"{item.id} {item.title} {item.subtitle} {item.badge}".lower() for w in words
            )
        ]
        self._fill(matched)

    def action_confirm(self) -> None:
        self._submit()

    def _submit(self) -> None:
        if self._mode == "rename" and self._rename_key:
            title = " ".join(self._query.split())
            self.dismiss(f"{RENAME_PREFIX}{self._rename_key}\n{title}")
            return
        options = self.query_one("#options", OptionList)
        idx = options.highlighted
        if idx is None and self._filtered:
            idx = 0
        if idx is not None and 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx].id)

    @on(OptionList.OptionSelected, "#options")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        if self._mode == "rename":
            return
        try:
            idx = int(event.option.id or "0")
        except ValueError:
            return
        if 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx].id)

    def _highlighted_item(self) -> PickItem | None:
        options = self.query_one("#options", OptionList)
        idx = options.highlighted
        if idx is None or not (0 <= idx < len(self._filtered)):
            return None
        return self._filtered[idx]

    def action_rename(self) -> None:
        if not self._renamable:
            return
        item = self._highlighted_item()
        if item is None or item.id.startswith("__"):
            return
        self._mode = "rename"
        self._rename_key = item.id
        self._query = "" if item.title == "Untitled chat" else item.title
        self._paint_field()

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in {"up", "down"}:
            options = self.query_one("#options", OptionList)
            options.focus()
            if event.key == "down":
                options.action_cursor_down()
            else:
                options.action_cursor_up()
            event.stop()
            return
        if event.key == "backspace":
            self._query = self._query[:-1]
            self._sync_query()
            event.stop()
            event.prevent_default()
            return
        char = getattr(event, "character", None)
        if char and char.isprintable() and len(char) == 1 and event.key not in {"enter"}:
            self._query += char
            self._sync_query()
            event.stop()
            event.prevent_default()

    def action_cancel(self) -> None:
        if self._mode == "rename":
            self._mode = "search"
            self._rename_key = None
            self._query = ""
            self._sync_query()
            return
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Small form (used by MCP preset install, skill install...)
# ---------------------------------------------------------------------------


# Textual Select rejects an empty string as a value; map "" <-> this sentinel.
_SELECT_BLANK = "__blank__"


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    placeholder: str = ""
    secret: bool = False
    required: bool = True
    value: str = ""
    kind: str = "text"  # text | select | toggle
    options: tuple[tuple[str, str], ...] = ()  # (value, label) for select
    help: str = ""


class FormScreen(ModalScreen[dict[str, str] | None]):
    """Small modal form. Text, secret, select and toggle fields; values come back as strings."""

    DEFAULT_CSS = """
    FormScreen { align: center middle; }
    FormScreen > VerticalScroll {
        width: 80;
        max-width: 96%;
        height: auto;
        max-height: 92%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    FormScreen .picker-title { text-style: none; color: $foreground; }
    FormScreen .picker-hint { color: $text-muted; margin: 0 0 1 0; }
    FormScreen .field-label { margin: 1 0 0 0; }
    FormScreen .field-help { color: $text-muted; }
    FormScreen Input {
        border: none;
        height: 1;
        padding: 0 1;
        background: #2A2A2A;
        color: $foreground;
        text-style: none;
    }
    FormScreen Input:focus { border: none; background: #2F3A4A; }
    FormScreen Input > .input--placeholder { color: #8A8A8A; text-style: none; }
    FormScreen Select { margin: 0; height: auto; }
    FormScreen Select > SelectCurrent { border: none; height: 1; padding: 0 1; background: $panel; }
    FormScreen Select:focus > SelectCurrent { border: none; background: $primary 20%; }
    FormScreen Checkbox { margin: 1 0 0 0; background: transparent; border: none; padding: 0; }
    FormScreen Horizontal { height: auto; margin: 1 0 0 0; }
    FormScreen Button { margin: 0 1 0 0; }
    FormScreen #form-error { color: $error; height: auto; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, fields: list[FormField], *, hint: str = "", submit_label: str = "OK"
    ) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._hint = hint
        self._submit_label = submit_label

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self._title, classes="picker-title", markup=True)
            if self._hint:
                yield Static(self._hint, classes="picker-hint", markup=True)
            for field in self._fields:
                fid = f"f-{field.name}"
                if field.kind == "toggle":
                    yield Checkbox(
                        field.label, value=field.value.lower() in {"true", "1", "yes", "on"}, id=fid
                    )
                else:
                    req = (
                        ""
                        if field.required or field.kind == "select"
                        else "  [dim](optional)[/dim]"
                    )
                    yield Static(f"{escape(field.label)}{req}", classes="field-label", markup=True)
                    if field.kind == "select":
                        options = [
                            (label, value if value != "" else _SELECT_BLANK)
                            for value, label in field.options
                        ]
                        values = [v for _, v in options]
                        raw = field.value if field.value != "" else _SELECT_BLANK
                        current = raw if raw in values else (values[0] if values else _SELECT_BLANK)
                        yield Select(options, value=current, allow_blank=False, id=fid)
                    else:
                        yield Input(
                            value=field.value,
                            placeholder=field.placeholder,
                            password=field.secret,
                            id=fid,
                            compact=True,
                        )
                if field.help:
                    yield Static(field.help, classes="field-help", markup=True)
            yield Static("", id="form-error")
            with Horizontal():
                yield Button(self._submit_label, variant="primary", id="ok")
                yield Button("Cancel  esc", id="cancel")

    def on_mount(self) -> None:
        for field in self._fields:
            if field.kind not in {"select", "toggle"}:
                self.query_one(f"#f-{field.name}", Input).focus()
                return
        if self._fields:
            self.query_one(f"#f-{self._fields[0].name}").focus()

    def _value_of(self, field: FormField) -> str:
        widget = self.query_one(f"#f-{field.name}")
        if isinstance(widget, Checkbox):
            return "true" if widget.value else "false"
        if isinstance(widget, Select):
            value = widget.value
            if value is Select.BLANK or value == _SELECT_BLANK:
                return ""
            return str(value)
        return str(widget.value).strip()  # type: ignore[union-attr]

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _submit(self) -> None:
        values: dict[str, str] = {}
        for field in self._fields:
            value = self._value_of(field)
            if field.required and field.kind == "text" and not value:
                self.query_one("#form-error", Static).update(f"{escape(field.label)} is required")
                self.query_one(f"#f-{field.name}").focus()
                return
            values[field.name] = value
        self.dismiss(values)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Tools browser
# ---------------------------------------------------------------------------


class ToolsScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ToolsScreen { align: center middle; }
    ToolsScreen > Vertical {
        width: 96%;
        height: 90%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    ToolsScreen .picker-title { text-style: bold; color: $primary; }
    ToolsScreen Input { margin: 0 0 1 0; }
    ToolsScreen DataTable { height: 1fr; }
    ToolsScreen #tool-detail { height: 8; color: $text-muted; border-top: tall $panel; padding: 0 1; }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self._rows = rows

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"Tools available to the agent  [dim]({len(self._rows)})[/dim]",
                classes="picker-title",
                markup=True,
            )
            yield Input(placeholder="Filter tools…", id="filter")
            table = DataTable(zebra_stripes=True, cursor_type="row")
            table.add_columns("Tool", "Kind", "Parameters", "Description")
            yield table
            yield Static("", id="tool-detail", markup=True)

    def on_mount(self) -> None:
        self._fill(self._rows)
        self.query_one("#filter", Input).focus()

    def _fill(self, rows: list[dict[str, Any]]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            desc = row["description"].splitlines()[0] if row["description"] else ""
            table.add_row(
                row["name"],
                "MCP" if row["mcp"] else "builtin",
                ", ".join(row["params"][:6]) + (" …" if len(row["params"]) > 6 else ""),
                desc[:90],
                key=row["name"],
            )

    @on(Input.Changed, "#filter")
    def _filter(self, event: Input.Changed) -> None:
        needle = event.value.strip().lower()
        rows = (
            self._rows
            if not needle
            else [r for r in self._rows if needle in f"{r['name']} {r['description']}".lower()]
        )
        self._fill(rows)

    @on(DataTable.RowHighlighted)
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        row = next((r for r in self._rows if r["name"] == key), None)
        if row:
            params = ", ".join(row["params"]) or "-"
            self.query_one("#tool-detail", Static).update(
                f"[b]{escape(row['name'])}[/b]\n{escape(row['description'][:700])}\n[dim]params:[/dim] {escape(params)}"
            )

    def action_close(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Settings editor (schema driven)
# ---------------------------------------------------------------------------

_SECRET_HINTS = ("key", "token", "secret", "password", "passwd")


def _is_secret(path: tuple[str, ...]) -> bool:
    last = path[-1].lower() if path else ""
    return any(h in last for h in _SECRET_HINTS)


def _fmt_value(value: Any, path: tuple[str, ...]) -> str:
    if isinstance(value, str) and value and _is_secret(path):
        return "••••••" + value[-4:] if len(value) > 8 else "••••••"
    if isinstance(value, (dict, list)):
        return f"{type(value).__name__} ({len(value)})"
    return json.dumps(value, ensure_ascii=False)


class SettingsScreen(ModalScreen[bool]):
    """Browse and edit every key of ``config.json`` (schema validated on save)."""

    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical {
        width: 96%;
        height: 92%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    SettingsScreen .picker-title { text-style: bold; color: $primary; }
    SettingsScreen .picker-hint { color: $text-muted; }
    SettingsScreen Horizontal { height: 1fr; }
    SettingsScreen Tree { width: 1fr; height: 1fr; }
    SettingsScreen #editor { width: 48; height: 1fr; border-left: tall $panel; padding: 0 1; }
    SettingsScreen #editor .field-path { color: #8FBC8F; text-style: bold; }
    SettingsScreen #editor .field-help { color: $text-muted; margin: 0 0 1 0; height: auto; }
    SettingsScreen #editor Input { margin: 1 0; }
    SettingsScreen #editor Button { margin: 0 1 0 0; }
    SettingsScreen #status { height: 1; color: $text-muted; }
    SettingsScreen #actions { height: auto; margin: 1 0 0 0; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(
        self,
        load: Callable[[], dict[str, Any]],
        save: Callable[[dict[str, Any]], str | None],
        *,
        path_label: str,
        root: tuple[str, ...] = (),
        title: str = "Settings",
    ) -> None:
        super().__init__()
        self._load = load
        self._save = save
        self._path_label = path_label
        self._root = root
        self._title = title
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._selected: tuple[str, ...] | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            scope = f"  [b]{escape('.'.join(self._root))}[/b]" if self._root else ""
            yield Static(
                f"{escape(self._title)}{scope}  [dim]{escape(self._path_label)}[/dim]",
                classes="picker-title",
                markup=True,
            )
            yield Static(
                "Every key of the Navin config. Select a value to edit it; booleans toggle with Enter. "
                "Ctrl+S saves (validated by the schema). Most changes need a restart of navin-cli.",
                classes="picker-hint",
            )
            with Horizontal():
                yield Tree("config", id="tree")
                with Vertical(id="editor"):
                    yield Static("Select a setting", classes="field-path")
                    yield Static("", classes="field-help")
                    yield Input(placeholder="value (JSON for lists/objects)", id="value")
                    with Horizontal():
                        yield Button("Apply", variant="primary", id="apply")
                        yield Button("Reset", id="reset")
            yield Static("", id="status")
            with Horizontal(id="actions"):
                yield Button("Save  ctrl+s", variant="success", id="save")
                yield Button("Reload", id="reload")
                yield Button("Close  esc", id="close")

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        try:
            self._data = self._load()
        except Exception as exc:  # noqa: BLE001
            self._data = {}
            self._status(f"[$error]failed to load config: {escape(str(exc))}[/]")
        self._dirty = False
        tree = self.query_one("#tree", Tree)
        tree.clear()
        tree.root.data = ()
        subtree: Any = self._data
        try:
            subtree = self._get(self._root) if self._root else self._data
        except (KeyError, IndexError, ValueError, TypeError):
            subtree = {}
        tree.root.set_label(".".join(self._root) or "config")
        self._populate(tree.root, subtree, self._root)
        tree.root.expand()
        if self._root:
            for child in tree.root.children:
                child.expand()
        tree.focus()

    def _populate(self, node: TreeNode, value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key in sorted(value):
                child = value[key]
                sub = path + (str(key),)
                if isinstance(child, dict) and child:
                    branch = node.add(f"[b]{escape(str(key))}[/b]", data=sub)
                    self._populate(branch, child, sub)
                else:
                    node.add_leaf(
                        f"{escape(str(key))}: [dim]{escape(_fmt_value(child, sub))}[/dim]", data=sub
                    )
        elif isinstance(value, list):
            for idx, child in enumerate(value):
                sub = path + (str(idx),)
                if isinstance(child, (dict, list)) and child:
                    branch = node.add(f"[b]{idx}[/b]", data=sub)
                    self._populate(branch, child, sub)
                else:
                    node.add_leaf(f"{idx}: [dim]{escape(_fmt_value(child, sub))}[/dim]", data=sub)

    def _get(self, path: tuple[str, ...]) -> Any:
        cur: Any = self._data
        for key in path:
            if isinstance(cur, list):
                cur = cur[int(key)]
            else:
                cur = cur[key]
        return cur

    def _set(self, path: tuple[str, ...], value: Any) -> None:
        cur: Any = self._data
        for key in path[:-1]:
            cur = cur[int(key)] if isinstance(cur, list) else cur[key]
        last = path[-1]
        if isinstance(cur, list):
            cur[int(last)] = value
        else:
            cur[last] = value

    @on(Tree.NodeSelected)
    def _node_selected(self, event: Tree.NodeSelected) -> None:
        path = event.node.data
        if not isinstance(path, tuple) or not path:
            return
        self._selected = path
        value = self._get(path)
        self.query_one(".field-path", Static).update(escape(".".join(path)))
        kind = type(value).__name__
        help_text = f"type: {kind}"
        if isinstance(value, bool):
            help_text += "  (Enter toggles)"
            # Toggle directly for booleans: quickest UX.
            self._set(path, not value)
            self._dirty = True
            event.node.set_label(f"{escape(path[-1])}: [dim]{json.dumps(not value)}[/dim]")
            self._status(f"{'.'.join(path)} = {json.dumps(not value)}  [dim](unsaved)[/dim]")
            value = not value
        self.query_one(".field-help", Static).update(help_text)
        inp = self.query_one("#value", Input)
        if isinstance(value, str):
            inp.value = value
        else:
            inp.value = json.dumps(value, ensure_ascii=False)
        if not isinstance(value, bool):
            inp.focus()

    @on(Button.Pressed, "#apply")
    @on(Input.Submitted, "#value")
    def _apply(self) -> None:
        if not self._selected:
            return
        raw = self.query_one("#value", Input).value
        current = self._get(self._selected)
        value: Any
        if isinstance(current, str):
            value = raw
        elif current is None:
            stripped = raw.strip()
            if stripped in {"", "null", "None"}:
                value = None
            else:
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    value = raw
        else:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._status(f"[$error]invalid JSON: {escape(str(exc))}[/]")
                return
        self._set(self._selected, value)
        self._dirty = True
        tree = self.query_one("#tree", Tree)
        node = tree.cursor_node
        if node is not None and node.data == self._selected:
            node.set_label(
                f"{escape(self._selected[-1])}: [dim]{escape(_fmt_value(value, self._selected))}[/dim]"
            )
        self._status(f"{'.'.join(self._selected)} updated  [dim](unsaved, ctrl+s to save)[/dim]")

    @on(Button.Pressed, "#reset")
    def _reset(self) -> None:
        if self._selected:
            value = self._get(self._selected)
            inp = self.query_one("#value", Input)
            inp.value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        error = self._save(self._data)
        if error:
            self._status(f"[$error]{escape(error)}[/]")
            return
        self._dirty = False
        self._status("[$success]saved[/]  [dim]restart navin-cli to apply runtime changes[/dim]")

    @on(Button.Pressed, "#reload")
    def _reload_pressed(self) -> None:
        self._reload()
        self._status("reloaded from disk")

    @on(Button.Pressed, "#close")
    def action_close(self) -> None:
        self.dismiss(self._dirty)

    def _status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)


# ---------------------------------------------------------------------------
# Help / status
# ---------------------------------------------------------------------------


class MarkdownScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    MarkdownScreen { align: center middle; }
    MarkdownScreen > VerticalScroll {
        width: 90%;
        height: 88%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close", show=False)]

    def __init__(self, markdown: str) -> None:
        super().__init__()
        self._markdown = markdown

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Markdown(self._markdown)

    def action_close(self) -> None:
        self.dismiss(None)


HELP_MARKDOWN = """
# navin-cli

Full-screen terminal client for the Navin agent. Same engine, tools, skills,
memory, MCP servers and slash commands as Navin Desktop.

## Keys

| Key | Action |
| --- | --- |
| `Enter` | Send message |
| `Shift+Enter` / `Ctrl+J` | New line |
| `Esc` | Stop the running turn (shown as ``esc to interrupt`` while Working) |
| `Ctrl+P` | Command palette (all actions + slash commands) |
| `Ctrl+N` | New chat (`/new`) |
| `Ctrl+I` | Provider settings |
| `Ctrl+O` | Model list |
| `Ctrl+T` | Mode list (default: agent) |
| `Ctrl+S` | Sessions (F2 renames, type to search) |
| `Ctrl+W` | Workspace / project folder |
| `Ctrl+D` | Account (navin.live) |
| `Ctrl+B` | Show / hide the right panel |
| `Ctrl+G` | Settings |
| `F2` / `F3` / `F4` | Graph / Evolve / AGI |
| `Ctrl+R` | Toggle reasoning visibility |
| `Ctrl+L` | Clear the screen (again to reload this chat) |
| `Up` / `Down` | Prompt history (when the composer is empty) |
| `F1` | This help |
| `Ctrl+C` / `Cmd+C` | Copy the selection (does not quit) |
| `Ctrl+Shift+C` / `Cmd+Shift+C` | Copy the last message (up to 5000 lines, including tool output) |
| `Ctrl+V` / `Cmd+V` | Paste |
| `Ctrl+A` | Select all in the prompt |
| `Ctrl+F` | Find in the conversation (Enter next, Esc close) |
| `PageUp` / `PageDown` / `Ctrl+Up` / `Ctrl+Down` | Scroll the conversation |
| Zoom | Terminal zoom: `Ctrl++` / `Ctrl+-` (Windows Terminal / the host) |
| `Ctrl+Q` | Quit |

## Panel (Ctrl+B)

The right panel is the control board: every row is a shortcut you can click.
Provider, Model, Mode, Session and Workspace open their list; Settings, Commands, New chat, Graph,
Evolve and Help run directly; the Hide panel button (or Ctrl+B) closes the panel. While it is
hidden, mode and model stay on the last row of the chat field; shortcuts stay in this panel.

## Modes

`agent` is the default: `/forge`, build and verify end to end. `chat` sends
your text as is. `ask`, `plan`, `review`, `security` and `debug` prefix it
with `/ask`, `/blueprint`, `/inspect`, `/fortify`, `/debug` exactly like the
desktop composer. `/mode <name>` switches without opening the list.

## Slash commands

While a turn is running, the chat shows ``Working (elapsed • esc to interrupt)``.
Background ``exec`` sessions add ``/ps to view``. Esc (or ``/stop``) cancels the turn.

Type `/` to get inline completion. Every builtin command of Navin is available:
`/new`, `/stop` (or Esc), `/ps` (background terminals), `/restart`, `/status`, `/update`, `/title`, `/model`, `/history`, `/goal`,
`/trigger`, `/skill`, `/pack`, `/checkpoint`, `/dream`, `/dream-log`,
`/board`, `/pilot`, `/pairing`, workflows (`/forge`, `/blueprint`, `/cruise`,
`/inspect`, `/fortify`, `/debug`, `/ask`, `/ops`, `/pulse`, ...) and more.
`/help` prints the engine help.

## Settings (Ctrl+G)

The desktop settings page, section by section: Providers (Enter configures,
`t` tests the connection, `d` disconnects), Models (Enter edits, `u` makes
it the default, `a` adds, `d` deletes, `r` task routing), Tools & MCP (`i`
enables a preset, `c` adds a custom server, `r` removes), Skills (Enter
toggles, `i` installs from git / npm / a folder), Image, Video, Voice, Web,
System (time zone, machine resources: minimum 4 CPU / 8 GB RAM), Security,
Guardrails (board autonomy of the current project), Git (global switches,
forge tokens), Browser, Rules (`.navin/rules/*.md`, `n` creates one),
Account (navin.live) and About. Enter toggles a switch, cycles auto / on /
off, opens a picker or edits inline; `Ctrl+S` saves through the config
schema. Every key of `config.json` stays reachable from About > Advanced.

Ctrl+P > "Configure" still opens the remaining hubs: Tools, Agent loop,
Memory, Automation & tasks, Packs.

## Graph (F2) and Evolve (F3)

`F2` opens the project graph (same metagraph as the desktop): filter with
`/`, `k` cycles file kinds, `v` switches files / packages, `h` top hubs,
`i` impact (everything that depends on the file), `d` dependencies, `u`
used by, `a` back to all, `r` rebuild, `c` asks the agent about the file.

`F3` opens Evolve (navin-engine): `s`/`x` start or stop the daemon, `t`
autorun on commit, `1` proof, `2` diagnose, `3` optimize, `4` evolve,
`v` verify a promotion certificate, `m` merge, `p` publish a PR, `b`
rollback, `c` cancel a job, `d` docs. Both panels analyse the folder where
`navin-cli` was launched; change it with "Project folder" in the palette.

`F4` opens AGI (same panel as the desktop rail): skills evolution, world
model, policy, transfer protocol, episodic memory. Switches write the same
`.navin/*.json` files as `navin agi`. Locked stages wait for the exam below.
`r` refreshes. `1` runs skill jobs, `2` trains the world model, `3` trains
policy, `4`/`5`/`6`/`7` safety / kill drill / verify / campaign.

## Desktop sidebar equivalents

AGI = `F4`. Guardrails, Git, Browser, Install skill (Skills > `i`) and Rules are
sections of Settings (Ctrl+G). Terminal = the `exec` tool used by the agent
in the chat. Tasks = `/board`. Graph = `F2`. Evolve = `F3`.

## Approvals and questions

When a tool needs permission a card appears: `y` allow, `a` always, `n` deny.
When the agent asks a question, pick with digits or arrows, or type a custom
answer in the card input.

## Files

Preferences: `~/.navin/tui.json` (theme, mode, sidebar, prompt history).
Config: `~/.navin/config.json` (edited through Settings, schema validated).
"""
