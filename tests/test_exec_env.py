"""Sandboxed exec must see forge / git / SSH env without a manual allowlist."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.agent.tools.exec_env import (
    build_exec_env,
    forge_tokens_from_config,
    forwarded_host_env,
    names_for_forge_host,
)
from navin.agent.tools.sandbox import wrap_command
from navin.agent.tools.shell import ExecTool


class ForgeHostNamesTest(unittest.TestCase):
    def test_github_and_gitlab_use_their_cli_names(self) -> None:
        self.assertIn("GITHUB_TOKEN", names_for_forge_host("github.com"))
        self.assertIn("GITLAB_TOKEN", names_for_forge_host("gitlab.com"))

    def test_self_hosted_includes_gitea_actions_and_tea(self) -> None:
        names = names_for_forge_host("git.navinspire.ai")
        self.assertIn("GITEA_SERVER_TOKEN", names)
        self.assertIn("FORGEJO_TOKEN", names)
        self.assertIn("TEA_TOKEN", names)


class ForwardedHostEnvTest(unittest.TestCase):
    def test_gitea_server_token_is_forwarded_without_an_allowlist(self) -> None:
        env = forwarded_host_env({"GITEA_SERVER_TOKEN": "tea-secret", "OPENAI_API_KEY": "nope"})
        self.assertEqual(env["GITEA_SERVER_TOKEN"], "tea-secret")
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_forgejo_tea_gitlab_and_gpg_are_forwarded(self) -> None:
        env = forwarded_host_env(
            {
                "GITEA_URL": "https://git.example",
                "TEA_URL": "https://git.example",
                "FORGEJO_INSTANCE_URL": "https://git.example",
                "GLAB_TOKEN": "glpat-x",
                "GNUPGHOME": "/home/me/.gnupg",
                "TMPDIR": "/tmp/navin",
                "ANTHROPIC_API_KEY": "sk-ant",
            }
        )
        self.assertEqual(env["GITEA_URL"], "https://git.example")
        self.assertEqual(env["TEA_URL"], "https://git.example")
        self.assertEqual(env["FORGEJO_INSTANCE_URL"], "https://git.example")
        self.assertEqual(env["GLAB_TOKEN"], "glpat-x")
        self.assertEqual(env["GNUPGHOME"], "/home/me/.gnupg")
        self.assertEqual(env["TMPDIR"], "/tmp/navin")
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_ssh_agent_and_proxy_are_forwarded(self) -> None:
        env = forwarded_host_env(
            {
                "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.sock",
                "https_proxy": "http://proxy:8080",
                "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem",
            }
        )
        self.assertEqual(env["SSH_AUTH_SOCK"], "/run/user/1000/ssh-agent.sock")
        self.assertEqual(env["https_proxy"], "http://proxy:8080")
        self.assertEqual(env["SSL_CERT_FILE"], "/etc/ssl/certs/ca.pem")

    def test_per_host_navin_forge_tokens_are_forwarded(self) -> None:
        env = forwarded_host_env(
            {"NAVIN_FORGE_TOKEN_GIT_NAVINSPIRE_AI": "host-secret"}
        )
        self.assertEqual(env["NAVIN_FORGE_TOKEN_GIT_NAVINSPIRE_AI"], "host-secret")


class SettingsTokenExportTest(unittest.TestCase):
    def test_forgejo_settings_token_fills_tea_and_gitea_names(self) -> None:
        exported = forge_tokens_from_config(
            {"git.navinspire.ai": "cfg-token"},
            environ={},
        )
        self.assertEqual(exported["GITEA_SERVER_TOKEN"], "cfg-token")
        self.assertEqual(exported["GITEA_HTTP_TOKEN"], "cfg-token")
        self.assertEqual(exported["FORGEJO_TOKEN"], "cfg-token")
        self.assertEqual(exported["TEA_TOKEN"], "cfg-token")
        self.assertEqual(
            exported["NAVIN_FORGE_TOKEN_GIT_NAVINSPIRE_AI"],
            "cfg-token",
        )

    def test_process_env_wins_over_settings(self) -> None:
        exported = forge_tokens_from_config(
            {"git.navinspire.ai": "cfg-token"},
            environ={"GITEA_SERVER_TOKEN": "env-token"},
        )
        self.assertNotIn("GITEA_SERVER_TOKEN", exported)
        self.assertEqual(exported["FORGEJO_TOKEN"], "cfg-token")


class BuildExecEnvTest(unittest.TestCase):
    def test_operator_allowlist_still_works(self) -> None:
        env = build_exec_env(
            base={"HOME": "/tmp", "PATH": "/bin"},
            allowed_env_keys=["MY_CUSTOM_TOKEN"],
            environ={"MY_CUSTOM_TOKEN": "custom"},
        )
        self.assertEqual(env["MY_CUSTOM_TOKEN"], "custom")

    def test_provider_keys_stay_out(self) -> None:
        env = build_exec_env(
            base={"HOME": "/tmp"},
            environ={"OPENAI_API_KEY": "sk-secret", "GITEA_SERVER_TOKEN": "ok"},
        )
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertEqual(env["GITEA_SERVER_TOKEN"], "ok")


class ExecToolEnvTest(unittest.TestCase):
    def test_tool_injects_settings_forge_tokens(self) -> None:
        tool = ExecTool(
            working_dir="/tmp",
            forge_tokens={"github.com": "ghp_from_settings"},
        )
        with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False):
            env = tool._build_env()
        self.assertEqual(env.get("GITHUB_TOKEN") or env.get("GH_TOKEN"), "ghp_from_settings")

    def test_builtin_deny_rules_do_not_strip_forge_env(self) -> None:
        tool = ExecTool(
            working_dir="/tmp",
            builtin_deny_rules=True,
            forge_tokens={"git.example": "tea-from-settings"},
        )
        with patch.dict(os.environ, {"GITEA_SERVER_TOKEN": "from-env", "OPENAI_API_KEY": "nope"}, clear=False):
            env = tool._build_env()
        self.assertEqual(env["GITEA_SERVER_TOKEN"], "from-env")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertTrue(tool.builtin_deny_rules)


class BwrapCredentialBindsTest(unittest.TestCase):
    def test_bwrap_exposes_ssh_and_system_git(self) -> None:
        from navin.agent.tools import sandbox as sandbox_mod

        real_which = shutil.which
        with tempfile.TemporaryDirectory() as ws, patch.object(
            sandbox_mod.sys, "platform", "linux"
        ), patch.object(
            sandbox_mod.shutil,
            "which",
            side_effect=lambda name, *a, **k: (
                "/usr/bin/bwrap" if name == "bwrap" else real_which(name, *a, **k)
            ),
        ):
            wrapped = wrap_command("bwrap", "git push", ws, ws)
            self.assertIn("/etc/ssh", wrapped)
            self.assertIn("/etc/gitconfig", wrapped)
            self.assertIn("/etc/passwd", wrapped)
            self.assertIn("/etc/hosts", wrapped)
            self.assertIn("/mnt", wrapped)
            self.assertIn(".ssh", wrapped)
            self.assertIn(".netrc", wrapped)
            self.assertIn(".nvm", wrapped)
            self.assertIn(".docker", wrapped)
            self.assertIn("docker.sock", wrapped)
            self.assertIn("--bind-try", wrapped)
            self.assertNotIn("/.navin ", wrapped)
            self.assertNotIn(".navin/", wrapped)

    def test_landlock_allows_writes_to_an_existing_docker_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock = Path(tmp) / "docker.sock"
            sock.write_text("", encoding="utf-8")
            with patch(
                "navin.agent.tools.sandbox.native_sandbox_binary",
                return_value="/opt/bin/navin-sandbox",
            ), patch(
                "navin.agent.tools.sandbox.docker_socket_paths",
                return_value=[str(sock)],
            ):
                wrapped = wrap_command("landlock", "docker ps", tmp, tmp)
        self.assertIn("--allow-write", wrapped)
        self.assertIn(str(sock), wrapped)
