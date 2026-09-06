"""The WebUI sign-in must publish the authorize URL instead of blocking on it.

Regression cover for the report where `/api/settings/provider/oauth-login`
spun for 120 s and then answered 500 `Authorization code not found`: the kit
had printed the URL to a callback the WebUI threw away, and no browser ever
opened on the user's machine.
"""

from __future__ import annotations

import threading
import unittest

from navin.webui.oauth_login import (
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SIGNED_IN,
    OAuthLoginManager,
    extract_authorize_url,
)


class _Token:
    def __init__(self, access: str) -> None:
        self.access = access


class ExtractAuthorizeUrlTest(unittest.TestCase):
    def test_pulls_the_url_out_of_the_rich_markup(self):
        line = "[cyan]A browser window will open for login.[/cyan]"
        self.assertIsNone(extract_authorize_url(line))
        url = "https://auth.openai.com/oauth/authorize?client_id=x&state=y"
        self.assertEqual(extract_authorize_url(url), url)
        self.assertEqual(extract_authorize_url(f"[dim]{url}[/dim]"), url)

    def test_ignores_empty_input(self):
        self.assertIsNone(extract_authorize_url(""))


class OAuthLoginManagerTest(unittest.TestCase):
    def test_returns_the_url_without_waiting_for_the_callback(self):
        manager = OAuthLoginManager()
        release = threading.Event()
        url = "https://auth.openai.com/oauth/authorize?state=abc"

        def runner(print_fn, prompt_fn):
            print_fn(f"[dim]{url}[/dim]")
            release.wait(timeout=5)
            return _Token("access-token")

        state = manager.start("openai_codex", runner, wait_seconds=5)
        # The slow part is still running: the caller is not blocked by it.
        self.assertEqual(state["status"], STATUS_PENDING)
        self.assertEqual(state["authorize_url"], url)

        release.set()
        session = manager._sessions["openai_codex"]
        session.finished.wait(timeout=5)
        self.assertEqual(manager.snapshot("openai_codex")["status"], STATUS_SIGNED_IN)

    def test_a_failed_flow_surfaces_its_message(self):
        manager = OAuthLoginManager()

        def runner(print_fn, prompt_fn):
            raise RuntimeError("Authorization code not found.")

        state = manager.start("openai_codex", runner, wait_seconds=5)
        self.assertEqual(state["status"], STATUS_ERROR)
        self.assertEqual(state["error"], "Authorization code not found.")

    def test_a_pasted_callback_url_unblocks_the_prompt(self):
        manager = OAuthLoginManager()
        seen: list[str] = []

        def runner(print_fn, prompt_fn):
            print_fn("https://auth.openai.com/oauth/authorize?state=abc")
            seen.append(prompt_fn("paste the callback URL:"))
            return _Token("access-token")

        manager.start("openai_codex", runner, wait_seconds=5)
        state = manager.submit_code("openai_codex", "  http://localhost:1455/?code=xyz  ")
        self.assertEqual(seen, ["http://localhost:1455/?code=xyz"])
        self.assertEqual(state["status"], STATUS_SIGNED_IN)

    def test_a_second_click_rejoins_the_running_sign_in(self):
        manager = OAuthLoginManager()
        release = threading.Event()
        starts: list[int] = []

        def runner(print_fn, prompt_fn):
            starts.append(1)
            print_fn("https://auth.openai.com/oauth/authorize?state=abc")
            release.wait(timeout=5)
            return _Token("access-token")

        manager.start("openai_codex", runner, wait_seconds=5)
        manager.start("openai_codex", runner, wait_seconds=1)
        self.assertEqual(len(starts), 1)
        release.set()

    def test_snapshot_without_a_sign_in_is_an_error_not_a_crash(self):
        manager = OAuthLoginManager()
        self.assertEqual(manager.snapshot("openai_codex")["status"], STATUS_ERROR)

    def test_submitting_a_code_with_no_flow_raises(self):
        manager = OAuthLoginManager()
        with self.assertRaises(LookupError):
            manager.submit_code("openai_codex", "code")


if __name__ == "__main__":
    unittest.main()
