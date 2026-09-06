"""Montage WebUI API: doctor status, assets listing, media readiness."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from navin.montage.packages import ALL_PACKAGES, get_package
from navin.montage.stock import resolve_stock_keys, stock_status
from navin.webui.montage_api import (
    MontageApiError,
    delete_montage_timeline,
    get_montage_job,
    get_montage_timeline,
    list_montage_assets,
    list_montage_jobs,
    list_montage_timelines,
    media_readiness,
    montage_status_payload,
    packages_status,
    put_montage_timeline,
)


def _config(*, plan: str = "plus", managed_key: str | None = "sk-test") -> SimpleNamespace:
    return SimpleNamespace(
        license=SimpleNamespace(plan=plan, managed_api_key=managed_key),
        providers=SimpleNamespace(
            navin=SimpleNamespace(api_key=None, api_base=None),
            groq=SimpleNamespace(api_key=None, api_base=None),
            openrouter=SimpleNamespace(api_key=None, api_base=None),
        ),
        tools=SimpleNamespace(
            image_generation=SimpleNamespace(
                provider="navin", model="google/gemini-3.1-flash-image", enabled=None
            ),
            video_generation=SimpleNamespace(
                provider="navin", model="minimax/hailuo-3", enabled=None
            ),
            music_generation=SimpleNamespace(
                provider="navin", model="google/lyria-3-clip-preview", enabled=None
            ),
            montage=SimpleNamespace(
                stock=SimpleNamespace(
                    pexels_api_key=None,
                    unsplash_access_key=None,
                    pixabay_api_key=None,
                )
            ),
        ),
        transcription=SimpleNamespace(
            provider="navin", model="qwen/qwen3-asr-flash-2026-02-10", enabled=True
        ),
        voice=SimpleNamespace(
            tts_provider="navin", tts_model="google/gemini-3.1-flash-tts-preview"
        ),
    )


class MontageApiTest(unittest.TestCase):
    def test_status_includes_doctor_checks_and_profiles(self) -> None:
        payload = montage_status_payload(_config())
        self.assertIn("doctor", payload)
        self.assertIn("checks", payload["doctor"])
        self.assertTrue(payload["profiles"])
        self.assertIn("ffmpeg", {c["name"] for c in payload["doctor"]["checks"]})

    def test_managed_plan_marks_media_ready_without_byok_keys(self) -> None:
        media = media_readiness(_config(plan="plus", managed_key="sk-managed"))
        self.assertTrue(media["managed_key_active"])
        self.assertTrue(media["tools"]["image"]["ready"])
        self.assertTrue(media["tools"]["music"]["ready"])
        self.assertTrue(media["tools"]["stt"]["ready"])

    def test_free_plan_without_keys_is_not_ready(self) -> None:
        media = media_readiness(_config(plan="free", managed_key=None))
        self.assertFalse(media["managed_key_active"])
        self.assertFalse(media["tools"]["image"]["ready"])

    def test_list_assets_under_marketing_montage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            demo = root / "marketing" / "montage" / "demos"
            demo.mkdir(parents=True)
            clip = demo / "demo-1.mp4"
            clip.write_bytes(b"fake")
            kit = root / "marketing" / "montage" / "project-kit.md"
            kit.write_text("# kit\n", encoding="utf-8")
            listed = list_montage_assets(root)
            self.assertTrue(listed["exists"])
            names = {row["name"] for row in listed["assets"]}
            self.assertIn("demo-1.mp4", names)
            self.assertIn("project-kit.md", names)
            kinds = {row["name"]: row["kind"] for row in listed["assets"]}
            self.assertEqual(kinds["demo-1.mp4"], "video")
            self.assertEqual(kinds["project-kit.md"], "document")

    def test_list_assets_requires_workspace(self) -> None:
        with self.assertRaises(MontageApiError):
            list_montage_assets(None)

    def test_job_api_lists_and_gets_manifests(self) -> None:
        from navin.montage.jobs import create_job

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = create_job(root, "test", ["render"])
            listed = list_montage_jobs(root)
            self.assertEqual(listed["count"], 1)
            self.assertEqual(listed["jobs"][0]["id"], created["id"])
            self.assertEqual(get_montage_job(root, created["id"])["status"], "pending")

    def test_job_api_returns_404_for_unknown_job(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(MontageApiError) as caught:
                get_montage_job(Path(tmp), "missing")
            self.assertEqual(caught.exception.status, 404)

    def test_timeline_api_crud_and_errors(self) -> None:
        from navin.montage.assemble import AssembleSpec, VisualClip
        from navin.montage.timeline import timeline_from_spec

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "media"
            media.mkdir()
            image = media / "still.png"
            image.write_bytes(b"png")
            spec = AssembleSpec(
                visuals=(VisualClip.from_path(image, 2.0),),
                output=str(root / "marketing/montage/exports/out.mp4"),
            )
            document = timeline_from_spec(spec, root, name="api")
            saved = put_montage_timeline(root, "api", document)
            self.assertEqual(saved["name"], "api")
            self.assertEqual(get_montage_timeline(root, "api"), document)
            self.assertEqual(list_montage_timelines(root)["count"], 1)
            self.assertTrue(delete_montage_timeline(root, "api")["ok"])
            with self.assertRaises(MontageApiError) as caught:
                get_montage_timeline(root, "api")
            self.assertEqual(caught.exception.status, 404)

    def test_package_catalog_covers_stock_ffmpeg_hyperframes_remotion(self) -> None:
        ids = {p.id for p in ALL_PACKAGES}
        self.assertIn("stock-pexels", ids)
        self.assertIn("ffmpeg", ids)
        self.assertIn("hyperframes", ids)
        self.assertIn("remotion", ids)
        self.assertEqual(get_package("remotion").tier, "lazy")
        self.assertTrue(get_package("remotion").optional)
        self.assertEqual(get_package("stock-pexels").tier, "builtin")
        self.assertEqual(get_package("ffmpeg").tier, "system")

    def test_stock_keys_prefer_config_then_env(self) -> None:
        cfg = _config()
        cfg.tools.montage = SimpleNamespace(
            stock=SimpleNamespace(
                pexels_api_key="cfg-pexels",
                unsplash_access_key=None,
                pixabay_api_key=None,
            )
        )
        keys = resolve_stock_keys(cfg)
        self.assertEqual(keys["pexels"], "cfg-pexels")
        status = stock_status(cfg, probe=False)
        self.assertTrue(status["providers"]["pexels"]["configured"])
        self.assertFalse(status["providers"]["unsplash"]["configured"])

    def test_packages_status_marks_builtin_stock(self) -> None:
        cfg = _config()
        cfg.tools.montage = SimpleNamespace(
            stock=SimpleNamespace(
                pexels_api_key="x",
                unsplash_access_key="",
                pixabay_api_key="",
            )
        )
        payload = packages_status(cfg)
        by_id = {row["id"]: row for row in payload["items"]}
        self.assertTrue(by_id["stock-pexels"]["ui_hidden"])
        self.assertFalse(by_id["stock-pexels"]["needs_action"])
        self.assertEqual(by_id["hyperframes"]["tier"], "lazy")
        self.assertTrue(by_id["remotion"]["optional"])
        # Pending installs surface as needs_action; ready ones do not.
        if by_id["hyperframes"]["ready"]:
            self.assertFalse(by_id["hyperframes"]["needs_action"])
        else:
            self.assertTrue(by_id["hyperframes"]["needs_action"])

    def test_ffmpeg_installable_when_missing(self) -> None:
        from navin.montage.install import ffmpeg_install_plan

        plan = ffmpeg_install_plan()
        self.assertIn("options", plan)
        self.assertTrue(any(o.get("kind") == "user-local" for o in plan["options"]))
        if not plan.get("present"):
            # Missing ffmpeg must expose an Install path (pkg manager or user-local).
            self.assertTrue(
                plan.get("installable") or any(o.get("runnable") for o in plan["options"])
            )
            row = {r["id"]: r for r in packages_status(_config())["items"]}["ffmpeg"]
            self.assertTrue(row["installable"])
            self.assertTrue(row["needs_action"])

    def test_ffmpeg_summary_installable_is_false_when_present(self) -> None:
        from unittest.mock import patch

        from navin.webui.montage_api import packages_status

        plan = {
            "present": True,
            "path": "/usr/bin/ffmpeg",
            "installable": True,
            "options": [],
            "selected": {"kind": "pacman", "manual": "sudo pacman -S --needed --noconfirm ffmpeg"},
        }
        with (
            patch("navin.webui.montage_api.ffmpeg_install_plan", return_value=plan),
            patch("navin.webui.montage_api.hyperframes_bin", return_value=None),
            patch("navin.webui.montage_api.remotion_bin", return_value=None),
            patch("navin.agent.tools.browser._installed_chromium", return_value="/usr/bin/chromium"),
        ):
            payload = packages_status(_config())
        self.assertTrue(payload["ffmpeg"]["present"])
        self.assertFalse(payload["ffmpeg"]["installable"])
        row = {r["id"]: r for r in payload["items"]}["ffmpeg"]
        self.assertFalse(row["installable"])
        self.assertFalse(row["needs_action"])

    def test_ffmpeg_selected_prefers_pacman_on_omarchy(self) -> None:
        from navin.host.packages import linux_package_flavor
        from navin.montage.install import ffmpeg_install_plan

        if linux_package_flavor() != "omarchy":
            self.skipTest("live omarchy host only")
        selected = ffmpeg_install_plan()["selected"]
        self.assertIsNotNone(selected)
        self.assertEqual(selected["kind"], "pacman")
        self.assertIn("pacman -S --needed --noconfirm ffmpeg", selected["manual"])

    def test_ffmpeg_selected_prefers_available_pacman_over_user_local(self) -> None:
        from unittest.mock import patch

        from navin.host.packages import InstallPlan
        from navin.montage.install import ffmpeg_install_plan

        fake = SimpleNamespace(
            manager_available=True,
            argv=None,
            manual="sudo pacman -S --needed --noconfirm ffmpeg",
        )
        # apt/dnf/yum come before pacman in the recipe list: on a Debian-like
        # host they would win, so the root-owned managers are absent here.
        no_root_manager = InstallPlan(argv=None, manual="", manager_available=False)
        with (
            patch("navin.montage.install.pacman_plan", return_value=fake),
            patch("navin.montage.install.privileged_install", return_value=no_root_manager),
            patch("navin.montage.install.shutil.which", return_value=None),
        ):
            selected = ffmpeg_install_plan()["selected"]
        self.assertIsNotNone(selected)
        self.assertEqual(selected["kind"], "pacman")
        self.assertIn("pacman -S --needed --noconfirm ffmpeg", selected["manual"])


if __name__ == "__main__":
    unittest.main()
