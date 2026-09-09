# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Project graph (metagraph) for the terminal: the desktop "Graph" tab.

Reuses :mod:`navin.webui.metagraph` (same index, same native engine) and
renders the graph as a navigable table + a text neighbourhood view instead
of a force-directed canvas. Queries (hubs / impact / path / find) are the
same ones the desktop and the ``metagraph`` tool run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

KIND_COLORS: dict[str, str] = {
    "front": "#E8E8E8",
    "back": "#C8C8C8",
    "sql": "#A8A8A8",
    "config": "#888888",
    "test": "#707070",
    "docs": "#585858",
    "asset": "#404040",
    "other": "#2C2C2C",
}
KIND_ORDER = ("front", "back", "sql", "config", "test", "docs", "asset", "other")


def kind_badge(kind: str) -> str:
    color = KIND_COLORS.get(kind, KIND_COLORS["other"])
    return f"[{color}]●[/{color}] {kind}"


def _squeeze(path: str, width: int) -> str:
    """Keep the file name readable when the path is too long: 'a/b/…/x.py'."""
    if len(path) <= width:
        return path
    head, _, tail = path.partition("/")
    keep = width - len(head) - 3
    return f"{head}/…{path[-keep:]}" if keep > 8 else "…" + path[-(width - 1):]


def _bar(counts: dict[str, int], width: int = 40) -> str:
    total = sum(counts.values()) or 1
    out = []
    for kind in KIND_ORDER:
        n = counts.get(kind, 0)
        if not n:
            continue
        cells = max(1, round(width * n / total))
        color = KIND_COLORS.get(kind, KIND_COLORS["other"])
        out.append(f"[{color}]{'█' * cells}[/{color}]")
    return "".join(out)


class GraphModel:
    """Loaded graph + adjacency indexes, independent from the widgets."""

    def __init__(self, root: Path, view: str = "files") -> None:
        self.root = root
        self.view = view
        self.payload: dict[str, Any] = {}
        self.nodes: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.out_edges: dict[str, list[str]] = {}
        self.in_edges: dict[str, list[str]] = {}
        self.error: str = ""

    def load(self) -> None:
        from navin.webui.metagraph import build_metagraph

        try:
            self.payload = build_metagraph(self.root, view=self.view, layout=False)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self.payload = {}
        self.nodes = list(self.payload.get("nodes") or [])
        self.by_id = {n["id"]: n for n in self.nodes}
        self.out_edges = {}
        self.in_edges = {}
        for edge in self.payload.get("edges") or []:
            src, dst = edge.get("source"), edge.get("target")
            if not src or not dst:
                continue
            self.out_edges.setdefault(src, []).append(dst)
            self.in_edges.setdefault(dst, []).append(src)
        for lst in (*self.out_edges.values(), *self.in_edges.values()):
            lst.sort()

    def query(self, op: str, **args: Any) -> dict[str, Any]:
        from navin.webui.metagraph import query_metagraph

        try:
            return query_metagraph(self.root, op, view=self.view, args=args)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @staticmethod
    def degree(node: dict[str, Any]) -> int:
        return int(node.get("in_degree") or 0) + int(node.get("out_degree") or 0)

    def filtered(self, text: str, kind: str | None, *, only_ids: set[str] | None = None) -> list[dict[str, Any]]:
        text = text.strip().lower()
        rows = []
        for node in self.nodes:
            if kind and node.get("kind") != kind:
                continue
            if only_ids is not None and node["id"] not in only_ids:
                continue
            if text:
                hay = node["id"].lower() + " " + str(node.get("role") or "").lower()
                if text not in hay:
                    continue
            rows.append(node)
        rows.sort(key=lambda n: (-self.degree(n), n["id"]))
        return rows


class GraphScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    GraphScreen { align: center middle; }
    GraphScreen > Vertical {
        width: 98%;
        height: 94%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    GraphScreen .g-title { text-style: bold; color: $primary; height: 1; }
    GraphScreen .g-summary { color: $text-muted; height: auto; margin: 0 0 1 0; }
    GraphScreen #g-filter { height: 3; }
    GraphScreen #g-body { height: 1fr; }
    GraphScreen DataTable { width: 3fr; height: 1fr; }
    GraphScreen #g-detail { width: 2fr; height: 1fr; border-left: tall $panel; padding: 0 1; }
    GraphScreen #g-actions { height: auto; margin: 1 0 0 0; }
    GraphScreen #g-actions Button { margin: 0 1 0 0; }
    GraphScreen #g-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("k", "cycle_kind", "Kind filter", show=False),
        Binding("v", "toggle_view", "Files / packages", show=False),
        Binding("h", "hubs", "Hubs", show=False),
        Binding("i", "impact", "Impact", show=False),
        Binding("d", "deps", "Dependencies", show=False),
        Binding("u", "used_by", "Used by", show=False),
        Binding("a", "all_nodes", "All", show=False),
        Binding("r", "rebuild", "Rebuild", show=False),
        Binding("c", "ask_agent", "Ask agent", show=False),
        Binding("slash", "focus_filter", "Filter", show=False),
    ]

    def __init__(self, root: Path, *, on_ask: Any = None) -> None:
        super().__init__()
        self.model = GraphModel(root)
        self._kind: str | None = None
        self._only: set[str] | None = None
        self._only_label = ""
        self._rows: list[dict[str, Any]] = []
        self._on_ask = on_ask
        self._loading = True

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", classes="g-title", markup=True, id="g-title")
            yield Static("Building the graph from the code index...", classes="g-summary", markup=True, id="g-summary")
            yield Input(placeholder="/ filter by path or role  ·  k kind  ·  v files/packages  ·  h hubs  ·  i impact  ·  d deps  ·  u used by  ·  a all", id="g-filter")
            with Horizontal(id="g-body"):
                table = DataTable(zebra_stripes=True, cursor_type="row")
                table.add_column("Kind", width=9)
                table.add_column("File", width=54)
                table.add_column("In", width=4)
                table.add_column("Out", width=4)
                table.add_column("Role", width=48)
                yield table
                yield VerticalScroll(Static("", id="g-detail-text", markup=True), id="g-detail")
            with Horizontal(id="g-actions"):
                yield Button("Hubs  [h]", id="act-h", compact=True)
                yield Button("Impact  [i]", id="act-i", compact=True)
                yield Button("Deps  [d]", id="act-d", compact=True)
                yield Button("Used by  [u]", id="act-u", compact=True)
                yield Button("All  [a]", id="act-a", compact=True)
                yield Button("Kind  [k]", id="act-k", compact=True)
                yield Button("View  [v]", id="act-v", compact=True)
                yield Button("Rebuild  [r]", id="act-r", compact=True)
                yield Button("Ask agent  [c]", id="act-c", compact=True)
                yield Button("Close  [esc]", id="act-close", compact=True)
            yield Static("", id="g-status", markup=True)

    async def on_mount(self) -> None:
        self.query_one("#g-title", Static).update(f"◈ Graph  [dim]{escape(str(self.model.root))}[/dim]")
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        self._loading = True
        await asyncio.to_thread(self.model.load)
        self._loading = False
        if self.model.error:
            self.query_one("#g-summary", Static).update(f"[$error]{escape(self.model.error)}[/]")
            return
        self._render_summary()
        self._fill()
        self.query_one(DataTable).focus()

    def _render_summary(self) -> None:
        p = self.model.payload
        kinds = {str(k): int(v) for k, v in (p.get("kinds") or {}).items()}
        chips = "  ".join(f"{kind_badge(k)} {kinds[k]}" for k in KIND_ORDER if kinds.get(k))
        meta = (
            f"{p.get('total_files', 0)} files · {len(p.get('edges') or [])} edges · {p.get('symbols', 0)} symbols"
            f" · roles: {p.get('annotated', 0)} ({p.get('annotated_manual', 0)} manual, {p.get('annotated_stale', 0)} stale)"
            f" · engine {p.get('engine', '?')} · view {p.get('view', 'files')}"
            + ("  [$warning]truncated[/]" if p.get("truncated") else "")
        )
        self.query_one("#g-summary", Static).update(f"{_bar(kinds)}\n{chips}\n[dim]{escape(meta)}[/dim]")

    def _fill(self, keep: str | None = None) -> None:
        text = self.query_one("#g-filter", Input).value
        self._rows = self.model.filtered(text, self._kind, only_ids=self._only)
        table = self.query_one(DataTable)
        table.clear()
        for node in self._rows[:2000]:
            role = str(node.get("role") or "")
            if node.get("role_stale"):
                role = "⚠ " + role
            table.add_row(
                Text.from_markup(kind_badge(str(node.get("kind") or "other"))),
                Text(_squeeze(node["id"], 54)),
                Text(str(node.get("in_degree") or 0), justify="right"),
                Text(str(node.get("out_degree") or 0), justify="right"),
                Text(role[:48], style="dim"),
                key=node["id"],
            )
        scope = f" · {self._only_label}" if self._only_label else ""
        kind = f" · kind={self._kind}" if self._kind else ""
        more = " (showing 2000)" if len(self._rows) > 2000 else ""
        self.status(f"{len(self._rows)} nodes{more}{kind}{scope}")
        if not self._rows:
            self.query_one("#g-detail-text", Static).update("[dim]No node matches.[/dim]")
            return
        target = 0
        if keep:
            target = next((i for i, n in enumerate(self._rows) if n["id"] == keep), 0)
        table.move_cursor(row=target)
        self._show_node(self._rows[target]["id"])

    # -- detail --------------------------------------------------------------

    def current_id(self) -> str | None:
        table = self.query_one(DataTable)
        if not self._rows or table.cursor_row is None:
            return None
        try:
            return str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value)
        except Exception:  # noqa: BLE001
            return None

    def _show_node(self, node_id: str) -> None:
        node = self.model.by_id.get(node_id)
        if node is None:
            return
        deps = self.model.out_edges.get(node_id, [])
        users = self.model.in_edges.get(node_id, [])
        lines = [f"[b]{escape(node_id)}[/b]", f"{kind_badge(str(node.get('kind') or 'other'))}  ·  {node.get('size', 0)} bytes  ·  {node.get('symbols', 0)} symbols"]
        role = node.get("role")
        if role:
            src = node.get("role_source") or "auto"
            stale = "  [$warning]⚠ stale role[/]" if node.get("role_stale") else ""
            lines.append(f"[dim]{escape(str(role))}[/dim]  [dim]({src}){stale}[/dim]")
        lines.append("")
        lines.append(f"[b]▲ used by[/b] ({len(users)})")
        for u in users[:25]:
            lines.append(f"  [dim]│[/dim] {escape(u)}")
        if len(users) > 25:
            lines.append(f"  [dim]│ ... {len(users) - 25} more[/dim]")
        lines.append(f"[b]▼ depends on[/b] ({len(deps)})")
        for d in deps[:25]:
            lines.append(f"  [dim]│[/dim] {escape(d)}")
        if len(deps) > 25:
            lines.append(f"  [dim]│ ... {len(deps) - 25} more[/dim]")
        self.query_one("#g-detail-text", Static).update("\n".join(lines))

    @on(DataTable.RowHighlighted)
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self._show_node(str(event.row_key.value))

    @on(DataTable.RowSelected)
    def _selected(self) -> None:
        self.action_deps()

    @on(Input.Changed, "#g-filter")
    def _filter_changed(self) -> None:
        if not self._loading:
            self._fill()

    @on(Input.Submitted, "#g-filter")
    def _filter_submitted(self) -> None:
        self.query_one(DataTable).focus()

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        bid = (event.button.id or "").removeprefix("act-")
        mapping = {
            "h": self.action_hubs,
            "i": self.action_impact,
            "d": self.action_deps,
            "u": self.action_used_by,
            "a": self.action_all_nodes,
            "k": self.action_cycle_kind,
            "v": self.action_toggle_view,
            "r": self.action_rebuild,
            "c": self.action_ask_agent,
            "close": self.action_close,
        }
        handler = mapping.get(bid)
        if handler:
            handler()

    def status(self, text: str) -> None:
        self.query_one("#g-status", Static).update(text)

    # -- actions -------------------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(None)

    def action_focus_filter(self) -> None:
        self.query_one("#g-filter", Input).focus()

    def action_cycle_kind(self) -> None:
        order: list[str | None] = [None, *KIND_ORDER]
        idx = order.index(self._kind) if self._kind in order else 0
        self._kind = order[(idx + 1) % len(order)]
        self._fill()

    def _reset_filters(self, *, text: bool = True, kind: bool = True, scope: bool = True) -> None:
        if scope:
            self._only, self._only_label = None, ""
        if kind:
            self._kind = None
        if text:
            self._loading = True  # suspend Input.Changed -> _fill
            self.query_one("#g-filter", Input).value = ""
            self._loading = False

    def action_toggle_view(self) -> None:
        self.model.view = "packages" if self.model.view == "files" else "files"
        self._reset_filters()
        self.query_one("#g-summary", Static).update(f"Building {self.model.view} view...")
        self.run_worker(self._load(), exclusive=True)

    def action_rebuild(self) -> None:
        self.query_one("#g-summary", Static).update("Rebuilding from the code index...")
        self.run_worker(self._load(), exclusive=True)

    def action_all_nodes(self) -> None:
        self._reset_filters()
        self._fill()

    def action_hubs(self) -> None:
        result = self.model.query("hubs", limit=30)
        ids = [h["id"] for h in result.get("hubs") or []]
        self._reset_filters(kind=False)
        self._only, self._only_label = set(ids), "top hubs"
        self._fill()

    def action_impact(self) -> None:
        node_id = self.current_id()
        if not node_id:
            return
        result = self.model.query("impact", path=node_id)
        deps = list(result.get("dependents") or [])
        if not deps:
            self.status(f"nothing depends on {node_id} (no impact)")
            return
        self._reset_filters()
        self._only, self._only_label = set(deps) | {node_id}, f"impact of {node_id} ({len(deps)} dependents)"
        self._fill(keep=node_id)

    def action_deps(self) -> None:
        node_id = self.current_id()
        if not node_id:
            return
        deps = self.model.out_edges.get(node_id, [])
        if not deps:
            self.status(f"{node_id} has no dependencies")
            return
        self._reset_filters()
        self._only, self._only_label = set(deps) | {node_id}, f"dependencies of {node_id}"
        self._fill(keep=node_id)

    def action_used_by(self) -> None:
        node_id = self.current_id()
        if not node_id:
            return
        users = self.model.in_edges.get(node_id, [])
        if not users:
            self.status(f"nothing depends on {node_id}")
            return
        self._reset_filters()
        self._only, self._only_label = set(users) | {node_id}, f"files using {node_id}"
        self._fill(keep=node_id)

    def action_ask_agent(self) -> None:
        node_id = self.current_id()
        if not node_id or self._on_ask is None:
            return
        self.dismiss(None)
        self._on_ask(f"/ask Using the metagraph tool on {self.model.root}, explain the role of `{node_id}`, what depends on it and the risk of changing it.")
