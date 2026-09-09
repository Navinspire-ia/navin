# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Constraint drift detector: MEMORY.md constraints vs git dirty / last commit."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.webui.project_brain import (
    constraint_drift_payload,
    detect_constraint_drift,
    project_brain_payload,
)


class _Scope:
    def __init__(self, path: Path):
        self.project_path = path
        self.project_name = path.name


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


class DetectConstraintDriftUnitTests(unittest.TestCase):
    def test_billing_path_matches_billing_constraint(self) -> None:
        violations = detect_constraint_drift(
            ["Do not touch billing"],
            paths=["src/billing/invoice.py", "README.md"],
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["severity"], "warning")
        self.assertIn("src/billing/invoice.py", violations[0]["paths"])
        self.assertIn("billing", violations[0]["tokens"])

    def test_unrelated_paths_do_not_match(self) -> None:
        violations = detect_constraint_drift(
            ["Do not touch billing"],
            paths=["src/auth/login.py", "docs/readme.md"],
        )
        self.assertEqual(violations, [])

    def test_commit_message_can_flag_drift(self) -> None:
        violations = detect_constraint_drift(
            ["Keep payments immutable"],
            paths=[],
            commit_message="refactor payments refund flow",
        )
        self.assertEqual(len(violations), 1)
        self.assertTrue(violations[0]["in_commit_message"])

    def test_never_raises_on_empty_input(self) -> None:
        self.assertEqual(detect_constraint_drift([], paths=None), [])
        self.assertEqual(detect_constraint_drift(["???"], paths=[""]), [])


class ConstraintDriftGitIntegrationTests(unittest.TestCase):
    def test_dirty_file_flags_soft_warning_in_brain_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            root.mkdir()
            _git(root, "init")
            _git(root, "config", "user.email", "t@example.com")
            _git(root, "config", "user.name", "Test")
            (root / "README.md").write_text("hi\n", encoding="utf-8")
            _git(root, "add", "README.md")
            _git(root, "commit", "-m", "init")

            memory = root / "memory" / "MEMORY.md"
            memory.parent.mkdir(parents=True)
            memory.write_text(
                "# Memory\n\n## Constraints\n\n- Do not touch billing\n\n## Notes\n\nx\n",
                encoding="utf-8",
            )
            billing = root / "src" / "billing" / "charge.py"
            billing.parent.mkdir(parents=True)
            billing.write_text("print('x')\n", encoding="utf-8")

            drift = constraint_drift_payload(
                root, ["Do not touch billing"]
            )
            self.assertTrue(drift["supported"])
            self.assertEqual(drift["source"], "dirty")
            self.assertTrue(drift["violations"])
            self.assertIn("billing", drift["violations"][0]["detail"].lower())

            payload = project_brain_payload(_Scope(root))  # type: ignore[arg-type]
            self.assertTrue(payload["drift"]["supported"])
            self.assertIsInstance(payload["drift"]["violations"], list)
            self.assertTrue(payload["drift"]["violations"])

    def test_clean_tree_uses_last_commit_without_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clean"
            root.mkdir()
            _git(root, "init")
            _git(root, "config", "user.email", "t@example.com")
            _git(root, "config", "user.name", "Test")
            target = root / "lib" / "billing_utils.py"
            target.parent.mkdir(parents=True)
            target.write_text("x = 1\n", encoding="utf-8")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "add billing helpers")

            drift = constraint_drift_payload(root, ["Do not touch billing"])
            self.assertTrue(drift["supported"])
            self.assertEqual(drift["source"], "last_commit")
            self.assertTrue(drift["violations"])


if __name__ == "__main__":
    unittest.main()
