# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Azure OpenAI in Settings lists the resource's deployments (the names Navin
sends as model), else the models of the v1 API, with clear errors."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx

from navin.webui.settings_api import azure_model_rows

CONFIG = SimpleNamespace(api_base="https://navin-test.openai.azure.com/", api_key="azure-key")


def _reply(status: int, body):
    return httpx.Response(status, json=body, request=httpx.Request("GET", "https://navin-test.openai.azure.com"))


def test_deployments_are_listed_with_their_base_model():
    calls = []

    def fake_get(url, headers=None, params=None, **_kw):
        calls.append((url, headers, params))
        return _reply(200, {"data": [{"id": "gpt5-prod", "model": "gpt-5.6-terra"}, {"id": "mini", "model": "mini"}]})

    with patch("navin.webui.settings_api.httpx.get", fake_get):
        rows, message = azure_model_rows(CONFIG)
    assert [row["id"] for row in rows] == ["gpt5-prod", "mini"]
    assert rows[0]["label"] == "gpt5-prod (gpt-5.6-terra)"
    assert message is None
    url, headers, params = calls[0]
    assert url == "https://navin-test.openai.azure.com/openai/deployments"
    assert headers["api-key"] == "azure-key" and params == {"api-version": "2022-12-01"}


def test_without_a_deployment_list_the_v1_models_are_offered():
    def fake_get(url, **_kw):
        if url.endswith("/openai/deployments"):
            return _reply(404, {"error": {"code": "404"}})
        return _reply(200, {"data": [{"id": "gpt-5.6-luna"}]})

    with patch("navin.webui.settings_api.httpx.get", fake_get):
        rows, message = azure_model_rows(SimpleNamespace(api_base="https://navin-test.openai.azure.com/openai/v1", api_key="k"))
    assert [row["id"] for row in rows] == ["gpt-5.6-luna"]
    assert "deployment name" in message


def test_refused_key_missing_endpoint_and_no_credentials_are_explained():
    with patch("navin.webui.settings_api.httpx.get", lambda *_a, **_k: _reply(401, {"error": "denied"})):
        rows, message = azure_model_rows(CONFIG)
    assert rows == [] and "refused" in message
    rows, message = azure_model_rows(SimpleNamespace(api_base="", api_key="k"))
    assert rows == [] and "endpoint" in message
    with patch.dict("sys.modules", {"azure.identity": None}):
        rows, message = azure_model_rows(SimpleNamespace(api_base="https://x.openai.azure.com", api_key=""))
    assert rows == [] and "Entra ID" in message
