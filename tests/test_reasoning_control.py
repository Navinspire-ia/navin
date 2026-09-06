"""Every provider says "no thinking" in words its endpoint understands.

The rule under test: when the agent loop routes a step to
``reasoning_effort="none"``, the request that leaves navin must carry an
explicit "off" (or the documented floor), on every wire. Silence is the bug -
the endpoint fills it with its own default, which for GPT-5, Gemini 2.5+,
GLM, Qwen3 and Ollama's thinking models means a full chain of thought the
user never sees and always waits for.

Two kinds of models are allowed to receive nothing: those that never reason
(GPT-4o, Llama) and those that reason whatever we say and reject the knob
(Grok 4, Magistral). Both are named in ``reasoning_control`` so the exemption
is a decision, not an oversight.
"""

from __future__ import annotations

import unittest

from navin.providers import registry
from navin.providers.anthropic_provider import AnthropicProvider
from navin.providers.bedrock_provider import BedrockProvider
from navin.providers.openai_compat_provider import OpenAICompatProvider
from navin.providers.reasoning_control import (
    ALWAYS,
    ALWAYS_TUNABLE,
    DEFAULT_OFF,
    DEFAULT_ON,
    REJECT_UNSUPPORTED,
    REJECT_VALUE,
    WIRE_RESPONSES,
    ReasoningOffNegotiator,
    always_reasons,
    classify_rejection,
    off_shapes,
    reasoning_default,
)

MESSAGES = [{"role": "user", "content": "hi"}]


def _explicit_off(kwargs: dict) -> str | None:
    """Name the "off" signal carried by a chat-completions request, if any."""
    effort = kwargs.get("reasoning_effort")
    if effort in ("none", "minimal", "low"):
        return f"reasoning_effort={effort}"
    extra = kwargs.get("extra_body") or {}
    reasoning = extra.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("enabled") is False:
            return "reasoning.enabled=false"
        if reasoning.get("effort") in ("none", "minimal", "low"):
            return f"reasoning.effort={reasoning['effort']}"
    thinking = extra.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        return "thinking.type=disabled"
    if extra.get("enable_thinking") is False:
        return "enable_thinking=false"
    if extra.get("reasoning_split") is False:
        return "reasoning_split=false"
    template = extra.get("chat_template_kwargs") or {}
    if template.get("enable_thinking") is False:
        return "chat_template_kwargs.enable_thinking=false"
    return None


class FamilyDefaultsTest(unittest.TestCase):
    def test_families_that_think_unless_told_not_to(self) -> None:
        for model in (
            "gpt-5.1", "openai/gpt-5-mini", "o3", "gpt-oss-120b",
            "gemini-3-flash-preview", "google/gemini-2.5-flash",
            "z-ai/glm-5.3-flash", "qwen3.8-max", "qwen3:8b",
            "deepseek-v4-flash", "kimi-k2.5", "grok-3-mini",
            "llama-3.3-nemotron-super-49b", "grok-4-fast-non-reasoning",
            # Mistral's chat line takes reasoning_effort, so "none" is said.
            "mistral-large-latest", "mistral-medium-latest",
            "some-model-nobody-has-met",
        ):
            with self.subTest(model=model):
                self.assertEqual(reasoning_default(model), DEFAULT_ON)

    def test_families_that_do_not_think_unless_asked(self) -> None:
        for model in (
            "gpt-4o", "gpt-4.1-mini", "chatgpt-4o-latest", "llama-3.3-70b",
            "meta-llama/llama-4-maverick", "codestral",
            "gemini-2.0-flash", "qwen2.5-72b", "kimi-k2-0905", "grok-3",
            "deepseek-chat", "command-r-plus", "glm-4-flash",
        ):
            with self.subTest(model=model):
                self.assertEqual(reasoning_default(model), DEFAULT_OFF)

    def test_families_that_think_whatever_we_say(self) -> None:
        for model in (
            "grok-4", "grok-4-0709", "grok-4-1-fast-reasoning", "grok-code-fast-1",
            "magistral-medium", "deepseek-r1", "deepseek-reasoner", "kimi-k2-thinking",
            "qwq-32b", "sonar-reasoning-pro", "MiniMax-M3", "minimax/minimax-m3",
        ):
            with self.subTest(model=model):
                self.assertEqual(reasoning_default(model), ALWAYS)

    def test_families_that_always_think_but_take_a_depth(self) -> None:
        """xAI: reasoning_effort came back with Grok 4.3 (low..high, xhigh on 4.6),
        reasoning cannot be disabled. Dropping the knob left them at "high"."""
        for model in (
            "x-ai/grok-4.6", "grok-4-6", "grok-4.5", "grok-4-3", "grok-4.20-multi-agent",
            "sonar-deep-research",
        ):
            with self.subTest(model=model):
                self.assertEqual(reasoning_default(model), ALWAYS_TUNABLE)
                self.assertFalse(always_reasons(model))

    def test_always_tunable_floor_is_low_not_silence(self) -> None:
        for model in ("grok-4-6", "grok-4.5"):
            with self.subTest(model=model):
                self.assertEqual(
                    off_shapes(model, spec_name="xai"), [{"reasoning_effort": "low"}]
                )
                self.assertEqual(
                    off_shapes(model, wire=WIRE_RESPONSES), [{"reasoning": {"effort": "low"}}]
                )


class OffShapesTest(unittest.TestCase):
    def test_openrouter_uses_its_own_vocabulary(self) -> None:
        shapes = off_shapes("z-ai/glm-5.3-flash", base_url="https://openrouter.ai/api/v1")
        self.assertEqual(shapes[0], {"extra_body": {"reasoning": {"enabled": False}}})
        self.assertEqual(shapes[-1], {"extra_body": {"reasoning": {"effort": "low"}}})

    def test_openai_vocabulary_walks_none_minimal_low(self) -> None:
        self.assertEqual(
            [s["reasoning_effort"] for s in off_shapes("gpt-5", base_url="https://api.openai.com/v1")],
            ["none", "minimal", "low"],
        )

    def test_responses_api_puts_it_under_reasoning(self) -> None:
        shapes = off_shapes("gpt-5.1", wire=WIRE_RESPONSES)
        self.assertEqual(shapes[0], {"reasoning": {"effort": "none"}})

    def test_documented_vocabularies_skip_the_unknown_word(self) -> None:
        for spec_name, model in (
            ("gemini", "gemini-3-flash-preview"),
            ("groq", "qwen/qwen3-32b"),
            ("ollama", "qwen3:8b"),
        ):
            with self.subTest(spec=spec_name):
                efforts = [s["reasoning_effort"] for s in off_shapes(model, spec_name=spec_name)]
                self.assertEqual(efforts, ["none", "low"])

    def test_grok_mini_has_only_a_floor_and_grok_4_nothing(self) -> None:
        self.assertEqual(
            off_shapes("grok-3-mini", spec_name="xai"), [{"reasoning_effort": "low"}]
        )
        self.assertEqual(off_shapes("grok-4", spec_name="xai"), [])

    def test_models_that_never_reason_get_nothing(self) -> None:
        self.assertEqual(off_shapes("gpt-4o", base_url="https://api.openai.com/v1"), [])
        self.assertEqual(off_shapes("gpt-4o", wire=WIRE_RESPONSES), [])


class RejectionClassificationTest(unittest.TestCase):
    def test_openai_refusing_the_value(self) -> None:
        self.assertEqual(
            classify_rejection(
                "Error code: 400 - Invalid value: 'none'. Supported values are: "
                "'minimal', 'low', 'medium', and 'high'. (param: reasoning_effort)"
            ),
            REJECT_VALUE,
        )

    def test_openai_not_knowing_the_parameter(self) -> None:
        self.assertEqual(
            classify_rejection("400 Unrecognized request argument supplied: reasoning_effort"),
            REJECT_UNSUPPORTED,
        )

    def test_xai_not_knowing_the_parameter(self) -> None:
        self.assertEqual(
            classify_rejection("400 Argument not supported on this model: reasoning_effort"),
            REJECT_UNSUPPORTED,
        )

    def test_openrouter_mandatory_reasoning(self) -> None:
        self.assertEqual(
            classify_rejection("Reasoning is mandatory for this endpoint and cannot be disabled."),
            REJECT_VALUE,
        )

    def test_errors_about_something_else_are_not_ours(self) -> None:
        self.assertIsNone(classify_rejection("429 rate limit exceeded"))
        self.assertIsNone(classify_rejection("400 'temperature' does not support 0.2"))
        self.assertIsNone(classify_rejection("connection reset by peer"))


class NegotiatorTest(unittest.TestCase):
    SHAPES = [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_a_refused_value_steps_down_one_floor(self) -> None:
        negotiator = ReasoningOffNegotiator()
        self.assertEqual(negotiator.shape("m", self.SHAPES), {"a": 1})
        self.assertTrue(negotiator.register_rejection("m", "invalid value for reasoning", self.SHAPES))
        self.assertEqual(negotiator.shape("m", self.SHAPES), {"b": 2})

    def test_an_unknown_parameter_ends_the_attempts(self) -> None:
        negotiator = ReasoningOffNegotiator()
        self.assertTrue(
            negotiator.register_rejection(
                "m", "unrecognized request argument supplied: reasoning_effort", self.SHAPES
            )
        )
        self.assertIsNone(negotiator.shape("m", self.SHAPES))
        # Already silent: a further refusal is somebody else's error.
        self.assertFalse(negotiator.register_rejection("m", "reasoning invalid", self.SHAPES))

    def test_a_documented_knob_keeps_walking_on_unsupported_wording(self) -> None:
        negotiator = ReasoningOffNegotiator()
        negotiator.register_rejection(
            "m", "reasoning_effort none is not supported for this model", self.SHAPES,
            knob_known=True,
        )
        self.assertEqual(negotiator.shape("m", self.SHAPES), {"b": 2})

    def test_unrelated_errors_change_nothing(self) -> None:
        negotiator = ReasoningOffNegotiator()
        self.assertFalse(negotiator.register_rejection("m", "502 bad gateway", self.SHAPES))
        self.assertEqual(negotiator.shape("m", self.SHAPES), {"a": 1})


class NegotiatorMemoryTest(unittest.TestCase):
    """A refusal learned in one process is not paid again in the next."""

    SHAPES = [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_a_new_negotiator_for_the_same_endpoint_starts_where_the_old_one_stopped(self) -> None:
        first = ReasoningOffNegotiator(scope="https://openrouter.ai/api/v1")
        first.register_rejection("z-ai/glm", "invalid value for reasoning", self.SHAPES)
        self.assertEqual(first.shape("z-ai/glm", self.SHAPES), {"b": 2})
        # Simulates a gateway restart or a fresh `navin agent -m` process.
        second = ReasoningOffNegotiator(scope="https://openrouter.ai/api/v1")
        self.assertEqual(second.shape("z-ai/glm", self.SHAPES), {"b": 2})

    def test_the_same_model_behind_another_endpoint_is_negotiated_on_its_own(self) -> None:
        via_router = ReasoningOffNegotiator(scope="https://openrouter.ai/api/v1")
        via_router.register_rejection("glm", "invalid value for reasoning", self.SHAPES)
        direct = ReasoningOffNegotiator(scope="https://api.z.ai/api/coding/paas/v4")
        self.assertEqual(direct.shape("glm", self.SHAPES), {"a": 1})

    def test_forgetting_wipes_the_memory(self) -> None:
        from navin.providers.reasoning_control import forget_reasoning_negotiations

        taught = ReasoningOffNegotiator(scope="ep")
        taught.register_rejection("m", "unrecognized request argument supplied: reasoning", self.SHAPES)
        self.assertTrue(ReasoningOffNegotiator(scope="ep").is_omitting("m", self.SHAPES))
        forget_reasoning_negotiations()
        self.assertEqual(ReasoningOffNegotiator(scope="ep").shape("m", self.SHAPES), {"a": 1})


# One reasoning-capable model per provider spec. A spec missing here fails
# the contract test below on purpose: adding a provider means deciding how
# it is told to stop thinking.
REPRESENTATIVE_MODELS = {
    "custom": "my-thinking-model",
    "openrouter": "z-ai/glm-5.3-flash",
    "navin": "google/gemini-3-flash-preview",
    "opencode": "opencode/gpt-5.1",
    "opencode_zen": "gpt-5.1",
    "opencode_go": "glm-5",
    "huggingface": "Qwen/Qwen3-235B-A22B",
    "siliconflow": "Qwen/Qwen3-32B",
    "novita": "deepseek/deepseek-v3.2",
    "openai": "gpt-5.1",
    "github_copilot": "gpt-5",
    "xai": "grok-3-mini",
    "xai_oauth": "grok-3-mini",
    "qwen": "qwen3.8-max",
    "dashscope": "qwen3.8-max",
    "deepseek": "deepseek-chat",
    "gemini": "gemini-3-flash-preview",
    "zai": "glm-5",
    "zhipu": "glm-5",
    "moonshot": "kimi-k2.5",
    "minimax": "minimax-m2.5",
    "volcengine": "doubao-seed-1.8",
    "hunyuan": "hunyuan-t1",
    "qianfan": "ernie-x1",
    "stepfun": "step-3",
    "mistral": "mistral-medium-latest",
    "vllm": "Qwen/Qwen3-32B",
    "ollama": "qwen3:8b",
    "lm_studio": "qwen3-8b",
    "atomic_chat": "qwen3-8b",
    "omniroute": "omniroute/gpt-5.1",
    "ovms": "qwen3-8b",
    "nvidia": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "groq": "qwen/qwen3-32b",
}
# Backends whose request shape is not the OpenAI chat-completions dict.
NON_CHAT_BACKENDS = {"anthropic", "azure_openai", "bedrock", "openai_codex"}


class EveryChatProviderSaysOffTest(unittest.TestCase):
    def test_every_openai_compatible_spec_has_a_representative(self) -> None:
        missing = [
            spec.name
            for spec in registry.PROVIDERS
            if spec.backend not in NON_CHAT_BACKENDS
            and not getattr(spec, "is_transcription_only", False)
            and spec.name not in REPRESENTATIVE_MODELS
        ]
        self.assertEqual(missing, [], "decide how these providers are told to stop thinking")

    def test_none_is_explicit_on_every_chat_wire(self) -> None:
        for spec in registry.PROVIDERS:
            if spec.backend in NON_CHAT_BACKENDS or getattr(spec, "is_transcription_only", False):
                continue
            model = REPRESENTATIVE_MODELS[spec.name]
            provider = OpenAICompatProvider(
                api_base=spec.default_api_base or "https://api.example.invalid/v1",
                default_model=model,
                spec=spec,
            )
            kwargs = provider._build_kwargs(MESSAGES, None, model, 4096, 0.2, "none", None)
            with self.subTest(provider=spec.name, model=model):
                self.assertIsNotNone(
                    _explicit_off(kwargs),
                    f"{spec.name}: 'none' left the request silent about reasoning: {kwargs}",
                )

    def test_models_that_always_reason_are_not_sent_the_knob(self) -> None:
        provider = OpenAICompatProvider(
            api_base="https://api.x.ai/v1",
            default_model="grok-4",
            spec=registry.find_by_name("xai"),
        )
        for effort in ("none", "high"):
            kwargs = provider._build_kwargs(MESSAGES, None, "grok-4", 4096, 0.2, effort, None)
            with self.subTest(effort=effort):
                self.assertNotIn("reasoning_effort", kwargs)
                self.assertNotIn("reasoning", kwargs.get("extra_body") or {})

    def test_mistral_none_reaches_the_wire(self) -> None:
        """The remap produced "none" and a later line used to drop it."""
        provider = OpenAICompatProvider(
            api_base="https://api.mistral.ai/v1",
            default_model="mistral-medium-latest",
            spec=registry.find_by_name("mistral"),
        )
        for effort in ("none", "low", "minimal"):
            kwargs = provider._build_kwargs(
                MESSAGES, None, "mistral-medium-latest", 4096, 0.2, effort, None,
            )
            with self.subTest(effort=effort):
                self.assertEqual(kwargs.get("reasoning_effort"), "none")

    def test_no_configured_effort_still_leaves_the_default_alone(self) -> None:
        provider = OpenAICompatProvider(
            api_base="https://api.openai.com/v1", default_model="gpt-5.1",
            spec=registry.find_by_name("openai"),
        )
        kwargs = provider._build_kwargs(MESSAGES, None, "gpt-5.1", 4096, 0.2, None, None)
        self.assertIsNone(_explicit_off(kwargs))

    def test_a_learned_unknown_parameter_is_never_sent_again(self) -> None:
        provider = OpenAICompatProvider(
            api_base="https://api.example.invalid/v1", default_model="house-model",
        )
        exc = RuntimeError("400 Unrecognized request argument supplied: reasoning_effort")
        self.assertTrue(provider._register_reasoning_rejection("house-model", exc, "high"))
        kwargs = provider._build_kwargs(MESSAGES, None, "house-model", 4096, 0.2, "high", None)
        self.assertNotIn("reasoning_effort", kwargs)
        # And the off ladder is skipped for it too: the endpoint has no knob.
        kwargs = provider._build_kwargs(MESSAGES, None, "house-model", 4096, 0.2, "none", None)
        self.assertNotIn("reasoning_effort", kwargs)


class ResponsesApiSaysOffTest(unittest.TestCase):
    def _provider(self) -> OpenAICompatProvider:
        return OpenAICompatProvider(
            api_base="https://api.openai.com/v1", default_model="gpt-5.1",
            spec=registry.find_by_name("openai"),
        )

    def test_gpt5_none_is_spelled_out(self) -> None:
        body = self._provider()._build_responses_body(
            MESSAGES, None, "gpt-5.1", 4096, 0.2, "none", None,
        )
        self.assertEqual(body.get("reasoning"), {"effort": "none"})
        self.assertNotIn("include", body)

    def test_an_explicit_effort_is_unchanged(self) -> None:
        body = self._provider()._build_responses_body(
            MESSAGES, None, "gpt-5.1", 4096, 0.2, "high", None,
        )
        self.assertEqual(body.get("reasoning"), {"effort": "high"})
        self.assertEqual(body.get("include"), ["reasoning.encrypted_content"])

    def test_a_refused_none_steps_down_to_minimal(self) -> None:
        provider = self._provider()
        exc = RuntimeError(
            "Error code: 400 - Invalid value: 'none'. Supported values are: "
            "'minimal', 'low', 'medium', and 'high'."
        )
        self.assertTrue(provider._register_reasoning_rejection("gpt-5.1", exc, "none"))
        body = provider._build_responses_body(MESSAGES, None, "gpt-5.1", 4096, 0.2, "none", None)
        self.assertEqual(body.get("reasoning"), {"effort": "minimal"})


class AnthropicSaysOffTest(unittest.TestCase):
    def _kwargs(self, model: str, effort: str | None) -> dict:
        provider = AnthropicProvider(api_key="test", default_model=model)
        return provider._build_kwargs(MESSAGES, None, model, 4096, 0.2, effort, None)

    def test_none_and_minimal_disable_thinking_explicitly(self) -> None:
        for effort in ("none", "minimal"):
            with self.subTest(effort=effort):
                kwargs = self._kwargs("claude-sonnet-4-6", effort)
                self.assertEqual(kwargs.get("thinking"), {"type": "disabled"})
                self.assertNotIn("output_config", kwargs.get("extra_body") or {})

    def test_no_configured_effort_sends_no_thinking_field(self) -> None:
        self.assertNotIn("thinking", self._kwargs("claude-sonnet-4-6", None))

    def test_models_predating_the_field_are_not_sent_it(self) -> None:
        self.assertNotIn("thinking", self._kwargs("claude-3-5-sonnet-20241022", "none"))

    def test_high_still_turns_thinking_on(self) -> None:
        kwargs = self._kwargs("claude-sonnet-4-6", "high")
        self.assertEqual(kwargs.get("thinking"), {"type": "adaptive"})


class BedrockSaysOffTest(unittest.TestCase):
    def _kwargs(self, model: str, effort: str | None, max_tokens: int = 4096) -> dict:
        provider = BedrockProvider(default_model=model, region="us-east-1", client=object())
        return provider._build_kwargs(MESSAGES, None, model, max_tokens, 0.2, effort, None)

    def test_none_disables_thinking_on_every_claude_that_knows_the_field(self) -> None:
        for model in (
            "us.anthropic.claude-sonnet-4-6-v1:0",
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "us.anthropic.claude-opus-4-7-v1:0",
        ):
            with self.subTest(model=model):
                fields = self._kwargs(model, "none").get("additionalModelRequestFields") or {}
                self.assertEqual(fields.get("thinking"), {"type": "disabled"})

    def test_high_reaches_the_wire_for_budget_and_adaptive_generations(self) -> None:
        legacy = self._kwargs("anthropic.claude-3-7-sonnet-20250219-v1:0", "high", 4096)
        fields = legacy["additionalModelRequestFields"]
        self.assertEqual(fields["thinking"]["type"], "enabled")
        self.assertGreater(legacy["inferenceConfig"]["maxTokens"], fields["thinking"]["budget_tokens"])
        self.assertEqual(legacy["inferenceConfig"]["temperature"], 1.0)

        adaptive = self._kwargs("us.anthropic.claude-sonnet-4-6-v1:0", "high")
        self.assertEqual(adaptive["additionalModelRequestFields"]["thinking"], {"type": "adaptive"})

    def test_opus_4_7_keeps_its_adaptive_effort_shape(self) -> None:
        kwargs = self._kwargs("us.anthropic.claude-opus-4-7-v1:0", "high")
        self.assertEqual(
            kwargs["additionalModelRequestFields"]["thinking"],
            {"type": "adaptive", "effort": "high"},
        )

    def test_old_claude_and_no_effort_send_nothing(self) -> None:
        self.assertNotIn(
            "additionalModelRequestFields",
            self._kwargs("anthropic.claude-3-5-sonnet-20241022-v2:0", "none"),
        )
        self.assertNotIn(
            "additionalModelRequestFields",
            self._kwargs("us.anthropic.claude-sonnet-4-6-v1:0", None),
        )


if __name__ == "__main__":
    unittest.main()
