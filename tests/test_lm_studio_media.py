"""LM Studio must appear as a local OpenAI-compatible media provider."""

from __future__ import annotations

import unittest

from navin.audio.transcription_registry import (
    get_transcription_provider,
    resolve_transcription_provider,
    transcription_provider_names,
)
from navin.audio.tts_registry import get_tts_provider, resolve_tts_provider, tts_provider_names
from navin.config.schema import ProviderConfig
from navin.providers.image_generation import get_image_gen_provider, image_gen_provider_names
from navin.providers.media_credentials import media_credentials_ready
from navin.providers.video_generation import get_video_gen_provider, video_gen_provider_names


class LmStudioMediaProviderTest(unittest.TestCase):
    def test_registered_for_image_video_tts_and_stt(self) -> None:
        self.assertIn("lm_studio", image_gen_provider_names())
        self.assertIn("lm_studio", video_gen_provider_names())
        self.assertIn("lm_studio", tts_provider_names())
        self.assertIn("lm_studio", transcription_provider_names())

    def test_aliases_resolve_for_audio(self) -> None:
        self.assertIsNotNone(resolve_tts_provider("lmstudio"))
        self.assertIsNotNone(resolve_tts_provider("lm-studio"))
        self.assertIsNotNone(resolve_transcription_provider("lmstudio"))
        self.assertIsNotNone(resolve_transcription_provider("lm-studio"))

    def test_default_bases_point_at_lm_studio_port(self) -> None:
        image_cls = get_image_gen_provider("lm_studio")
        video_cls = get_video_gen_provider("lm_studio")
        assert image_cls is not None
        assert video_cls is not None
        image = image_cls(api_key=None)
        video = video_cls(api_key=None)
        self.assertEqual(image.api_base, "http://localhost:1234/v1")
        self.assertEqual(video.api_base, "http://localhost:1234/v1")

        tts = get_tts_provider("lm_studio")
        stt = get_transcription_provider("lm_studio")
        assert tts is not None
        assert stt is not None
        self.assertEqual(tts.default_model, "tts-1")
        self.assertEqual(stt.default_model, "whisper")

    def test_empty_config_is_ready_via_default_local_base(self) -> None:
        self.assertTrue(media_credentials_ready("lm_studio", ProviderConfig()))


if __name__ == "__main__":
    unittest.main()
