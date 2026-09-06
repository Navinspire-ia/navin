"""Runtime-context providers resolve concurrently but keep a stable order.

The prompt is assembled from these blocks, so their order must not depend on
which provider answers first: a reordering would silently change the prompt
between two identical turns.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from navin.runtime_context import RuntimeContextBlock, resolve_runtime_context


def _run(coro):
    return asyncio.run(coro)


class ResolveRuntimeContextTest(unittest.TestCase):
    def test_slow_first_provider_still_comes_first(self) -> None:
        async def slow(_request):
            await asyncio.sleep(0.05)
            return [RuntimeContextBlock(source="slow", content="first")]

        async def fast(_request):
            return [RuntimeContextBlock(source="fast", content="second")]

        blocks = _run(resolve_runtime_context([slow, fast], request=None))
        self.assertEqual([b.content for b in blocks], ["first", "second"])

    def test_providers_run_concurrently(self) -> None:
        async def sleeper(_request):
            await asyncio.sleep(0.1)
            return []

        async def timed() -> float:
            loop = asyncio.get_running_loop()
            start = loop.time()
            await resolve_runtime_context([sleeper, sleeper, sleeper], request=None)
            return loop.time() - start

        # Three 100ms providers sequentially would take 300ms; concurrently
        # they finish near the slowest one.
        self.assertLess(_run(timed()), 0.25)

    def test_no_providers_is_fine(self) -> None:
        self.assertEqual(_run(resolve_runtime_context([], request=None)), [])


class ProviderFencingTest(unittest.TestCase):
    """A provider is advisory: it may fail or stall, the turn may not.

    A status crawl over a network filesystem once held every first token for
    minutes. The resolver now bounds each provider and drops the block, so no
    future provider can reintroduce that freeze.
    """

    def test_a_stuck_provider_is_dropped_and_the_rest_survive(self) -> None:
        async def stuck(_request):
            await asyncio.sleep(30)
            return [RuntimeContextBlock(source="stuck", content="never")]

        async def healthy(_request):
            return [RuntimeContextBlock(source="healthy", content="kept")]

        with mock.patch("navin.runtime_context.PROVIDER_TIMEOUT_S", 0.05):
            blocks = _run(resolve_runtime_context([stuck, healthy], request=None))
        self.assertEqual([b.content for b in blocks], ["kept"])

    def test_a_crashing_provider_is_dropped_and_the_rest_survive(self) -> None:
        async def crashing(_request):
            raise OSError("mount gone")

        async def healthy(_request):
            return [RuntimeContextBlock(source="healthy", content="kept")]

        blocks = _run(resolve_runtime_context([crashing, healthy], request=None))
        self.assertEqual([b.content for b in blocks], ["kept"])

    def test_a_cancelled_turn_still_propagates(self) -> None:
        started = asyncio.Event()

        async def slow(_request):
            started.set()
            await asyncio.sleep(30)
            return []

        async def cancelling() -> bool:
            task = asyncio.ensure_future(resolve_runtime_context([slow], request=None))
            await started.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return True
            return False

        self.assertTrue(_run(cancelling()))


if __name__ == "__main__":
    unittest.main()
