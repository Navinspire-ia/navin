# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the WebUI Git import helper."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from navin.webui.git_clone import (
    GitCloneError,
    clone_git_project,
    normalize_git_url,
    repo_name_from_url,
)


class NormalizeGitUrlTest(unittest.TestCase):
    def test_https_passthrough(self) -> None:
        self.assertEqual(
            normalize_git_url("https://github.com/org/repo.git"),
            "https://github.com/org/repo.git",
        )

    def test_owner_repo_shorthand(self) -> None:
        self.assertEqual(
            normalize_git_url("org/repo"),
            "https://github.com/org/repo",
        )

    def test_host_without_scheme(self) -> None:
        self.assertEqual(
            normalize_git_url("github.com/org/repo"),
            "https://github.com/org/repo",
        )

    def test_strips_query_and_fragment(self) -> None:
        self.assertEqual(
            normalize_git_url("https://github.com/org/repo?tab=readme#install"),
            "https://github.com/org/repo",
        )

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(GitCloneError):
            normalize_git_url("not a url")


class RepoNameTest(unittest.TestCase):
    def test_from_https(self) -> None:
        self.assertEqual(
            repo_name_from_url("https://github.com/org/my-app.git"),
            "my-app",
        )

    def test_from_ssh(self) -> None:
        self.assertEqual(
            repo_name_from_url("git@github.com:org/my_app.git"),
            "my_app",
        )


class CloneGitProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        # No real config / gh lookup: subprocess.run is mocked below and a
        # `gh auth token` call would hit the fake clone.
        patcher = mock.patch(
            "navin.utils.git_credentials.credential_for_url", return_value=None
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_clone_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            fake = mock.Mock(
                returncode=0,
                stdout="",
                stderr="",
            )

            def _run(argv, **_kwargs):
                # Mimic git clone creating the target folder.
                Path(argv[-1]).mkdir(parents=True, exist_ok=True)
                return fake

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run) as run:
                    result = clone_git_project(
                        "https://github.com/org/demo.git",
                        parent=str(parent),
                    )
            self.assertEqual(result["name"], "demo")
            self.assertEqual(result["path"], str(parent / "demo"))
            self.assertFalse(result["reused"])
            self.assertEqual(run.call_args.args[0][:3], ["git", "clone", "--depth"])

    def test_reuses_existing_same_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "demo"
            target.mkdir()
            (target / "README.md").write_text("hi", encoding="utf-8")

            def _run(argv, **_kwargs):
                # remote get-url origin
                return mock.Mock(
                    returncode=0,
                    stdout="https://github.com/org/demo.git\n",
                    stderr="",
                )

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run):
                    result = clone_git_project(
                        "org/demo",
                        parent=str(parent),
                    )
            self.assertTrue(result["reused"])
            self.assertEqual(result["path"], str(target))

    def test_rejects_conflicting_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "demo"
            target.mkdir()
            (target / "other.txt").write_text("x", encoding="utf-8")

            def _run(_argv, **_kwargs):
                return mock.Mock(returncode=1, stdout="", stderr="")

            with mock.patch("navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"):
                with mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run):
                    with self.assertRaises(GitCloneError) as ctx:
                        clone_git_project(
                            "https://github.com/org/demo.git",
                            parent=str(parent),
                        )
            self.assertIn("already exists", ctx.exception.message)

    def test_clone_strips_bundle_library_path(self) -> None:
        # Regression #2: the frozen desktop build prepends its _internal
        # dir to LD_LIBRARY_PATH; git-remote-https then loads the bundled
        # libssl (OPENSSL_3.0.0) and aborts against host libcurl.
        fake = mock.Mock(returncode=0, stdout="", stderr="")

        def _run(argv, **kwargs):
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            captured["env"] = kwargs["env"]
            return fake

        captured: dict = {}
        with mock.patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": "/usr/lib/Navin/navin-dist/_internal:/opt/custom/lib",
                "DYLD_LIBRARY_PATH": "/usr/lib/Navin/navin-dist/_internal",
            },
        ):
            with mock.patch(
                "navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"
            ):
                with mock.patch(
                    "navin.webui.git_clone.subprocess.run", side_effect=_run
                ):
                    with tempfile.TemporaryDirectory() as tmp:
                        clone_git_project(
                            "https://github.com/org/demo.git", parent=tmp
                        )
        env = captured["env"]
        self.assertNotIn("navin-dist", env.get("LD_LIBRARY_PATH", ""))
        self.assertEqual(env.get("LD_LIBRARY_PATH"), "/opt/custom/lib")
        self.assertNotIn("DYLD_LIBRARY_PATH", env)

    def test_clone_error_keeps_stderr_head_and_tail(self) -> None:
        # Regression #2: the [-400:] slice hid the first OPENSSL lines of
        # the failure dump; the message must keep head and tail.
        head = "OPENSSL_3.2.0 not found (required by libcurl)" + "x" * 150
        tail = "fatal: remote helper 'https' aborted session"
        stderr = f"{head}\n{tail}"
        fake = mock.Mock(returncode=128, stdout="", stderr=stderr)

        def _run(argv, **_kwargs):
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            return fake

        with mock.patch(
            "navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"
        ):
            with mock.patch(
                "navin.webui.git_clone.subprocess.run", side_effect=_run
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(GitCloneError) as ctx:
                        clone_git_project(
                            "https://github.com/org/demo.git", parent=tmp
                        )
        message = ctx.exception.message
        self.assertIn("OPENSSL_3.2.0 not found", message)
        self.assertIn("fatal: remote helper", message)


class CloneAuthTest(unittest.TestCase):
    """Tokens reach git through the env only; auth failures are coded 401s."""

    def _clone_failing(self, stderr: str, credential=None) -> GitCloneError:
        fake = mock.Mock(returncode=128, stdout="", stderr=stderr)
        with mock.patch(
            "navin.utils.git_credentials.credential_for_url", return_value=credential
        ), mock.patch(
            "navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"
        ), mock.patch("navin.webui.git_clone.subprocess.run", return_value=fake):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(GitCloneError) as ctx:
                    clone_git_project("https://github.com/org/private.git", parent=tmp)
        return ctx.exception

    def test_terminal_prompt_disabled_is_auth_required(self) -> None:
        err = self._clone_failing(
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled"
        )
        self.assertEqual(err.status, 401)
        self.assertEqual(err.code, "auth_required")
        self.assertEqual(err.host, "github.com")
        self.assertIn("Settings > Git & GitHub", err.message)
        body = err.payload()
        self.assertEqual(body["code"], "auth_required")
        self.assertEqual(body["host"], "github.com")
        self.assertEqual(body["message"], err.message)

    def test_rejected_saved_token_says_update_it(self) -> None:
        from navin.utils.git_credentials import GitCredential

        cred = GitCredential(
            scheme="https", netloc="github.com", host="github.com", kind="github",
            username="x-access-token", source="settings", token="ghp_secret",
        )
        err = self._clone_failing(
            "remote: Invalid username or password.\n"
            "fatal: Authentication failed for 'https://github.com/org/private.git/'",
            credential=cred,
        )
        self.assertEqual((err.status, err.code), (401, "auth_required"))
        self.assertIn("rejected the saved token", err.message)
        self.assertNotIn("ghp_secret", err.message + err.detail)

    def test_ssh_publickey_is_auth_required(self) -> None:
        err = self._clone_failing(
            "git@github.com: Permission denied (publickey).\n"
            "fatal: Could not read from remote repository."
        )
        self.assertEqual((err.status, err.code), (401, "auth_required"))

    def test_not_found_stays_404(self) -> None:
        err = self._clone_failing("remote: Repository not found.\nfatal: repository 'x' not found")
        self.assertEqual((err.status, err.code), (404, "not_found"))
        self.assertIn("Settings > Git & GitHub", err.message)

    def test_token_goes_in_env_never_in_argv(self) -> None:
        from navin.utils.git_credentials import GitCredential

        cred = GitCredential(
            scheme="https", netloc="github.com", host="github.com", kind="github",
            username="x-access-token", source="settings", token="ghp_topsecret",
        )
        captured: dict = {}

        def _run(argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs["env"]
            Path(argv[-1]).mkdir(parents=True, exist_ok=True)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch(
            "navin.utils.git_credentials.credential_for_url", return_value=cred
        ), mock.patch(
            "navin.webui.git_clone.shutil.which", return_value="/usr/bin/git"
        ), mock.patch("navin.webui.git_clone.subprocess.run", side_effect=_run):
            with tempfile.TemporaryDirectory() as tmp:
                clone_git_project("https://github.com/org/private.git", parent=tmp)
        self.assertNotIn("ghp_topsecret", " ".join(captured["argv"]))
        env = captured["env"]
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GCM_INTERACTIVE"], "never")
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])
        index = int(env["GIT_CONFIG_COUNT"]) - 1
        self.assertEqual(
            env[f"GIT_CONFIG_KEY_{index}"], "http.https://github.com/.extraheader"
        )
        import base64

        value = env[f"GIT_CONFIG_VALUE_{index}"]
        self.assertTrue(value.startswith("Authorization: Basic "))
        decoded = base64.b64decode(value.split()[-1]).decode()
        self.assertEqual(decoded, "x-access-token:ghp_topsecret")


if __name__ == "__main__":
    unittest.main()
