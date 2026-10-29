# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Transcript widgets: user/assistant messages, tool calls, cards, composer."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Iterator
from operator import itemgetter
from typing import Any

from rich.cells import cell_len, set_cell_size
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.geometry import Region
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from navin.providers.model_names import bedrock_display_name, is_bedrock_model_id
from navin.tui.brand import MARK, tide_text
from navin.tui.copy_chip import CopyChip
from navin.tui.frame_scheduler import FRAME_SECONDS, FrameRequest, note_input, schedule_paint
from navin.tui.frames import paint_input
from navin.tui.markdown import install_path_styles, readable_validation_report
from navin.tui.markdown_stream import TranscriptMarkdown
from navin.tui.modes import display_user_text
from navin.tui.nodelist_fix import install as install_nodelist_fix
from navin.tui.paths import PATH_INK, looks_like_path
from navin.tui.perf import install as install_textual_perf
from navin.tui.settled import SettledMarkdown
from navin.tui.textmarkup import escape
from navin.tui.virtual import BlockRecord, RecordGap
from navin.utils.command_output import (
    COMMAND_PREVIEW_LINES,
    GIT_PREVIEW_LINES,
    CompactRowsCache,
    command_exit_code,
    compact_command_rows,
    is_exit_line,
    is_git_command,
)
from navin.utils.pasted_content import (
    allocate_paste_token,
    collapse_text_for_composer,
    expand_pasted_content,
    pasted_content_label,
    should_collapse_pasted_text,
    split_long_user_text,
)
from navin.utils.tool_hints import (
    CARD_ONLY_TOOLS,
    MAX_TRANSCRIPT_LINES,
    PREVIEW_OPEN_LINES,
    StreamRowsCache,
    activity_head_text,
    activity_label,
    activity_palette,
    activity_path_key,
    clip_transcript,
    command_summary,
    describe_explore_step,
    edit_group_key,
    file_operation_label,
    format_preview_markup_line,
    format_tool_detail,
    format_tool_preview_markup,
    format_turn_summary,
    highlight_output_statuses,
    humanize_agent_error,
    index_notice_text,
    is_validation_pending,
    preview_rows,
    redact_command,
    result_summary,
    short_run_target,
    tool_cluster_kind,
    tool_target,
    tool_verb,
)

install_path_styles()
install_textual_perf()
install_nodelist_fix()

# Coalesce bursts without making input or the engine wait for each paint.
# Streams paint on the app-wide frame clock (navin.tui.frame_scheduler).
STREAM_FRAME_SECONDS = FRAME_SECONDS
# One answer re-parses its Markdown tail at most this often; the clock's
# other frames go to tool rows, spinners and, first of all, typing.
STREAM_PAINT_SECONDS = 1 / 20
# When the process is already busy (a long turn, parallel answers, the engine
# working on the same loop), each paint and its relayout take CPU from the
# model stream itself: paint less often, down to this slowest cadence.
STREAM_PAINT_SLOW_SECONDS = 1 / 6
STREAM_PAINT_BUSY_SHARE = 0.3


def stream_paint_delay(widget: Any) -> float:
    """Next stream paint delay, from this process's CPU share since the last one."""
    wall, cpu = time.monotonic(), time.process_time()
    last = getattr(widget, "_stream_paint_clock", None)
    widget._stream_paint_clock = (wall, cpu)
    if last is None or wall - last[0] <= 0:
        return STREAM_PAINT_SECONDS
    share = (cpu - last[1]) / (wall - last[0])
    if share <= STREAM_PAINT_BUSY_SHARE:
        return STREAM_PAINT_SECONDS
    # 30% busy -> 1/20 s, fully busy (or more, with threads) -> 1/6 s.
    ratio = min(1.0, (share - STREAM_PAINT_BUSY_SHARE) / (1 - STREAM_PAINT_BUSY_SHARE))
    return STREAM_PAINT_SECONDS + ratio * (STREAM_PAINT_SLOW_SECONDS - STREAM_PAINT_SECONDS)
TOOL_OUTPUT_FRAME_SECONDS = 0.1
ACTIVITY_PAGE_SIZE = 20
# One-cell spinner for running rows: aligned with the done/failed icons.
ROW_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def content_width(widget: Widget) -> int:
    """Content width from the last layout.

    ``size`` and ``region`` resolve through the compositor's full map, which
    Textual rebuilds for every widget after each scroll. Hot paths (render
    lines, live previews, spinners) must not pay that on every frame.
    """
    width = widget.outer_size.width - widget.styles.gutter.width - widget.scrollbar_gutter.width
    return max(0, width)


def same_height_text(widget: Widget, old: str, new: str) -> bool:
    """Whether ``new`` keeps the rows ``old`` took, so no layout is needed.

    Any layout request reflows and repaints the whole screen (about 30 ms on
    a long chat). A status line whose spinner or clock ticks keeps its size:
    true only when both texts fit the width unwrapped, on as many lines.
    """
    if not widget.display:
        return True  # takes no rows; showing it again lays it out
    width = content_width(widget)
    if width <= 0:
        return False
    old_lines, new_lines = old.split("\n"), new.split("\n")
    return len(old_lines) == len(new_lines) and all(
        cell_len(line) <= width for line in (*old_lines, *new_lines)
    )


def swap_classes(widget: Widget, remove: set[str] | frozenset[str], add: set[str] | frozenset[str]) -> None:
    """Change several classes with one restyle of the widget subtree.

    Each add_class / remove_class restyles the widget and all descendants;
    a finished tool card used to pay that three times.
    """
    classes = (set(widget.classes) - set(remove)) | set(add)
    if classes != set(widget.classes):
        widget.set_classes(classes)


class LocalDisplayStyles:
    """Toggle children's ``display`` without restyling the whole subtree.

    Textual restyles a parent and every descendant when one child's display
    changes, only to refresh sibling-order pseudo classes (``:first-child``,
    ``:last-child``, ``:odd``). Navin's CSS does not use them. On the chat
    column that cost grew with the session: showing the Working line or the
    slash menu restyled every message ever painted.
    """

    def _refresh_styles(self) -> None:
        return


def forget_app_watchers(widget: Widget) -> None:
    """Drop ``widget.watch(app, ...)`` callbacks when the widget leaves.

    Textual prunes them only the next time the watched value changes (a
    theme switch), so every tool row ever removed stayed reachable from the
    app until then.
    """
    try:
        app = widget.app
    except Exception:  # noqa: BLE001 - never mounted
        return
    for callbacks in (getattr(app, "__watchers", None) or {}).values():
        if any(node is widget for node, _callback in callbacks):
            callbacks[:] = [item for item in callbacks if item[0] is not widget]


def restyle_focus(widget: Widget, *children: Widget) -> None:
    """Apply a focus change to ``widget`` and the few children its CSS styles.

    Textual's default ``watch_has_focus`` restyles the whole subtree. For a
    focusable container that is every block inside it: focusing the chat
    (or a tool group with hundreds of rows) cost seconds on a long session,
    though only the container's own ``:focus`` rules (if any) change.
    """
    if not widget.is_attached:
        return
    widget.app.stylesheet.update_nodes([widget, *children], animate=True)


# -- records: blocks kept as data while out of view (navin.tui.virtual) -----

# Plain attributes a Static-based block carries next to its content.
_STATIC_ATTRS = ("latest", "detail", "echo", "narration", "task_id", "transient")


def static_state(widget: Static) -> dict[str, Any]:
    """What rebuilds a Static block exactly: content, markup mode, attributes."""
    state: dict[str, Any] = {"content": widget.content, "markup": widget._render_markup}
    attrs = widget.__dict__
    for name in _STATIC_ATTRS:
        if name in attrs:
            state[name] = attrs[name]
    return state


def build_static(widget_type: type[Static], state: dict[str, Any]) -> Static:
    # The subclass __init__ formats its arguments into content; the content
    # is already formatted, so only the Static part runs again.
    widget = widget_type.__new__(widget_type)
    Static.__init__(widget, state["content"], markup=state["markup"])
    for name in _STATIC_ATTRS:
        if name in state:
            setattr(widget, name, state[name])
    return widget


def static_record(widget: Static) -> BlockRecord:
    return BlockRecord(
        type(widget), static_state(widget),
        lambda record: build_static(record.widget_type, record.state),
        classes=set(widget.classes),
    )


def token_label(text: str) -> str:
    """``~840 tokens`` / ``~1.2k tokens``: the unit a model is billed in.

    Four characters per token is the usual estimate; exact tokenizing on
    every streamed frame would cost more than the number is worth.
    """
    tokens = max(1, round(len(text) / 4)) if text.strip() else 0
    if tokens >= 1000:
        return f"~{tokens / 1000:.1f}k tokens"
    return f"~{tokens} tokens"


# Every running row's spinner advances on one shared tick, in one paint pass.
ROW_SPIN_SECONDS = 0.12


def running_tool_rows(app: Any) -> dict[Any, None]:
    """Rows whose spinner is live, in start order (one registry per app)."""
    rows = getattr(app, "_running_tool_rows", None)
    if rows is None:
        rows = {}
        app._running_tool_rows = rows
    return rows


def tick_running_rows(app: Any) -> None:
    """Advance every live row spinner at once.

    NavinApp calls this from its own status tick; another host gets a single
    app-level timer that stops itself when no row is running. Either way a
    row never owns a timer, so 12 parallel reads cost one pass, not 12.
    """
    rows = getattr(app, "_running_tool_rows", None)
    if not rows:
        timer = getattr(app, "_row_spin_timer", None)
        if timer is not None:
            timer.stop()
            app._row_spin_timer = None
        return
    with app.batch_update():
        for row in list(rows):
            row._tick()


def on_screen(widget: Widget) -> bool:
    """Whether a widget is in the current composition, without a full map."""
    try:
        screen = widget.screen
    except Exception:  # noqa: BLE001 - detached
        return False
    if screen is not widget.app.screen:
        return False
    return widget in screen._compositor.visible_widgets

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
    "computer": "▣",
    "spawn": "⑂",
    "ask_user": "?",
    "message": "›",
    "cron": "◷",
    "memory": "◈",
    "todo": "☑",
    "board": "▣",
    "git": "⌥",
    "skill": "✦",
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
    "computer": "#00E5FF",
    "board": "#7C4DFF",
    "todo": "#7C4DFF",
    "spawn": "#FF6D00",
    "git": "#00C853",
    "memory": "#536DFE",
    "cron": "#00B8FF",
    "ask_user": "#FF4081",
    "message": "#FF4081",
    "skill": "#00E5A0",
}


def tool_icon(name: str) -> str:
    base = name.lower()
    for key, icon in _TOOL_ICONS.items():
        if base == key or base.startswith(key):
            return icon
    if base.startswith("mcp") or "__" in base:
        return "⧉"
    return "•"


def tool_ink_class(name: str, arguments: dict | None = None) -> str:
    """CSS class for the tool family color (no Rich markup, so no double paint)."""
    verb = tool_verb(name)
    if verb == "run":
        target = tool_target(arguments or {})
        if target.startswith("git"):
            return "-ink-git"
    return {
        "read": "-ink-read",
        "list": "-ink-read",
        "grep": "-ink-search",
        "find": "-ink-search",
        "run": "-ink-run",
        "edit": "-ink-edit",
        "create": "-ink-edit",
        "check": "-ink-check",
        "search": "-ink-web",
        "fetch": "-ink-web",
        "browse": "-ink-web",
        "board": "-ink-board",
        "todo": "-ink-board",
        "git": "-ink-git",
        "ask": "-ink-ask",
    }.get(verb, "-ink-run")


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
    """Escape model or tool text for Textual markup."""
    return escape(text)


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
_PATH_COLOR = PATH_INK
_CMD_COLOR = "#FFB000"
_SEARCH_COLOR = "#FF2E93"
_URL_COLOR = "#E040FB"
_OP_COLOR = "#FF8F1C"


def _arg_ink(key: str, raw: str) -> str:
    if key in _CMD_KEYS:
        return _CMD_COLOR
    if key in _PATHISH_KEYS or looks_like_path(raw):
        return _PATH_COLOR
    if key in _SEARCH_KEYS:
        return _SEARCH_COLOR
    if key in _URL_KEYS:
        return _URL_COLOR
    return _OP_COLOR


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


_SENTENCE_BREAK_RE = re.compile(r"([.!?])\s+(?=[A-ZÉÈÀÂÊÎÔÛÇ«\"'])")


PREVIEW_CHARS = 140

_CLIENT_PROMPT_RE = re.compile(
    r"(?is)"
    r"("
    r"\b(choisis|choose|which option|quelle option|expliques?-moi|tell me|dis-moi|"
    r"que veux-tu|what do you (?:want|prefer)|pick one)\b"
    r"|^\s*(?:[1-9][.)]\s+\S.+\n\s*[2-9][.)]\s+\S)"
    r"|^\s*[-*]\s+\S.+\n\s*[-*]\s+\S"
    r")"
)


def looks_like_client_prompt(text: str) -> bool:
    """True when the model is asking the user (question, choices), not thinking."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _CLIENT_PROMPT_RE.search(raw):
        return True
    if raw.endswith("?") and len(raw) <= 400 and raw.count("?") <= 3:
        return True
    return False


# Narration between tool steps stays whole; only a runaway one is capped.
NARRATION_CHARS = 600


# Markdown structure in an interim message: a heading, list, table, quote or
# code fence line, bold / inline code, or several paragraphs.
_RICH_LINE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>\s|```)", re.M)


def is_rich_narration(text: str) -> bool:
    """True when an interim message carries Markdown worth rendering as such.

    Flattening it into the one-line narration turned tables, lists, headings,
    bold and code into a grey run-on line ("· # Title | a | b | | --- |...").
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if "```" in stripped or "**" in stripped or "`" in stripped or _RICH_LINE.search(stripped):
        return True
    return len([part for part in re.split(r"\n\s*\n", stripped) if part.strip()]) >= 2


def narration_text(text: str, limit: int = NARRATION_CHARS) -> str:
    """The full interim message as one wrapped line, capped with "…"."""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    cut = compact[: max(limit - 1, 1)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def markdown_plain(text: str) -> str:
    """Markdown as read on screen: no `#`, `**`, backticks, list or table marks."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if re.fullmatch(r"[|:\-\s]+", line) or line.startswith("```"):
            continue
        line = re.sub(r"^#{1,6}\s+|^>\s?|^(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?", "", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", line)
        line = re.sub(r"`+([^`]*)`+", r"\1", line)
        if line.startswith("|"):
            line = " · ".join(cell.strip() for cell in line.strip("|").split("|"))
        out.append(line)
    return "\n".join(out)


def assistant_preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """One short line for the folded reflection view."""
    compact = " ".join(markdown_plain(text).split())
    if not compact:
        return ""
    match = re.match(r"^(.+?[.!?])(?:\s|$)", compact)
    if match and 12 <= len(match.group(1)) <= limit:
        return match.group(1)
    if len(compact) <= limit:
        return compact
    cut = compact[: max(limit - 1, 1)]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def readable_assistant_markdown(text: str) -> str:
    """Keep real markdown. Break a single huge line into paragraphs."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return raw
    report = readable_validation_report(raw)
    if report is not None:
        return report
    if "\n" in raw.strip():
        return raw
    if len(raw) < 220:
        return raw
    return _SENTENCE_BREAK_RE.sub(r"\1\n\n", raw)


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
    """The prompt surface identifies user messages without a separate label."""

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    UserMessage {
        height: auto;
        margin: 1 0 1 0;
        padding: 0 1;
        border: none;
        background: $panel;
    }
    UserMessage > .user-body {
        height: auto;
        min-height: 1;
        color: $foreground;
        padding: 0;
        background: transparent;
        text-wrap: wrap;
    }
    UserMessage > .user-paste-chip {
        height: 1;
        width: auto;
        color: $text-muted;
        background: transparent;
        padding: 0 1;
    }
    UserMessage > .user-expand-body {
        display: none;
    }
    UserMessage.-expanded > .user-expand-body {
        display: block;
    }
    """

    _BODY_CHUNK_LINES = 80

    def __init__(self, text: str, *, show_head: bool = True) -> None:
        super().__init__()
        self.raw_text = clip_transcript(display_user_text(text))
        self._expanded = False
        self._show_head = show_head

    def _body_chunks(self, text: str, *extra_classes: str) -> ComposeResult:
        lines = text.splitlines(keepends=True) or ([text] if text else [""])
        classes = " ".join(("user-body", *extra_classes))
        if len(lines) <= self._BODY_CHUNK_LINES:
            yield Static(text, classes=classes, markup=False)
            return
        # Several Static children report height reliably. One huge Static is
        # often clipped on Windows Terminal, so the start of a paste vanishes.
        step = self._BODY_CHUNK_LINES
        for index in range(0, len(lines), step):
            yield Static("".join(lines[index : index + step]), classes=classes, markup=False)

    def compose(self) -> ComposeResult:
        prefix, rest = split_long_user_text(self.raw_text)
        if rest is None:
            yield from self._body_chunks(self.raw_text)
            return
        if prefix:
            yield Static(prefix, classes="user-body", markup=False)
        yield Static(pasted_content_label(len(rest)), classes="user-paste-chip", markup=False)
        yield from self._body_chunks(rest, "user-expand-body")

    def on_click(self, event: events.Click) -> None:
        if split_long_user_text(self.raw_text)[1] is None:
            return
        self._expanded = not self._expanded
        self.set_class(self._expanded, "-expanded")
        event.stop()

    def copy_text(self) -> str:
        return clip_transcript(self.raw_text)

    def snapshot_record(self) -> BlockRecord | None:
        return BlockRecord(
            type(self),
            {"raw_text": self.raw_text, "show_head": self._show_head, "expanded": self._expanded},
            UserMessage._from_record,
            classes=set(self.classes),
            copy=lambda record: clip_transcript(record.state["raw_text"]),
        )

    @staticmethod
    def _from_record(record: BlockRecord) -> UserMessage:
        state = record.state
        message = record.widget_type("", show_head=state["show_head"])
        message.raw_text = state["raw_text"]
        message._expanded = state["expanded"]
        return message


class ReasoningBlock(Vertical):
    """Model thinking, folded to one line: ``Thinking…`` then ``Thought for 4s``.

    The live animation belongs to the working line under the chat; this row
    stays still and keeps the full text one click away.
    """

    DEFAULT_CSS = """
    ReasoningBlock {
        height: auto;
        margin: 0;
        padding: 0;
    }
    ReasoningBlock > .reasoning-head {
        color: $text-muted;
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
        self._paint_timer: FrameRequest | None = None
        self._segment_started = time.monotonic()
        self._elapsed = 0.0

    def compose(self) -> ComposeResult:
        yield Static(self._head_markup(), classes="reasoning-head")
        body = escape("".join(self._buffer)[-6000:]) if self._open and self._done else ""
        yield Static(body, classes="reasoning-body")

    def snapshot(self) -> dict[str, Any]:
        return {"buffer": "".join(self._buffer), "elapsed": self.elapsed, "open": self._open}

    @classmethod
    def from_snapshot(cls, state: dict[str, Any]) -> ReasoningBlock:
        block = cls()
        block._buffer = [state["buffer"]] if state["buffer"] else []
        block._done = True
        block._elapsed = state["elapsed"]
        block._open = state["open"]
        return block

    @property
    def elapsed(self) -> float:
        running = 0.0 if self._done else time.monotonic() - self._segment_started
        return self._elapsed + running

    def _head_markup(self) -> str:
        hint = "[dim] (click to collapse)[/dim]" if self._open else "[dim] (click to expand)[/dim]"
        if not self._done:
            return f"∴ Thinking…{hint if self._buffer else ''}"
        return f"∴ Thought for {format_elapsed(max(1.0, self.elapsed))}{hint}"

    def append(self, text: str) -> None:
        self._buffer.append(text)
        if self._paint_timer is None:
            self._paint_timer = schedule_paint(self, "reasoning", self._paint)

    def reopen(self) -> None:
        """Continue the same thought: a new segment with nothing in between."""
        if not self._done:
            return
        self._done = False
        self._segment_started = time.monotonic()
        self._paint()

    def _paint(self) -> None:
        self._paint_timer = None
        if not self.is_attached:
            return
        if not self._done and self.screen is not self.app.screen:
            self._paint_timer = schedule_paint(self, "reasoning", self._paint, delay=0.1)
            return
        if self._open:
            joined = "".join(self._buffer)
            self.query_one(".reasoning-body", Static).update(escape(joined[-6000:]))
        self.query_one(".reasoning-head", Static).update(self._head_markup())

    def finish(self) -> None:
        if not self._done:
            self._elapsed += time.monotonic() - self._segment_started
        self._done = True
        if self._paint_timer is not None:
            self._paint_timer.stop()
            self._paint_timer = None
        self._paint()

    def toggle(self) -> None:
        self._open = not self._open
        self.set_class(self._open, "-open")
        if self._paint_timer is not None:
            self._paint_timer.stop()
            self._paint_timer = None
        self._paint()

    def on_click(self) -> None:
        self.toggle()


_BELL_AND_ERASE_RE = re.compile(r"\x07|\x1b\[[0-2]?K|\x1b\[\?25[hl]")


def terminal_lines(text: str) -> list[str]:
    """Output lines as a terminal leaves them on screen.

    A carriage return redraws its line (progress bars, spinners), so only the
    last redraw is kept instead of one row per frame; bells and erase codes
    are dropped.
    """
    text = _BELL_AND_ERASE_RE.sub("", text.replace("\r\n", "\n"))
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            frames = [frame for frame in line.split("\r") if frame.strip()]
            line = frames[-1] if frames else ""
        lines.append(line)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


class ToolCall(LocalDisplayStyles, Vertical, can_focus=True):
    """One tool invocation: header line + expandable result."""

    DEFAULT_CSS = """
    ToolCall {
        height: auto;
        margin: 0;
        padding: 0;
        background: $background;
    }
    ToolCall > .tool-head {
        background: $background;
        color: $foreground;
        height: auto;
        text-style: none;
    }
    ToolCall:focus > .tool-head { background: $surface; }
    ToolCall.-validation-pending > .tool-head { color: $warning; }
    ToolCall > .tool-summary {
        height: auto;
        color: $text-muted;
        background: $background;
    }
    ToolCall.-cancelled > .tool-head { color: $text-muted; }
    ToolCall > .tool-output {
        height: auto;
        max-height: 34;
        overflow-x: hidden;
        overflow-y: auto;
        /* Scrolls with the wheel and PageUp/PageDown; no bar inside the card. */
        scrollbar-size-vertical: 0;
        scrollbar-gutter: auto;
        background: $background;
    }
    ToolCall .tool-body {
        height: auto;
        padding: 0;
        color: #E8E8E8;
        background: $background;
        text-style: none;
    }
    ToolCall .tool-command {
        height: auto;
        color: $text-muted;
        background: $background;
    }
    ToolCall > .tool-actions {
        width: 1fr;
        height: auto;
        background: $background;
    }
    ToolCall > .tool-actions > CopyChip { margin: 0 0 0 1; }
    ToolCall Button.tool-more.-style-default {
        height: 1;
        min-height: 1;
        min-width: 0;
        width: auto;
        margin: 0;
        padding: 0 1;
        border: none;
        background: $background;
        color: $text-muted;
        text-style: none;
    }
    ToolCall Button.tool-more.-style-default:hover, ToolCall Button.tool-more.-style-default:focus {
        background: $panel;
        color: $foreground;
        text-style: none;
    }
    ToolCall.-cluster { margin: 0; }
    """

    ALLOW_SELECT = True
    SPINNER_STEPS = 12
    BINDINGS = [
        Binding("enter,space", "toggle", "Expand / collapse", show=False),
        Binding("f", "show_full", "Full output", show=False),
        Binding("pageup", "page_output(-1)", "Previous output page", show=False),
        Binding("pagedown", "page_output(1)", "Next output page", show=False),
        Binding("home", "output_edge(False)", "Start of output", show=False),
        Binding("end", "output_edge(True)", "End of output", show=False),
    ]

    def __init__(self, call_id: str, name: str, arguments: dict[str, Any]) -> None:
        super().__init__()
        self.call_id = call_id
        self.call_ids = [call_id] if call_id else []
        self.tool_name = name
        self.arguments = arguments if isinstance(arguments, dict) else {}
        self.group_key = edit_group_key(name, self.arguments)
        self.phase = "start"
        self.result: Any = None
        self.error: str | None = None
        self._output_lines: list[str] = []
        self._output_buffer = ""
        self._pending_output: list[str] = []
        self._pending_output_chars = 0
        self.percent: float | None = None
        self.expected_s: float | None = None
        self.added = 0
        self.removed = 0
        self.edit_count = 1
        self.diff_text = ""
        self.file_path = ""
        self.file_operation = ""
        self.file_recorded = False
        self.file_truncated = False
        self.file_binary = False
        self._show_full = False
        self._spin = 0
        self._open = False
        self._output_mounted = False
        self.cluster_kind = ""
        self._cluster_owner: ToolCluster | None = None
        self.tree_mark = ""
        self._spin_app: Any = None
        self._output_timer: FrameRequest | None = None
        self._painted_head: tuple[str, bool] | None = None
        self._body_size: tuple[int, int] | None = None
        self._preview_overflow = False
        # Live output only (created on its first paint, dropped at the end):
        # thousands of finished rows in a long session keep no cache objects.
        self._command_summary_cache: CompactRowsCache | None = None
        self._stream_rows_cache: StreamRowsCache | None = None
        self._body_revision = 0
        self._painted_body_key: tuple | None = None
        if tool_verb(name) in {"edit", "create"}:
            self._open = True
        self.add_class("-running")
        self.add_class(tool_ink_class(name, self.arguments))

    def compose(self) -> ComposeResult:
        yield Static(self._plain_head(), classes="tool-head", markup=False)
        summary = Static("", classes="tool-summary", markup=False)
        summary.display = False
        yield summary
        if self._open:
            self._output_mounted = True
            with VerticalScroll(classes="tool-output"):
                yield from self._output_widgets()
            with Horizontal(classes="tool-actions"):
                yield from self._action_widgets()

    def _output_widgets(self) -> list[Widget]:
        return [
            Static("", classes="tool-command", markup=False),
            Static("", classes="tool-body", markup=False),
        ]

    def _action_widgets(self) -> list[Widget]:
        return [
            Button("Show full output", classes="tool-more", compact=True),
            # The command and its full retained output, one click.
            CopyChip(self.copy_text, classes="tool-copy"),
        ]

    def _ensure_output(self) -> None:
        """Mount the output viewport and its actions on the first open.

        Most rows stay folded for their whole life. Their six output widgets
        (about 1,000 tracked objects) made every row cost three times what
        it shows, and full garbage collections over a long turn froze the
        CLI for a second. Mounted synchronously, so queries see them at once.
        """
        if self._output_mounted:
            return
        self._output_mounted = True
        viewport = VerticalScroll(classes="tool-output")
        actions = Horizontal(classes="tool-actions")
        self.mount(viewport, actions)
        viewport.mount(*self._output_widgets())
        actions.mount(*self._action_widgets())

    def on_mount(self) -> None:
        # Grouped rows animate too: each running step shows its own spinner,
        # driven by the one app-level tick (a finished replayed row gets none).
        if self.phase in {"start", "output"}:
            self._start_spinning()
        self.watch(self.app, "theme", self._theme_changed, init=False)
        self._refresh_head()
        self._refresh_body()

    def watch_has_focus(self, _has_focus: bool) -> None:
        # Only ``ToolCall:focus > .tool-head`` depends on focus.
        restyle_focus(self, *self.query_children(".tool-head"))

    def on_unmount(self) -> None:
        self._stop_spinning()
        if self._output_timer is not None:
            self._output_timer.stop()
            self._output_timer = None
        forget_app_watchers(self)

    @property
    def spinning(self) -> bool:
        """Whether the shared tick animates this row."""
        return self._spin_app is not None

    def _start_spinning(self) -> None:
        if self._spin_app is not None or not self.is_attached:
            return
        app = self.app
        self._spin_app = app
        running_tool_rows(app)[self] = None
        if not getattr(app, "drives_row_spinners", False) and getattr(app, "_row_spin_timer", None) is None:
            app._row_spin_timer = app.set_interval(ROW_SPIN_SECONDS, lambda: tick_running_rows(app))

    def _stop_spinning(self) -> None:
        app, self._spin_app = self._spin_app, None
        if app is not None:
            running_tool_rows(app).pop(self, None)

    def _theme_changed(self, _theme: str) -> None:
        self._refresh_head()
        self._refresh_body()

    def _refresh_head(self, *, notify_cluster: bool = True, animation_only: bool = False) -> None:
        if not self.is_mounted:
            if notify_cluster:
                self._notify_cluster()
            return
        try:
            summary = self._summary_text()
            inline = bool(self.cluster_kind)
            key = (self._plain_head(), self.app.current_theme.dark, summary)
            if key != self._painted_head:
                head = activity_head_text(key[0], dark=key[1])
                if inline and summary:
                    palette = activity_palette(key[1])
                    # "Semantic index is building in the background ..." is the
                    # project's intelligence coming online: in the index colour,
                    # not greyed out like a line count.
                    notice = summary == index_notice_text(self.tool_name, self.result, self.output_lines)
                    head.append(f"  {summary}", style=f"bold {palette['index']}" if notice else palette["muted"])
                head_widget = self.query_one(".tool-head", Static)
                old = str(head_widget.content)
                # A finished step swaps its spinner for a check: same row.
                head_widget.update(
                    head,
                    layout=not (animation_only or same_height_text(head_widget, old, head.plain)),
                )
                line = self.query_one(".tool-summary", Static)
                below = "" if inline or not summary else f"  └ {summary}"
                if str(line.content) != below:
                    line.update(below)
                line.display = bool(below)
                self._painted_head = key
        except Exception:  # noqa: BLE001 - children not composed yet
            pass
        if notify_cluster:
            self._notify_cluster()

    def _notify_cluster(self) -> None:
        if self._cluster_owner is not None:
            self._cluster_owner.request_head_refresh()
            return
        node = self.parent
        while node is not None:
            if isinstance(node, ToolCluster):
                node._refresh_head()
                return
            node = getattr(node, "parent", None)

    def _tick(self) -> None:
        if self.phase in {"start", "output"} and on_screen(self):
            self._spin = (self._spin + 1) % len(ROW_SPINNER)
            self._refresh_head(notify_cluster=False, animation_only=True)

    def _status_glyph(self) -> str:
        """One cell: spinner while running, green check, red cross."""
        if self.phase in {"start", "output"}:
            return ROW_SPINNER[self._spin % len(ROW_SPINNER)]
        if self.validation_pending:
            return "!"
        if self.phase == "error":
            return "×"
        if self.phase == "end":
            return "✓"
        return "•"

    @property
    def validation_pending(self) -> bool:
        return self.phase == "error" and is_validation_pending(self.tool_name, self.error)

    def _plain_head(self) -> str:
        if self.cluster_kind == "explore":
            label = describe_explore_step(self.tool_name, self.arguments)
        else:
            label = activity_label(
                self.tool_name,
                self.arguments,
                phase=self.phase,
                added=self.added,
                removed=self.removed,
                operation=self.file_operation,
                path=self.file_path,
                counts_known=not self.file_binary,
                error=self.error,
            )
        if getattr(self, "_command_below", False):
            # The full command is printed under the heading: keep the heading
            # to its first words instead of repeating 160 characters of it.
            raw = self.arguments.get("command") or self.arguments.get("cmd")
            if isinstance(raw, str):
                label = label.replace(
                    command_summary(raw.strip()), short_run_target(redact_command(raw.strip())),
                )
        if self.percent is not None and self.phase in {"start", "output", "error"}:
            # While it runs, and where a failure stopped it; once done, the
            # icon and the outcome line already say it.
            from navin.utils.task_progress import progress_bar

            label += "  " + progress_bar(self.percent)
        if self.expected_s and self.phase in {"start", "output"}:
            from navin.quality.test_timing import duration_hint

            label += "  " + duration_hint(self.expected_s)
        glyph = self._status_glyph()
        if self.cluster_kind:
            mark = self.tree_mark
            if self.cluster_kind == "explore" and self.phase == "cancelled":
                label += "  (cancelled)"
            return f"{mark}{glyph} {label}"
        return f"{glyph} {label}".replace("\n", "\n  │ ")

    def _summary_text(self) -> str:
        """The outcome under a finished row (``120 lines``, ``exit 0 · 4 lines``)."""
        if self.phase not in {"end", "error"} or self.file_recorded:
            return ""
        key = (self._body_revision, self.phase)
        cached = getattr(self, "_summary_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        text = result_summary(
            self.tool_name, self.arguments, self.result, self.output_lines,
            phase=self.phase, error=self.error,
        )
        self._summary_cache = (key, text)
        return text

    def _head_text(self) -> str:
        return self._plain_head()

    # Everything a finished card needs to be built again off its record.
    _SNAPSHOT_FIELDS = (
        "phase", "result", "error", "percent", "added", "removed", "edit_count",
        "diff_text", "file_path", "file_operation", "file_recorded", "file_truncated",
        "file_binary", "_show_full", "_open", "cluster_kind", "tree_mark",
    )

    def snapshot(self) -> dict[str, Any]:
        """The card as data (results and arguments are shared, not copied)."""
        self._flush_output_buffer()
        data = {name: getattr(self, name) for name in self._SNAPSHOT_FIELDS}
        data.update(
            call_id=self.call_id, call_ids=list(self.call_ids), tool_name=self.tool_name,
            arguments=self.arguments, output_lines=self._output_lines,
            classes=frozenset(self.classes),
        )
        return data

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> ToolCall:
        card = cls(data["call_id"], data["tool_name"], data["arguments"])
        for name in cls._SNAPSHOT_FIELDS:
            setattr(card, name, data[name])
        card.call_ids = list(data["call_ids"])
        card._output_lines = list(data["output_lines"])
        card._output_buffer = "\n".join(card._output_lines)
        card.set_classes(set(data["classes"]))
        return card

    def continuation(self) -> ToolCall:
        """Copy a live card's data without reusing its widget or timers."""
        self._flush_output_buffer()
        card = ToolCall(self.call_id, self.tool_name, dict(self.arguments))
        for name in (
            "phase", "result", "error", "percent", "added", "removed",
            "edit_count", "diff_text", "file_path", "file_operation",
            "file_recorded", "file_truncated", "file_binary", "_show_full", "_open",
        ):
            setattr(card, name, getattr(self, name))
        card.call_ids = list(self.call_ids)
        card._output_buffer = self._output_buffer
        card._output_lines = list(self._output_lines)
        return card

    def apply(
        self, *, phase: str, result: Any = None, error: str | None = None,
        output: str | None = None, percent: float | None = None,
        output_mode: str = "delta",
    ) -> None:
        from navin.utils.task_progress import parse_progress_from_output

        if percent is None and output and tool_verb(self.tool_name) != "run":
            # A shell's `[100%]` or `60%` is output, not a progress report:
            # parsing it drew a bar that jumped on every streamed chunk.
            percent = parse_progress_from_output(output).get("percent")
        progress_changed = percent is not None and percent != self.percent
        if percent is not None:
            self.percent = percent
        if output:
            self._body_revision += 1
            if output_mode == "snapshot":
                self._output_buffer = ""
                self._pending_output.clear()
                self._pending_output_chars = 0
            self._pending_output.append(output)
            self._pending_output_chars += len(output)
            if self._pending_output_chars >= 64_000:
                self._flush_output_buffer()
        if phase == "output":
            self.phase = "output"
            if not self._open and not self.cluster_kind and (
                tool_verb(self.tool_name) == "run" or self.tool_name == "test_run"
            ):
                # A running command shows its output live under its row.
                self._open = True
        elif phase in {"end", "error", "cancelled"}:
            self._body_revision += 1
            if tool_verb(self.tool_name) == "run" or self.tool_name == "test_run":
                code = command_exit_code(result)
                if code is not None and phase == "end":
                    if code == 0 and self.percent is not None:
                        # A bar only on a command that reported its progress.
                        self.percent = 100
                    if code != 0:
                        phase = "error"
            self.phase = phase
            self.result = result
            self.error = error
            self._stop_spinning()
            swap_classes(
                self,
                {"-running", "-ok", "-error", "-cancelled", "-validation-pending"},
                {"-validation-pending" if self.validation_pending else "-ok" if phase == "end" else f"-{phase}"},
            )
            self._reveal_if_preview()
        if phase == "output":
            if progress_changed:
                self._refresh_head()
            if self.is_attached and self._output_timer is None:
                self._output_timer = self._schedule_output_paint()
            if not self.is_attached:
                self._notify_cluster()
            return
        if self._output_timer is not None:
            self._output_timer.stop()
        self._paint_output()

    @property
    def output_lines(self) -> list[str]:
        self._flush_output_buffer()
        return self._output_lines

    def _flush_output_buffer(self) -> None:
        if not self._pending_output:
            return
        old = self._output_buffer
        buffer = self._output_buffer = old + "".join(self._pending_output)
        self._pending_output.clear()
        self._pending_output_chars = 0
        # Complete lines never change: re-split only from the start of the
        # old last line (it may have grown or been redrawn). The same line
        # objects survive, so the preview caches compare them by identity.
        start = old.rfind("\n") + 1
        tail = terminal_lines(old[start:])
        prefix = len(self._output_lines) - len(tail)
        if old and prefix == old.count("\n", 0, start) and self._output_lines[prefix:] == tail:
            lines = self._output_lines[:prefix] + terminal_lines(buffer[start:])
        else:
            lines = terminal_lines(buffer)
        self._output_lines = lines[-MAX_TRANSCRIPT_LINES:]
        drop = len(lines) - MAX_TRANSCRIPT_LINES
        if 0 < drop <= 64:
            # Cut the dropped lines off the raw text, without joining 5000.
            cut = -1
            for _ in range(drop):
                cut = buffer.find("\n", cut + 1)
            self._output_buffer = buffer[cut + 1:]
        elif drop > 0:
            trailing = "\n" if buffer.endswith("\n") else ""
            self._output_buffer = "\n".join(self._output_lines) + trailing

    def _paint_output(self) -> None:
        self._output_timer = None
        if not self.is_attached:
            self._notify_cluster()
            return
        if self._defer_output_paint():
            # Keep all output in memory, but don't parse and style thousands
            # of hidden lines while the user types or opens a picker. The
            # latest output paints as soon as this activity comes into view.
            self._output_timer = self._schedule_output_paint()
            return
        self._flush_output_buffer()
        if self.phase != "output":
            self._refresh_head()
        self._refresh_body()

    def _schedule_output_paint(self) -> FrameRequest | None:
        # Live logs repaint ten times a second on the app's shared frame
        # clock: parallel commands share its frames instead of each waking
        # its own timer between the user's key presses.
        return schedule_paint(
            self, "output", self._paint_stream_output, delay=TOOL_OUTPUT_FRAME_SECONDS, yield_to_input=True,
        )

    async def _paint_stream_output(self) -> None:
        # Async on purpose: the frame clock runs these previews one at a
        # time within its budget and lets a queued key paint between them.
        if not self.is_attached:
            self._output_timer = None
            return
        self._paint_output()

    def _defer_output_paint(self) -> bool:
        return (
            self.phase == "output" and self._preview_overflow
            and self._body_size is not None and self._body_size[1] >= COMMAND_PREVIEW_LINES
            and not on_screen(self)
        )

    def adopt(self, call_id: str, arguments: dict[str, Any] | None) -> None:
        """Fold another edit of the same file into this row."""
        if call_id and call_id not in self.call_ids:
            self.call_ids.append(call_id)
            self.edit_count = len(self.call_ids)
        self.call_id = call_id or self.call_id
        self._body_revision += 1
        if isinstance(arguments, dict) and arguments:
            merged = dict(self.arguments) if isinstance(self.arguments, dict) else {}
            merged.update(arguments)
            self.arguments = merged
        swap_classes(self, {"-ok", "-error"}, {"-running"})
        self.phase = "start"
        self._start_spinning()

    def copy_text(self) -> str:
        # The detail formatter already caps the output. Capping it again
        # after adding the heading would discard the newest retained lines.
        return (
            self._plain_head() + "\n"
            + (self._command_details() + "\n" if self._command_details() else "")
            + format_tool_detail(
                self.tool_name,
                self.arguments if isinstance(self.arguments, dict) else {},
                result=self.result,
                error=self.error,
                output_lines=self.output_lines,
                diff_text=self.diff_text,
                include_run_output=True,
            )
            + ("\nDiff truncated by source." if self.file_truncated else "")
        )

    def _command_details(self) -> str:
        if tool_verb(self.tool_name) != "run":
            return ""
        raw = self.arguments.get("command") or self.arguments.get("cmd")
        if not isinstance(raw, str):
            return ""
        command = redact_command(raw.strip())
        # The heading shows a short form; print the exact command under it
        # whenever the two differ.
        return command if short_run_target(command) != command else ""

    def _refresh_body(self) -> None:
        if not self.is_mounted:
            return
        if not self.display or (self._cluster_owner is not None and not self._cluster_owner.display):
            return
        if self.screen is not self.app.screen or self._defer_output_paint():
            # Resize and theme messages may have been queued before a modal
            # opened or output scrolled away. Defer hidden output there too.
            if self._output_timer is None:
                self._output_timer = self._schedule_output_paint()
            return
        if not self._open and not self._output_mounted:
            self._painted_body_key = None
            return
        if self._open:
            self._ensure_output()
        try:
            body = self.query_one(".tool-body", Static)
            viewport = self.query_one(".tool-output", VerticalScroll)
        except Exception:  # noqa: BLE001
            return
        if not self._open:
            self._painted_body_key = None
            body.display = False
            self.query_one(".tool-command", Static).display = False
            viewport.display = False
            self.query_one(".tool-more", Button).display = False
            self.query_one(".tool-copy", CopyChip).display = False
            self.query_one(".tool-actions").display = False
            return
        # Before the first layout this is 0: the preview uses its minimum
        # width and on_resize repaints at the real one.
        width = content_width(viewport) or content_width(self)
        body_key = (
            self._body_revision, width, self.app.current_theme.dark, self._show_full,
            self.file_path, self.file_operation, self.file_binary, self.file_truncated,
            self.added, self.removed,
        )
        if body_key == self._painted_body_key:
            return
        if self.phase == "output" and self._stream_rows_cache is None:
            self._stream_rows_cache, self._command_summary_cache = StreamRowsCache(), CompactRowsCache()
        rows = preview_rows(
            self.tool_name, self.arguments, result=self.result, error=self.error,
            output_lines=self.output_lines, diff_text=self.diff_text,
            include_run_output=True, cache=self._stream_rows_cache,
        )
        command_output = tool_verb(self.tool_name) == "run" or self.tool_name in {"test_run", "git"}
        git_output = command_output and is_git_command(self.tool_name, self.arguments)
        # One C-level pass: a live log holds thousands of rows per paint.
        changed_rows = not set(map(itemgetter(1), rows)).isdisjoint(("add", "del"))
        compact = command_output and not git_output and not self.diff_text and not changed_rows
        preview_limit = GIT_PREVIEW_LINES if git_output else PREVIEW_OPEN_LINES
        diagnostic = self.phase in {"error", "cancelled"} and not command_output
        if diagnostic:
            preview_limit = 3
            summary = rows[:preview_limit]
            compact = True
        else:
            summary = compact_command_rows(rows, cache=self._command_summary_cache) if compact else rows[:preview_limit]
        finished = self.phase in {"end", "error", "cancelled"}
        status_only = False
        has_diff = bool(self.diff_text) or changed_rows
        if finished and not command_output and not has_diff and not diagnostic and not self._show_full and rows:
            # Browser consoles, fetched pages, search hits: one heading line,
            # the payload behind "Show output". Only diffs stay inline.
            summary = []
            status_only = True
        if diagnostic and not self._show_full:
            # A failed step that is not a command: the red cross and the
            # short reason under the row say it; the raw error stays behind
            # "Show error details".
            summary = []
            status_only = True
        elif compact and finished and not self._show_full:
            # A finished command reads as its row, its outcome line (exit
            # code, line count) and the last line or two of output. The
            # full output is one click (or F) away.
            errors = [row for row in summary if row[1] == "failure" and not is_exit_line(row[2])]
            totals = [row for row in summary if _TEST_TOTALS.search(row[2]) and row not in errors]
            kept = [*errors[-1:], *totals[-1:]] or [
                row for row in summary if row[2].strip() and not is_exit_line(row[2])
            ][-2:]
            # No 1, 2 gutter: these are the last lines of the output, not
            # source lines, and the numbers read as noise.
            summary = [(None, row[1], row[2]) for row in kept[-2:]]
            status_only = True
        self._preview_overflow = (
            len(summary) != len(rows) or any(shown[2] != row[2] for shown, row in zip(summary, rows))
            or (compact and any(cell_len(row[2]) > max(1, width - 8) for row in summary))
        )
        limit = MAX_TRANSCRIPT_LINES if self._show_full else len(summary) or 1
        text = format_tool_preview_markup(
            self.tool_name,
            {**self.arguments, **({"path": self.file_path} if self.file_path else {})},
            result=self.result,
            error=self.error,
            output_lines=self.output_lines,
            diff_text=self.diff_text,
            limit=limit,
            width=width,
            dark=self.app.current_theme.dark,
            include_run_output=True,
            rows=rows if self._show_full else summary,
            compact=compact and not self._show_full,
        )
        note = ""
        if self.file_truncated:
            note = "Diff truncated by source. Counts cover the whole change."
        elif self.file_binary and self.phase == "end":
            note = "No text preview."
        elif self.file_recorded and not rows:
            if self.file_operation == "unchanged":
                note = "No content changes."
            elif self.added or self.removed:
                note = "Diff unavailable in this saved activity."
            else:
                note = "Empty file."
        if note:
            text += ("\n" if text else "") + format_preview_markup_line(
                None, "meta", note, width=width, dark=self.app.current_theme.dark,
            )
        # Rich escapes and Textual escapes are different. Pass styled text,
        # so an unmatched '[' in code cannot consume a closing style tag.
        rendered = Text.from_markup(text)
        if not self.file_recorded and not self.diff_text:
            highlight_output_statuses(rendered, dark=self.app.current_theme.dark)
        if body.content != rendered:
            # Preview rows are already wrapped and padded to the viewport.
            # New text at the same width/height only repaints those cells;
            # relaying out the whole transcript stalls typing on live logs.
            size = (width, rendered.plain.count("\n") + bool(rendered.plain))
            body.update(rendered, layout=size != self._body_size)
            self._body_size = size
        body.display = self._open and bool(text)
        command = self.query_one(".tool-command", Static)
        details = self._command_details()
        if command.content != details:
            command.update(details)
        # The full script is one click away; the row stays one line.
        command.display = self._open and bool(details) and self._show_full
        if command.display != getattr(self, "_command_below", False):
            self._command_below = command.display
            self._refresh_head()
        viewport.display = body.display or command.display
        more = self.query_one(".tool-more", Button)
        more.display = self._open and (self._preview_overflow or bool(details))
        if (status_only and not command_output and not diagnostic and not self._show_full
                and len(rows) <= 1 and self._summary_text()):
            # The outcome line already says it all: no button for one line.
            more.display = False
        noun = "line" if len(rows) == 1 else "lines"
        label = "Show less" if self._show_full else (
            "Show error details" if diagnostic and self._preview_overflow else
            f"Show output ({len(rows)} {noun})" if status_only and self._preview_overflow
            else f"Full output ({len(rows)} {noun})" if compact and self._preview_overflow
            else f"Show all (+{len(rows) - preview_limit} lines)" if len(rows) > preview_limit
            else "Show command"
        )
        if more.label.plain != label:
            more.label = label
        more.tooltip = "F expands the full output. Enter folds this activity."
        copy = self.query_one(".tool-copy", CopyChip)
        copy.display = viewport.display
        self.query_one(".tool-actions").display = more.display or copy.display
        self._painted_body_key = body_key
        if self.phase == "output" and viewport.display:
            # Live output follows its newest line, like a terminal.
            viewport.scroll_end(animate=False)
        if self.phase in {"end", "error", "cancelled"}:
            self._command_summary_cache = self._stream_rows_cache = None

    def on_resize(self) -> None:
        if self._open:
            self._refresh_body()

    def set_diff(self, added: int, removed: int) -> None:
        self.added = max(0, int(added or 0))
        self.removed = max(0, int(removed or 0))
        self._refresh_head()

    def add_diff(self, added: int, removed: int) -> None:
        self.added += max(0, int(added or 0))
        self.removed += max(0, int(removed or 0))
        self._refresh_head()
        self._refresh_body()

    def set_diff_text(self, text: str) -> None:
        blob = (text or "").strip("\n")
        if not blob:
            return
        self.diff_text = blob
        self._body_revision += 1
        self._reveal_if_preview()
        self._refresh_body()

    def _has_preview(self) -> bool:
        if self._open and self._body_size and self._body_size[1]:
            return True
        return self.file_recorded or bool(self._command_details()) or bool(
            format_tool_detail(
                self.tool_name,
                self.arguments if isinstance(self.arguments, dict) else {},
                result=self.result,
                error=self.error,
                output_lines=self.output_lines,
                diff_text=self.diff_text,
                limit=2,
                include_run_output=True,
            )
        )

    def _reveal_if_preview(self) -> None:
        if self.cluster_kind == "explore" and self.phase not in {"error", "cancelled"}:
            return
        if not self._has_preview():
            return
        self._open = True

    def collapse(self) -> None:
        self._open = False
        self._refresh_head()
        self._refresh_body()

    def toggle(self) -> None:
        self._open = not self._open
        self._refresh_body()

    def action_toggle(self) -> None:
        self.toggle()

    def action_show_full(self) -> None:
        self._show_full = not self._show_full
        self._open = True
        self._refresh_body()

    def action_page_output(self, direction: int) -> None:
        if not self._output_mounted:
            raise SkipAction()
        viewport = self.query_one(".tool-output", VerticalScroll)
        if not viewport.display or not viewport.max_scroll_y:
            raise SkipAction()
        if direction < 0:
            viewport.scroll_page_up(animate=False)
        else:
            viewport.scroll_page_down(animate=False)

    def action_output_edge(self, end: bool) -> None:
        self._show_full = True
        self._open = True
        self._refresh_body()

        def scroll() -> None:
            if not self._output_mounted:
                return  # hidden row: the next paint opens it
            viewport = self.query_one(".tool-output", VerticalScroll)
            if end:
                viewport.scroll_end(animate=False)
                more = self.query_one(".tool-more", Button)
                (more if more.display else viewport).scroll_visible(animate=False)
            else:
                viewport.scroll_home(animate=False)
                self.scroll_visible(animate=False, top=True)

        self.call_after_refresh(scroll)

    @on(Button.Pressed, ".tool-more")
    def _more_pressed(self, event: Button.Pressed) -> None:
        self.action_show_full()
        event.stop()

    def on_click(self, event: events.Click) -> None:
        if event.widget is self or "tool-head" in getattr(event.widget, "classes", ()):
            self.focus()
            self.toggle()
            event.stop()


class ToolCluster(LocalDisplayStyles, Vertical, can_focus=True):
    """Cursor-style group: ``Explored`` / ``Edited``, then the operations."""

    TITLES = {"explore": "Explored", "edit": "Edited"}

    DEFAULT_CSS = """
    ToolCluster {
        height: auto;
        margin: 0;
        padding: 0;
        background: $background;
    }
    ToolCluster > .cluster-head {
        height: auto;
        color: $text-muted;
        background: $background;
        text-style: none;
    }
    ToolCluster > .cluster-head:hover { color: $foreground; }
    ToolCluster:focus > .cluster-head { background: $surface; }
    ToolCluster > .cluster-body {
        height: auto;
        padding: 0;
        background: $background;
    }
    ToolCluster.-collapsed > .cluster-body { display: none; }
    ToolCluster > Button.cluster-more {
        height: 1;
        min-height: 1;
        min-width: 0;
        width: auto;
        border: none;
        padding: 0 1;
    }
    ToolCluster.-collapsed > Button.cluster-more { display: none; }
    """
    BINDINGS = [Binding("enter,space", "toggle", "Expand / collapse", show=False)]

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.tools: list[ToolCall] = []
        self._open = True
        self._painted_head: tuple[str, bool] | None = None
        self._visible_limit = ACTIVITY_PAGE_SIZE
        self._head_pending = False
        self._loading_page = False

    def compose(self) -> ComposeResult:
        yield Static(self._head_text(), classes="cluster-head", markup=False)
        # Empty for a live group (rows mount as they arrive); a group built
        # back from its record mounts its first page of rows at once.
        yield Vertical(*self.tools[:self._visible_limit], classes="cluster-body")
        more = Button("Show more activity", classes="cluster-more", compact=True)
        more.display = False
        yield more

    def on_mount(self) -> None:
        self.watch(self.app, "theme", lambda _: self._refresh_head(), init=False)
        self._refresh_head()

    def on_unmount(self) -> None:
        forget_app_watchers(self)

    def watch_has_focus(self, _has_focus: bool) -> None:
        # Only ``ToolCluster:focus > .cluster-head`` depends on focus; the
        # rows below (up to a page of tool cards) keep their styles.
        restyle_focus(self, *self.query_children(".cluster-head"))

    def _head_text(self) -> str:
        title = self.TITLES.get(self.kind, self.kind.title() or "Tools")
        shown = self.tools
        confirmed = [tool for tool in shown if tool.file_recorded and tool.phase != "cancelled"]
        if self.kind == "edit":
            operations = {file_operation_label(tool.file_operation) for tool in confirmed}
            if len(operations) == 1:
                title = next(iter(operations))
            elif not confirmed:
                title = "Editing" if any(tool.phase in {"start", "output"} for tool in self.tools) else "Edits"
        if any(tool.phase in {"start", "output"} for tool in self.tools):
            glyph = "◦"
        else:
            glyph = "•"
        extra = ""
        if self.kind == "edit" and shown:
            files = len({activity_path_key(tool.file_path) or tool.group_key or tool.call_id for tool in confirmed or shown})
            noun = "file" if files == 1 else "files"
            extra = f" {files} {noun}"
            if any(tool.file_recorded and not tool.file_binary and tool.file_operation != "unchanged" for tool in shown):
                extra += f" (+{sum(tool.added for tool in shown)} -{sum(tool.removed for tool in shown)})"
        elif not self._open and len(self.tools) > 1:
            extra = f"  {len(self.tools)}"
        return f"{glyph} {title}{extra}"

    def _refresh_head(self) -> None:
        self._head_pending = False
        if not self.is_mounted:
            return
        try:
            head = self.query_one(".cluster-head", Static)
            shown = self.tools
            head.display = self.kind != "edit" or len(shown) > 1 or not self._open
            key = (self._head_text(), self.app.current_theme.dark)
            if key != self._painted_head:
                text = activity_head_text(key[0], dark=key[1])
                # Counts and spinners change; the one-line head keeps its size.
                head.update(text, layout=not same_height_text(head, str(head.content), text.plain))
                self._painted_head = key
            more = self.query_one(".cluster-more", Button)
            remaining = max(0, len(self.tools) - self._visible_limit)
            more.display = remaining > 0 and self._open
            if remaining:
                more.label = f"Show next {min(ACTIVITY_PAGE_SIZE, remaining)} ({remaining} more)"
        except Exception:  # noqa: BLE001
            pass

    def request_head_refresh(self) -> None:
        # A multi-file event can update hundreds of records in one tick.
        # Count them once per batch instead of rescanning the whole group
        # for every unmounted record.
        if self.is_mounted and not self._head_pending:
            self._head_pending = True
            self.call_later(self._refresh_head)

    def _retree(self) -> None:
        last = len(self.tools) - 1
        # Appending changes only the old tail and the new tail.
        for index in range(max(0, last - 1), last + 1):
            tool = self.tools[index]
            tool.tree_mark = "" if self.kind == "edit" and last == 0 else "└ " if index == last else "├ "
            tool._refresh_head(notify_cluster=False)

    async def add_call(self, widget: ToolCall) -> None:
        widget.cluster_kind = self.kind
        widget._cluster_owner = self
        widget.add_class("-cluster")
        self.tools.append(widget)
        if len(self.tools) > self._visible_limit:
            if len(self.tools) == self._visible_limit + 1:
                self._retree()
            self.request_head_refresh()
            return
        body = self.query_one(".cluster-body", Vertical)
        await body.mount(widget)
        self._retree()
        if self.kind == "edit":
            widget._reveal_if_preview()
            widget._refresh_body()
        self._refresh_head()

    @on(Button.Pressed, ".cluster-more")
    async def _more_activity(self, event: Button.Pressed) -> None:
        event.stop()
        if self._loading_page:
            return
        self._loading_page = True
        try:
            start = self._visible_limit
            self._visible_limit = min(len(self.tools), start + ACTIVITY_PAGE_SIZE)
            body = self.query_one(".cluster-body", Vertical)
            for index in range(start, self._visible_limit):
                widget = self.tools[index]
                widget.tree_mark = "└ " if index == len(self.tools) - 1 else "├ "
            await body.mount(*self.tools[start:self._visible_limit])
            self._refresh_head()
        finally:
            self._loading_page = False

    def toggle(self) -> None:
        self._open = not self._open
        self.set_class(not self._open, "-collapsed")
        self._refresh_head()

    def action_toggle(self) -> None:
        self.toggle()

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        classes = set(getattr(target, "classes", ()) or ())
        if "cluster-head" in classes or target is self:
            self.toggle()
            event.stop()

    def snapshot(self, tool_snapshot: Any) -> dict[str, Any]:
        return {
            "kind": self.kind, "open": self._open, "visible_limit": self._visible_limit,
            "tools": [tool_snapshot(tool) for tool in self.tools],
        }

    @classmethod
    def from_snapshot(cls, state: dict[str, Any], build_tool: Any) -> ToolCluster:
        cluster = cls(state["kind"])
        cluster._open = state["open"]
        cluster._visible_limit = state["visible_limit"]
        for data in state["tools"]:
            tool = build_tool(data)
            tool._cluster_owner = cluster
            cluster.tools.append(tool)
        return cluster

    def copy_text(self) -> str:
        parts = [self._head_text()]
        for tool in self.tools:
            detail = tool.copy_text()
            if detail:
                parts.append(detail)
        return clip_transcript("\n".join(parts))


class InterimMarkdown(TranscriptMarkdown):
    """An interim message with Markdown, kept rendered in the activity chain."""

    def __init__(self, text: str) -> None:
        super().__init__(text, classes="assistant-body -interim")
        self.narration = text


class ProgressLine(Static):
    DEFAULT_CSS = """
    ProgressLine {
        height: auto;
        margin: 0;
        padding: 0 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__(f"· {text}", markup=False)
        self.narration = text


def format_elapsed(seconds: float) -> str:
    """Cursor-style clock: ``12s``, ``10m 54s``, ``1h 02m``."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def running_exec_count() -> int:
    try:
        from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

        return DEFAULT_EXEC_SESSION_MANAGER.running_count()
    except Exception:  # noqa: BLE001
        return 0


def format_working_line(*, elapsed_s: float, background: int = 0, activity: str = "") -> str:
    """Status shown in chat while a turn runs: what the agent is doing now.
    Keys are Navin keys, not Cursor's."""
    clock = format_elapsed(elapsed_s)
    label = " ".join((activity or "").split()) or "Working"
    if cell_len(label) > 72:
        # Cut by terminal cells: wide glyphs take two.
        label = set_cell_size(label, 71).rstrip() + "…"
    parts = [f"{label} ({clock} • esc to interrupt)"]
    if background == 1:
        parts.append("1 background terminal running")
        parts.append("/ps to view")
    elif background > 1:
        parts.append(f"{background} background terminals running")
        parts.append("/ps to view")
    return " • ".join(parts)


class JumpToBottom(Static):
    """Shown under the conversation once the reader scrolls up: one click
    brings the latest messages back and follows them again."""

    DEFAULT_CSS = """
    JumpToBottom {
        height: 0;
        min-height: 0;
        display: none;
        content-align: center middle;
        text-align: center;
        color: $accent;
        background: $background;
        text-style: bold;
    }
    JumpToBottom.-visible {
        display: block;
        height: 1;
    }
    JumpToBottom:hover {
        background: $boost;
    }
    """

    class Pressed(Message):
        """The reader asked for the latest messages."""

    def show(self, visible: bool, label: str) -> None:
        if visible:
            self.update(label)
        self.set_class(visible, "-visible")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Pressed())


class LivePresenceLine(Static):
    """The agent on the Live call, above the composer: one calm accent that
    breathes while listening, follows the voice, turns while thinking and
    moves while speaking (the terminal twin of the desktop LivePresence)."""

    DEFAULT_CSS = """
    LivePresenceLine {
        height: 0;
        min-height: 0;
        padding: 0 1;
        color: $text-muted;
        background: $background;
        display: none;
    }
    LivePresenceLine.-visible {
        display: block;
        height: 1;
    }
    """

    _BREATH = ("·", "•", "●", "●", "•", "·")
    _SPIN = ("◐", "◓", "◑", "◒")
    _BARS = "▁▂▃▄▅▆▇"

    def paint(self, mode: str | None, caption: str, *, frame: int, level: float = 0.0) -> None:
        if mode is None:
            if self.display:
                self.update("")
                self.display = False
                self.remove_class("-visible")
            return
        if mode == "listening":
            glyph = self._BREATH[(frame // 2) % len(self._BREATH)]
        elif mode == "hearing":
            peak = min(len(self._BARS) - 1, int(max(0.0, level) * 60))
            glyph = "".join(self._BARS[max(0, peak - abs(2 - i))] for i in range(5))
        elif mode == "thinking":
            glyph = self._SPIN[frame % len(self._SPIN)]
        elif mode == "speaking":
            glyph = "".join(self._BARS[(frame * 3 + i * 5) % len(self._BARS)] for i in range(5))
        else:  # muted
            glyph = "○"
        text = Text()
        text.append(glyph, style="bold #5EA8FF" if mode != "muted" else "dim")
        text.append(f"  {caption}")
        self.update(text)
        if not self.display:
            self.display = True
            self.add_class("-visible")


class WorkingLine(Static):
    """Live turn status, pinned under the transcript like Cursor's Working row."""

    DEFAULT_CSS = """
    WorkingLine {
        height: 0;
        min-height: 0;
        margin: 0;
        padding: 0 1;
        color: $text-muted;
        background: $background;
        display: none;
    }
    WorkingLine.-visible {
        display: block;
        height: auto;
        min-height: 1;
        margin: 0;
    }
    """

    def set_line(self, text: str, *, frame: int = 0) -> None:
        glyph = ROW_SPINNER[frame % len(ROW_SPINNER)]
        content = f"{glyph} {text}" if text else ""
        old = str(self.content)
        if old == content and self.display == bool(text):
            return
        if text:
            line = Text(content)
            line.stylize(activity_palette(self.app.current_theme.dark)["blue"], 0, 1)
            # The spinner and the clock tick 8 times a second: same size, no reflow.
            self.update(line, layout=not (self.has_class("-visible") and same_height_text(self, old, content)))
            self.display = True
            self.add_class("-visible")
            return
        self.update("")
        self.display = False
        self.remove_class("-visible")

class AgentsPanel(Static):
    """Agents working in parallel, under the chat: ``main`` then one row each.

    ``● main``, then per agent its own name, what it is doing now, and on the
    right how long it has run and the tokens it has used. Six agents at most;
    finished agents (succeeded, failed, cancelled) stay with their outcome
    until dismissed (F8 Dismiss, F9 dismiss finished), a new conversation, or
    on their own: 10 minutes after a success, 15 minutes after a failure.
    Each finished row carries its own hint ("· F9 dismiss" / "· F8 details").
    """

    DEFAULT_CSS = """
    AgentsPanel {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        background: $background;
        display: none;
    }
    """

    MAX_VISIBLE_AGENTS = 6
    # main, agents, optional More row, plus one spare: the bottom hint line is
    # no longer painted (each finished row carries its own hint); kept for
    # callers that size the panel from it.
    MAX_ROWS = MAX_VISIBLE_AGENTS + 3
    # Legacy shared hint, not rendered any more; kept for compatibility.
    HINT = "  F8 relaunch, cancel or dismiss · F9 dismiss finished"
    DONE_HINT = " · F9 dismiss"
    ERROR_HINT = " · F8 details"
    # Finished rows leave the panel by themselves (they stay in F8).
    AUTO_DISMISS_DONE_S = 10 * 60
    AUTO_DISMISS_ERROR_S = 15 * 60

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__("", *args, markup=False, **kwargs)
        self._agents: dict[str, dict[str, Any]] = {}
        self._history: dict[str, dict[str, Any]] = {}
        self._main: tuple[bool, float] = (False, 0.0)
        self._painted: tuple | None = None
        self._dismissed: set[str] = set()
        # Task id of each painted line (None for main, more and hint lines).
        self._line_ids: list[str | None] = []
        self.expanded = False

    @property
    def active(self) -> bool:
        return bool(self._agents)

    def toggle_expanded(self) -> None:
        """Show every live agent, or fold back to the first six."""
        self.expanded = not self.expanded
        self._painted = None
        self.tick(*self._main)

    def upsert(
        self, task_id: str, *, label: str, status_line: str, phase: str,
        done: bool, error: str | None, started_ms_ago: int | None,
        tokens: int | None, task: str | None = None,
        steps: list[dict[str, str]] | None = None, model: str | None = None,
    ) -> None:
        now = time.monotonic()
        if task_id in self._dismissed:
            if done:
                # A late duplicate final frame: kept for F8, not in the panel.
                return
            # Resumed after being dismissed: it works again, show it.
            self._dismissed.discard(task_id)
        row = self._agents.setdefault(task_id, {"started": now, "tokens": 0})
        if started_ms_ago is not None:
            row["started"] = now - started_ms_ago / 1000
        # Provider errors read as a calm status ("Model timed out, retrying"),
        # never as the raw exception text.
        status = humanize_agent_error(error, retrying=not done) if error else (status_line or phase or "")
        row.update(label=(label or "").strip() or task_id, task=(task or "").strip(),
                   status=status.strip(), phase=phase, error=bool(error))
        if tokens:
            row["tokens"] = tokens
        row["model"] = model
        if steps is not None:
            row["steps"] = [dict(step) for step in steps]
        if done:
            row.setdefault("done_at", now)
            row["elapsed"] = row.get("elapsed") or now - row["started"]
            self._history[task_id] = dict(row)
            while len(self._history) > 400:
                del self._history[next(iter(self._history))]
        self.tick(*self._main)

    def inspection_rows(self) -> dict[str, dict[str, Any]]:
        """All live agents plus recent completed agents for the F8 inspector."""
        return {**self._history, **self._agents}

    def reset(self) -> None:
        self._agents.clear()
        self._history.clear()
        self._dismissed.clear()
        self._painted = None
        self.display = False
        self.update("")

    def dismiss(self, task_id: str) -> bool:
        """Take one finished agent off the panel (it stays in F8). True if removed."""
        row = self._agents.get(task_id)
        if row is None or "done_at" not in row:
            return False
        del self._agents[task_id]
        self._dismissed.add(task_id)
        self._painted = None
        if self.is_attached:
            self.tick(*self._main)
        return True

    def dismiss_finished(self) -> int:
        """Take every finished agent off the panel; running ones stay."""
        ids = [key for key, row in self._agents.items() if "done_at" in row]
        for task_id in ids:
            del self._agents[task_id]
            self._dismissed.add(task_id)
        self._painted = None
        if self.is_attached:
            self.tick(*self._main)
        return len(ids)

    def forget_finished(self) -> None:
        """A new conversation starts: the previous one's finished agents leave.

        Agents still running stay, since they are still working.
        """
        self._history.clear()
        for task_id in [key for key, row in self._agents.items() if "done_at" in row]:
            del self._agents[task_id]
        self._painted = None
        if self.is_attached:
            self.tick(*self._main)

    def on_click(self, event: Any = None) -> None:
        lines = str(self.content).splitlines()
        y = getattr(event, "y", None)
        if lines and y is not None and 0 <= y < len(lines) and (
                "show more" in lines[y] or "show less" in lines[y]):
            self.toggle_expanded()
            return
        task_id = None
        if y is not None and 0 <= y < len(self._line_ids):
            task_id = self._line_ids[y]
        if task_id:
            self.app.call_later(self.app.run_action, f"inspect_agents({task_id!r})")
        else:
            self.app.call_later(self.app.run_action, "inspect_agents")

    def _auto_dismiss(self, now: float) -> None:
        """Finished rows older than their grace period leave, like ``dismiss()``."""
        expired = []
        for task_id, row in self._agents.items():
            done_at = row.get("done_at")
            if done_at is None:
                continue  # running agents are never auto-dismissed
            grace = self.AUTO_DISMISS_ERROR_S if row["error"] else self.AUTO_DISMISS_DONE_S
            if now - done_at >= grace:
                expired.append(task_id)
        for task_id in expired:
            del self._agents[task_id]
            self._dismissed.add(task_id)
        if expired:
            self._painted = None

    def tick(self, main_active: bool, main_elapsed: float) -> None:
        self._main = (main_active, main_elapsed)
        now = time.monotonic()
        self._auto_dismiss(now)
        if not self._agents:
            if self.display:
                self.display = False
                self.update("")
                self._painted = None
            return
        self.display = True
        width = content_width(self) or max(20, self.app.size.width - 2)
        key = (width, int(main_elapsed), main_active, int(now), self.expanded,
               tuple((k, r["status"], r["tokens"], r.get("phase"), "done_at" in r) for k, r in self._agents.items()))
        if key != self._painted:
            self.update(self._render_rows(width, main_active, main_elapsed, now))
            self._painted = key

    @staticmethod
    def _tokens(count: int) -> str:
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1000:
            return f"{count / 1000:.1f}k"
        return str(count)

    def _names(self) -> dict[str, str]:
        # Several agents launched with the same label read as one: name
        # them by their task instead.
        labels = [row["label"] for row in self._agents.values()]
        names = {}
        for task_id, row in self._agents.items():
            name = row["label"]
            if labels.count(name) > 1 and row["task"]:
                name = row["task"].splitlines()[0]
            names[task_id] = name
        return names

    @staticmethod
    def _fit_status(status: str, hint: str, room: int) -> tuple[str, str]:
        """Fit ``status + hint`` in ``room`` cells: cut the status first, keep the
        hint while it leaves at least a few cells of status, else drop the hint."""
        if cell_len(status) + cell_len(hint) <= room:
            return status, hint
        min_status = min(cell_len(status), 8)
        if hint and room - cell_len(hint) >= min_status:
            room_status = room - cell_len(hint)
            return status[: max(0, room_status - 1)] + "…", hint
        if cell_len(status) <= room:
            return status, ""
        return (status[: max(0, room - 1)] + "…" if room > 1 else ""), ""

    def _render_rows(self, width: int, main_active: bool, main_elapsed: float, now: float) -> Text:
        palette = {"blue": "#5EA8FF", "green": "#8FBC8F", "coral": "#E39B91", "muted": "#8A8A8A", "text": "#E6E6E6"}
        if not self.app.current_theme.dark:
            palette = {"blue": "#3D82FF", "green": "#2D6A4F", "coral": "#914747", "muted": "#626262", "text": "#20242A"}
        out = Text(no_wrap=True, overflow="ellipsis")
        out.append("● ", style=palette["blue"])
        out.append("main", style=f"bold {palette['blue']}")
        # The working line under the chat carries the live clock.
        del main_elapsed
        state = "working" if main_active else "waiting for agents"
        out.append(f"  {state}", style=palette["muted"])
        self._line_ids = [None]

        def rank(row: dict[str, Any]) -> int:
            if "done_at" in row:
                return 0 if row["error"] else 2
            return 0 if row.get("phase") == "awaiting_approval" else 1

        rows = sorted(self._agents.items(), key=lambda item: (rank(item[1]), item[1]["started"]))
        shown, hidden = rows[:self.MAX_VISIBLE_AGENTS], rows[self.MAX_VISIBLE_AGENTS:]
        overflow = bool(hidden)
        if self.expanded and hidden:
            shown, hidden = rows, []
        elif not hidden:
            self.expanded = False
        names = self._names()
        name_width = min(28, max((cell_len(names[k]) for k, _ in shown), default=8))
        for task_id, row in shown:
            finished = "done_at" in row
            elapsed = row["elapsed"] if finished else now - row["started"]
            meta = format_elapsed(elapsed) + (f" · ↓ {self._tokens(row['tokens'])} tokens" if row["tokens"] else "")
            model_name = str(row.get("model") or "").strip()
            if model_name:
                # Short name: "openrouter/anthropic/claude-x" reads as "claude-x".
                meta = f"{model_name.rsplit('/', 1)[-1][:32]} · {meta}"
            # Finished agents: green check, or red cross and a short reason.
            waiting = not finished and row.get("phase") == "awaiting_approval"
            if finished:
                glyph, ink = ("×", palette["coral"]) if row["error"] else ("✓", palette["green"])
            elif waiting:
                glyph, ink = ("!", palette["coral"])
            else:
                glyph, ink = ("◯", palette["blue"])
            name = names[task_id]
            if cell_len(name) > name_width:
                name = name[: max(1, name_width - 1)] + "…"
            # Finished rows: "✓ Done" in green or "× reason" in coral, each
            # followed by its own muted key hint.
            hint, status_ink = "", palette["muted"]
            if finished and row["error"]:
                status, hint, status_ink = f"× {row['status'] or 'Stopped'}", self.ERROR_HINT, palette["coral"]
            elif finished:
                status, hint, status_ink = "✓ Done", self.DONE_HINT, palette["green"]
            elif waiting:
                status = "Waiting for your approval (answer above)"
            else:
                status = row["status"] or "Working…"
            left = 2 + name_width + 2
            room = max(0, width - left - cell_len(meta) - 2)
            status, hint = self._fit_status(status, hint, room)
            gap = max(1, width - left - cell_len(status) - cell_len(hint) - cell_len(meta))
            out.append("\n")
            self._line_ids.append(task_id)
            out.append(f"{glyph} ", style=ink)
            out.append(name.ljust(name_width), style=f"bold {palette['text']}")
            out.append("  ")
            out.append(status, style=status_ink)
            if hint:
                out.append(hint, style=palette["muted"])
            out.append(" " * gap)
            out.append(meta, style=palette["muted"])
        if hidden:
            running = sum("done_at" not in row for _, row in hidden)
            out.append("\n")
            out.append(f"  ▸ show more (+{len(hidden)}) · +{len(hidden)} more agents ({running} running)"
                       " · F7 or click to expand, F8 to inspect", style=palette["muted"])
        elif overflow:
            out.append("\n")
            out.append("  ▴ show less · F7 or click to collapse", style=palette["muted"])
        return out


class UpdateOffer(Static):
    """Persistent Codex-style line: a newer navin exists, /update installs it."""

    DEFAULT_CSS = """
    UpdateOffer {
        margin: 1 0 0 0;
        padding: 0 1;
        color: $accent;
        text-style: italic;
        border-left: tall $accent;
        background: $background;
    }
    UpdateOffer:hover { color: $foreground; text-style: none; }
    """

    def __init__(self, latest: str, detail: str) -> None:
        super().__init__("", markup=True)
        self.latest = latest
        self.detail = detail
        self.update(
            f"[$accent]◌[/] navin [b]{escape(latest)}[/] is available  "
            f"[$text-muted]/update to install · clic[/]"
        )

    def on_click(self) -> None:
        self.app.call_later(self.app.run_action, "update")

    def snapshot_record(self) -> BlockRecord | None:
        return static_record(self)


class SystemNote(Static):
    DEFAULT_CSS = """
    SystemNote {
        margin: 1 0 0 0;
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
        padding: 1 1;
    }
    SystemNote.-success { border-left: tall $success; }
    SystemNote.-quiet { border-left: none; padding: 0 1; margin: 1 0 0 0; }
    """

    def __init__(self, text: str, level: str = "info") -> None:
        super().__init__(text, markup=True)
        if level in {"warning", "error", "success", "quiet"}:
            self.add_class(f"-{level}")

    def snapshot_record(self) -> BlockRecord | None:
        # Passing notes leave on their own timer; they are never virtualized.
        return None if getattr(self, "transient", False) else static_record(self)


class SubagentCard(Static):
    DEFAULT_CSS = """
    SubagentCard {
        margin: 0;
        padding: 0 1;
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


class PromptEcho(Static):
    """A closed question or permission, kept in the chain: ``? question`` /
    ``→ answer``, or ``✓ Allowed: ...``."""

    DEFAULT_CSS = """
    PromptEcho {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        color: $text-muted;
        background: $background;
    }
    AssistantMessage > PromptEcho { padding: 0; }
    """

    def __init__(self, rows: list[tuple[str, str, str]]) -> None:
        # rows: (glyph, palette ink, text). External text is never markup.
        text = Text()
        for index, (glyph, ink, body) in enumerate(rows):
            if index:
                text.append("\n")
            text.append(f"{glyph} ", style=ink)
            text.append(str(body).strip())
        super().__init__(text, markup=False)
        self.echo = "\n".join(f"{glyph} {body}" for glyph, _ink, body in rows)

    def snapshot_record(self) -> BlockRecord | None:
        return static_record(self)

    @classmethod
    def choice(cls, question: str, answer: str, *, dark: bool = True) -> PromptEcho:
        palette = activity_palette(dark)
        return cls([("?", palette["blue"], question), ("→", palette["muted"], answer or "no answer")])

    @classmethod
    def approval(cls, allowed: bool, tool: str, action: str, reason: str = "", *, dark: bool = True) -> PromptEcho:
        palette = activity_palette(dark)
        subject = " ".join(part for part in (tool, action) if part).strip() or "request"
        if len(subject) > 120:
            subject = subject[:119].rstrip() + "…"
        verdict = "Allowed" if allowed else "Denied"
        if reason:
            subject += f" ({reason})"
        glyph, ink = ("✓", palette["green"]) if allowed else ("×", palette["coral"])
        return cls([(glyph, ink, f"{verdict}: {subject}")])


def _assistant_record_copy(record: BlockRecord) -> str:
    # Same as AssistantMessage.copy_text, from the record's data.
    answer = str(record.state["text"]).strip()
    if answer:
        return clip_transcript(answer)
    parts = [ToolCall.from_snapshot(data).copy_text() for _key, data in record.state["tools"]]
    return clip_transcript("\n\n".join(part for part in parts if part))


class AssistantMessage(LocalDisplayStyles, Vertical):
    """An assistant turn: reasoning, activity (tools), streamed Markdown body."""

    ALLOW_SELECT = True

    def render_line(self, y: int) -> Strip:
        # A turn may be thousands of rows tall. The default container renderer
        # builds a blank strip for every row on each resize while streaming.
        return Strip.blank(content_width(self), self.visual_style.rich_style)

    DEFAULT_CSS = """
    AssistantMessage {
        height: auto;
        margin: 0;
        padding: 0 1;
        background: $background;
    }
    AssistantMessage > .assistant-preview {
        margin: 0 0 1 0;
        padding: 0;
        color: $foreground;
        background: $background;
    }
    AssistantMessage > .assistant-body {
        margin: 0;
        padding: 0;
        height: auto;
        overflow: hidden;
        display: none;
        background: $background;
        color: $foreground;
    }
    AssistantMessage.-open > .assistant-preview {
        display: none;
    }
    AssistantMessage.-open > .assistant-body {
        display: block;
    }
    AssistantMessage > .assistant-body.-interim {
        display: block;
        margin: 1 0 0 0;
    }
    AssistantMessage > .assistant-body.-interim.-first {
        margin: 0;
    }
    AssistantMessage > .assistant-foot {
        margin: 0 0 1 0;
        padding: 0;
        color: $text-muted;
        display: none;
        background: $background;
        text-style: none;
    }
    /* One chain: a blank line after each command or group so runs do not touch;
       a narration line opens a new group (the two margins collapse into one). */
    AssistantMessage > ReasoningBlock,
    AssistantMessage > SubagentCard {
        margin: 0;
    }
    AssistantMessage > ToolCall,
    AssistantMessage > ToolCluster {
        margin: 0 0 1 0;
    }
    AssistantMessage > ProgressLine,
    AssistantMessage > PromptEcho {
        margin: 1 0 0 0;
    }
    AssistantMessage > ProgressLine.-first,
    AssistantMessage > PromptEcho.-first {
        margin: 0;
    }
    AssistantMessage.-has-activity > .assistant-body {
        margin-top: 1;
    }
    AssistantMessage > .assistant-foot.-visible { display: block; }
    /* Under a finished answer: one click copies the whole report. */
    AssistantMessage > .assistant-copy { display: none; }
    AssistantMessage.-copyable > .assistant-copy { display: block; }
    AssistantMessage > .assistant-history-controls {
        display: none;
        height: 1;
        margin: 1 0;
    }
    AssistantMessage > .assistant-history-controls Button {
        height: 1;
        min-height: 1;
        min-width: 0;
        width: auto;
        padding: 0 1;
        margin: 0 1 0 0;
        border: none;
        color: $text-muted;
        background: $background;
    }
    AssistantMessage > .assistant-history-controls Button:hover,
    AssistantMessage > .assistant-history-controls Button:focus {
        color: $foreground;
        background: $panel;
    }
    AssistantMessage > .assistant-body Markdown { margin: 0; padding: 0; background: transparent; }
    AssistantMessage > .assistant-body MarkdownFence {
        margin: 0 0 1 0;
        padding: 0 1;
        color: $foreground;
        background: $panel;
    }
    AssistantMessage > .assistant-body MarkdownH1,
    AssistantMessage > .assistant-body MarkdownH2,
    AssistantMessage > .assistant-body MarkdownH3 { margin: 0 0 1 0; padding: 0; background: transparent; border: none; }
    AssistantMessage > .assistant-body MarkdownParagraph { margin: 0 0 1 0; }
    AssistantMessage > .assistant-body MarkdownBlock { color: $foreground; }
    /* One palette with the activity rows: blue emphasis, lavender code,
       green paths and passes, mustard commands and warnings, coral failures. */
    AssistantMessage > .assistant-body MarkdownBlock > .strong {
        color: #5EA8FF;
        text-style: bold;
    }
    AssistantMessage > .assistant-body MarkdownBlock:light > .strong { color: #3D82FF; }
    AssistantMessage > .assistant-body MarkdownBlock > .em { color: $foreground; text-style: italic; }
    AssistantMessage > .assistant-body MarkdownBlock > .status_ok { color: #8FBC8F; text-style: bold; }
    AssistantMessage > .assistant-body MarkdownBlock:light > .status_ok { color: #2D6A4F; }
    AssistantMessage > .assistant-body MarkdownBlock > .status_fail { color: #E39B91; text-style: bold; }
    AssistantMessage > .assistant-body MarkdownBlock:light > .status_fail { color: #914747; }
    AssistantMessage > .assistant-body MarkdownBlock > .status_warn { color: #D6BC78; text-style: bold; }
    AssistantMessage > .assistant-body MarkdownBlock:light > .status_warn { color: #795B13; }
    AssistantMessage > .assistant-body MarkdownBlock > .link { color: $accent; text-style: underline; }
    AssistantMessage > .assistant-body MarkdownBullet {
        color: $text-muted;
    }
    AssistantMessage > .assistant-body MarkdownH1,
    AssistantMessage > .assistant-body MarkdownH2 {
        color: $primary;
        text-style: bold;
    }
    AssistantMessage > .assistant-body MarkdownH3 {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    AssistantMessage > .assistant-body MarkdownTable {
        margin: 0 0 1 0;
    }
    AssistantMessage > .assistant-body MarkdownTable .header {
        color: $primary;
        background: $panel;
        text-style: bold;
    }
    AssistantMessage.-validation-pending > .assistant-body MarkdownH2 {
        color: $warning;
    }
    AssistantMessage.-validation-pending > .assistant-body MarkdownTable {
        width: 56;
        max-width: 100%;
    }
    AssistantMessage.-validation-pending > .assistant-body MarkdownFence {
        color: $warning;
    }
    AssistantMessage.-validation-pending > .assistant-body MarkdownFence > Label {
        width: 1fr;
        height: auto;
        padding: 1;
        text-wrap: wrap;
        text-overflow: fold;
    }
    AssistantMessage > .assistant-body MarkdownBulletList,
    AssistantMessage > .assistant-body MarkdownOrderedList { margin: 0 0 1 0; }
    AssistantMessage > .assistant-body MarkdownBulletList MarkdownParagraph,
    AssistantMessage > .assistant-body MarkdownOrderedList MarkdownParagraph { margin: 0; }
    AssistantMessage > .assistant-body MarkdownBlock > .code_inline,
    AssistantMessage > .assistant-body MarkdownBlock:dark > .code_inline {
        color: #C6AFE3;
        background: transparent;
    }
    AssistantMessage > .assistant-body MarkdownBlock:light > .code_inline {
        color: #705393;
        background: transparent;
    }
    AssistantMessage > .assistant-body MarkdownBlock > .code_path,
    AssistantMessage > .assistant-body MarkdownBlock:dark > .code_path {
        color: #8FBC8F;
        background: transparent;
    }
    AssistantMessage > .assistant-body MarkdownBlock:light > .code_path {
        color: #2D6A4F;
        background: transparent;
    }
    AssistantMessage > .assistant-body MarkdownBlock > .code_command,
    AssistantMessage > .assistant-body MarkdownBlock:dark > .code_command {
        color: #D7BA7D;
        background: transparent;
    }
    AssistantMessage > .assistant-body MarkdownBlock:light > .code_command {
        color: #805B20;
        background: transparent;
    }
    """

    def __init__(self, bot_name: str = "navin", bot_icon: str = MARK, *, show_head: bool = True) -> None:
        super().__init__()
        self.bot_name = bot_name
        self._show_head = show_head
        icon = (bot_icon or "").strip()
        self.bot_icon = "" if icon in {"≈", "~"} else icon
        self._paint_timer: FrameRequest | None = None
        self._paint_lock = asyncio.Lock()
        self._painted_markdown = ""
        self._buffer: list[str] = []
        self._tools: dict[str, ToolCall] = {}
        self._file_tools: dict[tuple[str, str], ToolCall] = {}
        self._cluster: ToolCluster | None = None
        self._subagents: dict[str, SubagentCard] = {}
        self._progress_line: ProgressLine | None = None
        self._reasoning: ReasoningBlock | None = None
        self._activity_blocks: list[Vertical] = []
        self._activity_start = 0
        self._activity_expanded = False
        self._pinned_activity: set[Vertical] = set()
        self.streamed = False
        self.finished = False
        self._open = False
        self._composed = asyncio.Event()
        # Set when this turn is built back from its record (navin.tui.virtual).
        self._settled: tuple[list[Widget], dict[str, Any]] | None = None
        self.from_record = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="assistant-history-controls"):
            yield Button("Earlier activity", classes="assistant-history", compact=True)
            yield Button("Recent only", classes="assistant-recent", compact=True)
        settled, self._settled = self._settled, None
        if settled is not None:
            yield from self._compose_settled(*settled)
            return
        yield Static("", classes="assistant-preview", markup=True)
        yield TranscriptMarkdown("", classes="assistant-body -answer")
        yield CopyChip(self.copy_text, classes="assistant-copy")
        yield Static("", classes="assistant-foot", markup=True)

    def _compose_settled(self, mount: list[Widget], state: dict[str, Any]) -> ComposeResult:
        # A finished turn: its visible activity rows, then the answer as one
        # SettledMarkdown instead of a widget per paragraph, row and cell.
        yield from mount
        preview = Static(state["preview"], classes="assistant-preview", markup=True)
        preview.display = not self._open
        yield preview
        body = SettledMarkdown(state["body"], classes="assistant-body -answer")
        body.display = self._open
        yield body
        yield CopyChip(self.copy_text, classes="assistant-copy")
        foot = Static(state["foot"], classes="assistant-foot", markup=True)
        foot.set_class(state["foot_visible"], "-visible")
        yield foot

    def on_mount(self) -> None:
        self._composed.set()
        if self.from_record:
            self._refresh_history_controls()

    # -- virtualization ---------------------------------------------------

    def snapshot_record(self) -> BlockRecord | None:
        """This finished turn as data, or None while anything in it is live."""
        if not self.finished or not self.is_mounted or self._paint_lock.locked():
            return None
        if self._paint_timer is not None and self._paint_timer.scheduler.pending(self._paint_timer.key):
            return None
        if self._reasoning is not None and not self._reasoning._done:
            return None
        if any(tool.phase in {"start", "output"} for tool in self._tools.values()):
            return None
        snapshots: dict[int, dict[str, Any]] = {}

        def tool_snapshot(tool: ToolCall) -> dict[str, Any]:
            data = snapshots.get(id(tool))
            if data is None:
                data = snapshots[id(tool)] = tool.snapshot()
            return data

        activity_ids = {id(block) for block in self._activity_blocks}
        activity: list[dict[str, Any]] = []
        for child in self.children:
            if child is self._progress_line:
                continue
            if isinstance(child, ToolCall):
                entry: dict[str, Any] = {"kind": "tool", "tool": tool_snapshot(child)}
            elif isinstance(child, ToolCluster):
                entry = {"kind": "cluster", "cluster": child.snapshot(tool_snapshot)}
            elif isinstance(child, ReasoningBlock):
                entry = {"kind": "reasoning", "state": child.snapshot()}
            elif isinstance(child, (InterimMarkdown, SettledMarkdown)) and "-interim" in child.classes:
                entry = {"kind": "interim", "text": getattr(child, "narration", None) or child.source}
            elif isinstance(child, (ProgressLine, PromptEcho, SubagentCard)):
                entry = {"kind": "static", "type": type(child), "state": static_state(child)}
            else:
                continue
            entry["classes"] = frozenset(child.classes)
            entry["display"] = bool(child.display)
            entry["activity"] = id(child) in activity_ids
            activity.append(entry)
        foot = self._query_static(".assistant-foot")
        preview = self._query_static(".assistant-preview")
        state = {
            "bot_name": self.bot_name, "bot_icon": self.bot_icon, "show_head": self._show_head,
            "text": self.text, "body": self._painted_markdown, "open": self._open,
            "streamed": self.streamed, "activity": activity,
            "activity_start": self._activity_start, "activity_expanded": self._activity_expanded,
            "tools": [(key, tool_snapshot(tool)) for key, tool in self._tools.items()],
            "foot": foot.content if foot is not None else "",
            "foot_visible": foot is not None and foot.has_class("-visible"),
            "preview": preview.content if preview is not None and not self._open else "",
        }
        return BlockRecord(
            type(self), state, AssistantMessage._from_record,
            classes=set(self.classes), copy=_assistant_record_copy,
        )

    @staticmethod
    def _from_record(record: BlockRecord) -> AssistantMessage:
        state = record.state
        block = record.widget_type(state["bot_name"], state["bot_icon"], show_head=state["show_head"])
        block.from_record = True
        block._buffer = [state["text"]] if state["text"] else []
        block._painted_markdown = state["body"]
        block._open = state["open"]
        block.finished = True
        block.streamed = state["streamed"]
        built: dict[int, ToolCall] = {}

        def build_tool(data: dict[str, Any]) -> ToolCall:
            tool = built.get(id(data))
            if tool is None:
                tool = built[id(data)] = ToolCall.from_snapshot(data)
            return tool

        mount: list[Widget] = []
        activity: list[Widget] = []
        for entry in state["activity"]:
            kind = entry["kind"]
            if kind == "tool":
                widget: Widget = build_tool(entry["tool"])
            elif kind == "cluster":
                widget = ToolCluster.from_snapshot(entry["cluster"], build_tool)
            elif kind == "reasoning":
                widget = ReasoningBlock.from_snapshot(entry["state"])
                block._reasoning = widget
            elif kind == "interim":
                widget = SettledMarkdown(entry["text"])
            else:
                widget = build_static(entry["type"], entry["state"])
                if isinstance(widget, SubagentCard):
                    block._subagents[widget.task_id] = widget
            widget.set_classes(set(entry["classes"]))
            if entry["activity"]:
                activity.append(widget)
            widget.display = entry["display"]
            # Older activity behind "Earlier activity" is mounted only when asked.
            if entry["display"] or not entry["activity"]:
                mount.append(widget)
        block._activity_blocks = activity
        block._activity_start = state["activity_start"]
        block._activity_expanded = state["activity_expanded"]
        block._tools = {key: build_tool(data) for key, data in state["tools"]}
        block._settled = (mount, state)
        return block

    @staticmethod
    def _activity_running(block: Vertical) -> bool:
        if isinstance(block, ToolCall):
            return block.phase in {"start", "output"}
        if isinstance(block, ToolCluster):
            return any(tool.phase in {"start", "output"} for tool in block.tools)
        return isinstance(block, ReasoningBlock) and not block._done

    def _register_activity(self, block: Widget) -> None:
        if not self._activity_blocks:
            block.add_class("-first")
            self._set_child_state(True, "-has-activity", "assistant-body")
        self._activity_blocks.append(block)
        if not self._activity_expanded:
            start = self._recent_activity_start()
            for older in self._activity_blocks[self._activity_start:start]:
                if self._activity_running(older):
                    self._pinned_activity.add(older)
                else:
                    older.display = False
            self._activity_start = start
        self._release_finished_activity()
        self._refresh_history_controls()

    @staticmethod
    def _activity_rows(block: Widget) -> int:
        if isinstance(block, ToolCluster):
            return max(1, min(len(block.tools), block._visible_limit))
        return 1

    def _recent_activity_start(self) -> int:
        """First activity block of the recent page: about ACTIVITY_PAGE_SIZE rows.

        Counting blocks let a long turn show twenty clusters of twenty rows:
        hundreds of rows on screen, all reflowed whenever the streamed answer
        grew by a line. The newest block always stays.
        """
        blocks = self._activity_blocks
        start = len(blocks)
        rows = 0
        while start > 0 and len(blocks) - start < ACTIVITY_PAGE_SIZE:
            rows += self._activity_rows(blocks[start - 1])
            if rows > ACTIVITY_PAGE_SIZE and start < len(blocks):
                break
            start -= 1
        return start

    def _release_finished_activity(self) -> None:
        for block in tuple(self._pinned_activity):
            if not self._activity_running(block):
                block.display = False
                self._pinned_activity.discard(block)

    def _refresh_history_controls(self) -> None:
        controls = self.query_one(".assistant-history-controls", Horizontal)
        controls.display = bool(self._activity_start or self._activity_expanded)
        earlier = controls.query_one(".assistant-history", Button)
        earlier.display = self._activity_start > 0
        earlier.label = f"Earlier activity ({self._activity_start})"
        controls.query_one(".assistant-recent", Button).display = self._activity_expanded

    @on(Button.Pressed, ".assistant-history")
    async def _show_earlier_activity(self, event: Button.Pressed) -> None:
        event.stop()
        start = max(0, self._activity_start - ACTIVITY_PAGE_SIZE)
        revealed = self._activity_blocks[start:self._activity_start]
        boundary = self._activity_start
        self._activity_expanded = True
        self._activity_start = start
        unmounted = [block for block in revealed if not block.is_attached and block.parent is None]
        if unmounted:
            # A turn rebuilt from its record keeps older rows as data until
            # the reader asks for them.
            anchor = next(
                (block for block in self._activity_blocks[boundary:] if block.parent is self),
                self._query_static(".assistant-preview"),
            )
            for block in unmounted:
                block.display = True
            if anchor is not None:
                await self.mount_all(unmounted, before=anchor)
        for block in revealed:
            self._pinned_activity.discard(block)
            block.display = True
            for tool in ([block] if isinstance(block, ToolCall) else block.query(ToolCall)):
                tool._refresh_body()
        self._refresh_history_controls()
        if isinstance(self.parent, Transcript):
            self.parent.auto_follow = False
        if revealed:
            self.call_after_refresh(revealed[0].scroll_visible, animate=False, top=True)

    @on(Button.Pressed, ".assistant-recent")
    def _show_recent_activity(self, event: Button.Pressed) -> None:
        event.stop()
        self._activity_expanded = False
        start = self._recent_activity_start()
        for older in self._activity_blocks[:start]:
            if self._activity_running(older):
                self._pinned_activity.add(older)
            else:
                older.display = False
        self._activity_start = start
        self._refresh_history_controls()
        if isinstance(self.parent, Transcript):
            self.parent.auto_follow = True
            self.parent.follow()

    async def _ready_body(self) -> Markdown | None:
        await self._composed.wait()
        if not self.is_attached:
            return None
        try:
            return self.query_one(".assistant-body.-answer", Markdown)
        except NoMatches:
            return None

    async def _ready_preview(self) -> Static | None:
        await self._composed.wait()
        if not self.is_attached:
            return None
        try:
            return self.query_one(".assistant-preview", Static)
        except NoMatches:
            return None

    def _query_static(self, selector: str) -> Static | None:
        if not self.is_attached:
            return None
        try:
            return self.query_one(selector, Static)
        except NoMatches:
            return None

    # -- reasoning --------------------------------------------------------

    async def reasoning(self, text: str, *, end: bool = False, visible: bool = True) -> None:
        if not visible or not self.is_attached:
            return
        preview = await self._ready_preview()
        if preview is None:
            return
        if end:
            if self._reasoning is not None:
                self._reasoning.finish()
                self._release_finished_activity()
            return
        current = self._reasoning
        if (current is not None and current._done and current.is_attached
                and self._activity_blocks and self._activity_blocks[-1] is current
                and not self.text.strip()):
            # Nothing happened since the last thought: keep one row.
            current.reopen()
            current.append(text)
            return
        if self._reasoning is None or self._reasoning._done:
            block = ReasoningBlock()
            self._reasoning = block
            await self.mount(block, before=preview)
            self._register_activity(block)
            block.append(text)
            return
        self._reasoning.append(text)

    def toggle_reasoning(self) -> None:
        if self._reasoning is not None:
            self._reasoning.toggle()
            return
        self.toggle_body()

    # -- activity ---------------------------------------------------------

    def has_tool(self, call_id: str) -> bool:
        """Whether a tool call card with this call id lives on this bubble."""
        if not call_id:
            return False
        return any(call_id in tool.call_ids for tool in self._tools.values())

    async def continue_tool_in(self, call_id: str, target: AssistantMessage) -> None:
        """Move a running call below a follow-up while retaining its output."""
        for key, old in list(self._tools.items()):
            if call_id not in old.call_ids or old.phase not in {"start", "output"}:
                continue
            card = old.continuation()
            await target._mount_tool(key, card)
            del self._tools[key]
            for file_key, tool in list(self._file_tools.items()):
                if tool is old:
                    target._file_tools[file_key] = card
                    del self._file_tools[file_key]
            block = old._cluster_owner or old
            if old._cluster_owner is not None:
                cluster = old._cluster_owner
                cluster.tools.remove(old)
                cluster._retree()
                cluster._refresh_head()
            await old.remove()
            if isinstance(block, ToolCluster) and block.tools:
                continue
            if block in self._activity_blocks:
                index = self._activity_blocks.index(block)
                self._activity_blocks.remove(block)
                if index < self._activity_start:
                    self._activity_start -= 1
            self._pinned_activity.discard(block)
            if isinstance(block, ToolCluster):
                if self._cluster is block:
                    self._cluster = None
                await block.remove()
        self._refresh_history_controls()

    async def _mount_tool(self, key: str, widget: ToolCall) -> None:
        preview = await self._ready_preview()
        if preview is None:
            return
        self._tools[key] = widget
        family = tool_cluster_kind(widget.tool_name)
        if widget.tool_name == "manage_files" and widget.arguments.get("action") in {"delete", "move", "copy"}:
            family = "edit"
        if family:
            cluster = await self._ensure_cluster(family, widget.call_id)
            if cluster is not None:
                await cluster.add_call(widget)
        else:
            self._cluster = None
            await self.mount(widget, before=preview)
            self._register_activity(widget)

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
        percent: float | None = None,
        output_mode: str = "delta",
        expected_s: float | None = None,
    ) -> None:
        if not visible:
            return
        if (name or "").lower() in CARD_ONLY_TOOLS:
            return
        if not self.is_attached:
            return
        preview = await self._ready_preview()
        if preview is None:
            return
        key = call_id or f"{name}:{len(self._tools)}"
        widgets = [tool for tool in self._tools.values() if key in tool.call_ids]
        if not call_id and phase != "start":
            # No call id: the event belongs to a running row of that tool, not
            # to a second row. Parallel calls end in call order: the row with
            # the same arguments, else the oldest one still running.
            running = [
                tool for tool in self._tools.values()
                if tool.tool_name == name and tool.phase in {"start", "output"}
            ]
            same = [tool for tool in running if arguments and tool.arguments == arguments]
            widgets = (same or running)[:1]
        if not widgets and name == "board" and phase == "start" and arguments.get("task_id"):
            # Retrying a completion prerequisite updates its existing card.
            widgets = [tool for tool in self._tools.values() if tool.validation_pending and all(
                tool.arguments.get(field) == arguments.get(field)
                for field in ("action", "task_id", "status")
            )]
            if widgets:
                widgets = widgets[-1:]
                widgets[0].adopt(key, arguments)
        if not widgets:
            widget = ToolCall(key, name, arguments)
            await self._mount_tool(key, widget)
            widgets = [widget]
        for widget in widgets:
            if expected_s is not None and widget.expected_s != expected_s:
                widget.expected_s = expected_s
                widget._refresh_head()
            if phase == "start":
                if percent is not None:
                    widget.percent = percent
                    widget._refresh_head()
                continue
            # File events already carry the exact diff and outcome. A tool's
            # aggregate result must not be printed inside every file preview.
            if widget.file_recorded:
                widget._refresh_head()
                widget._refresh_body()
                continue
            widget.apply(phase=phase, result=result, error=error, output=output, percent=percent, output_mode=output_mode)
        self._release_finished_activity()

    def _mounted_after(self, widget: Widget | None, anchor: Widget) -> bool:
        """Whether ``widget`` sits below ``anchor`` in this bubble."""
        if widget is None or not widget.is_attached or widget.parent is not self:
            return False
        children = list(self.children)
        try:
            return children.index(widget) > children.index(anchor)
        except ValueError:
            return False

    async def _ensure_cluster(self, kind: str, call_id: str = "") -> ToolCluster | None:
        current = self._cluster
        # Only extend the newest block: reasoning or a card mounted since then
        # would leave new rows above the live tail (or inside a paged-out block).
        # A full cluster stops mounting rows (it pages them): a new call opens
        # a new one so the newest row is still painted at the bottom. The files
        # of one large patch stay together under the cluster's own paging.
        # No call id (a replayed batch) counts as the same call.
        same_call = not call_id or bool(
            current and current.tools and call_id in current.tools[-1].call_ids
        )
        if (current is not None and current.kind == kind and current.is_mounted
                and self._activity_blocks and self._activity_blocks[-1] is current
                and (same_call or len(current.tools) < current._visible_limit)
                and not self._mounted_after(self._progress_line, current)):
            return current
        preview = await self._ready_preview()
        if preview is None:
            return None
        cluster = ToolCluster(kind)
        self._cluster = cluster
        await self.mount(cluster, before=preview)
        self._register_activity(cluster)
        return cluster

    async def note_file_edit(
        self, path: str, added: int, removed: int, *, diff: str = "",
        call_id: str = "", kind: str = "", phase: str = "end",
        tool: str = "edit_file", error: str | None = None,
        truncated: bool = False, binary: bool = False,
    ) -> None:
        if not path or not self.is_attached:
            return
        file_key = (call_id, activity_path_key(path))
        match = self._file_tools.get(file_key)
        if match is None:
            match = next((
                row for row in self._tools.values()
                if not row.file_path and (
                    (call_id and call_id in row.call_ids)
                    or (not call_id and row.group_key == edit_group_key(tool, {"path": path}))
                )
            ), None)
        if match is None:
            # A patch can change several files, and a replay can deliver the
            # file event before the tool event. Give every file its own row.
            key = call_id or f"file:{len(self._tools)}"
            match = ToolCall(key, tool, {"path": path})
            self._tools[f"{key}:file:{len(self._tools)}"] = match
            cluster = await self._ensure_cluster("edit", call_id)
            if cluster is None:
                return
            await cluster.add_call(match)
        self._file_tools[file_key] = match
        match.file_path = path
        match.file_truncated = truncated
        match.file_binary = binary
        if phase == "start":
            match._refresh_head()
            return
        match.file_recorded = phase == "end"
        match.file_operation = kind if phase == "end" else ""
        match.set_diff(added if phase == "end" else 0, removed if phase == "end" else 0)
        match.diff_text = diff.strip("\n") if phase == "end" else ""
        match.apply(phase=phase, error=error)
        self._release_finished_activity()

    async def progress(self, text: str, *, transient: bool = False) -> None:
        if not self.is_attached:
            return
        preview = await self._ready_preview()
        if preview is None:
            return
        if not transient:
            streaming = self.text.strip() if self._buffer else ""
            if streaming and (
                streaming == text.strip() or narration_text(streaming) == narration_text(text)
            ):
                # The same narration is still on screen as the live stream: the
                # stream end moves it into the chain. Mounting it now would show
                # it twice until then.
                return
            if is_rich_narration(text):
                # Tables, lists, headings, bold and code stay rendered, with
                # the answer's colors, instead of one flattened grey line.
                source = text.strip()
                if self._recent_narration(source):
                    return
                self._drop_progress_line()
                note = InterimMarkdown(source)
                await self.mount(note, before=preview)
                self._register_activity(note)
                self._cluster = None
                self._refresh_preview()
                return
            # Narration stays in the chain, one line before the steps it
            # announces: narration, tools, narration, tools, answer.
            text = narration_text(text)
            if self._recent_narration(text):
                return
            self._drop_progress_line()
            line = ProgressLine(text)
            await self.mount(line, before=preview)
            self._register_activity(line)
            self._cluster = None
            self._refresh_preview()
            return
        # A transient hint replaces the previous one instead of stacking.
        if self._progress_line is not None and self._progress_line.is_attached:
            self._progress_line.update(f"· {text}")
            self._progress_line.narration = text
            self.move_child(self._progress_line, before=preview)
            self._refresh_preview()
            return
        line = ProgressLine(text)
        self._progress_line = line
        await self.mount(line, before=preview)
        self._refresh_preview()

    def _recent_narration(self, text: str, window: int = 8) -> bool:
        """Whether this narration is already in the chain since the last one.

        The stream end and the progress event both deliver the same narration;
        a tool card mounted between them used to defeat a last-block check and
        print the line twice.
        """
        for block in reversed(self._activity_blocks[-window:]):
            if isinstance(block, (ProgressLine, InterimMarkdown)):
                return block.is_attached and narration_text(block.narration) == narration_text(text)
        return False

    def _drop_progress_line(self) -> None:
        line = self._progress_line
        self._progress_line = None
        if line is not None and line.is_attached:
            line.remove()

    async def prompt_echo(self, echo: PromptEcho) -> None:
        """Keep a closed question or permission in this turn's chain."""
        preview = await self._ready_preview()
        if preview is None:
            return
        self._cluster = None
        await self.mount(echo, before=preview)
        self._register_activity(echo)

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
        if not self.is_attached:
            return
        preview = await self._ready_preview()
        if preview is None:
            return
        card = self._subagents.get(task_id)
        if card is None:
            card = SubagentCard(task_id)
            self._subagents[task_id] = card
            self._cluster = None
            await self.mount(card, before=preview)
        card.apply(label, phase, status_line, model, iteration, done, error)

    # -- body -------------------------------------------------------------

    def _folded(self) -> bool:
        """Fold only live reflection. Finished answers and user prompts stay open."""
        if self.finished or looks_like_client_prompt(self.text):
            return False
        raw = self.text.strip()
        return len(raw) > PREVIEW_CHARS or len(raw.split()) > 28

    def _set_child_state(self, add: bool, name: str, *child_classes: str) -> None:
        """Toggle a state class read only by direct children with ``child_classes``.

        A class change restyles the whole subtree by default: on a long turn
        (dozens of tool rows, a long answer) each toggle froze the CLI for up
        to a second (freeze log: ``add_class("-open")`` in ``delta``,
        ``remove_class`` in ``stream_end``; ``-copyable`` cost 220 ms per
        finish). Only the children named here read these classes, so only
        they are restyled.
        """
        if self.has_class(name) == add:
            return
        self.set_class(add, name, update=False)
        if not self.is_attached:
            return
        targets = [child for child in self.children if any(child.has_class(c) for c in child_classes)]
        if targets:
            self.app.stylesheet.update_nodes(targets, animate=False)

    def _set_open_class(self, add: bool) -> None:
        self._set_child_state(add, "-open", "assistant-preview", "assistant-body")

    def _sync_layers(self) -> None:
        """Keep preview and body from painting on the same cells (Windows Terminal)."""
        if not self.is_mounted:
            return
        try:
            preview = self.query_one(".assistant-preview", Static)
            body = self.query_one(".assistant-body.-answer", Markdown)
        except Exception:  # noqa: BLE001
            return
        preview.display = not self._open
        body.display = self._open
        if self._open:
            preview.update("")

    async def reveal(self) -> None:
        """Show the full answer (end of turn, or a question to the user)."""
        self._open = True
        self._set_open_class(True)
        self._sync_layers()
        await self._paint_body(force=True)

    def _preview_markup(self) -> str:
        raw = self.text.strip()
        if not raw:
            return ""
        preview = assistant_preview(raw)
        line = _markup_escape(preview)
        if not self._folded():
            return line
        plain = " ".join(markdown_plain(raw).split())
        hidden = plain[len(preview.rstrip("…")):] if len(plain) > len(preview) else ""
        hint = f"+{token_label(hidden)} · click" if hidden.strip() else "click"
        return f"{line}  [$text-muted]{hint}[/]"

    def _refresh_preview(self) -> None:
        if not self.is_mounted:
            return
        try:
            preview = self.query_one(".assistant-preview", Static)
        except Exception:  # noqa: BLE001
            return
        if self._open or not self.text.strip():
            # No "◌ …" placeholder: the working line says the turn is live.
            if preview.content:
                preview.update("")
            preview.display = False
            return
        preview.display = True
        preview.update(self._preview_markup())

    def toggle_body(self) -> None:
        self._open = not self._open
        self._set_open_class(self._open)
        self._sync_layers()
        if self._open and self.text.strip():
            self.run_worker(self._paint_body(force=True), exclusive=True, group="assistant-body")

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if target is None:
            return
        classes = set(getattr(target, "classes", ()) or ())
        # Only the folded one-line preview opens the answer. A click (or the
        # end of a drag selection) on an open answer never folds it back into
        # its raw preview line.
        if self._open or self.screen.get_selected_text():
            return
        if "assistant-preview" in classes or target is self:
            self.toggle_body()
            event.stop()

    async def _paint_body(self, *, force: bool = False) -> None:
        """Append only the changed markdown; serialize final and scheduled paints."""
        async with self._paint_lock:
            self._refresh_preview()
            if not self._open and not force:
                return
            body = await self._ready_body()
            if body is None:
                return
            text = readable_assistant_markdown(self.text) if force else self.text
            self.set_class(text.startswith("## Validation pending\n"), "-validation-pending")
            if text == self._painted_markdown:
                return
            if text.startswith(self._painted_markdown):
                await body.append(text[len(self._painted_markdown):])
            else:
                await body.update(text)
            self._painted_markdown = text

    async def _flush_stream(self) -> None:
        if not self.is_attached:
            self._paint_timer = None
            return
        if self.screen is not self.app.screen:
            # The full source stays in the buffer while a modal is open.
            # Mounting hidden paragraphs would still reflow the background
            # screen and delay navigation in the foreground.
            self._paint_timer = schedule_paint(self, "stream", self._flush_stream, delay=0.1)
            return
        if self._painted_markdown and self._open and not on_screen(self):
            # Scrolled out of view (parallel answers above the followed
            # one): each grown paragraph would reflow the whole screen for
            # nothing visible. The text stays buffered and paints as soon
            # as this answer is back in view, or when it finishes.
            self._paint_timer = schedule_paint(self, "stream", self._flush_stream, delay=0.25)
            return
        count = len(self._buffer)
        try:
            if not self._open and looks_like_client_prompt(self.text):
                self._open = True
                self._set_open_class(True)
                self._sync_layers()
            await self._paint_body()
            transcript = self.parent
            if isinstance(transcript, Transcript):
                transcript.follow()
        finally:
            self._paint_timer = None
        if len(self._buffer) != count and self.is_attached:
            self._paint_timer = schedule_paint(self, "stream", self._flush_stream, delay=stream_paint_delay(self))

    async def delta(self, text: str) -> None:
        if not text or not self.is_attached:
            return
        await self._composed.wait()
        if self.finished:
            self.finished = False
            # More text is coming: the copy icon returns with the new end.
            self._set_child_state(False, "-copyable", "assistant-copy")
        self._drop_progress_line()
        self.streamed = True
        self._buffer.append(text)
        if len(self._buffer) == 1 and not self._open:
            self._open = True
            self._set_open_class(True)
            self._sync_layers()
        if self._paint_timer is None:
            self._paint_timer = schedule_paint(self, "stream", self._flush_stream, delay=stream_paint_delay(self))

    async def stream_end(self, *, resuming: bool = False, discarded: bool = False) -> None:
        if self._paint_timer is not None:
            self._paint_timer.stop()
            self._paint_timer = None
        if discarded:
            # A mid-stream retry streams the whole answer again: drop the
            # partial text outright, it must not linger as narration above it.
            body = await self._ready_body()
            async with self._paint_lock:
                self._buffer.clear()
                self._painted_markdown = ""
                self._open = False
                self._set_open_class(False)
                self._sync_layers()
                if body is not None:
                    await body.update("")
            self._refresh_preview()
            return
        if resuming:
            body = await self._ready_body()
            if body is None:
                return
            # The next iteration is a fresh stream, with one status while tools run.
            async with self._paint_lock:
                narration = self.text.strip()
                self._buffer.clear()
                self._painted_markdown = ""
                self._open = False
                self._set_open_class(False)
                self._sync_layers()
                await body.update("")
            if narration:
                # The whole narration stays in the chain (wrapped, or rendered
                # when it carries Markdown), not just its first sentence.
                await self.progress(narration)
            else:
                self._refresh_preview()
            return
        if looks_like_client_prompt(self.text):
            await self.reveal()
            return
        if self._buffer:
            await self._paint_body(force=self._open)

    async def set_text(self, text: str, *, render_as: str = "markdown") -> None:
        if not self.is_attached:
            return
        self._drop_progress_line()
        await self.stream_end()
        self._buffer = [text]
        if self.finished or looks_like_client_prompt(text):
            body = await self._ready_body()
            if body is None:
                return
            if render_as == "text":
                text = (
                    "```text\n" + text + "\n```"
                    if "\n" in text and not text.lstrip().startswith("#")
                    else text
                )
                self._open = True
                self._set_open_class(True)
                self._sync_layers()
                await body.update(text)
                self._painted_markdown = text
                return
            await self.reveal()
            return
        self._refresh_preview()
        if not self._open:
            return
        body = await self._ready_body()
        if body is None:
            return
        if render_as == "text":
            text = (
                "```text\n" + text + "\n```"
                if "\n" in text and not text.lstrip().startswith("#")
                else text
            )
            await body.update(text)
            self._painted_markdown = text
            return
        await self._paint_body(force=True)

    async def finish(
        self, *, latency_ms: int | None, model: str | None, preset: str | None,
    ) -> None:
        if not self.is_attached:
            return
        await self._composed.wait()
        if not self.is_attached:
            return
        await self.stream_end()
        self._drop_progress_line()
        if not self.is_attached:
            return
        if self._reasoning is not None and not self._reasoning._done:
            self._reasoning.finish()
        self.finished = True
        # Only a written answer gets the icon; tool rows copy on their own.
        self._set_child_state(bool(self.text.strip()), "-copyable", "assistant-copy")
        await self.reveal()
        if not self.is_attached:
            return
        for tool in self._tools.values():
            if tool.phase in {"start", "output"}:
                tool.apply(phase="cancelled", error="Interrupted before a result was received.")
            if tool.cluster_kind == "explore":
                tool.collapse()
                continue
            # Edits / runs keep the numbered diff open. Do not fold it on finish.
            if tool.diff_text or tool._has_preview() or tool_verb(tool.tool_name) in {
                "edit",
                "create",
            }:
                tool._reveal_if_preview()
                tool._refresh_body()
                continue
            tool.collapse()
        self._release_finished_activity()
        foot = self._query_static(".assistant-foot")
        if foot is None:
            return
        if self._tools:
            completed = [
                tool for tool in self._tools.values() if tool.phase == "end"
                and (tool.file_recorded or tool_verb(tool.tool_name) not in {"edit", "create"})
            ]
            files: dict[str, tuple[int, int]] = {}
            other = []
            for tool in completed:
                if tool.file_recorded:
                    if tool.file_operation == "unchanged":
                        continue
                    key = activity_path_key(tool.file_path)
                    plus, minus = files.get(key, (0, 0))
                    files[key] = plus + tool.added, minus + tool.removed
                else:
                    other.append((tool.tool_name, 0, 0))
            summary_rows = [("edit_file", plus, minus) for plus, minus in files.values()] + other
            summary = format_turn_summary(
                summary_rows
            ) if summary_rows else "No completed operations"
            foot.update(summary)
            foot.add_class("-visible")
        else:
            foot.update("")
            foot.remove_class("-visible")

    @property
    def text(self) -> str:
        return "".join(self._buffer)

    def copy_text(self) -> str:
        # The answer as written (Markdown source), not the activity log:
        # tool rows copy on their own. A turn with only tools copies them.
        answer = self.text.strip()
        if answer:
            return clip_transcript(answer)
        parts = [tool.copy_text() for tool in self._tools.values()]
        return clip_transcript("\n\n".join(part for part in parts if part))


# ---------------------------------------------------------------------------
# Approval / choice cards
# ---------------------------------------------------------------------------


class ApprovalCard(Vertical):
    """Permission request raised by a tool (exec, write outside workspace...)."""

    DEFAULT_CSS = """
    ApprovalCard {
        height: auto;
        margin: 1 0 0 0;
        padding: 1 1;
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
        # Commands may contain unmatched brackets or end in a backslash.
        # Keep external text out of the markup parser, including after truncation.
        title = Text("⚠ Permission needed  ")
        title.append(self.tool, style="dim")
        yield Static(title, classes="card-title", markup=False)
        detail = Text(self.action)
        if self.reason:
            detail.append("\nwhy:", style="dim").append(f" {self.reason}")
        if self.detail:
            detail.append(f"\n{self.detail[:600]}")
        if self.consequence:
            detail.append("\nimpact:", style="dim").append(f" {self.consequence}")
        if self.scope:
            detail.append("\nscope:", style="dim").append(f" {self.scope}")
        yield Static(detail, classes="card-detail", markup=False)
        with Horizontal():
            yield Button(Text("Allow  [y]"), variant="success", id="allow")
            if self.remember_offered:
                yield Button(Text("Always  [a]"), variant="primary", id="always")
            yield Button(Text("Deny  [n]"), variant="error", id="deny")

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
        title = Text(f"{'✓' if allowed else '✗'} Permission {verdict}  ")
        title.append(self.tool, style="dim")
        if reason:
            title.append(f"  {reason}", style="dim")
        self.query_one(".card-title", Static).update(title)


class ChoiceCard(Vertical):
    """A question with options asked by the agent (ask_user tool)."""

    DEFAULT_CSS = """
    ChoiceCard {
        height: auto;
        margin: 1 0 0 0;
        padding: 1 1;
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
        yield Static(f"❔ {self.question}", classes="card-title", markup=False)
        items: list[Option] = []
        for idx, opt in enumerate(self.options, start=1):
            oid = str(opt.get("id") or opt.get("value") or idx)
            label = str(opt.get("label") or opt.get("title") or oid)
            desc = str(opt.get("description") or "")
            text = Text(f"{idx}.", style="bold")
            text.append(f" {label}", style="not bold")
            if oid == self.recommended_id:
                text.append(" ★", style=f"bold {self.app.get_css_variables()['accent']}")
            if desc:
                text.append(f"\n   {desc}", style="dim not bold")
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

    def on_key(self, event: events.Key) -> None:
        if not self.closed and event.key in "123456789" and self.query_one(OptionList).has_focus:
            event.prevent_default()
            event.stop()
            self.pick_index(int(event.key) - 1)

    def answer(self, *, option_id: str = "", skipped: bool = False, custom_text: str = "") -> None:
        if self.closed:
            return
        self.post_message(self.Answered(self.request_id, option_id, skipped, custom_text))

    def chosen_label(self, option_id: str = "", skipped: bool = False, custom_text: str = "") -> str:
        """The answer as the user saw it (same id fallback as the options)."""
        if skipped:
            return "skipped"
        if custom_text:
            return custom_text
        for idx, opt in enumerate(self.options, start=1):
            if str(opt.get("id") or opt.get("value") or idx) == option_id:
                return str(opt.get("label") or opt.get("title") or option_id)
        return option_id or "no answer"

    def close(self, option_id: str = "", skipped: bool = False, custom_text: str = "") -> None:
        self.closed = True
        self.add_class("-closed")
        self.answer_label = self.chosen_label(option_id, skipped, custom_text)
        title = Text(f"✓ {self.question}  ")
        title.append(f"→ {self.answer_label}", style="dim")
        with contextlib.suppress(NoMatches):
            self.query_one(".card-title", Static).update(title)


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


class QueuedPromptRow(Horizontal):
    DEFAULT_CSS = """
    QueuedPromptRow { height: 1; background: $background; }
    QueuedPromptRow Static { width: 1fr; height: 1; color: $text-muted; text-overflow: ellipsis; }
    QueuedPromptRow Button.-style-default {
        width: auto; min-width: 0; height: 1; min-height: 1;
        border: none; padding: 0 1; background: $background; color: $text-muted; text-style: none;
    }
    QueuedPromptRow .queue-remove:hover { color: $error; }
    QueuedPromptRow Button.queue-send { color: $primary; }
    """

    class Removed(Message):
        def __init__(self, prompt_id: int) -> None:
            super().__init__()
            self.prompt_id = prompt_id

    class Sent(Message):
        def __init__(self, prompt_id: int) -> None:
            super().__init__()
            self.prompt_id = prompt_id

    class Edited(Message):
        def __init__(self, prompt_id: int) -> None:
            super().__init__()
            self.prompt_id = prompt_id

    def __init__(self, prompt_id: int, text: str, position: int) -> None:
        super().__init__()
        self.prompt_id = prompt_id
        self.text = text
        self.position = position

    def compose(self) -> ComposeResult:
        preview = " ".join(display_user_text(self.text).split())
        yield Static(f"{self.position}. {preview}", markup=False)
        yield Button("Edit", classes="queue-edit", compact=True,
                     tooltip="Load this message in the composer to change it. Your current draft goes back to the queue.")
        yield Button("Send now", classes="queue-send", compact=True,
                     tooltip="Send this message now. During a reply, add it to the current task.")
        yield Button("Remove", classes="queue-remove", compact=True)

    def set_position(self, position: int) -> None:
        if position != self.position:
            self.position = position
            preview = " ".join(display_user_text(self.text).split())
            self.query_one(Static).update(f"{position}. {preview}")

    @on(Button.Pressed)
    def remove_prompt(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.has_class("queue-send"):
            self.post_message(self.Sent(self.prompt_id))
        elif event.button.has_class("queue-edit"):
            self.post_message(self.Edited(self.prompt_id))
        else:
            self.post_message(self.Removed(self.prompt_id))


class PromptQueue(Vertical):
    """Pending prompts stay outside the transcript until they are sent."""

    DEFAULT_CSS = """
    PromptQueue { height: auto; display: none; margin: 0; padding: 0 1; background: $background; }
    PromptQueue > Horizontal { height: auto; min-height: 1; }
    PromptQueue #queue-title { width: 1fr; color: $primary; }
    PromptQueue #queue-items { height: auto; max-height: 5; background: $background; }
    PromptQueue Button.-style-default {
        width: auto; min-width: 0; height: 1; min-height: 1;
        border: none; padding: 0 1; background: $background; color: $primary; text-style: none;
    }
    """

    class Resumed(Message):
        pass

    class ParallelRequested(Message):
        """Run every queued prompt now, each in its own parallel agent."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", id="queue-title", markup=False)
            yield Button("Run in parallel (F6)", id="queue-parallel", compact=True)
            yield Button("Resume queue", id="queue-resume", compact=True)
        yield VerticalScroll(id="queue-items")

    async def set_items(self, items: list[tuple[int, str]], *, paused: bool) -> None:
        if not self.is_mounted or not self.query("#queue-title"):
            return
        self.display = bool(items)
        self.query_one("#queue-title", Static).update(
            f"Queued {len(items)} · {'paused' if paused else 'after current reply'}"
            " · Alt+Enter sends to the running task"
        )
        self.query_one("#queue-resume", Button).display = paused
        body = self.query_one("#queue-items", VerticalScroll)
        existing = {row.prompt_id: row for row in body.query(QueuedPromptRow)}
        wanted = {key for key, _ in items}
        for key, row in existing.items():
            if key not in wanted:
                await row.remove()
        new_rows = []
        for position, (key, text) in enumerate(items, 1):
            if key in existing:
                existing[key].set_position(position)
            else:
                new_rows.append(QueuedPromptRow(key, text, position))
        if new_rows:
            await body.mount(*new_rows)

    @on(Button.Pressed, "#queue-resume")
    def resume_queue(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.Resumed())

    @on(Button.Pressed, "#queue-parallel")
    def run_parallel(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.ParallelRequested())


class StarField(Widget):
    """A row of small stars drifting slowly under the prompt.

    The app's status tick calls ``advance``; the row owns no timer. Only this
    one-row strip repaints, so the motion costs a few hundred bytes a step.
    """

    DEFAULT_CSS = """
    StarField {
        height: 1;
        width: 1fr;
        background: $background;
    }
    """

    GLYPHS = ("·", "·", "∙", "⋆", "✦")
    #: Brightness per twinkle step: mostly faint, a short glint.
    TWINKLE = (0, 0, 1, 2, 3, 2, 1, 0, 0, 0)
    #: One star per this many cells.
    DENSITY = 9

    def __init__(self, seed: int, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._seed = seed
        self._stars: list[tuple[int, int, int, str]] = []
        self._stars_width = -1
        self.frame = 0

    def advance(self) -> None:
        self.frame += 1
        self.refresh()

    def _layout_stars(self, width: int) -> list[tuple[int, int, int, str]]:
        if width != self._stars_width:
            import random

            rng = random.Random(self._seed * 7919 + width)
            self._stars = [
                (rng.randrange(width), rng.choice((2, 3, 5)), rng.randrange(len(self.TWINKLE)), rng.choice(self.GLYPHS))
                for _ in range(max(2, width // self.DENSITY))
            ]
            self._stars_width = width
        return self._stars

    def _inks(self) -> tuple[Style, ...]:
        theme = self.app.current_theme
        base = self.styles.background
        ink = Color.parse(theme.foreground or "#E0E0E0")
        accent = Color.parse(theme.primary or theme.accent or "#5EA8FF")
        return tuple(
            Style(color=base.blend(color, amount).rich_color, bgcolor=base.rich_color)
            for color, amount in ((ink, 0.22), (ink, 0.38), (ink, 0.6), (accent, 0.75))
        )

    def render_line(self, y: int) -> Strip:
        width = content_width(self)
        background = Style(bgcolor=self.styles.background.rich_color)
        if width <= 0:
            return Strip.blank(0, background)
        cells: dict[int, tuple[str, int]] = {}
        for x0, speed, phase, glyph in self._layout_stars(width):
            # Drift left at the star's own pace; faster ones read as nearer.
            x = (x0 - self.frame // speed) % width
            cells[x] = (glyph, self.TWINKLE[(self.frame + phase) % len(self.TWINKLE)])
        inks = self._inks()
        segments: list[Segment] = []
        gap = 0
        for x in range(width):
            star = cells.get(x)
            if star is None:
                gap += 1
                continue
            if gap:
                segments.append(Segment(" " * gap, background))
                gap = 0
            segments.append(Segment(star[0], inks[star[1]]))
        if gap:
            segments.append(Segment(" " * gap, background))
        return Strip(segments, width)


class ComposerShell(Vertical):
    """The prompt between two gray hairlines, open on both sides, above a
    row of drifting stars."""

    DEFAULT_CSS = """
    ComposerShell {
        height: auto;
        background: $background;
        padding: 0;
        border: none;
        border-top: solid $foreground 35%;
        border-bottom: solid $foreground 35%;
    }
    ComposerShell > .composer-input { height: auto; background: $background; padding: 0 1; }
    ComposerShell #composer-prompt { width: 2; height: 1; color: $text-muted; }
    ComposerShell.-focus #composer-prompt { color: $primary; }
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

    def on_click(self, event: events.Click) -> None:
        """Clicking anywhere in the prompt shell focuses the composer."""
        if isinstance(event.widget, Composer):
            return
        node = event.widget
        while node is not None:
            if isinstance(node, ComposerMeta):
                return  # the meta row opens its own pickers
            node = node.parent
        composer = self.query(Composer)
        if composer:
            composer.first().focus()

    def set_busy(self, busy: bool) -> None:
        self.set_class(busy, "-busy")

    def set_focus(self, focused: bool) -> None:
        self.set_class(focused, "-focus")


_TEST_TOTALS = re.compile(r"\b\d+ (?:passed|failed|tests?|errors?)\b", re.IGNORECASE)


class ComposerMeta(Horizontal):
    """Mode, model, effort and context on the footer line under the chat."""

    DEFAULT_CSS = """
    ComposerMeta {
        width: auto;
        height: 1;
        padding: 0 0 0 1;
        background: $background;
    }
    ComposerMeta #meta-mode {
        width: auto;
        color: $primary 75%;
        text-style: none;
    }
    ComposerMeta #meta-mode:hover { color: $foreground; }
    /* A quiet line: mode in a soft tint, model, effort and path in gray. */
    ComposerMeta #meta-sep { width: auto; color: $text-muted 60%; padding: 0 1; }
    ComposerMeta #meta-model { width: auto; min-width: 0; max-width: 36; height: 1; color: $text-muted; text-overflow: ellipsis; }
    ComposerMeta #meta-model:hover { color: $foreground; }
    /* Live first, in its own green ink: never mistaken for the mode or model. */
    ComposerMeta #meta-live { width: auto; height: 1; padding-right: 2; color: $success; text-style: bold; }
    ComposerMeta #meta-live:hover { color: $foreground; }
    ComposerMeta #meta-live.-on { color: $background; background: $success; padding: 0 1; margin-right: 1; }
    ComposerMeta #meta-reasoning { width: auto; height: 1; padding-left: 1; color: $text-muted; }
    ComposerMeta #meta-reasoning:hover { color: $foreground; }
    ComposerMeta #meta-effort { width: auto; height: 1; padding-left: 1; color: $text-muted 70%; }
    /* Effort in the activity palette: green, mustard, blue, lavender. */
    ComposerMeta #meta-reasoning.-lvl-1, ComposerMeta #meta-effort.-lvl-1 { color: #8FBC8F; }
    ComposerMeta #meta-reasoning.-lvl-2, ComposerMeta #meta-effort.-lvl-2 { color: #D6BC78; }
    ComposerMeta #meta-reasoning.-lvl-3, ComposerMeta #meta-effort.-lvl-3 { color: #5EA8FF; }
    ComposerMeta #meta-reasoning.-lvl-4, ComposerMeta #meta-effort.-lvl-4 { color: #C6AFE3; }
    ComposerMeta #meta-reasoning.-lvl-1:light, ComposerMeta #meta-effort.-lvl-1:light { color: #2D6A4F; }
    ComposerMeta #meta-reasoning.-lvl-2:light, ComposerMeta #meta-effort.-lvl-2:light { color: #795B13; }
    ComposerMeta #meta-reasoning.-lvl-3:light, ComposerMeta #meta-effort.-lvl-3:light { color: #3D82FF; }
    ComposerMeta #meta-reasoning.-lvl-4:light, ComposerMeta #meta-effort.-lvl-4:light { color: #705393; }
    ComposerMeta #meta-reasoning.-glow { text-style: bold; }
    ComposerMeta #meta-reasoning.-lvl-0.-glow { color: $primary; }
    """

    #: Bars per effort level; Auto shows none.
    EFFORT_LEVELS = {"none": 0, "minimal": 1, "low": 1, "medium": 2, "high": 3, "xhigh": 4, "max": 4}
    EFFORT_BARS = "▂▄▆█"
    #: The new level fills one bar per frame, glows, then settles.
    EFFORT_FRAME_S = 0.07
    EFFORT_GLOW_FRAMES = 12

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._effort: str | None = None
        self._effort_frame = 0
        self._effort_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        from navin.tui.live_voice import cli_live_enabled

        if cli_live_enabled():
            yield Static("◉ Live", id="meta-live", markup=False)
        yield Static("", id="meta-mode", markup=True)
        yield Static("", id="meta-sep", markup=True)
        yield Static("", id="meta-model", markup=True)
        yield Static("Auto", id="meta-reasoning", markup=False)
        yield Static("", id="meta-effort", markup=False)

    def _effort_level(self) -> int:
        return self.EFFORT_LEVELS.get((self._effort or "").lower(), 0)

    def _effort_bars(self, count: int | None = None) -> str:
        level = self._effort_level()
        return self.EFFORT_BARS[: level if count is None else min(level, count)]

    def _tint_effort(self) -> None:
        tint = {f"-lvl-{self._effort_level()}"}
        every = {f"-lvl-{level}" for level in range(len(self.EFFORT_BARS) + 1)}
        for widget_id in ("#meta-reasoning", "#meta-effort"):
            swap_classes(self.query_one(widget_id, Static), every - tint, tint)

    def _show_effort(self, effort: str) -> None:
        """Paint the effort level; a change animates its bars in."""
        changed = self._effort is not None and effort != self._effort
        self._effort = effort
        self._tint_effort()
        if self._effort_timer is not None:
            self._effort_timer.stop()
            self._effort_timer = None
        if changed and self.is_attached and self.app.animation_level != "none":
            self._effort_frame = 0
            self._effort_step()
            self._effort_timer = self.set_interval(self.EFFORT_FRAME_S, self._effort_step)
            return
        self._settle_effort()

    def _effort_step(self) -> None:
        self._effort_frame += 1
        reasoning_w = self.query_one("#meta-reasoning", Static)
        effort_w = self.query_one("#meta-effort", Static)
        reasoning_w.add_class("-glow")
        effort_w.add_class("-glow")
        effort_w.update(self._effort_bars(self._effort_frame))
        if self._effort_frame >= len(self.EFFORT_BARS) + self.EFFORT_GLOW_FRAMES:
            if self._effort_timer is not None:
                self._effort_timer.stop()
                self._effort_timer = None
            self._settle_effort()

    def _settle_effort(self) -> None:
        reasoning_w = self.query_one("#meta-reasoning", Static)
        effort_w = self.query_one("#meta-effort", Static)
        reasoning_w.remove_class("-glow")
        effort_w.remove_class("-glow")
        bars = self._effort_bars()
        if effort_w.content != bars:
            effort_w.update(bars)

    def set_meta(
        self,
        *,
        mode: str,
        model: str = "",
        extra: str = "",
        busy: bool = False,
        spin: int = 0,
        provider: str = "",
        context_used: int = 0,
        context_window: int = 0,
        reasoning: str = "",
        permission: str | None = None,
    ) -> None:
        def update(widget: Static, value: str) -> None:
            if widget.content != value:
                widget.update(value)

        name, _slug_provider = split_model_slug(model)
        if is_bedrock_model_id(name):
            name = bedrock_display_name(name)  # "Claude Sonnet 5.5", not the profile ID
        mode_w = self.query_one("#meta-mode", Static)
        sep_w = self.query_one("#meta-sep", Static)
        model_w = self.query_one("#meta-model", Static)
        reasoning_w = self.query_one("#meta-reasoning", Static)
        update(reasoning_w, reasoning or "Auto")
        reasoning_w.tooltip = "Choose native reasoning effort (Ctrl+Shift+R)"
        if reasoning != self._effort:
            self._show_effort(reasoning)
        # Context use and the permission posture stay off the footer: full
        # access is the default and /permission changes it.
        del context_used, context_window, permission
        # The working line under the chat is the one live status; the
        # footer stays still while a turn runs.
        del busy, spin
        prefix = ""
        if extra:
            update(mode_w, extra)
            update(sep_w, "")
            update(model_w, "")
            return
        update(mode_w, f"{prefix}{escape(mode)}")
        if name:
            update(sep_w, "·")
            update(model_w, escape(name))
        else:
            update(sep_w, "·")
            update(model_w, "no model")

    def set_live(self, on: bool) -> None:
        """The Live call next to the model: click to call, click to hang up."""
        try:
            live_w = self.query_one("#meta-live", Static)
        except Exception:  # noqa: BLE001 - the terminal Live is switched off
            return
        label = "● Live" if on else "◉ Live"
        if live_w.content != label:
            live_w.update(label)
        live_w.set_class(on, "-on")
        live_w.tooltip = "Hang up the Live call (/live)" if on else "Start a Live voice call (/live)"

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if isinstance(target, Static) and target.id == "meta-live":
            event.stop()
            self.app.call_later(self.app.run_action, "toggle_live")
            return
        if isinstance(target, Static) and target.id in {"meta-reasoning", "meta-effort"}:
            event.stop()
            self.app.call_later(self.app.run_action, "pick_reasoning")
            return
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
        min-height: 1;
        border: none !important;
        background: $background;
        color: $foreground;
        padding: 0 0 0 0;
        scrollbar-size-vertical: 1;
    }
    Composer:focus { border: none !important; }
    Composer .text-area--placeholder { color: $text-muted; }
    """

    BINDINGS = [
        # Most terminals send Ctrl+Enter as a plain Enter, so the message was
        # only queued and the running agent never saw it. Alt+Enter reaches the
        # app in every terminal; Alt+S covers Windows Terminal, where Alt+Enter
        # toggles full screen.
        Binding("ctrl+enter", "send_now", "Send now", show=False),
        Binding("alt+enter", "send_now", "Send now", show=False),
        Binding("alt+s", "send_now", "Send now", show=False),
        Binding("ctrl+j", "newline", "Newline", show=False),
        Binding("shift+enter", "newline", "Newline", show=False),
        Binding("ctrl+a", "select_all", "Select all", show=False),
        Binding("super+a", "select_all", "Select all", show=False),
        Binding("ctrl+v", "paste_any", "Paste", show=False, priority=True),
        Binding("super+v", "paste_any", "Paste", show=False, priority=True),
        Binding("ctrl+c", "copy_any", "Copy", show=False),
        Binding("super+c", "copy_any", "Copy", show=False),
        Binding("ctrl+shift+v", "paste_any", "Paste", show=False),
        Binding("super+shift+v", "paste_any", "Paste", show=False),
        Binding("shift+insert", "paste_any", "Paste", show=False),
        Binding("ctrl+alt+v", "paste_any", "Paste", show=False),
        Binding("super+alt+v", "paste_any", "Paste", show=False),
        # Windows Terminal keeps Ctrl+V for itself and sends nothing when the
        # clipboard holds only an image: Alt+V reaches the app and attaches it.
        Binding("alt+v", "paste_image", "Paste image", show=False, priority=True),
        Binding("ctrl+f", "find", "Find", show=False),
        Binding("super+f", "find", "Find", show=False),
        Binding("pageup", "page_chat", "Page up", show=False, priority=True),
        Binding("pagedown", "page_chat_down", "Page down", show=False, priority=True),
        Binding("ctrl+up", "page_chat", "Page up", show=False, priority=True),
        Binding("ctrl+down", "page_chat_down", "Page down", show=False, priority=True),
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
        def __init__(self, text: str, *, send_now: bool = False) -> None:
            super().__init__()
            self.text = text
            self.send_now = send_now

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
        self._last_slash_prefix: str | None = None
        self.shortcut_keys: set[str] = set()
        self._last_paste = ""
        self._last_paste_at = 0.0
        self._last_paste_source = ""
        self._paste_generation = 0
        self._pastes: dict[str, str] = {}
        self._input_frame: tuple | None = None

    def _shell(self) -> ComposerShell | None:
        parent = self.parent
        while parent is not None:
            if isinstance(parent, ComposerShell):
                return parent
            parent = parent.parent
        return None

    def on_focus(self) -> None:
        shell = self._shell()
        if shell is not None:
            shell.set_focus(True)
        self._input_frame = None
        self.call_after_refresh(self._paint_input)

    def on_blur(self) -> None:
        shell = self._shell()
        if shell is not None:
            shell.set_focus(False)

    @property
    def is_slash_prefix(self) -> bool:
        text = self.text
        return text.startswith("/") and "\n" not in text and " " not in text

    def expand_for_submit(self) -> str:
        """Put pasted bodies back so the model receives the full prompt."""
        return expand_pasted_content(self.text, self._pastes)

    def _insert_paste(self, text: str, *, source: str = "clipboard") -> None:
        """Insert a paste once. A WT confirm + Ctrl+V must not double it."""
        payload = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not payload:
            return
        now = time.monotonic()
        if (payload == self._last_paste and source != self._last_paste_source
                and now - self._last_paste_at < 1.5):
            return
        self._paste_generation += 1
        self._last_paste = payload
        self._last_paste_at = now
        self._last_paste_source = source
        if should_collapse_pasted_text(payload):
            token = allocate_paste_token(len(payload), self._pastes)
            self._pastes[token] = payload
            insert = token
        else:
            insert = payload
        self.history.checkpoint()
        selection = ((0, 0), self.document.end) if not self.text.strip() else self.selection
        self.replace(insert, *selection, maintain_selection_offset=False)
        self.history.checkpoint()

    async def _on_paste(self, event: events.Paste) -> None:
        event.prevent_default()
        event.stop()
        # Bracketed paste contains literal newlines, not Enter key events.
        # Trust its payload instead of replacing it with an older local copy.
        if not (event.text or "").strip():
            # Windows Terminal, iTerm and most emulators catch Ctrl+V themselves:
            # with only an image in the clipboard they send an empty paste. Read
            # the OS clipboard so the image is still attached, like Codex does.
            self.run_worker(self._paste_from_clipboard(), group="clipboard", exclusive=True)
            return
        self._insert_paste(event.text, source="terminal")

    async def _on_key(self, event: events.Key) -> None:
        # Live command logs hold their (costly) repaints while the user types.
        note_input(self.app)
        if event.key == "escape":
            # TextArea may swallow Escape (focus release in indent mode), so
            # the app-level Stop binding never fired from the prompt.
            event.prevent_default()
            event.stop()
            await self.app.run_action("app.stop_turn")
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.expand_for_submit()))
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
        self._paint_input()

    def _paint_input(self) -> None:
        state = (self.text, self.selection, self.scroll_offset, self.region, self._cursor_visible)
        if state != self._input_frame and paint_input(self):
            self._input_frame = state

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        await super()._on_mouse_up(event)
        # MouseDown hides the cursor. Repaint once selection ends, even when
        # its position is unchanged and live output is awaiting layout.
        self._paint_input()

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._paint_input()

    def action_newline(self) -> None:
        self.insert("\n")

    def action_send_now(self) -> None:
        self.post_message(self.Submitted(self.expand_for_submit(), send_now=True))

    def action_copy_any(self) -> None:
        """Prefer transcript selection; otherwise copy the prompt selection."""
        from textual.actions import SkipAction

        selected = None
        with contextlib.suppress(Exception):
            selected = self.screen.get_selected_text()
        if not selected:
            selected = self.selected_text
        if selected:
            self.app.copy_to_clipboard(selected)
            return
        raise SkipAction()

    def action_paste_any(self) -> None:
        """Paste OS clipboard (Windows / WSL) or the in-app clipboard."""
        self.run_worker(self._paste_from_clipboard(), group="clipboard", exclusive=True)

    def action_paste_image(self) -> None:
        """Attach the clipboard image first (Alt+V), else paste its text."""
        self.run_worker(self._paste_from_clipboard(prefer_image=True), group="clipboard", exclusive=True)

    async def _attach_clipboard_image(self, generation: int, snapshot: object) -> bool | None:
        """True when attached, False when no image, None when the prompt moved on."""
        from navin.tui.images import image_token, save_clipboard_image

        try:
            image = await asyncio.to_thread(save_clipboard_image)
        except Exception:  # noqa: BLE001 - clipboard failures must not close the chat
            image = None
        if generation != self._paste_generation or snapshot != (self.text, self.selection):
            return None
        if image is None:
            return False
        self.insert(image_token(image) + " ")
        self.notify("Image attached - the model will see it.", timeout=3)
        return True

    async def _paste_from_clipboard(self, prefer_image: bool = False) -> None:
        from navin.tui.clipboard import pick_paste_text, read_clipboard

        if not prefer_image and getattr(self.app, "_copy_running", False) and self.app.clipboard:
            self._insert_paste(self.app.clipboard)
            return
        generation = self._paste_generation
        snapshot = (self.text, self.selection)
        tried_image = False
        if prefer_image:
            attached = await self._attach_clipboard_image(generation, snapshot)
            if attached is not False:
                return
            tried_image = True
        try:
            os_text = await asyncio.to_thread(read_clipboard)
        except Exception:  # noqa: BLE001 - clipboard failures must not close the chat
            os_text = ""
        if generation != self._paste_generation or snapshot != (self.text, self.selection):
            return
        text = pick_paste_text(self.app.clipboard, os_text) if os_text else ""
        if not text:
            if not tried_image:
                attached = await self._attach_clipboard_image(generation, snapshot)
                if attached is not False:
                    return
            text = pick_paste_text(self.app.clipboard, "")
        if text:
            self._insert_paste(text)
        else:
            self.notify("No clipboard text or image available. Use your terminal's Paste command.", timeout=3)

    def action_find(self) -> None:
        self.post_message(self.FindRequested())

    def _scroll_chat(self, direction: int) -> None:
        action = getattr(self.app, "action_page_transcript", None)
        if callable(action):
            action(direction)
            return
        self.post_message(self.PageChat(direction))

    def action_page_chat(self) -> None:
        self._scroll_chat(-1)

    def action_page_chat_down(self) -> None:
        self._scroll_chat(1)

    def action_cursor_page_up(self) -> None:
        """TextArea also binds PageUp; send it to the transcript, not the prompt."""
        self._scroll_chat(-1)

    def action_cursor_page_down(self) -> None:
        self._scroll_chat(1)

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
        self._paint_input()
        text = self.text
        prefix = text if text.startswith("/") and "\n" not in text and " " not in text else None
        if prefix != self._last_slash_prefix:
            self._last_slash_prefix = prefix
            self.post_message(self.SlashTyping(prefix))

    def set_text(self, text: str) -> None:
        display, pastes = collapse_text_for_composer(text)
        self._pastes = pastes
        self.load_text(display)
        self.move_cursor(self.document.end)

    def clear_text(self) -> None:
        self._paste_generation += 1
        self._last_paste = ""
        self._last_paste_at = 0.0
        self._pastes = {}
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
        options: list[Option] = []
        for row in matches[:40]:
            cmd = str(row["command"])
            hint = (
                f" [dim]{escape(str(row.get('arg_hint') or ''))}[/dim]"
                if row.get("arg_hint")
                else ""
            )
            title = escape(str(row.get("title") or ""))
            options.append(Option(f"[b]{cmd}[/b]{hint}  {title}", id=cmd))
        self.add_options(options)
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


class ChatColumn(LocalDisplayStyles, Vertical):
    """Transcript plus the prompt stack; their display toggles stay local."""


class Transcript(LocalDisplayStyles, VerticalScroll):
    can_focus = True

    class OlderRequested(Message):
        def __init__(self, generation: int) -> None:
            super().__init__()
            self.generation = generation

    DEFAULT_CSS = """
    Transcript {
        height: 1fr;
        align-vertical: bottom;
        padding: 0;
        background: $background;
        /* Its own bar stays hidden: EdgeScrollbar draws a thin one on the
           screen edge, past the sidebar. */
        scrollbar-size-vertical: 0;
    }
    """

    auto_follow = reactive(True)
    has_older = False
    history_generation = 0

    # Virtualization (navin.tui.virtual). Textual styles, arranges and keeps
    # alive every mounted widget, so the chat keeps only what is near the
    # viewport as widgets: one screen of margin on each side. Finished blocks
    # farther away become records in a RecordGap as tall as they were, and
    # are built again (cheaply) when the reader scrolls back to them.
    MARGIN_SCREENS = 1
    DROP_SCREENS = 2
    MIN_MARGIN_LINES = 24
    # The newest blocks always stay widgets: live code follows the tail.
    KEEP_TAIL_BLOCKS = 2
    # Records built per pass; a pass repeats until the margin is covered.
    MAX_BUILD_PER_PASS = 24
    MAX_PASSES = 8
    TRIM_DELAY_S = 0.3
    PREFETCH_SCREENS = 1

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loading_history = False
        self._sync_timer: Timer | None = None
        self._sync_due = 0.0
        self._sync_running = False
        self._sync_again = False
        self._adjusting_scroll = False
        self.records_built = 0
        self.records_made = 0

    class FollowChanged(Message):
        """The reader left the tail (scrolled up) or came back to it."""

        def __init__(self, transcript: Transcript, following: bool) -> None:
            super().__init__()
            self.transcript = transcript
            self.following = following

    @property
    def loading_history(self) -> bool:
        return self._loading_history

    @loading_history.setter
    def loading_history(self, value: bool) -> None:
        was = self._loading_history
        self._loading_history = bool(value)
        if was and not value:
            # Pages painted meanwhile were kept as widgets; settle them now.
            self.schedule_sync(self.TRIM_DELAY_S)

    def watch_has_focus(self, _has_focus: bool) -> None:
        # No Navin rule depends on the chat's own focus. The default restyled
        # every block of the conversation (seconds on a long session).
        restyle_focus(self)

    def watch_auto_follow(self, follow: bool) -> None:
        self.post_message(self.FollowChanged(self, follow))
        if self.is_attached:
            if follow:
                # scroll_y watchers run inside the animator's frame callback.
                # anchor() stops that same animation; defer it until the frame
                # has removed its completed entry to avoid Textual's KeyError.
                self.call_later(self._resume_anchor)
                self.schedule_trim()
            else:
                self.anchor(False)

    def _resume_anchor(self) -> None:
        if self.is_attached and self.auto_follow:
            self.anchor()

    # -- blocks and records -------------------------------------------------

    def blocks(self) -> Iterator[Any]:
        """Every block in reading order: widgets, and records standing in for
        blocks out of view (find, copy and export see the whole chat)."""
        for child in list(self.children):
            if isinstance(child, RecordGap):
                yield from list(child.records)
            else:
                yield child

    def _gaps(self) -> list[RecordGap]:
        return [child for child in self.children if isinstance(child, RecordGap)]

    @property
    def windowed_count(self) -> int:
        """Blocks currently held as records instead of widgets."""
        return sum(len(gap.records) for gap in self._gaps())

    def mounted_block_count(self) -> int:
        return sum(1 for child in self.children if not isinstance(child, RecordGap))

    # -- scheduling ---------------------------------------------------------

    def schedule_sync(self, delay: float = 0.0) -> None:
        if not self.is_attached:
            return
        if self._sync_running:
            self._sync_again = True
            return
        # Textual timers divide by their interval: never schedule zero.
        delay = max(0.001, delay)
        due = time.monotonic() + delay
        if self._sync_timer is not None:
            if self._sync_due <= due:
                return
            self._sync_timer.stop()
        self._sync_due = due
        self._sync_timer = self.set_timer(delay, self._run_sync)

    def schedule_trim(self) -> None:
        self.schedule_sync(self.TRIM_DELAY_S)

    def _trim(self) -> None:
        """Settle the window soon (compatibility entry point)."""
        self.schedule_sync(0)

    async def _run_sync(self) -> None:
        self._sync_timer = None
        await self.sync_window()

    def _can_sync(self) -> bool:
        if not self.is_attached or self._loading_history:
            return False
        try:
            screen = self.screen
            if screen is not self.app.screen:
                return False
        except Exception:  # noqa: BLE001 - detached mid-pass
            return False
        # A drag selection holds widgets; keep them until the button is up.
        return not getattr(screen, "_selecting", False)

    async def sync_window(self) -> bool:
        """Build records near the viewport back into widgets, and turn finished
        blocks far from it into records. Returns whether anything changed."""
        if self._sync_running:
            self._sync_again = True
            return False
        self._sync_running = True
        changed = False
        retry = False
        try:
            for _ in range(self.MAX_PASSES):
                self._sync_again = False
                if not self._can_sync():
                    retry = self.is_attached and not self._loading_history
                    break
                step = await self._sync_pass()
                changed = changed or step
                if not step:
                    break
        except Exception:  # noqa: BLE001 - a failed pass must not end the app
            from loguru import logger

            logger.opt(exception=True).warning("transcript window sync failed")
        finally:
            self._sync_running = False
        if self._sync_again:
            self.schedule_sync(0.02)
        elif retry:
            # A modal or a drag selection holds the window; look again later.
            self.schedule_sync(0.5)
        return changed

    # -- geometry -----------------------------------------------------------

    def _region(self, widget: Widget) -> Region | None:
        if not widget.display:
            return None
        try:
            return widget.virtual_region
        except Exception:  # noqa: BLE001 - not laid out yet
            return None

    def _viewport(self) -> tuple[int, int, int]:
        height = self.scrollable_content_region.height or self.outer_size.height
        margin = max(self.MIN_MARGIN_LINES, height * self.MARGIN_SCREENS)
        return round(self.scroll_y), height, margin

    def _selected_roots(self) -> set[int]:
        roots: set[int] = set()
        try:
            selections = self.screen.selections
        except Exception:  # noqa: BLE001
            return roots
        for widget in list(selections):
            node = widget
            while node is not None and node.parent is not self:
                node = node.parent
            if node is not None:
                roots.add(id(node))
        return roots

    def _pinned(self, child: Widget, index: int, count: int, selected: set[int]) -> bool:
        if index >= count - self.KEEP_TAIL_BLOCKS or not child.display:
            return True
        if isinstance(child, (ApprovalCard, ChoiceCard)) or id(child) in selected:
            return True
        app = self.app
        # Blocks the app still writes to or removes later stay widgets.
        if child is getattr(app, "_current", None):
            return True
        if any(child is bubble for bubble in getattr(app, "_parked_bubbles", ()) or ()):
            return True
        if any(getattr(app, name, None) is child for name in ("_display_error_note", "_retry_wait_note")):
            return True
        focused = app.focused
        return focused is not None and (focused is child or child in focused.ancestors)

    # -- passes -------------------------------------------------------------

    async def _sync_pass(self) -> bool:
        top, height, margin = self._viewport()
        if height <= 0:
            return False
        keep_lo, keep_hi = top - margin, top + height + margin
        children = list(self.children)
        for gap in children:
            if not isinstance(gap, RecordGap) or not gap.records:
                continue
            region = self._region(gap)
            if region is None:
                continue
            span = self._build_span(gap, region.y, keep_lo, keep_hi, top, height)
            if span is not None:
                return await self._build(gap, *span)
        return await self._drop(children, top, height, margin)

    def _build_span(self, gap: RecordGap, gap_y: int, low: int, high: int,
                    top: int, height: int) -> tuple[int, int] | None:
        offsets = gap.offsets()
        hits = [
            index for index, (offset, record) in enumerate(zip(offsets, gap.records))
            if gap_y + offset + max(1, record.height) > low and gap_y + offset < high
        ]
        if not hits:
            return None
        start, end = hits[0], hits[-1] + 1
        limit = self.MAX_BUILD_PER_PASS
        if end - start > limit:
            if gap_y + gap.total_height <= top:
                start = end - limit  # above the viewport: nearest first
            elif gap_y >= top + height:
                end = start + limit
            else:
                at = next((i for i in hits if gap_y + offsets[i] + max(1, gap.records[i].height) > top), start)
                start = max(start, at - limit // 2)
                end = min(end, start + limit)
        return start, end

    def _anchor(self, skip: set[int]) -> tuple[Widget, int] | None:
        """The first block on screen that this pass leaves as it is."""
        if self.auto_follow:
            return None
        top, height, _margin = self._viewport()
        for child in self.children:
            if id(child) in skip or isinstance(child, RecordGap):
                continue
            region = self._region(child)
            if region is not None and region.bottom > top and region.y < top + height:
                return child, region.y
        return None

    def _settle(self, anchor: tuple[Widget, int] | None) -> None:
        """Lay out now and keep the anchor where the reader saw it."""
        self.screen._refresh_layout()
        if anchor is None:
            return
        widget, old_y = anchor
        if not widget.is_attached:
            return
        new_y = widget.virtual_region.y
        if new_y != old_y:
            self._adjusting_scroll = True
            try:
                self.scroll_to(y=self.scroll_y + new_y - old_y, animate=False, immediate=True)
            finally:
                self._adjusting_scroll = False

    async def _build(self, gap: RecordGap, start: int, end: int, *, jump_to: BlockRecord | None = None) -> bool:
        records = gap.records[start:end]
        widgets: list[Widget] = []
        for record in records:
            widget = record.build()
            widget.display = False
            widgets.append(widget)
        tail = gap.records[end:]
        tail_gap = RecordGap(tail) if tail else None
        if tail_gap is not None:
            tail_gap.display = False
        try:
            await self.mount_all([*widgets, *([tail_gap] if tail_gap else [])], after=gap)
        except Exception:  # noqa: BLE001 - the gap left meanwhile (chat cleared)
            return False
        if not gap.is_attached:
            return False
        # The anchor is measured now: the new widgets are still out of layout.
        anchor = self._anchor(skip=set())
        if anchor is None and not self.auto_follow and jump_to is None:
            region = self._region(gap)
            offsets = gap.offsets()
            top, height, _margin = self._viewport()
            if region is not None:
                for widget, record, offset in zip(widgets, records, offsets[start:end]):
                    if region.y + offset + max(1, record.height) > top:
                        anchor = (widget, region.y + offset)
                        break
        with self.app.batch_update():
            head = gap.records[:start]
            gap.set_records(head)
            if not head:
                gap.display = False
            for widget in widgets:
                widget.display = True
            if tail_gap is not None:
                tail_gap.display = True
            self._settle(anchor)
            if jump_to is not None:
                target = widgets[records.index(jump_to)] if jump_to in records else None
                if target is not None:
                    self.scroll_to_widget(target, animate=False)
        self.records_built += len(widgets)
        if not head:
            await gap.remove()
        return True

    async def _drop(self, children: list[Widget], top: int, height: int, margin: int) -> bool:
        drop_lo = top - margin * self.DROP_SCREENS
        drop_hi = top + height + margin * self.DROP_SCREENS
        selected = self._selected_roots()
        count = len(children)
        runs: list[list[tuple[Widget, BlockRecord]]] = []
        previous = -2
        for index, child in enumerate(children):
            if isinstance(child, RecordGap) or self._pinned(child, index, count, selected):
                continue
            region = self._region(child)
            if region is None or (region.bottom > drop_lo and region.y < drop_hi):
                continue
            snapshot = getattr(child, "snapshot_record", None)
            record = snapshot() if callable(snapshot) else None
            if record is None:
                continue
            record.classes.discard("-find")
            following = children[index + 1] if index + 1 < count else None
            next_region = self._region(following) if following is not None else None
            if next_region is not None and next_region.y > region.y:
                record.height = next_region.y - region.y
            else:
                record.height = region.height + child.styles.margin.bottom
            if index != previous + 1 or not runs:
                runs.append([])
            runs[-1].append((child, record))
            previous = index
        if not runs:
            return False
        dropped = [child for run in runs for child, _record in run]
        placements: list[tuple[RecordGap, list[BlockRecord], str]] = []
        for run in runs:
            first, last = run[0][0], run[-1][0]
            records = [record for _child, record in run]
            index = children.index(first)
            before = children[index - 1] if index > 0 else None
            after_index = children.index(last) + 1
            after = children[after_index] if after_index < count else None
            if isinstance(before, RecordGap):
                placements.append((before, records, "append"))
            elif isinstance(after, RecordGap):
                placements.append((after, records, "prepend"))
            else:
                gap = RecordGap([])
                gap.display = False
                try:
                    await self.mount(gap, before=first)
                except Exception:  # noqa: BLE001
                    return False
                placements.append((gap, records, "new"))
        if any(child.parent is not self for child in dropped):
            return False
        anchor = self._anchor(skip={id(child) for child in dropped})
        with self.app.batch_update():
            for gap, records, mode in placements:
                if mode == "append":
                    gap.set_records(gap.records + records)
                elif mode == "prepend":
                    gap.set_records(records + gap.records)
                else:
                    gap.set_records(records)
                    gap.display = True
            for child in dropped:
                child.display = False
            self._settle(anchor)
        self.records_made += len(dropped)
        await super().remove_children(dropped)
        await self._merge_gaps()
        return True

    async def _merge_gaps(self) -> None:
        merged: list[Widget] = []
        previous: Widget | None = None
        for child in list(self.children):
            if isinstance(child, RecordGap) and isinstance(previous, RecordGap):
                previous.set_records(previous.records + child.records)
                child.display = False
                merged.append(child)
                continue
            previous = child
        if merged:
            await super().remove_children(merged)

    # -- reading older blocks ----------------------------------------------

    def _gap_above(self) -> bool:
        for child in self.children:
            if isinstance(child, RecordGap):
                if child.records:
                    return True
                continue
            return False
        return False

    def reveal_windowed(self, through: Any = None) -> bool:
        """Bring blocks held as records back, keeping the reading position.

        With ``through`` (a record from ``blocks()``: find, jump to a
        message), that block is built and scrolled into view. Otherwise the
        blocks above the viewport are built.
        """
        if through is None:
            if not self._gap_above():
                return False
            self.auto_follow = False
            self.schedule_sync(0)
            return True
        if not isinstance(through, BlockRecord):
            return False
        self.auto_follow = False
        self.run_worker(self._reveal_record(through), group="transcript-reveal", exclusive=True)
        return True

    async def _reveal_record(self, record: BlockRecord) -> None:
        while self._sync_running:
            await asyncio.sleep(0.01)
        self._sync_running = True
        try:
            for gap in self._gaps():
                if any(item is record for item in gap.records):
                    index = next(i for i, item in enumerate(gap.records) if item is record)
                    start = max(0, index - 2)
                    await self._build(gap, start, min(len(gap.records), index + 3), jump_to=record)
                    break
        finally:
            self._sync_running = False
        self.schedule_sync(self.TRIM_DELAY_S)

    def scroll_to_widget(self, widget: Any, *args: Any, **kwargs: Any) -> bool:
        if isinstance(widget, BlockRecord):
            # Its widget does not exist yet: reveal_windowed() builds it and
            # scrolls to it.
            return False
        return super().scroll_to_widget(widget, *args, **kwargs)

    def request_older(self) -> None:
        if self._gap_above():
            self.schedule_sync(0)
            return
        if self.has_older and not self.loading_history:
            self.loading_history = True
            self.auto_follow = False
            self.post_message(self.OlderRequested(self.history_generation))

    def _near_top(self, y: float) -> bool:
        # The reader scrolling up fetches older blocks a screen ahead, so they
        # are there before the top instead of stopping the scroll on it.
        # Relayouts (resize, reveal) keep the strict top check in watch_scroll_y.
        return y <= max(3, self.outer_size.height * self.PREFETCH_SCREENS)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self._near_top(self.scroll_y):
            self.request_older()

    def on_resize(self) -> None:
        self.follow()
        self.schedule_sync(0.05)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if self._adjusting_scroll:
            return
        # Only a move up leaves the tail. A frame of a smooth scroll toward the
        # end lands short of it too, and used to drop follow for the turn.
        if self.is_vertical_scroll_end:
            self.auto_follow = True
        elif new_value < old_value:
            self.auto_follow = False
        if new_value < old_value and new_value <= 3:
            self.request_older()
        if self.windowed_count:
            self.schedule_sync(0.01)

    def nudge(self, delta: int) -> None:
        if not delta:
            return
        if delta < 0 and self._near_top(self.scroll_y + delta):
            self.request_older()
        self.scroll_relative(y=delta, animate=False)
        self.auto_follow = self.is_vertical_scroll_end

    def page(self, direction: int) -> None:
        height = max(1, self.outer_size.height - 2)
        self.nudge(direction * height)

    async def add(self, widget: Any) -> None:
        await self.mount(widget)
        self.follow()

    def follow(self) -> None:
        if self.is_attached and self.auto_follow:
            # Resolve the bottom position in the same layout as the new
            # content, instead of painting and scrolling in separate frames.
            self.anchor()
            self.schedule_trim()


class EdgeScrollbar(Widget):
    """A thin scrollbar for the conversation, on the screen's right edge.

    The transcript hides its own bar so the chat keeps its columns. This one
    sits against the terminal border, past the sidebar when it is open, and
    spans the rows of the conversation (the composer rows stay blank). Drag
    the thumb, click the track to jump there, or use the wheel over it.
    """

    ALLOW_SELECT = False
    can_focus = False
    COMPONENT_CLASSES = {"edge-scrollbar--track", "edge-scrollbar--thumb"}
    TRACK_GLYPH = "▕"
    THUMB_GLYPH = "▐"
    WHEEL_LINES = 3

    DEFAULT_CSS = """
    EdgeScrollbar {
        width: 1;
        height: 1fr;
        background: $background;
    }
    EdgeScrollbar > .edge-scrollbar--track { color: $scrollbar; }
    EdgeScrollbar > .edge-scrollbar--thumb { color: $scrollbar-active; }
    EdgeScrollbar:hover > .edge-scrollbar--thumb,
    EdgeScrollbar.-dragging > .edge-scrollbar--thumb { color: $foreground; }
    """

    def __init__(self, target: str = "#transcript", *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._target_selector = target
        self._target: Transcript | None = None
        self._shape: tuple[int, int, int] = (0, -1, 0)
        self._grab: int | None = None

    @property
    def shape(self) -> tuple[int, int, int]:
        """(track rows, thumb start, thumb rows); start is -1 when nothing scrolls."""
        return self._shape

    def on_mount(self) -> None:
        if not self._bind():
            self.call_after_refresh(self._bind)

    def _bind(self) -> bool:
        if self._target is not None:
            return True
        if not self.is_attached:
            return False
        try:
            target = self.screen.query_one(self._target_selector, Transcript)
        except NoMatches:
            return False
        self._target = target
        self.watch(target, "scroll_y", self.sync, init=False)
        self.watch(target, "virtual_size", self.sync, init=False)
        # Resizes, the sidebar and the composer growing change the track.
        self.screen.screen_layout_refresh_signal.subscribe(self, self._on_layout)
        self.sync()
        return True

    def _on_layout(self, _screen: object) -> None:
        self.sync()

    def _measure(self) -> tuple[int, int, int]:
        target = self._target
        if target is None or not target.is_attached or not target.display:
            return (0, -1, 0)
        # The chat column starts on this bar's first row; last laid-out sizes
        # avoid rebuilding the compositor map on every scroll frame.
        track = max(0, min(self.outer_size.height, target.outer_size.height))
        max_scroll = target.max_scroll_y
        virtual = target.virtual_size.height
        if not track or max_scroll <= 0 or virtual <= 0:
            return (track, -1, 0)
        view = max(1, virtual - max_scroll)
        size = min(track, max(1, round(track * view / virtual)))
        ratio = min(1.0, max(0.0, target.scroll_y / max_scroll))
        return (track, round((track - size) * ratio), size)

    def sync(self) -> None:
        shape = self._measure()
        if shape != self._shape:
            self._shape = shape
            self.refresh()

    def render_line(self, y: int) -> Strip:
        width = content_width(self)
        base = self.visual_style.rich_style
        track, start, size = self._shape
        if width <= 0 or start < 0 or not 0 <= y < track:
            return Strip.blank(width, base)
        thumb = start <= y < start + size
        style = self.get_component_rich_style(
            "edge-scrollbar--thumb" if thumb else "edge-scrollbar--track"
        )
        glyph = self.THUMB_GLYPH if thumb else self.TRACK_GLYPH
        segments = [Segment(glyph, style)]
        if width > 1:
            segments.insert(0, Segment(" " * (width - 1), base))
        return Strip(segments, width)

    # -- pointer ----------------------------------------------------------

    def _drag_to(self, y: int) -> None:
        target = self._target
        track, _start, size = self._shape
        if target is None or self._grab is None:
            return
        span = track - size
        max_scroll = target.max_scroll_y
        if span <= 0 or max_scroll <= 0:
            return
        start = min(span, max(0, y - self._grab))
        target.scroll_to(y=max_scroll * start / span, animate=False, immediate=True)
        self.sync()

    def _end_drag(self) -> None:
        self._grab = None
        self.remove_class("-dragging")

    def on_mouse_down(self, event: events.MouseDown) -> None:
        track, start, size = self._shape
        y = int(event.y)
        if event.button != 1 or start < 0 or not 0 <= y < track:
            return
        event.stop()
        if start <= y < start + size:
            self._grab = y - start
        else:
            # A click on the track brings the thumb's middle there.
            self._grab = size // 2
            self._drag_to(y)
        self.add_class("-dragging")
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._grab is None:
            return
        event.stop()
        self._drag_to(int(event.y))

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._grab is None:
            return
        event.stop()
        self._end_drag()
        self.release_mouse()

    def on_mouse_release(self, _event: events.MouseRelease) -> None:
        self._end_drag()

    def _wheel(self, event: events.MouseEvent, lines: int) -> None:
        event.stop()
        if self._target is not None and self._target.is_attached:
            self._target.nudge(lines)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel(event, -self.WHEEL_LINES)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel(event, self.WHEEL_LINES)


def compact_shortcut(key: str) -> str:
    """Footer keys stay short: ctrl+p -> ^p, F2 stays F2."""
    raw = (key or "").strip()
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("ctrl+shift+"):
        rest = raw.split("+", 2)[-1]
        return f"^{rest.upper() if len(rest) == 1 else rest}"
    if lower.startswith("ctrl+"):
        rest = raw.split("+", 1)[-1]
        return f"^{rest.lower() if len(rest) == 1 else rest}"
    return raw


class DockHint(Static):
    """One quiet shortcut on the single footer line."""

    DEFAULT_CSS = """
    DockHint {
        width: auto;
        height: 1;
        padding: 0 2 0 0;
        color: $text-muted 80%;
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
            shown = compact_shortcut(self.key)
            self.update(f"[$primary 70%]{escape(shown)}[/] {escape(self.label)}")
        elif self.action:
            self.update(escape(self.label))
        else:
            self.update(f"[$text-muted]{escape(self.label)}[/]")

    def on_click(self) -> None:
        if self.action:
            self.app.call_later(self.app.run_action, self.action)


def short_path(path: str, keep: int = 3) -> str:
    """``~/projects/deploy7/guidia``: home as ``~`` and the last folders only."""
    import os

    raw = (path or "").replace("\\", "/").rstrip("/") or (path or "")
    home = os.path.expanduser("~").replace("\\", "/").rstrip("/")
    prefix = ""
    if home and (raw == home or raw.startswith(home + "/")):
        prefix, raw = "~", raw[len(home):]
    parts = [part for part in raw.split("/") if part]
    if len(parts) > keep:
        return f"{prefix}/…/" + "/".join(parts[-keep:])
    if prefix:
        return prefix + ("/" + "/".join(parts) if parts else "")
    return raw


class DockPath(Static):
    """The path slot shrinks as the model line grows; refit it then."""

    def on_resize(self) -> None:
        if isinstance(self.parent, DockBar):
            self.parent._paint_path()


class DockBar(Horizontal):
    """One footer line: mode, model, effort, context and path, then Settings and panel.

    Every other command lives in Settings and the palette, and keeps its key.
    """

    DEFAULT_CSS = """
    DockBar {
        width: 1fr;
        height: 1;
        min-height: 1;
        padding: 0;
        background: $background;
        overflow: hidden;
    }
    DockBar > #dock-path {
        width: 1fr;
        height: 1;
        padding: 0 2 0 4;
        color: $text-muted 70%;
        overflow: hidden;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._path = ""

    def compose(self) -> ComposeResult:
        yield ComposerMeta(id="composer-meta")
        yield DockPath("", id="dock-path")
        # Right of the path, which already names the project.
        yield DockHint("ctrl+s", "Sessions", "toggle_projects", id="dock-sessions")
        yield DockHint("ctrl+g", "Settings", "open_settings", id="dock-settings")
        yield DockHint("ctrl+p", "panel", "toggle_sidebar", id="dock-hide")

    def set_panel(self, visible: bool) -> None:
        # The line stays: it carries the model and context in both layouts.
        with contextlib.suppress(NoMatches):
            self.query_one("#dock-hide", DockHint).set_label("hide panel" if visible else "panel")

    def set_sessions(self, opened: bool) -> None:
        """Label of the Sessions button: what a click on it will do."""
        with contextlib.suppress(NoMatches):
            self.query_one("#dock-sessions", DockHint).set_label("hide sessions" if opened else "Sessions")

    def set_path(self, path: str) -> None:
        self._path = short_path((path or "").strip())
        self._paint_path()

    def on_resize(self) -> None:
        self._paint_path()

    def _paint_path(self) -> None:
        try:
            slot = self.query_one("#dock-path", Static)
        except Exception:  # noqa: BLE001
            return
        raw = self._path
        width = content_width(slot) or 24
        if width < 8:
            width = 24
        leaf = raw.rsplit("/", 1)[-1] or raw
        if len(raw) <= width:
            shown = raw
        elif len(leaf) + 4 <= width:
            shown = fit_path(raw, width)
        elif len(leaf) <= width:
            # Narrow terminal: the project name reads better than a cut path.
            shown = leaf
        else:
            shown = leaf[: max(1, width - 1)] + "…"
        slot.update(escape(shown))


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


def account_side_text(payload: dict[str, Any]) -> str:
    """Panel body: plan, usage %, monthly price. No spent / budget dollars."""
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
    return "  ".join(bits)


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


def _key_markup(key: str, *, ink: str = "$text-muted") -> str:
    """Shortcut column: muted by default, pad with spaces outside the markup."""
    shown = escape(key)
    pad = max(0, _KEY_WIDTH - len(key))
    if not key:
        return " " * _KEY_WIDTH
    return f"[{ink}]{shown}[/]{' ' * pad}"


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
        markup = f"{_key_markup(self.key)}{escape(self.label)}{tail}"
        if markup == getattr(self, "_painted_markup", None):
            return
        self._painted_markup = markup
        # One fixed row whose width never follows the text: a repaint is enough.
        # The live activity line changes with every tool step.
        self.update(markup, layout=not self.is_mounted)

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
    SideCard.-accent > .card-body { color: $primary; }
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
        accent: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.key = key
        self.title_text = title
        self.action = action
        self._body_raw = ""
        self._wrap_as_path = False
        self._key_ink = "$primary" if accent else "$text-muted"
        if boxed:
            self.add_class("-boxed")
        if bare:
            self.add_class("-bare")
        if accent:
            self.add_class("-accent")

    def compose(self) -> ComposeResult:
        pad = _key_markup(self.key, ink=self._key_ink) if self.key else (" " * _KEY_WIDTH)
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
        pad = _key_markup(self.key, ink=self._key_ink) if self.key else (" " * _KEY_WIDTH)
        self.query_one(".card-head", Static).update(f"{pad}{escape(text)}")

    def on_click(self) -> None:
        self.app.call_later(self.app.run_action, self.action)


def _version_markup(version: str, latest: str) -> str:
    """``navin  v2.3.7`` and, when newer, the version to update to."""
    label = f"navin  v{version}" if version else "navin"
    text = f"[$text-muted]{escape(label)}[/]"
    if latest and latest != version:
        text += f"  [$warning]v{escape(latest)} · /update[/]"
    return text


def _account_markup(text: str) -> str:
    """The account line of the panel and the sessions column; empty when not signed in."""
    body = "" if text in {"", "Account", "not connected"} else escape(text)
    return f"[$primary]{body}[/]" if body else ""


class ProjectsSidebar(Vertical):
    """Right column: every folder the CLI was used in, with its latest sessions.

    Opened by ctrl+s or the Sessions button under the chat, in the place of the
    panel (ctrl+p): one right column at a time, same width, so the chat never
    jumps. The list itself is :class:`ProjectsPanel`.
    """

    DEFAULT_CSS = """
    ProjectsSidebar {
        width: 36;
        min-width: 32;
        height: 1fr;
        border-left: vkey $border;
        padding: 1 1 0 2;
        display: none;
        background: $background;
        overflow: hidden;
    }
    ProjectsSidebar.-visible { display: block; }
    ProjectsSidebar > #projects-hide {
        width: auto;
        height: 1;
        min-height: 1;
        min-width: 0;
        margin: 0 0 1 0;
    }
    ProjectsSidebar > #projects-head { height: 1; margin: 0 0 1 0; }
    ProjectsSidebar #projects-title { width: 1fr; height: 1; }
    ProjectsSidebar .side-add {
        width: auto;
        height: 1;
        padding: 0 1;
        color: $text-muted 80%;
    }
    ProjectsSidebar .side-add:hover { color: $foreground; background: $boost; }
    ProjectsSidebar > #side-projects { height: 1fr; }
    ProjectsSidebar > #projects-foot { height: auto; padding: 0; background: $background; }
    ProjectsSidebar > #projects-foot > #projects-split {
        height: 1;
        margin: 1 0 1 0;
        border-top: solid $foreground 40%;
    }
    ProjectsSidebar > #projects-foot > #projects-version {
        width: 1fr;
        height: 1;
        min-height: 1;
        margin: 1 0 0 0;
        padding: 0;
        color: $text-muted;
        overflow: hidden;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._version = ""
        self._latest = ""

    def compose(self) -> ComposeResult:
        from navin.optional_live import live_modules_available
        from navin.tui.projects_panel import ProjectsPanel, _Hit

        yield Button("Hide sessions  ctrl+s", id="projects-hide", compact=True)
        with Horizontal(id="projects-head"):
            yield Static("[b]Projects[/]", id="projects-title", markup=True)
            yield _Hit("[$text-muted]+[/] Add", "add", classes="side-add")
        yield ProjectsPanel(id="side-projects")
        # Same foot as the panel: a line, the account, the version. No path:
        # the dock line under the chat shows it already.
        with Vertical(id="projects-foot"):
            yield Static("", id="projects-split")
            if live_modules_available():
                yield SideCard("ctrl+d", "Account", "open_account", id="projects-account", accent=True)
            yield Static("[$text-muted]navin[/]", id="projects-version", markup=True)

    @on(Button.Pressed, "#projects-hide")
    def _hide_sessions(self) -> None:
        self.app.call_later(self.app.run_action, "toggle_projects")

    def set_version(self, version: str) -> None:
        self._version = version
        self._render_version()

    def set_update_available(self, latest: str) -> None:
        self._latest = latest
        self._render_version()

    def _render_version(self) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#projects-version", Static).update(_version_markup(self._version, self._latest))

    def set_account(self, text: str) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#projects-account", SideCard).set_body(_account_markup(text))

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if target is not None and getattr(target, "id", None) == "projects-version" and self._latest:
            self.app.call_later(self.app.run_action, "update")
            event.stop()


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
        background: $background;
        overflow: hidden;
    }
    Sidebar.-visible { display: block; }
    Sidebar > #side-scroll {
        width: 1fr;
        height: 1fr;
        padding: 0;
        scrollbar-size-vertical: 0;
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
        background: $background;
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
        ("ctrl+⇧p", "Commands", "command_palette"),
        ("ctrl+n", "New chat", "new_chat"),
        ("F2", "Graph", "open_graph"),
        ("F3", "Evolve", "open_evolve"),
        ("F1", "Help", "show_help"),
    )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="side-scroll"):
            yield Button("Hide panel  ctrl+p", id="side-panel", compact=True)
            with Vertical(id="side-controls"):
                for key, label, action, widget_id in self.CONTROLS:
                    yield SideAction(key, label, action, id=widget_id)
            yield SideCard("ctrl+o", "Model", "pick_model", id="side-model")
            with Vertical(id="side-session-rows"):
                yield SideAction("ctrl+t", "Mode", "pick_mode", id="side-mode")
                yield SideAction("ctrl+s", "Sessions", "toggle_projects", id="side-session")
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
            from navin.optional_live import live_modules_available

            if live_modules_available():
                yield SideCard(
                    "ctrl+d",
                    "Account",
                    "open_account",
                    id="side-account",
                    accent=True,
                )
            yield Static("[$text-muted]navin[/]", id="side-version", markup=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._workspace_raw = ""
        self._model: dict[str, Any] = {}
        self._version = ""
        self._latest = ""

    def set_panel_label(self, visible: bool) -> None:
        self.query_one("#side-panel", Button).label = (
            "Hide panel  ctrl+p" if visible else "Show panel  ctrl+p"
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
        self.query_one("#side-version", Static).update(_version_markup(self._version, self._latest))

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if target is not None and getattr(target, "id", None) == "side-version" and self._latest:
            self.app.call_later(self.app.run_action, "update")
            event.stop()

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
        with contextlib.suppress(Exception):
            self.query_one("#side-account", SideCard).set_body(_account_markup(text))
