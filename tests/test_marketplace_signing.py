"""Marketplace package signatures: accept valid HMAC, reject unsigned / malicious."""

from __future__ import annotations

import unittest

from navin.marketplace_signing import (
    MarketplaceSigningError,
    assert_featured_eligible,
    canonical_skill_payload,
    sign_skill_package,
    verify_skill_signature,
)

SECRET = "a" * 32
OTHER_SECRET = "b" * 32


class CanonicalPayloadTest(unittest.TestCase):
    def test_stable_layout(self):
        payload = canonical_skill_payload(
            slug="demo-skill",
            version="1.2.3",
            content_hash="abc",
            package_url="https://cdn.example/demo.tgz",
        )
        self.assertEqual(
            payload,
            "navin-marketplace-v1\ndemo-skill\n1.2.3\nabc\nhttps://cdn.example/demo.tgz",
        )


class SignAndVerifyTest(unittest.TestCase):
    def test_round_trip(self):
        sig = sign_skill_package(
            slug="demo-skill",
            version="1.0.0",
            content_hash="deadbeef",
            secret=SECRET,
        )
        self.assertTrue(
            verify_skill_signature(
                slug="demo-skill",
                version="1.0.0",
                content_hash="deadbeef",
                signature=sig,
                secret=SECRET,
            )
        )

    def test_rejects_empty_signature(self):
        self.assertFalse(
            verify_skill_signature(
                slug="demo-skill",
                version="1.0.0",
                content_hash="deadbeef",
                signature="",
                secret=SECRET,
            )
        )
        self.assertFalse(
            verify_skill_signature(
                slug="demo-skill",
                version="1.0.0",
                content_hash="deadbeef",
                signature=None,
                secret=SECRET,
            )
        )

    def test_rejects_tampered_hash(self):
        sig = sign_skill_package(
            slug="demo-skill",
            version="1.0.0",
            content_hash="deadbeef",
            secret=SECRET,
        )
        self.assertFalse(
            verify_skill_signature(
                slug="demo-skill",
                version="1.0.0",
                content_hash="cafebabe",
                signature=sig,
                secret=SECRET,
            )
        )

    def test_rejects_wrong_secret(self):
        sig = sign_skill_package(
            slug="demo-skill",
            version="1.0.0",
            package_url="https://cdn.example/demo.tgz",
            secret=SECRET,
        )
        self.assertFalse(
            verify_skill_signature(
                slug="demo-skill",
                version="1.0.0",
                package_url="https://cdn.example/demo.tgz",
                signature=sig,
                secret=OTHER_SECRET,
            )
        )

    def test_rejects_malicious_slug_swap(self):
        sig = sign_skill_package(
            slug="safe-skill",
            version="1.0.0",
            content_hash="deadbeef",
            secret=SECRET,
        )
        self.assertFalse(
            verify_skill_signature(
                slug="evil-skill",
                version="1.0.0",
                content_hash="deadbeef",
                signature=sig,
                secret=SECRET,
            )
        )

    def test_sign_requires_target(self):
        with self.assertRaises(MarketplaceSigningError):
            sign_skill_package(slug="x", version="1.0.0", secret=SECRET)

    def test_featured_requires_valid_signature(self):
        sig = sign_skill_package(
            slug="demo-skill",
            version="1.0.0",
            content_hash="deadbeef",
            secret=SECRET,
        )
        # Patch env-less path via explicit fields + verify uses SECRET through monkeypatch.
        # assert_featured_eligible reads process env; set it for this assertion.
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"MARKETPLACE_SIGNING_SECRET": SECRET}):
            assert_featured_eligible(
                {
                    "slug": "demo-skill",
                    "version": "1.0.0",
                    "content_hash": "deadbeef",
                    "signature": sig,
                }
            )
            with self.assertRaises(MarketplaceSigningError):
                assert_featured_eligible(
                    {
                        "slug": "demo-skill",
                        "version": "1.0.0",
                        "content_hash": "deadbeef",
                        "signature": "",
                    }
                )


if __name__ == "__main__":
    unittest.main()
