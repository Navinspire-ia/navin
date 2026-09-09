# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Real integration scenarios for Review / Security / Debug tools."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.code_review import CodeReviewTool
from navin.agent.tools.debug_repair import DebugRepairTool
from navin.agent.tools.security_scan import SecurityScanTool
from navin.debug.repair import start_repair_branch
from navin.review.scope import collect_review_scope
from navin.security.scan import run_security_scan


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")


class NonGitScopeTest(unittest.TestCase):
    def test_scope_walks_filesystem_outside_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print(1)\n", encoding="utf-8")
            (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            scope = collect_review_scope(root)
            self.assertTrue(scope["ok"])
            self.assertFalse(scope["git_repo"])
            self.assertEqual(scope["mode"], "filesystem")
            self.assertIn("app.py", scope["files"])
            self.assertNotIn("logo.png", scope["files"])


class DirtyBranchTest(unittest.TestCase):
    def test_start_branch_preserves_dirty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "init")
            (root / "a.py").write_text("x=2\n", encoding="utf-8")
            (root / "b.py").write_text("new\n", encoding="utf-8")
            result = start_repair_branch(root, slug="dirty")
            self.assertTrue(result["ok"], result)
            self.assertGreaterEqual(result["dirty_preserved"], 1)
            self.assertTrue((root / "a.py").read_text(encoding="utf-8").startswith("x=2"))
            self.assertTrue((root / "b.py").exists())

    def test_start_branch_blocks_during_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "a.py").write_text("base\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "init")
            base = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            _git(root, "checkout", "-b", "feature")
            (root / "a.py").write_text("feature\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "feature")
            _git(root, "checkout", base)
            (root / "a.py").write_text("mainline\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "mainline")
            merge = subprocess.run(
                ["git", "merge", "feature", "--no-edit"],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(merge.returncode, 0)
            self.assertTrue((root / ".git" / "MERGE_HEAD").exists())
            result = start_repair_branch(root, slug="conflict")
            self.assertFalse(result["ok"])
            self.assertIn("merge", (result.get("error") or "").lower())


class MissingCliScanTest(unittest.TestCase):
    def test_full_scan_works_without_gitleaks_bandit_semgrep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vuln.py").write_text(
                'import subprocess\nsubprocess.run("ls", shell=True)\n',
                encoding="utf-8",
            )
            result = run_security_scan(root, kind="full", max_findings=40)
            self.assertTrue(result["ok"])
            self.assertGreaterEqual(result["finding_count"], 1)
            tools = {
                t["tool"]: t
                for t in result.get("tools") or []
                if isinstance(t, dict) and "tool" in t
            }
            # Heuristic always present; CLIs may be missing - must not crash.
            self.assertTrue(any(t.get("available") for t in tools.values()) or result["finding_count"] > 0)
            for name in ("gitleaks", "bandit", "semgrep"):
                if name in tools and not tools[name].get("available"):
                    self.assertTrue(
                        tools[name].get("error") or tools[name].get("available") is False
                    )


class ToolChainSmokeTest(unittest.TestCase):
    def test_review_security_debug_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "db.py").write_text("q=1\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "init")
            (root / "db.py").write_text('q=f"SELECT {x}"\n', encoding="utf-8")

            async def _run() -> None:
                review = CodeReviewTool(workspace=root)
                security = SecurityScanTool(workspace=root)
                debug = DebugRepairTool(workspace=root)

                scope = json.loads(await review.execute(action="scope"))
                self.assertTrue(scope["ok"])
                self.assertIn("db.py", scope["files"])

                findings = [
                    {
                        "severity": "high",
                        "category": "security",
                        "confidence": 0.95,
                        "summary": "SQLi",
                        "file_path": "db.py",
                        "start_line": 1,
                        "existing_code": 'f"SELECT {x}"',
                        "recommendation": "Use parameters",
                    }
                ]
                report = json.loads(
                    await review.execute(
                        action="report",
                        findings_json=json.dumps(findings),
                        verdict="request_changes",
                        effort=2,
                    )
                )
                self.assertTrue(Path(report["report_path"]).is_file())
                self.assertIn("preview_opened", report)
                self.assertFalse(report["preview_opened"])  # no websocket bus
                html = Path(report["report_path"]).read_text(encoding="utf-8")
                self.assertIn("Real example", html)
                self.assertIn("Deliverables", html)

                scan = await security.execute(kind="quick", write_report=True)
                self.assertIn("security-report-", scan)
                self.assertIn('"preview_opened": false', scan)

                branch = json.loads(await debug.execute(action="start_branch", slug="chain"))
                self.assertTrue(branch["ok"], branch)
                dbg_report = json.loads(
                    await debug.execute(
                        action="report",
                        payload_json=json.dumps(
                            {
                                "signal": "fail",
                                "repro_steps": "pytest",
                                "root_cause": "SQLi sink",
                                "before": "red",
                                "after": "green",
                                "findings": findings,
                            }
                        ),
                    )
                )
                self.assertTrue(Path(dbg_report["report_path"]).is_file())
                self.assertFalse(dbg_report["preview_opened"])

            asyncio.run(_run())


class ReportPreviewOpenTest(unittest.TestCase):
    """Reports emit FilePreviewOpenRequestedEvent on websocket turns."""

    def test_code_review_report_opens_preview_on_websocket(self) -> None:
        from types import SimpleNamespace

        from navin.agent.tools.context import RequestContext, request_context
        from navin.bus.outbound_events import (
            FilePreviewOpenRequestedEvent,
            outbound_event_from_message,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "a.py").write_text("x=1\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "init")

            sent: list = []
            bus = SimpleNamespace(outbound=SimpleNamespace(put_nowait=sent.append))
            tool = CodeReviewTool(workspace=root)
            tool._bus = bus

            findings = [
                {
                    "severity": "medium",
                    "category": "bug",
                    "confidence": 0.9,
                    "summary": "demo",
                    "file_path": "a.py",
                    "start_line": 1,
                    "existing_code": "x=1",
                    "recommendation": "ok",
                }
            ]

            async def _run() -> None:
                with request_context(RequestContext(channel="websocket", chat_id="c1")):
                    raw = await tool.execute(
                        action="report",
                        findings_json=json.dumps(findings),
                        verdict="comment",
                        effort=1,
                    )
                data = json.loads(raw)
                self.assertTrue(data["preview_opened"])
                self.assertEqual(len(sent), 1)
                event = outbound_event_from_message(sent[0])
                self.assertIsInstance(event, FilePreviewOpenRequestedEvent)
                self.assertEqual(event.path, data["report_path"])

            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
