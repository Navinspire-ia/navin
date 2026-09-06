"""Phase 6 unified Resume seed: git + decisions + tasks + handoff + decisions append."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.continuity.resume_seed import (
    append_decisions_from_brief,
    build_resume_seed,
    write_leave_handoff,
)
from navin.project_scaffold import ensure_navin_project_pack
from navin.security.workspace_access import default_workspace_scope
from navin.utils.helpers import ensure_project_scaffold
from navin.webui.continuity_api import resume_seed_payload


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(root), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    )


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class ResumeSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        # Repo first - project scaffold may initialise its own git-backed store.
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "t@t")
        _git(self.root, "config", "user.name", "t")
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        _git(self.root, "add", "-f", "a.txt")
        _git(self.root, "commit", "-m", "init")
        (self.root / "a.txt").write_text("two\n", encoding="utf-8")

        ensure_project_scaffold(self.root, silent=True)
        ensure_navin_project_pack(self.root)
        (self.root / ".navin" / "memory").mkdir(parents=True, exist_ok=True)
        (self.root / ".navin" / "memory" / "MEMORY.md").write_text(
            "# Memory\n\n## Constraints\n\n- Keep public APIs stable\n\n## Notes\n",
            encoding="utf-8",
        )
        (self.root / ".navin" / "continuity" / "RESUME.md").write_text(
            "# Resume\n\n## Where we left off\n\n"
            "Finishing the exporter. Next: run the smoke tests and ship.\n",
            encoding="utf-8",
        )
        (self.root / ".navin" / "continuity" / "DECISIONS.md").write_text(
            "# Decisions\n\n"
            "### 2026-08-01 - Prefer apply_patch over edit_file\n\n"
            "- Status: active\n",
            encoding="utf-8",
        )
        from navin.board.store import ProjectBoardStore

        store = ProjectBoardStore(self.root)
        store.create_task(
            title="Ship exporter smoke",
            description="finish and verify",
            status="in_progress",
            actor="test",
            actor_type="human",
        )

    def test_seed_includes_git_decisions_tasks_constraints(self) -> None:
        payload = build_resume_seed(self.root, project_name="Exporter")
        seed = payload["seed"]
        self.assertIn("Resume work on Exporter", seed)
        self.assertIn("Finishing the exporter", seed)
        self.assertIn("Prefer apply_patch", seed)
        self.assertIn("Keep public APIs stable", seed)
        self.assertIn("Ship exporter smoke", seed)
        self.assertTrue(any("Git:" in line or "branch" in line for line in payload["git"]) or "Git:" in seed)
        self.assertTrue(payload["actionable"])

    def test_api_payload_matches(self) -> None:
        scope = default_workspace_scope(self.root, True)
        payload = resume_seed_payload(scope)  # type: ignore[arg-type]
        self.assertIn("seed", payload)
        self.assertIn("Ship exporter smoke", payload["seed"])

    def test_leave_handoff_writes_auto_block(self) -> None:
        result = write_leave_handoff(
            self.root,
            body="Goal: ship. Done: a.txt. Next: push.",
            session_key="websocket:test",
        )
        self.assertTrue(result["saved"])
        text = (self.root / ".navin" / "continuity" / "RESUME.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("navin:auto-handoff:start", text)
        self.assertIn("Goal: ship", text)

    def test_append_decisions_from_brief(self) -> None:
        brief = (
            "Goal: harden resume\n"
            "Done: resume_seed.py\n"
            "Decisions:\n"
            "- Store head_sha on task PR open\n"
            "- Keep a single continuity brain\n"
            "Next: write tests\n"
        )
        added = append_decisions_from_brief(self.root, brief)
        self.assertEqual(len(added), 2)
        text = (self.root / ".navin" / "continuity" / "DECISIONS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Store head_sha on task PR open", text)
        # Idempotent: second pass should not duplicate.
        self.assertEqual(append_decisions_from_brief(self.root, brief), [])


if __name__ == "__main__":
    unittest.main()
