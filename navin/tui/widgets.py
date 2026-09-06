"""Transcript widgets: user/assistant messages, tool calls, cards, composer."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rich.markup import escape
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from navin.tui.brand import MARK, tide_text, wave_frame
from navin.tui.modes import display_user_text

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Single-width glyphs only: emoji are double-width in most terminals and break
# column alignment (and some fonts render them as tofu).
_TOOL_ICONS: dict[str, str] = {
    "read_file": "≡",
    "write_file": "✎",
    "edit_file": "✎",
    "apply_patch": "✎",
    "list_dir": "▤",
    "glob": "⌕",
    "grep": "⌕",
    "find_files": "⌕",
    "exec": "$",
    "shell": "$",
    "web_search": "◍",
    "web_fetch": "◍",
    "browser": "◍",
    "spawn": "⑂",
    "ask_user": "?",
    "message": "›",
    "cron": "◷",
    "memory": "◈",
    "todo": "☑",
    "board": "▣",
    "git": "⌥",
}


# Saturated inks on black: no gray, no near-white, no muddy teal.
_TOOL_COLORS: dict[str, str] = {
    "read_file": "#FF6B2C",
    "list_dir": "#FF6B2C",
    "grep": "#FF2E93",
    "glob": "#FF2E93",
    "find_files": "#FF2E93",
    "exec": "#FFB000",
    "shell": "#FFB000",
    "write_file": "#FF3B30",
    "edit_file": "#FF3B30",
    "apply_patch": "#FF3B30",
    "web_search": "#E040FB",
    "web_fetch": "#E040FB",
    "browser": "#E040FB",
    "board": "#7C4DFF",
    "todo": "#7C4DFF",
    "spawn": "#FF6D00",
    "git": "#00C853",
    "memory": "#536DFE",
    "cron": "#00B8FF",
    "ask_user": "#FF4081",
    "message": "#FF4081",
}


def tool_icon(name: str) -> str:
    base = name.lower()
    for key, icon in _TOOL_ICONS.items():
        if base == key or base.startswith(key):
            return icon
    if base.startswith("mcp") or "__" in base:
        return "⧉"
    return "•"


def tool_color(name: str) -> str:
    """Hex ink for a tool family (not the same blue for every call)."""
    base = (name or "").lower()
    for key, color in _TOOL_COLORS.items():
        if base == key or base.startswith(key):
            return color
    if base.startswith("mcp") or "__" in base:
        return "#FF7043"
    return "#FF8F1C"


def _markup_escape(text: str) -> str:
    """Escape Rich markup so ``]`` inside args cannot leak a closing tag."""
    return escape(text).replace("]", r"\]")


def summarize_arguments(arguments: dict[str, Any], limit: int = 90) -> str:
    """One-line preview of tool arguments, most useful key first."""
    if not arguments:
        return ""
    preferred = ("command", "cmd", "path", "file_path", "pattern", "query", "url", "task", "name")
    parts: list[str] = []
    seen: set[str] = set()
    for key in preferred:
        if key in arguments and arguments[key] not in (None, ""):
            parts.append(f"{key}={_short(arguments[key], 60)}")
            seen.add(key)
    for key, value in arguments.items():
        if key in seen or value in (None, "", [], {}):
            continue
        parts.append(f"{key}={_short(value, 30)}")
    text = "  ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_PATHISH_KEYS = frozenset(
    {
        "path",
        "file_path",
        "file",
        "filename",
        "dir",
        "directory",
        "target",
        "src",
        "dest",
    }
)
_CMD_KEYS = frozenset({"command", "cmd"})
_SEARCH_KEYS = frozenset({"pattern", "query"})
_URL_KEYS = frozenset({"url"})
_PATH_COLOR = "#0369FF"
_CMD_COLOR = "#FFB000"
_SEARCH_COLOR = "#FF2E93"
_URL_COLOR = "#E040FB"
_OP_COLOR = "#FF8F1C"


def _arg_ink(key: str, raw: str) -> str:
    if key in _PATHISH_KEYS or _looks_like_path(raw):
        return _PATH_COLOR
    if key in _CMD_KEYS:
        return _CMD_COLOR
    if key in _SEARCH_KEYS:
        return _SEARCH_COLOR
    if key in _URL_KEYS:
        return _URL_COLOR
    return _OP_COLOR


def _looks_like_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if "/" in text or "\\" in text:
        return True
    return text.endswith(
        (".py", ".ts", ".tsx", ".js", ".json", ".md", ".toml", ".yml", ".yaml", ".css")
    )


def tool_args_markup(arguments: dict[str, Any], limit: int = 90) -> str:
    """Bold, colored args so paths and ops stand out from chat prose."""
    if not arguments:
        return ""
    preferred = ("command", "cmd", "path", "file_path", "pattern", "query", "url", "task", "name")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in preferred:
        if key in arguments and arguments[key] not in (None, ""):
            pairs.append((key, _short(arguments[key], 60)))
            seen.add(key)
    for key, value in arguments.items():
        if key in seen or value in (None, "", [], {}):
            continue
        pairs.append((key, _short(value, 30)))
    bits: list[str] = []
    used = 0
    for key, raw in pairs:
        ink = _arg_ink(key, raw)
        piece = f"[$text-muted]{escape(key)}=[/][b {ink}]{_markup_escape(raw)}[/]"
        extra = 2 if bits else 0
        if used + extra + len(key) + 1 + len(raw) > limit and bits:
            break
        bits.append(piece)
        used += extra + len(key) + 1 + len(raw)
    return "  ".join(bits)


def _short(value: Any, limit: int) -> str:
    if isinstance(value, str):
        text = value.replace("\n", "⏎")
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_result(result: Any, limit: int = 4000) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            text = str(result)
    if len(text) > limit:
        text = text[:limit] + f"\n… ({len(text) - limit} more chars)"
    return text


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TideRule(Static):
    """The Navin tide line: a hairline fading from charcoal to light gray."""

    DEFAULT_CSS = """
    TideRule {
        height: 1;
        width: 1fr;
    }
    """

    def __init__(self, char: str = "─", **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._char = char

    def on_mount(self) -> None:
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _draw(self) -> None:
        width = self.size.width or self.content_size.width
        if width > 0:
            self.update(tide_text(width, self._char))


class UserMessage(Vertical):
    """A user turn: left rail and a quiet label, not a framed card."""

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    UserMessage {
        height: auto;
        margin: 1 2 0 2;
        padding: 0 1 0 1;
        border-left: tall $secondary;
        background: $background;
    }
    UserMessage > .user-head {
        height: 1;
        color: $text-muted;
        text-style: bold;
        background: $background;
    }
    UserMessage > .user-body {
        color: $foreground;
        padding: 0;
        background: $background;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self.raw_text = display_user_text(text)

    def compose(self) -> ComposeResult:
        yield Static("you", classes="user-head")
        yield Static(self.raw_text, classes="user-body", markup=False)


class ReasoningBlock(Vertical):
    """Collapsible reasoning stream (model thinking)."""

    DEFAULT_CSS = """
    ReasoningBlock {
        height: auto;
        margin: 0 2 0 2;
        padding: 0 2;
        border-left: tall $accent 50%;
    }
    ReasoningBlock > .reasoning-head {
        color: $accent;
        text-style: italic;
    }
    ReasoningBlock > .reasoning-body {
        color: $text-muted;
        display: none;
        padding: 0 0 0 2;
    }
    ReasoningBlock.-open > .reasoning-body {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._buffer: list[str] = []
        self._open = False
        self._done = False

    def compose(self) -> ComposeResult:
        yield Static("◌ thinking…", classes="reasoning-head")
        yield Static("", classes="reasoning-body")

    def append(self, text: str) -> None:
        self._buffer.append(text)
        joined = "".join(self._buffer)
        body = self.query_one(".reasoning-body", Static)
        body.update(escape(joined[-6000:]))
        words = len(joined.split())
        self.query_one(".reasoning-head", Static).update(
            f"[$accent]◌ thinking…[/] [dim]({words} words, click or press r to expand)[/dim]"
        )

    def finish(self) -> None:
        self._done = True
        joined = "".join(self._buffer)
        words = len(joined.split())
        self.query_one(".reasoning-head", Static).update(
            f"[$success]✓[/] [$accent]reasoning[/] [dim]({words} words, click to toggle)[/dim]"
        )

    def toggle(self) -> None:
        self._open = not self._open
        self.set_class(self._open, "-open")

    def on_click(self) -> None:
        self.toggle()


class ToolCall(Vertical):
    """One tool invocation: header line + expandable result."""

    DEFAULT_CSS = """
    ToolCall {
        height: auto;
        margin: 0;
        padding: 0;
        background: $background;
    }
    ToolCall > .tool-head {
        color: $foreground;
        background: $background;
        text-style: bold;
    }
    ToolCall > .tool-body {
        display: none;
        padding: 0 0 0 5;
        color: $text-muted;
        max-height: 24;
        overflow-y: auto;
        border-left: tall $border;
        background: $background;
    }
    ToolCall.-open > .tool-body { display: block; }
    """

    SPINNER_STEPS = 12

    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.call_id = call_id
        self.tool_name = name
        self.arguments = arguments
        self.phase = "start"
        self.result: Any = None
        self.error: str | None = None
        self.output_lines: list[str] = []
        self._spin = 0
        self._open = False
        self.add_class("-running")

    def compose(self) -> ComposeResult:
        yield Static(self._head_text(), classes="tool-head", markup=True)
        yield Static("", classes="tool-body", markup=False)

    def on_mount(self) -> None:
        self.set_interval(0.12, self._tick)
        self._refresh_head()
        self._refresh_body()

    def _refresh_head(self) -> None:
        if not self.is_mounted:
            return
        try:
            self.query_one(".tool-head", Static).update(self._head_text())
        except Exception:  # noqa: BLE001 - children not composed yet
            pass

    def _tick(self) -> None:
        if self.phase in {"start", "output"}:
            self._spin = (self._spin + 1) % self.SPINNER_STEPS
            self._refresh_head()

    def _status_glyph(self) -> str:
        # Three cells wide in every state so the tool names stay aligned.
        if self.phase in {"start", "output"}:
            return wave_frame(self._spin)
        if self.phase == "error":
            return " ✗ "
        return " ✓ "

    def _head_text(self) -> str:
        args_md = tool_args_markup(self.arguments)
        args_md = f"  {args_md}" if args_md else ""
        tail = ""
        if self.phase == "output" and self.output_lines:
            tail = f"  [$text-muted]{_markup_escape(self.output_lines[-1][:60])}[/]"
        elif self.phase == "error" and self.error:
            tail = f"  [$error]{_markup_escape(self.error.splitlines()[0][:80])}[/]"
        ink = tool_color(self.tool_name)
        icon = f"[{ink}]{tool_icon(self.tool_name)}[/]"
        if self.phase == "error":
            name = f"[b $error]{_markup_escape(self.tool_name)}[/]"
            mark = "[$error] ✗ [/]"
        elif self.phase in {"start", "output"}:
            name = f"[b {ink}]{_markup_escape(self.tool_name)}[/]"
            mark = f"[{ink}]{self._status_glyph()}[/]"
        else:
            name = f"[b {ink}]{_markup_escape(self.tool_name)}[/]"
            mark = "[$text-muted] ✓ [/]"
        return f"{mark} {icon} {name}{args_md}{tail}"

    def apply(
        self, *, phase: str, result: Any = None, error: str | None = None, output: str | None = None
    ) -> None:
        if output:
            self.output_lines.extend(output.splitlines())
            self.output_lines = self.output_lines[-400:]
        if phase == "output":
            self.phase = "output"
        elif phase in {"end", "error"}:
            self.phase = phase
            self.result = result
            self.error = error
            self.remove_class("-running")
            self.add_class("-ok" if phase == "end" else "-error")
        self._refresh_head()
        self._refresh_body()

    def _refresh_body(self) -> None:
        if not self.is_mounted:
            return
        chunks: list[str] = []
        if self.arguments:
            chunks.append("args: " + render_result(self.arguments, 1200))
        if self.output_lines:
            chunks.append("\n".join(self.output_lines[-120:]))
        if self.error:
            chunks.append("error: " + self.error)
        elif self.result is not None:
            chunks.append(render_result(self.result))
        try:
            self.query_one(".tool-body", Static).update("\n".join(chunks))
        except Exception:  # noqa: BLE001
            pass

    def toggle(self) -> None:
        self._open = not self._open
        self.set_class(self._open, "-open")
        if self._open:
            self._refresh_body()

    def on_click(self) -> None:
        self.toggle()


class ProgressLine(Static):
    DEFAULT_CSS = """
    ProgressLine {
        margin: 0 2 0 2;
        padding: 0 2;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"· {escape(text)}", markup=True)


class SystemNote(Static):
    DEFAULT_CSS = """
    SystemNote {
        margin: 1 2 0 2;
        padding: 0 1;
        color: $text-muted;
        border-left: tall $border;
        background: $background;
    }
    SystemNote.-warning { border-left: tall $warning; color: $warning; }
    SystemNote.-error {
        border-left: tall $error;
        color: $error;
        background: $panel;
        padding: 1 2;
    }
    SystemNote.-success { border-left: tall $success; }
    SystemNote.-quiet { border-left: none; padding: 0 3; margin: 1 2 0 2; }
    """

    def __init__(self, text: str, level: str = "info") -> None:
        super().__init__(text, markup=True)
        if level in {"warning", "error", "success", "quiet"}:
            self.add_class(f"-{level}")


class SubagentCard(Static):
    DEFAULT_CSS = """
    SubagentCard {
        margin: 0 2 0 2;
        padding: 0 2;
        color: $text-muted;
        border-left: tall $accent 60%;
    }
    SubagentCard.-done { border-left: tall $success; }
    SubagentCard.-error { border-left: tall $error; color: $error; }
    """

    def __init__(self, task_id: str) -> None:
        super().__init__("", markup=True)
        self.task_id = task_id

    def apply(
        self,
        label: str,
        phase: str,
        status_line: str,
        model: str | None,
        iteration: int,
        done: bool,
        error: str | None,
    ) -> None:
        glyph = "✓" if done and not error else ("✗" if error else "⟳")
        model_md = f" [dim]{escape(model)}[/dim]" if model else ""
        it = f" [dim]#{iteration}[/dim]" if iteration else ""
        status = escape(error or status_line or phase)
        self.update(f"{glyph} ⑂ [b]{escape(label)}[/b]{model_md}{it}  {status}")
        self.set_class(bool(done and not error), "-done")
        self.set_class(bool(error), "-error")


class AssistantMessage(Vertical):
    """An assistant turn: reasoning, activity (tools), streamed Markdown body."""

    DEFAULT_CSS = """
    AssistantMessage {
        height: auto;
        margin: 1 2 0 2;
        padding: 0 1 0 1;
        border-left: tall $primary;
        background: $background;
    }
    AssistantMessage > .assistant-head {
        height: 1;
        margin: 0;
        padding: 0;
        color: $primary;
        text-style: bold;
        background: $background;
    }
    AssistantMessage > .assistant-body {
        margin: 0;
        padding: 0;
        height: auto;
        background: $background;
    }
    AssistantMessage > .assistant-foot {
        margin: 0 0 1 0;
        padding: 0;
        color: $text-muted;
        display: none;
        background: $background;
    }
    AssistantMessage > ReasoningBlock,
    AssistantMessage > ToolCall,
    AssistantMessage > ProgressLine,
    AssistantMessage > SubagentCard {
        margin: 1 0;
    }
    AssistantMessage > .assistant-foot.-visible { display: block; }
    AssistantMessage > .assistant-body Markdown { margin: 0; padding: 0; background: transparent; }
    AssistantMessage > .assistant-body MarkdownFence {
        margin: 0 0 1 0;
        padding: 0 1;
        color: #E8E8E8;
        background: #121212;
    }
    AssistantMessage > .assistant-body MarkdownH1,
    AssistantMessage > .assistant-body MarkdownH2,
    AssistantMessage > .assistant-body MarkdownH3 { margin: 0 0 1 0; padding: 0; background: transparent; border: none; }
    AssistantMessage > .assistant-body MarkdownParagraph { margin: 0 0 1 0; }
    AssistantMessage > .assistant-body MarkdownBulletList,
    AssistantMessage > .assistant-body MarkdownOrderedList { margin: 0 0 1 0; }
    AssistantMessage > .assistant-body MarkdownListItem MarkdownParagraph { margin: 0; }
    AssistantMessage > .assistant-body MarkdownBlock > .code_inline,
    AssistantMessage > .assistant-body MarkdownBlock:dark > .code_inline,
    AssistantMessage > .assistant-body MarkdownBlock:light > .code_inline {
        text-style: bold;
        color: $primary;
        background: transparent;
    }
    """

    def __init__(self, bot_name: str = "navin", bot_icon: str = MARK) -> None:
        super().__init__()
        self.bot_name = bot_name
        icon = (bot_icon or "").strip()
        self.bot_icon = "" if icon in {"≈", "~"} else icon
        self._stream: Any = None
        self._buffer: list[str] = []
        self._tools: dict[str, ToolCall] = {}
        self._subagents: dict[str, SubagentCard] = {}
        self._reasoning: ReasoningBlock | None = None
        self.streamed = False
        self.finished = False
        self._composed = asyncio.Event()

    def compose(self) -> ComposeResult:
        head = escape(self.bot_name.lower())
        if self.bot_icon:
            head = f"{escape(self.bot_icon)} {head}"
        yield Static(head, classes="assistant-head", markup=True)
        yield Markdown("", classes="assistant-body")
        yield Static("", classes="assistant-foot", markup=True)

    def on_mount(self) -> None:
        self._composed.set()

    async def _ready_body(self) -> Markdown:
        await self._composed.wait()
        return self.query_one(".assistant-body", Markdown)

    # -- reasoning --------------------------------------------------------

    async def reasoning(self, text: str, *, end: bool = False, visible: bool = True) -> None:
        if not visible:
            return
        body = await self._ready_body()
        if end:
            if self._reasoning is not None:
                self._reasoning.finish()
            return
        if self._reasoning is None or self._reasoning._done:
            block = ReasoningBlock()
            self._reasoning = block
            await self.mount(block, before=body)
            block.append(text)
            return
        self._reasoning.append(text)

    def toggle_reasoning(self) -> None:
        if self._reasoning is not None:
            self._reasoning.toggle()

    # -- activity ---------------------------------------------------------

    async def tool_event(
        self,
        call_id: str,
        name: str,
        phase: str,
        arguments: dict[str, Any],
        result: Any,
        error: str | None,
        output: str | None,
        visible: bool = True,
    ) -> None:
        if not visible:
            return
        body = await self._ready_body()
        key = call_id or f"{name}:{len(self._tools)}"
        widget = self._tools.get(key)
        if widget is None:
            widget = ToolCall(key, name, arguments)
            self._tools[key] = widget
            await self.mount(widget, before=body)
            if phase == "start":
                return
        widget.apply(phase=phase, result=result, error=error, output=output)

    async def progress(self, text: str) -> None:
        body = await self._ready_body()
        await self.mount(ProgressLine(text), before=body)

    async def subagent(
        self,
        task_id: str,
        label: str,
        phase: str,
        status_line: str,
        model: str | None,
        iteration: int,
        done: bool,
        error: str | None,
    ) -> None:
        body = await self._ready_body()
        card = self._subagents.get(task_id)
        if card is None:
            card = SubagentCard(task_id)
            self._subagents[task_id] = card
            await self.mount(card, before=body)
        card.apply(label, phase, status_line, model, iteration, done, error)

    # -- body -------------------------------------------------------------

    async def delta(self, text: str) -> None:
        if not text:
            return
        self.finished = False
        self.streamed = True
        self._buffer.append(text)
        body = await self._ready_body()
        if self._stream is None:
            self._stream = Markdown.get_stream(body)
            already = "".join(self._buffer[:-1])
            if already:
                await self._stream.write(already)
        await self._stream.write(text)

    async def stream_end(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None

    async def set_text(self, text: str, *, render_as: str = "markdown") -> None:
        await self.stream_end()
        self._buffer = [text]
        body = await self._ready_body()
        if render_as == "text":
            text = (
                "```text\n" + text + "\n```"
                if "\n" in text and not text.lstrip().startswith("#")
                else text
            )
        await body.update(text)

    async def finish(
        self, *, latency_ms: int | None, model: str | None, preset: str | None
    ) -> None:
        await self._composed.wait()
        await self.stream_end()
        self.finished = True
        for tool in self._tools.values():
            if tool.phase in {"start", "output"}:
                tool.apply(phase="end")
        foot = self.query_one(".assistant-foot", Static)
        if self._tools:
            n = len(self._tools)
            foot.update(f"{n} tool call{'s' if n != 1 else ''}")
            foot.add_class("-visible")
        else:
            foot.update("")
            foot.remove_class("-visible")

    @property
    def text(self) -> str:
        return "".join(self._buffer)


# ---------------------------------------------------------------------------
# Approval / choice cards
# ---------------------------------------------------------------------------


class ApprovalCard(Vertical):
    """Permission request raised by a tool (exec, write outside workspace...)."""

    DEFAULT_CSS = """
    ApprovalCard {
        height: auto;
        margin: 1 2 0 2;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    ApprovalCard .card-title { text-style: bold; color: $warning; }
    ApprovalCard .card-detail { color: $text-muted; margin: 0 0 1 0; }
    ApprovalCard Horizontal { height: auto; }
    ApprovalCard Button { margin: 0 1 0 0; min-width: 12; }
    ApprovalCard.-closed { border: round $panel; }
    ApprovalCard.-closed Horizontal { display: none; }
    """

    class Decided(Message):
        def __init__(self, request_id: str, allowed: bool, remember: bool) -> None:
            super().__init__()
            self.request_id = request_id
            self.allowed = allowed
            self.remember = remember

    def __init__(
        self,
        request_id: str,
        tool: str,
        action: str,
        reason: str,
        detail: str,
        consequence: str,
        scope: str,
        remember_offered: bool,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.tool = tool
        self.action = action
        self.reason = reason
        self.detail = detail
        self.consequence = consequence
        self.scope = scope
        self.remember_offered = remember_offered
        self.closed = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"⚠ Permission needed  [dim]{escape(self.tool)}[/dim]",
            classes="card-title",
            markup=True,
        )
        lines = [escape(self.action)]
        if self.reason:
            lines.append(f"[dim]why:[/dim] {escape(self.reason)}")
        if self.detail:
            lines.append(escape(self.detail[:600]))
        if self.consequence:
            lines.append(f"[dim]impact:[/dim] {escape(self.consequence)}")
        if self.scope:
            lines.append(f"[dim]scope:[/dim] {escape(self.scope)}")
        yield Static("\n".join(lines), classes="card-detail", markup=True)
        with Horizontal():
            yield Button("Allow  [y]", variant="success", id="allow")
            if self.remember_offered:
                yield Button("Always  [a]", variant="primary", id="always")
            yield Button("Deny  [n]", variant="error", id="deny")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id or ""
        self.decide(allowed=bid != "deny", remember=bid == "always")

    def decide(self, *, allowed: bool, remember: bool) -> None:
        if self.closed:
            return
        self.post_message(self.Decided(self.request_id, allowed, remember))

    def close(self, allowed: bool, reason: str = "") -> None:
        self.closed = True
        self.add_class("-closed")
        verdict = "allowed" if allowed else "denied"
        extra = f"  [dim]{escape(reason)}[/dim]" if reason else ""
        self.query_one(".card-title", Static).update(
            f"{'✓' if allowed else '✗'} Permission {verdict}  [dim]{escape(self.tool)}[/dim]{extra}"
        )


class ChoiceCard(Vertical):
    """A question with options asked by the agent (ask_user tool)."""

    DEFAULT_CSS = """
    ChoiceCard {
        height: auto;
        margin: 1 2 0 2;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    ChoiceCard .card-title { text-style: bold; color: $accent; }
    ChoiceCard OptionList { height: auto; max-height: 12; margin: 1 0 0 0; background: transparent; }
    ChoiceCard Input { margin: 1 0 0 0; }
    ChoiceCard .card-hint { color: $text-muted; }
    ChoiceCard.-closed OptionList, ChoiceCard.-closed Input, ChoiceCard.-closed .card-hint { display: none; }
    ChoiceCard.-closed { border: round $panel; }
    """

    class Answered(Message):
        def __init__(
            self, request_id: str, option_id: str, skipped: bool, custom_text: str
        ) -> None:
            super().__init__()
            self.request_id = request_id
            self.option_id = option_id
            self.skipped = skipped
            self.custom_text = custom_text

    def __init__(
        self,
        request_id: str,
        question: str,
        options: list[dict[str, Any]],
        allow_skip: bool,
        recommended_id: str,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.question = question
        self.options = options
        self.allow_skip = allow_skip
        self.recommended_id = recommended_id
        self.closed = False

    def compose(self) -> ComposeResult:
        yield Static(f"❔ {escape(self.question)}", classes="card-title", markup=True)
        items: list[Option] = []
        for idx, opt in enumerate(self.options, start=1):
            oid = str(opt.get("id") or opt.get("value") or idx)
            label = str(opt.get("label") or opt.get("title") or oid)
            desc = str(opt.get("description") or "")
            star = " [b $accent]★[/]" if oid == self.recommended_id else ""
            text = f"[b]{idx}.[/b] {escape(label)}{star}"
            if desc:
                text += f"\n   [dim]{escape(desc)}[/dim]"
            items.append(Option(text, id=oid))
        yield OptionList(*items)
        yield Input(placeholder="Or type a custom answer and press Enter…", id="custom")
        hint = (
            "Enter selects · digits jump · Esc skips"
            if self.allow_skip
            else "Enter selects · digits jump"
        )
        yield Static(hint, classes="card-hint")

    def on_mount(self) -> None:
        opts = self.query_one(OptionList)
        if self.recommended_id:
            for idx, opt in enumerate(self.options):
                if str(opt.get("id") or opt.get("value") or idx + 1) == self.recommended_id:
                    opts.highlighted = idx
                    break
        opts.focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.answer(option_id=str(event.option.id or ""))

    @on(Input.Submitted)
    def _custom(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        if text:
            self.answer(custom_text=text)

    def pick_index(self, index: int) -> None:
        if 0 <= index < len(self.options):
            opt = self.options[index]
            self.answer(option_id=str(opt.get("id") or opt.get("value") or index + 1))

    def answer(self, *, option_id: str = "", skipped: bool = False, custom_text: str = "") -> None:
        if self.closed:
            return
        self.post_message(self.Answered(self.request_id, option_id, skipped, custom_text))

    def close(self, option_id: str = "", skipped: bool = False, custom_text: str = "") -> None:
        self.closed = True
        self.add_class("-closed")
        if skipped:
            chosen = "skipped"
        elif custom_text:
            chosen = custom_text
        else:
            chosen = option_id
            for opt in self.options:
                if str(opt.get("id") or opt.get("value") or "") == option_id:
                    chosen = str(opt.get("label") or option_id)
        self.query_one(".card-title", Static).update(
            f"✓ {escape(self.question)}  [dim]→ {escape(chosen)}[/dim]"
        )


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


def split_model_slug(slug: str) -> tuple[str, str]:
    """``z-ai/glm-5.3-flash`` -> (``glm-5.3-flash``, ``z-ai``)."""
    text = (slug or "").strip()
    if "/" in text:
        provider, name = text.split("/", 1)
        return name.strip(), provider.strip()
    return text, ""


class ComposerShell(Vertical):
    """Prompt, then mode · model, then the line under both."""

    DEFAULT_CSS = """
    ComposerShell {
        height: auto;
        background: $panel;
        padding: 1 2 1 2;
        border-left: wide $foreground 35%;
    }
    ComposerShell > TideRule { margin: 0; height: 1; }
    ComposerShell.-focus { border-left: wide $foreground; }
    ComposerShell.-busy { border-left: wide $foreground 70%; }
    ComposerShell.-busy.-focus { border-left: wide $foreground; }
    """

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if isinstance(event.widget, Composer):
            return
        event.stop()
        self.post_message(Composer.ChatScroll(-3))

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if isinstance(event.widget, Composer):
            return
        event.stop()
        self.post_message(Composer.ChatScroll(3))

    def set_busy(self, busy: bool) -> None:
        self.set_class(busy, "-busy")

    def set_focus(self, focused: bool) -> None:
        self.set_class(focused, "-focus")


class ComposerMeta(Horizontal):
    """Mode and model on the last row of the chat field."""

    DEFAULT_CSS = """
    ComposerMeta {
        height: 1;
        padding: 0 0 0 0;
        background: $panel;
    }
    ComposerMeta #meta-mode {
        width: auto;
        color: $primary;
    }
    ComposerMeta #meta-mode:hover { color: $primary; }
    ComposerMeta #meta-sep { width: auto; color: $text-muted; padding: 0 1; }
    ComposerMeta #meta-model { width: 1fr; color: $text-muted; }
    ComposerMeta #meta-model:hover { color: $foreground; }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="meta-mode", markup=True)
        yield Static("", id="meta-sep", markup=True)
        yield Static("", id="meta-model", markup=True)

    def set_meta(
        self,
        *,
        mode: str,
        model: str = "",
        extra: str = "",
        busy: bool = False,
        spin: int = 0,
    ) -> None:
        name, provider = split_model_slug(model)
        mode_w = self.query_one("#meta-mode", Static)
        sep_w = self.query_one("#meta-sep", Static)
        model_w = self.query_one("#meta-model", Static)
        if extra:
            mode_w.update(extra)
            sep_w.update("")
            model_w.update("")
            return
        prefix = f"{wave_frame(spin)} " if busy else ""
        mode_w.update(f"[$primary]{prefix}{escape(mode)}[/]")
        if name:
            sep_w.update("·")
            tail = f"  [dim]{escape(provider)}[/]" if provider else ""
            model_w.update(f"{escape(name)}{tail}")
        else:
            sep_w.update("·")
            model_w.update("no model")

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if isinstance(target, Static) and target.id == "meta-model":
            self.app.call_later(self.app.run_action, "pick_model")
        else:
            self.app.call_later(self.app.run_action, "pick_mode")


class Composer(TextArea):
    """Multi-line prompt. Enter submits, Shift+Enter / Ctrl+J inserts a newline.

    Mode and model sit on the last row of the same field.
    """

    DEFAULT_CSS = """
    Composer {
        height: auto;
        max-height: 12;
        min-height: 2;
        border: none !important;
        background: $panel;
        padding: 0 0 0 0;
        scrollbar-size-vertical: 1;
    }
    Composer:focus { border: none !important; }
    Composer .text-area--placeholder { color: $text-muted; }
    """

    BINDINGS = [
        Binding("ctrl+j", "newline", "Newline", show=False),
        Binding("shift+enter", "newline", "Newline", show=False),
        Binding("alt+enter", "newline", "Newline", show=False),
        Binding("ctrl+a", "select_all", "Select all", show=False),
        Binding("ctrl+v", "paste_any", "Paste", show=False),
        Binding("ctrl+shift+v", "paste_any", "Paste", show=False),
        Binding("shift+insert", "paste_any", "Paste", show=False),
        Binding("ctrl+f", "find", "Find", show=False),
        Binding("pageup", "page_chat", "Page up", show=False),
        Binding("pagedown", "page_chat_down", "Page down", show=False),
    ]

    class FindRequested(Message):
        pass

    class PageChat(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    class ChatScroll(Message):
        """A few lines of conversation scroll (mouse wheel on the prompt)."""

        def __init__(self, delta: int) -> None:
            super().__init__()
            self.delta = delta

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class HistoryRequested(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    class SlashTyping(Message):
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    class MenuNav(Message):
        """Up/Down/Tab while the slash menu is open."""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    class Shortcut(Message):
        """A single-key shortcut pressed on an empty composer (y/a/n, digits)."""

        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    def __init__(self, placeholder: str = "") -> None:
        super().__init__(
            "",
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            placeholder=placeholder,
            highlight_cursor_line=False,
            classes="-textual-compact",
        )
        self.menu_open = False
        self.shortcut_keys: set[str] = set()

    def _shell(self) -> ComposerShell | None:
        parent = self.parent
        return parent if isinstance(parent, ComposerShell) else None

    def on_focus(self) -> None:
        shell = self._shell()
        if shell is not None:
            shell.set_focus(True)

    def on_blur(self) -> None:
        shell = self._shell()
        if shell is not None:
            shell.set_focus(False)

    @property
    def is_slash_prefix(self) -> bool:
        text = self.text
        return text.startswith("/") and "\n" not in text and " " not in text

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if self.menu_open and self.is_slash_prefix and event.key in {"up", "down", "tab"}:
            event.prevent_default()
            event.stop()
            self.post_message(self.MenuNav(event.key))
            return
        if not self.text.strip() and event.key in self.shortcut_keys:
            event.prevent_default()
            event.stop()
            self.post_message(self.Shortcut(event.key))
            return
        if event.key in {"up", "down"}:
            row, _ = self.cursor_location
            lines = self.document.line_count
            at_edge = (event.key == "up" and row == 0) or (event.key == "down" and row >= lines - 1)
            if at_edge and (not self.text.strip() or lines == 1):
                event.prevent_default()
                event.stop()
                self.post_message(self.HistoryRequested(-1 if event.key == "up" else 1))
                return
        await super()._on_key(event)

    def action_newline(self) -> None:
        self.insert("\n")

    def action_paste_any(self) -> None:
        """Paste OS clipboard (Windows / WSL) or the in-app clipboard."""
        from navin.tui.clipboard import read_clipboard

        text = (self.app.clipboard or "").strip("\x00") or read_clipboard()
        if text:
            self.insert(text)

    def action_find(self) -> None:
        self.post_message(self.FindRequested())

    def action_page_chat(self) -> None:
        self.post_message(self.PageChat(-1))

    def action_page_chat_down(self) -> None:
        self.post_message(self.PageChat(1))

    def action_cursor_page_up(self) -> None:
        """TextArea also binds PageUp; send it to the transcript, not the prompt."""
        self.post_message(self.PageChat(-1))

    def action_cursor_page_down(self) -> None:
        self.post_message(self.PageChat(1))

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self.max_scroll_y > 0 and self.scroll_y > 0:
            return
        event.stop()
        self.post_message(self.ChatScroll(-3))

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.max_scroll_y > 0 and not self.is_vertical_scroll_end:
            return
        event.stop()
        self.post_message(self.ChatScroll(3))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        text = self.text
        if text.startswith("/") and "\n" not in text and " " not in text:
            self.post_message(self.SlashTyping(text))
        else:
            self.post_message(self.SlashTyping(None))

    def set_text(self, text: str) -> None:
        self.load_text(text)
        self.move_cursor(self.document.end)

    def clear_text(self) -> None:
        self.load_text("")


class SlashMenu(OptionList):
    """Inline completion popup for slash commands."""

    DEFAULT_CSS = """
    SlashMenu {
        height: auto;
        max-height: 10;
        margin: 0 2;
        border-left: wide $primary;
        background: $surface;
        display: none;
        scrollbar-size-vertical: 1;
    }
    SlashMenu.-visible { display: block; }
    SlashMenu > .option-list--option-highlighted { background: $primary 20%; }
    """

    def show_matches(self, matches: list[dict[str, Any]]) -> None:
        self.clear_options()
        if not matches:
            self.remove_class("-visible")
            return
        for row in matches[:40]:
            cmd = str(row["command"])
            hint = (
                f" [dim]{escape(str(row.get('arg_hint') or ''))}[/dim]"
                if row.get("arg_hint")
                else ""
            )
            title = escape(str(row.get("title") or ""))
            self.add_option(Option(f"[b]{cmd}[/b]{hint}  {title}", id=cmd))
        self.highlighted = 0
        self.add_class("-visible")

    def hide(self) -> None:
        self.remove_class("-visible")

    @property
    def visible_menu(self) -> bool:
        return self.has_class("-visible")


# ---------------------------------------------------------------------------
# Transcript container
# ---------------------------------------------------------------------------


class FindBar(Horizontal):
    """One-line find, like a browser: type, Enter next, Shift+Enter previous, Esc close."""

    DEFAULT_CSS = """
    FindBar {
        height: 1;
        padding: 0 1;
        display: none;
    }
    FindBar.-visible { display: block; }
    FindBar Input {
        border: none;
        height: 1;
        width: 1fr;
        background: $surface;
        padding: 0 1;
    }
    FindBar Input:focus { border: none; background: $primary 18%; }
    FindBar #find-count { width: auto; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("shift+enter", "prev", "Previous", show=False),
        Binding("f3", "next", "Next", show=False),
        Binding("shift+f3", "prev", "Previous", show=False),
    ]

    class Closed(Message):
        pass

    class Moved(Message):
        def __init__(self, query: str, direction: int) -> None:
            super().__init__()
            self.query = query
            self.direction = direction

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Find in conversation…", id="find-query")
        yield Static("", id="find-count")

    def show(self, seed: str = "") -> None:
        self.add_class("-visible")
        inp = self.query_one("#find-query", Input)
        if seed and not inp.value:
            inp.value = seed
        inp.focus()

    def hide(self) -> None:
        self.remove_class("-visible")
        self.query_one("#find-count", Static).update("")

    @property
    def visible_bar(self) -> bool:
        return self.has_class("-visible")

    def set_count(self, index: int, total: int) -> None:
        self.query_one("#find-count", Static).update(f"{index}/{total}" if total else "no match")

    def action_close(self) -> None:
        self.hide()
        self.post_message(self.Closed())

    def action_next(self) -> None:
        self.post_message(self.Moved(self.query_one("#find-query", Input).value, 1))

    def action_prev(self) -> None:
        self.post_message(self.Moved(self.query_one("#find-query", Input).value, -1))

    @on(Input.Submitted, "#find-query")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_next()

    @on(Input.Changed, "#find-query")
    def _changed(self, event: Input.Changed) -> None:
        self.post_message(self.Moved(event.value, 0))


class Transcript(VerticalScroll):
    DEFAULT_CSS = """
    Transcript {
        height: 1fr;
        padding: 0 0 2 0;
        scrollbar-size-vertical: 1;
        background: $background;
    }
    """

    auto_follow = reactive(True)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self.auto_follow = self.is_vertical_scroll_end

    def nudge(self, delta: int) -> None:
        if not delta:
            return
        self.scroll_relative(y=delta, animate=False)
        self.auto_follow = self.is_vertical_scroll_end

    def page(self, direction: int) -> None:
        height = max(1, self.size.height - 2)
        self.nudge(direction * height)

    async def add(self, widget: Any) -> None:
        await self.mount(widget)
        if self.auto_follow:
            self.scroll_end(animate=False)

    def follow(self) -> None:
        if self.auto_follow:
            self.scroll_end(animate=False)


class DockHint(Static):
    """One quiet shortcut on the single footer line."""

    DEFAULT_CSS = """
    DockHint {
        width: auto;
        height: 1;
        padding: 0 1 0 0;
        color: $foreground;
    }
    DockHint:hover { color: $foreground; }
    """

    def __init__(self, key: str, label: str, action: str, *, id: str | None = None) -> None:
        super().__init__("", markup=True, id=id)
        self.key = key
        self.label = label
        self.action = action
        self._paint()

    def set_label(self, label: str) -> None:
        self.label = label
        self._paint()

    def _paint(self) -> None:
        if self.key:
            self.update(f"[$text-muted]{escape(self.key)}[/]  {escape(self.label)}")
        elif self.action:
            self.update(escape(self.label))
        else:
            self.update(f"[$text-muted]{escape(self.label)}[/]")

    def on_click(self) -> None:
        if self.action:
            self.app.call_later(self.app.run_action, self.action)


class DockBar(Horizontal):
    """One footer line: project path, then shortcuts."""

    DEFAULT_CSS = """
    DockBar {
        width: 1fr;
        height: 1;
        min-height: 1;
        padding: 0 1;
        background: $background;
        overflow: hidden;
    }
    DockBar.-hidden { display: none; height: 0; min-height: 0; }
    DockBar > #dock-path {
        width: 1fr;
        height: 1;
        padding: 0 2 0 1;
        color: $text-muted;
        overflow: hidden;
    }
    DockBar > #dock-path:hover { color: $foreground; }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._path = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="dock-path")
        yield DockHint("ctrl+p", "commands", "command_palette", id="dock-commands")
        yield DockHint("ctrl+b", "panel", "toggle_sidebar", id="dock-hide")
        yield DockHint("ctrl+i", "Provider", "open_settings('providers')", id="dock-provider")
        yield DockHint("ctrl+o", "Model", "pick_model", id="dock-model")
        yield DockHint("ctrl+t", "Mode", "pick_mode", id="dock-mode")
        yield DockHint("ctrl+s", "Session", "pick_session", id="dock-session")
        yield DockHint("ctrl+g", "Settings", "open_settings", id="dock-settings")

    def set_panel(self, visible: bool) -> None:
        self.set_class(visible, "-hidden")

    def set_path(self, path: str) -> None:
        self._path = (path or "").replace("\\", "/").strip()
        self._paint_path()

    def on_resize(self) -> None:
        self._paint_path()

    def _paint_path(self) -> None:
        try:
            slot = self.query_one("#dock-path", Static)
        except Exception:  # noqa: BLE001
            return
        raw = self._path
        width = slot.size.width or 24
        if width < 8:
            width = 24
        shown = raw if len(raw) <= width else fit_path(raw, width)
        slot.update(escape(shown))

    def on_click(self, event: events.Click) -> None:
        if getattr(event.widget, "id", None) == "dock-path":
            event.stop()
            self.app.call_later(self.app.run_action, "pick_project")


_KEY_WIDTH = 8  # "ctrl+b  " - every key column in the panel is this wide


def wrap_path(path: str, width: int) -> str:
    """Break a filesystem path on ``/`` so the panel never splits a folder name."""
    raw = (path or "").replace("\\", "/").strip()
    if width < 4 or len(raw) <= width:
        return raw
    parts = raw.split("/")
    lines: list[str] = []
    current = ""
    for i, part in enumerate(parts):
        chunk = part if i == 0 else f"/{part}"
        if current and len(current) + len(chunk) > width:
            lines.append(current)
            current = chunk
            while len(current) > width:
                lines.append(current[:width])
                current = current[width:]
        else:
            current += chunk
    if current:
        lines.append(current)
    return "\n".join(lines)


def context_meter(used: int, window: int, cells: int = 12) -> tuple[str, str]:
    """Segmented context bar plus ``10% of 200k`` caption."""
    if window <= 0:
        return "", ""
    pct = max(0, min(100, int(round(100 * used / window))))
    filled = max(0, min(cells, int(round(cells * pct / 100))))
    bar = ("█" * filled) + ("░" * (cells - filled))
    if window >= 1000:
        cap = f"{pct}% of {window // 1000}k"
    else:
        cap = f"{pct}% of {window}"
    return bar, cap


def context_line(used: int, window: int, width: int) -> str:
    """One line: bar fills the row, then ``11% of 200k`` on the same line."""
    if window <= 0 or width < 8:
        return ""
    _, cap = context_meter(used, window, cells=1)
    cells = max(4, width - len(cap) - 1)
    bar, cap = context_meter(used, window, cells=cells)
    line = f"{bar} {cap}"
    return line[:width] if len(line) > width else line


def _as_price_usd(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            amount = float(value)
        except ValueError:
            return None
        return amount if amount > 0 else None
    return None


def account_price_label(payload: dict[str, Any]) -> str:
    """Current plan price, e.g. ``$69/month`` or ``$40/seat/month``."""
    plan = str(payload.get("plan") or "").strip()
    if plan:
        try:
            from navin.license_client import plan_price_label
        except ImportError:
            label = ""
        else:
            label = plan_price_label(plan)
        if label:
            return label
    price = _as_price_usd(payload.get("plan_price_usd"))
    if price is None:
        return ""
    if price == int(price):
        return f"${int(price)}/month"
    return f"${price}/month"


def account_spend_label(payload: dict[str, Any]) -> str:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if not usage:
        return ""
    spent = int(usage.get("spent_micro_usd") or 0) / 1_000_000
    budget = int(usage.get("budget_micro_usd") or 0) / 1_000_000
    if not (budget or spent):
        return ""
    return f"${spent:.2f} / ${budget:.2f}"


def account_side_text(payload: dict[str, Any]) -> str:
    """Panel body: plan, usage %, plan price, then spend / budget."""
    if not payload.get("connected"):
        return ""
    plan = str(payload.get("plan_label") or payload.get("plan") or "plan")
    bits = [f"• {plan}"]
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    if usage and usage.get("used_percent") is not None:
        bits.append(f"{int(usage.get('used_percent') or 0)}%")
    price = account_price_label(payload)
    if price and price.casefold() != plan.casefold():
        bits.append(price)
    lines = ["  ".join(bits)]
    spend = account_spend_label(payload)
    if spend:
        lines.append(spend)
    return "\n".join(lines)


def fit_path(path: str, width: int) -> str:
    """One-line path: keep the head and the leaf, insert ``...`` if needed."""
    raw = (path or "").replace("\\", "/").strip()
    if width < 4 or len(raw) <= width:
        return raw
    leaf = raw.rsplit("/", 1)[-1] or raw
    mark = "..."
    if len(mark) + 1 + len(leaf) >= width:
        return (mark + leaf)[-width:]
    budget = width - len(mark) - 1 - len(leaf)
    head = raw[:budget].rstrip("/")
    cut = head.rfind("/")
    if cut >= 1:
        head = head[:cut]
    if not head:
        return f"{mark}/{leaf}"
    return f"{head}/{mark}/{leaf}"


def _key_markup(key: str) -> str:
    """Shortcut column: muted color, pad with spaces outside the markup."""
    shown = escape(key)
    pad = max(0, _KEY_WIDTH - len(key))
    if not key:
        return " " * _KEY_WIDTH
    return f"[$text-muted]{shown}[/]{' ' * pad}"


class SideAction(Static):
    """One clickable row: muted key, readable label, muted value."""

    DEFAULT_CSS = """
    SideAction { height: 1; color: $foreground; overflow: hidden; }
    SideAction:hover { color: $foreground; }
    """

    def __init__(self, key: str, label: str, action: str, *, id: str | None = None) -> None:
        super().__init__("", markup=True, id=id)
        self.key = key
        self.label = label
        self.action = action
        self._value = ""
        self._render_row()

    def set_value(self, value: str) -> None:
        self._value = value
        self._render_row()

    def set_label(self, label: str) -> None:
        self.label = label
        self._render_row()

    def _render_row(self) -> None:
        tail = f"  [$text-muted]{escape(self._value)}[/]" if self._value else ""
        self.update(f"{_key_markup(self.key)}{escape(self.label)}{tail}")

    def on_click(self) -> None:
        if self.action:
            self.app.call_later(self.app.run_action, self.action)


class SideCard(Vertical):
    """A clickable block: key + title on the first row, details under it."""

    DEFAULT_CSS = """
    SideCard { width: 1fr; height: auto; margin: 0 0 1 0; color: $foreground; }
    SideCard:hover { color: $foreground; }
    SideCard > .card-head { height: 1; color: $foreground; }
    SideCard > .card-body { height: auto; color: $text-muted; padding: 0 0 0 8; }
    SideCard > .card-meter {
        width: 1fr;
        height: 1;
        color: $text-muted;
        padding: 0;
        overflow: hidden;
    }
    SideCard > .card-meter.-empty { display: none; height: 0; }
    SideCard.-boxed {
        border: round $foreground 45%;
        padding: 0 1 0 1;
        margin: 0 1 1 0;
    }
    SideCard.-bare > .card-head { display: none; height: 0; }
    SideCard.-bare > .card-body {
        padding: 0;
        height: auto;
        overflow: hidden;
    }
    """

    def __init__(
        self,
        key: str,
        title: str,
        action: str,
        *,
        id: str | None = None,
        boxed: bool = False,
        bare: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.key = key
        self.title_text = title
        self.action = action
        self._body_raw = ""
        self._wrap_as_path = False
        if boxed:
            self.add_class("-boxed")
        if bare:
            self.add_class("-bare")

    def compose(self) -> ComposeResult:
        pad = _key_markup(self.key) if self.key else (" " * _KEY_WIDTH)
        yield Static(f"{pad}{escape(self.title_text)}", classes="card-head", markup=True)
        yield Static("", classes="card-body", markup=True)
        yield Static("", classes="card-meter -empty")

    def set_body(self, text: str, *, wrap_as_path: bool = False) -> None:
        self._body_raw = text
        self._wrap_as_path = wrap_as_path
        self._paint_body()

    def on_resize(self) -> None:
        self._paint_body()

    def _paint_body(self) -> None:
        try:
            body = self.query_one(".card-body", Static)
        except Exception:  # noqa: BLE001 - not composed yet
            return
        text = self._body_raw
        if self._wrap_as_path:
            width = max(12, (self.size.width or 28) - _KEY_WIDTH)
            text = escape(wrap_path(text, width))
        body.update(text)

    def set_meter(self, text: str) -> None:
        try:
            meter = self.query_one(".card-meter", Static)
        except Exception:  # noqa: BLE001
            return
        meter.update(text)
        meter.set_class(not text, "-empty")

    def set_title(self, text: str) -> None:
        self.title_text = text
        pad = _key_markup(self.key) if self.key else (" " * _KEY_WIDTH)
        self.query_one(".card-head", Static).update(f"{pad}{escape(text)}")

    def on_click(self) -> None:
        self.app.call_later(self.app.run_action, self.action)


class Sidebar(Vertical):
    """Right column: AGI, actions, activity. Footer shortcuts live on the dock."""

    DEFAULT_CSS = """
    Sidebar {
        width: 36;
        min-width: 32;
        height: 1fr;
        border-left: vkey $border;
        padding: 2 2 0 2;
        display: none;
        background: $panel;
        overflow: hidden;
    }
    Sidebar.-visible { display: block; }
    Sidebar > #side-scroll {
        width: 1fr;
        height: 1fr;
        padding: 0;
        scrollbar-size-vertical: 1;
    }
    Sidebar #side-panel {
        width: auto;
        height: 1;
        min-height: 1;
        min-width: 0;
        margin: 0 0 1 0;
    }
    Sidebar #side-controls { height: auto; margin: 0 0 1 0; }
    Sidebar #side-model { margin: 0 0 1 0; }
    Sidebar #side-session-rows { height: auto; margin: 0 0 1 0; }
    Sidebar #side-agi { margin: 0 0 1 0; }
    Sidebar #side-actions { height: auto; margin: 0 0 1 0; }
    Sidebar #side-meta { height: auto; margin: 0; }
    Sidebar > #side-foot {
        height: auto;
        padding: 1 0 0 0;
        background: $panel;
    }
    Sidebar > #side-foot > #side-split {
        height: 1;
        margin: 1 0 1 0;
        border-top: solid $foreground 40%;
    }
    Sidebar > #side-foot > #side-version {
        width: 1fr;
        height: 1;
        min-height: 1;
        margin: 1 0 0 0;
        padding: 0;
        color: $text-muted;
        overflow: hidden;
    }
    """

    CONTROLS: tuple[tuple[str, str, str, str], ...] = (
        ("ctrl+i", "Provider", "open_settings('providers')", "side-provider"),
    )

    ACTIONS: tuple[tuple[str, str, str], ...] = (
        ("ctrl+y", "Guardrails", "open_settings('guardrails')"),
        ("ctrl+k", "Skills", "open_settings('skills')"),
        ("ctrl+e", "Security", "open_settings('security')"),
        ("ctrl+u", "Tools & MCP", "open_settings('mcp')"),
        ("ctrl+g", "Settings", "open_settings"),
        ("ctrl+p", "Commands", "command_palette"),
        ("ctrl+n", "New chat", "new_chat"),
        ("F2", "Graph", "open_graph"),
        ("F3", "Evolve", "open_evolve"),
        ("F1", "Help", "show_help"),
    )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="side-scroll"):
            yield Button("Hide panel  ctrl+b", id="side-panel", compact=True)
            with Vertical(id="side-controls"):
                for key, label, action, widget_id in self.CONTROLS:
                    yield SideAction(key, label, action, id=widget_id)
            yield SideCard("ctrl+o", "Model", "pick_model", id="side-model")
            with Vertical(id="side-session-rows"):
                yield SideAction("ctrl+t", "Mode", "pick_mode", id="side-mode")
                yield SideAction("ctrl+s", "Session", "pick_session", id="side-session")
            yield SideCard("F4", "AGI", "open_agi", id="side-agi")
            with Vertical(id="side-actions"):
                for key, label, action in self.ACTIONS:
                    yield SideAction(key, label, action)
            with Vertical(id="side-meta"):
                yield SideAction("", "Activity", "", id="side-activity")
                yield SideCard("", "Git", "open_settings('git')", id="side-git")
        with Vertical(id="side-foot"):
            yield SideCard("", "", "pick_project", id="side-workspace", bare=True)
            yield Static("", id="side-split")
            yield SideCard("ctrl+d", "Account", "open_account", id="side-account")
            yield Static("[$text-muted]navin[/]", id="side-version", markup=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._workspace_raw = ""
        self._model: dict[str, Any] = {}
        self._version = ""
        self._latest = ""

    def set_panel_label(self, visible: bool) -> None:
        self.query_one("#side-panel", Button).label = (
            "Hide panel  ctrl+b" if visible else "Show panel  ctrl+b"
        )

    @on(Button.Pressed, "#side-panel")
    def _hide_panel(self) -> None:
        self.app.call_later(self.app.run_action, "toggle_sidebar")

    def set_version(self, version: str) -> None:
        self._version = version
        self._render_version()

    def set_update_available(self, latest: str) -> None:
        """Keep the newer version in view next to the running one."""
        self._latest = latest
        self._render_version()

    def _render_version(self) -> None:
        label = f"navin  v{self._version}" if self._version else "navin"
        text = f"[$text-muted]{escape(label)}[/]"
        if self._latest and self._latest != self._version:
            text += f"  [$warning]v{escape(self._latest)} available[/]"
        self.query_one("#side-version", Static).update(text)

    def set_agi(self, text: str) -> None:
        self.query_one("#side-agi", SideCard).set_body(text)

    def set_model(
        self,
        *,
        slug: str,
        tools: int,
        tools_loaded: int,
        billed: int,
        used: int,
        window: int,
    ) -> None:
        self._model = {
            "slug": slug,
            "tools": tools,
            "tools_loaded": tools_loaded,
            "billed": billed,
            "used": used,
            "window": window,
        }
        self._paint_model()

    def _paint_model(self) -> None:
        data = self._model
        if not data:
            return
        try:
            card = self.query_one("#side-model", SideCard)
        except Exception:  # noqa: BLE001
            return
        lines = [escape(str(data.get("slug") or "no model"))]
        billed = int(data.get("billed") or 0)
        if billed:
            lines.append(f"· billed {billed:,}")
        card.set_body("\n".join(lines))
        width = 0
        try:
            width = card.query_one(".card-meter", Static).size.width
        except Exception:  # noqa: BLE001
            width = 0
        if width < 8:
            width = card.size.width or max(0, (self.size.width or 0) - 4)
        if width < 8:
            width = 32
        card.set_meter(
            context_line(int(data.get("used") or 0), int(data.get("window") or 0), width)
        )

    def set_git(self, text: str) -> None:
        body = f"• {escape(text)}" if text else ""
        self.query_one("#side-git", SideCard).set_body(body)

    def set_workspace(self, text: str) -> None:
        self._workspace_raw = text
        self._paint_workspace()

    def _paint_workspace(self) -> None:
        try:
            card = self.query_one("#side-workspace", SideCard)
        except Exception:  # noqa: BLE001
            return
        raw = (self._workspace_raw or "").replace("\\", "/").strip()
        width = card.size.width or max(0, (self.size.width or 0) - 4)
        if width < 8:
            width = 28
        card.set_body(escape(wrap_path(raw, width)))

    def on_resize(self) -> None:
        self._paint_workspace()
        self._paint_model()

    def set_control(self, widget_id: str, value: str) -> None:
        self.query_one(f"#{widget_id}", SideAction).set_value(value)

    def set_activity(self, lines: list[str]) -> None:
        last = lines[-1] if lines else "idle"
        last = last.replace("[/]", "")
        while "[" in last and "]" in last:
            start = last.find("[")
            end = last.find("]", start)
            if end < 0:
                break
            last = last[:start] + last[end + 1 :]
        self.query_one("#side-activity", SideAction).set_value(last.strip() or "idle")

    def set_account(self, text: str) -> None:
        body = "" if text in {"", "Account", "not connected"} else escape(text)
        self.query_one("#side-account", SideCard).set_body(body)
