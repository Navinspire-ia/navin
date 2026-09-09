# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Workspace-wide diagnostics for the Problems panel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from navin.lsp.client import LspClient
from navin.lsp.manager import LspManager
from navin.webui.project_insights import workspace_diagnostics_payload


class TestLspClientAllDiagnostics:
    def test_returns_a_copy_of_published_rows(self, tmp_path: Path) -> None:
        client = LspClient(["true"], tmp_path, name="pyright")
        client._diagnostics["/proj/a.py"] = [
            {
                "range": {
                    "start": {"line": 2, "character": 0},
                    "end": {"line": 2, "character": 3},
                },
                "severity": 1,
                "code": "reportMissingImports",
                "message": "Import \"missing\" could not be resolved",
            }
        ]
        snap = client.all_diagnostics()
        assert "/proj/a.py" in snap
        assert snap["/proj/a.py"][0]["code"] == "reportMissingImports"
        # Mutating the snapshot must not touch the live cache.
        snap["/proj/a.py"].clear()
        assert len(client._diagnostics["/proj/a.py"]) == 1


class TestLspManagerWorkspaceDiagnostics:
    def test_flattens_diagnostics_from_every_live_client(self, tmp_path: Path) -> None:
        root = tmp_path
        (root / "pkg").mkdir()
        seed = root / "pkg" / "main.py"
        seed.write_text("import missing\n", encoding="utf-8")
        sibling = root / "pkg" / "other.py"
        sibling.write_text("x = 1\n", encoding="utf-8")

        manager = LspManager(root)
        fake = MagicMock()
        fake.name = "pyright"
        fake.all_diagnostics.return_value = {
            str(sibling.resolve()): [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "severity": 1,
                    "code": "reportUndefinedVariable",
                    "message": "\"x\" is not accessed",
                }
            ]
        }
        manager._clients["pyright"] = fake

        with patch.object(manager, "diagnostics", return_value=[]) as diag:
            rows = manager.workspace_diagnostics(
                seed_rels=["pkg/main.py"],
                wait_s=0.01,
            )
        diag.assert_called()
        assert len(rows) == 1
        assert rows[0]["path"] == "pkg/other.py"
        assert rows[0]["abs_path"] == str(sibling.resolve())
        assert rows[0]["tool"] == "pyright"
        assert rows[0]["line"] == 1
        assert "not accessed" in rows[0]["message"]

    def test_default_seeds_skips_vendor_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.js").write_text("export {}\n", encoding="utf-8")
        manager = LspManager(tmp_path)
        seeds = manager.default_workspace_seeds()
        assert "src/app.py" in seeds
        assert not any("node_modules" in seed for seed in seeds)


class TestWorkspaceDiagnosticsPayload:
    def test_groups_rows_by_absolute_path(self, tmp_path: Path) -> None:
        root = tmp_path
        target = root / "broken.py"
        target.write_text("import nope\n", encoding="utf-8")
        abs_path = str(target.resolve())

        fake_rows = [
            {
                "path": "broken.py",
                "abs_path": abs_path,
                "line": 1,
                "col": 8,
                "end_line": 1,
                "end_col": 12,
                "severity": "error",
                "code": "reportMissingImports",
                "message": "Import \"nope\" could not be resolved",
                "tool": "pyright",
            }
        ]

        with patch("navin.lsp.LspManager.for_root") as for_root:
            manager = MagicMock()
            manager.workspace_diagnostics.return_value = fake_rows
            for_root.return_value = manager
            payload = workspace_diagnostics_payload(str(root))

        assert payload["supported"] is True
        assert payload["errors"] == 1
        assert payload["file_count"] == 1
        assert abs_path in payload["files"]
        assert payload["files"][abs_path]["diagnostics"][0]["code"] == (
            "reportMissingImports"
        )
        assert payload["diagnostics"][0]["path"] == abs_path

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        import pytest

        from navin.webui.project_insights import ProjectInsightsError

        with pytest.raises(ProjectInsightsError):
            workspace_diagnostics_payload(str(tmp_path / "missing"))

    def test_seed_paths_are_relativized(self, tmp_path: Path) -> None:
        seed = tmp_path / "a.py"
        seed.write_text("x=1\n", encoding="utf-8")
        with patch("navin.lsp.LspManager.for_root") as for_root:
            manager = MagicMock()
            manager.workspace_diagnostics.return_value = []
            for_root.return_value = manager
            workspace_diagnostics_payload(
                str(tmp_path),
                seed_paths=[str(seed.resolve())],
            )
        kwargs = manager.workspace_diagnostics.call_args.kwargs
        assert kwargs["seed_rels"] == ["a.py"]
