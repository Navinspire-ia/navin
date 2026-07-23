"""Placeholder LLM provider used when no API key / endpoint is configured yet.

Allows the gateway and WebUI to start so operators can finish setup in Settings
after connecting, instead of blocking on the CLI onboard wizard.
"""

from __future__ import annotations

from typing import Any

from navin.providers.base import LLMProvider, LLMResponse

UNCONFIGURED_MODEL = "unconfigured"

SETUP_MESSAGE = (
    "No model provider is configured yet. "
    "Open Settings → Providers, add an API key or local endpoint, "
    "choose a model, then send a message again."
)


class UnconfiguredProvider(LLMProvider):
    """LLM stand-in that never calls a remote API."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason.strip()

    def get_default_model(self) -> str:
        return UNCONFIGURED_MODEL

    def _response(self) -> LLMResponse:
        content = SETUP_MESSAGE
        if self.reason:
            content = f"{SETUP_MESSAGE}\n\n({self.reason})"
        return LLMResponse(
            content=content,
            finish_reason="error",
            error_kind="unconfigured",
            error_should_retry=False,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        del messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
        return self._response()
