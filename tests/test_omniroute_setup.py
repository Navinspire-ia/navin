"""OmniRoute guided setup: detect -> install -> start -> configure (wizard + Settings)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config, ModelPresetConfig
from navin.providers import omniroute_setup
from navin.providers.omniroute_setup import (
    OMNIROUTE_INSTALL_COMMAND,
    OMNIROUTE_START_COMMAND,
    OmniRouteSetupError,
    configure_omniroute,
    install_omniroute,
    omniroute_status_payload,
    start_omniroute,
)
from tests.provider_test_utils import isolated_provider_env

OMNIROUTE_BASE = "http://localhost:20128/v1"

CATALOG = [
    {"id": "auto", "label": "Auto", "owned_by": "OmniRoute", "free": True},
    {"id": "auto/coding", "label": "Auto - coding", "owned_by": "OmniRoute", "free": True},
    {"id": "oc/big-pickle", "label": None, "owned_by": "opencode", "free": True},
    {"id": "kiro/claude-sonnet-4-6", "label": None, "owned_by": "kiro"},
]


class _IsolatedConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._previous_path = get_config_path()
        set_config_path(Path(self._tmp.name) / "config.json")
        self._env = isolated_provider_env()
        self._env.__enter__()
        save_config(Config())

    def tearDown(self) -> None:
        self._env.__exit__(None, None, None)
        set_config_path(self._previous_path)
        self._tmp.cleanup()


class NodeRequirementTest(unittest.TestCase):
    def test_engines_range_is_mirrored(self) -> None:
        supported = omniroute_setup._node_supported
        self.assertIsNone(supported(None))
        self.assertIsNone(supported("garbage"))
        self.assertFalse(supported("v20.19.0"))
        self.assertFalse(supported("v22.21.0"))
        self.assertTrue(supported("v22.22.2"))
        self.assertFalse(supported("v23.5.0"))
        self.assertTrue(supported("v24.8.0"))
        self.assertTrue(supported("v26.0.0"))
        self.assertFalse(supported("v27.0.0"))


class StatusPayloadTest(_IsolatedConfig):
    def test_fresh_host_is_in_the_install_phase(self) -> None:
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=False),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            payload = omniroute_status_payload()

        self.assertEqual(payload["phase"], "install")
        self.assertFalse(payload["installed"])
        self.assertFalse(payload["running"])
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["default_api_base"], OMNIROUTE_BASE)
        self.assertEqual(payload["dashboard_url"], "http://localhost:20128")
        self.assertEqual(payload["default_model"], "auto")
        self.assertEqual(payload["models"], [])
        self.assertEqual(payload["install"]["command"], OMNIROUTE_INSTALL_COMMAND)
        self.assertEqual(payload["install"]["start_command"], OMNIROUTE_START_COMMAND)
        self.assertTrue(payload["install"]["supported"])
        self.assertTrue(payload["install"]["node_supported"])
        self.assertEqual([combo["id"] for combo in payload["combos"]][0], "auto")

    def test_running_gateway_lists_the_upstream_pool_without_the_combos(self) -> None:
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=True),
            patch.object(omniroute_setup, "omniroute_keyless_catalog", return_value=CATALOG),
            patch.object(omniroute_setup, "_node_version", return_value=None),
            patch.object(omniroute_setup.shutil, "which", return_value=None),
        ):
            payload = omniroute_status_payload()

        # Docker / remote start: no local binary, yet the port answers.
        self.assertTrue(payload["installed"])
        self.assertEqual(payload["phase"], "configure")
        self.assertEqual(
            [row["id"] for row in payload["models"]],
            ["oc/big-pickle", "kiro/claude-sonnet-4-6"],
        )
        self.assertEqual(payload["model_count"], 2)
        self.assertEqual(payload["free_model_count"], 1)
        # No Node at all: the wizard shows the manual steps, not an Install button.
        self.assertFalse(payload["install"]["supported"])
        self.assertIsNone(payload["install"]["node_version"])

    def test_ready_once_configured_and_running(self) -> None:
        config = load_config()
        config.providers.omniroute.api_base = OMNIROUTE_BASE
        save_config(config)
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=True),
            patch.object(omniroute_setup, "omniroute_keyless_catalog", return_value=[]),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            payload = omniroute_status_payload()
        self.assertEqual(payload["phase"], "ready")
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["api_base"], OMNIROUTE_BASE)


class ConfigureTest(_IsolatedConfig):
    def _configure(self, **kwargs):
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=True),
            patch.object(omniroute_setup, "omniroute_keyless_catalog", return_value=CATALOG),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            return configure_omniroute(**kwargs)

    def test_default_pins_the_endpoint_and_the_auto_preset_as_chat_default(self) -> None:
        payload = self._configure()

        reloaded = load_config()
        self.assertEqual(reloaded.providers.omniroute.api_base, OMNIROUTE_BASE)
        self.assertIsNone(reloaded.providers.omniroute.api_key)
        preset = reloaded.model_presets["omniroute-auto"]
        self.assertEqual(preset.provider, "omniroute")
        self.assertEqual(preset.model, "auto")
        self.assertEqual(preset.label, "OmniRoute Auto (free)")
        self.assertIsNone(preset.reasoning_effort)
        self.assertEqual(reloaded.agents.defaults.model_preset, "omniroute-auto")
        self.assertEqual(reloaded.agents.defaults.provider, "omniroute")
        self.assertEqual(reloaded.agents.defaults.model, "")

        self.assertEqual(payload["phase"], "ready")
        self.assertTrue(payload["active"])
        self.assertEqual(payload["preset"], "omniroute-auto")
        self.assertEqual(payload["last_action"]["action"], "configure")
        self.assertEqual(payload["last_action"]["preset"], "omniroute-auto")
        self.assertIn(OMNIROUTE_BASE, payload["last_action"]["message"])

    def test_configure_is_idempotent(self) -> None:
        self._configure()
        self._configure()
        reloaded = load_config()
        omni = [p for p in reloaded.model_presets.values() if p.provider == "omniroute"]
        self.assertEqual(len(omni), 1)
        self.assertEqual(reloaded.agents.defaults.model_preset, "omniroute-auto")

    def test_custom_endpoint_is_kept(self) -> None:
        config = load_config()
        config.providers.omniroute.api_base = "http://192.168.1.20:20128/v1"
        save_config(config)
        payload = self._configure()
        self.assertEqual(load_config().providers.omniroute.api_base, "http://192.168.1.20:20128/v1")
        self.assertEqual(payload["last_action"]["api_base"], "http://192.168.1.20:20128/v1")

    def test_another_combo_without_activation_leaves_the_default_alone(self) -> None:
        config = load_config()
        config.model_presets["mine"] = ModelPresetConfig(
            provider="anthropic", model="claude-sonnet-4.5"
        )
        config.agents.defaults.model_preset = "mine"
        save_config(config)

        self._configure(model="auto/coding", make_active=False)

        reloaded = load_config()
        self.assertEqual(reloaded.agents.defaults.model_preset, "mine")
        preset = reloaded.model_presets["omniroute-auto-coding"]
        self.assertEqual(preset.model, "auto/coding")
        self.assertEqual(preset.label, "OmniRoute Auto - coding (free)")

    def test_endpoint_only_when_no_model_is_requested(self) -> None:
        self._configure(model=None)
        reloaded = load_config()
        self.assertEqual(reloaded.providers.omniroute.api_base, OMNIROUTE_BASE)
        self.assertFalse(any(p.provider == "omniroute" for p in reloaded.model_presets.values()))

    def test_model_ids_are_validated(self) -> None:
        with self.assertRaises(OmniRouteSetupError) as ctx:
            self._configure(model="auto; rm -rf /")
        self.assertEqual(ctx.exception.status, 400)


class ActionGuardsTest(_IsolatedConfig):
    def test_install_without_npm_points_at_node(self) -> None:
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=False),
            patch.object(omniroute_setup, "_node_version", return_value=None),
            patch.object(omniroute_setup.shutil, "which", return_value=None),
        ):
            with self.assertRaises(OmniRouteSetupError) as ctx:
                install_omniroute()
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("npm", ctx.exception.message)
        self.assertIn(OMNIROUTE_INSTALL_COMMAND, ctx.exception.message)

    def test_install_refuses_an_unsupported_node(self) -> None:
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=False),
            patch.object(omniroute_setup, "_node_version", return_value="v20.19.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            with self.assertRaises(OmniRouteSetupError) as ctx:
                install_omniroute()
        self.assertIn("v20.19.0", ctx.exception.message)

    def test_install_runs_npm_globally(self) -> None:
        class _Done:
            returncode = 0
            stdout = "added 1 package"
            stderr = ""

        seen: list[list[str]] = []

        def _run(argv, *, timeout_s):
            seen.append(list(argv))
            return _Done()

        binary = omniroute_setup.OmniRouteBinary(path="/usr/local/bin/omniroute", source="path")
        with (
            patch.object(
                omniroute_setup, "resolve_omniroute_binary", side_effect=[None, binary, binary]
            ),
            patch.object(omniroute_setup, "server_running", return_value=False),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
            patch.object(omniroute_setup, "_run", side_effect=_run),
        ):
            payload = install_omniroute()

        self.assertEqual(seen, [["/usr/bin/npm", "install", "-g", "omniroute"]])
        self.assertTrue(payload["last_action"]["ok"])
        self.assertTrue(payload["installed"])
        self.assertEqual(payload["phase"], "start")
        self.assertIsNone(payload["active_job"])

    def test_start_requires_an_install(self) -> None:
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=False),
        ):
            with self.assertRaises(OmniRouteSetupError) as ctx:
                start_omniroute()
        self.assertEqual(ctx.exception.status, 400)

    def test_start_spawns_the_binary_detached_and_waits_for_the_port(self) -> None:
        binary = omniroute_setup.OmniRouteBinary(path="/usr/local/bin/omniroute", source="path")
        spawned: list[tuple[list[str], dict]] = []

        def _popen(argv, **kwargs):
            spawned.append((list(argv), kwargs))
            return object()

        running = iter([False, False, True, True])
        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=binary),
            patch.object(
                omniroute_setup, "server_running", side_effect=lambda *a, **k: next(running)
            ),
            patch.object(omniroute_setup, "omniroute_keyless_catalog", return_value=CATALOG),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
            patch.object(omniroute_setup.subprocess, "Popen", side_effect=_popen),
            patch.object(omniroute_setup.time, "sleep", return_value=None),
            patch.object(
                omniroute_setup,
                "_server_log_path",
                return_value=Path(self._tmp.name) / "omniroute.log",
            ),
        ):
            payload = start_omniroute()

        self.assertEqual(len(spawned), 1)
        argv, kwargs = spawned[0]
        self.assertEqual(argv, ["/usr/local/bin/omniroute"])
        self.assertTrue(kwargs.get("start_new_session") or kwargs.get("creationflags"))
        self.assertTrue(payload["running"])
        self.assertTrue(payload["last_action"]["ok"])
        self.assertEqual(payload["phase"], "configure")


class WebUISurfaceTest(_IsolatedConfig):
    def test_configure_action_defaults_to_auto_and_attaches_settings(self) -> None:
        from navin.webui.omniroute_setup_api import omniroute_setup_action

        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=True),
            patch.object(omniroute_setup, "omniroute_keyless_catalog", return_value=CATALOG),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            payload = omniroute_setup_action("configure", {})

        self.assertEqual(payload["last_action"]["preset"], "omniroute-auto")
        settings = payload["settings"]
        row = next(p for p in settings["providers"] if p["name"] == "omniroute")
        self.assertTrue(row["configured"])
        active = next(p for p in settings["model_presets"] if p["active"])
        self.assertEqual(active["name"], "omniroute-auto")
        self.assertEqual(active["provider"], "omniroute")
        self.assertEqual(active["model"], "auto")

    def test_configure_action_can_pin_only_the_endpoint(self) -> None:
        from navin.webui.omniroute_setup_api import omniroute_setup_action

        with (
            patch.object(omniroute_setup, "resolve_omniroute_binary", return_value=None),
            patch.object(omniroute_setup, "server_running", return_value=False),
            patch.object(omniroute_setup, "_node_version", return_value="v24.8.0"),
            patch.object(omniroute_setup.shutil, "which", return_value="/usr/bin/npm"),
        ):
            payload = omniroute_setup_action("configure", {"model": ["none"]})
        self.assertIsNone(payload["last_action"]["preset"])
        self.assertTrue(payload["configured"])

    def test_unknown_action_is_a_404(self) -> None:
        from navin.webui.omniroute_setup_api import omniroute_setup_action

        with self.assertRaises(OmniRouteSetupError) as ctx:
            omniroute_setup_action("pull", {})
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
