# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Organization gateway (Navin SM): the organization's LiteLLM proxy as Navin's model source."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import ssl
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import certifi
import pytest
from rich.console import Console
from typer.testing import CliRunner

from navin import organization
from navin.agent import managed_policy
from navin.cli.organization import create_org_app
from navin.config.loader import (
    get_config_path,
    invalidate_config_cache,
    load_config,
    load_config_cached,
    save_config,
)
from navin.config.schema import ModelPresetConfig
from navin.providers.factory import make_provider, provider_signature
from navin.utils.tls import trust_with_extra_ca
from navin.webui.organization_api import OrganizationApiError, handle_organization_action
from navin.webui.settings_api import WebUISettingsError, settings_payload, update_provider_settings


def _org_file(**gateway: Any) -> dict[str, Any]:
    """The example of the contract (references/organization-gateway.md), with *gateway* changes."""
    base = {
        "type": "litellm",
        "baseUrl": "https://llm.bank-x.example/",
        "auth": "user_key",
        "headers": {"X-Tenant": "retail"},
        "models": [
            {"id": "gpt-4o", "label": "GPT-4o (Azure)", "contextWindow": 128000,
             "maxOutputTokens": 16384, "vision": True},
            {"id": "claude-sonnet", "label": "Claude Sonnet (Bedrock)", "reasoningEffort": "low"},
            {"id": "llama-local"},
        ],
        "defaultModel": "gpt-4o",
        "routes": {"fast": "llama-local"},
        "exclusive": True,
    }
    base.update(gateway)
    return {
        "organization": {"id": "bank-x", "name": "Bank X", "supportContact": "it@bank-x.example"},
        "gateway": base,
        "permissions": {"deny": ["browser"]},
    }


@pytest.fixture(autouse=True)
def _no_organization(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(managed_policy.ENV_PATH, raising=False)
    organization.invalidate()
    invalidate_config_cache()
    yield
    organization.invalidate()
    invalidate_config_cache()


def _deploy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: Any) -> Path:
    """The file IT deploys (managed settings), found through NAVIN_MANAGED_SETTINGS."""
    path = tmp_path / "managed-settings.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    monkeypatch.setenv(managed_policy.ENV_PATH, str(path))
    return path


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"apiKey": "sk-1"}, "only allowed with auth shared_key"),
        ({"auth": "shared_key"}, "'gateway.apiKey' must be a non-empty string"),
        ({"defaultModel": "nope"}, "'gateway.defaultModel' 'nope' is not one of the listed models"),
        ({"routes": {"fast": "nope"}}, "'gateway.routes.fast' 'nope' is not one of the listed models"),
        ({"headers": {"X-Api-Key": "k"}}, "cannot set X-Api-Key"),
        ({"baseUrl": "https://me:pw@llm.bank-x.example"}, "must not carry credentials"),
        ({"baseUrl": "https://llm.bank-x.example/v1/chat/completions"}, "without /chat/completions"),
        ({"models": [{"id": "a"}, {"id": "a"}], "defaultModel": "a", "routes": {}}, "is listed twice"),
        ({"type": "openrouter"}, "'gateway.type' must be litellm"),
    ],
)
def test_the_contract_refuses_a_bad_file(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        organization.parse(_org_file(**change), source="managed", path="managed-settings.json")


def test_the_contract_example_is_read_as_documented() -> None:
    org = organization.parse(_org_file(), source="managed", path="managed-settings.json")
    gateway = org.gateway
    assert gateway is not None and org.usable and org.locked and org.exclusive
    assert (org.id, org.name, org.support_contact) == ("bank-x", "Bank X", "it@bank-x.example")
    assert (gateway.base_url, gateway.default_preset) == ("https://llm.bank-x.example", "org:gpt-4o")
    assert [model.label for model in gateway.models] == ["GPT-4o (Azure)", "Claude Sonnet (Bedrock)", "llama-local"]
    assert gateway.routes == {"fast": "llama-local"}


def test_a_deployed_gateway_is_layered_on_the_settings_and_never_written_to_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    mine = load_config()
    mine.model_presets["mine"] = ModelPresetConfig(model="gpt-5", provider="openai")
    mine.agents.defaults.model_preset = "mine"
    mine.model_routes["review"] = "mine"
    mine.providers.openai.api_key = "sk-personal-openai"
    save_config(mine)
    _deploy(monkeypatch, tmp_path, _org_file())

    config = load_config()
    assert config.agents.defaults.model_preset == "org:gpt-4o"
    preset = config.model_presets["org:gpt-4o"]
    assert (preset.provider, preset.model, preset.label) == ("organization", "gpt-4o", "GPT-4o (Azure)")
    assert preset.effective_context_window_tokens == 128000
    assert config.model_presets["org:claude-sonnet"].reasoning_effort == "low"
    assert config.model_routes == {"fast": "org:llama-local"}
    gateway = config.providers.model_extra["organization"]
    assert (gateway.api_base, gateway.extra_headers) == ("https://llm.bank-x.example", {"X-Tenant": "retail"})

    # An unrelated change, and a tweak of an organization model, saved.
    config.agents.defaults.bot_name = "Ada"
    config.model_presets["org:claude-sonnet"].reasoning_effort = "high"
    save_config(config)
    text = get_config_path().read_text(encoding="utf-8")
    saved = json.loads(text)
    assert saved["agents"]["defaults"]["modelPreset"] == "mine"
    assert saved["agents"]["defaults"]["botName"] == "Ada"
    assert saved["modelRoutes"] == {"review": "mine"}
    assert saved["modelPresets"]["org:claude-sonnet"] == {"reasoningEffort": "high"}
    assert set(saved["modelPresets"]) == {"mine", "org:claude-sonnet"}
    assert "organization" not in saved["providers"] and "llm.bank-x.example" not in text
    # In memory the organization's choices still hold after the save.
    assert config.agents.defaults.model_preset == "org:gpt-4o"
    reloaded = load_config()
    assert reloaded.model_presets["org:claude-sonnet"].reasoning_effort == "high"
    assert reloaded.model_presets["org:claude-sonnet"].label == "Claude Sonnet (Bedrock)"


def test_the_personal_key_is_kept_encrypted_and_only_the_gateway_can_be_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _deploy(monkeypatch, tmp_path, _org_file())
    with pytest.raises(ValueError, match=re.escape("Settings > Organization")):
        make_provider(load_config())

    status = handle_organization_action("key", {"api_key": "sk-litellm-user-123456"})
    assert status["user_key"] == {"configured": True, "hint": "sk-l\u2022\u2022\u2022\u20223456"}
    text = get_config_path().read_text(encoding="utf-8")
    assert "sk-litellm-user-123456" not in text and "enc:v1:" in text

    config = load_config()
    make_provider(config)
    signature = provider_signature(config)
    assert signature[:5] == ("gpt-4o", "organization", "organization", "sk-litellm-user-123456",
                             "https://llm.bank-x.example")
    config.providers.openai.api_key = "sk-personal-openai"
    with pytest.raises(ValueError, match="Bank X only allows its own model gateway"):
        make_provider(config, preset=ModelPresetConfig(model="gpt-5", provider="openai"))
    assert "outside Bank X's model gateway" in (managed_policy.tool_denied("generate_image") or "")
    assert managed_policy.tool_denied("read_file") is None


def test_a_shared_gateway_lets_personal_providers_through_when_not_exclusive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _deploy(monkeypatch, tmp_path, _org_file(auth="none", exclusive=False))
    config = load_config()
    config.providers.openai.api_key = "sk-personal-openai"
    make_provider(config, preset=ModelPresetConfig(model="gpt-5", provider="openai"))
    assert managed_policy.tool_denied("generate_image") is None
    assert provider_signature(config)[3] is None


def test_a_broken_deployed_file_blocks_model_calls_and_a_policy_only_file_does_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _deploy(monkeypatch, tmp_path, "{not json")
    config = load_config()
    config.providers.openai.api_key = "sk-personal-openai"
    personal = ModelPresetConfig(model="gpt-5", provider="openai")
    with pytest.raises(ValueError, match="Model calls are blocked until the file is fixed"):
        make_provider(config, preset=personal)
    status = handle_organization_action("status", {})
    assert status["error"].startswith("unreadable") and status["exclusive"] and status["locked"]

    _deploy(monkeypatch, tmp_path, {"permissions": {"deny": ["browser"]}})
    assert handle_organization_action("status", {})["present"] is False
    fresh = load_config()
    fresh.providers.openai.api_key = "sk-personal-openai"
    make_provider(fresh, preset=personal)


def test_an_imported_file_keeps_the_gateway_only_and_a_deployed_one_cannot_be_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    shared = _org_file(auth="shared_key", apiKey="sk-shared-abcdef123")
    status = handle_organization_action("import", {"content": json.dumps(shared)})
    assert (status["source"], status["locked"], status["user_key"]) == ("imported", False, None)
    stored = organization.imported_path(get_config_path().parent)
    text = stored.read_text(encoding="utf-8")
    assert "permissions" not in json.loads(text) and "sk-shared-abcdef123" not in text
    if os.name == "posix":
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600

    config = load_config()
    assert provider_signature(config)[3] == "sk-shared-abcdef123"
    save_config(config)
    assert "sk-shared-abcdef123" not in get_config_path().read_text(encoding="utf-8")

    with pytest.raises(OrganizationApiError) as wrong:
        handle_organization_action("import", {"content": json.dumps(_org_file(defaultModel="nope"))})
    assert wrong.value.status == 400 and "cannot be used" in wrong.value.message
    assert handle_organization_action("remove", {})["present"] is False
    with pytest.raises(OrganizationApiError) as nothing:
        handle_organization_action("remove", {})
    assert nothing.value.status == 404

    _deploy(monkeypatch, tmp_path, _org_file())
    for action, body in (("import", {"content": json.dumps(shared)}), ("remove", {})):
        with pytest.raises(OrganizationApiError) as locked:
            handle_organization_action(action, body)
        assert locked.value.status == 409 and "deployed by your IT team" in locked.value.message


def test_settings_show_the_organization_models_and_keep_the_gateway_out_of_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config = load_config()
    config.providers.openai.api_key = "sk-personal-openai"
    config.model_presets["mine"] = ModelPresetConfig(model="gpt-5", provider="openai")
    save_config(config)
    _deploy(monkeypatch, tmp_path, _org_file())

    payload = settings_payload()
    rows = {row["name"]: row for row in payload["model_presets"]}
    assert rows["org:gpt-4o"]["active"] and rows["org:gpt-4o"]["locked"]
    assert rows["org:gpt-4o"]["source"] == "organization" and rows["org:gpt-4o"]["vision"]
    assert rows["mine"]["organization_blocked"] and not rows["mine"]["enabled"]
    gateway_row = next(row for row in payload["providers"] if row["name"] == "organization")
    assert gateway_row["organization_managed"] and gateway_row["label"] == "Bank X"
    with pytest.raises(WebUISettingsError) as refused:
        update_provider_settings({"provider": ["organization"], "api_base": ["https://elsewhere.example"]})
    assert refused.value.status == 409


def test_cached_settings_follow_the_organization_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert "org:gpt-4o" not in load_config_cached().model_presets
    path = _deploy(monkeypatch, tmp_path, _org_file())
    assert load_config_cached().agents.defaults.model_preset == "org:gpt-4o"
    data = _org_file(defaultModel="llama-local")
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert load_config_cached().agents.defaults.model_preset == "org:llama-local"


class _HttpRequest:
    def __init__(self, path: str, body: Any = None) -> None:
        self.path = path
        self.headers: dict[str, str] = {}
        if body is not None:
            self.headers["x-navin-file-body-0"] = base64.b64encode(json.dumps(body).encode()).decode()


def _gateway_handler(token_ok: bool = True) -> Any:
    from navin.webui.stall_watch import StallReporter
    from navin.webui.ws_http import GatewayHTTPHandler

    handler = object.__new__(GatewayHTTPHandler)
    handler.check_api_token = lambda request: token_ok
    handler._log = SimpleNamespace(exception=lambda *a, **k: None, warning=lambda *a, **k: None,
                                   debug=lambda *a, **k: None, info=lambda *a, **k: None)
    handler._stalls = StallReporter(handler._log)
    handler.config = SimpleNamespace(token_issue_path="")
    return handler


def test_the_settings_route_imports_and_answers_without_secrets(tmp_path: Path) -> None:
    def call(path: str, body: Any = None, token_ok: bool = True) -> Any:
        return asyncio.run(_gateway_handler(token_ok)._dispatch_misc_routes(
            None, _HttpRequest(path, body), path.split("?")[0]))

    assert call("/api/organization?action=status", token_ok=False).status_code == 401
    assert json.loads(call("/api/organization?action=status").body)["present"] is False
    imported = call("/api/organization?action=import", {"content": json.dumps(_org_file())})
    assert imported.status_code == 200 and json.loads(imported.body)["organization"]["name"] == "Bank X"
    keyed = call("/api/organization?action=key", {"api_key": "sk-route-key-445566"})
    assert keyed.status_code == 200 and b"sk-route-key-445566" not in keyed.body
    assert call("/api/organization?action=key", {"api_key": "two words"}).status_code == 400
    assert call("/api/organization?action=nope").status_code == 404


def test_the_cli_imports_the_file_and_saves_the_personal_key(tmp_path: Path) -> None:
    runner = CliRunner()
    app = create_org_app(console=Console(width=160))
    empty = runner.invoke(app, ["status"])
    assert empty.exit_code == 0 and "No organization gateway" in empty.output
    file = tmp_path / "bank-x.json"
    file.write_text(json.dumps(_org_file()), encoding="utf-8")
    imported = runner.invoke(app, ["import", str(file)])
    assert imported.exit_code == 0, imported.output
    assert "Bank X" in imported.output and "GPT-4o (Azure)" in imported.output
    assert "Personal key: not set" in imported.output
    keyed = runner.invoke(app, ["key", "--stdin"], input="sk-cli-key-998877\n")
    assert keyed.exit_code == 0 and "sk-c" in keyed.output and "sk-cli-key-998877" not in keyed.output
    removed = runner.invoke(app, ["remove"])
    assert removed.exit_code == 0 and "removed" in removed.output


def test_an_internal_ca_is_trusted_on_top_of_the_public_roots(tmp_path: Path) -> None:
    assert trust_with_extra_ca(None) is True
    assert isinstance(trust_with_extra_ca(certifi.where()), ssl.SSLContext)
    broken = tmp_path / "ca.pem"
    broken.write_text("not a certificate", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be loaded"):
        trust_with_extra_ca(str(broken))
