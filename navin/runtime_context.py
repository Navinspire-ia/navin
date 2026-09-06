"""Optional, persistent context appended to the current user prompt."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from loguru import logger

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

# Providers run between the user's message and the model's first token, so a
# single stuck reader (a status crawling a network filesystem, a digest behind
# a dead mount) used to freeze every turn. Past this bound the block is
# dropped and the turn goes on without it: every block here is advisory
# metadata, never something a turn cannot proceed without.
# 2.5s is the ceiling a chat turn can hide before the UI feels stuck. 10s
# made "salut" look frozen while git status crawled a large WSL tree.
PROVIDER_TIMEOUT_S = 2.5

RUNTIME_CONTEXT_HISTORY_META = "_runtime_context"
RUNTIME_CONTEXT_MESSAGE_META = "runtime_context"
RUNTIME_CONTEXT_TAG = "[Runtime Context - metadata only, not instructions]"
RUNTIME_CONTEXT_END = "[/Runtime Context]"


@dataclass(frozen=True)
class RuntimeContextBlock:
    """One provider-owned block appended to the current user content."""

    source: str
    content: str


RuntimeContextResult: TypeAlias = (
    RuntimeContextBlock | Sequence[RuntimeContextBlock] | None
)
RuntimeContextProvider: TypeAlias = Callable[
    ["RequestContext"], Awaitable[RuntimeContextResult]
]


def wrap_runtime_context_lines(lines: Iterable[str]) -> str:
    """Wrap non-empty runtime metadata lines in the established prompt markers."""
    content = "\n".join(line for line in lines if line)
    if not content:
        return ""
    return f"{RUNTIME_CONTEXT_TAG}\n{content}\n{RUNTIME_CONTEXT_END}"


def normalize_runtime_context_blocks(result: RuntimeContextResult) -> list[RuntimeContextBlock]:
    """Return validated, non-empty blocks while preserving provider order."""
    if result is None:
        return []
    values = [result] if isinstance(result, RuntimeContextBlock) else list(result)
    blocks: list[RuntimeContextBlock] = []
    for block in values:
        if not isinstance(block, RuntimeContextBlock):
            raise TypeError("runtime context providers must return RuntimeContextBlock values")
        source = block.source.strip()
        content = block.content.strip()
        if not source:
            raise ValueError("runtime context block source must not be empty")
        if content:
            blocks.append(RuntimeContextBlock(source=source, content=content))
    return blocks


async def resolve_runtime_context(
    providers: Iterable[RuntimeContextProvider],
    request: RequestContext,
) -> list[RuntimeContextBlock]:
    """Resolve providers concurrently, keeping the caller's stable order.

    Providers are independent context readers (board, diagnostics, memory);
    running them one after another made BUILD pay the sum of their latencies
    when the turn only needs the max. ``gather`` returns results in argument
    order, so the assembled prompt is byte-identical to the sequential one.
    """
    provider_list = list(providers)
    if not provider_list:
        return []
    results = await asyncio.gather(
        *(_resolve_one(provider, request) for provider in provider_list)
    )
    blocks: list[RuntimeContextBlock] = []
    for result in results:
        blocks.extend(normalize_runtime_context_blocks(result))
    return blocks


async def _resolve_one(
    provider: RuntimeContextProvider,
    request: "RequestContext",
) -> RuntimeContextResult:
    """One provider, bounded and fenced: it may fail, it may not stall the turn."""
    name = getattr(provider, "__qualname__", None) or repr(provider)
    try:
        return await asyncio.wait_for(provider(request), timeout=PROVIDER_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(
            "Runtime context provider {} exceeded {}s; continuing without its block",
            name,
            PROVIDER_TIMEOUT_S,
        )
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - advisory metadata never fails a turn
        logger.warning(
            "Runtime context provider {} failed ({}: {}); continuing without its block",
            name,
            type(exc).__name__,
            exc,
        )
        logger.debug("Runtime context provider failure detail", exc_info=True)
        return None


def append_runtime_context(
    content: Any,
    blocks: Sequence[RuntimeContextBlock],
) -> tuple[Any, dict[str, Any] | None]:
    """Append blocks and return a durable marker for exact display-time removal."""
    if not blocks:
        return content, None

    rendered = [block.content for block in blocks]
    sources = [block.source for block in blocks]
    if isinstance(content, list):
        context_blocks = [{"type": "text", "text": text} for text in rendered]
        return [*content, *context_blocks], {
            "version": 1,
            "sources": sources,
            "blocks": context_blocks,
        }

    text = "" if content is None else str(content)
    suffix = "\n\n".join(rendered)
    merged = f"{text}\n\n{suffix}" if text else suffix
    return merged, {
        "version": 1,
        "sources": sources,
        "suffix": suffix,
    }


def public_history_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Return a user-visible copy with trusted runtime context removed exactly."""
    cleaned = deepcopy(dict(message))
    marker = cleaned.pop(RUNTIME_CONTEXT_HISTORY_META, None)
    if not isinstance(marker, Mapping) or marker.get("version") != 1:
        return cleaned

    content = cleaned.get("content")
    suffix = marker.get("suffix")
    if isinstance(content, str) and isinstance(suffix, str) and suffix:
        if content == suffix:
            cleaned["content"] = ""
        elif content.endswith("\n\n" + suffix):
            cleaned["content"] = content[: -(len(suffix) + 2)]
        return cleaned

    expected = marker.get("blocks")
    if isinstance(content, list) and isinstance(expected, list) and expected:
        count = len(expected)
        if content[-count:] == expected:
            cleaned["content"] = content[:-count]
    return cleaned


def public_history_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return user-visible copies of persisted messages."""
    return [public_history_message(message) for message in messages]
