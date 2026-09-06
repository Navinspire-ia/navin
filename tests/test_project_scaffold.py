"""Project scaffold creates durable memory / board / metadata files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from navin.project_scaffold import ensure_navin_project_pack
from navin.utils.helpers import ensure_project_scaffold, sync_workspace_templates
from navin.webui.project_brain import _extract_constraints, project_brain_payload


class _Scope:
    def __init__(self, path: Path):
        self.project_path = path
        self.project_name = path.name


class EnsureProjectScaffoldTests(unittest.TestCase):
    def test_creates_memory_metadata_board_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "acme"
            root.mkdir()
            created = ensure_project_scaffold(root, silent=True)
            self.assertTrue(created)
            # Brain files live inside .navin/: the project root stays clean.
            self.assertTrue((root / ".navin" / "SOUL.md").is_file())
            self.assertTrue((root / ".navin" / "USER.md").is_file())
            self.assertTrue((root / ".navin" / "AGENTS.md").is_file())
            self.assertTrue((root / ".navin" / "memory" / "MEMORY.md").is_file())
            self.assertTrue((root / ".navin" / "memory" / "history.jsonl").is_file())
            self.assertFalse((root / "SOUL.md").exists())
            self.assertFalse((root / "memory").exists())
            self.assertTrue((root / ".navin" / "metadata" / "index.json").is_file())
            self.assertTrue((root / ".navin" / "checkpoints" / ".gitignore").is_file())
            self.assertFalse((root / ".metadata").exists())
            self.assertFalse((root / ".checkpoints").exists())
            self.assertTrue((root / ".navin" / "board" / "board.json").is_file())
            self.assertTrue((root / ".navin" / "board" / "milestones.json").is_file())
            self.assertTrue((root / ".navin" / "project.json").is_file())
            self.assertTrue((root / ".navin" / "agents" / "README.md").is_file())
            self.assertTrue((root / ".navin" / "continuity" / "RESUME.md").is_file())
            self.assertTrue((root / ".navin" / "continuity" / "DECISIONS.md").is_file())
            self.assertTrue((root / ".navin" / "review-rules.json").is_file())
            self.assertTrue((root / ".navin" / "skills").is_dir())
            self.assertFalse((root / ".navin" / "skill").exists())
            self.assertFalse((root / "skills").exists())
            self.assertTrue((root / ".navin" / "prompts" / "README.md").is_file())

            again = ensure_project_scaffold(root, silent=True)
            self.assertEqual(again, [])

    def test_migrates_legacy_root_layout_into_navin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "legacy"
            root.mkdir()
            (root / "SOUL.md").write_text("custom soul\n", encoding="utf-8")
            (root / "HEARTBEAT.md").write_text("## Active tasks\n- x\n", encoding="utf-8")
            legacy_memory = root / "memory"
            legacy_memory.mkdir()
            (legacy_memory / "MEMORY.md").write_text("# Memory\n\nfact\n", encoding="utf-8")
            # A user-owned folder that merely shares the name must stay put.
            (root / "prompts").mkdir()
            (root / "prompts" / "app-prompt.md").write_text("mine\n", encoding="utf-8")

            ensure_project_scaffold(root, silent=True)

            self.assertFalse((root / "SOUL.md").exists())
            self.assertFalse((root / "HEARTBEAT.md").exists())
            self.assertFalse((root / "memory").exists())
            self.assertEqual(
                (root / ".navin" / "SOUL.md").read_text(encoding="utf-8"),
                "custom soul\n",
            )
            self.assertEqual(
                (root / ".navin" / "memory" / "MEMORY.md").read_text(encoding="utf-8"),
                "# Memory\n\nfact\n",
            )
            self.assertIn(
                "Active tasks",
                (root / ".navin" / "HEARTBEAT.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((root / "prompts" / "app-prompt.md").is_file())

    def test_customized_root_agents_md_stays_at_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agents"
            root.mkdir()
            (root / "AGENTS.md").write_text("# My own rules\n", encoding="utf-8")
            ensure_project_scaffold(root, silent=True)
            self.assertTrue((root / "AGENTS.md").is_file())
            self.assertEqual(
                (root / "AGENTS.md").read_text(encoding="utf-8"),
                "# My own rules\n",
            )
            # Navin's own copy is still scaffolded inside .navin/.
            self.assertTrue((root / ".navin" / "AGENTS.md").is_file())

    def test_navin_pack_is_like_cursor_claude_hub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "hub"
            root.mkdir()
            created = ensure_navin_project_pack(root)
            self.assertTrue(created)
            manifest = json.loads((root / ".navin" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "navin-project")
            self.assertTrue(manifest["continuity"]["shared_across_studios"])
            self.assertEqual(ensure_navin_project_pack(root), [])

    def test_does_not_overwrite_existing_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "kept"
            root.mkdir()
            memory = root / ".navin" / "memory" / "MEMORY.md"
            memory.parent.mkdir(parents=True)
            memory.write_text("# Custom\n\nkeep me\n", encoding="utf-8")
            sync_workspace_templates(root, silent=True)
            self.assertEqual(memory.read_text(encoding="utf-8"), "# Custom\n\nkeep me\n")

    def test_project_brain_payload_shared_and_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "brainy"
            root.mkdir()
            payload = project_brain_payload(_Scope(root))  # type: ignore[arg-type]
            self.assertTrue(payload["shared"])
            self.assertTrue(payload["files"]["soul"]["exists"])
            self.assertTrue(payload["files"]["memory"]["exists"])
            memory = root / ".navin" / "memory" / "MEMORY.md"
            memory.write_text(
                "# Memory\n\n## Constraints\n\n- Do not touch billing\n- Keep APIs stable\n\n## Notes\n\nx\n",
                encoding="utf-8",
            )
            payload2 = project_brain_payload(_Scope(root))  # type: ignore[arg-type]
            self.assertEqual(
                payload2["constraints"],
                ["Do not touch billing", "Keep APIs stable"],
            )
            self.assertEqual(payload2["rules_dir"], ".navin/rules")
            self.assertIsInstance(payload2["rules"], list)
            self.assertTrue(payload2["drift"]["supported"])
            self.assertIsInstance(payload2["drift"]["violations"], list)

    def test_extract_constraints_ignores_placeholders(self) -> None:
        text = "## Constraints\n\n(Hard rules)\n\n- Real rule\n\n## Other\n"
        self.assertEqual(_extract_constraints(text), ["Real rule"])

    def test_system_prompt_memory_is_project_scoped(self) -> None:
        from navin.agent.context import ContextBuilder

        with tempfile.TemporaryDirectory() as tmp:
            default_ws = Path(tmp) / "default"
            project = Path(tmp) / "project"
            default_ws.mkdir()
            project.mkdir()
            (default_ws / ".navin" / "memory").mkdir(parents=True)
            (default_ws / ".navin" / "memory" / "MEMORY.md").write_text(
                "# Memory\n\nDEFAULT-WS-FACT\n", encoding="utf-8",
            )
            (project / ".navin" / "memory").mkdir(parents=True)
            (project / ".navin" / "memory" / "MEMORY.md").write_text(
                "# Memory\n\nPROJECT-FACT\n", encoding="utf-8",
            )
            builder = ContextBuilder(workspace=default_ws)
            prompt = builder.build_system_prompt(workspace=project)
            self.assertIn("PROJECT-FACT", prompt)
            self.assertNotIn("DEFAULT-WS-FACT", prompt)
            # Default workspace turns still read their own memory.
            prompt_default = builder.build_system_prompt()
            self.assertIn("DEFAULT-WS-FACT", prompt_default)


if __name__ == "__main__":
    unittest.main()
