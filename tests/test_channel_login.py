# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""In-workbench channel login helpers."""

from __future__ import annotations

import unittest

from navin.webui.channel_login import ChannelLoginError, login_snapshot, qr_png_data_url, start_channel_login


class ChannelLoginTest(unittest.TestCase):
    def test_idle_snapshot(self) -> None:
        row = login_snapshot("whatsapp")
        self.assertEqual(row["name"], "whatsapp")
        self.assertIn(row["status"], {"idle", "starting", "waiting_qr", "connected", "failed", "cancelled"})

    def test_qr_png_data_url(self) -> None:
        try:
            import segno  # noqa: F401
        except ImportError:
            self.skipTest("segno extra not installed")
        url = qr_png_data_url("https://example.com/navin-whatsapp")
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertGreater(len(url), 80)

    def test_unknown_channel_has_no_cli_handoff(self) -> None:
        async def _run() -> None:
            await start_channel_login("telegram")

        with self.assertRaises(ChannelLoginError):
            import asyncio

            asyncio.run(_run())

    def test_missing_whatsapp_support_points_at_install_button(self) -> None:
        from unittest.mock import patch

        async def _run() -> None:
            with patch(
                "navin.channels.whatsapp._load_neonize",
                side_effect=RuntimeError(
                    "WhatsApp support is not installed. Click Install support on the WhatsApp tool, then connect again."
                ),
            ):
                await start_channel_login("whatsapp")

        with self.assertRaises(ChannelLoginError) as raised:
            import asyncio

            asyncio.run(_run())
        self.assertIn("Install support", raised.exception.message)
        self.assertNotIn("navin plugins", raised.exception.message)

    def test_protobuf_mismatch_points_at_install_button(self) -> None:
        from unittest.mock import patch

        from navin.channels.whatsapp import _WHATSAPP_PROTOBUF_MISMATCH, _is_protobuf_runtime_mismatch

        class VersionError(Exception):
            pass

        mismatch = VersionError(
            "Detected incompatible Protobuf Gencode/Runtime versions when loading "
            "waE2E/WAWebProtobufsE2E.proto: gencode 7.34.1 runtime 6.33.6."
        )
        self.assertTrue(_is_protobuf_runtime_mismatch(mismatch))
        self.assertFalse(_is_protobuf_runtime_mismatch(RuntimeError("other")))

        async def _run() -> None:
            with patch(
                "navin.channels.whatsapp._load_neonize",
                side_effect=RuntimeError(_WHATSAPP_PROTOBUF_MISMATCH),
            ):
                await start_channel_login("whatsapp")

        with self.assertRaises(ChannelLoginError) as raised:
            import asyncio

            asyncio.run(_run())
        self.assertIn("Install support", raised.exception.message)
        self.assertNotIn("gencode", raised.exception.message)


class WhatsAppExtraPinTest(unittest.TestCase):
    def test_whatsapp_extra_pins_neonize_compatible_with_protobuf_6(self) -> None:
        from packaging.requirements import Requirement

        from navin.optional_features import optional_dependency_groups

        extras = optional_dependency_groups()["whatsapp"]
        neonize = next(Requirement(raw) for raw in extras if raw.startswith("neonize"))
        self.assertTrue(neonize.specifier.contains("0.3.17.post0", prereleases=True))
        self.assertFalse(neonize.specifier.contains("0.3.18.post0", prereleases=True))
        self.assertFalse(neonize.specifier.contains("0.4.3.post0", prereleases=True))


if __name__ == "__main__":
    unittest.main()
