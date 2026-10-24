# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A project whose port is taken (Navin's own editor on 5173) starts on a free one."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.agent.tools import preview_server as ps


def _vite_project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "package.json").write_text(
        json.dumps({"name": "tt", "scripts": {"dev": "vite"}}), encoding="utf-8",
    )
    return root


class FreePortTest(unittest.TestCase):
    def test_vite_project_knows_its_port_flag(self) -> None:
        server = ps._package_server(_vite_project())
        self.assertEqual((server.port, server.port_flag), (5173, "--port"))

    def test_command_on_port(self) -> None:
        root = Path("/p")
        vite = ps.ProjectDevServer(cwd=root, command="npm run dev", port=5173, label="a", port_flag="--port")
        self.assertEqual(ps._command_on_port(vite, 5174), "npm run dev -- --port 5174 --strictPort")
        pnpm = ps.ProjectDevServer(cwd=root, command="pnpm run dev", port=3000, label="a", port_flag="-p")
        self.assertEqual(ps._command_on_port(pnpm, 3001), "pnpm run dev -p 3001")
        other = ps.ProjectDevServer(cwd=root, command="python app.py", port=8000, label="a")
        self.assertEqual(ps._command_on_port(other, 8001), "python app.py")

    def setUp(self) -> None:
        from navin.instance import NAVIN_DEFAULT_PORTS

        patcher = mock.patch.object(ps, "_navin_ports", return_value=frozenset(NAVIN_DEFAULT_PORTS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_free_port_skips_busy_and_navin_ports(self) -> None:
        busy = {5177, 5178}
        with mock.patch.object(ps, "loopback_host", side_effect=lambda port: "127.0.0.1" if port in busy else None):
            self.assertEqual(ps._free_port_after(5173), 5179)
            self.assertEqual(ps._free_port_after(8764), 8769)

    def _start(self, server, taken: set[int]):
        started: dict = {}

        async def fake_start(**kwargs):
            started.update(kwargs)
            return "s1", SimpleNamespace(done=False, exit_code=None, output="")

        manager = SimpleNamespace(start=fake_start)
        with (
            mock.patch.object(ps, "loopback_host", side_effect=lambda port: "127.0.0.1" if port in taken else None),
            mock.patch.object(ps, "_looks_like_navin_port", side_effect=lambda port: port == 5173),
            mock.patch("navin.agent.tools.exec_session.DEFAULT_EXEC_SESSION_MANAGER", manager),
        ):
            result = asyncio.run(ps.start_project_dev_server(server))
        return result, started

    def test_navin_editor_on_5173_moves_the_project(self) -> None:
        (session_id, error, moved), started = self._start(ps._package_server(_vite_project()), {5173})
        self.assertIsNone(error)
        self.assertEqual(session_id, "s1")
        self.assertEqual(moved.port, 5177)
        self.assertEqual(moved.url, "http://127.0.0.1:5177")
        self.assertEqual(started["command"], "npm run dev -- --port 5177 --strictPort")
        self.assertEqual(started["env"]["PORT"], "5177")

    def test_a_free_reserved_port_still_moves_the_project(self) -> None:
        """5173 free here (Navin runs elsewhere, e.g. WSL) must not be handed to the project."""
        (session_id, error, moved), started = self._start(ps._package_server(_vite_project()), set())
        self.assertIsNone(error)
        self.assertEqual(moved.port, 5177)
        self.assertEqual(started["command"], "npm run dev -- --port 5177 --strictPort")

    def test_a_free_ordinary_port_is_kept(self) -> None:
        root = Path(tempfile.mkdtemp())
        server = ps.ProjectDevServer(cwd=root, command="npm run dev", port=5190, label="a", port_flag="--port")
        (_sid, error, kept), started = self._start(server, set())
        self.assertIsNone(error)
        self.assertEqual(kept.port, 5190)
        self.assertEqual(started["command"], "npm run dev")

    def test_a_root_script_delegating_to_a_sub_folder_starts_the_real_app(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "package.json").write_text(
            json.dumps({"name": "ws", "scripts": {"dev": "npm --prefix gestion-projet run dev"}}),
            encoding="utf-8",
        )
        app = root / "gestion-projet"
        app.mkdir()
        (app / "package.json").write_text(
            json.dumps({"name": "gestion-projet", "scripts": {"dev": "vite"}}), encoding="utf-8",
        )
        server = ps._package_server(root)
        self.assertEqual((server.cwd, server.port, server.port_flag), (app.resolve(), 5173, "--port"))
        (_sid, error, moved), started = self._start(server, set())
        self.assertIsNone(error)
        self.assertEqual(started["cwd"], str(app.resolve()))
        self.assertEqual(started["command"], "npm run dev -- --port 5177 --strictPort")

    def test_a_delegation_outside_the_project_is_ignored(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "package.json").write_text(
            json.dumps({"name": "ws", "scripts": {"dev": "cd .. && vite"}}), encoding="utf-8",
        )
        server = ps._package_server(root)
        self.assertEqual(server.cwd, root)


if __name__ == "__main__":
    unittest.main()
