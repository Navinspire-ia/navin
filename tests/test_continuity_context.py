"""Continuity runtime context for long-running projects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.continuity.context import continuity_digest
from navin.project_scaffold import ensure_navin_project_pack
from navin.utils.helpers import ensure_project_scaffold


class ContinuityDigestTests(unittest.TestCase):
    def test_blank_scaffold_resume_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fresh"
            root.mkdir()
            ensure_project_scaffold(root, silent=True)
            # Template-only resume must not spam every turn.
            self.assertEqual(continuity_digest(root), [])

    def test_resume_constraints_and_decisions_are_injected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "heavy"
            root.mkdir()
            ensure_navin_project_pack(root)
            (root / "memory").mkdir(parents=True, exist_ok=True)
            (root / "memory" / "MEMORY.md").write_text(
                "# Memory\n\n## Constraints\n\n"
                "- Never rewrite public APIs without a migration\n"
                "- Keep billing and license untouched without review\n\n"
                "## Notes\nok\n",
                encoding="utf-8",
            )
            (root / ".navin" / "continuity" / "RESUME.md").write_text(
                "# Resume brief\n\n"
                "## Where we left off\n\n"
                "Stabilizing the auth gateway after the SSO migration. "
                "Next: finish org RLS and ship the audit export.\n\n"
                "## Hot paths\n\n"
                "navin/webui/workspaces.py, site/supabase/\n",
                encoding="utf-8",
            )
            (root / ".navin" / "continuity" / "DECISIONS.md").write_text(
                "# Decisions\n\n"
                "### 2026-08-01 - Gateway-local Vite for UI iteration\n\n"
                "- Status: active\n"
                "- Why: avoid desktop rebuilds\n\n"
                "### 2026-07-12 - Board lives in .navin/board\n\n"
                "- Status: active\n",
                encoding="utf-8",
            )
            lines = continuity_digest(root)
            blob = "\n".join(lines)
            self.assertIn("Project resume", blob)
            self.assertIn("auth gateway", blob)
            self.assertIn("Durable decisions:", blob)
            self.assertIn("Gateway-local Vite", blob)
            self.assertIn("Hard constraints", blob)
            self.assertIn("Never rewrite public APIs", blob)

    def test_auto_handoff_block_is_injected_even_on_template_resume(self) -> None:
        """The Consolidator mirror must surface even when the manual part of
        RESUME.md is still the untouched scaffold template."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "auto"
            root.mkdir()
            ensure_project_scaffold(root, silent=True)
            resume = root / ".navin" / "continuity" / "RESUME.md"
            existing = resume.read_text(encoding="utf-8")
            resume.write_text(
                existing
                + "\n<!-- navin:auto-handoff:start -->\n"
                + "## Last auto handoff (2026-08-04 07:30, session websocket:x)\n\n"
                + "Goal: ship the exporter. Done: navin/export.py. "
                + "Next: run pytest tests/test_export.py.\n"
                + "<!-- navin:auto-handoff:end -->\n",
                encoding="utf-8",
            )
            blob = "\n".join(continuity_digest(root))
            self.assertIn("Project resume", blob)
            self.assertIn("ship the exporter", blob)
            # The template placeholders themselves must still stay out.
            self.assertNotIn("(State, open risks, next concrete action)", blob)


if __name__ == "__main__":
    unittest.main()
