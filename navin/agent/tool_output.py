"""Live tool-output streaming for long-running tools (builds, installs...).

The progress hook binds, per turn, an async *emitter* and, per tool call, the
call metadata (call id, name, arguments) via contextvars. Tools that produce
output incrementally (currently :class:`navin.agent.tools.shell.ExecTool`)
call :func:`emit_tool_output` with their cumulative output tail; the emitter
forwards it as a ``phase="output"`` tool event that channels merge into the
same activity card as the ``start``/``end`` events.

Contextvars propagate through ``await`` within one task and are copied into
tasks spawned by ``asyncio.gather``/``create_task``, so parallel tool calls
each see their own call metadata.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from loguru import logger

ToolOutputEmitter = Callable[[dict[str, Any]], Awaitable[None]]

_EMITTER: ContextVar[ToolOutputEmitter | None] = ContextVar(
    "navin_tool_output_emitter",
    default=None,
)
_CALL_META: ContextVar[dict[str, Any] | None] = ContextVar(
    "navin_tool_output_call_meta",
    default=None,
)


def bind_tool_output_emitter(emitter: ToolOutputEmitter | None) -> None:
    """Install the per-turn emitter (progress hook, before tool execution)."""
    _EMITTER.set(emitter)


def bind_tool_call_meta(call_id: str, name: str, arguments: dict[str, Any]) -> None:
    """Record which tool call is about to execute in the current task."""
    _CALL_META.set({"call_id": call_id, "name": name, "arguments": arguments})


def tool_output_streaming_enabled() -> bool:
    return _EMITTER.get() is not None and _CALL_META.get() is not None


async def emit_tool_output(
    output: str,
    *,
    percent: float | int | None = None,
    eta_s: float | int | None = None,
    label: str | None = None,
    indeterminate: bool | None = None,
) -> None:
    """Push the cumulative output tail for the current tool call, if bound.

    Best-effort: emission failures are logged and swallowed so a UI hiccup
    can never break the tool itself. Optional progress fields ride on the
    same ``phase="output"`` event so ShellRunCard can fill a bar.
    """
    emitter = _EMITTER.get()
    meta = _CALL_META.get()
    if emitter is None or meta is None or not output:
        return
    payload: dict[str, Any] = {
        "version": 1,
        "phase": "output",
        "call_id": meta["call_id"],
        "name": meta["name"],
        "arguments": meta["arguments"],
        "output": output,
        "result": None,
        "error": None,
        "files": [],
        "embeds": [],
    }
    if percent is not None:
        try:
            payload["percent"] = max(0.0, min(100.0, float(percent)))
            payload["indeterminate"] = False
        except (TypeError, ValueError):
            pass
    elif indeterminate is not None:
        payload["indeterminate"] = bool(indeterminate)
    if eta_s is not None:
        try:
            payload["eta_s"] = max(0.0, float(eta_s))
        except (TypeError, ValueError):
            pass
    if label:
        payload["label"] = label
    try:
        await emitter(payload)
    except Exception:
        logger.debug("tool output emission failed", exc_info=True)


async def emit_tool_meta(**fields: Any) -> None:
    """Attach structured facts to the current tool call's activity card.

    Same ``phase="output"`` channel as :func:`emit_tool_output`, without any
    output text: the WebUI merges the fields into the call's event (by
    ``call_id``) and later ``end``/``error`` frames keep them. Used by exec to
    say whether a command runs inside the OS sandbox before it even starts.
    ``None`` values are dropped so a caller can pass optional facts as is.
    """
    emitter = _EMITTER.get()
    meta = _CALL_META.get()
    facts = {key: value for key, value in fields.items() if value is not None}
    if emitter is None or meta is None or not facts:
        return
    payload: dict[str, Any] = {
        "version": 1,
        "phase": "output",
        "call_id": meta["call_id"],
        "name": meta["name"],
        "arguments": meta["arguments"],
        "result": None,
        "error": None,
        "files": [],
        "embeds": [],
        **facts,
    }
    try:
        await emitter(payload)
    except Exception:
        logger.debug("tool meta emission failed", exc_info=True)


def current_tool_call_id() -> str | None:
    meta = _CALL_META.get()
    if not meta:
        return None
    call_id = meta.get("call_id")
    return str(call_id) if call_id else None
