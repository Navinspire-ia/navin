# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The children an event loop started are known by tag, never found by a heap walk."""

from __future__ import annotations

import asyncio
import signal
import sys
import unittest

from navin.utils import child_processes


@unittest.skipIf(sys.platform == "win32", "POSIX signals and process groups")
class ChildRegistryTests(unittest.TestCase):
    def test_children_are_listed_by_tag_and_only_the_tagged_ones_are_signalled(self) -> None:
        loop = asyncio.new_event_loop()
        registry = child_processes.install(loop)
        self.assertIs(child_processes.install(loop), registry)  # once per loop
        self.assertIs(child_processes.registry_of(loop), registry)

        async def main() -> None:
            with child_processes.tagged("mcp:time"):
                server = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
            # a child started outside any tag, sharing our process group
            tool = await asyncio.create_subprocess_exec("sleep", "30")
            self.assertEqual(len(registry), 2)
            self.assertEqual({t.get_pid() for t in registry.transports()}, {server.pid, tool.pid})
            self.assertEqual([t.get_pid() for t in registry.transports("mcp:")], [server.pid])
            self.assertEqual(child_processes.terminate(loop, "mcp:"), 1)
            await asyncio.wait_for(server.wait(), 2.0)
            self.assertEqual(server.returncode, -signal.SIGTERM)
            self.assertIsNone(tool.returncode)
            # gone from the live list, and a second round finds nothing to do
            self.assertEqual([t.get_pid() for t in registry.transports("mcp:")], [])
            self.assertEqual(child_processes.terminate(loop, "mcp:"), 0)
            tool.kill()
            await tool.wait()

        try:
            loop.run_until_complete(main())
        finally:
            loop.close()

    def test_a_loop_without_registry_has_nothing_to_terminate(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            self.assertIsNone(child_processes.registry_of(loop))
            self.assertEqual(child_processes.terminate(loop, "mcp:"), 0)
        finally:
            loop.close()

    def test_signalling_a_gone_pid_is_reported_not_raised(self) -> None:
        self.assertFalse(child_processes.signal_tree(0))
        self.assertFalse(child_processes.signal_tree(2**22 - 1))  # above pid_max on Linux


if __name__ == "__main__":
    unittest.main()
