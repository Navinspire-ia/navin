# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Evolve workbench for the terminal: the desktop "Evolve" tab.

Reads the same artefacts (``<project>/.navin/{proofs,diagnoses,optimize,
evolve-runs,fixes,promotions}``) and talks to the same navin-engine daemon
through :mod:`navin.webui.evolve_api`. Every blocking call (daemon start,
engine CLI, campaign submission) runs in a thread so the UI never freezes.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from pathlib import Path
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from navin.tui.screens import FormField, FormScreen

VERDICT_STYLE = {"pass": "$success", "weak": "$warning", "fail": "$error"}
SEVERITY_STYLE = {
    "critical": "$error",
    "high": "$error",
    "medium": "$warning",
    "low": "$primary",
    "info": "dim",
}


def _when(raw: Any) -> str:
    text = str(raw or "")
    try:
        if text.startswith("epoch:"):
            ts = float(text[6:])
        else:
            ts = float(text)
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return text[:16]


def _verdict(v: Any) -> str:
    v = str(v or "?")
    color = VERDICT_STYLE.get(v, "dim")
    return f"[{color}]{v}[/{color}]"


def _short(commit: Any) -> str:
    return str(commit or "")[:8]


class EvolveScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    EvolveScreen { align: center middle; }
    EvolveScreen > Vertical {
        width: 98%;
        height: 94%;
        border: round $primary;
        background: $surface;
        padding: 0 1;
    }
    EvolveScreen .e-title { text-style: bold; color: $primary; height: 1; }
    EvolveScreen #e-daemon { height: auto; margin: 0 0 1 0; }
    EvolveScreen #e-body { height: 1fr; }
    EvolveScreen DataTable { width: 3fr; height: 1fr; }
    EvolveScreen #e-detail { width: 2fr; height: 1fr; border-left: tall $panel; padding: 0 1; }
    EvolveScreen #e-actions, EvolveScreen #e-actions2 { height: auto; margin: 1 0 0 0; }
    EvolveScreen #e-actions2 { margin: 0; }
    EvolveScreen #e-actions Button, EvolveScreen #e-actions2 Button { margin: 0 1 0 0; }
    EvolveScreen .e-group { width: 11; color: $text-muted; padding: 0 1 0 0; content-align: right middle; }
    EvolveScreen #e-status { height: 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("s", "start", "Start daemon", show=False),
        Binding("x", "stop", "Stop daemon", show=False),
        Binding("t", "autorun", "Toggle autorun", show=False),
        Binding("1", "campaign('proof.run')", "Proof", show=False),
        Binding("2", "campaign('diagnose.run')", "Diagnose", show=False),
        Binding("3", "campaign('optimize.run')", "Optimize", show=False),
        Binding("4", "campaign('evolve.run')", "Evolve", show=False),
        Binding("v", "verify", "Verify cert", show=False),
        Binding("m", "merge", "Merge", show=False),
        Binding("p", "publish", "Publish PR", show=False),
        Binding("b", "rollback", "Rollback", show=False),
        Binding("c", "cancel", "Cancel job", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("d", "docs", "Docs", show=False),
    ]

    def __init__(self, root: Path, *, on_ask: Any = None, on_markdown: Any = None) -> None:
        super().__init__()
        self.root = root
        self.overview: dict[str, Any] = {}
        self._items: list[tuple[str, dict[str, Any]]] = []  # (kind, artefact)
        self._busy = False
        self._on_ask = on_ask
        self._on_markdown = on_markdown

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"⚗ Evolve  [dim]{escape(str(self.root))}[/dim]", classes="e-title", markup=True)
            yield Static("Loading...", id="e-daemon", markup=True)
            with Horizontal(id="e-body"):
                table = DataTable(zebra_stripes=True, cursor_type="row")
                table.add_column("Kind", width=10)
                table.add_column("When", width=16)
                table.add_column("Commit", width=8)
                table.add_column("Verdict / score", width=16)
                table.add_column("Summary", width=70)
                yield table
                yield VerticalScroll(Static("", id="e-detail-text", markup=True), id="e-detail")
            with Horizontal(id="e-actions"):
                yield Static("[b]daemon[/b]", classes="e-group", markup=True)
                yield Button("Start  [s]", id="act-s", compact=True)
                yield Button("Stop  [x]", id="act-x", compact=True)
                yield Button("Autorun  [t]", id="act-t", compact=True)
                yield Static("[b]campaigns[/b]", classes="e-group", markup=True)
                yield Button("Proof  [1]", id="act-1", compact=True)
                yield Button("Diagnose  [2]", id="act-2", compact=True)
                yield Button("Optimize  [3]", id="act-3", compact=True)
                yield Button("Evolve  [4]", id="act-4", compact=True)
                yield Button("Cancel job  [c]", id="act-c", compact=True)
            with Horizontal(id="e-actions2"):
                yield Static("[b]promotion[/b]", classes="e-group", markup=True)
                yield Button("Verify cert  [v]", id="act-v", compact=True)
                yield Button("Merge  [m]", id="act-m", compact=True)
                yield Button("Publish PR  [p]", id="act-p", compact=True)
                yield Button("Rollback  [b]", id="act-b", compact=True)
                yield Static("", classes="e-group")
                yield Button("Refresh  [r]", id="act-r", compact=True)
                yield Button("Docs  [d]", id="act-d", compact=True)
                yield Button("Close  [esc]", id="act-close", compact=True)
            yield Static("", id="e-status", markup=True)

    async def on_mount(self) -> None:
        self.run_worker(self._refresh(), exclusive=True)

    # -- data ----------------------------------------------------------------

    async def _refresh(self, keep: str | None = None) -> None:
        from navin.webui.evolve_api import EvolveApiError, overview

        try:
            self.overview = await asyncio.to_thread(overview, str(self.root))
        except EvolveApiError as exc:
            self.query_one("#e-daemon", Static).update(f"[$error]{escape(exc.message)}[/]")
            return
        except Exception as exc:  # noqa: BLE001
            self.query_one("#e-daemon", Static).update(f"[$error]{escape(str(exc))}[/]")
            return
        self._render_daemon()
        self._fill(keep)
        self.query_one(DataTable).focus()

    def _render_daemon(self) -> None:
        o = self.overview
        daemon = o.get("daemon") or {}
        if daemon.get("online"):
            st = daemon.get("status") or {}
            jobs = st.get("jobs") or []
            running = [j for j in jobs if str(j.get("state")) in {"running", "queued"}]
            uptime = int(st.get("uptime_secs") or 0)
            line1 = f"[$success]● daemon online[/]  {escape(str(st.get('engine') or ''))}  up {uptime // 60}m{uptime % 60:02d}s  ·  {len(running)} active job(s), {len(jobs)} total"
            if jobs:
                line1 += "\n  " + "  ".join(
                    f"[dim]#{j.get('id')}[/dim] {escape(str(j.get('kind')))} [{'yellow' if j.get('state') in ('running', 'queued') else 'dim'}]{escape(str(j.get('state')))}[/]"
                    for j in jobs[-6:]
                )
        else:
            reason = daemon.get("reason") or "not_running"
            hint = "press [b]s[/b] to start it" if reason == "not_running" else "endpoint answered badly, try [b]x[/b] then [b]s[/b]"
            line1 = f"[$warning]○ daemon offline[/] ({escape(str(reason))})  ·  {hint}"
        engine_bin = o.get("engine_bin") or "[$error]navin-engine binary not found[/]"
        autorun = o.get("autorun") or {}
        auto = f"[$success]on[/] ({escape(str(autorun.get('kind') or 'proof.run'))})" if autorun.get("enabled") else "[dim]off[/dim]"
        hint = o.get("hint") or {}
        project = " · ".join(
            f"{k}: {escape(str(v))}" for k, v in (("start", hint.get("start")), ("test", hint.get("test")), ("url", hint.get("url"))) if v
        )
        preset = o.get("default_preset") or "-"
        line2 = f"[dim]engine {escape(str(engine_bin))}  ·  autorun on commit {auto}  ·  generator preset {escape(str(preset))}[/dim]"
        line3 = f"[dim]{project}[/dim]" if project else "[dim]no start/test command detected: campaigns will probe the project themselves[/dim]"
        self.query_one("#e-daemon", Static).update("\n".join([line1, line2, line3]))

    def _fill(self, keep: str | None = None) -> None:
        o = self.overview
        items: list[tuple[str, dict[str, Any]]] = []
        for kind, key in (
            ("promotion", "promotions"),
            ("proof", "proofs"),
            ("diagnosis", "diagnoses"),
            ("optimize", "optimize_runs"),
            ("evolve", "evolve_runs"),
            ("fix", "fix_reports"),
        ):
            for art in o.get(key) or []:
                if isinstance(art, dict):
                    items.append((kind, art))

        def sort_key(item: tuple[str, dict[str, Any]]) -> float:
            raw = str(item[1].get("collected_at") or item[1].get("issued_at") or item[1].get("created_at") or "")
            try:
                return float(raw[6:]) if raw.startswith("epoch:") else float(raw)
            except ValueError:
                return 0.0

        items.sort(key=sort_key, reverse=True)
        self._items = items
        table = self.query_one(DataTable)
        table.clear()
        for idx, (kind, art) in enumerate(items):
            table.add_row(*(Text.from_markup(c) for c in self._row_cells(kind, art)), key=str(idx))
        if not items:
            self.query_one("#e-detail-text", Static).update(
                "[dim]No artefact yet. Start the daemon ([b]s[/b]) then launch a proof ([b]1[/b]) to measure the robustness of the project, "
                "diagnose ([b]2[/b]) to turn weak checks into findings, optimize ([b]3[/b]) for performance variants or evolve ([b]4[/b]) to let the engine propose and prove fixes.[/dim]"
            )
            self.status("0 artefacts")
            return
        target = 0
        if keep:
            target = next((i for i, (_, a) in enumerate(items) if a.get("_file") == keep or a.get("id") == keep), 0)
        table.move_cursor(row=target)
        self._show(target)
        self.status(f"{len(items)} artefacts")

    def _row_cells(self, kind: str, art: dict[str, Any]) -> list[str]:
        when = _when(art.get("collected_at") or art.get("issued_at") or art.get("created_at"))
        commit = _short(art.get("commit") or art.get("commit_sha"))
        if kind == "proof":
            score = f"{_verdict(art.get('verdict'))} {art.get('robustness_score', '')}"
            faults = art.get("faults") or []
            summary = f"profile {art.get('profile', '')} · {len(faults)} faults · " + ", ".join(
                f"{f.get('fault')}={f.get('verdict')}" for f in faults[:4]
            )
        elif kind == "diagnosis":
            score = f"{_verdict(art.get('source_verdict'))} {art.get('robustness_score', '')}"
            summary = f"{len(art.get('findings') or [])} findings · {art.get('summary', '')}"
        elif kind == "optimize":
            winner = art.get("winner")
            gain = art.get("winner_gain_percent")
            score = f"[$success]+{gain}%[/]" if gain else "[dim]no winner[/dim]"
            summary = f"objective {art.get('objective', '')} · {len(art.get('variants') or [])} variants" + (f" · winner {winner}" if winner else "")
        elif kind == "evolve":
            score = f"{_verdict(art.get('verdict_before'))} {art.get('robustness_before', '')}"
            summary = f"{art.get('findings_addressed', 0)}/{art.get('findings_total', 0)} findings addressed · {art.get('generator', '')}"
        elif kind == "promotion":
            outcome = str(art.get("outcome") or "")
            color = {"merged": "green", "branch_only": "yellow", "blocked": "red"}.get(outcome, "dim")
            score = f"[{color}]{outcome}[/{color}]"
            cert = art.get("certificate") or {}
            summary = f"{art.get('finding', '')} · {art.get('candidate_id', '')}" + (
                f" · {cert.get('score_before')}→{cert.get('score_after')}" if cert else ""
            ) + (f" · {art.get('branch')}" if art.get("branch") else "")
            when = _when(cert.get("issued_at")) if cert and not art.get("collected_at") else when
        else:
            score = ""
            summary = str(art.get("summary") or art.get("title") or art.get("_file") or "")
        return [kind, when, commit, score, escape(str(summary))[:70]]

    # -- detail --------------------------------------------------------------

    def current(self) -> tuple[str, dict[str, Any]] | None:
        table = self.query_one(DataTable)
        if not self._items or table.cursor_row is None:
            return None
        try:
            idx = int(str(table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value))
        except Exception:  # noqa: BLE001
            return None
        return self._items[idx] if 0 <= idx < len(self._items) else None

    def _show(self, idx: int) -> None:
        if not (0 <= idx < len(self._items)):
            return
        kind, art = self._items[idx]
        lines: list[str] = [f"[b]{kind}[/b]  [dim]{escape(str(art.get('_file') or art.get('id') or ''))}[/dim]"]
        lines.append(f"[dim]commit {escape(_short(art.get('commit') or art.get('commit_sha')))} · {escape(_when(art.get('collected_at') or art.get('issued_at')))}[/dim]")
        lines.append("")
        if kind == "proof":
            lines.append(f"verdict {_verdict(art.get('verdict'))}  robustness [b]{art.get('robustness_score')}[/b]/100  profile {escape(str(art.get('profile')))}")
            for fault in art.get("faults") or []:
                lines.append(f"\n{_verdict(fault.get('verdict'))} [b]{escape(str(fault.get('fault')))}[/b]  [dim]{escape(str(fault.get('description') or ''))}[/dim]")
                for check in fault.get("checks") or []:
                    extra = ""
                    if check.get("measured") is not None:
                        extra = f"  [dim]{check.get('measured')} / {check.get('threshold')}[/dim]"
                    lines.append(f"   {_verdict(check.get('verdict'))} {escape(str(check.get('name')))}: {escape(str(check.get('detail') or ''))}{extra}")
                for ev in (fault.get("evidence") or [])[:3]:
                    lines.append(f"   [dim]· {escape(str(ev))[:160]}[/dim]")
        elif kind == "diagnosis":
            lines.append(f"source {_verdict(art.get('source_verdict'))}  score {art.get('robustness_score')}  ·  {escape(str(art.get('summary') or ''))}")
            for f in art.get("findings") or []:
                sev = str(f.get("severity") or "")
                color = SEVERITY_STYLE.get(sev, "dim")
                lines.append(f"\n[{color}]■ {escape(sev)}[/{color}] [b]{escape(str(f.get('title')))}[/b]  [dim]{escape(str(f.get('id')))} · {escape(str(f.get('confidence')))} confidence · {escape(str(f.get('family') or ''))}[/dim]")
                lines.append(f"   symptom: {escape(str(f.get('symptom') or ''))}")
                lines.append(f"   cause: {escape(str(f.get('root_cause') or ''))}")
                lines.append(f"   fix: {escape(str(f.get('remediation') or ''))}")
            for note in art.get("notes") or []:
                lines.append(f"[dim]note: {escape(str(note))}[/dim]")
        elif kind == "optimize":
            base = art.get("baseline") or {}
            lines.append(
                f"objective [b]{escape(str(art.get('objective')))}[/b]  baseline p95 {base.get('p95_ms')} ms · {base.get('rps')} rps · {base.get('requests')} req / {base.get('failures')} failures  ·  repeats {art.get('bench_repeats')} · invariants {art.get('invariants_checked')}"
            )
            if art.get("winner"):
                lines.append(f"[$success]winner {escape(str(art.get('winner')))} +{art.get('winner_gain_percent')}%[/]" + (f"  promotion {escape(str(art.get('promotion_id')))}" if art.get("promotion_id") else ""))
            for v in art.get("variants") or []:
                st = v.get("stats") or {}
                ok = "[$success]eligible[/]" if v.get("eligible") else "[dim]not eligible[/dim]"
                tests = "" if v.get("tests_passed") is None else ("  tests [$success]pass[/]" if v.get("tests_passed") else "  tests [$error]fail[/]")
                lines.append(f"\n[b]{escape(str(v.get('candidate_id')))}[/b]  gain {v.get('gain_percent')}%  {ok}{tests}")
                lines.append(f"   [dim]{escape(str(v.get('rationale') or ''))}[/dim]")
                if st:
                    lines.append(f"   p50 {st.get('p50_ms')} · p95 {st.get('p95_ms')} · p99 {st.get('p99_ms')} ms · {st.get('rps')} rps")
                if v.get("note"):
                    lines.append(f"   [dim]{escape(str(v.get('note')))}[/dim]")
            for note in art.get("notes") or []:
                lines.append(f"[dim]note: {escape(str(note))}[/dim]")
        elif kind == "evolve":
            lines.append(f"before {_verdict(art.get('verdict_before'))} {art.get('robustness_before')}  ·  {art.get('findings_addressed')}/{art.get('findings_total')} findings addressed  ·  generator {escape(str(art.get('generator') or ''))}")
            for out in art.get("outcomes") or []:
                acc = out.get("accepted")
                mark = "[$success]✓[/]" if acc else "[dim]○[/dim]"
                lines.append(f"\n{mark} [b]{escape(str(out.get('title') or out.get('finding')))}[/b]  [dim]{escape(str(out.get('finding')))}[/dim]")
                if acc:
                    lines.append(f"   accepted {escape(str(acc))}" + (f" · promotion {escape(str(out.get('promotion')))}" if out.get("promotion") else ""))
                for key in ("reason", "note", "error"):
                    if out.get(key):
                        lines.append(f"   [dim]{escape(str(out.get(key)))}[/dim]")
        elif kind == "promotion":
            outcome = str(art.get("outcome") or "")
            color = {"merged": "green", "branch_only": "yellow", "blocked": "red"}.get(outcome, "dim")
            lines.append(f"[{color}]{escape(outcome)}[/{color}]  mode {escape(str(art.get('mode') or ''))}  merged {art.get('merged')}  finding {escape(str(art.get('finding')))}  candidate {escape(str(art.get('candidate_id')))}")
            if art.get("branch"):
                lines.append(f"branch {escape(str(art.get('branch')))}" + (f" · {escape(_short(art.get('commit_sha')))}" if art.get("commit_sha") else ""))
            for r in art.get("reasons") or []:
                lines.append(f"  [dim]· {escape(str(r))}[/dim]")
            cert = art.get("certificate") or {}
            if cert:
                lines.append(f"\n[b]certificate[/b] {cert.get('score_before')} → {cert.get('score_after')}  family {escape(str(cert.get('family') or ''))}  issued {escape(_when(cert.get('issued_at')))}")
                lines.append(f"  [dim]checksum {escape(str(cert.get('checksum') or ''))[:24]}…  signature {escape(str(cert.get('signature') or ''))[:24]}…[/dim]")
                lines.append("  [dim]press [b]v[/b] to verify the Ed25519 signature with the engine[/dim]")
            if art.get("pull_request"):
                lines.append(f"pull request {escape(str(art.get('pull_request')))}")
            if art.get("rolled_back_at"):
                lines.append(f"[$warning]rolled back {escape(_when(art.get('rolled_back_at')))}[/]")
            diff = art.get("diff")
            if diff:
                lines.append("\n[b]diff[/b]")
                for raw in str(diff).splitlines()[:80]:
                    if raw.startswith("+") and not raw.startswith("+++"):
                        lines.append(f"[$success]{escape(raw)}[/]")
                    elif raw.startswith("-") and not raw.startswith("---"):
                        lines.append(f"[$error]{escape(raw)}[/]")
                    elif raw.startswith("@@"):
                        lines.append(f"[$primary]{escape(raw)}[/]")
                    else:
                        lines.append(f"[dim]{escape(raw)}[/dim]")
        else:
            for key, value in art.items():
                if key.startswith("_"):
                    continue
                lines.append(f"[b]{escape(str(key))}[/b]: {escape(str(value))[:200]}")
        self.query_one("#e-detail-text", Static).update("\n".join(lines))

    @on(DataTable.RowHighlighted)
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value is not None:
            try:
                self._show(int(str(event.row_key.value)))
            except ValueError:
                pass

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        bid = (event.button.id or "").removeprefix("act-")
        if bid == "close":
            self.action_close()
        elif bid in "1234":
            self.action_campaign({"1": "proof.run", "2": "diagnose.run", "3": "optimize.run", "4": "evolve.run"}[bid])
        else:
            handler = getattr(self, f"action_{ {'s': 'start', 'x': 'stop', 't': 'autorun', 'v': 'verify', 'm': 'merge', 'p': 'publish', 'b': 'rollback', 'c': 'cancel', 'r': 'refresh', 'd': 'docs'}.get(bid, '') }", None)
            if handler:
                result = handler()
                if asyncio.iscoroutine(result):
                    self.run_worker(result, exclusive=False)

    def status(self, text: str) -> None:
        self.query_one("#e-status", Static).update(text)

    # -- actions -------------------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(None)

    async def _run(self, label: str, fn: Any, *args: Any, keep: str | None = None) -> Any:
        """Run a blocking evolve_api call in a thread, report, refresh."""
        from navin.webui.evolve_api import EvolveApiError

        if self._busy:
            self.status("[$warning]another action is still running[/]")
            return None
        self._busy = True
        self.status(f"[$warning]⟳ {escape(label)}...[/]")
        try:
            result = await asyncio.to_thread(fn, *args)
        except EvolveApiError as exc:
            self.status(f"[$error]{escape(label)}: {escape(exc.message)}[/]")
            return None
        except Exception as exc:  # noqa: BLE001
            self.status(f"[$error]{escape(label)}: {escape(str(exc))}[/]")
            return None
        finally:
            self._busy = False
        await self._refresh(keep)
        return result

    async def action_refresh(self) -> None:
        cur = self.current()
        await self._refresh(cur[1].get("_file") if cur else None)

    async def action_start(self) -> None:
        from navin.webui.evolve_api import start_daemon

        result = await self._run("starting daemon (up to 25s)", start_daemon, str(self.root))
        if result is not None:
            self.status("[$success]daemon online[/]" if result.get("online") else "[$warning]daemon did not answer[/]")

    async def action_stop(self) -> None:
        from navin.webui.evolve_api import stop_daemon

        result = await self._run("stopping daemon", stop_daemon, str(self.root))
        if result is not None:
            self.status("[$success]daemon stopped[/]" if result.get("stopped") else "[dim]daemon was not running[/dim]")

    async def action_autorun(self) -> None:
        from navin.webui.evolve_api import set_autorun

        enabled = bool((self.overview.get("autorun") or {}).get("enabled"))
        result = await self._run("toggling autorun", set_autorun, str(self.root), not enabled)
        if result is not None:
            self.status(f"autorun on commit: {'[$success]on[/]' if result.get('enabled') else '[dim]off[/dim]'}")

    def action_campaign(self, kind: str) -> None:
        # push_screen_wait needs a worker; bindings call this synchronously.
        self.run_worker(self._campaign(kind), exclusive=False)

    async def _campaign(self, kind: str) -> None:
        from navin.webui.evolve_api import enqueue_campaign

        if not (self.overview.get("daemon") or {}).get("online"):
            self.status("[$warning]start the daemon first ([b]s[/b])[/]")
            return
        hint = self.overview.get("hint") or {}
        presets = list(self.overview.get("model_presets") or [])
        default_preset = str(self.overview.get("default_preset") or (presets[0] if presets else ""))
        fields = [
            FormField("start", "Start command", placeholder="auto-detected", secret=False, required=False, value=str(hint.get("start") or "")),
            FormField("url", "Probe URL", placeholder="auto-detected by booting the app", secret=False, required=False, value=str(hint.get("url") or "")),
            FormField("test", "Test command", placeholder="optional", secret=False, required=False, value=str(hint.get("test") or "")),
        ]
        if kind in {"proof.run", "diagnose.run", "evolve.run"}:
            fields.append(FormField("profile", "Profile (quick / standard / deep)", secret=False, required=False, value="quick"))
        if kind == "optimize.run":
            fields.append(FormField("objective", "Objective (p95 / throughput)", secret=False, required=False, value="p95"))
        if kind in {"optimize.run", "evolve.run"}:
            fields.append(FormField("preset", f"Model preset ({', '.join(presets[:6]) or 'none configured'})", secret=False, required=False, value=default_preset))
        fields.append(FormField("dirty", "Prove the working tree (dirty) instead of HEAD? yes/no", secret=False, required=False, value="no"))
        answer = await self.app.push_screen_wait(FormScreen(f"Launch {kind}", fields, hint="Empty fields are resolved by the engine from the project itself.", submit_label="Launch"))
        if not answer:
            return
        query = {k: [v] for k, v in answer.items() if v}
        result = await self._run(f"submitting {kind}", enqueue_campaign, str(self.root), kind, query)
        if result is not None:
            job = result.get("id") if isinstance(result, dict) else result
            self.status(f"[$success]{escape(kind)} queued as job #{escape(str(job))}[/]  ·  [dim]r to refresh, c to cancel[/dim]")

    def _promotion_id(self) -> str | None:
        cur = self.current()
        if cur is None or cur[0] != "promotion":
            self.status("[$warning]select a promotion row first[/]")
            return None
        return str(cur[1].get("id") or "")

    async def action_verify(self) -> None:
        from navin.webui.evolve_api import verify_certificate

        pid = self._promotion_id()
        if not pid:
            return
        result = await self._run("verifying certificate", verify_certificate, str(self.root), pid, keep=pid)
        if isinstance(result, dict):
            ok = result.get("authentic")
            self.status("[$success]certificate authentic (Ed25519 signature valid)[/]" if ok else f"[$error]certificate NOT authentic[/] {escape(str(result.get('reason') or ''))}")

    async def action_merge(self) -> None:
        from navin.webui.evolve_api import merge_promotion

        pid = self._promotion_id()
        if pid:
            result = await self._run("merging promotion (fast-forward, clean tree required)", merge_promotion, str(self.root), pid, keep=pid)
            if result is not None:
                self.status(f"[$success]merged {escape(pid)}[/]")

    async def action_publish(self) -> None:
        from navin.webui.evolve_api import publish_promotion

        pid = self._promotion_id()
        if pid:
            result = await self._run("pushing branch and opening a pull request", publish_promotion, str(self.root), pid, keep=pid)
            if isinstance(result, dict):
                self.status(f"[$success]{escape(str(result.get('pull_request') or result.get('url') or 'published'))}[/]")

    async def action_rollback(self) -> None:
        from navin.webui.evolve_api import rollback_promotion

        pid = self._promotion_id()
        if pid:
            result = await self._run("rolling back promotion", rollback_promotion, str(self.root), pid, keep=pid)
            if result is not None:
                self.status(f"[$success]rolled back {escape(pid)}[/]")

    def action_cancel(self) -> None:
        self.run_worker(self._cancel(), exclusive=False)

    async def _cancel(self) -> None:
        from navin.webui.evolve_api import cancel_job

        jobs = ((self.overview.get("daemon") or {}).get("status") or {}).get("jobs") or []
        active = [j for j in jobs if str(j.get("state")) in {"running", "queued"}]
        if not active:
            self.status("[dim]no active job to cancel[/dim]")
            return
        answer = await self.app.push_screen_wait(
            FormScreen("Cancel job", [FormField("id", f"Job id ({', '.join('#' + str(j.get('id')) + ' ' + str(j.get('kind')) for j in active)})", secret=False, value=str(active[-1].get("id")))], submit_label="Cancel job")
        )
        if not answer:
            return
        result = await self._run(f"cancelling job #{answer['id']}", cancel_job, str(self.root), answer["id"])
        if result is not None:
            self.status(f"[$success]job #{escape(answer['id'])} cancelled[/]")

    def action_docs(self) -> None:
        from navin.webui.evolve_api import EvolveApiError, docs

        if self._on_markdown is None:
            return
        try:
            payload = docs("en")
        except EvolveApiError as exc:
            self.status(f"[$error]{escape(exc.message)}[/]")
            return
        self._on_markdown("Evolve", str(payload.get("markdown") or ""))
