"""User-facing LLM errors must never leak OpenRouter branding."""

from __future__ import annotations

import unittest

from navin.providers.user_facing_errors import user_facing_llm_error


class UserFacingLlmErrorTest(unittest.TestCase):
    def test_openrouter_tool_routing_404_is_rewritten(self):
        raw = (
            "Error: {'message': 'No endpoints found that support tool use. "
            'Try disabling "apply_patch". To learn more about provider routing, '
            "visit: https://openrouter.ai/docs/guides/routing/provider-selection', "
            "'code': 404}"
        )
        out = user_facing_llm_error(raw)
        self.assertNotIn("openrouter", out.lower())
        self.assertNotIn("apply_patch", out.lower())
        self.assertNotIn("http", out.lower())
        self.assertIn("tools", out.lower())

    def test_openrouter_name_is_scrubbed(self):
        out = user_facing_llm_error("Error: openrouter timeout after 30s")
        self.assertNotIn("openrouter", out.lower())
        self.assertIn("model provider", out.lower())

    def test_urls_are_stripped(self):
        out = user_facing_llm_error(
            "Error: see https://openrouter.ai/docs/foo for details"
        )
        self.assertNotIn("http", out.lower())
        self.assertNotIn("openrouter", out.lower())

    def test_video_auth_cookie_401_is_rewritten(self):
        raw = (
            'OpenRouter video download failed: '
            '{"error":{"message":"No user or org id found in auth cookie","code":401}}'
        )
        out = user_facing_llm_error(raw)
        self.assertNotIn("openrouter", out.lower())
        self.assertNotIn("auth cookie", out.lower())
        self.assertIn("authentication", out.lower())

    def test_rate_limit_does_not_call_the_model_free(self):
        # Subscribers on DeepSeek V4 Flash used to see "This free model is
        # busy" because every 429 was rewritten that way. The copy must stay
        # plan-neutral.
        out = user_facing_llm_error("Error: 429 rate limit exceeded")
        self.assertNotIn("free", out.lower())
        self.assertIn("capacity", out.lower())

    def test_connection_failure_tells_the_user_what_to_check(self):
        # The SDK message alone ("Connection error.") carries no status and no
        # body, so the bubble used to leave the user with nothing to act on.
        out = user_facing_llm_error("Error calling LLM: Connection error.")
        self.assertIn("could not reach", out.lower())
        self.assertIn("proxy", out.lower())

    def test_dns_failure_is_treated_as_a_connection_failure(self):
        out = user_facing_llm_error(
            "Error calling LLM: [Errno -2] Name or service not known"
        )
        self.assertIn("could not reach", out.lower())

    def test_upstream_body_mentioning_a_connection_keeps_its_message(self):
        # A 500 from the provider is not a local connectivity problem: telling
        # the user to check their VPN would send them down the wrong path.
        out = user_facing_llm_error(
            "Error: upstream model host dropped the pooled connection, retry"
        )
        self.assertNotIn("could not reach", out.lower())
        self.assertIn("upstream model host", out.lower())

    def test_media_job_with_no_output_gets_actionable_guidance(self):
        # Veo/OpenRouter: job ends "failed" with this detail when the content
        # policy blocks the prompt (brands, product UI) or the render glitched.
        raw = (
            "Video generation failed: Video generation completed with no "
            "output (content policy violation)"
        )
        out = user_facing_llm_error(raw)
        self.assertIn("content-policy", out.lower())
        self.assertIn("rephrase", out.lower())
        self.assertNotIn("openrouter", out.lower())

    def test_dict_dump_502_is_not_the_generic_fallback(self) -> None:
        raw = (
            "{'message': 'Provider returned error', 'code': 502, "
            "'metadata': {'error_type': 'provider_unavailable'}}"
        )
        out = user_facing_llm_error(raw)
        self.assertIn("temporary error", out.lower())
        self.assertNotIn("openrouter", out.lower())
        self.assertNotIn("{", out)

    def test_http_status_is_read_from_a_dict_body(self) -> None:
        from navin.providers.user_facing_errors import http_status_from_error_payload

        self.assertEqual(
            http_status_from_error_payload(
                {"message": "Provider returned error", "code": 502}
            ),
            502,
        )
        self.assertEqual(
            http_status_from_error_payload(
                "{'message': 'Provider returned error', 'code': 429}"
            ),
            429,
        )
        self.assertEqual(
            http_status_from_error_payload("Error code: 503 - overloaded"),
            503,
        )


if __name__ == "__main__":
    unittest.main()
