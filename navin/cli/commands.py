"""CLI commands for navin."""

import asyncio
import os
import select
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        with suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Keep console encoding setup before importing CLI UI/logging libraries.
import typer  # noqa: E402
from loguru import logger  # noqa: E402

# Remove default handler and re-add with unified navin format
logger.remove()
_log_handler_id = logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <5}</level> | "
        "<cyan>{extra[channel]}</cyan> | "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=None,
    filter=lambda record: record["extra"].setdefault("channel", "-") or True,
)


def _set_navin_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("navin")
    else:
        logger.disable("navin")


def _optional_managed_usage_hooks(config: Any) -> list[Any]:
    from navin.optional_live import live_modules_available

    if not live_modules_available():
        return []
    try:
        from navin.license_client import ManagedUsageHook
    except ImportError:
        return []
    return [ManagedUsageHook(config)]


from prompt_toolkit import PromptSession, print_formatted_text  # noqa: E402
from prompt_toolkit.application import run_in_terminal  # noqa: E402
from prompt_toolkit.formatted_text import ANSI, HTML  # noqa: E402
from prompt_toolkit.history import FileHistory  # noqa: E402
from prompt_toolkit.key_binding import KeyBindings  # noqa: E402
from prompt_toolkit.keys import Keys  # noqa: E402
from prompt_toolkit.patch_stdout import patch_stdout  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.markup import escape  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from navin import __logo__, __version__  # noqa: E402
from navin import optional_features as feature_support  # noqa: E402
from navin.agent.hooks import DEFAULT_HOOK_FACTORIES  # noqa: E402
from navin.agent.loop import AgentLoop  # noqa: E402
from navin.bus.notify import set_notification_bus  # noqa: E402
from navin.bus.outbound_events import (  # noqa: E402
    ProgressEvent,
    RetryWaitEvent,
    StreamDeltaEvent,
    StreamedResponseEvent,
    StreamEndEvent,
    outbound_event_from_message,
)
from navin.cli.agi import create_agi_app  # noqa: E402
from navin.cli.app_templates import create_app_templates_app  # noqa: E402
from navin.cli.gateway import create_gateway_app  # noqa: E402
from navin.cli.lsp import create_lsp_app  # noqa: E402
from navin.cli.stream import StreamRenderer, ThinkingSpinner  # noqa: E402
from navin.config.paths import get_workspace_path, is_default_workspace  # noqa: E402
from navin.config.schema import Config  # noqa: E402
from navin.optional_live import live_modules_available as _live_account_enabled  # noqa: E402
from navin.security.network import is_loopback_host  # noqa: E402
from navin.utils.evaluator import evaluate_response, resolve_evaluator_prompt  # noqa: E402
from navin.utils.helpers import sync_workspace_templates  # noqa: E402
from navin.utils.restart import (  # noqa: E402
    consume_restart_notice_from_env,
    format_restart_completed_message,
    should_show_cli_restart_notice,
)
from navin.webui.build import (  # noqa: E402
    BuildMode,
    WebUIBuildError,
    ensure_webui_bundle,
)
from navin.webui.sidebar_state import read_webui_sidebar_state  # noqa: E402


def _sanitize_surrogates(text: str) -> str:
    """Reconstruct surrogate pairs into real characters; replace lone surrogates.

    On Windows, console input may produce lone surrogate code points (e.g.
    ``\\ud83d\\udc08`` for U+1F408).  Round-tripping through UTF-16 reconstructs
    paired surrogates into their actual characters and replaces unpaired ones
    with U+FFFD.
    """
    return text.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")


def _signal_name(signum: int) -> str:
    with suppress(ValueError):
        return signal.Signals(signum).name
    return f"signal {signum}"


def _ensure_interactive_tty_mode() -> None:
    """Restore interactive line input after a raw-mode TTY leak."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    with suppress(Exception):
        import termios

        attrs = termios.tcgetattr(fd)
        required_lflag = termios.ISIG | termios.ICANON | termios.ECHO
        blocked_input_flags = getattr(termios, "IGNCR", 0) | getattr(termios, "INLCR", 0)
        if (
            (attrs[3] & required_lflag) == required_lflag
            and attrs[0] & termios.ICRNL
            and not attrs[0] & blocked_input_flags
        ):
            return
        attrs[0] = (attrs[0] | termios.ICRNL) & ~blocked_input_flags
        attrs[3] |= required_lflag
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        logger.debug("Restored foreground gateway TTY mode")


def _install_gateway_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    tasks: list[asyncio.Task],
    print_status: Callable[[str], None],
) -> Callable[[], None]:
    """Install foreground gateway signal handlers and return a restore callback."""
    loop_signals: list[int] = []
    previous_handlers: list[tuple[int, Any]] = []
    shutdown_requested = False

    def request_shutdown(signum: int) -> None:
        nonlocal shutdown_requested
        sig_name = _signal_name(signum)
        if shutdown_requested:
            logger.warning("Forcing gateway shutdown after repeated {}", sig_name)
            for task in tasks:
                if not task.done():
                    task.cancel()
            return
        shutdown_requested = True
        logger.info("Gateway shutdown requested by {}", sig_name)
        print_status("\nShutting down... Press Ctrl+C again to force.")
        shutdown_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, request_shutdown, signum)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, lambda sig, _frame: request_shutdown(sig))
            except (RuntimeError, ValueError):
                logger.debug("Could not install gateway handler for {}", _signal_name(signum))
                continue
            previous_handlers.append((signum, previous))
        else:
            loop_signals.append(signum)

    def restore() -> None:
        for signum in loop_signals:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signum)
        for signum, handler in previous_handlers:
            with suppress(RuntimeError, ValueError):
                signal.signal(signum, handler)

    return restore


def _advance_dream_cursor_if_behind(memory: Any) -> None:
    latest = memory.get_latest_cursor()
    if memory.get_last_dream_cursor() < latest:
        memory.set_last_dream_cursor(latest)


def _dream_runtime_override(agent: Any, config: Any) -> Any | None:
    """Resolve the Dream consolidation model, when one is configured.

    ``agents.defaults.dream.model_override`` names either a model preset (the
    dedicated nightly-consolidation preset) or a raw model slug. Unresolvable
    values fall back to the session default instead of skipping the run: a
    consolidation on the wrong model beats no consolidation at all.
    """
    try:
        override = (config.agents.defaults.dream.model_override or "").strip()
    except Exception:
        return None
    if not override:
        return None
    resolver = getattr(agent, "runtime_resolver", None)
    if resolver is None:
        return None
    try:
        return resolver.resolve_preset(override)
    except Exception:
        pass
    try:
        return resolver.resolve_override(model=override, model_preset=None)
    except Exception:
        logger.warning(
            "Dream model override {!r} could not be resolved; using the default model",
            override,
        )
        return None


def _commit_dream_changes(memory: Any) -> str | None:
    """Commit durable Dream edits, without entering the commit path for a no-op run."""
    if not memory.git.is_initialized():
        return None
    diff_body = memory.dream_content_diff()
    if not diff_body:
        return None
    message = memory.build_dream_commit_message(
        "dream: periodic memory consolidation",
        diff_body,
    )
    return memory.git.auto_commit(message)


class SafeFileHistory(FileHistory):
    """FileHistory subclass that sanitizes surrogate characters on write.

    On Windows, special Unicode input (emoji, mixed-script) can produce
    surrogate characters that crash prompt_toolkit's file write.
    See issue #2846.
    """

    def store_string(self, string: str) -> None:
        super().store_string(_sanitize_surrogates(string))


app = typer.Typer(
    name="navin",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{__logo__} navin - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
_REASONING_SENTENCE_ENDINGS = (".", "!", "?", "。", "！", "？")
_REASONING_FLUSH_CHARS = 60

_HEARTBEAT_PREAMBLE = (
    "[Your response will be delivered directly to the user's messaging app. "
    "Output ONLY the final user-facing message. Never reference internal "
    "files (HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your "
    "decision process. If nothing needs reporting, respond with just "
    "HEARTBEAT_OK and nothing else.]\n\n"
)


def _heartbeat_has_active_tasks(content: str) -> bool:
    """True if HEARTBEAT.md has task lines, ignoring headers, blanks and comments."""
    in_comment = False
    in_active_section: bool = False
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("##") and not stripped.startswith("###"):
                heading = stripped.lstrip("#").strip().lower()
                in_active_section = heading.startswith("active tasks")
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if in_active_section is False:
            continue
        return True
    return False


def _pick_heartbeat_target_from_sessions(
    *,
    enabled_channels: Iterable[str],
    sessions: Iterable[dict[str, Any]],
    archived_keys: Iterable[str],
) -> tuple[str, str]:
    enabled = set(enabled_channels)
    archived = set(archived_keys)
    for item in sessions:
        key = item.get("key") or ""
        if key in archived:
            continue
        if ":" not in key:
            continue
        channel, chat_id = key.split(":", 1)
        if channel in {"cli", "system"}:
            continue
        if channel in enabled and chat_id:
            return channel, chat_id
    return "cli", "direct"


# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    with suppress(Exception):
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return

    with suppress(Exception):
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    with suppress(Exception):
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)


def _build_cli_key_bindings() -> KeyBindings:
    """Key bindings for the interactive prompt.

    Behaviour:
      * Enter       -> submit the current input (keeps the familiar
                       single-line Enter-to-send feel even though the buffer
                       is multiline-capable).
      * Alt+Enter   -> insert a newline for multi-line input.
      * Shift+Enter -> insert a newline on terminals that emit the CSI-u
                       (kitty / fixterms) keyboard-protocol encoding for it.
    """
    # prompt_toolkit does not recognize CSI-u, so register its Shift+Enter
    # sequence as a best-effort addition without overriding existing mappings.
    with suppress(Exception):
        from prompt_toolkit.input import ansi_escape_sequences as _aes

        _aes.ANSI_SEQUENCES.setdefault("\x1b[13;2u", Keys.ControlF3)

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")  # Alt+Enter / Meta+Enter (ESC + CR, "\x1b\r")
    def _(event):
        event.current_buffer.insert_text("\n")

    # LF-as-Enter terminals send Alt+Enter as ESC + LF rather than ESC + CR.
    @kb.add("escape", Keys.ControlJ)  # Alt+Enter on LF-as-Enter terminals
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add(Keys.ControlF3)  # Shift+Enter on CSI-u capable terminals
    def _(event):
        event.current_buffer.insert_text("\n")

    return kb


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    with suppress(Exception):
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())

    from navin.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=SafeFileHistory(str(history_file)),
        enable_open_in_editor=False,
        # Multiline-capable buffer; Enter still submits via the custom key
        # bindings, while Alt+Enter adds a newline.
        multiline=True,
        key_bindings=_build_cli_key_bindings(),
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=sys.stdout.isatty(),
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
    show_header: bool = True,
) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = _response_renderable(content, render_markdown, metadata)
    if show_header:
        console.print()
        console.print(f"[cyan]{__logo__} navin[/cyan]")
    console.print(body)
    console.print()


def _response_renderable(content: str, render_markdown: bool, metadata: dict | None = None):
    """Render plain-text command output without markdown collapsing newlines."""
    if not render_markdown:
        return Text(content)
    if (metadata or {}).get("render_as") == "text":
        return Text(content)
    return Markdown(content)


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(
    response: str,
    render_markdown: bool,
    metadata: dict | None = None,
) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} navin[/cyan]"),
                c.print(_response_renderable(content, render_markdown, metadata)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


def _print_cli_progress_line(text: str, thinking: ThinkingSpinner | None, renderer: StreamRenderer | None = None) -> None:
    """Print a CLI progress line, pausing the spinner if needed."""
    if not text.strip():
        return
    target = renderer.console if renderer else console
    pause = renderer.pause_spinner() if renderer else (thinking.pause() if thinking else nullcontext())
    with pause:
        if renderer:
            renderer.ensure_header()
        target.print(f"  [dim]↳ {text}[/dim]")


class _ReasoningBuffer:
    def __init__(self) -> None:
        self._text = ""

    def add(self, text: str) -> str | None:
        if not text:
            return None
        self._text += text
        if self._should_flush(text):
            return self.flush()
        return None

    def flush(self) -> str | None:
        text = self._text.strip()
        self._text = ""
        return text or None

    def clear(self) -> None:
        self._text = ""

    def _should_flush(self, text: str) -> bool:
        stripped = text.rstrip()
        return (
            "\n" in text
            or stripped.endswith(_REASONING_SENTENCE_ENDINGS)
            or len(self._text) >= _REASONING_FLUSH_CHARS
        )


def _print_cli_reasoning(text: str, thinking: ThinkingSpinner | None, renderer: StreamRenderer | None = None) -> None:
    """Print reasoning/thinking content in a distinct style."""
    if not text.strip():
        return
    target = renderer.console if renderer else console
    pause = renderer.pause_spinner() if renderer else (thinking.pause() if thinking else nullcontext())
    with pause:
        if renderer:
            renderer.ensure_header()
        target.print(f"[dim italic]✻ {text}[/dim italic]")


def _flush_cli_reasoning(
    reasoning_buffer: _ReasoningBuffer,
    thinking: ThinkingSpinner | None,
    renderer: StreamRenderer | None = None,
) -> None:
    text = reasoning_buffer.flush()
    if text:
        _print_cli_reasoning(text, thinking, renderer)


async def _print_interactive_progress_line(text: str, thinking: ThinkingSpinner | None, renderer: StreamRenderer | None = None) -> None:
    """Print an interactive progress line, pausing the spinner if needed."""
    if not text.strip():
        return
    if renderer:
        with renderer.pause_spinner():
            renderer.ensure_header()
            renderer.console.print(f"  [dim]↳ {text}[/dim]")
    else:
        with thinking.pause() if thinking else nullcontext():
            await _print_interactive_line(text)


async def _maybe_print_interactive_progress(
    msg: Any,
    thinking: ThinkingSpinner | None,
    channels_config: Any,
    renderer: StreamRenderer | None = None,
    reasoning_buffer: _ReasoningBuffer | None = None,
) -> bool:
    event = outbound_event_from_message(msg)
    if isinstance(event, RetryWaitEvent):
        await _print_interactive_progress_line(msg.content, thinking, renderer)
        return True

    if not isinstance(event, ProgressEvent):
        return False

    reasoning_buffer = reasoning_buffer or _ReasoningBuffer()

    if event.reasoning_end:
        if channels_config and not channels_config.show_reasoning:
            reasoning_buffer.clear()
        else:
            _flush_cli_reasoning(reasoning_buffer, thinking, renderer)
        return True

    is_tool_hint = event.tool_hint
    is_reasoning = event.reasoning or event.reasoning_delta
    if is_reasoning:
        if channels_config and not channels_config.show_reasoning:
            reasoning_buffer.clear()
            return True
        text = reasoning_buffer.add(msg.content)
        if text:
            _print_cli_reasoning(text, thinking, renderer)
        return True
    if channels_config and is_tool_hint and not channels_config.send_tool_hints:
        return True
    if channels_config and not is_tool_hint and not channels_config.send_progress:
        return True

    await _print_interactive_progress_line(msg.content, thinking, renderer)
    return True


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} navin v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """navin - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    non_interactive_refresh: bool = typer.Option(False, "--refresh", help="Refresh config, preserving existing settings without prompting"),
):
    """Initialize or refresh navin configuration and workspace (providers are configured in the WebUI)."""
    from navin.config.loader import get_config_path, load_config, save_config, set_config_path
    from navin.config.schema import Config

    explicit_config = config is not None
    if config:
        config_path = Path(config).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    # Create or update config
    if config_path.exists():
        should_refresh = non_interactive_refresh
        if not non_interactive_refresh:
            console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
            console.print(
                "  [bold]y[/bold] = overwrite with defaults (existing values will be lost)"
            )
            console.print(
                "  [bold]N[/bold] = refresh config, keeping existing values and adding new fields"
            )
            if typer.confirm("Overwrite?"):
                config = _apply_workspace_override(Config())
                save_config(config, config_path)
                console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
            else:
                should_refresh = True

        if should_refresh:
            config = _apply_workspace_override(load_config(config_path))
            save_config(config, config_path)
            console.print(
                f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)"
            )
    else:
        config = _apply_workspace_override(Config())
        save_config(config, config_path)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    _onboard_plugins(config_path)

    # Create workspace, preferring the configured workspace path.
    workspace_path = get_workspace_path(config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)

    webui_cmd = "navin webui"
    if explicit_config:
        webui_cmd += f' -c "{config_path}"'

    typer.echo(f"\n✓ navin is ready. Run: {webui_cmd}")


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from navin.channels.registry import discover_all
    from navin.config.loader import merge_missing_defaults

    all_channels = discover_all()
    if not all_channels:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        if name not in channels:
            channels[name] = cls.default_config()
        else:
            channels[name] = merge_missing_defaults(channels[name], cls.default_config())

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _print_enable_options(
    extras: dict[str, list[str] | None],
    builtin_channels: set[str],
    plugin_channels: dict[str, Any],
    config: Config,
) -> None:
    table = Table(title="Available Features")
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Enabled")

    for item in sorted(builtin_channels | set(plugin_channels) | set(extras)):
        is_channel = item in builtin_channels or item in plugin_channels
        enabled = (
            feature_support.channel_enabled(config, item)
            if is_channel
            else feature_support.extra_installed(item, extras[item])
        )
        table.add_row(
            item,
            "channel" if is_channel else "feature",
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


def _model_display(config: Config) -> tuple[str, str]:
    """Return (resolved_model_name, preset_tag) for display strings."""
    resolved = config.resolve_preset()
    name = config.agents.defaults.model_preset
    tag = f" (preset: {name})" if name else ""
    return resolved.model or "not configured", tag


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from navin.config.loader import load_config, resolve_config_env_vars, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    try:
        loaded = resolve_config_env_vars(load_config(config_path))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    _warn_deprecated_config_keys(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


def _read_trigger_cli_message(message: str | None) -> str:
    """Read a trigger message from an argument or stdin."""
    if message and message.strip():
        return message
    try:
        if not sys.stdin.isatty():
            content = sys.stdin.read()
            if content.strip():
                return content
    except Exception:
        pass
    console.print("[red]Error: trigger message is required[/red]")
    raise typer.Exit(1)


def _warn_deprecated_config_keys(config_path: Path | None) -> None:
    """Hint users to remove obsolete keys from their config file."""
    import json

    from navin.config.loader import get_config_path

    path = config_path or get_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if "memoryWindow" in raw.get("agents", {}).get("defaults", {}):
        console.print(
            "[dim]Hint: `memoryWindow` in your config is no longer used "
            "and can be safely removed.[/dim]"
        )


def _load_inspection_config(
    config: str | None = None,
    workspace: str | None = None,
) -> tuple[Path, Config]:
    """Load config for diagnostic commands without resolving secret env refs."""
    from navin.config.loader import get_config_path, load_config, set_config_path

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve(strict=False)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    display_path = config_path or get_config_path()
    try:
        loaded = load_config(config_path)
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    _warn_deprecated_config_keys(display_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return display_path, loaded


def _confirm_webui_action(message: str, *, yes: bool) -> None:
    """Confirm a WebUI first-run mutation or fail clearly in non-interactive shells."""
    if yes:
        return
    if not _cli_can_prompt():
        console.print(
            "[red]Error: WebUI setup needs confirmation. Re-run with --yes.[/red]"
        )
        raise typer.Exit(1)
    if not typer.confirm(message, default=True):
        console.print("[yellow]WebUI setup cancelled.[/yellow]")
        raise typer.Exit(1)


def _cli_can_prompt() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _webui_build_mode_for_interactive(*, yes: bool = False) -> BuildMode:
    if yes:
        return "auto"
    return "prompt" if _cli_can_prompt() else "warn"


def _resolve_webui_config_path(config: str | None) -> Path:
    """Resolve the config path used by ``navin webui`` and bind loader state."""
    from navin.config.loader import get_config_path, set_config_path

    if not config:
        return get_config_path()
    config_path = Path(config).expanduser().resolve(strict=False)
    set_config_path(config_path)
    console.print(f"[dim]Using config: {config_path}[/dim]")
    return config_path


def _load_webui_setup_config(config_path: Path) -> Config:
    """Load config for first-run mutation without resolving env-var placeholders."""
    from navin.config.loader import load_config

    try:
        return load_config(config_path)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e


def _provider_setup_error(config: Config) -> str | None:
    """Return the provider setup error, or None when the current model can start."""
    from navin.config.loader import resolve_config_env_vars
    from navin.providers.factory import build_provider_snapshot

    try:
        build_provider_snapshot(resolve_config_env_vars(config.model_copy(deep=True)))
    except ValueError as exc:
        return str(exc)
    return None


def _webui_config_dict(config: Config) -> dict[str, Any]:
    """Return the current WebSocket config as a mutable alias-key dictionary."""
    from navin.channels.websocket import WebSocketConfig

    current = getattr(config.channels, "websocket", None) or {}
    model = WebSocketConfig.model_validate(current)
    return model.model_dump(by_alias=True, exclude_none=True)


def _webui_channel_enabled(config: Config) -> bool:
    from navin.channels.websocket import WebSocketConfig

    current = getattr(config.channels, "websocket", None) or {}
    return bool(WebSocketConfig.model_validate(current).enabled)


def _prepare_webui_bundle_for_gateway(
    config: Config,
    *,
    mode: BuildMode,
    webui_static_dist: bool = True,
) -> None:
    """Refresh or warn about stale bundled WebUI assets before gateway startup."""
    if not webui_static_dist or not _webui_channel_enabled(config):
        return

    def _print(message: str) -> None:
        console.print(f"[yellow]{escape(message)}[/yellow]")

    def _confirm(message: str) -> bool:
        return typer.confirm(message, default=True)

    try:
        ensure_webui_bundle(
            mode=mode,
            confirm=_confirm if mode == "prompt" else None,
            output=_print,
        )
    except WebUIBuildError as exc:
        if mode == "warn":
            console.print(f"[yellow]Warning: {escape(str(exc))}[/yellow]")
            return
        console.print(f"[red]Error: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc


def _host_for_local_browser(host: str) -> str:
    """Map bind hosts to a browser-openable local host."""
    if host in {"0.0.0.0", ""}:
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _gateway_health_url(host: str, port: int) -> str:
    """Return a health URL that can be opened from this device."""
    return f"http://{_host_for_local_browser(host)}:{port}/health"


def _gateway_health_bind_note(host: str) -> str:
    """Describe a non-local bind without presenting it as a usable URL."""
    return "" if is_loopback_host(host) else f" [dim](listening on {host})[/dim]"


_GATEWAY_HEALTH_MAX_CONNECTIONS = 64
_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS = 2.0


def _print_gateway_health_endpoint(host: str, port: int) -> None:
    """Print a usable health URL and make non-loopback binds explicit."""
    console.print(
        f"[green]✓[/green] Health endpoint: {_gateway_health_url(host, port)}"
        f"{_gateway_health_bind_note(host)}"
    )
    if is_loopback_host(host):
        return

    console.print(
        "[yellow]Warning: the unauthenticated health endpoint is listening beyond loopback "
        "and may be reachable from other devices. "
        f"Keep port {port} private or protect it with a firewall or reverse proxy.[/yellow]"
    )


def _webui_bootstrap_secret(config: Config) -> str:
    ws_cfg = _webui_config_dict(config)
    return str(ws_cfg.get("tokenIssueSecret") or ws_cfg.get("token") or "").strip()


def _webui_browser_url(config: Config) -> str:
    from urllib.parse import quote

    ws_cfg = _webui_config_dict(config)
    host = _host_for_local_browser(str(ws_cfg.get("host") or "127.0.0.1"))
    port = int(ws_cfg.get("port") or 8765)
    base_url = f"http://{host}:{port}"
    secret = _webui_bootstrap_secret(config)
    if not secret:
        return base_url
    return f"{base_url}/#/?bootstrapSecret={quote(secret, safe='')}"


def _append_webui_hash_param(url: str, key: str, value: str) -> str:
    """Append a ``key=value`` pair to the WebUI URL hash query (``#/?...``)."""
    from urllib.parse import quote

    param = f"{key}={quote(value, safe='')}"
    if "#" in url:
        base, hash_part = url.split("#", 1)
        separator = "&" if "?" in hash_part else "?"
        return f"{base}#{hash_part}{separator}{param}"
    return f"{url}/#/?{param}"


def _webui_display_url(url: str) -> str:
    marker = "bootstrapSecret="
    if marker not in url:
        return url
    prefix, _ = url.split(marker, 1)
    return f"{prefix}{marker}<redacted>"


def _ensure_local_webui_channel(config: Config, *, port: int | None, yes: bool) -> tuple[bool, bool]:
    """Enable the local WebUI channel with safe localhost defaults."""
    from navin.channels.websocket import WebSocketConfig

    current = getattr(config.channels, "websocket", None) or {}
    model = WebSocketConfig.model_validate(current)
    changed = False
    generated_secret = False

    needs_enable = not model.enabled
    needs_port = port is not None and model.port != port
    needs_secret = not model.token_issue_secret.strip() and not model.token.strip()
    if not needs_enable and not needs_port and not needs_secret:
        return False, False

    target_port = port if port is not None else model.port
    console.print()
    console.print("[bold]Local WebUI setup[/bold]")
    console.print(f"  URL: [cyan]http://127.0.0.1:{target_port}[/cyan]")
    console.print("  Bind: [cyan]127.0.0.1 only[/cyan] (not exposed to your LAN)")
    console.print("  Auth: generated WebUI bootstrap secret stored in config")
    console.print(
        "  LAN access requires an explicit host change plus a WebUI password in config."
    )
    _confirm_webui_action("Update the local WebUI channel in this config?", yes=yes)

    if not model.enabled:
        model.enabled = True
        changed = True
    if model.host != "127.0.0.1":
        model.host = "127.0.0.1"
        changed = True
    if port is not None and model.port != port:
        model.port = port
        changed = True
    if not model.websocket_requires_token:
        model.websocket_requires_token = True
        changed = True
    if needs_secret:
        import secrets

        model.token_issue_secret = secrets.token_urlsafe(32)
        changed = True
        generated_secret = True

    setattr(config.channels, "websocket", model.model_dump(by_alias=True, exclude_none=True))
    return changed, generated_secret


def _warn_webui_bind_scope(config: Config) -> None:
    ws_cfg = _webui_config_dict(config)
    host = str(ws_cfg.get("host") or "127.0.0.1")
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    console.print(
        "[yellow]Warning: WebUI is configured to bind outside localhost. "
        "Keep tokenIssueSecret set and use this only on trusted networks.[/yellow]"
    )


def _wait_for_webui(url: str, *, timeout_s: float = 5.0) -> None:
    """Best-effort wait for the WebUI listener before opening a browser."""
    import time
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _tcp_endpoint_reachable(host, port, timeout_s=0.2):
            return
        time.sleep(0.1)


def _tcp_endpoint_reachable(host: str, port: int, *, timeout_s: float = 0.25) -> bool:
    """Return whether a local TCP endpoint accepts connections."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _gateway_health_ready(host: str, port: int, *, timeout_s: float = 0.4) -> bool:
    """Return whether the navin gateway health endpoint responds OK."""
    import json
    import urllib.error
    import urllib.request

    browser_host = _host_for_local_browser(host)
    try:
        with urllib.request.urlopen(
            f"http://{browser_host}:{port}/health",
            timeout=timeout_s,
        ) as response:
            if response.status != 200:
                return False
            body = response.read(1024)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload.get("status") == "ok"


def _webui_endpoint_reachable(url: str, *, timeout_s: float = 0.25) -> bool:
    """Return whether the WebUI URL's TCP endpoint is already listening."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return _tcp_endpoint_reachable(host, port, timeout_s=timeout_s)


def _print_foreground_port_conflict(
    *,
    webui_url: str,
    gateway_host: str,
    gateway_port: int,
    config: Config | None = None,
) -> None:
    from navin.ports import check_ports, format_port_table

    console.print(
        "[red]Error: navin cannot start because one of its local ports is already in use.[/red]"
    )
    console.print(f"  WebUI: [cyan]{webui_url}[/cyan]")
    console.print(
        f"  Gateway health: [cyan]http://{_host_for_local_browser(gateway_host)}:{gateway_port}/health[/cyan]"
    )
    try:
        results = check_ports(config, include_external=True)
        console.print()
        console.print(format_port_table(results))
        conflicts = [row for row in results if row.is_conflict]
        for row in conflicts:
            endpoint = f"{row.role.host}:{row.role.port}"
            pid = row.listener.pid if row.listener else None
            cmd = (row.listener.cmdline if row.listener else None) or row.detail
            console.print(
                f"  [red]Conflict[/red] {row.role.spec.name} {endpoint}"
                + (f" pid={pid}" if pid else "")
                + (f" ({cmd})" if cmd else "")
            )
    except Exception:
        pass
    console.print()
    console.print("If this is an existing navin instance, use it or stop it first:")
    console.print("  [cyan]navin gateway status[/cyan]")
    console.print("  [cyan]navin gateway stop[/cyan]")
    console.print("  [cyan]navin ports[/cyan]")
    console.print("Or choose different ports with [cyan]--port[/cyan] and [cyan]--gateway-port[/cyan].")


def _log_external_port_status(config: Config) -> None:
    """Best-effort info log for external MCP ports (never blocks startup)."""
    try:
        from navin.ports import check_ports

        for row in check_ports(config, include_external=True):
            if row.role.spec.owner != "external":
                continue
            logger.info(
                "Port {}: {}:{} status={}",
                row.role.spec.name,
                row.role.host,
                row.role.port,
                row.status,
            )
    except Exception:
        pass


def _find_chromium_browser() -> list[str] | None:
    """Locate a Chromium-based browser that supports ``--app`` windows."""
    import platform
    import shutil

    system = platform.system()
    if system == "Windows":
        program_dirs = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LocalAppData", ""),
        ]
        relative_paths = [
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for base in program_dirs:
            if not base:
                continue
            for rel in relative_paths:
                exe = os.path.join(base, rel)
                if os.path.isfile(exe):
                    return [exe]
        return None
    if system == "Darwin":
        mac_apps = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        for exe in mac_apps:
            if os.path.isfile(exe):
                return [exe]
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


def _app_window_profile() -> Path:
    """Private Chromium profile reserved for Navin's app window."""
    from navin.config.paths import get_webui_dir

    return get_webui_dir() / "browser-profile"


def _app_window_chrome_args() -> list[str]:
    """Flags that make the app window's own frame match the app inside it.

    A Chromium app window paints its title bar from the *browser* theme and
    ignores the page's ``theme-color``, so a dark Navin sat under a light frame.
    ``--force-dark-mode`` fixes the frame without touching page rendering, which
    is a separate feature flag.

    It only has any effect on a fresh browser process, though: joining a Chrome
    that is already running silently drops every flag. Hence the private profile
    directory, which also keeps Navin's window out of the user's session rather
    than borrowing its cookies and extensions.
    """
    from navin.webui.sidebar_state import webui_theme

    args = [
        f"--user-data-dir={_app_window_profile()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if webui_theme() == "dark":
        args.append("--force-dark-mode")
    return args


def _close_stale_macos_app_browser() -> None:
    """Terminate a window-less Chrome left behind by a closed app window.

    macOS keeps an application alive after its last window closes, and its
    Chromium process singleton does not relay command lines to the running
    instance (Linux and Windows do). Relaunching Navin.app then degrades into
    a bare "reopen" event on that lingering instance: the ``--app=<url>``
    argument is dropped and the user gets an empty new-tab window instead of
    Navin. The lingering instance serves nothing without its window, so it is
    terminated before the fresh launch. Only processes using Navin's private
    profile directory are touched - never the user's own browser.
    """
    import platform
    import subprocess
    import time

    from navin.utils.proc import no_window_kwargs

    if platform.system() != "Darwin":
        return
    pattern = f"--user-data-dir={_app_window_profile()}"
    try:
        alive = (
            subprocess.run(  # noqa: S603
                ["pgrep", "-f", pattern],
                capture_output=True,
                check=False,
                **no_window_kwargs(),
            ).returncode
            == 0
        )
        if not alive:
            return
        subprocess.run(  # noqa: S603
            ["pkill", "-TERM", "-f", pattern], check=False, **no_window_kwargs()
        )
        # The singleton lock must be released before the new launch, or it
        # would still hand itself over to the dying process.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            gone = (
                subprocess.run(  # noqa: S603
                    ["pgrep", "-f", pattern],
                    capture_output=True,
                    check=False,
                    **no_window_kwargs(),
                ).returncode
                != 0
            )
            if gone:
                return
            time.sleep(0.1)
    except OSError:
        return


def _open_webui_on_windows_host(url: str) -> bool:
    """From inside WSL, open the WebUI on the Windows side of the boundary.

    The Linux opening machinery is a dead end here: a stock WSL has no browser
    and no portal, so ``webbrowser`` ends in ``gio: Operation not supported``
    while the user sits in front of a Windows screen. The window that can show
    the page is a Windows one, ideally the same standalone app window the
    Windows desktop build opens, pointed at *this* gateway, so the WSL project
    opens in it rather than in a second Windows-side instance.
    """
    import subprocess

    from navin.utils import wsl
    from navin.utils.proc import detached_no_window_kwargs
    from navin.webui.sidebar_state import webui_theme

    want_app_window = os.environ.get("NAVIN_WEBUI_TAB", "").strip() not in {"1", "true", "yes"}
    local_app_data = wsl.windows_env("LOCALAPPDATA")
    browser = wsl.find_host_browser(local_app_data) if want_app_window else None
    if browser:
        args = [
            browser,
            f"--app={url}",
            "--window-size=1440,900",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if local_app_data:
            # The profile has to live on the Windows side: Chrome cannot write
            # its lock files through the WSL redirector. A private profile also
            # keeps the flags effective when the user's own Chrome is running.
            args.append(f"--user-data-dir={local_app_data}\\Navin\\browser-profile")
        if webui_theme() == "dark":
            args.append("--force-dark-mode")
        try:
            subprocess.Popen(  # noqa: S603
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **detached_no_window_kwargs(),
            )
            return True
        except OSError:
            pass  # Interop disabled or the exe vanished; try the default browser.

    return wsl.open_url_on_host(url)


def _open_webui_browser(url: str, *, wait: bool = True) -> None:
    """Open the WebUI as a dedicated app window (Cursor-style), or a tab as fallback.

    Chromium ``--app=URL`` gives a standalone window without the address bar,
    with its own taskbar entry. ``NAVIN_WEBUI_TAB=1`` forces a normal tab.
    """
    import subprocess
    import webbrowser

    from navin.utils import wsl

    if wait:
        _wait_for_webui(url)
    display_url = _webui_display_url(url)

    if wsl.is_wsl_guest():
        if _open_webui_on_windows_host(url):
            console.print(
                f"[green]✓[/green] Opened Navin on Windows: [cyan]{display_url}[/cyan]"
            )
            return
        # No interop and no Windows browser reachable: fall through to the
        # Linux paths for the rare WSL that runs its own browser under WSLg.

    if os.environ.get("NAVIN_WEBUI_TAB", "").strip() not in {"1", "true", "yes"}:
        browser = _find_chromium_browser()
        if browser:
            _close_stale_macos_app_browser()
            try:
                subprocess.Popen(  # noqa: S603
                    [
                        *browser,
                        f"--app={url}",
                        "--window-size=1440,900",
                        *_app_window_chrome_args(),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                console.print(
                    f"[green]✓[/green] Opened Navin app window: [cyan]{display_url}[/cyan]"
                )
                return
            except OSError:
                pass  # Fall back to a normal browser tab below.

    try:
        # webbrowser reports False for "no way to open anything"; a claimed
        # success can still fail later inside gio, but at least the honest
        # failures stop printing a green check mark over a dead end.
        if webbrowser.open(url):
            console.print(f"[green]✓[/green] Opened WebUI: [cyan]{display_url}[/cyan]")
        else:
            console.print(f"[yellow]No browser found; open {display_url} yourself.[/yellow]")
    except Exception as exc:
        console.print(f"[yellow]Could not open browser ({exc}); visit {display_url}[/yellow]")


def _print_webui_foreground_lifecycle(*, attached: bool) -> None:
    """Explain how the browser and gateway lifecycles differ."""
    console.print()
    if attached:
        console.print("[green]navin is attached to the existing gateway.[/green]")
    else:
        console.print("[green]navin is running in this terminal.[/green]")
    console.print("[dim]Closing the browser does not stop channels or automations.[/dim]")
    console.print("[dim]Press Ctrl+C here to stop navin.[/dim]")


def _attach_to_background_gateway(runtime: Any) -> None:
    """Keep a foreground WebUI command attached to a managed gateway."""
    _print_webui_foreground_lifecycle(attached=True)
    try:
        while runtime.status().running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping navin...[/yellow]")
        result = runtime.stop()
        if result.ok or result.message == "gateway_not_running":
            console.print("[green]Gateway stopped.[/green]")
            return
        console.print(f"[red]Gateway could not be stopped: {result.message}[/red]")
        raise typer.Exit(1)

    console.print("[yellow]Gateway stopped.[/yellow]")


def _gateway_instance_command(
    subcommand: str,
    *,
    config_path: Path,
    workspace: str | None,
) -> str:
    """Return a copyable gateway command for the same config/workspace instance."""
    import shlex

    parts = ["navin", "gateway", subcommand, "--config", str(config_path)]
    if workspace:
        workspace_path = str(Path(workspace).expanduser().resolve(strict=False))
        parts.extend(["--workspace", workspace_path])
    return " ".join(shlex.quote(part) for part in parts)


def _migrate_cron_store(config: "Config") -> None:
    """One-time migration: move legacy global cron store into the workspace."""
    from navin.config.paths import get_cron_dir

    legacy_path = get_cron_dir() / "jobs.json"
    new_path = config.workspace_path / "cron" / "jobs.json"
    if legacy_path.is_file() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.move(str(legacy_path), str(new_path))


@app.command()
def trigger(
    trigger_id: str = typer.Argument(..., help="Trigger ID returned by /trigger"),
    message: str | None = typer.Argument(None, help="Message to deliver; stdin is used when omitted"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Deliver a local trigger message to its bound chat session."""
    from navin.triggers.local_store import (
        LocalTriggerStore,
        TriggerDisabledError,
        TriggerNotFoundError,
        TriggerStoreError,
    )

    runtime_config = _load_runtime_config(config, workspace)
    content = _read_trigger_cli_message(message)
    store = LocalTriggerStore(runtime_config.workspace_path)
    try:
        delivery = store.enqueue(trigger_id, content)
    except (TriggerNotFoundError, TriggerDisabledError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    except (TriggerStoreError, ValueError) as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Queued[/green] {delivery.trigger_id} ({delivery.id})")


# ============================================================================
# OpenAI-Compatible API Server
# ============================================================================


@app.command()
def serve(
    port: int | None = typer.Option(None, "--port", "-p", help="API server port"),
    host: str | None = typer.Option(None, "--host", "-H", help="Bind address"),
    timeout: float | None = typer.Option(None, "--timeout", "-t", help="Per-request timeout (seconds)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show navin runtime logs"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Start the OpenAI-compatible API server (/v1/chat/completions)."""
    try:
        from aiohttp import web  # noqa: F401
    except ImportError:
        console.print("[red]aiohttp is required. Install with: navin plugins enable api[/red]")
        raise typer.Exit(1)

    from navin.api.server import create_app
    from navin.bus.queue import MessageBus
    from navin.providers.image_generation import image_gen_provider_configs
    from navin.session.manager import SessionManager

    _set_navin_logs(verbose)

    runtime_config = _load_runtime_config(config, workspace)
    api_cfg = runtime_config.api
    host = host if host is not None else api_cfg.host
    port = port if port is not None else api_cfg.port
    timeout = timeout if timeout is not None else api_cfg.timeout
    api_key = api_cfg.api_key.strip() if api_cfg.api_key else ""
    if not is_loopback_host(host) and not api_key:
        console.print(
            f"[red]Error: host {host} is available beyond this device but api_key is not set. "
            "Set api.api_key in config to prevent unauthenticated access.[/red]"
        )
        raise typer.Exit(1)
    if _tcp_endpoint_reachable(_host_for_local_browser(host), port):
        from navin.ports import who_listens

        listener = who_listens(port)
        detail = ""
        if listener and listener.pid:
            detail = f" (pid={listener.pid}"
            if listener.cmdline:
                detail += f" {listener.cmdline[:80]}"
            detail += ")"
        console.print(
            f"[red]Error: API port {host}:{port} is already in use{detail}.[/red]"
        )
        console.print("Check with [cyan]navin ports[/cyan] or choose another --port.")
        raise typer.Exit(1)
    sync_workspace_templates(runtime_config.workspace_path)
    from navin.index.warmer import schedule_warm

    schedule_warm(runtime_config.workspace_path)
    bus = MessageBus()
    set_notification_bus(bus)
    session_manager = SessionManager(runtime_config.workspace_path)
    try:
        agent_loop = AgentLoop.from_config(
            runtime_config, bus,
            session_manager=session_manager,
            image_generation_provider_configs=image_gen_provider_configs(runtime_config),
            hook_factories=list(DEFAULT_HOOK_FACTORIES),
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc

    model_name, preset_tag = _model_display(runtime_config)
    console.print(f"{__logo__} Starting OpenAI-compatible API server")
    console.print(f"  [cyan]Endpoint[/cyan] : http://{host}:{port}/v1/chat/completions")
    console.print(f"  [cyan]Model[/cyan]    : {model_name}{preset_tag}")
    console.print("  [cyan]Session[/cyan]  : api:default")
    console.print(f"  [cyan]Timeout[/cyan]  : {timeout}s")
    if not is_loopback_host(host):
        console.print(
            "[yellow]API is available beyond this device "
            "(authentication required).[/yellow]"
        )
    console.print()

    api_app = create_app(
        agent_loop, model_name=model_name, request_timeout=timeout,
        api_key=api_key,
    )

    async def on_startup(_app):
        await agent_loop._connect_mcp()

    async def on_cleanup(_app):
        await agent_loop.close_mcp()

    api_app.on_startup.append(on_startup)
    api_app.on_cleanup.append(on_cleanup)

    web.run_app(api_app, host=host, port=port, print=lambda msg: logger.info(msg))


# ============================================================================
# WebUI Launcher
# ============================================================================


@app.command()
def webui(
    port: int | None = typer.Option(None, "--port", "-p", help="WebUI port"),
    gateway_port: int | None = typer.Option(
        None,
        "--gateway-port",
        help="Gateway health port",
    ),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    background: bool = typer.Option(
        False,
        "--background",
        help="Keep the gateway running after this command exits",
    ),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser"),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Open the WebUI with this project directory preselected (like `navin .`)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Apply safe local WebUI defaults without prompting",
    ),
) -> None:
    """Prepare the local WebUI, start the gateway, and open the browser workbench."""
    from navin.config.loader import save_config
    from navin.gateway import GatewayRuntime, GatewayRuntimePaths, GatewayStartOptions

    _ensure_interactive_tty_mode()

    # Self-heal the `navin` command: the desktop installers (Tauri MSI/NSIS,
    # DMG, AppImage) ship a full CLI but never touch the PATH. First launch
    # creates the shims (Windows/WSL) or the symlink (macOS/Linux).
    try:
        from navin.cli_link import ensure_cli_on_path

        healed = ensure_cli_on_path()
        if healed is not None and healed.created:
            console.print(f"[dim]{healed.message}[/dim]")
    except Exception:
        pass

    config_path = _resolve_webui_config_path(config)
    created_config = not config_path.exists()
    if created_config:
        console.print(f"[yellow]No config found at {config_path}.[/yellow]")
        _confirm_webui_action("Create a navin config and workspace now?", yes=yes)

    setup_config = _load_webui_setup_config(config_path)
    if workspace:
        setup_config.agents.defaults.workspace = workspace
    if gateway_port is not None:
        # Persist explicit desktop/standalone ports so a second launch can
        # attach to the same instance instead of starting a duplicate.
        setup_config.gateway.port = gateway_port

    provider_error = _provider_setup_error(setup_config)
    if provider_error:
        console.print(f"[dim]Provider check: {provider_error}[/dim]")
        console.print(
            "[yellow]Continuing without a model provider. "
            "Configure it in WebUI Settings → Providers after connecting.[/yellow]"
        )

    try:
        changed_webui, generated_bootstrap_secret = _ensure_local_webui_channel(
            setup_config,
            port=port,
            yes=yes,
        )
        _warn_webui_bind_scope(setup_config)
        webui_url = _webui_browser_url(setup_config)
    except ValueError as exc:
        console.print(f"[red]Error: invalid WebUI channel config: {exc}[/red]")
        raise typer.Exit(1) from exc

    from navin.config.security_profile import ensure_webui_assisted_profile

    changed_security_profile = ensure_webui_assisted_profile(setup_config)
    if changed_security_profile:
        console.print(
            "[dim]Security profile: assisted "
            "(ask before destructive shell/file ops; allow-for-session).[/dim]"
        )

    from navin.webui.mcp_presets_api import ensure_auto_enabled_mcp_presets

    added_mcp_presets = ensure_auto_enabled_mcp_presets(setup_config)
    if added_mcp_presets:
        console.print(
            "[dim]MCP presets auto-enabled: "
            + ", ".join(added_mcp_presets)
            + ".[/dim]"
        )

    if project:
        project_dir = Path(project).expanduser()
        if not project_dir.is_dir():
            console.print(f"[red]Error: project directory not found: {project}[/red]")
            raise typer.Exit(1)
        project_dir = project_dir.resolve()
        console.print(f"Project: [cyan]{project_dir}[/cyan]")
        webui_url = _append_webui_hash_param(webui_url, "project", str(project_dir))

    if (
        created_config
        or changed_webui
        or changed_security_profile
        or added_mcp_presets
        or workspace
        or gateway_port is not None
    ):
        save_config(setup_config, config_path)
        console.print(f"[green]✓[/green] Saved config: {config_path}")

    workspace_path = get_workspace_path(setup_config.workspace_path)
    workspace_path.mkdir(parents=True, exist_ok=True)
    sync_workspace_templates(workspace_path)

    runtime_config = _load_runtime_config(str(config_path), workspace)
    effective_gateway_port = gateway_port if gateway_port is not None else runtime_config.gateway.port

    console.print()
    console.print(f"WebUI: [cyan]{_webui_display_url(webui_url)}[/cyan]")
    gateway_health_url = _gateway_health_url(
        runtime_config.gateway.host,
        effective_gateway_port,
    )
    console.print(
        f"Gateway health: [cyan]{gateway_health_url}[/cyan]"
        f"{_gateway_health_bind_note(runtime_config.gateway.host)}"
    )
    if no_open:
        console.print("[dim]Browser opening disabled by --no-open.[/dim]")
        if generated_bootstrap_secret:
            console.print(
                "[yellow]A WebUI bootstrap secret was generated and saved in this config.[/yellow]"
            )
            console.print(
                "[dim]Open the WebUI and enter channels.websocket.tokenIssueSecret from "
                f"{config_path}, or rerun without --no-open to open the authenticated URL.[/dim]"
            )

    webui_bundle_mode = _webui_build_mode_for_interactive(yes=yes)

    config_arg = str(config_path)
    workspace_arg = str(Path(workspace).expanduser().resolve(strict=False)) if workspace else None
    runtime = GatewayRuntime(
        paths=GatewayRuntimePaths.for_instance(
            data_dir=config_path.parent,
            workspace=workspace_arg,
            config_path=config_arg,
        )
    )
    start_options = GatewayStartOptions(
        port=effective_gateway_port,
        workspace=workspace_arg,
        config_path=config_arg,
    )

    if background:
        _prepare_webui_bundle_for_gateway(runtime_config, mode=webui_bundle_mode)
        result = runtime.start_background(start_options)
        restarted = False
        restart_attempted = False
        if not result.ok and result.message == "gateway_already_running" and changed_webui:
            restart_attempted = True
            console.print("[yellow]WebUI config changed; restarting the background gateway.[/yellow]")
            result = runtime.restart(start_options, timeout_s=20)
            restarted = result.ok
        if not result.ok and (restart_attempted or result.message != "gateway_already_running"):
            action = "restarted" if restart_attempted else "started"
            console.print(f"[yellow]Gateway was not {action}: {result.message}[/yellow]")
            console.print(f"Logs: {result.status.log_path}")
            raise typer.Exit(1)
        if restarted:
            console.print("[green]Gateway restarted in the background.[/green]")
        elif result.ok:
            console.print("[green]Gateway started in the background.[/green]")
        else:
            console.print("[yellow]Gateway is already running in the background.[/yellow]")
        console.print(
            "Manage this instance: "
            f"[cyan]{_gateway_instance_command('status', config_path=config_path, workspace=workspace)}[/cyan]"
        )
        console.print(
            "View logs: "
            f"[cyan]{_gateway_instance_command('logs', config_path=config_path, workspace=workspace)}[/cyan]"
        )
        console.print("[dim]Closing the browser does not stop channels or automations.[/dim]")
        console.print(
            "Stop navin: "
            f"[cyan]{_gateway_instance_command('stop', config_path=config_path, workspace=workspace)}[/cyan]"
        )
        if not no_open:
            _open_webui_browser(webui_url)
        return

    gateway_ready = _gateway_health_ready(runtime_config.gateway.host, effective_gateway_port)
    webui_ready = _webui_endpoint_reachable(webui_url)
    if gateway_ready and webui_ready:
        console.print("[yellow]Gateway is already running; attaching to the existing WebUI.[/yellow]")
        console.print(
            "Restart the gateway if you need it to pick up local source changes: "
            f"[cyan]{_gateway_instance_command('restart', config_path=config_path, workspace=workspace)}[/cyan]"
        )
        if not no_open:
            _open_webui_browser(webui_url, wait=False)
        if runtime.status().running:
            _attach_to_background_gateway(runtime)
        else:
            console.print(
                "[yellow]This gateway is controlled by another foreground command. "
                "Stop it from that terminal.[/yellow]"
            )
        return

    gateway_port_taken = gateway_ready or _tcp_endpoint_reachable(
        _host_for_local_browser(runtime_config.gateway.host),
        effective_gateway_port,
    )
    webui_port_taken = webui_ready
    if gateway_port_taken or webui_port_taken:
        _print_foreground_port_conflict(
            webui_url=webui_url,
            gateway_host=runtime_config.gateway.host,
            gateway_port=effective_gateway_port,
            config=runtime_config,
        )
        raise typer.Exit(1)

    _print_webui_foreground_lifecycle(attached=False)
    _run_gateway(
        runtime_config,
        port=effective_gateway_port,
        open_browser_url=None if no_open else webui_url,
        webui_bundle_mode=webui_bundle_mode,
    )


# ============================================================================
# Gateway / Server
# ============================================================================


def _run_gateway(
    config: Config,
    *,
    port: int | None = None,
    open_browser_url: str | None = None,
    webui_static_dist: bool = True,
    webui_bundle_mode: BuildMode = "warn",
    webui_runtime_surface: str = "browser",
    webui_runtime_capabilities: dict[str, Any] | None = None,
    health_server_enabled: bool = True,
) -> None:
    """Shared gateway runtime; ``open_browser_url`` opens a tab once channels are up."""
    # Lancée depuis le Finder/Explorer, l'app hérite d'un PATH minimal qui ne
    # voit ni npx ni uvx : compléter AVANT de démarrer quoi que ce soit, pour
    # que les serveurs MCP stdio, les presets et les prérequis de skills
    # trouvent les outils installés en user.
    from navin.utils.path_env import augment_path_for_user_tools

    augment_path_for_user_tools()

    from navin.agent.tools.message import MessageTool
    from navin.bus.queue import MessageBus
    from navin.bus.runtime_events import RuntimeEventBus
    from navin.channels.manager import ChannelManager
    from navin.cron.bound_runner import run_bound_cron_job
    from navin.cron.service import CronJobSkippedError, CronService
    from navin.cron.session_turns import is_bound_cron_job
    from navin.cron.spend import CronSpendHook
    from navin.cron.types import CronJob, CronLimits
    from navin.providers.factory import (
        build_provider_snapshot_allowing_unconfigured,
        load_provider_snapshot_allowing_unconfigured,
    )
    from navin.providers.image_generation import image_gen_provider_configs
    from navin.providers.unconfigured import UnconfiguredProvider
    from navin.session.manager import SessionManager
    from navin.session.webui_turns import WebuiTurnCoordinator
    from navin.triggers.local_runner import run_local_trigger_queue
    from navin.triggers.local_store import LocalTriggerStore
    from navin.webui.token_usage import TokenUsageHook

    port = port if port is not None else config.gateway.port
    webui_url = _webui_browser_url(config)
    gateway_host_for_browser = _host_for_local_browser(config.gateway.host)
    if health_server_enabled and _tcp_endpoint_reachable(gateway_host_for_browser, port):
        _print_foreground_port_conflict(
            webui_url=webui_url,
            gateway_host=config.gateway.host,
            gateway_port=port,
            config=config,
        )
        raise typer.Exit(1)
    if _webui_channel_enabled(config) and _webui_endpoint_reachable(webui_url):
        _print_foreground_port_conflict(
            webui_url=webui_url,
            gateway_host=config.gateway.host,
            gateway_port=port,
            config=config,
        )
        raise typer.Exit(1)

    _log_external_port_status(config)

    console.print(f"{__logo__} Starting navin gateway version {__version__} on port {port}...")
    if webui_runtime_surface == "browser":
        # A CLI install has no toast: one line here, from the daily cache, is
        # how its user learns a newer navin exists. The desktop shell runs the
        # gateway with its own surface and shows the update in its window.
        from navin.update.notice import notice_in_background

        notice_in_background(lambda text: console.print(f"[yellow]{escape(text)}[/yellow]"))
    if sys.platform != "win32":
        from navin.agent.tools.sandbox import ensure_native_sandbox

        sandbox_bin = ensure_native_sandbox()
        if sandbox_bin:
            console.print(f"OS sandbox: {sandbox_bin}")
        else:
            console.print(
                "[red]OS sandbox missing: navin-sandbox was not built. "
                "Agent commands run unconfined. Install rustup "
                "(https://rustup.rs) then `make native`, or ship a "
                "packaged build that includes the helper.[/red]"
            )
    _prepare_webui_bundle_for_gateway(
        config,
        mode=webui_bundle_mode,
        webui_static_dist=webui_static_dist,
    )
    sync_workspace_templates(config.workspace_path)

    # Managed model catalog (opt-in). Never block boot on the network (up to
    # 5 s): refresh in background unless no model is chosen yet. Later updates
    # come from Settings / chat picker (forced), or activate / plan change.
    from navin.providers.managed_catalog import sync_managed_catalog

    if config.model_catalog.enabled:
        if not config.agents.defaults.model:
            sync_managed_catalog(config, force=True, min_interval_s=0)
        else:
            def _sync_catalog_offline_copy() -> None:
                try:
                    from navin.config.loader import load_config as _load

                    sync_managed_catalog(_load(), force=True, min_interval_s=0)
                except Exception as exc:  # best-effort by design
                    logger.debug("Background catalog sync failed: {}", exc)

            threading.Thread(
                target=_sync_catalog_offline_copy,
                name="navin-catalog-sync",
                daemon=True,
            ).start()

    bus = MessageBus()
    # Lets subsystems with no bus of their own - the scheduler, the indexer,
    # the language servers - reach the WebUI notification centre.
    set_notification_bus(bus)
    runtime_events = RuntimeEventBus()
    provider_snapshot = build_provider_snapshot_allowing_unconfigured(config)
    if isinstance(provider_snapshot.provider, UnconfiguredProvider):
        console.print(
            "[yellow]No model provider configured yet. "
            "Gateway will start - finish setup in WebUI Settings → Providers after connecting.[/yellow]"
        )
        if provider_snapshot.provider.reason:
            console.print(f"[dim]{provider_snapshot.provider.reason}[/dim]")
    session_manager = SessionManager(config.workspace_path)

    # Self-heal the gateway state file with the current PID after any restart.
    from navin.config.loader import get_config_path
    from navin.gateway.runtime import GatewayRuntime, GatewayRuntimePaths

    config_path = str(get_config_path().resolve(strict=False))
    GatewayRuntime.refresh_state_pid(
        paths=GatewayRuntimePaths.for_instance(
            workspace=str(config.workspace_path)
            if not is_default_workspace(config.workspace_path)
            else None,
            config_path=config_path,
        )
    )

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(
        cron_store_path,
        max_consecutive_failures=config.loops.max_consecutive_failures,
        backoff_base_ms=config.loops.backoff_base_ms,
        backoff_cap_ms=config.loops.backoff_cap_ms,
        timezone_name=config.agents.defaults.timezone,
    )
    trigger_store = LocalTriggerStore(config.workspace_path)

    # Create agent with cron service
    agent = AgentLoop.from_config(
        config, bus,
        provider=provider_snapshot.provider,
        model=provider_snapshot.model,
        context_window_tokens=provider_snapshot.context_window_tokens,
        cron_service=cron,
        session_manager=session_manager,
        image_generation_provider_configs=image_gen_provider_configs(config),
        provider_snapshot_loader=load_provider_snapshot_allowing_unconfigured,
        runtime_events=runtime_events,
        provider_signature=provider_snapshot.signature,
        hooks=[
            TokenUsageHook(timezone_name=config.agents.defaults.timezone),
            CronSpendHook(cron),
            *_optional_managed_usage_hooks(config),
        ],
        local_trigger_store=trigger_store,
        hook_factories=list(DEFAULT_HOOK_FACTORIES),
    )
    # Pull plan limits from navin.live on a timer so Stripe renewals / upgrades
    # re-clamp the live AgentLoop without a manual Refresh or restart.
    from navin.optional_live import live_modules_available

    try:
        if not live_modules_available():
            raise ImportError("live account disabled")
        from navin.license_sync import start_license_sync, stop_license_sync

        license_sync = start_license_sync(lambda: agent)
    except ImportError:
        stop_license_sync = None  # type: ignore[assignment]
        license_sync = None
    WebuiTurnCoordinator(
        bus=bus,
        sessions=session_manager,
        schedule_background=lambda coro: agent._schedule_background(coro),
    ).subscribe(runtime_events)
    from navin.bus.events import OutboundMessage
    from navin.session.keys import session_key_for_channel

    def _channel_session_key(channel: str, chat_id: str) -> str:
        return session_key_for_channel(
            channel,
            chat_id,
            unified_session=config.agents.defaults.unified_session,
        )

    async def _deliver_to_channel(
        msg: OutboundMessage, *, record: bool = False, session_key: str | None = None,
    ) -> None:
        """Publish a user-visible message and mirror it into that channel's session."""
        metadata = dict(msg.metadata or {})
        record = record or bool(metadata.pop("_record_channel_delivery", False))
        if metadata != (msg.metadata or {}):
            msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=msg.content,
                reply_to=msg.reply_to,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        if (
            record
            and msg.channel != "cli"
            and msg.content.strip()
            and hasattr(session_manager, "get_or_create")
            and hasattr(session_manager, "save")
        ):
            key = session_key or _channel_session_key(msg.channel, msg.chat_id)
            session = session_manager.get_or_create(key)
            extra: dict[str, Any] = {"_channel_delivery": True}
            if msg.media:
                extra["media"] = list(msg.media)
            session.add_message("assistant", msg.content, **extra)
            session_manager.save(session)
        await bus.publish_outbound(msg)

    message_tool = getattr(agent, "tools", {}).get("message")
    if isinstance(message_tool, MessageTool):
        message_tool.set_send_callback(_deliver_to_channel)

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        async def _silent(*_args, **_kwargs):
            pass

        # Dream is an internal job - run directly, not through the agent loop.
        if job.name == "dream":
            from navin.agent.memory import MemoryStore

            dream_session_key = MemoryStore.dream_session_key
            prune_dream_sessions = MemoryStore.prune_dream_sessions

            store = agent.context.memory
            resp = None
            diff_body = ""
            try:
                result = store.build_dream_prompt()
                if result is None:
                    logger.info("Dream: nothing to process")
                    return None
                prompt, last_cursor = result
                key = dream_session_key()
                resp = await agent.process_direct(
                    prompt,
                    session_key=key,
                    ephemeral=True,
                    tools=store.build_dream_tools(),
                    on_progress=_silent,
                    runtime=_dream_runtime_override(agent, config),
                )
                # Ground truth: the real file delta, not the LLM's self-report.
                diff_body = store.dream_content_diff()
                productive = bool(diff_body) or (
                    not store.git.is_initialized()
                    and MemoryStore.dream_run_completed(resp)
                )
                if productive:
                    store.set_last_dream_cursor(last_cursor)
                    logger.info("Dream cron job completed, cursor advanced to {}", last_cursor)
                elif MemoryStore.dream_run_completed(resp):
                    logger.info(
                        "Dream cron job completed with no memory changes; "
                        "cursor not advanced",
                    )
                else:
                    logger.warning(
                        "Dream cron job did not complete; cursor remains at {}",
                        store.get_last_dream_cursor(),
                    )
            except Exception:
                logger.exception("Dream cron job failed")
            finally:
                from navin.webui.token_usage import record_response_token_usage

                record_response_token_usage(
                    resp,
                    source="dream",
                    timezone_name=config.agents.defaults.timezone,
                )
                sha = _commit_dream_changes(store)
                if sha:
                    logger.info("Dream commit: {}", sha)
                store.compact_history()
                prune_dream_sessions(agent.sessions.sessions_dir)
            return None

        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        if job.name == "trading-loop":
            try:
                from navin.trading.loop import maybe_tick

                await asyncio.to_thread(maybe_tick)
            except Exception:
                logger.exception("Cron trading-loop tick failed")
            return None

        if job.name == "marketing-loop":
            try:
                from navin.marketing.loop import maybe_tick as marketing_maybe_tick

                await asyncio.to_thread(marketing_maybe_tick)
            except Exception:
                logger.exception("Cron marketing-loop tick failed")
            return None

        if job.name == "heartbeat":
            from navin import workspace_layout

            # Desk ticks run even when the LLM turn is skipped (no chat target,
            # empty HEARTBEAT.md). Tenders follow, Career watch, Leads watch,
            # Marketing watch and Trading watch are not agent decisions.
            career_note = ""
            try:
                from navin.gateway.heartbeat_desks import tick_heartbeat_desks

                career_note = await asyncio.to_thread(tick_heartbeat_desks)
            except Exception:
                logger.exception("Heartbeat: desk ticks failed")

            heartbeat_file = workspace_layout.read_with_root_fallback(
                config.workspace_path, "HEARTBEAT.md"
            )
            try:
                content = heartbeat_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("Heartbeat: HEARTBEAT.md missing")
                return None
            if not _heartbeat_has_active_tasks(content):
                logger.debug("Heartbeat: HEARTBEAT.md has no active tasks")
                return None

            channel, chat_id = _pick_heartbeat_target()
            if channel == "cli":
                return None

            prompt = (
                _HEARTBEAT_PREAMBLE
                + "You are executing periodic heartbeat tasks. Read the active tasks below, "
                "perform each one, and report what you did:\n\n"
                + content
                + career_note
            )

            # Internal check: funnel all output through the post-run gate so the
            # turn can't deliver directly via the message tool and skip it.
            suppress_token = None
            if isinstance(message_tool, MessageTool):
                suppress_token = message_tool.set_suppress_delivery(True)
            try:
                resp = await agent.process_direct(
                    prompt,
                    session_key="heartbeat",
                    channel=channel,
                    chat_id=chat_id,
                    on_progress=_silent,
                )
            finally:
                if isinstance(message_tool, MessageTool) and suppress_token is not None:
                    message_tool.reset_suppress_delivery(suppress_token)

            # Keep a small tail of heartbeat history so the loop stays bounded.
            session = agent.sessions.get_or_create("heartbeat")
            session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
            agent.sessions.save(session)

            if not resp or not resp.content:
                return

            response = resp.content

            evaluator_prompt = resolve_evaluator_prompt(config.workspace_path)

            # Fail closed: stay silent on evaluator failure instead of notifying.
            should_notify = await evaluate_response(
                response=response,
                task_context=prompt,
                provider=agent.provider,
                model=agent.model,
                evaluator_prompt=evaluator_prompt,
                default_notify=False,
            )

            if should_notify:
                logger.info("Heartbeat: completed, delivering response")
                await _deliver_to_channel(
                    OutboundMessage(channel=channel, chat_id=chat_id, content=response),
                    record=True,
                )
            else:
                logger.info("Heartbeat: silenced by post-run evaluation")
            return response

        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}",
            job.name,
            job.id,
            reason,
        )
        raise CronJobSkippedError(reason)

    cron.on_job = on_cron_job

    def _webui_runtime_model_name() -> str | None:
        # Refresh from the saved config first: between a settings save and the
        # next turn the in-memory runtime lags behind, and the bootstrap would
        # otherwise report a stale model name. llm_runtime() also broadcasts
        # runtime_model_updated to connected clients when the model changed.
        try:
            model = agent.llm_runtime().model
        except Exception:
            model = getattr(agent, "model", None)
        if isinstance(model, str):
            stripped = model.strip()
            return stripped or None
        return None

    # Create channel manager (forwards SessionManager so the WebSocket channel
    # can serve the embedded webui's REST surface).
    from navin.webui.runtime_surface import desktop_sidecar_surface

    if webui_runtime_surface in {"native", "desktop"}:
        webui_runtime_surface = "native"
    else:
        webui_runtime_surface = desktop_sidecar_surface()
    channels = ChannelManager(
        config,
        bus,
        session_manager=session_manager,
        cron_service=cron,
        local_trigger_store=trigger_store,
        webui_runtime_model_name=_webui_runtime_model_name,
        # Guardrails > Memory adds or drops the recall tool at runtime through
        # the registry's public register/unregister; the loop is not involved.
        webui_tool_registry=lambda: agent.tools,
        webui_cron_pending_job_ids=getattr(agent, "pending_cron_job_ids_for_session", None),
        webui_local_trigger_pending_ids=getattr(
            agent,
            "pending_local_trigger_ids_for_session",
            None,
        ),
        webui_static_dist=webui_static_dist,
        webui_runtime_surface=webui_runtime_surface,
        webui_runtime_capabilities=webui_runtime_capabilities,
    )

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        sidebar_state = read_webui_sidebar_state()
        return _pick_heartbeat_target_from_sessions(
            enabled_channels=channels.enabled_channels,
            sessions=session_manager.list_sessions(),
            archived_keys=sidebar_state.get("archived_keys", []),
        )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    hb_cfg = config.gateway.heartbeat
    if hb_cfg.enabled:
        console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    else:
        console.print("[yellow]✗[/yellow] Heartbeat: disabled")

    async def _health_server(host: str, health_port: int):
        """Lightweight HTTP health endpoint on the gateway port."""
        import json as _json

        connection_slots = asyncio.Semaphore(_GATEWAY_HEALTH_MAX_CONNECTIONS)

        async def handle(reader, writer):
            if connection_slots.locked():
                writer.close()
                return

            async with connection_slots:
                try:
                    data = await asyncio.wait_for(
                        reader.read(4096),
                        timeout=_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS,
                    )
                    request_line = data.split(b"\r\n", 1)[0].decode(
                        "utf-8", errors="replace",
                    )
                    method, path = "", ""
                    parts = request_line.split(" ")
                    if len(parts) >= 2:
                        method, path = parts[0], parts[1]

                    if method == "GET" and path == "/health":
                        body = _json.dumps({"status": "ok"})
                        status = "200 OK"
                        content_type = "application/json"
                    else:
                        body = "Not Found"
                        status = "404 Not Found"
                        content_type = "text/plain"

                    resp = (
                        f"HTTP/1.0 {status}\r\n"
                        f"Content-Type: {content_type}\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n"
                        f"\r\n{body}"
                    )
                    writer.write(resp.encode())
                    await writer.drain()
                except (asyncio.TimeoutError, ConnectionError):
                    pass
                finally:
                    writer.close()

        server = await asyncio.start_server(handle, host, health_port)
        _print_gateway_health_endpoint(host, health_port)
        async with server:
            await server.serve_forever()
    # Register Dream system job (idempotent on restart)
    from navin.cron.types import CronJob, CronPayload
    dream_cfg = config.agents.defaults.dream
    if dream_cfg.enabled:
        cron.register_system_job(CronJob(
            id="dream",
            name="dream",
            schedule=dream_cfg.build_schedule(config.agents.defaults.timezone),
            payload=CronPayload(kind="system_event"),
            limits=CronLimits(
                daily_token_budget=dream_cfg.daily_token_budget,
                max_consecutive_failures=dream_cfg.max_consecutive_failures,
            ),
        ))
        console.print(f"[green]✓[/green] Dream: {dream_cfg.describe_schedule()}")
    else:
        console.print("[yellow]○[/yellow] Dream: disabled")
        _advance_dream_cursor_if_behind(agent.context.memory)

    # Register Heartbeat system job (idempotent on restart)
    if hb_cfg.enabled:
        cron.register_system_job(CronJob(
            id="heartbeat",
            name="heartbeat",
            schedule=hb_cfg.build_schedule(config.agents.defaults.timezone),
            payload=CronPayload(kind="system_event"),
            limits=CronLimits(
                daily_token_budget=hb_cfg.daily_token_budget,
                max_consecutive_failures=hb_cfg.max_consecutive_failures,
            ),
        ))

    async def _open_browser_when_ready() -> None:
        """Wait for the gateway to bind, then point the user's browser at the webui."""
        if not open_browser_url:
            return
        try:
            from urllib.parse import urlparse

            parsed = urlparse(open_browser_url)
            target_host = parsed.hostname or config.gateway.host or "127.0.0.1"
            target_port = parsed.port or port
            # Channels start asynchronously; a short poll lets us avoid racing the bind.
            for _ in range(40):  # ~4s max
                try:
                    reader, writer = await asyncio.open_connection(
                        target_host,
                        target_port,
                    )
                    writer.close()
                    with suppress(Exception):
                        await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            # App-window (Cursor-style) with tab fallback; already past the bind poll.
            await asyncio.to_thread(_open_webui_browser, open_browser_url, wait=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Could not open the webui browser")

    async def run():
        tasks: list[asyncio.Task] = []
        shutdown_task: asyncio.Task | None = None
        runtime_tasks: asyncio.Future | None = None
        runtime_tasks_drained = False
        shutdown_event = asyncio.Event()
        _ensure_interactive_tty_mode()
        restore_shutdown_handlers = _install_gateway_shutdown_handlers(
            asyncio.get_running_loop(),
            shutdown_event,
            tasks,
            console.print,
        )
        try:
            # Pre-import the provider SDKs off the event loop: they are loaded
            # lazily so startup never pays for them, but the first user turn
            # should not pay the ~1 s import either. Failures are irrelevant
            # here - the real import at call time will report them properly.
            def _prewarm_provider_sdks() -> None:
                for module in ("anthropic", "openai"):
                    try:
                        __import__(module)
                    except Exception:
                        pass

            threading.Thread(
                target=_prewarm_provider_sdks,
                name="navin-sdk-prewarm",
                daemon=True,
            ).start()

            await cron.start()
            trading_tick_inflight = False

            async def _trading_loop_supervisor() -> None:
                nonlocal trading_tick_inflight
                while True:
                    try:
                        from navin.trading.loop import guard_tick, maybe_tick

                        while True:
                            if trading_tick_inflight:
                                await asyncio.sleep(20)
                                continue
                            trading_tick_inflight = True
                            try:
                                await asyncio.to_thread(maybe_tick)
                                # Intraday guard: stops, venue fills and threshold
                                # alerts between research cycles (own interval).
                                await asyncio.to_thread(guard_tick)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Trading loop supervisor tick failed")
                            finally:
                                trading_tick_inflight = False
                            await asyncio.sleep(20)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Trading loop supervisor crashed - restarting in 5s")
                        trading_tick_inflight = False
                        await asyncio.sleep(5)

            career_tick_inflight = False

            async def _career_loop_supervisor() -> None:
                nonlocal career_tick_inflight
                while True:
                    try:
                        from navin.career.loop import maybe_tick as career_maybe_tick

                        while True:
                            if career_tick_inflight:
                                await asyncio.sleep(20)
                                continue
                            career_tick_inflight = True
                            try:
                                await asyncio.to_thread(career_maybe_tick)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Career loop supervisor tick failed")
                            finally:
                                career_tick_inflight = False
                            await asyncio.sleep(20)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Career loop supervisor crashed - restarting in 5s")
                        career_tick_inflight = False
                        await asyncio.sleep(5)

            marketing_tick_inflight = False

            async def _marketing_loop_supervisor() -> None:
                nonlocal marketing_tick_inflight
                while True:
                    try:
                        from navin.marketing.loop import maybe_tick as marketing_maybe_tick

                        while True:
                            if marketing_tick_inflight:
                                await asyncio.sleep(20)
                                continue
                            marketing_tick_inflight = True
                            try:
                                await asyncio.to_thread(marketing_maybe_tick)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Marketing loop supervisor tick failed")
                            finally:
                                marketing_tick_inflight = False
                            await asyncio.sleep(20)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Marketing loop supervisor crashed - restarting in 5s")
                        marketing_tick_inflight = False
                        await asyncio.sleep(5)

            tenders_tick_inflight = False

            async def _tenders_loop_supervisor() -> None:
                nonlocal tenders_tick_inflight
                while True:
                    try:
                        from navin.tenders.loop import maybe_tick as tenders_maybe_tick

                        while True:
                            if tenders_tick_inflight:
                                await asyncio.sleep(20)
                                continue
                            tenders_tick_inflight = True
                            try:
                                await asyncio.to_thread(tenders_maybe_tick)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Tenders loop supervisor tick failed")
                            finally:
                                tenders_tick_inflight = False
                            await asyncio.sleep(20)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Tenders loop supervisor crashed - restarting in 5s")
                        tenders_tick_inflight = False
                        await asyncio.sleep(5)

            leads_tick_inflight = False

            async def _leads_loop_supervisor() -> None:
                nonlocal leads_tick_inflight
                while True:
                    try:
                        from navin.leads.loop import maybe_tick as leads_maybe_tick

                        while True:
                            if leads_tick_inflight:
                                await asyncio.sleep(20)
                                continue
                            leads_tick_inflight = True
                            try:
                                await asyncio.to_thread(leads_maybe_tick)
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                logger.exception("Leads loop supervisor tick failed")
                            finally:
                                leads_tick_inflight = False
                            await asyncio.sleep(20)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("Leads loop supervisor crashed - restarting in 5s")
                        leads_tick_inflight = False
                        await asyncio.sleep(5)

            async def _run_isolated_desk_loops() -> None:
                """Career / Leads / Marketing / Tenders / Trading must not cancel the WebUI."""
                desk_loop_tasks = [
                    asyncio.create_task(_trading_loop_supervisor(), name="navin-trading-loop"),
                    asyncio.create_task(_career_loop_supervisor(), name="navin-career-loop"),
                    asyncio.create_task(_marketing_loop_supervisor(), name="navin-marketing-loop"),
                    asyncio.create_task(_tenders_loop_supervisor(), name="navin-tenders-loop"),
                    asyncio.create_task(_leads_loop_supervisor(), name="navin-leads-loop"),
                ]
                try:
                    await asyncio.gather(*desk_loop_tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    for item in desk_loop_tasks:
                        if not item.done():
                            item.cancel()
                    await asyncio.gather(*desk_loop_tasks, return_exceptions=True)
                    raise

            tasks = [
                asyncio.create_task(agent.run(), name="navin-agent-loop"),
                asyncio.create_task(channels.start_all(), name="navin-channels"),
                asyncio.create_task(
                    run_local_trigger_queue(
                        store=trigger_store,
                        submit_turn=getattr(agent, "submit_local_trigger_turn", None),
                    ),
                    name="navin-local-triggers",
                ),
                asyncio.create_task(_run_isolated_desk_loops(), name="navin-desk-loops"),
            ]
            # When the WebSocket/WebUI channel is enabled it already binds the
            # gateway port and serves GET /health. A second listener on the same
            # port races the channel start and leaves the WebUI unreachable.
            start_standalone_health = health_server_enabled and not _webui_channel_enabled(
                config
            )
            if start_standalone_health:
                tasks.append(asyncio.create_task(
                    _health_server(config.gateway.host, port),
                    name="navin-health-server",
                ))
            elif health_server_enabled and _webui_channel_enabled(config):
                _print_gateway_health_endpoint(
                    str((_webui_config_dict(config).get("host") or config.gateway.host)),
                    int((_webui_config_dict(config).get("port") or port)),
                )
            if open_browser_url:
                tasks.append(asyncio.create_task(
                    _open_browser_when_ready(),
                    name="navin-open-browser",
                ))
            runtime_tasks = asyncio.gather(*tasks)
            shutdown_task = asyncio.create_task(
                shutdown_event.wait(),
                name="navin-gateway-shutdown",
            )
            done, _pending = await asyncio.wait(
                {runtime_tasks, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runtime_tasks in done:
                runtime_tasks_drained = True
                await runtime_tasks
            elif runtime_tasks is not None:
                runtime_tasks.cancel()
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except Exception:
            import traceback

            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
        finally:
            try:
                if license_sync is not None and stop_license_sync is not None:
                    stop_license_sync(license_sync)
                if shutdown_task and not shutdown_task.done():
                    shutdown_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await shutdown_task
                cron.stop()
                agent.stop()
                # Some SDKs swallow task cancellation while attempting to reconnect.
                # Close channel transports before waiting for their runners to exit.
                await channels.stop_all()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if runtime_tasks is not None and not runtime_tasks_drained:
                    with suppress(asyncio.CancelledError, Exception):
                        await runtime_tasks
                # A stopped gateway must not leave exec sessions or browser
                # engines running: a dev server started through a session
                # would keep its port with nothing left to stop it.
                await _close_agent_subprocesses()
                # Flush all cached sessions to durable storage before exit.
                # This prevents data loss on filesystems with write-back
                # caching (rclone VFS, NFS, FUSE mounts, etc.).
                flushed = agent.sessions.flush_all()
                if flushed:
                    logger.info("Shutdown: flushed {} session(s) to disk", flushed)
            finally:
                restore_shutdown_handlers()

    asyncio.run(run())


app.add_typer(
    create_gateway_app(
        console=console,
        log_handler_id=_log_handler_id,
        load_runtime_config=_load_runtime_config,
        run_gateway=_run_gateway,
        prepare_webui_bundle=lambda config, mode: _prepare_webui_bundle_for_gateway(
            config,
            mode=mode,
        ),
    ),
    name="gateway",
)


ports_app = typer.Typer(help="List and check Navin service ports")


@ports_app.callback(invoke_without_command=True)
def ports_root(
    ctx: typer.Context,
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace path"),
) -> None:
    """Show the port registry status (default) or run a subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    runtime = _load_runtime_config(config, workspace)
    from navin.ports import check_ports, format_port_table

    results = check_ports(runtime, include_external=True)
    console.print(format_port_table(results))
    conflicts = [row for row in results if row.is_conflict]
    if conflicts:
        console.print()
        console.print(
            f"[yellow]{len(conflicts)} Navin-owned port(s) held by another process.[/yellow]"
        )


@ports_app.command("check")
def ports_check(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace path"),
) -> None:
    """Exit non-zero when a Navin-owned port is busy with a non-Navin process."""
    runtime = _load_runtime_config(config, workspace)
    from navin.ports import check_navin_ports, check_ports, format_port_table

    results = check_ports(runtime, include_external=True)
    console.print(format_port_table(results))
    conflicts = check_navin_ports(runtime)
    if not conflicts:
        console.print("[green]OK[/green] - no Navin port conflicts")
        raise typer.Exit(0)
    console.print()
    console.print(
        f"[red]FAIL[/red] - {len(conflicts)} Navin-owned port(s) conflict with other services"
    )
    for row in conflicts:
        endpoint = f"{row.role.host}:{row.role.port}"
        console.print(f"  - {row.role.spec.name} {endpoint}: {row.detail}")
    raise typer.Exit(1)


app.add_typer(ports_app, name="ports")


# ============================================================================
# Agent Commands
# ============================================================================


def _warn_about_orphaned_subagents(agent_loop: Any) -> None:
    """Say so when a one-shot run ends with subagents still working.

    ``spawn`` reports back through the bus, but a ``--message`` run exits as soon
    as the turn is done, so that report has nowhere to land. The agent will have
    promised to follow up; without this notice the work vanishes silently.
    """
    try:
        running = agent_loop.subagents.get_running_count()
    except Exception:  # noqa: BLE001 - a missing manager must not break shutdown
        return
    if running < 1:
        return
    console.print(
        f"\n[yellow]{running} subagent(s) were still running and did not report back.[/yellow]\n"
        "[dim]A --message run exits after one turn. Use the interactive session "
        "to receive subagent results.[/dim]"
    )


async def _close_agent_subprocesses() -> None:
    """Stop the long-lived child processes tools may leave behind.

    Anything still attached to the loop when it closes is torn down by the
    garbage collector afterwards, which prints an "Event loop is closed"
    traceback under the agent's last message. A new tool that owns a subprocess
    belongs here.
    """
    from navin.agent.tools.browser import shutdown_browser_sessions
    from navin.agent.tools.computer import shutdown_computer_sessions
    from navin.agent.tools.exec_session import DEFAULT_EXEC_SESSION_MANAGER

    for closer in (
        DEFAULT_EXEC_SESSION_MANAGER.shutdown,
        shutdown_browser_sessions,
        shutdown_computer_sessions,
    ):
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001 - shutdown must not mask the turn's result
            logger.debug("Shutdown cleanup failed: {}", exc)


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show navin runtime logs during chat"),
):
    """Interact with the agent directly."""
    from navin.bus.queue import MessageBus
    from navin.cron.service import CronService
    from navin.providers.image_generation import image_gen_provider_configs

    config = _load_runtime_config(config, workspace)
    sync_workspace_templates(config.workspace_path)

    # Start the code index off the first-tool path so a cold repo is warm
    # before the interactive prompt or -m turn asks for a symbol.
    from navin.index.warmer import schedule_warm

    schedule_warm(config.workspace_path)

    bus = MessageBus()

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    _set_navin_logs(logs)

    try:
        agent_loop = AgentLoop.from_config(
            config, bus,
            cron_service=cron,
            image_generation_provider_configs=image_gen_provider_configs(config),
            hook_factories=list(DEFAULT_HOOK_FACTORIES),
        )
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    restart_notice = consume_restart_notice_from_env()
    if restart_notice and should_show_cli_restart_notice(restart_notice, session_id):
        _print_agent_response(
            format_restart_completed_message(restart_notice.started_at_raw),
            render_markdown=False,
        )

    # Shared reference for progress callbacks
    _thinking: ThinkingSpinner | None = None

    def _make_progress(renderer: StreamRenderer | None = None):
        reasoning_buffer = _ReasoningBuffer()

        async def _cli_progress(content: str, *, tool_hint: bool = False, reasoning: bool = False, **_kwargs: Any) -> None:
            ch = agent_loop.channels_config

            if _kwargs.get("reasoning_end"):
                if ch and not ch.show_reasoning:
                    reasoning_buffer.clear()
                else:
                    _flush_cli_reasoning(reasoning_buffer, _thinking, renderer)
                return

            if reasoning:
                if ch and not ch.show_reasoning:
                    reasoning_buffer.clear()
                    return
                text = reasoning_buffer.add(content)
                if text:
                    _print_cli_reasoning(text, _thinking, renderer)
                return
            if ch and tool_hint and not ch.send_tool_hints:
                return
            if ch and not tool_hint and not ch.send_progress:
                return
            _print_cli_progress_line(content, _thinking, renderer)
        return _cli_progress

    if message:
        # Single message mode - direct call, no bus needed
        async def run_once():
            renderer = StreamRenderer(
                render_markdown=markdown,
                bot_name=config.agents.defaults.bot_name,
                bot_icon=config.agents.defaults.bot_icon,
            )
            response = await agent_loop.process_direct(
                message, session_id,
                on_progress=_make_progress(renderer),
                on_stream=renderer.on_delta,
                on_stream_end=renderer.on_end,
            )
            if not renderer.streamed:
                await renderer.close()
                print_kwargs: dict[str, Any] = {}
                if renderer.header_printed:
                    print_kwargs["show_header"] = False
                _print_agent_response(
                    response.content if response else "",
                    render_markdown=markdown,
                    metadata=response.metadata if response else None,
                    **print_kwargs,
                )
            _warn_about_orphaned_subagents(agent_loop)
            await agent_loop.close_mcp()
            await _close_agent_subprocesses()

        asyncio.run(run_once())
    else:
        # Interactive mode - route through bus like other channels
        from navin.bus.events import InboundMessage
        _init_prompt_session()
        _model, _preset_tag = _model_display(config)
        _icon = config.agents.defaults.bot_icon or __logo__
        console.print(f"{_icon} Interactive mode [bold blue]({_model})[/bold blue]{_preset_tag} - type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run(
                recovery_channel=cli_channel, recovery_session_key=session_id,
            ))
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[Any] = []
            renderer: StreamRenderer | None = None
            reasoning_buffer = _ReasoningBuffer()

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        event = outbound_event_from_message(msg)

                        if isinstance(event, StreamDeltaEvent):
                            if renderer:
                                await renderer.on_delta(msg.content)
                            continue
                        if isinstance(event, StreamEndEvent):
                            if renderer:
                                await renderer.on_end(
                                    resuming=event.resuming,
                                )
                            continue
                        if isinstance(event, StreamedResponseEvent):
                            if msg.content and renderer and not renderer.streamed:
                                await renderer.close()
                                print_kwargs: dict[str, Any] = {}
                                if renderer.header_printed:
                                    print_kwargs["show_header"] = False
                                _print_agent_response(
                                    msg.content,
                                    render_markdown=markdown,
                                    metadata=msg.metadata,
                                    **print_kwargs,
                                )
                            turn_done.set()
                            continue

                        if await _maybe_print_interactive_progress(
                            msg,
                            renderer,
                            agent_loop.channels_config,
                            renderer,
                            reasoning_buffer,
                        ):
                            continue

                        if not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg)
                            turn_done.set()
                        elif msg.content:
                            await _print_interactive_response(
                                msg.content,
                                render_markdown=markdown,
                                metadata=msg.metadata,
                            )

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        # Stop spinner before user input to avoid prompt_toolkit conflicts
                        if renderer:
                            renderer.stop_for_input()
                        user_input = _sanitize_surrogates(await _read_interactive_input_async())
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        reasoning_buffer.clear()
                        renderer = StreamRenderer(
                            render_markdown=markdown,
                            bot_name=config.agents.defaults.bot_name,
                            bot_icon=config.agents.defaults.bot_icon,
                        )

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                            metadata={"_wants_stream": True},
                        ))

                        await turn_done.wait()

                        if turn_response:
                            response_msg = turn_response[0]
                            content = response_msg.content
                            meta = response_msg.metadata
                            if content and not isinstance(response_msg.event, StreamedResponseEvent):
                                if renderer:
                                    await renderer.close()
                                print_kwargs: dict[str, Any] = {}
                                if renderer and renderer.header_printed:
                                    print_kwargs["show_header"] = False
                                _print_agent_response(
                                    content,
                                    render_markdown=markdown,
                                    metadata=meta,
                                    **print_kwargs,
                                )
                        elif renderer and not renderer.streamed:
                            await renderer.close()
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()
                await _close_agent_subprocesses()

        asyncio.run(run_interactive())


@app.command(hidden=True)
def tui(
    path: str | None = typer.Argument(None, help="Project folder to work in (default: the current directory)", show_default=False),
    session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID (default: last navin-cli session, else cli:direct)"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory (default: the project folder)"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Write navin runtime logs to the log file while navin-cli runs"),
):
    """Hidden alias of `navin-cli`. Prefer the `navin-cli` command.

    Like `navin-cli .` or `navin-cli ~/projects/app`, it works in the folder
    you launch it from.
    """
    # Must run before Textual is imported: it reads TEXTUAL_COLOR_SYSTEM once.
    from navin.tui.terminal import prepare_terminal_env

    prepare_terminal_env()
    try:
        import textual  # noqa: F401
    except ImportError:
        console.print(
            "[red]The terminal UI needs the 'textual' package.[/red]\n"
            "Install it with: [bold]uv pip install 'textual>=6,<7'[/bold] (or pip install textual)"
        )
        raise typer.Exit(1)

    project = Path(path or ".").expanduser()
    if not project.is_dir():
        console.print(f"[red]Not a folder: {project}[/red]")
        raise typer.Exit(1)
    project = project.resolve()
    # navin-cli is a project tool: the agent, Graph and Evolve all work in the
    # folder it was started from (or the one given), like `navin-cli .`.
    os.chdir(project)
    # Packaged builds: make sure `navin-cli` exists next to `navin` (no-op from source).
    with suppress(Exception):
        from navin.cli_link import ensure_cli_on_path

        ensure_cli_on_path()

    config_path = Path(config).expanduser().resolve() if config else None
    loaded = _load_runtime_config(config, workspace or str(project))
    # navin-cli owns the screen: runtime logs would corrupt it, so they go to the
    # file sink only (same switch as `navin agent --logs`).
    _set_navin_logs(logs)

    from navin.tui import run_tui

    run_tui(config=loaded, session_id=session_id, config_path=config_path, project_root=project)


def run_cli() -> None:
    """Entry point of the `navin-cli` command: the terminal UI, in this folder."""
    sys.argv = [sys.argv[0], "tui", *sys.argv[1:]]
    run()


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Show channel status."""
    from navin.channels.registry import discover_all

    _, loaded = _load_inspection_config(config=config)

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled")

    for name, cls in sorted(discover_all().items()):
        section = getattr(loaded.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


@channels_app.command("login")
def channels_login(
    channel_name: str = typer.Argument(..., help="Channel name (e.g. whatsapp)"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-authentication even if already logged in"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with a channel via QR code or other interactive login."""
    from navin.channels.registry import discover_all

    _, loaded = _load_inspection_config(config=config)
    channel_cfg = getattr(loaded.channels, channel_name, None) or {}

    # Validate channel exists
    all_channels = discover_all()
    if channel_name not in all_channels:
        available = ", ".join(all_channels.keys())
        console.print(f"[red]Unknown channel: {channel_name}[/red]  Available: {available}")
        raise typer.Exit(1)

    console.print(f"{__logo__} {all_channels[channel_name].display_name} Login\n")

    channel_cls = all_channels[channel_name]
    channel = channel_cls(channel_cfg, bus=None)

    success = asyncio.run(channel.login(force=force))

    if not success:
        raise typer.Exit(1)


# ============================================================================
# Language Server Commands
# ============================================================================

app.add_typer(create_lsp_app(console=console), name="lsp")
app.add_typer(create_app_templates_app(console=console), name="app")

# Desktop control (computer use): enable / doctor / dedicated display.
from navin.cli.computer import create_computer_app  # noqa: E402

app.add_typer(create_computer_app(console=console), name="computer")


# ============================================================================
# AGI: skills evolution + memory switches (same as the AGI panel)
# ============================================================================

app.add_typer(create_agi_app(console=console), name="agi")


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage optional navin features")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list(
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """List optional navin features."""
    from navin.channels.registry import discover_channel_names, discover_plugins
    from navin.config.loader import load_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)

    _print_enable_options(
        feature_support.optional_dependency_groups(),
        set(discover_channel_names()),
        discover_plugins(),
        load_config(resolved_config_path),
    )


@plugins_app.command("enable")
def plugins_enable(
    name: str = typer.Argument(..., help="Feature name (e.g. matrix, bedrock, telegram)"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show optional package install logs"),
):
    """Enable a navin feature."""
    from navin.config.loader import get_config_path, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)
    resolved_config_path = resolved_config_path or get_config_path()
    _set_navin_logs(logs)

    try:
        payload = feature_support.enable_optional_feature(
            name,
            config_path=resolved_config_path,
            runner=feature_support.run_install_command,
        )
    except feature_support.OptionalFeatureError as exc:
        console.print(f"[red]{escape(exc.message)}[/red]")
        raise typer.Exit(1) from exc

    message = payload.get("last_action", {}).get("message") or f"Enabled feature '{name}'"
    console.print(f"[green]{escape(message)}[/green]")


@plugins_app.command("disable")
def plugins_disable(
    name: str = typer.Argument(..., help="Channel name (e.g. telegram, matrix, slack)"),
    config_path: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Disable a navin channel feature."""
    from navin.config.loader import get_config_path, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None:
        set_config_path(resolved_config_path)
    resolved_config_path = resolved_config_path or get_config_path()

    try:
        payload = feature_support.disable_optional_feature(name, config_path=resolved_config_path)
    except feature_support.OptionalFeatureError as exc:
        console.print(f"[red]{escape(exc.message)}[/red]")
        raise typer.Exit(1) from exc

    message = payload.get("last_action", {}).get("message") or f"Disabled channel '{name}'"
    console.print(f"[green]{escape(message)}[/green] in {resolved_config_path}")


# ============================================================================
# Career desk (same store as Studio #/career, Tauri, and the career tool)
# ============================================================================


@app.command(
    name="tenders",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def tenders_desk(ctx: typer.Context) -> None:
    """Start, stop, schedule, tick or watch the Tenders loop.

    Same store as Studio ``#/tenders``, Tauri, HTTP ``/api/tenders``, and
    ``python -m navin.tenders.desk_cli``. Heartbeat is follow/watch only.
    Never send a buyer mail from the loop or from heartbeat.
    """
    from navin.tenders.desk_cli import main as tenders_main

    raise typer.Exit(tenders_main(list(ctx.args)))


@app.command(
    name="career",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def career_desk(ctx: typer.Context) -> None:
    """Start, stop, schedule, tick or watch the Career loop.

    Same store as Studio ``#/career``, Tauri, HTTP ``/api/career``, and
    ``python -m navin.career.desk_cli``. Heartbeat is watch only.
    """
    from navin.career.desk_cli import main as career_main

    raise typer.Exit(career_main(list(ctx.args)))


@app.command(
    name="trading",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def trading_desk(ctx: typer.Context) -> None:
    """Start, stop, schedule, tick or watch the Trading loop.

    Same store as Studio ``#/trading``, Tauri, HTTP ``/api/trading``, and
    ``python -m navin.trading.desk_cli``. Heartbeat is watch only.
    """
    from navin.trading.desk_cli import main as trading_main

    raise typer.Exit(trading_main(list(ctx.args)))


@app.command(
    name="marketing",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def marketing_desk(ctx: typer.Context) -> None:
    """Start, stop, schedule, tick or watch the Marketing loop.

    Same store as Studio ``#/marketing``, Tauri, HTTP ``/api/marketing``, and
    ``python -m navin.marketing.desk_cli``. Heartbeat is watch only.
    """
    from navin.marketing.desk_cli import main as marketing_main

    raise typer.Exit(marketing_main(list(ctx.args)))


@app.command(
    name="leads",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    add_help_option=False,
)
def leads_desk(ctx: typer.Context) -> None:
    """Start, stop, schedule, tick or watch the Leads loop.

    Same store as Studio ``#/leads``, Tauri, HTTP ``/api/leads``, and
    ``python -m navin.leads.desk_cli``. Heartbeat is watch only.
    Never scrape LinkedIn. Never send a sequence from the loop or heartbeat.
    """
    from navin.leads.desk_cli import main as leads_main

    raise typer.Exit(leads_main(list(ctx.args)))


# ============================================================================
# Diagnostics
# ============================================================================


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Report what this installation can do and what is missing.

    Works the same from source and from a packaged build, on Linux, macOS and
    Windows: nothing here needs a shell script or a source checkout.
    """
    from navin.diagnostics import doctor_report, missing_required, workspace_note

    _config_path, loaded = _load_inspection_config(config=config, workspace=workspace)
    sections = doctor_report(loaded, workspace=loaded.workspace_path)
    sections[0].checks.append(workspace_note(loaded.workspace_path))

    console.print(f"{__logo__} navin doctor\n")
    for section in sections:
        # A section can come back empty when it reports on history this
        # installation has not accumulated yet.
        if not section.checks:
            continue
        console.print(f"[bold]{section.title}[/bold]")
        for check in section.checks:
            mark = "[green]OK[/green]  " if check.ok else "[yellow]MISS[/yellow]"
            line = f"  {mark} {check.name:<22} {escape(check.detail)}"
            if not check.ok and check.hint:
                line += f" [dim]- {escape(check.hint)}[/dim]"
            console.print(line)
        console.print("")

    blocking = missing_required(sections)
    if blocking:
        names = ", ".join(check.name for check in blocking)
        console.print(f"[red]Missing and needed: {names}[/red]")
        raise typer.Exit(1)
    console.print("[dim]Missing optional tools only remove the capabilities they serve.[/dim]")


def _download_with_progress(service: Any) -> dict[str, Any]:
    """Run the signed download on a thread and draw its progress in the terminal."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = service.download_update()
        except Exception as exc:  # noqa: BLE001 - reported on the main thread
            outcome["error"] = exc

    worker = threading.Thread(target=run, name="navin-update-download", daemon=True)
    worker.start()
    with Progress(
        TextColumn("[bold]Downloading[/bold]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("download", total=None)
        while worker.is_alive():
            status = service.update_status()
            total = int(status.get("totalBytes") or 0)
            done = int(status.get("downloadedBytes") or 0)
            if total:
                progress.update(task, total=total, completed=done)
            worker.join(0.2)
        status = service.update_status()
        if status.get("totalBytes"):
            progress.update(task, total=status["totalBytes"], completed=status["totalBytes"])
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


@app.command()
def update(
    check: bool = typer.Option(False, "--check", help="Only report whether a newer version exists"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Install without asking"),
):
    """Update navin to the latest signed release.

    Checks the signed release manifest, downloads the archive for this OS,
    verifies its checksum, proves the new version starts, then swaps the
    installation in place. Works for the packaged CLI installed with
    `curl https://navin.live/install`; the desktop app updates itself from
    its own window.
    """
    from navin.update import service

    kind = service._install_kind()
    if kind == "source":
        console.print(
            f"{__logo__} navin v{__version__} runs from a source checkout. "
            "Update it with git pull (or pip / uv), not with this command."
        )
        return
    if not service.updates_configured():
        console.print(
            "[yellow]This build has no update server configured.[/yellow] "
            "Download the latest release from https://navin.live/download"
        )
        raise typer.Exit(1)

    try:
        info = service.check_for_update(force=True)
    except service.UpdateError as exc:
        console.print(f"[red]Could not check for updates: {escape(str(exc))}[/red]")
        raise typer.Exit(1) from exc

    if not info.get("available"):
        console.print(f"{__logo__} navin v{__version__} is up to date.")
        return

    latest = str(info.get("latestVersion") or "")
    console.print(f"{__logo__} navin [bold]{latest}[/bold] is available (you have {__version__}).")
    notes = str(info.get("notes") or "").strip()
    if notes:
        console.print(f"[dim]{escape(notes)}[/dim]")
    if not info.get("supported"):
        reason = str(info.get("reason") or "This installation cannot be updated automatically.")
        console.print(f"[yellow]{escape(reason)}[/yellow]")
        raise typer.Exit(1)
    if kind != "cli":
        console.print(
            "[yellow]This is a desktop installation: open Navin and use Settings > Updates, "
            "or download the new installer from https://navin.live/download[/yellow]"
        )
        raise typer.Exit(1)
    if check:
        console.print("Run [bold]navin update[/bold] to install it.")
        return
    if not yes:
        if not sys.stdin.isatty():
            console.print("Not a terminal: pass --yes to install without a prompt.")
            raise typer.Exit(1)
        if not typer.confirm(f"Install navin {latest} now?", default=True):
            console.print("Update skipped.")
            return

    try:
        _download_with_progress(service)
        result = service.apply_cli_update()
    except service.UpdateError as exc:
        console.print(f"[red]Update failed: {escape(str(exc))}[/red]")
        console.print("[dim]Nothing was changed; the current version keeps working.[/dim]")
        raise typer.Exit(1) from exc

    # The daily "a newer navin exists" hint is stale now; the next start
    # re-reads the manifest instead of announcing the version just installed.
    with suppress(Exception):
        from navin.update.notice import _cache_path

        _cache_path().unlink(missing_ok=True)

    if result.get("deferred"):
        console.print(
            f"[green]navin {latest} is ready.[/green] It is put in place the moment this "
            "command exits; give it a few seconds, then run [bold]navin --version[/bold]."
        )
        return
    console.print(f"[green]Updated to navin {latest}.[/green] New terminals and sessions use it right away.")


@app.command(name="install-cli")
def install_cli(
    force: bool = typer.Option(False, "--force", help="Replace an existing navin command"),
):
    """Make the `navin` command available in your terminal.

    Needed after installing the macOS disk image or a bare Linux binary: both put
    the executable somewhere no shell looks. The Linux packages and the Windows
    installer already do this for you.
    """
    from navin.cli_link import install_cli_link

    result = install_cli_link(force=force)
    colour = "green" if result.created else "yellow"
    console.print(f"[{colour}]{escape(result.message)}[/{colour}]")
    if not result.created and result.path is None and sys.platform != "win32":
        raise typer.Exit(1)


# ============================================================================
# Cache maintenance
# ============================================================================


def _cache_categories() -> list[tuple[str, str, Path, bool]]:
    """(id, description, path, cleared_by_default) for every cache Navin keeps.

    Only regenerable data qualifies. Config, chat threads, sessions, history,
    snapshots and workspaces are user data and never listed here.
    """
    from navin.config.paths import get_data_dir, get_webui_dir

    data = get_data_dir()
    categories = [
        ("index", "Code index (rebuilt on demand per project)", data / "index", True),
        ("logs", "Log files", data / "logs", True),
        ("updates", "Downloaded update artifacts", data / "updates", True),
        ("media", "Chat attachments cache (old chats lose their previews)", data / "media", False),
        (
            "lsp-extensions",
            "Language servers installed from VS Code extensions (reinstall with navin lsp install)",
            data / "lsp-extensions",
            False,
        ),
        ("browser", "Browser profile (logins in the agent browser are lost)", get_webui_dir() / "browser-profile", False),
    ]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            categories.append(
                ("browser", "Browser profile (desktop app)", Path(local) / "Navin" / "browser-profile", False)
            )
    return categories


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if path.is_dir():
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    return total


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _clear_dir_contents(path: Path) -> int:
    """Delete everything inside *path* (kept itself); returns bytes freed."""
    import shutil as _shutil

    freed = 0
    if not path.is_dir():
        return 0
    for entry in path.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                freed += _dir_size_bytes(entry)
                _shutil.rmtree(entry, ignore_errors=True)
            else:
                freed += entry.stat().st_size if entry.is_file() else 0
                entry.unlink(missing_ok=True)
        except OSError:
            # A file held open (live log on Windows) stays; everything else goes.
            continue
    return freed


@app.command(name="cache")
def cache_command(
    clear: bool = typer.Option(False, "--clear", help="Delete the regenerable caches (code index, logs, update downloads)"),
    media: bool = typer.Option(False, "--media", help="With --clear: also delete the chat attachments cache"),
    browser: bool = typer.Option(False, "--browser", help="With --clear: also delete the agent browser profile (logins lost)"),
    all_caches: bool = typer.Option(False, "--all", help="With --clear: delete every cache category"),
):
    """Show what Navin caches and how big it is; --clear empties it.

    Only regenerable data is touched. Configuration, chats, sessions, history
    and project workspaces are never deleted by this command.
    """
    categories = _cache_categories()
    if not clear:
        total = 0
        for cat_id, description, path, default in categories:
            size = _dir_size_bytes(path)
            total += size
            extra = "" if default else f" [dim](--{cat_id})[/dim]"
            console.print(f"  {_human_size(size):>10}  {cat_id:<8} {escape(description)}{extra}")
        console.print(f"\n  {_human_size(total):>10}  total")
        console.print("\n[dim]navin cache --clear  vide les caches régénérables "
                      "(--media / --browser / --all pour élargir)[/dim]")
        return

    selected = {"index", "logs", "updates"}
    if media or all_caches:
        selected.add("media")
    if browser or all_caches:
        selected.add("browser")

    freed = 0
    for cat_id, description, path, _default in categories:
        if cat_id not in selected:
            continue
        size = _clear_dir_contents(path)
        freed += size
        if size:
            console.print(f"  [green]✓[/green] {cat_id:<8} {_human_size(size)} freed")
    console.print(f"[green]Cache cleared: {_human_size(freed)} freed.[/green]")


# ============================================================================
# Embedded interpreter
# ============================================================================


@app.command(
    name="python",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    add_help_option=False,
)
def python_command(ctx: typer.Context):
    """Run a Python script, module or snippet with navin's own libraries.

    ``navin python report.py``, ``navin python -m pytest``, ``navin python -c code``.
    A packaged build has no separate interpreter on disk, so this is how skills,
    the document converters and the agent's own scripts reach python-docx,
    openpyxl, pandas and everything else navin ships with. From source it is the
    same interpreter navin itself runs on.
    """
    import runpy

    args = list(ctx.args)
    if not args:
        console.print("Usage: navin python [-m module | -c code | script.py] [args...]")
        raise typer.Exit(2)

    # sys.argv has to look like a normal interpreter's to the code being run:
    # argparse, __file__ and sys.path[0] are all read from it.
    original_argv = sys.argv[:]
    original_path = sys.path[:]
    try:
        if args[0] == "-c":
            if len(args) < 2:
                console.print("navin python -c needs the code to run")
                raise typer.Exit(2)
            sys.argv = ["-c", *args[2:]]
            sys.path.insert(0, "")
            exec(compile(args[1], "<command>", "exec"), {"__name__": "__main__"})  # noqa: S102
        elif args[0] == "-m":
            if len(args) < 2:
                console.print("navin python -m needs the module to run")
                raise typer.Exit(2)
            sys.argv = [args[1], *args[2:]]
            sys.path.insert(0, "")
            try:
                runpy.run_module(args[1], run_name="__main__", alter_sys=True)
            except ImportError as exc:
                # A packaged build carries the modules navin ships, not the whole
                # standard library: PyInstaller collects what it sees imported.
                # The bare traceback names runpy internals and blames navin.
                console.print(f"[red]{escape(str(exc))}[/red]")
                raise typer.Exit(1) from None
        else:
            script = Path(args[0]).expanduser()
            if not script.is_file():
                console.print(f"[red]No such file: {script}[/red]")
                raise typer.Exit(2)
            sys.argv = [str(script), *args[1:]]
            sys.path.insert(0, str(script.parent.resolve()))
            runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        raise typer.Exit(code if isinstance(code, int) else (0 if code is None else 1)) from None
    finally:
        sys.argv = original_argv
        sys.path[:] = original_path


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
):
    """Show navin status."""
    config_path, loaded = _load_inspection_config(config=config, workspace=workspace)
    workspace_path = loaded.workspace_path

    console.print(f"{__logo__} navin Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(
        f"Workspace: {workspace_path} "
        f"{'[green]✓[/green]' if workspace_path.exists() else '[red]✗[/red]'}"
    )

    if config_path.exists():
        from navin.providers.registry import PROVIDERS

        _model, _preset_tag = _model_display(loaded)
        console.print(f"Model: {_model}{_preset_tag}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(loaded.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

license_app = typer.Typer(help="Manage the navin.live subscription of this device")

if _live_account_enabled():
    app.add_typer(license_app, name="license")


@license_app.command("activate")
def license_activate(
    key: str = typer.Argument(..., help="License key (NAVIN-XXXX-XXXX-XXXX-XXXX)"),
    name: str | None = typer.Option(None, "--name", help="Device name shown in the dashboard"),
):
    """Activate this device with a navin.live license key."""
    from navin.config.loader import load_config
    from navin.license_client import LicenseError, activate, uses_managed_key

    config = load_config()
    try:
        result = activate(config, key, name=name)
    except LicenseError as e:
        console.print(f"[red]Activation failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Could not reach the license server: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Device activated[/green]  plan: [bold]{result.get('plan', '?')}[/bold]")
    if uses_managed_key(config):
        console.print("[green]✓ Managed models ready[/green] [dim](provisioned OpenRouter key installed)[/dim]")
    elif result.get("managedKey"):
        console.print("[yellow]Managed key received but not installed (your own OpenRouter key is kept).[/yellow]")
    else:
        console.print("[dim]No managed models on this plan (BYOK / local models).[/dim]")


@license_app.command("status")
def license_status():
    """Check the subscription attached to this device."""
    from navin.config.loader import load_config
    from navin.license_client import (
        LicenseError,
        plan_price_usd,
        uses_managed_key,
        validate,
    )

    config = load_config()
    if not config.license.activated:
        console.print("[yellow]This device is not activated.[/yellow] Run: navin license activate <KEY>")
        raise typer.Exit(1)
    try:
        result = validate(config)
    except LicenseError as e:
        console.print(f"[red]License check failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Could not reach the license server: {e}[/red]")
        raise typer.Exit(1)

    # On affiche l'offre et son prix public, jamais les plafonds internes.
    plan = str(result.get("plan", "?"))
    price = plan_price_usd(plan)
    price_suffix = f"  [dim]${price}/month[/dim]" if isinstance(price, int) and price > 0 else ""
    console.print(f"[green]✓ Subscription valid[/green]  plan: [bold]{plan}[/bold]{price_suffix}")
    if uses_managed_key(config):
        console.print("  [dim]Model calls billed on the provisioned key.[/dim]")


@license_app.command("deactivate")
def license_deactivate():
    """Forget the license on this device (removes the managed key)."""
    from navin.config.loader import load_config, save_config

    config = load_config()
    if not config.license.activated and not config.license.managed_api_key:
        console.print("[dim]Nothing to deactivate.[/dim]")
        return
    # La clé provisionnée ne part qu'avec la licence ; une clé BYOK reste.
    if config.providers.openrouter.api_key == config.license.managed_api_key:
        config.providers.openrouter.api_key = ""
    config.license.license_key = ""
    config.license.activation_token = ""
    config.license.device = ""
    config.license.plan = ""
    config.license.managed_api_key = ""
    config.license.managed_provider = ""
    save_config(config)
    console.print("[green]✓ License removed from this device.[/green]")


provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, Callable[[], None]] = {}
_LOGOUT_HANDLERS: dict[str, Callable[[], None]] = {}

_PROVIDER_DISPLAY: dict[str, str] = {
    "openai_codex": "OpenAI Codex",
    "github_copilot": "GitHub Copilot",
    "xai_oauth": "Grok (x.ai subscription)",
}

_OAUTH_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai_codex": "openai-codex/gpt-5.6-sol",
    "github_copilot": "github-copilot/gpt-5.4-mini",
    "xai_oauth": "xai-oauth/grok-4.6",
}


def _register_login(name: str):
    """Register an OAuth login handler."""
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


def _register_logout(name: str):
    """Register an OAuth logout handler."""
    def decorator(fn):
        _LOGOUT_HANDLERS[name] = fn
        return fn
    return decorator


def _resolve_oauth_provider(provider: str):
    """Resolve and validate an OAuth provider configuration."""
    from navin.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)
    return spec


def _set_oauth_provider_as_main(
    provider_name: str,
    *,
    model: str | None = None,
    config_path: str | None = None,
) -> None:
    """Persist an OAuth provider as the active agent provider."""
    from navin.config.loader import get_config_path, load_config, save_config, set_config_path

    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else None
    if resolved_config_path is not None and get_config_path() != resolved_config_path:
        set_config_path(resolved_config_path)
        console.print(f"[dim]Using config: {resolved_config_path}[/dim]")

    config = load_config(resolved_config_path)
    selected_model = (model or "").strip() or _OAUTH_PROVIDER_DEFAULT_MODELS[provider_name]
    config.agents.defaults.model_preset = None
    config.agents.defaults.provider = provider_name
    config.agents.defaults.model = selected_model
    save_config(config, resolved_config_path)

    saved_path = resolved_config_path or get_config_path()
    console.print(
        f"[green]✓ Set {provider_name.replace('_', '-')} as the main provider[/green]  "
        f"[dim]{selected_model}[/dim]"
    )
    console.print(f"[dim]Saved: {saved_path}[/dim]")


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
    set_main: bool = typer.Option(
        False,
        "--set-main",
        "--main",
        help="Set this OAuth provider as the active agent provider after login",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model to use when setting this provider as the active provider",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authenticate with an OAuth provider."""
    spec = _resolve_oauth_provider(provider)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    if config:
        from navin.config.loader import set_config_path

        resolved_config_path = Path(config).expanduser().resolve()
        set_config_path(resolved_config_path)
        console.print(f"[dim]Using config: {resolved_config_path}[/dim]")

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()
    if set_main or model:
        _set_oauth_provider_as_main(spec.name, model=model, config_path=config)


@provider_app.command("logout")
def provider_logout(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Log out from an OAuth provider."""
    spec = _resolve_oauth_provider(provider)

    handler = _LOGOUT_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Logout not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Logout - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        from navin.config.loader import load_config, resolve_config_env_vars
        from navin.providers.openai_codex_provider import codex_token_storage

        proxy = None
        try:
            proxy = resolve_config_env_vars(load_config()).providers.openai_codex.proxy or None
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from e
        storage = codex_token_storage()
        token = None
        with suppress(Exception):
            token = get_token(storage=storage, proxy=proxy)
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
                storage=storage,
                proxy=proxy,
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_logout("openai_codex")
def _logout_openai_codex() -> None:
    """Clear local OAuth credentials for OpenAI Codex."""
    try:
        from navin.providers.openai_codex_provider import codex_token_storage
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)

    _delete_oauth_files(codex_token_storage().get_token_path(), _PROVIDER_DISPLAY["openai_codex"])


@_register_logout("xai_oauth")
def _logout_xai_oauth() -> None:
    """Clear local OAuth credentials for Grok (x.ai subscription)."""
    try:
        from navin.providers.xai_oauth_provider import get_storage
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)

    _delete_oauth_files(get_storage().get_token_path(), _PROVIDER_DISPLAY["xai_oauth"])


@_register_logout("github_copilot")
def _logout_github_copilot() -> None:
    """Clear local OAuth credentials for GitHub Copilot."""
    try:
        from navin.providers.github_copilot_provider import get_storage
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)

    storage = get_storage()
    _delete_oauth_files(storage.get_token_path(), _PROVIDER_DISPLAY["github_copilot"])


def _delete_oauth_files(token_path: Path, provider_label: str) -> None:
    """Delete OAuth token and lock files, reporting the result."""
    removed_paths: list[Path] = []
    skipped: list[tuple[Path, OSError]] = []
    for path in (token_path, token_path.with_suffix(".lock")):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            skipped.append((path, exc))
            continue
        removed_paths.append(path)

    if not removed_paths and not skipped:
        console.print(f"[yellow]! No local OAuth credentials found for {provider_label}[/yellow]")
        return

    if removed_paths:
        console.print(f"[green]✓ Logged out from {provider_label}[/green]")
        for path in removed_paths:
            console.print(f"[dim]Removed: {path}[/dim]")
    for path, exc in skipped:
        console.print(f"[yellow]! Could not remove {path}: {exc}[/yellow]")


@_register_login("xai_oauth")
def _login_xai_oauth() -> None:
    try:
        from navin.providers.xai_oauth_provider import login_xai_oauth

        console.print("[cyan]Starting Grok (x.ai) device flow...[/cyan]\n")
        token = login_xai_oauth(
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
        account = token.account_id or "xAI"
        console.print(f"[green]✓ Authenticated with Grok (x.ai subscription)[/green]  [dim]{account}[/dim]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    try:
        from navin.providers.github_copilot_provider import login_github_copilot

        console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
        token = login_github_copilot(
            print_fn=lambda s: console.print(s),
            prompt_fn=lambda s: typer.prompt(s),
        )
        account = token.account_id or "GitHub"
        console.print(f"[green]✓ Authenticated with GitHub Copilot[/green]  [dim]{account}[/dim]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


def _known_cli_names() -> set[str]:
    """Names of registered commands and sub-apps (e.g. webui, gateway)."""
    names: set[str] = set()
    for command in app.registered_commands:
        name = command.name or (command.callback.__name__ if command.callback else "")
        if name:
            names.add(name.replace("_", "-"))
            names.add(name)
    for group in app.registered_groups:
        if group.name:
            names.add(group.name)
    return names


def _desktop_shell_binary() -> Path | None:
    """The Tauri desktop app bundled near this executable, if any.

    In the installed apps the CLI sidecar is either next to the desktop shell
    (``navin-desktop``, one-file layouts) or one level below it in the
    ``navin-dist`` resource directory (one-dir layouts). Source checkouts and
    bare-binary installs have no shell, so ``navin <dir>`` falls back to the
    browser WebUI there.
    """
    if not getattr(sys, "frozen", False):
        return None
    name = "navin-desktop.exe" if sys.platform == "win32" else "navin-desktop"
    here = Path(sys.executable).resolve().parent
    candidates = [here, here.parent]
    if sys.platform == "darwin":
        # Contents/Resources/navin-dist/navin -> Contents/MacOS/navin-desktop
        candidates.append(here.parent.parent / "MacOS")
    for directory in candidates:
        shell = directory / name
        if shell.is_file():
            return shell
    return None


def _launch_desktop_shell(shell: Path, project: str) -> None:
    """Start the desktop app on *project*, detached from this console."""
    import subprocess

    from navin.utils.proc import detached_no_window_kwargs

    subprocess.Popen(
        [str(shell), project],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached_no_window_kwargs(),
    )


def _rewrite_project_dir_argv(argv: list[str]) -> list[str]:
    """Support ``navin .`` / ``navin <dir>``: open Navin on that project.

    Mirrors ``cursor .`` / ``code .``: when the first argument is an existing
    directory (and not a CLI command name), rewrite the invocation to
    ``navin webui --background --yes --project <dir>``.
    """
    if len(argv) < 2:
        return argv
    candidate = argv[1]
    if candidate.startswith("-") or candidate in _known_cli_names():
        return argv
    path = Path(candidate).expanduser()
    if not path.is_dir():
        return argv
    resolved = str(path.resolve())
    return [argv[0], "webui", "--background", "--yes", "--project", resolved, *argv[2:]]


def run() -> None:
    """Console entry point (``navin``) with `navin <dir>` support."""
    argv = _rewrite_project_dir_argv(sys.argv)
    if argv is not sys.argv:
        # `navin <dir>` was recognized. When the desktop app is installed,
        # open a window on that project (what `cursor .` does) instead of the
        # background browser WebUI.
        project = argv[argv.index("--project") + 1]
        shell = _desktop_shell_binary()
        if shell is not None:
            _launch_desktop_shell(shell, project)
            return
    sys.argv[:] = argv
    app()


if __name__ == "__main__":
    run()
