"""A red verify must tell the model *what* failed, not only that it failed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.runner import AgentRunSpec, _verify_failure_summary
from navin.quality.verification_log import (
    last_verification_summary,
    record_verification,
)
from navin.utils.runtime import build_verify_failed_message


class VerifyFailurePromptTest(unittest.TestCase):
    def test_the_nudge_includes_the_recorded_summary(self) -> None:
        msg = build_verify_failed_message(last_summary="test_login.py::test_ok FAILED")
        self.assertIn("verify action=check", msg["content"])
        self.assertIn("test_login.py::test_ok FAILED", msg["content"])

    def test_no_summary_keeps_the_generic_nudge(self) -> None:
        msg = build_verify_failed_message()
        self.assertNotIn("Last verification output", msg["content"])

    def test_the_log_digest_is_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_verification(root, source="lint", ok=True, summary="clean")
            record_verification(
                root, source="verify", ok=False, summary="2 tests red",
            )
            digest = last_verification_summary(root)
        self.assertIn("verify FAIL: 2 tests red", digest)
        self.assertLess(digest.index("verify FAIL"), digest.index("lint PASS"))

    def test_the_runner_prefers_the_workspace_log_over_a_tool_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record_verification(
                root, source="verify", ok=False, summary="login_test exploded",
            )
            spec = AgentRunSpec(
                initial_messages=[],
                tools=None,  # type: ignore[arg-type]
                runtime=None,  # type: ignore[arg-type]
                max_iterations=1,
                max_tool_result_chars=100,
                workspace=root,
            )
            summary = _verify_failure_summary(
                spec,
                [{"name": "verify", "detail": "stale event"}],
            )
        self.assertIn("login_test exploded", summary)


if __name__ == "__main__":
    unittest.main()
