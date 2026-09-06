"""Media sections must not advertise Navin to people who never subscribed.

Image / video / music / voice used to default to the ``navin`` provider in the
schema, so a fresh install with no account showed Settings preconfigured on a
managed plan, and the Settings payload even flagged that row as ``managed``.
Media providers now start unset, and the managed slot is filled in only for an
active paid plan.
"""

import unittest
from unittest import mock

from navin.config.schema import Config, ProviderConfig
from navin.providers.managed_catalog import heal_managed_media_settings
from navin.webui.settings_api import (
    _image_generation_provider_rows,
    _video_generation_provider_rows,
    settings_payload,
    update_image_generation_settings,
)


def _paid(config: Config) -> Config:
    config.license.plan = "plus"
    config.license.managed_api_key = "sk-managed"
    return config


def _navin_row(rows: list[dict]) -> dict:
    return next(row for row in rows if row["name"] == "navin")


class MediaDefaultsTest(unittest.TestCase):
    def test_fresh_install_picks_no_media_provider(self) -> None:
        config = Config()
        self.assertEqual(config.tools.image_generation.provider, "")
        self.assertEqual(config.tools.video_generation.provider, "")
        self.assertEqual(config.tools.music_generation.provider, "")
        self.assertEqual(config.voice.tts_provider, "")
        self.assertEqual(config.transcription.provider, "")


class MediaProviderRowTest(unittest.TestCase):
    def test_navin_row_is_not_managed_without_a_plan(self) -> None:
        config = Config()
        for rows in (
            _image_generation_provider_rows(config),
            _video_generation_provider_rows(config),
        ):
            row = _navin_row(rows)
            self.assertFalse(row["managed"])
            self.assertFalse(row["configured"])

    def test_navin_row_is_managed_on_a_paid_plan(self) -> None:
        config = _paid(Config())
        row = _navin_row(_image_generation_provider_rows(config))
        self.assertTrue(row["managed"])
        self.assertTrue(row["configured"])
        self.assertEqual(row["api_key_hint"], "Navin plan")


class MediaSettingsPayloadTest(unittest.TestCase):
    def _payload(self, config: Config) -> dict:
        with mock.patch("navin.webui.settings_api.load_config", return_value=config), mock.patch(
            "navin.webui.settings_api.save_config"
        ):
            return settings_payload()

    def test_signed_out_payload_selects_nothing(self) -> None:
        payload = self._payload(Config())
        for section in ("image_generation", "video_generation", "music_generation"):
            self.assertEqual(payload[section]["provider"], "", section)
            self.assertFalse(payload[section]["provider_configured"], section)
        self.assertEqual(payload["voice"]["tts_provider"], "")
        self.assertFalse(payload["voice"]["tts_provider_configured"])
        self.assertEqual(payload["transcription"]["provider"], "")
        self.assertFalse(payload["transcription"]["provider_configured"])

    def test_usable_runtime_fallback_is_still_shown(self) -> None:
        """BYOK keeps its zero-setup mic: a fallback that can run stays visible."""
        config = Config()
        config.providers.openrouter = ProviderConfig(api_key="sk-byok")
        payload = self._payload(config)
        self.assertEqual(payload["voice"]["tts_provider"], "openrouter")
        self.assertTrue(payload["voice"]["tts_provider_configured"])
        self.assertEqual(payload["transcription"]["provider"], "openrouter")
        self.assertTrue(payload["transcription"]["provider_configured"])

    def test_explicit_pick_survives_a_missing_key(self) -> None:
        config = Config()
        config.transcription.provider = "groq"
        payload = self._payload(config)
        self.assertEqual(payload["transcription"]["provider"], "groq")
        self.assertFalse(payload["transcription"]["provider_configured"])

    def test_explicit_pick_is_reported(self) -> None:
        config = Config()
        config.providers.openrouter = ProviderConfig(api_key="sk-byok")
        config.tools.image_generation.provider = "openrouter"
        payload = self._payload(config)
        self.assertEqual(payload["image_generation"]["provider"], "openrouter")
        self.assertTrue(payload["image_generation"]["provider_configured"])


class MediaProviderUpdateTest(unittest.TestCase):
    def test_empty_provider_clears_the_selection(self) -> None:
        config = Config()
        config.tools.image_generation.provider = "openrouter"
        with mock.patch("navin.webui.settings_api.load_config", return_value=config), mock.patch(
            "navin.webui.settings_api.save_config"
        ) as save, mock.patch(
            "navin.webui.settings_api.settings_payload", return_value={"ok": True}
        ):
            update_image_generation_settings({"provider": [""]})
        self.assertEqual(config.tools.image_generation.provider, "")
        self.assertTrue(save.called)

    def test_unknown_provider_is_still_rejected(self) -> None:
        from navin.webui.settings_api import WebUISettingsError

        with mock.patch("navin.webui.settings_api.load_config", return_value=Config()):
            with self.assertRaises(WebUISettingsError):
                update_image_generation_settings({"provider": ["nope"]})


class MediaHealTest(unittest.TestCase):
    def test_paid_plan_gets_the_managed_slot(self) -> None:
        config = _paid(Config())
        self.assertTrue(heal_managed_media_settings(config))
        self.assertEqual(config.tools.image_generation.provider, "navin")
        self.assertEqual(config.tools.video_generation.provider, "navin")
        self.assertEqual(config.tools.music_generation.provider, "navin")
        self.assertFalse(heal_managed_media_settings(config))

    def test_free_plan_is_left_unset(self) -> None:
        self.assertFalse(heal_managed_media_settings(Config()))

    def test_byok_choice_survives(self) -> None:
        config = _paid(Config())
        config.tools.image_generation.provider = "openrouter"
        heal_managed_media_settings(config)
        self.assertEqual(config.tools.image_generation.provider, "openrouter")


if __name__ == "__main__":
    unittest.main()
