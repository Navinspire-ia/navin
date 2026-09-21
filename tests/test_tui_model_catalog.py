"""Exercise the terminal catalog picker with real config persistence."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from textual.widgets import DataTable, Input

from navin.config.loader import get_config_path, load_config, save_config, set_config_path
from navin.config.schema import Config, ProviderConfig
from navin.tui.screens import PickerScreen
from navin.tui.settings import SettingsHub, _provider_choices
from tests.test_tui_models import Host


def test_provider_alias_is_not_listed_twice():
    choices = _provider_choices({"providers": {"azureOpenai": {"apiKey": "test"}}})
    keys = [key for key, _ in choices]
    assert keys.count("azure_openai") == 1
    assert "azureOpenai" not in keys


@pytest.mark.parametrize("configure_provider", [False, True])
def test_single_provider_catalog_preview(tmp_path, monkeypatch, configure_provider):
    previous = get_config_path()
    set_config_path(tmp_path / "config.json")
    try:
        config = Config()
        if not configure_provider:
            config.providers.anthropic = ProviderConfig(api_key="test")
        save_config(config)
        calls = []

        def catalog(query):
            calls.append(query)
            return {"models": [
                {"id": "claude-sonnet-5", "label": "Sonnet", "context_window": 200000},
                {"id": "claude-opus-5", "label": "Opus"},
            ]}

        monkeypatch.setattr("navin.webui.settings_api.provider_models_payload", catalog)
        applied = AsyncMock()
        hub = SettingsHub(
            lambda: load_config().model_dump(mode="json", by_alias=True), lambda data: None,
            config_label="Preview", project_root=tmp_path, workspace=str(tmp_path),
            version="test", start="providers" if configure_provider else "models", apply_preset=applied,
        )

        async def run():
            app = Host(hub)
            async with app.run_test(size=(110, 38)) as pilot:
                await pilot.pause()
                if configure_provider:
                    hub.run_worker(hub._provider_form("anthropic"))
                    await pilot.pause()
                    app.screen.query_one("#f-api_key", Input).value = "test"
                    await pilot.click("#ok")
                else:
                    hub.query_one("#table", DataTable).focus()
                    await pilot.press("a")
                await pilot.pause()
                assert isinstance(app.screen, PickerScreen)
                assert app.screen._title == "Anthropic models"
                assert calls == [{"provider": ["anthropic"]}]
                await pilot.press(*"Sonnet", "enter")
                await pilot.pause()
                await app.workers.wait_for_complete()
                assert app.screen is hub
                defaults = load_config().agents.defaults
                assert defaults.model == "claude-sonnet-5"
                assert defaults.model_preset == "claude-sonnet-5"
                assert hub._model_rows()[0].cells[2] == "claude-sonnet-5"
                applied.assert_awaited_once_with("claude-sonnet-5")
                app.save_screenshot(str(tmp_path / "model-catalog-preview.svg"))

        asyncio.run(run())
    finally:
        set_config_path(previous)
