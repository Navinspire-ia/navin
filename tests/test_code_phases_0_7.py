"""Contract suite: Navin Code phases 0-7 (prod wiring, not stubs).

Each class asserts the phase's critical surfaces still exist and behave.
Run: ``.venv/bin/python -m pytest tests/test_code_phases_0_7.py -q``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.security.workspace_access import default_workspace_scope


class Phase0FoundationsTest(unittest.TestCase):
    def test_assist_preset_prefers_code_routes(self) -> None:
        from navin.webui import assist_api

        class FakeConfig:
            model_routes = {"code": "tab-model", "fast": "chat-fast"}

        with patch("navin.config.loader.load_config", return_value=FakeConfig()):
            self.assertEqual(assist_api._assist_preset(), "tab-model")

    def test_runtime_health_payload_shape(self) -> None:
        from navin.webui.runtime_health import runtime_health_payload

        payload = runtime_health_payload(workspace=str(Path.home()))
        self.assertIn("pressure", payload)
        self.assertIn("level", payload)
        self.assertIn(payload["level"], {"ok", "warning", "critical"})

    def test_ws_http_registers_runtime_health_route(self) -> None:
        src = Path("navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn("/api/webui/runtime/health", src)

    def test_code_statusbar_wires_runtime_health(self) -> None:
        # The status bar was extracted out of DevWorkbench into its own component,
        # so the wiring now spans both files: the workbench fetches and owns the
        # state, DevStatusBar renders it.
        workbench = Path("webui/src/components/dev/DevWorkbench.tsx").read_text(
            encoding="utf-8"
        )
        status_bar = Path("webui/src/components/dev/DevStatusBar.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("fetchRuntimeHealth", workbench)
        self.assertIn("runtimeHealth", workbench)
        self.assertIn("runtimeHealth", status_bar)
        self.assertIn("Host ok", workbench + status_bar)
        telemetry = Path("webui/src/lib/tab-telemetry.ts").read_text(encoding="utf-8")
        self.assertIn("recordTabLatency", telemetry)
        self.assertIn("tabLatencyPercentiles", telemetry)


class Phase1EditorTest(unittest.TestCase):
    def test_outline_and_related_files(self) -> None:
        from navin.webui import assist_api, symbols_api

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            (root / "calc.py").write_text(
                "from util import add\n\ndef total(xs):\n    return ",
                encoding="utf-8",
            )
            from navin.index import get_index

            get_index(root).ensure()
            related = assist_api.resolve_related_files(root, "calc.py")
            self.assertGreaterEqual(len(related), 1)
            scope = default_workspace_scope(root, True)
            outline = symbols_api.outline_payload(scope, path="calc.py")
            names = {item.get("name") for item in outline.get("items") or []}
            self.assertIn("total", names)

    def test_fim_sanitize(self) -> None:
        from navin.webui.assist_api import finalize_completion

        self.assertEqual(
            finalize_completion("return ", "\n", "```python\nadd(xs)\n```"),
            "add(xs)",
        )


class Phase2AgentTest(unittest.TestCase):
    def test_code_denied_and_apply_patch_keys(self) -> None:
        from navin.command.modules import (
            APPLY_PATCH_ONLY_METADATA_KEY,
            CODE_DENIED_TOOLS,
            READ_ONLY_TOOLS_METADATA_KEY,
        )

        self.assertIn("scrape", CODE_DENIED_TOOLS)
        self.assertEqual(APPLY_PATCH_ONLY_METADATA_KEY, "apply_patch_only")
        self.assertEqual(READ_ONLY_TOOLS_METADATA_KEY, "read_only_tools")

    def test_context_pack_provider_registered(self) -> None:
        from navin.agent.context_pack import agent_context_pack_provider, build_context_pack_lines

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x = 1\n", encoding="utf-8")
            lines = build_context_pack_lines(
                workspace=root,
                metadata={"open_files": ["a.py"]},
            )
            joined = "\n".join(lines)
            self.assertIn("Agent context pack", joined)
            self.assertTrue("a.py" in joined or "Open/attached" in joined or len(lines) >= 1)
        self.assertTrue(callable(agent_context_pack_provider))

    def test_eval_corpus_exists(self) -> None:
        corpus = Path("navin/evals/datasets/code_agent_v1.jsonl")
        self.assertTrue(corpus.is_file())
        rows = [ln for ln in corpus.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertGreaterEqual(len(rows), 5)


class Phase3ContextTest(unittest.TestCase):
    def test_rules_list_write_roundtrip(self) -> None:
        from navin.agent.project_rules import list_navin_rules, write_navin_rule

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_navin_rule(
                root, name="style", content="# Style\n\nUse tabs.\n",
            )
            rows = list_navin_rules(root)
            names = {str(r.get("name")) for r in rows}
            self.assertIn("style", names)

    def test_context_usage_buckets(self) -> None:
        from navin.webui.context_usage import context_usage_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = context_usage_payload(
                {"messages": []},
                project_path=root,
            )
            self.assertIn("buckets", payload)
            self.assertIn("included", payload)

    def test_warmer_schedule(self) -> None:
        from navin.index.warmer import schedule_warm

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.py").write_text("print(1)\n", encoding="utf-8")
            # Best-effort: may return False if index busy; must not raise.
            schedule_warm(root)


class Phase4GitPrTest(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None, "git missing")
    def test_stage_and_branch(self) -> None:
        from navin.webui.project_search import git_branch_payload, git_stage_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(
                ["git", "init", "-q", "-b", "main"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "t@t"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "t"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "a.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "a.txt").write_text("two\n", encoding="utf-8")
            scope = default_workspace_scope(root, True)
            staged = git_stage_payload(scope, ["a.txt"], stage=True)
            self.assertTrue(any(f["path"] == "a.txt" and f["staged"] for f in staged["files"]))
            created = git_branch_payload(scope, op="create", name="feature/phase4")
            self.assertEqual(created.get("branch"), "feature/phase4")


class Phase5DapTest(unittest.TestCase):
    def test_runtime_detect_and_fake_start(self) -> None:
        from navin.dap.session import DebugSession, detect_runtime

        self.assertEqual(detect_runtime("app.py"), "python")
        self.assertEqual(detect_runtime("server.js"), "node")
        fixture = Path("tests/fixtures/fake_dap_adapter.py").resolve()
        self.assertTrue(fixture.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            session = DebugSession(root, fake_adapter=str(fixture))
            session.set_breakpoints("app.py", [1])
            started = session.start(program="app.py")
            self.assertIn(started["state"], {"starting", "running", "stopped"})
            session.stop()


class Phase6ContinuityTest(unittest.TestCase):
    def test_leave_handoff_api_payload(self) -> None:
        from navin.project_scaffold import ensure_navin_project_pack
        from navin.utils.helpers import ensure_project_scaffold
        from navin.webui.continuity_api import leave_handoff_payload, resume_seed_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if shutil.which("git"):
                subprocess.run(
                    ["git", "init", "-q", "-b", "main"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                )
            ensure_project_scaffold(root, silent=True)
            ensure_navin_project_pack(root)
            scope = default_workspace_scope(root, True)
            seed = resume_seed_payload(scope)
            self.assertIn("seed", seed)
            result = leave_handoff_payload(
                scope,
                body="Goal: verify phases. Done: tests. Next: ship.",
                session_key="websocket:phase6",
            )
            self.assertTrue(result.get("saved"))
            resume = (root / ".navin" / "continuity" / "RESUME.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Goal: verify phases", resume)

    def test_heartbeat_board_continuity_task(self) -> None:
        from navin.workspace_layout import read_with_root_fallback

        text = read_with_root_fallback(Path(), "HEARTBEAT.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Code board continuity", text)
        self.assertIn("HEARTBEAT_OK", text)

    def test_board_persists_head_sha(self) -> None:
        from navin.board.store import ProjectBoardStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectBoardStore(root)
            task = store.create_task(title="ship", actor="test", actor_type="human")
            updated = store.update_task(
                task["id"],
                fields={
                    "pr_url": "https://github.com/o/r/pull/9",
                    "head_sha": "abc123def456",
                    "branch": "navin/task-ship",
                },
                actor="test",
                actor_type="agent",
            )
            self.assertEqual(updated["head_sha"], "abc123def456")
            reloaded = store.get_task(task["id"])
            self.assertEqual(reloaded["head_sha"], "abc123def456")


class Phase7PolishTest(unittest.TestCase):
    def test_constraint_drift_in_brain(self) -> None:
        from navin.utils.helpers import ensure_project_scaffold
        from navin.webui.project_brain import project_brain_payload

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_project_scaffold(root, silent=True)
            (root / ".navin" / "memory" / "MEMORY.md").write_text(
                "# Memory\n\n## Constraints\n\n- Never delete public APIs\n",
                encoding="utf-8",
            )
            scope = default_workspace_scope(root, True)
            brain = project_brain_payload(scope)
            self.assertTrue(brain["drift"]["supported"])
            self.assertIn("violations", brain["drift"])

    def test_pr_sync_payload_shape(self) -> None:
        from navin.board.pr_sync import pr_sync_payload
        from navin.board.store import ProjectBoardStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectBoardStore(root)
            payload = pr_sync_payload(root, store)
            self.assertIn("suggestions", payload)
            self.assertIn("available", payload)

    def test_node_adapter_discovery_helper(self) -> None:
        from navin.dap.session import (
            DebugSessionError,
            _candidate_js_debug_servers,
            resolve_adapter_argv,
        )

        candidates = _candidate_js_debug_servers()
        self.assertIsInstance(candidates, list)
        env = {k: v for k, v in os.environ.items() if k != "NAVIN_JS_DEBUG_ADAPTER"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navin.dap.session.shutil.which", return_value="/usr/bin/node"):
                with patch(
                    "navin.dap.session._candidate_js_debug_servers",
                    return_value=[],
                ):
                    with self.assertRaises(DebugSessionError):
                        resolve_adapter_argv(runtime="node")

    def test_ui_wires_pr_sync_and_drift(self) -> None:
        board = Path("webui/src/components/dev/DevBoardPanel.tsx").read_text(
            encoding="utf-8"
        )
        home = Path("webui/src/components/project/ProjectHomeView.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn("fetchGithubPrSync", board)
        self.assertIn("prSuggestions", board)
        self.assertIn("Mark done", board)
        self.assertIn("driftWarnings", home)
        self.assertIn("brain?.drift", home)

    def test_ws_routes_registered(self) -> None:
        src = Path("navin/webui/ws_http.py").read_text(encoding="utf-8")
        for needle in (
            "/resume-seed",
            "/leave-handoff",
            "/github/pr-sync",
            "/project-rules",
            "/context-usage",
            "/github/pr/create",
            "/github/checks",
            "/github/fix-ci",
            "/debug",
        ):
            self.assertIn(needle, src, msg=f"missing route {needle}")


if __name__ == "__main__":
    unittest.main()
