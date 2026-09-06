"""Asking for no thinking has to be said out loud.

navin's agent modes route reasoning_effort to "none", and the provider used to
express that by omitting the parameter. Omission is not a refusal: the endpoint
applies its own default, so the model reasoned anyway and the routing decision
never reached the wire.

Measured 2026-09-02 on ``z-ai/glm-5.3-flash`` via OpenRouter: with the
parameter omitted, a prompt that invites arithmetic spends 726-900 completion
tokens, 99% of them reasoning tokens the loop never sees; the same call at the
floor effort spends 120-209 and answers correctly. Reasoning tokens are
generated at normal speed, so that was seconds of silence per step, on every
step of a multi-step turn.

Some endpoints refuse silence outright ("Reasoning is mandatory for this
endpoint and cannot be disabled"), hence a downgrade path rather than a hard
failure: off, then the floor, then the endpoint's default.
"""

from __future__ import annotations

import unittest

from navin.providers.openai_compat_provider import OpenAICompatProvider

MODEL = "z-ai/glm-5.3-flash"
MANDATORY_ERROR = (
    "Error code: 400 - {'error': {'message': 'Reasoning is mandatory for "
    "this endpoint and cannot be disabled.', 'code': 400}}"
)


def _gateway_provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="test",
        api_base="https://openrouter.ai/api/v1",
        default_model=MODEL,
    )


def _reasoning_of(provider: OpenAICompatProvider, effort: str | None) -> dict | None:
    kwargs = provider._build_kwargs(
        [{"role": "user", "content": "hi"}], None, MODEL, 8192, 0.1, effort, None,
    )
    return (kwargs.get("extra_body") or {}).get("reasoning")


class ReasoningOffIsRequestedTest(unittest.TestCase):
    def test_none_asks_the_gateway_to_stop_thinking(self) -> None:
        self.assertEqual(
            _reasoning_of(_gateway_provider(), "none"), {"enabled": False}
        )

    def test_an_explicit_effort_is_left_alone(self) -> None:
        provider = _gateway_provider()
        kwargs = provider._build_kwargs(
            [{"role": "user", "content": "hi"}], None, MODEL, 8192, 0.1, "high", None,
        )
        self.assertEqual(kwargs.get("reasoning_effort"), "high")
        self.assertIsNone((kwargs.get("extra_body") or {}).get("reasoning"))

    def test_no_effort_configured_keeps_the_endpoint_default(self) -> None:
        """Omitting reasoning_effort entirely must stay a no-op."""
        self.assertIsNone(_reasoning_of(_gateway_provider(), None))

    def test_a_direct_vendor_endpoint_is_not_sent_gateway_vocabulary(self) -> None:
        """``reasoning.enabled`` is OpenRouter's shape; a bare endpoint gets
        the OpenAI word for it instead of silence."""
        provider = OpenAICompatProvider(
            api_key="test",
            api_base="https://api.example.invalid/v1",
            default_model=MODEL,
        )
        self.assertIsNone(_reasoning_of(provider, "none"))
        kwargs = provider._build_kwargs(
            [{"role": "user", "content": "hi"}], None, MODEL, 8192, 0.1, "none", None,
        )
        self.assertEqual(kwargs.get("reasoning_effort"), "none")


class MandatoryReasoningDowngradeTest(unittest.TestCase):
    def test_a_refusal_walks_down_the_floors_then_gives_up(self) -> None:
        provider = _gateway_provider()
        exc = RuntimeError(MANDATORY_ERROR)

        self.assertTrue(provider._register_reasoning_rejection(MODEL, exc, "none"))
        self.assertEqual(_reasoning_of(provider, "none"), {"effort": "minimal"})

        self.assertTrue(provider._register_reasoning_rejection(MODEL, exc, "none"))
        self.assertEqual(_reasoning_of(provider, "none"), {"effort": "low"})

        self.assertTrue(provider._register_reasoning_rejection(MODEL, exc, "none"))
        self.assertIsNone(_reasoning_of(provider, "none"))

        # Nothing weaker is left, so the error must surface instead of looping.
        self.assertFalse(provider._register_reasoning_rejection(MODEL, exc, "none"))

    def test_the_downgrade_is_per_model(self) -> None:
        provider = _gateway_provider()
        provider._register_reasoning_rejection(MODEL, RuntimeError(MANDATORY_ERROR), "none")
        other = "openai/gpt-5-mini"
        kwargs = provider._build_kwargs(
            [{"role": "user", "content": "hi"}], None, other, 8192, 0.1, "none", None,
        )
        self.assertEqual(
            (kwargs.get("extra_body") or {}).get("reasoning"), {"enabled": False}
        )

    def test_an_unrelated_error_changes_nothing(self) -> None:
        provider = _gateway_provider()
        self.assertFalse(
            provider._register_reasoning_rejection(
                MODEL, RuntimeError("429 rate limit"), "none"
            )
        )
        self.assertEqual(_reasoning_of(provider, "none"), {"enabled": False})

    def test_a_refusal_on_an_explicit_effort_does_not_touch_the_off_ladder(self) -> None:
        """A 400 while asking for "high" says nothing about how to ask for off."""
        provider = _gateway_provider()
        self.assertFalse(
            provider._register_reasoning_rejection(MODEL, RuntimeError(MANDATORY_ERROR), "high")
        )
        self.assertEqual(_reasoning_of(provider, "none"), {"enabled": False})


if __name__ == "__main__":
    unittest.main()
