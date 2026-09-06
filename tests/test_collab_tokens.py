"""Mint / verify short-lived HMAC collab join tokens."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from navin.webui.collab_tokens import (
    CollabTokenError,
    collab_identity_from_claims,
    mint_collab_token,
    resolve_collab_secret,
    verify_collab_token,
)

SECRET = "a" * 32


class ResolveSecretTest(unittest.TestCase):
    def test_explicit_wins(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_collab_secret(SECRET), SECRET)

    def test_env_fallback(self):
        with mock.patch.dict(
            os.environ,
            {"NAVIN_COLLAB_SECRET": SECRET, "NAVIN_LICENSE_SIGNING_SECRET": "b" * 32},
            clear=True,
        ):
            self.assertEqual(resolve_collab_secret(), SECRET)

    def test_short_secret_rejected(self):
        with mock.patch.dict(os.environ, {"NAVIN_COLLAB_SECRET": "too-short"}, clear=True):
            self.assertEqual(resolve_collab_secret(), "")


class MintVerifyTest(unittest.TestCase):
    def test_roundtrip(self):
        token = mint_collab_token(
            member_id="user@example.com",
            org_id="org-1",
            role="member",
            display_name="Ada",
            chat_id="chat-abc",
            secret=SECRET,
            now=1_700_000_000,
        )
        claims = verify_collab_token(token, secret=SECRET, now=1_700_000_000 + 10)
        self.assertIsNotNone(claims)
        assert claims is not None
        self.assertEqual(claims["sub"], "user@example.com")
        self.assertEqual(claims["org_id"], "org-1")
        self.assertEqual(claims["role"], "member")
        self.assertEqual(claims["display_name"], "Ada")
        self.assertEqual(claims["chat_id"], "chat-abc")
        identity = collab_identity_from_claims(claims)
        self.assertEqual(identity["member_id"], "user@example.com")
        self.assertEqual(identity["role"], "member")

    def test_expired_rejected(self):
        token = mint_collab_token(
            member_id="u1",
            org_id="o1",
            role="viewer",
            secret=SECRET,
            ttl_s=60,
            now=1_700_000_000,
        )
        self.assertIsNone(
            verify_collab_token(token, secret=SECRET, now=1_700_000_000 + 120)
        )

    def test_tamper_rejected(self):
        token = mint_collab_token(
            member_id="u1",
            org_id="o1",
            role="admin",
            secret=SECRET,
            now=1_700_000_000,
        )
        payload, _, sig = token.partition(".")
        bad = f"{payload}x.{sig}"
        self.assertIsNone(verify_collab_token(bad, secret=SECRET, now=1_700_000_000))

    def test_wrong_secret_rejected(self):
        token = mint_collab_token(
            member_id="u1",
            org_id="o1",
            role="member",
            secret=SECRET,
            now=1_700_000_000,
        )
        self.assertIsNone(
            verify_collab_token(token, secret="b" * 32, now=1_700_000_000)
        )

    def test_expected_org_mismatch(self):
        token = mint_collab_token(
            member_id="u1",
            org_id="org-a",
            role="member",
            secret=SECRET,
            now=1_700_000_000,
        )
        self.assertIsNone(
            verify_collab_token(
                token,
                secret=SECRET,
                now=1_700_000_000,
                expected_org_id="org-b",
            )
        )

    def test_mint_requires_secret(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CollabTokenError):
                mint_collab_token(
                    member_id="u1",
                    org_id="o1",
                    role="member",
                    secret="short",
                )

    def test_mint_requires_ids(self):
        with self.assertRaises(CollabTokenError):
            mint_collab_token(member_id="", org_id="o1", role="member", secret=SECRET)


if __name__ == "__main__":
    unittest.main()
