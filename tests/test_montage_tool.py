# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Montage detect/doctor/analyze/calendar/render (setup mocked)."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from navin.montage.analyze import analyze_project, write_project_kit
from navin.montage.calendar import build_calendar, write_calendar
from navin.montage.detect import ToolchainDetect, detect_toolchain
from navin.montage.doctor import run_doctor
from navin.montage.render import render_composition
from navin.montage.screenshot import register_captures


def _write(root: Path, rel: str, content: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return target


class AnalyzeProjectTest(unittest.TestCase):
    def test_kit_from_readme_and_package(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root,
                "package.json",
                json.dumps(
                    {
                        "name": "acme-app",
                        "dependencies": {"react": "18.0.0", "vite": "5.0.0"},
                    }
                ),
            )
            _write(
                root,
                "README.md",
                "# Acme\n\nShip faster with Acme.\n",
            )
            _write(root, "public/hero.png", "fake")
            kit = analyze_project(root)
            self.assertEqual(kit.name, "acme-app")
            self.assertIn("React", kit.stack)
            self.assertIn("Vite", kit.stack)
            self.assertTrue(any("hero.png" in a for a in kit.assets))
            paths = write_project_kit(root, kit)
            self.assertTrue(paths["markdown"].is_file())
            self.assertTrue(paths["json"].is_file())
            data = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(data["name"], "acme-app")


class CalendarTest(unittest.TestCase):
    def test_writes_md_csv_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "README.md", "# Demo\n\nDemo product.\n")
            kit = analyze_project(root)
            rows = build_calendar(kit, days=7)
            self.assertEqual(len(rows), 7)
            paths = write_calendar(root, rows, kit=kit, days=7)
            self.assertTrue(paths["markdown"].is_file())
            self.assertTrue(paths["csv"].is_file())
            self.assertTrue(paths["json"].is_file())
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["days"], 7)


class DoctorTest(unittest.TestCase):
    def test_missing_hyperframes_not_ready_for_composition(self) -> None:
        tc = ToolchainDetect(
            node="/usr/bin/node",
            npm="/usr/bin/npm",
            npx="/usr/bin/npx",
            ffmpeg=None,
            chrome="/usr/bin/google-chrome",
            hyperframes=None,
            free_disk_gb=10.0,
        )
        report = run_doctor(tc)
        self.assertTrue(report.ready_for_ai)
        self.assertFalse(report.ready_for_composition)
        self.assertIn("hyperframes", report.render())


class ScreenshotTest(unittest.TestCase):
    def test_register_capture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = _write(root, "public/shot.png", "png-bytes")
            result = register_captures(root, [str(src.relative_to(root))])
            self.assertTrue(result["saved"])
            self.assertTrue((root / result["saved"][0]).is_file())


class RenderGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_render_refuses_without_toolchain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "marketing/montage/compositions/reel.html", "<html></html>")
            with patch(
                "navin.montage.render.run_doctor",
                return_value=run_doctor(
                    ToolchainDetect(
                        node=None,
                        npm=None,
                        hyperframes=None,
                        chrome=None,
                        free_disk_gb=0.5,
                    )
                ),
            ):
                result = await render_composition(
                    root,
                    composition="marketing/montage/compositions/reel.html",
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "not_ready")


class DemoPackageTest(unittest.TestCase):
    def test_register_and_package_keeps_master_on_bad_source(self) -> None:
        from navin.montage.demo import (
            package_social,
            register_demo,
            render_package_result,
        )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Minimal fake webm bytes (not a real container)
            src = root / "raw-demo.webm"
            src.write_bytes(b"FAKEWEBM")
            registered = register_demo(root, str(src), name="walkthrough")
            self.assertTrue(registered["ok"])
            self.assertTrue((root / registered["demo"]).is_file())
            packaged = package_social(
                root,
                registered["demo"],
                title="Walkthrough",
            )
            # Must not crash; should keep a master or report a typed error
            self.assertTrue(
                packaged.get("ok")
                or packaged.get("partial")
                or packaged.get("error")
            )
            text = render_package_result(packaged)
            self.assertNotIn("Package failed: None", text)
            if packaged.get("master"):
                self.assertTrue((root / packaged["master"]).is_file())
            self.assertTrue((root / packaged["brief"]).is_file())

    def test_demo_register_rejects_non_video(self) -> None:
        from navin.montage.demo import register_demo

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "note.txt"
            bad.write_text("hi", encoding="utf-8")
            result = register_demo(root, str(bad))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "bad_type")

    def test_package_real_ffmpeg_exports_three_ratios(self) -> None:
        import shutil
        import subprocess

        from navin.montage.demo import package_social, register_demo

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg not installed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "ok.mp4"
            completed = subprocess.run(  # noqa: S603
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=1280x720:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=f=440:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(src),
                ],
                capture_output=True,
                timeout=60,
            )
            if completed.returncode != 0:
                self.skipTest("ffmpeg cannot encode test clip")
            registered = register_demo(root, str(src), name="live")
            self.assertTrue(registered["ok"])
            packaged = package_social(root, registered["demo"], title="Live demo")
            self.assertTrue(packaged.get("ok"))
            self.assertFalse(packaged.get("partial"))
            self.assertEqual(
                set(packaged.get("exports") or {}),
                {
                    "youtube_landscape",
                    "youtube_shorts",
                    "instagram_reels",
                    "instagram_feed",
                    "tiktok",
                    "linkedin",
                },
            )
            for rel in packaged["exports"].values():
                path = root / rel
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)


class MontageToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_and_calendar_actions(self) -> None:
        from navin.agent.tools.montage import MontageTool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "README.md", "# ToolDemo\n\nHello.\n")
            tool = MontageTool.__new__(MontageTool)
            tool.workspace = root

            with patch.object(tool, "_root_or_error", return_value=(root, "")):
                out = await tool.execute(action="analyze")
                self.assertIn("project-kit.md", out)
                out2 = await tool.execute(action="calendar", days=7)
                self.assertIn("calendar.md", out2)

    async def test_setup_mocked(self) -> None:
        from navin.agent.tools.montage import MontageTool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = MontageTool.__new__(MontageTool)
            tool.workspace = root
            fake = {
                "ok": True,
                "home": str(Path.home() / ".navin" / "montage"),
                "hyperframes": "/tmp/hyperframes",
                "chrome": "/usr/bin/chrome",
                "logs": ["installed"],
                "skipped": True,
            }
            with (
                patch.object(tool, "_root_or_error", return_value=(root, "")),
                patch(
                    "navin.montage.bootstrap.run_setup",
                    new=AsyncMock(return_value=fake),
                ),
            ):
                out = await tool.execute(action="setup")
            self.assertIn("ok: True", out)

    def test_detect_toolchain_runs(self) -> None:
        tc = detect_toolchain()
        self.assertTrue(tc.montage_home)

    async def test_lipsync_reports_explicit_credential_requirement(self) -> None:
        from navin.agent.tools.montage import MontageTool

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = MontageTool.__new__(MontageTool)
            tool.workspace = root
            lip_sync = SimpleNamespace(provider="sync_labs", api_key=None)
            config = SimpleNamespace(
                tools=SimpleNamespace(montage=SimpleNamespace(lip_sync=lip_sync))
            )
            with (
                patch.object(tool, "_root_or_error", return_value=(root, "")),
                patch("navin.config.loader.load_config", return_value=config),
                patch.dict("os.environ", {}, clear=True),
            ):
                output = await tool.execute(
                    action="lipsync",
                    path="source.mp4",
                    voice="voice.wav",
                )
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["requires_credentials"])
        self.assertEqual(payload["error"], "credentials_required")


if __name__ == "__main__":
    unittest.main()
