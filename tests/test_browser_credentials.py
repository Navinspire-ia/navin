# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-site credential vault for browser capture.

Values go in through Settings and only names come back out; nothing is
stored in clear on disk; the keyring is used when present; refs resolve to
the right site; TOTP follows RFC 6238.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from navin.agent.tools.base import ToolResult
from navin.agent.tools.browser import BrowserToolConfig, _BrowserSession, _live_action_line
from navin.browser_credentials import (
    BrowserCredentialError,
    BrowserCredentialStore,
    handle_browser_credentials_action,
    normalize_site,
    parse_secret_ref,
    totp_code,
)


class _NoVault:
    def ready(self) -> bool:
        return False


class _MemoryVault:
    """Stands in for the OS keyring (TokenVault interface)."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def ready(self) -> bool:
        return True

    def get(self, account_id: str) -> dict[str, Any]:
        return dict(self.items.get(account_id) or {})

    def put(self, account_id: str, tokens: dict[str, Any]) -> None:
        self.items[account_id] = dict(tokens)

    def delete(self, account_id: str) -> None:
        self.items.pop(account_id, None)


class CredentialStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="navin-creds-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, vault: Any) -> BrowserCredentialStore:
        return BrowserCredentialStore(self.tmp / "creds", vault=vault, config_path=self.tmp / "config.json")

    def test_sites_and_refs_normalize(self) -> None:
        self.assertEqual(normalize_site("https://App.Example.com:443/login?x=1"), "app.example.com")
        self.assertEqual(normalize_site("http://localhost:5173/"), "localhost:5173")
        self.assertEqual(normalize_site("app.example.com"), "app.example.com")
        self.assertEqual(parse_secret_ref("site:App.Example.com/password"), ("app.example.com", "password"))
        self.assertEqual(parse_secret_ref("site:localhost:5173/username"), ("localhost:5173", "username"))
        for bad in ("password", "site:/x", "site:host/../etc", "site:host/a b"):
            with self.assertRaises(BrowserCredentialError):
                parse_secret_ref(bad)

    def test_file_fallback_is_encrypted_and_api_never_returns_values(self) -> None:
        store = self._store(_NoVault())
        snapshot = handle_browser_credentials_action(
            "set",
            {"site": "https://app.example.com", "values": {"username": "me@x.test", "password": "hunter2-long"}},
            store=store,
        )
        self.assertEqual(snapshot["vault"], "encrypted-file")
        self.assertEqual(snapshot["sites"][0]["site"], "app.example.com")
        self.assertEqual(snapshot["sites"][0]["names"], ["password", "username"])
        self.assertIn("site:app.example.com/password", snapshot["sites"][0]["refs"])
        dumped = json.dumps(snapshot)
        self.assertNotIn("hunter2-long", dumped)
        self.assertNotIn("me@x.test", dumped)
        for path in self.tmp.rglob("*"):
            if path.is_file() and path.name != ".config-key":
                self.assertNotIn(b"hunter2-long", path.read_bytes())
                self.assertNotIn(b"me@x.test", path.read_bytes())
        self.assertEqual(store.resolve("site:app.example.com/password"), "hunter2-long")
        self.assertTrue(store.has("site:app.example.com/username"))

        handle_browser_credentials_action("delete", {"site": "app.example.com", "name": "password"}, store=store)
        with self.assertRaises(BrowserCredentialError) as caught:
            store.resolve("site:app.example.com/password")
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("Settings", caught.exception.message)
        snapshot = handle_browser_credentials_action("delete", {"site": "app.example.com"}, store=store)
        self.assertEqual(snapshot["sites"], [])

    def test_keyring_backend_holds_the_values(self) -> None:
        vault = _MemoryVault()
        store = self._store(vault)
        store.set_secrets("localhost:3000", {"password": "kr-secret-1"})
        self.assertEqual(store.backend_name(), "keyring")
        self.assertEqual(vault.items["localhost:3000"], {"password": "kr-secret-1"})
        self.assertFalse((self.tmp / "creds" / "secrets.enc").exists())
        self.assertEqual(store.resolve("site:localhost:3000/password"), "kr-secret-1")

    def test_storage_state_round_trip_is_encrypted(self) -> None:
        for vault in (_NoVault(), _MemoryVault()):
            store = self._store(vault)
            state = {"cookies": [{"name": "sid", "value": "cookie-value-xyz", "domain": "a.test", "path": "/"}],
                     "origins": []}
            store.save_storage_state("https://a.test", state)
            self.assertEqual(store.load_storage_state("a.test"), state)
            for path in (self.tmp / "creds" / "sessions").iterdir():
                self.assertNotIn(b"cookie-value-xyz", path.read_bytes())
            self.assertTrue(store.list_sites()[0]["session"])
            handle_browser_credentials_action("forget_session", {"site": "a.test"}, store=store)
            self.assertIsNone(store.load_storage_state("a.test"))

    def test_unknown_action_and_bad_payload(self) -> None:
        store = self._store(_NoVault())
        with self.assertRaises(BrowserCredentialError):
            handle_browser_credentials_action("explode", {}, store=store)
        with self.assertRaises(BrowserCredentialError):
            handle_browser_credentials_action("set", {"site": "a.test", "values": "nope"}, store=store)
        with self.assertRaises(BrowserCredentialError):
            handle_browser_credentials_action("set", {"site": "a.test", "values": {"bad name": "x"}}, store=store)

    def test_totp_matches_rfc6238(self) -> None:
        import base64

        seed = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(totp_code(seed, at=59, digits=8), "94287082")
        self.assertEqual(totp_code(seed, at=1111111109, digits=8), "07081804")
        uri = f"otpauth://totp/Demo?secret={seed}&digits=8&period=30"
        self.assertEqual(totp_code(uri, at=59), "94287082")


class SecretRedactionTest(unittest.TestCase):
    def test_live_feed_never_prints_typed_text(self) -> None:
        line = _live_action_line("type", {"action": "type", "ref": 3, "text": "hunter2-long"})
        self.assertNotIn("hunter2", line)
        self.assertIn("text=<12 chars>", line)
        line = _live_action_line("type", {"action": "type", "secret_ref": "site:a.test/password"})
        self.assertIn("secret_ref=site:a.test/password", line)

    def test_results_and_history_are_scrubbed(self) -> None:
        session = _BrowserSession(BrowserToolConfig())
        session.add_secret("p@ss word!")
        self.assertEqual(session.redact("x p@ss word! y"), "x [secret] y")
        self.assertEqual(session.redact("q=p%40ss+word%21"), "q=[secret]")
        error = session.redact_result(ToolResult.error("bad p@ss word!"))
        self.assertTrue(error.is_error)
        self.assertEqual(str(error), "bad [secret]")
        blocks = session.redact_result([{"type": "image_url"}, {"type": "text", "text": "p@ss word!"}])
        self.assertEqual(blocks[1]["text"], "[secret]")
        session.record_history("type", {"action": "type", "text": "clear", "secret_ref": "site:a.test/password"})
        session.scrub_last_history_text()
        self.assertEqual(session.history[-1], {"action": "type", "text": "[secret]", "secret_ref": "site:a.test/password"})
        session.add_secret("abc")  # too short to redact without wrecking page text
        self.assertEqual(session.redact("abc"), "abc")


if __name__ == "__main__":
    unittest.main()
