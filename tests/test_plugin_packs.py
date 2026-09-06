"""Skill pack install: git/path plus npx, zip upload, and a single SKILL.md folder."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from navin.plugins.manager import PluginError, PluginManager, _normalize_npx_spec
from navin.webui.plugins_api import PluginsApiError, install_plugin


def _write_skill(root: Path, name: str) -> None:
    skill = root / "skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\nBody.\n",
        encoding="utf-8",
    )


class PluginPackInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "plugins"
        self.root.mkdir()
        self.manager = PluginManager(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_installs_a_single_skill_md_folder(self) -> None:
        src = Path(self._tmp.name) / "solo"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: solo\n---\nHi.\n", encoding="utf-8")
        row = self.manager.install_from_path(src, name="solo")
        self.assertEqual(row["skills"], ["solo"])
        self.assertTrue((self.root / "solo" / "skills" / "solo" / "SKILL.md").is_file())

    def test_installs_from_uploaded_files(self) -> None:
        row = self.manager.install_from_files(
            [
                (
                    "skills/demo/SKILL.md",
                    b"---\nname: demo\n---\nBody.\n",
                )
            ],
            name="demo",
        )
        self.assertEqual(row["name"], "demo")
        self.assertEqual(row["skills"], ["demo"])

    def test_installs_from_zip(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("pack/skills/zipped/SKILL.md", "---\nname: zipped\n---\nZ.\n")
        row = self.manager.install_from_archive(buf.getvalue(), filename="pack.zip", name="zipped")
        self.assertEqual(row["skills"], ["zipped"])

    def test_rejects_zip_slip(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escape/SKILL.md", "nope")
        with self.assertRaises(PluginError):
            self.manager.install_from_archive(buf.getvalue(), filename="evil.zip")

    def test_npx_spec_rejects_shell(self) -> None:
        with self.assertRaises(PluginError):
            _normalize_npx_spec("foo; rm -rf /")
        self.assertEqual(_normalize_npx_spec("npx @org/my-skill@1.2.3"), "@org/my-skill@1.2.3")

    def test_npx_install_uses_npm_pack(self) -> None:
        packed = Path(self._tmp.name) / "from-npm"
        packed.mkdir()
        _write_skill(packed, "from-npm")

        def fake_run(argv, **_kwargs):
            dest = Path(argv[argv.index("--pack-destination") + 1])
            import tarfile

            archive = dest / "from-npm-1.0.0.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(packed, arcname="package")

            class Result:
                returncode = 0
                stdout = "from-npm-1.0.0.tgz"
                stderr = ""

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            row = self.manager.install_from_npx("@acme/from-npm")
        self.assertEqual(row["skills"], ["from-npm"])

    def test_api_rejects_unknown_source(self) -> None:
        with self.assertRaises(PluginsApiError):
            install_plugin(source="ftp", location="x")

    def test_install_copies_skills_into_project_navin_skill(self) -> None:
        src = Path(self._tmp.name) / "solo"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: solo\n---\nHi.\n", encoding="utf-8")
        project = Path(self._tmp.name) / "repo"
        project.mkdir()
        with patch("navin.plugins.manager.plugins_root", return_value=self.root):
            payload = install_plugin(
                source="path",
                location=str(src),
                name="solo",
                scope="workspace",
                workspace_path=project,
            )
        dest = project / ".navin" / "skills" / "solo" / "SKILL.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(payload["copied"], ["solo"])
        self.assertTrue(str(payload["dest"]).endswith(".navin/skills"))
        self.assertEqual(payload["previews"][0]["name"], "solo")
        self.assertIn("Hi.", payload["previews"][0]["markdown"])

    def test_install_from_upload_copies_and_previews(self) -> None:
        project = Path(self._tmp.name) / "repo"
        project.mkdir()
        with patch("navin.plugins.manager.plugins_root", return_value=self.root):
            payload = install_plugin(
                source="upload",
                files=[
                    (
                        "skills/uploaded/SKILL.md",
                        b"---\nname: uploaded\ndescription: from zip\n---\nUploaded body.\n",
                    )
                ],
                name="uploaded",
                scope="workspace",
                workspace_path=project,
            )
        dest = project / ".navin" / "skills" / "uploaded" / "SKILL.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(payload["copied"], ["uploaded"])
        self.assertIn("Uploaded body.", payload["previews"][0]["markdown"])

    def test_npx_api_copies_into_navin_skill(self) -> None:
        packed = Path(self._tmp.name) / "from-npm"
        packed.mkdir()
        _write_skill(packed, "from-npm")
        project = Path(self._tmp.name) / "repo"
        project.mkdir()

        def fake_run(argv, **_kwargs):
            dest = Path(argv[argv.index("--pack-destination") + 1])
            import tarfile

            archive = dest / "from-npm-1.0.0.tgz"
            with tarfile.open(archive, "w:gz") as tf:
                tf.add(packed, arcname="package")

            class Result:
                returncode = 0
                stdout = "from-npm-1.0.0.tgz"
                stderr = ""

            return Result()

        with patch("navin.plugins.manager.plugins_root", return_value=self.root):
            with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
                payload = install_plugin(
                    source="npx",
                    location="@acme/from-npm",
                    scope="workspace",
                    workspace_path=project,
                )
        dest = project / ".navin" / "skills" / "from-npm" / "SKILL.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(payload["copied"], ["from-npm"])
        self.assertIn("Body.", payload["previews"][0]["markdown"])

    def test_git_api_copies_into_navin_skill(self) -> None:
        project = Path(self._tmp.name) / "repo"
        project.mkdir()

        def fake_run(argv, **_kwargs):
            dest = Path(argv[-1])
            skill = dest / "skills" / "from-git"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(
                "---\nname: from-git\ndescription: cloned\n---\nFrom git.\n",
                encoding="utf-8",
            )

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch("navin.plugins.manager.plugins_root", return_value=self.root):
            with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
                payload = install_plugin(
                    source="git",
                    location="https://github.com/acme/from-git.git",
                    name="from-git",
                    scope="workspace",
                    workspace_path=project,
                )
        dest = project / ".navin" / "skills" / "from-git" / "SKILL.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(payload["copied"], ["from-git"])
        self.assertIn("From git.", payload["previews"][0]["markdown"])

    def test_git_reuses_pack_for_a_second_project(self) -> None:
        first = Path(self._tmp.name) / "one"
        second = Path(self._tmp.name) / "two"
        first.mkdir()
        second.mkdir()
        calls = {"n": 0}

        def fake_run(argv, **_kwargs):
            calls["n"] += 1
            dest = Path(argv[-1])
            skill = dest / "skills" / "from-git"
            skill.mkdir(parents=True, exist_ok=True)
            (skill / "SKILL.md").write_text(
                "---\nname: from-git\ndescription: cloned\n---\nFrom git.\n",
                encoding="utf-8",
            )

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch("navin.plugins.manager.plugins_root", return_value=self.root):
            with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
                first_payload = install_plugin(
                    source="git",
                    location="https://github.com/acme/from-git.git",
                    name="from-git",
                    scope="workspace",
                    workspace_path=first,
                )
                second_payload = install_plugin(
                    source="git",
                    location="https://github.com/acme/from-git.git",
                    name="from-git",
                    scope="workspace",
                    workspace_path=second,
                )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first_payload["copied"], ["from-git"])
        self.assertEqual(second_payload["copied"], ["from-git"])
        self.assertTrue((second / ".navin" / "skills" / "from-git" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
