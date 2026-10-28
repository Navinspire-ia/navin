# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The child processes an event loop started, known without walking the heap.

asyncio keeps no list of the subprocess transports a loop made. Finding them
at exit meant ``gc.get_objects()`` plus an ``isinstance`` on every object of
the process: 200 to 400 ms on a CLI with a long chat loaded, and the attribute
reads woke the lazy modules of the OpenAI SDK (``openai.beta``, ``.audio``,
``.evals``...), another 300 ms of imports, at the moment the user wanted the
shell back. :func:`install` wraps the loop's transport factory once, so the
children are a short list, each with the tag the code that started it chose
(an MCP server, a terminal...). :func:`terminate` signals the ones carrying a
tag, their whole process group, when their owner is going away anyway.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import signal
import sys
import weakref
from collections.abc import Iterator
from typing import Any

_TAG: contextvars.ContextVar[str] = contextvars.ContextVar("navin_child_tag", default="")
_ATTRIBUTE = "_navin_children"


class ChildRegistry:
    """Live subprocess transports of one loop, with the tag each was started under."""

    def __init__(self) -> None:
        self._tags: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()

    def add(self, transport: Any, tag: str = "") -> None:
        self._tags[transport] = tag

    def transports(self, tag_prefix: str | None = None) -> list[Any]:
        """Transports not closed yet; with ``tag_prefix``, only the ones tagged so."""
        return [
            transport
            for transport, tag in list(self._tags.items())
            if not transport.is_closing() and (tag_prefix is None or tag.startswith(tag_prefix))
        ]

    def __len__(self) -> int:
        return len(self._tags)


def install(loop: asyncio.AbstractEventLoop) -> ChildRegistry:
    """Record every child ``loop`` starts from now on; idempotent per loop."""
    registry = getattr(loop, _ATTRIBUTE, None)
    if registry is not None:
        return registry
    registry = ChildRegistry()
    original = getattr(loop, "_make_subprocess_transport", None)
    if original is not None:

        async def tracked(*args: Any, **kwargs: Any) -> Any:
            transport = await original(*args, **kwargs)
            registry.add(transport, _TAG.get())
            return transport

        loop._make_subprocess_transport = tracked
    setattr(loop, _ATTRIBUTE, registry)
    return registry


def registry_of(loop: asyncio.AbstractEventLoop) -> ChildRegistry | None:
    """The registry :func:`install` put on ``loop``, None when it never was."""
    return getattr(loop, _ATTRIBUTE, None)


@contextlib.contextmanager
def tagged(tag: str) -> Iterator[None]:
    """Children started inside this block (same task) carry ``tag``."""
    token = _TAG.set(tag)
    try:
        yield
    finally:
        _TAG.reset(token)


def signal_tree(pid: int, sig: int = signal.SIGTERM) -> bool:
    """Send ``sig`` to a child and what it started; False when it was gone.

    A child leading its own session (``start_new_session=True``) gets the
    signal as a group; one sharing our group gets it alone, since signalling
    our own group would hit navin itself. Windows has no signals: the tree is
    killed outright.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        from navin.utils.proc import kill_windows_process_tree

        kill_windows_process_tree(pid)
        return True
    try:
        group = os.getpgid(pid)
        if group != os.getpgid(0):
            os.killpg(group, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def terminate(loop: asyncio.AbstractEventLoop, tag_prefix: str, sig: int = signal.SIGTERM) -> int:
    """Signal the running children of ``loop`` tagged ``tag_prefix``; returns how many."""
    registry = registry_of(loop)
    if registry is None:
        return 0
    count = 0
    for transport in registry.transports(tag_prefix):
        pid = transport.get_pid()
        if pid and transport.get_returncode() is None and signal_tree(pid, sig):
            count += 1
    return count
