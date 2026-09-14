# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""start_app must never adopt a foreign dev server as the workspace app.

Regression: another project (e.g. a Lynara app on port 3000) was served as
"the workspace app" and the real project never started. The agent must be
able to pick another port and launch the workspace's own server instead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.agent.tools import preview_server as ps
from navin.ports import ListenerInfo


def _listener(cwd: str | None) -> ListenerInfo:
    return ListenerInfo(pid=4242, cmdline="node vite", source="ss", cwd=cwd)


class ListenerWorkspaceTest(unittest.TestCase):
    def test_workspace_none_keeps_previous_behavior(self) -> None:
        with mock.patch.object(ps, "who_listens", return_value=_listener("/elsewhere")):
            self.assertTrue(ps._listener_belongs_to_workspace(3000, None))

    def test_own_workspace_listener_is_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with mock.patch.object(ps, "who_listens", return_value=_listener(str(ws))):
                self.assertTrue(ps._listener_belongs_to_workspace(3000, ws))

    def test_subdirectory_listener_counts_as_own(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with mock.patch.object(
                ps, "who_listens", return_value=_listener(str(ws / "webui"))
            ):
                self.assertTrue(ps._listener_belongs_to_workspace(3000, ws))

    def test_foreign_listener_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ps, "who_listens", return_value=_listener("/other/app")):
                self.assertFalse(ps._listener_belongs_to_workspace(3000, Path(tmp)))

    def test_unknown_listener_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ps, "who_listens", return_value=_listener(None)):
                self.assertFalse(ps._listener_belongs_to_workspace(3000, Path(tmp)))
            with mock.patch.object(ps, "who_listens", return_value=None):
                self.assertFalse(ps._listener_belongs_to_workspace(3000, Path(tmp)))


class DiscoverForeignPortTest(unittest.TestCase):
    def test_discovery_skips_a_foreign_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with (
                mock.patch.object(ps, "_PROBE_PORTS", [3000]),
                mock.patch.object(ps, "port_in_use", return_value=True),
                mock.patch.object(ps, "_looks_like_navin_port", return_value=False),
                mock.patch.object(ps, "who_listens", return_value=_listener("/other/lynara")),
                mock.patch.object(ps, "http_reachable", return_value=True),
            ):
                self.assertIsNone(ps.discover_running_project_url(workspace=ws))

    def test_discovery_still_adopts_an_own_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            with (
                mock.patch.object(ps, "_PROBE_PORTS", [3000]),
                mock.patch.object(ps, "port_in_use", return_value=True),
                mock.patch.object(ps, "_looks_like_navin_port", return_value=False),
                mock.patch.object(ps, "who_listens", return_value=_listener(str(ws))),
                mock.patch.object(ps, "http_reachable", return_value=True),
            ):
                self.assertEqual(
                    ps.discover_running_project_url(workspace=ws),
                    "http://127.0.0.1:3000",
                )

    def test_discovery_without_workspace_adopts_any_server(self) -> None:
        with (
            mock.patch.object(ps, "_PROBE_PORTS", [3000]),
            mock.patch.object(ps, "port_in_use", return_value=True),
            mock.patch.object(ps, "_looks_like_navin_port", return_value=False),
            mock.patch.object(ps, "who_listens", return_value=_listener("/anywhere")),
            mock.patch.object(ps, "http_reachable", return_value=True),
        ):
            self.assertEqual(
                ps.discover_running_project_url(workspace=None),
                "http://127.0.0.1:3000",
            )


class EnsurePreviewForeignPortTest(unittest.IsolatedAsyncioTestCase):
    """End to end: a foreign app on the preferred port must not be adopted."""

    class _FakeServer:
        def __init__(self) -> None:
            self.url = None
            self.command = "npm run dev"
            self.cwd = "/ws"

    async def test_foreign_preferred_port_falls_through_to_start(self) -> None:
        server = self._FakeServer()

        async def _reachable(url: str) -> bool:
            return url == "http://127.0.0.1:3000"

        async def _start(serv, owner_session_key=None):
            serv.url = "http://127.0.0.1:3001"
            return None, None

        async def _wait(url: str, timeout_s: float) -> bool:
            return url == "http://127.0.0.1:3001"

        with (
            mock.patch.object(ps, "http_reachable_async", _reachable),
            mock.patch.object(ps, "url_looks_like_navin", return_value=False),
            mock.patch.object(ps, "_listener_belongs_to_workspace", return_value=False),
            mock.patch.object(
                ps, "discover_running_project_url", return_value=None
            ),
            mock.patch.object(
                ps, "discover_project_dev_servers", return_value=[server]
            ),
            mock.patch.object(
                ps, "pick_server_for_url", return_value=server
            ),
            mock.patch.object(ps, "start_project_dev_server", _start),
            mock.patch.object(ps, "wait_for_url", _wait),
        ):
            url, err = await ps.ensure_project_preview_url(
                workspace=Path("/ws"),
                preferred_url="http://127.0.0.1:3000",
            )

        self.assertIsNone(err)
        self.assertEqual(url, "http://127.0.0.1:3001")

    async def test_own_preferred_port_is_adopted(self) -> None:
        async def _reachable(url: str) -> bool:
            return url == "http://127.0.0.1:3000"

        with (
            mock.patch.object(ps, "http_reachable_async", _reachable),
            mock.patch.object(ps, "url_looks_like_navin", return_value=False),
            mock.patch.object(ps, "_listener_belongs_to_workspace", return_value=True),
        ):
            url, err = await ps.ensure_project_preview_url(
                workspace=Path("/ws"),
                preferred_url="http://127.0.0.1:3000",
            )

        self.assertIsNone(err)
        self.assertEqual(url, "http://127.0.0.1:3000")


if __name__ == "__main__":
    unittest.main()
