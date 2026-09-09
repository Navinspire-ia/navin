# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The update pipeline's dev-only env overrides must die in packaged builds.

NAVIN_UPDATE_PUBLIC_KEY swaps the Ed25519 signature key and
NAVIN_UPDATE_ALLOW_HTTP downgrades the transport - both fine against a local
test server from a source checkout, both an update-hijack vector inside an
installed app. Packaged builds (sys.frozen) ignore them unconditionally.
"""

import base64
import sys
import unittest
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from navin.update import service


def _b64_public_key() -> str:
    key = Ed25519PrivateKey.generate().public_key()
    return base64.b64encode(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


class DevOverridesTest(unittest.TestCase):
    def test_public_key_env_honored_from_source(self):
        with mock.patch.dict("os.environ", {"NAVIN_UPDATE_PUBLIC_KEY": _b64_public_key()}):
            self.assertIsNotNone(service._public_key())

    def test_public_key_env_ignored_when_frozen(self):
        with (
            mock.patch.dict("os.environ", {"NAVIN_UPDATE_PUBLIC_KEY": _b64_public_key()}),
            mock.patch.object(sys, "frozen", create=True, new=True),
        ):
            # Without the env override the bundled public_key.txt is the only
            # source; when it is absent the service must refuse to update
            # rather than trust the environment.
            with mock.patch.object(service.Path, "exists", return_value=False):
                with self.assertRaises(service.UpdateError):
                    service._public_key()

    def test_allow_http_honored_from_source_for_localhost_only(self):
        with mock.patch.dict("os.environ", {"NAVIN_UPDATE_ALLOW_HTTP": "1"}):
            service._validate_base_url("http://127.0.0.1:8080")  # no raise
            with self.assertRaises(service.UpdateError):
                service._validate_base_url("http://evil.example.com")

    def test_allow_http_ignored_when_frozen(self):
        with (
            mock.patch.dict("os.environ", {"NAVIN_UPDATE_ALLOW_HTTP": "1"}),
            mock.patch.object(sys, "frozen", create=True, new=True),
        ):
            with self.assertRaises(service.UpdateError):
                service._validate_base_url("http://127.0.0.1:8080")

    def test_https_always_accepted(self):
        service._validate_base_url("https://updates.navin.live")  # no raise
