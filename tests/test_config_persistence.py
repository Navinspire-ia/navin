# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The config file must record choices, not freeze navin's defaults.

Every default used to be written to disk, so an installation kept the defaults
it was created with forever and improving one in the schema reached nobody who
had already run navin. These cover the pruning that fixes it, and the handful of
paths that must stay written anyway because following a changed default there
would move or expose the user's data instead of adjusting a preference.
"""

import json
import tempfile
import unittest
from pathlib import Path

from navin.agent.tools.database import DatabaseConnectionConfig
from navin.config.loader import _configured_values, load_config, save_config
from navin.config.schema import Config, ProviderConfig
from navin.config.secrets import ENC_PREFIX, is_secret_leaf, unlock_stored_secret, unlocked_secret


def _written(config: Config) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        save_config(config, path)
        return json.loads(path.read_text(encoding="utf-8"))


class PruningTest(unittest.TestCase):
    def test_an_untouched_default_is_not_written(self) -> None:
        written = _written(Config())
        self.assertNotIn("maxConcurrentSubagents", written["agents"]["defaults"])
        self.assertNotIn("maxToolIterations", written["agents"]["defaults"])

    def test_a_changed_value_is_written(self) -> None:
        config = Config()
        config.agents.defaults.max_concurrent_subagents = 7
        self.assertEqual(_written(config)["agents"]["defaults"]["maxConcurrentSubagents"], 7)

    def test_an_improved_default_reaches_an_existing_install(self) -> None:
        """The whole point: a value nobody chose must follow the schema."""
        written = _written(Config())
        self.assertNotIn("temperature", written["agents"]["defaults"])
        self.assertEqual(
            Config.model_validate(written).agents.defaults.temperature,
            Config().agents.defaults.temperature,
        )

    def test_the_file_is_far_smaller_than_the_whole_schema(self) -> None:
        config = Config()
        full = len(json.dumps(config.model_dump(mode="json", by_alias=True)))
        lean = len(json.dumps(_written(config)))
        self.assertLess(lean, full // 4)

    def test_a_secret_is_always_written(self) -> None:
        config = Config()
        config.providers.anthropic = ProviderConfig(api_key="sk-test")
        written = _written(config)
        stored = written["providers"]["anthropic"]["apiKey"]
        self.assertTrue(str(stored).startswith(ENC_PREFIX))
        self.assertNotIn("sk-test", json.dumps(written))


class RoundTripTest(unittest.TestCase):
    """Pruning must not change what navin actually runs with."""

    def _round_trip(self, config: Config) -> None:
        before = config.model_dump(mode="json", by_alias=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(config, path)
            after = load_config(path).model_dump(mode="json", by_alias=True)
        self.assertEqual(before, after)

    def test_a_default_config_survives(self) -> None:
        self._round_trip(Config())

    def test_a_configured_config_survives(self) -> None:
        config = Config()
        config.agents.defaults.model = "claude-sonnet-5"
        config.agents.defaults.context_window_tokens = 262_144
        config.providers.anthropic = ProviderConfig(api_key="sk-test")
        config.api.host = "0.0.0.0"
        config.api.api_key = "needed-because-of-the-wildcard-host"
        self._round_trip(config)

    def test_a_saved_config_loads_back_identically(self) -> None:
        config = Config()
        config.agents.defaults.bot_name = "navin-prod"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(config, path)
            self.assertEqual(load_config(path).agents.defaults.bot_name, "navin-prod")


class PinnedPathTest(unittest.TestCase):
    """Some values must stay on disk even while they match the default."""

    def test_the_workspace_is_always_written(self) -> None:
        """Following a moved default here would look like total data loss."""
        written = _written(Config())
        self.assertEqual(
            written["agents"]["defaults"]["workspace"],
            Config().agents.defaults.workspace,
        )

    def test_the_ports_are_always_written(self) -> None:
        """External clients and the desktop launcher connect to these."""
        written = _written(Config())
        self.assertEqual(written["api"]["port"], Config().api.port)
        self.assertEqual(written["gateway"]["port"], Config().gateway.port)

    def test_the_hosts_are_always_written(self) -> None:
        written = _written(Config())
        self.assertEqual(written["api"]["host"], Config().api.host)
        self.assertEqual(written["gateway"]["host"], Config().gateway.host)

    def test_the_workspace_boundary_is_not_pinned(self) -> None:
        """Only an operator's "yes" is recorded; the open default stays implicit."""
        self.assertNotIn("restrictToWorkspace", _written(Config()).get("tools", {}))

    def test_a_declared_database_connection_is_written_in_full(self) -> None:
        """Its engine and write permission describe someone else's database."""
        config = Config()
        config.tools.database.connections = {
            "prod": DatabaseConnectionConfig(engine="sqlite", path="/data/app.db"),
        }
        conn = _written(config)["tools"]["database"]["connections"]["prod"]
        self.assertEqual(conn["engine"], "sqlite")
        self.assertIn("allowWrites", conn)

    def test_no_connections_means_no_empty_stanza(self) -> None:
        """An empty mapping states nothing worth pinning."""
        written = _written(Config())
        self.assertNotIn("connections", written.get("tools", {}).get("database", {}))

    def test_empty_exec_patterns_are_not_written(self) -> None:
        written = _written(Config())
        self.assertNotIn("allowPatterns", written.get("tools", {}).get("exec", {}))

    def test_configured_exec_patterns_are_written(self) -> None:
        config = Config()
        config.tools.exec.deny_patterns = ["rm -rf /"]
        self.assertEqual(
            _written(config)["tools"]["exec"]["denyPatterns"],
            ["rm -rf /"],
        )


class WorkspaceRestrictionDefaultTest(unittest.TestCase):
    """The boundary is off unless someone asks for it, either way explicitly.

    Work reaches outside the project often enough - a sibling repo, a log, a
    dotfile - that fencing the agent in by default costs more than it protects.
    What matters here is that a value already on disk is never second-guessed:
    an operator who wrote either answer keeps it.
    """

    def _loaded(self, payload: dict) -> Config:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_config(path)

    def test_a_fresh_install_is_not_restricted(self) -> None:
        self.assertFalse(Config().tools.restrict_to_workspace)

    def test_a_config_with_no_such_key_is_not_restricted(self) -> None:
        config = self._loaded({"agents": {"defaults": {"botName": "navin"}}})
        self.assertFalse(config.tools.restrict_to_workspace)

    def test_an_old_config_without_livekit_still_loads(self) -> None:
        config = self._loaded({"agents": {"defaults": {"botName": "navin"}}})
        self.assertEqual(config.livekit.url, "")
        self.assertNotIn("jitsi", _written(config))

    def test_a_config_with_no_tools_section_at_all_is_not_either(self) -> None:
        self.assertFalse(self._loaded({}).tools.restrict_to_workspace)

    def test_an_explicit_true_is_respected(self) -> None:
        config = self._loaded({"tools": {"restrictToWorkspace": True}})
        self.assertTrue(config.tools.restrict_to_workspace)

    def test_an_explicit_false_is_respected(self) -> None:
        config = self._loaded({"tools": {"restrictToWorkspace": False}})
        self.assertFalse(config.tools.restrict_to_workspace)

    def test_the_legacy_exec_key_still_wins_over_the_default(self) -> None:
        config = self._loaded({"tools": {"exec": {"restrictToWorkspace": True}}})
        self.assertTrue(config.tools.restrict_to_workspace)

    def test_an_operators_fence_survives_a_save(self) -> None:
        """The value differs from the default, so it is written without pinning."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"tools": {"restrictToWorkspace": True}}), encoding="utf-8")
            save_config(load_config(path), path)
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(written["tools"]["restrictToWorkspace"], True)
            self.assertTrue(load_config(path).tools.restrict_to_workspace)


class ApprovalConfigTest(unittest.TestCase):
    """The agent runs unsupervised; asking is what an operator opts into."""

    def test_approvals_are_off_out_of_the_box(self) -> None:
        self.assertFalse(Config().tools.approvals.enabled)

    def test_the_default_is_not_written_to_disk(self) -> None:
        self.assertNotIn("approvals", _written(Config()).get("tools", {}))

    def test_turning_them_on_is_written(self) -> None:
        config = Config()
        config.tools.approvals.enabled = True
        self.assertIs(_written(config)["tools"]["approvals"]["enabled"], True)

    def test_a_machine_timeout_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Config.model_validate({"tools": {"approvals": {"timeoutS": 2}}})


class ExtraFieldTest(unittest.TestCase):
    """Channels and custom providers are extras, so pruning cannot reach them."""

    def test_a_channel_is_written_in_full(self) -> None:
        config = Config()
        config.channels.telegram = {"enabled": True, "botToken": "abc"}
        channel = _written(config)["channels"]["telegram"]
        self.assertTrue(str(channel["botToken"]).startswith(ENC_PREFIX))
        self.assertTrue(channel["enabled"])

    def test_a_custom_provider_survives(self) -> None:
        config = Config()
        config.providers.mine = {"apiBase": "http://localhost:1234"}
        self.assertEqual(
            _written(config)["providers"]["mine"]["apiBase"],
            "http://localhost:1234",
        )


class CodexProxyTest(unittest.TestCase):
    """The codex provider is excluded from dumps and re-added by hand."""

    def test_the_proxy_still_survives_a_lean_dump(self) -> None:
        config = Config()
        config.providers.openai_codex.proxy = "http://proxy:8080"
        written = _written(config)
        self.assertEqual(written["providers"]["openaiCodex"]["proxy"], "http://proxy:8080")

    def test_no_proxy_writes_no_codex_stanza(self) -> None:
        self.assertNotIn("openaiCodex", _configured_values(Config()).get("providers", {}))


class SecretAtRestTest(unittest.TestCase):
    """License tokens and API keys must not sit in cleartext on disk."""

    def test_secret_leaf_names(self) -> None:
        for key in (
            "apiKey",
            "managedApiKey",
            "psi_api_key",
            "unsplashAccessKey",
            "tokens",
            "githubToken",
            "botToken",
            "imapPassword",
            "Authorization",
            "x-api-key",
        ):
            self.assertTrue(is_secret_leaf(key), key)
        for key in ("maxTokens", "contextWindowTokens", "usageRemainingTokens", "host"):
            self.assertFalse(is_secret_leaf(key), key)


    def test_license_secrets_are_encrypted_and_load_back(self) -> None:
        config = Config()
        config.license.activation_token = "tok-secret"
        config.license.managed_api_key = "sk-or-v1-secret"
        config.license.account_email = "user@example.com"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(config, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raw["license"]["activationToken"].startswith(ENC_PREFIX))
            self.assertTrue(raw["license"]["managedApiKey"].startswith(ENC_PREFIX))
            self.assertEqual(raw["license"]["accountEmail"], "user@example.com")
            self.assertNotIn("tok-secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("sk-or-v1-secret", path.read_text(encoding="utf-8"))
            loaded = load_config(path)
            self.assertEqual(loaded.license.activation_token, "tok-secret")
            self.assertEqual(loaded.license.managed_api_key, "sk-or-v1-secret")

    def test_legacy_plaintext_config_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"license": {"activationToken": "plain-token"}}),
                encoding="utf-8",
            )
            self.assertEqual(load_config(path).license.activation_token, "plain-token")
            rewritten = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(rewritten["license"]["activationToken"].startswith(ENC_PREFIX))
            self.assertNotIn("plain-token", path.read_text(encoding="utf-8"))

    def test_env_refs_stay_readable(self) -> None:
        config = Config()
        config.providers.anthropic = ProviderConfig(api_key="${ANTHROPIC_API_KEY}")
        written = _written(config)
        self.assertEqual(written["providers"]["anthropic"]["apiKey"], "${ANTHROPIC_API_KEY}")

    def test_github_and_provider_keys_are_encrypted(self) -> None:
        config = Config()
        config.providers.openai = ProviderConfig(api_key="sk-openai-secret")
        config.providers.anthropic = ProviderConfig(api_key="sk-ant-secret")
        config.tools.forge.tokens = {"github.com": "ghp_githubsecret"}
        config.tools.seo.psi_api_key = "psi-secret"
        config.tools.montage.stock.unsplash_access_key = "unsplash-secret"
        config.channels.telegram = {"enabled": True, "botToken": "tg-secret"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(config, path)
            text = path.read_text(encoding="utf-8")
            for secret in (
                "sk-openai-secret",
                "sk-ant-secret",
                "ghp_githubsecret",
                "psi-secret",
                "unsplash-secret",
                "tg-secret",
            ):
                self.assertNotIn(secret, text)
            raw = json.loads(text)
            self.assertTrue(raw["providers"]["openai"]["apiKey"].startswith(ENC_PREFIX))
            self.assertTrue(raw["providers"]["anthropic"]["apiKey"].startswith(ENC_PREFIX))
            self.assertTrue(raw["tools"]["forge"]["tokens"]["github.com"].startswith(ENC_PREFIX))
            seo = raw["tools"]["seo"]
            psi = seo.get("psiApiKey") or seo.get("psi_api_key")
            self.assertTrue(str(psi).startswith(ENC_PREFIX))
            stock = raw["tools"]["montage"]["stock"]
            unsplash = stock.get("unsplashAccessKey") or stock.get("unsplash_access_key")
            self.assertTrue(str(unsplash).startswith(ENC_PREFIX))
            self.assertTrue(raw["channels"]["telegram"]["botToken"].startswith(ENC_PREFIX))
            loaded = load_config(path)
            self.assertEqual(loaded.providers.openai.api_key, "sk-openai-secret")
            self.assertEqual(loaded.tools.forge.tokens["github.com"], "ghp_githubsecret")
            self.assertEqual(loaded.tools.seo.psi_api_key, "psi-secret")

    def test_token_counts_are_not_encrypted(self) -> None:
        config = Config()
        config.agents.defaults.max_tokens = 4096
        written = _written(config)
        self.assertEqual(written["agents"]["defaults"]["maxTokens"], 4096)

    def test_websocket_issue_secret_is_encrypted(self) -> None:
        secret = "ws-issue-secret-test-value"
        config = Config()
        config.channels.websocket = {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8766,
            "tokenIssueSecret": secret,
            "websocketRequiresToken": True,
            "allowFrom": ["*"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save_config(config, path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(secret, text)
            raw = json.loads(text)
            stored = raw["channels"]["websocket"]["tokenIssueSecret"]
            self.assertTrue(stored.startswith(ENC_PREFIX))
            self.assertEqual(raw["channels"]["websocket"]["host"], "127.0.0.1")
            self.assertEqual(raw["channels"]["websocket"]["port"], 8766)
            loaded = load_config(path)
            self.assertEqual(loaded.channels.websocket["tokenIssueSecret"], secret)
            self.assertEqual(unlock_stored_secret(stored, path), secret)
            self.assertEqual(unlocked_secret(stored), "")
            self.assertEqual(unlocked_secret(secret), secret)
            self._assert_electron_unlocks(path, stored, secret)

    def _assert_electron_unlocks(self, config_path: Path, stored: str, secret: str) -> None:
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = Path(__file__).resolve().parent.parent / "desktop-electron" / "config-secrets.js"
        result = subprocess.run(
            [
                node,
                "-e",
                "const {unlockStoredSecret}=require(process.argv[1]);"
                "process.stdout.write(unlockStoredSecret(process.argv[2], process.argv[3]));",
                str(script),
                stored,
                str(config_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout, secret)

    def test_get_api_key_never_returns_ciphertext(self) -> None:
        config = Config()
        config.providers.openai.api_key = f"{ENC_PREFIX}not-a-real-blob"
        key = config.get_api_key("gpt-4o")
        self.assertTrue(key is None or not str(key).startswith(ENC_PREFIX))


if __name__ == "__main__":
    unittest.main()
