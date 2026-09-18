# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The handoff brief must only ever be mirrored inside the workspace.

Consolidator._persist_resume_brief used to write wherever
session.metadata["workspace_scope"]["project_path"] pointed. Metadata is
data, not a write target: a corrupted scope must not redirect the write
(audit L1).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.memory import Consolidator, MemoryStore
from navin.session.manager import SessionManager


class ResumeBriefContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "workspace"
        self.root.mkdir()
        self.store = MemoryStore(self.root)
        self.sessions = SessionManager(self.root / "sessions")
        self.consolidator = Consolidator(
            store=self.store,
            sessions=self.sessions,
            build_messages=lambda **_kw: [],
            get_tool_definitions=lambda: [],
        )
        self.session = self.sessions.get_or_create("s-brief")
        # Production keeps the continuity dir alive (RESUME.md is injected
        # every turn); the mirror only updates an existing file.
        (self.root / ".navin" / "continuity").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _resume_path(self, root: Path) -> Path:
        return root / ".navin" / "continuity" / "RESUME.md"

    def test_matching_workspace_gets_the_brief(self) -> None:
        self.session.metadata["workspace_scope"] = {"project_path": str(self.root)}
        self.consolidator._persist_resume_brief(self.session, "- [durable] shipped it")
        text = self._resume_path(self.root).read_text(encoding="utf-8")
        self.assertIn("navin:auto-handoff:start", text)
        self.assertIn("shipped it", text)

    def test_relative_project_path_resolves_to_the_workspace(self) -> None:
        self.session.metadata["workspace_scope"] = {
            "project_path": str(self.root / ".")
        }
        self.consolidator._persist_resume_brief(self.session, "- brief")
        self.assertTrue(self._resume_path(self.root).is_file())

    def test_foreign_project_path_is_refused(self) -> None:
        foreign = Path(self._tmp.name) / "elsewhere"
        foreign.mkdir()
        self.session.metadata["workspace_scope"] = {"project_path": str(foreign)}
        self.consolidator._persist_resume_brief(self.session, "- pwned")
        self.assertFalse(self._resume_path(foreign).exists())

    def test_parent_of_the_workspace_is_refused(self) -> None:
        parent = Path(self._tmp.name)
        self.session.metadata["workspace_scope"] = {"project_path": str(parent)}
        self.consolidator._persist_resume_brief(self.session, "- pwned")
        self.assertFalse(self._resume_path(parent).exists())

    def test_real_second_workspace_is_still_mirrored(self) -> None:
        # Subagents and linked repos legitimately run on another project;
        # a directory that already carries .navin/continuity keeps receiving
        # its own handoff brief.
        second = Path(self._tmp.name) / "second-project"
        (second / ".navin" / "continuity").mkdir(parents=True)
        self.session.metadata["workspace_scope"] = {"project_path": str(second)}
        self.consolidator._persist_resume_brief(self.session, "- second repo brief")
        text = self._resume_path(second).read_text(encoding="utf-8")
        self.assertIn("navin:auto-handoff:start", text)
        self.assertIn("second repo brief", text)


if __name__ == "__main__":
    unittest.main()
