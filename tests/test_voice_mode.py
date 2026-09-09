"""Live voice conversation: the per-turn brief only rides on flagged turns."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from navin.agent.voice_mode import (
    VOICE_MODE_METADATA_KEY,
    voice_mode_context_provider,
    voice_mode_requested,
    voice_mode_runtime_lines,
)
from navin.runtime_context import RUNTIME_CONTEXT_END, RUNTIME_CONTEXT_TAG


class VoiceModeFlagTest(unittest.TestCase):
    def test_missing_or_false_metadata_is_not_a_voice_turn(self) -> None:
        self.assertFalse(voice_mode_requested(None))
        self.assertFalse(voice_mode_requested({}))
        self.assertFalse(voice_mode_requested({VOICE_MODE_METADATA_KEY: False}))
        self.assertFalse(voice_mode_requested({VOICE_MODE_METADATA_KEY: "off"}))
        self.assertFalse(voice_mode_requested({VOICE_MODE_METADATA_KEY: 3}))

    def test_bool_and_truthy_strings_enable_it(self) -> None:
        self.assertTrue(voice_mode_requested({VOICE_MODE_METADATA_KEY: True}))
        self.assertTrue(voice_mode_requested({VOICE_MODE_METADATA_KEY: "true"}))
        self.assertTrue(voice_mode_requested({VOICE_MODE_METADATA_KEY: "1"}))

    def test_lines_are_empty_without_the_flag(self) -> None:
        self.assertEqual(voice_mode_runtime_lines({"webui": True}), [])

    def test_lines_cover_the_call_etiquette(self) -> None:
        text = "\n".join(voice_mode_runtime_lines({VOICE_MODE_METADATA_KEY: True}))
        self.assertIn("text-to-speech", text)
        self.assertIn("what you are about to do", text)
        self.assertIn("one precise question at a time", text)
        self.assertIn("Push back", text)
        self.assertIn("When you finish", text)
        self.assertNotIn("\u2014", text)
        self.assertNotIn("\u2013", text)


class VoiceModeProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_provider_is_silent_for_typed_turns(self) -> None:
        request = SimpleNamespace(metadata={"webui": True})
        self.assertIsNone(await voice_mode_context_provider(request))  # type: ignore[arg-type]

    async def test_provider_wraps_the_brief_as_runtime_context(self) -> None:
        request = SimpleNamespace(metadata={VOICE_MODE_METADATA_KEY: True})
        block = await voice_mode_context_provider(request)  # type: ignore[arg-type]
        assert block is not None
        self.assertEqual(block.source, "voice_mode")
        self.assertTrue(block.content.startswith(RUNTIME_CONTEXT_TAG))
        self.assertTrue(block.content.endswith(RUNTIME_CONTEXT_END))
        self.assertIn("Voice conversation", block.content)

    async def test_provider_tolerates_requests_without_metadata(self) -> None:
        self.assertIsNone(await voice_mode_context_provider(SimpleNamespace()))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
