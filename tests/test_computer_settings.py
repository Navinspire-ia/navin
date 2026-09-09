"""Settings choices must change the next desktop turn and respect provider catalogs."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from navin.agent.tools import browser, computer
from navin.agent.tools.registry import ToolRegistry
from navin.config.loader import load_config, save_config
from navin.config.schema import Config, ModelPresetConfig
from navin.providers.model_capabilities import supports_vision
from navin.webui.computer_api import (
    close_disabled_computer_sessions,
    computer_control,
    computer_diagnostics,
)
from navin.webui.computer_models import computer_models_payload, update_computer_model
from navin.webui.settings_api import (
    WebUISettingsError,
    _computer_payload,
    _model_row_payload,
    update_agent_settings,
)


@pytest.fixture
def config():
    cfg = Config()
    cfg.providers.navin.api_key = "test-managed-key"
    cfg.providers.openai.api_key = "test-byok-key"
    cfg.providers.anthropic.api_key = "test-anthropic-key"
    cfg.agents.defaults.provider = "openai"
    cfg.agents.defaults.model = "gpt-5"
    cfg.model_presets = {
        "chat": ModelPresetConfig(provider="openai", model="gpt-5"),
        "claude": ModelPresetConfig(provider="anthropic", model="claude-sonnet-4.5"),
    }
    cfg.agents.defaults.model_preset = "chat"
    save_config(cfg)
    return cfg


def catalog(rows, *, status="available"):
    return {"status": status, "models": rows, "message": None}


def settings_result(**kwargs):
    return {"computer": _computer_payload(load_config()), **kwargs}


def test_byok_catalog_uses_only_selected_provider_and_declared_vision(config):
    rows = [{"id": "vendor/private-visual", "vision": True},
            {"id": "gpt-5-mini", "vision": True},
            {"id": "gpt-5-text-only", "vision": False},
            {"id": "text-embedding-3-small", "vision": False}]
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog(rows)) as fetch:
        result = computer_models_payload({"provider": ["openai"]})
    fetch.assert_called_once_with({"provider": ["openai"]})
    assert {row["id"] for row in result["models"]} == {"vendor/private-visual", "gpt-5-mini"}
    assert "test-byok-key" not in str(result)


def test_navin_curates_flagships_across_families_without_leaking_other_plans(config):
    ids = ["qwen/qwen3.8-max", "qwen/qwen3.7-plus", "anthropic/claude-opus-5",
           "anthropic/claude-opus-4.8", "anthropic/claude-sonnet-5", "openai/gpt-5.6-sol",
           "openai/gpt-5-mini", "google/gemini-3.7-flash", "google/gemini-3-flash-preview",
           "x-ai/grok-4.6", "x-ai/grok-4.5", "deepseek/deepseek-chat"]
    for index, model in enumerate(ids):
        config.model_presets[f"navin-{index}"] = ModelPresetConfig(provider="navin", model=model)
    save_config(config)
    rows = [{"id": model, "vision": supports_vision(model)} for model in ids]
    rows.append({"id": "openai/gpt-5-pro-outside-plan", "vision": True})
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog(rows)):
        result = computer_models_payload({"provider": ["navin"]})
    assert {row["id"] for row in result["models"]} == {
        "qwen/qwen3.8-max", "anthropic/claude-opus-5", "anthropic/claude-sonnet-5",
        "openai/gpt-5.6-sol", "google/gemini-3.7-flash", "x-ai/grok-4.6",
    }


def test_navin_trusts_a_text_only_declaration_and_keeps_limited_plan_vision(config):
    config.model_presets.update({
        "managed": ModelPresetConfig(provider="navin", model="openai/gpt-5"),
        "economy": ModelPresetConfig(provider="navin", model="xiaomi/mimo-v2.5"),
    })
    save_config(config)
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog([
        {"id": "openai/gpt-5", "vision": False}, {"id": "xiaomi/mimo-v2.5", "vision": True},
    ])):
        result = computer_models_payload({"provider": ["navin"]})
    assert [row["id"] for row in result["models"]] == ["xiaomi/mimo-v2.5"]


def test_byok_unavailable_catalog_falls_back_only_to_its_saved_vision_models(config):
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog([], status="unavailable")):
        result = computer_models_payload({"provider": ["anthropic"]})
    assert [row["id"] for row in result["models"]] == ["claude-sonnet-4.5"]
    assert result["status"] == "unavailable"


def test_selecting_custom_byok_vision_persists_provider_capability_and_only_computer_route(config):
    before = config.agents.defaults.model_dump()
    model = "tenant/private-visual-v7"
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog([
        {"id": model, "vision": True, "context_window": 64000},
    ])), patch("navin.webui.computer_models.settings_payload", side_effect=settings_result):
        result = update_computer_model({"provider": ["openai"], "model": [model]})
    saved = load_config()
    selected = saved.model_presets[saved.model_routes["computer"]]
    assert (selected.provider, selected.model, selected.context_window_tokens) == ("openai", model, 64000)
    assert selected.input_modalities == ["text", "image"]
    assert result["computer"]["model_vision"] is True
    assert saved.agents.defaults.model_dump() == before
    assert saved.model_presets["claude"].provider == "anthropic"


def test_selecting_model_reuses_existing_preset_and_preserves_another_slug(config):
    config.model_presets["computer-openai-tenant-private-visual"] = ModelPresetConfig(
        provider="anthropic", model="claude-opus-4.5")
    save_config(config)
    rows = [{"id": "tenant/private-visual", "vision": True}, {"id": "gpt-5", "vision": True}]
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog(rows)), \
         patch("navin.webui.computer_models.settings_payload", side_effect=settings_result):
        update_computer_model({"provider": ["openai"], "model": ["tenant/private-visual"]})
        saved = load_config()
        assert saved.model_presets["computer-openai-tenant-private-visual"].provider == "anthropic"
        update_computer_model({"provider": ["openai"], "model": ["gpt-5"]})
    assert load_config().model_routes["computer"] == "chat"


@pytest.mark.parametrize("model", ["claude-sonnet-4.5", "gpt-5-text", "invented-vision"])
def test_model_outside_selected_vision_catalog_is_rejected_without_saving(config, model):
    before = load_config().model_dump()
    with patch("navin.webui.computer_models.provider_models_payload", return_value=catalog([
        {"id": "gpt-5-text", "vision": False},
    ])), pytest.raises(WebUISettingsError):
        update_computer_model({"provider": ["openai"], "model": [model]})
    assert load_config().model_dump() == before


@pytest.mark.parametrize("provider", ["auto", "", "unconfigured-provider"])
def test_provider_must_be_selected_and_configured(config, provider):
    with patch("navin.webui.computer_models.provider_models_payload") as fetch, pytest.raises(WebUISettingsError):
        computer_models_payload({"provider": [provider]})
    fetch.assert_not_called()


def test_provider_catalog_false_survives_serialization():
    row = _model_row_payload({"id": "gpt-5", "architecture": {"input_modalities": ["text"]}})
    assert row["vision"] is False


def test_computer_options_are_validated_and_round_trip(config):
    with patch("navin.webui.settings_api.settings_payload", side_effect=settings_result):
        result = update_agent_settings({
            "computer_enabled": ["false"], "computer_ask": ["always"],
            "computer_settle_ms": ["125"], "computer_type_delay_ms": ["0"],
            "computer_backend": ["x11"], "computer_display": [":99"],
            "computer_blocked_apps": ['["Finance*", " Finance* ", "Mail"]'],
            "computer_audit_screenshots": ["false"],
        })
    cfg = load_config().tools.computer
    assert (cfg.enabled, cfg.ask, cfg.settle_ms, cfg.type_delay_ms, cfg.display) == (False, "always", 125, 0, ":99")
    assert cfg.blocked_apps == ["Finance*", "Mail"]
    assert result["requires_restart"] is False
    assert result["computer"]["audit_screenshots"] is False


@pytest.mark.parametrize("bad", [
    {"computer_settle_ms": ["-1"]}, {"computer_type_delay_ms": ["1.5"]},
    {"computer_backend": ["imaginary"]}, {"computer_allowed_apps": ['{"app":"Foo"}']},
    {"computer_screenshot_max_width": ["99999"]}, {"computer_blocked_apps": ['[null]']},
])
def test_invalid_option_does_not_partially_save_other_changes(config, bad):
    before = load_config().model_dump()
    with pytest.raises(WebUISettingsError):
        update_agent_settings({"computer_enabled": ["false"], **bad})
    assert load_config().model_dump() == before


def test_enable_disable_changes_available_tool_before_next_turn(config):
    registry = ToolRegistry()
    def loader():
        return load_config().tools.computer
    computer.refresh_computer_registration(registry, loader)
    assert registry.has("computer")
    config.tools.computer.enabled = False
    save_config(config)
    computer.refresh_computer_registration(registry, loader)
    assert not registry.has("computer")
    config.tools.computer.enabled = True
    save_config(config)
    computer.refresh_computer_registration(registry, loader)
    assert registry.has("computer")


def test_stop_interrupts_sessions_and_requires_explicit_release(tmp_path):
    async def run():
        async def close():
            from navin.computer.policy import stop_reason
            assert stop_reason()
        with patch("navin.computer.policy.stop_file", return_value=tmp_path / "STOP"), \
             patch("navin.agent.tools.computer.shutdown_computer_sessions", side_effect=close) as shutdown:
            assert (await computer_control("stop"))["stopped"]
            shutdown.assert_awaited_once()
            assert (await computer_control("go"))["stopped"] is None
            shutdown.reset_mock(side_effect=True)
            await close_disabled_computer_sessions({"computer_enabled": ["false"]})
            shutdown.assert_awaited_once()
    # Disable also closes sessions, independently of the emergency stop file.
    asyncio.run(run())


def test_browser_live_view_and_next_launch_follow_saved_settings(config):
    async def run():
        tool = browser.BrowserTool(config=config.tools.browser, config_loader=lambda: load_config().tools.browser)
        session = browser._BrowserSession(config.tools.browser)
        session._live_bus = object()
        session._live_chat_id = "test"
        config.tools.browser.headless = False
        config.tools.browser.live_view = False
        save_config(config)
        with patch("navin.agent.tools.browser._get_session", return_value=session), \
             patch.object(session, "_stop_screencast", new_callable=AsyncMock) as stop, \
             patch.object(session, "_emit_live"), patch.object(tool, "_dispatch", new_callable=AsyncMock, return_value="ok"):
            assert await tool.execute(action="content") == "ok"
            assert session.config.headless is False
            assert session.config.live_view is False
            assert session._live_bus is None
            stop.assert_awaited()
    asyncio.run(run())


def test_diagnostics_close_backend_when_permission_probe_fails(config):
    from unittest.mock import MagicMock

    from navin.computer.base import PermissionMissingError

    backend = MagicMock()
    backend.request_permissions.side_effect = PermissionMissingError("grant screen capture")
    with patch("navin.webui.computer_api.create_backend", return_value=backend):
        result = computer_diagnostics(permission="screen_recording")
    assert not result["ready"]
    assert "grant screen capture" in str(result["checks"])
    backend.close.assert_called_once()


def test_passive_diagnostics_do_not_capture_or_open_a_wayland_portal(config):
    from unittest.mock import MagicMock

    from navin.computer.detect import BackendChoice

    backend = MagicMock()
    backend.permission_checks.return_value = []
    with patch("navin.webui.computer_api.create_backend", return_value=backend), \
         patch("navin.webui.computer_api.detect_platform", return_value=BackendChoice("wayland", "Linux")):
        result = computer_diagnostics(passive=True)
    assert result["passive"]
    assert not result["ready"]
    backend.doctor.assert_not_called()
    backend.screen.assert_not_called()
    backend.request_permissions.assert_not_called()
    backend.close.assert_called_once()


def test_permission_result_uses_latest_state_instead_of_stale_pending_check(config):
    from unittest.mock import MagicMock

    from navin.computer.base import Check, ScreenInfo
    from navin.computer.detect import BackendChoice

    backend = MagicMock()
    backend.request_permissions.return_value = [Check("screen_recording", False, "pending")]
    backend.permission_checks.return_value = [Check("screen_recording", True, "granted")]
    backend.screen.return_value = ScreenInfo(1440, 900)
    with patch("navin.webui.computer_api.create_backend", return_value=backend), \
         patch("navin.webui.computer_api.detect_platform", return_value=BackendChoice("macos", "macOS")):
        result = computer_diagnostics(permission="all")
    assert not result["ready"]
    assert result["passive"]
    assert len([check for check in result["checks"] if check["name"] == "screen_recording"]) == 1
    assert all(check["ok"] for check in result["checks"])
    backend.request_permissions.assert_called_once_with("all")
    backend.doctor.assert_not_called()


@pytest.mark.parametrize("platform,ready", [("x11", True), ("windows", True), ("macos", False)])
def test_optional_element_access_does_not_block_working_screen_and_input(config, platform, ready):
    from unittest.mock import MagicMock

    from navin.computer.base import Check, ScreenInfo
    from navin.computer.detect import BackendChoice

    backend = MagicMock()
    backend.doctor.return_value = [Check("screenshot", True), Check("input", True), Check("accessibility", False)]
    backend.screen.return_value = ScreenInfo(1440, 900)
    with patch("navin.webui.computer_api.create_backend", return_value=backend), \
         patch("navin.webui.computer_api.detect_platform", return_value=BackendChoice(platform, platform)):
        result = computer_diagnostics()
    assert result["ready"] is ready
    assert result["checks"][-1]["optional"] is (platform != "macos")


def test_cli_status_recognizes_byok_vision_and_uses_selected_instance(config, tmp_path):
    import io

    from rich.console import Console
    from typer.testing import CliRunner

    from navin.cli.computer import create_computer_app
    from navin.computer.policy import engage_stop
    from navin.config.loader import get_config_path, set_config_path

    selected_path = tmp_path / "selected" / "config.json"
    config.model_presets["visual"] = ModelPresetConfig(
        provider="openai", model="tenant/private-visual", input_modalities=["text", "image"])
    config.model_routes["computer"] = "visual"
    save_config(config, selected_path)
    set_config_path(selected_path)
    engage_stop("selected instance stopped")
    set_config_path(tmp_path / "different" / "config.json")
    output = io.StringIO()
    app = create_computer_app(console=Console(file=output, color_system=None, width=160))
    result = CliRunner().invoke(app, ["status", "--config", str(selected_path)])
    assert result.exit_code == 0, result.exception
    assert "vision, GUI coordinate accuracy unverified" in output.getvalue()
    assert "selected instance stopped" in output.getvalue()
    assert get_config_path() == selected_path
