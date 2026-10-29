# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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


class ClaudePluginLayoutTest(unittest.TestCase):
    """A plugin written for Claude Code installs unchanged and exposes everything."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "plugins"
        self.root.mkdir()
        self.manager = PluginManager(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _claude_plugin(self, name: str = "deploy-kit") -> Path:
        import json

        src = Path(self._tmp.name) / name
        (src / ".claude-plugin").mkdir(parents=True)
        (src / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": "2.0.0", "description": "Deploys", "author": {"name": "Acme"}}),
            encoding="utf-8",
        )
        (src / "commands").mkdir()
        (src / "commands" / "ship.md").write_text("---\ndescription: Ship\n---\nShip it.\n", encoding="utf-8")
        (src / "agents").mkdir()
        (src / "agents" / "reviewer.md").write_text("---\nname: reviewer\ndescription: Reviews\n---\nBody.\n", encoding="utf-8")
        (src / "hooks").mkdir()
        (src / "hooks" / "hooks.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "exec", "hooks": [{"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/hooks/check.sh"}]}]}}),
            encoding="utf-8",
        )
        (src / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"deploy": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]}}}),
            encoding="utf-8",
        )
        return src

    def test_claude_layout_installs_with_every_component(self) -> None:
        row = self.manager.install_from_path(self._claude_plugin())
        self.assertEqual(row["name"], "deploy-kit")
        self.assertEqual((row["version"], row["description"], row["author"]), ("2.0.0", "Deploys", "Acme"))
        self.assertEqual(row["commands"], ["ship"])
        self.assertEqual(row["agents"], ["reviewer"])
        self.assertEqual(row["hooks"], ["PreToolUse"])
        self.assertEqual(row["mcp_servers"], ["deploy"])
        self.assertEqual(self.manager.mcp_server_names("deploy-kit"), ["deploy-kit-deploy"])

    def test_commands_only_plugin_is_a_valid_plugin(self) -> None:
        """Claude plugins often ship commands alone: that used to be refused as empty."""
        src = Path(self._tmp.name) / "cmds"
        (src / "commands").mkdir(parents=True)
        (src / "commands" / "hello.md").write_text("Say hello.\n", encoding="utf-8")
        row = self.manager.install_from_path(src, name="cmds")
        self.assertEqual(row["commands"], ["hello"])
        self.assertEqual(row["skills"], [])

    def test_an_empty_folder_is_still_refused(self) -> None:
        src = Path(self._tmp.name) / "empty"
        src.mkdir()
        (src / "README.md").write_text("nothing\n", encoding="utf-8")
        with self.assertRaisesRegex(PluginError, "no components"):
            self.manager.install_from_path(src, name="empty")

    def test_claude_plugin_root_expands_like_navin_plugin_root(self) -> None:
        from navin.plugins.manager import expand_plugin_vars

        out = expand_plugin_vars({"args": ["${CLAUDE_PLUGIN_ROOT}/s.js", "${NAVIN_PLUGIN_ROOT}/t.js"]}, Path("/p/kit"))
        self.assertEqual(out, {"args": ["/p/kit/s.js", "/p/kit/t.js"]})

    def test_manifest_inline_mcp_servers_merge_with_the_file(self) -> None:
        import json

        src = Path(self._tmp.name) / "inline"
        (src / ".claude-plugin").mkdir(parents=True)
        (src / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "inline", "mcpServers": {"a": {"command": "npx", "args": ["a"]}}}), encoding="utf-8",
        )
        (src / ".mcp.json").write_text(json.dumps({"mcpServers": {"b": {"command": "npx", "args": ["b"]}}}), encoding="utf-8")
        row = self.manager.install_from_path(src)
        self.assertEqual(row["mcp_servers"], ["a", "b"])

    def test_github_shorthand_and_subfolder_clone(self) -> None:
        """``owner/repo`` clones from GitHub; ``#folder`` installs that folder of a monorepo."""
        from navin.plugins.manager import git_url_for

        self.assertEqual(git_url_for("acme/tools"), "https://github.com/acme/tools.git")
        self.assertEqual(git_url_for("https://x.test/r.git"), "https://x.test/r.git")
        self.assertIsNone(git_url_for("./local"))
        self.assertIsNone(git_url_for(str(Path(self._tmp.name))))
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            self.assertEqual(kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
            dest = Path(argv[-1])
            (dest / ".git").mkdir(parents=True)
            skill = dest / "plugins" / "ship" / "skills" / "ship"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: ship\n---\nShip.\n", encoding="utf-8")
            (dest / "plugins" / "other" / "skills").mkdir(parents=True)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch("navin.plugins.manager.subprocess.run", side_effect=fake_run):
            row = self.manager.install_from_git("acme/tools#plugins/ship")
        self.assertEqual(calls[0][:5], ["git", "clone", "--depth", "1", "https://github.com/acme/tools.git"])
        self.assertEqual(row["name"], "ship")
        self.assertEqual(row["skills"], ["ship"])
        self.assertEqual(row["source"], "acme/tools#plugins/ship")
        self.assertFalse((self.root / "ship" / ".git").exists())
        self.assertFalse((self.root / "ship" / "plugins").exists())


class MarketplaceTest(unittest.TestCase):
    """``/plugin marketplace add`` + ``/plugin install name@marketplace``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "plugins"
        self.root.mkdir()
        self.manager = PluginManager(self.root)
        from navin.plugins.marketplace import MarketplaceRegistry

        self.registry = MarketplaceRegistry(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _marketplace_folder(self, name: str = "acme-tools") -> Path:
        import json

        market = Path(self._tmp.name) / name
        (market / ".claude-plugin").mkdir(parents=True)
        (market / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({
                "name": name,
                "owner": {"name": "Acme"},
                "plugins": [
                    {"name": "deploy", "source": "./plugins/deploy", "description": "Deploys", "version": "1.0.0"},
                    {"name": "lint", "source": {"source": "github", "repo": "acme/lint"}},
                    {"name": "docs", "source": "https://git.example/docs.git"},
                    {"name": "broken", "source": {"source": "ftp", "url": "x"}},
                ],
            }),
            encoding="utf-8",
        )
        skill = market / "plugins" / "deploy" / "skills" / "deploy"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: deploy\n---\nDeploy.\n", encoding="utf-8")
        return market

    def test_add_folder_lists_resolved_plugins_and_skips_broken_entries(self) -> None:
        market = self.registry.add(str(self._marketplace_folder()))
        self.assertEqual((market.name, market.owner), ("acme-tools", "Acme"))
        by_name = {p.name: p for p in market.plugins}
        self.assertEqual(sorted(by_name), ["deploy", "docs", "lint"])
        self.assertEqual(by_name["deploy"].kind, "path")
        self.assertTrue(by_name["deploy"].location.endswith("plugins/deploy"))
        self.assertEqual((by_name["lint"].kind, by_name["lint"].location), ("git", "https://github.com/acme/lint.git"))
        self.assertEqual((by_name["docs"].kind, by_name["docs"].location), ("git", "https://git.example/docs.git"))
        # The registry survives a new instance; the plugin lister ignores it.
        from navin.plugins.marketplace import MarketplaceRegistry

        self.assertEqual([m.name for m in MarketplaceRegistry(self.root).list()], ["acme-tools"])
        self.assertEqual(self.manager.list(), [])
        with self.assertRaisesRegex(PluginError, "already registered"):
            self.registry.add(str(market.path))

    def test_install_plugin_at_marketplace_resolves_the_source(self) -> None:
        from navin.plugins.marketplace import install_from_marketplace

        self.registry.add(str(self._marketplace_folder()))
        row = install_from_marketplace("deploy@acme-tools", manager=self.manager, registry=self.registry)
        self.assertEqual((row["name"], row["skills"], row["source"]), ("deploy", ["deploy"], "deploy@acme-tools"))
        # A bare name works while only one marketplace offers it.
        self.manager.uninstall("deploy")
        self.assertEqual(install_from_marketplace("deploy", manager=self.manager, registry=self.registry)["name"], "deploy")
        with self.assertRaisesRegex(PluginError, "not in marketplace acme-tools"):
            install_from_marketplace("nope@acme-tools", manager=self.manager, registry=self.registry)
        with self.assertRaisesRegex(PluginError, "marketplace not found"):
            install_from_marketplace("deploy@unknown", manager=self.manager, registry=self.registry)

    def test_git_marketplace_clones_into_a_hidden_folder_and_updates(self) -> None:
        import json

        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            calls.append(list(argv))
            if argv[1] == "clone":
                dest = Path(argv[-1])
                (dest / ".git").mkdir(parents=True)
                (dest / "marketplace.json").write_text(
                    json.dumps({"name": "remote-market", "plugins": [{"name": "a", "source": "acme/a"}]}), encoding="utf-8",
                )

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        with patch("navin.plugins.marketplace.subprocess.run", side_effect=fake_run):
            market = self.registry.add("acme/marketplace")
            self.assertEqual(self.registry.update("remote-market"), ["remote-market"])
        self.assertEqual(market.name, "remote-market")
        self.assertEqual(market.path, self.root / ".marketplaces" / "remote-market")
        self.assertEqual(calls[0][:5], ["git", "clone", "--depth", "1", "https://github.com/acme/marketplace.git"])
        self.assertEqual(calls[1][:4], ["git", "-C", str(market.path), "pull"])
        self.assertEqual([p.location for p in market.plugins], ["https://github.com/acme/a.git"])
        # Hidden folder: never listed as an installed plugin.
        self.assertEqual(self.manager.list(), [])
        self.registry.remove("remote-market")
        self.assertFalse(market.path.exists())
        self.assertEqual(self.registry.list(), [])
