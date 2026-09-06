"""Pin the user-facing error strings the WebUI localizes at render time.

The chat stores assistant errors in English; webui/src/lib/agent-errors.ts
matches these exact strings to swap in the UI language. If a wording change
here breaks a test, update the frontend table (and its locales) in the same
commit - otherwise French users silently fall back to English.
"""

from __future__ import annotations

import unittest

from navin.providers.user_facing_errors import user_facing_llm_error

TOOL_USE = (
    "This model could not run with tools enabled "
    "(needed for Agent, Review, Debug, etc.). "
    "Ask mode may still work. Pick a tool-capable model "
    "and try again."
)
VIDEO_AUTH = "Video download failed due to an authentication error. Please try again."
AUDIO_AUTH = "Audio failed due to an authentication error. Please try again."
MEDIA_EMPTY = (
    "The provider finished the job but produced no media. This is "
    "usually a content-policy block (brand names, logos, real product "
    "UI, people) or a transient provider failure. Rephrase the prompt "
    "with neutral wording and try again, or switch to another media "
    "model in Settings."
)
RATE_LIMIT = (
    "The model is at capacity right now. Navin already retried "
    "automatically. Wait a few seconds and send your message again, "
    "or pick another model."
)
QUOTA = (
    "Your own API key is out of credit. Top up at the provider, "
    "or switch to a Navin plan model."
)
CONNECTION = (
    "Could not reach the model provider. Navin already retried "
    "automatically. Check your internet connection, and any VPN, "
    "proxy or firewall that could block the request, then send your "
    "message again."
)
UPSTREAM = (
    "The model provider hit a temporary error. Navin already retried "
    "automatically. Send your message again, or pick another model."
)
CONTEXT = (
    "This turn no longer fits in the model's context window. Start a "
    "new conversation, or pick a model with a larger window."
)
NOT_FOUND = (
    "This model is no longer available. Pick another model from the "
    "selector and try again."
)
DEFAULT = "Sorry, I encountered an error calling the AI model."


class UserFacingErrorSentinelsTest(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(user_facing_llm_error(""), DEFAULT)
        self.assertEqual(user_facing_llm_error(None), DEFAULT)

    def test_tool_use(self) -> None:
        self.assertEqual(
            user_facing_llm_error("404 No endpoints found that support tool use"),
            TOOL_USE,
        )

    def test_video_auth(self) -> None:
        self.assertEqual(
            user_facing_llm_error("No user or org id found in auth cookie"),
            VIDEO_AUTH,
        )

    def test_audio_auth(self) -> None:
        self.assertEqual(
            user_facing_llm_error("audio synthesis failed: 401 unauthorized"),
            AUDIO_AUTH,
        )

    def test_media_empty(self) -> None:
        self.assertEqual(
            user_facing_llm_error("video job completed with no output"),
            MEDIA_EMPTY,
        )

    def test_rate_limit(self) -> None:
        self.assertEqual(user_facing_llm_error("429 rate limit exceeded"), RATE_LIMIT)

    def test_quota(self) -> None:
        self.assertEqual(
            user_facing_llm_error("insufficient credit for this request"),
            QUOTA,
        )

    def test_connection(self) -> None:
        self.assertEqual(user_facing_llm_error("Connection error."), CONNECTION)

    def test_default_is_not_prefixed_with_error(self) -> None:
        # The runner used to pass the fallback through user_facing_llm_error a
        # second time, which prefixed "Error: " and broke WebUI localization.
        self.assertEqual(user_facing_llm_error(DEFAULT), DEFAULT)
        self.assertEqual(
            user_facing_llm_error(f"Error: {DEFAULT}"),
            DEFAULT,
        )

    def test_openrouter_502_dict_is_upstream_not_generic(self) -> None:
        raw = (
            "Error: {'code': 502, 'message': 'Upstream error from Nvidia: "
            "Internal server error', 'metadata': "
            "{'error_type': 'provider_unavailable'}}"
        )
        self.assertEqual(user_facing_llm_error(raw), UPSTREAM)
        self.assertNotIn("nvidia", user_facing_llm_error(raw).lower())

    def test_provider_returned_error_502_is_upstream(self) -> None:
        raw = (
            "{'message': 'Provider returned error', 'code': 502, "
            "'metadata': {'raw': 'internal error', "
            "'error_type': 'provider_unavailable'}}"
        )
        self.assertEqual(user_facing_llm_error(raw), UPSTREAM)

    def test_context_length_is_rewritten(self) -> None:
        self.assertEqual(
            user_facing_llm_error(
                "Error: This model's maximum context length was exceeded"
            ),
            CONTEXT,
        )

    def test_unknown_model_404_is_rewritten(self) -> None:
        self.assertEqual(
            user_facing_llm_error("Error: 404 unknown model google/gemini-1.5-flash"),
            NOT_FOUND,
        )


if __name__ == "__main__":
    unittest.main()
