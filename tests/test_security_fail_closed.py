"""Fail-closed security posture: token issuance and strict sandbox wiring.

Two audit findings shared the same shape - a guard that warned and then
proceeded anyway:

* the token-issue route emitted connection tokens with an empty secret,
  logging a warning any network peer never sees;
* the native sandbox printed "not confined" to stderr and ran the command
  unconfined, and the wrapper never passed --strict.

These pin the closed behaviour: secretless token issue is loopback-only,
and the strict security profile makes the sandbox refuse instead of
degrade (including on Windows, which has no OS sandbox at all).
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from navin.agent.tools.shell import ExecTool
from navin.config.schema import ToolsConfig
from navin.webui.ws_http import token_issue_refusal


class TokenIssueRefusalTest(unittest.TestCase):
    def test_a_valid_secret_lets_the_request_through(self) -> None:
        self.assertIsNone(token_issue_refusal(
            secret="s3cret",
            is_local_browser=False,
            bind_host="0.0.0.0",
            headers={"Authorization": "Bearer s3cret"},
        ))

    def test_a_wrong_secret_is_unauthorized(self) -> None:
        refusal = token_issue_refusal(
            secret="s3cret",
            is_local_browser=False,
            bind_host="0.0.0.0",
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(refusal, (401, "Unauthorized"))

    def test_without_a_secret_only_loopback_to_loopback_is_served(self) -> None:
        self.assertIsNone(token_issue_refusal(
            secret="",
            is_local_browser=True,
            bind_host="127.0.0.1",
            headers={},
        ))

    def test_without_a_secret_a_remote_peer_is_refused_not_warned(self) -> None:
        refusal = token_issue_refusal(
            secret="",
            is_local_browser=False,
            bind_host="127.0.0.1",
            headers={},
        )
        assert refusal is not None
        self.assertEqual(refusal[0], 403)

    def test_without_a_secret_a_non_loopback_bind_is_refused_even_for_local_peers(self) -> None:
        # Bound beyond loopback, a local-looking peer can be a proxy relaying
        # the outside world - the same reasoning as bootstrap_refusal.
        refusal = token_issue_refusal(
            secret="",
            is_local_browser=True,
            bind_host="0.0.0.0",
            headers={},
        )
        assert refusal is not None
        self.assertEqual(refusal[0], 403)


def _ctx(profile: str | None) -> SimpleNamespace:
    config = ToolsConfig()
    config.security_profile = profile
    return SimpleNamespace(config=config, workspace="/tmp", bus=None)


class ExecToolStrictWiringTest(unittest.TestCase):
    def test_the_strict_profile_turns_on_fail_closed_sandboxing(self) -> None:
        tool = ExecTool.create(_ctx("strict"))
        self.assertTrue(tool.sandbox_strict)

    def test_other_profiles_keep_the_fail_open_default(self) -> None:
        for profile in (None, "autonomous", "assisted"):
            with self.subTest(profile=profile):
                tool = ExecTool.create(_ctx(profile))
                self.assertFalse(tool.sandbox_strict)

    def test_strict_reaches_the_wrapper_as_a_flag(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            tool = ExecTool(working_dir=ws, sandbox="native", sandbox_strict=True)
            with mock.patch(
                "navin.agent.tools.shell.wrap_command",
                return_value="echo wrapped",
            ) as wrapper:
                asyncio.run(tool.execute(command="echo hi"))
            wrapper.assert_called_once()
            self.assertTrue(wrapper.call_args.kwargs.get("strict"))

    def test_on_windows_strict_refuses_instead_of_running_unsandboxed(self) -> None:
        with tempfile.TemporaryDirectory() as ws:
            tool = ExecTool(working_dir=ws, sandbox="native", sandbox_strict=True)
            with mock.patch("navin.agent.tools.shell._IS_WINDOWS", True):
                result = asyncio.run(tool.execute(command="echo hi"))
            self.assertTrue(getattr(result, "is_error", False))
            self.assertIn("strict security profile", str(result))


if __name__ == "__main__":
    unittest.main()
