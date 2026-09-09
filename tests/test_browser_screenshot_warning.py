# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A screenshot must never be described by a model that could not see it.

The provider-rejects case is handled by ``LLMProvider._strip_image_content``.
This covers the quiet one: a gateway that accepts the request and drops the
image block, leaving a text-only model free to invent what the page showed.
"""

from __future__ import annotations

from navin.agent.tools.browser import _BLIND_SCREENSHOT_WARNING, _screenshot_warning
from navin.agent.tools.context import RequestContext, request_context


class _Runtime:
    def __init__(self, model: str | None) -> None:
        self.model = model


def _ctx(model: str | None) -> RequestContext:
    return RequestContext(channel="test", chat_id="1", runtime=_Runtime(model))


class TestScreenshotWarning:
    def test_a_vision_model_is_not_lectured(self):
        with request_context(_ctx("gpt-4o")):
            assert _screenshot_warning() is None

    def test_a_text_only_model_is_warned(self):
        with request_context(_ctx("deepseek-chat")):
            warning = _screenshot_warning()
        assert warning == _BLIND_SCREENSHOT_WARNING
        assert "never describe or guess" in warning.lower()

    def test_an_unknown_model_is_warned_but_keeps_its_image(self):
        """The point of a warning rather than a strip: nothing is removed.

        ``supports_vision`` cannot recognise a self-hosted model, so a strip
        would blind one that reads images perfectly well.
        """
        with request_context(_ctx("acme-internal-vlm-v3")):
            assert _screenshot_warning() == _BLIND_SCREENSHOT_WARNING

    def test_no_context_does_not_raise(self):
        """Direct tool use outside an agent turn must still take screenshots."""
        assert _screenshot_warning() in (None, _BLIND_SCREENSHOT_WARNING)

    def test_a_missing_model_is_treated_as_unknown(self):
        with request_context(_ctx(None)):
            assert _screenshot_warning() == _BLIND_SCREENSHOT_WARNING

    def test_the_warning_names_the_remedy(self):
        assert "vision model" in _BLIND_SCREENSHOT_WARNING


class TestScreenshotPayload:
    def test_the_warning_rides_with_the_image_not_instead_of_it(self):
        """A blind model still gets the block; only the label grows."""
        from navin.utils.helpers import build_image_content_blocks

        blocks = build_image_content_blocks(
            b"\x89PNG\r\n\x1a\n", "image/png", "/tmp/x.png",
            f"(Screenshot of http://x, saved to /tmp/x.png)\n{_BLIND_SCREENSHOT_WARNING}",
        )
        kinds = [block["type"] for block in blocks]
        assert kinds == ["image_url", "text"], "the image block must survive"
        assert _BLIND_SCREENSHOT_WARNING in blocks[1]["text"]
