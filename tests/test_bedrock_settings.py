# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Bedrock in Settings > Providers: a Bedrock API key, an AWS region and an
optional AWS profile, never an API base."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from navin.config.loader import load_config, save_config
from navin.config.schema import Config
from navin.webui.settings_api import WebUISettingsError, update_provider_settings


def _update(query: dict) -> None:
    with patch("navin.webui.settings_api.settings_payload", return_value={}):
        update_provider_settings({key: [value] for key, value in query.items()})


def test_region_and_profile_are_saved_and_cleared():
    save_config(Config())
    _update({"provider": "bedrock", "aws_region": "eu-west-3", "aws_profile": "bedrock-eu"})
    bedrock = load_config().providers.bedrock
    assert (bedrock.region, bedrock.profile) == ("eu-west-3", "bedrock-eu")
    _update({"provider": "bedrock", "aws_profile": ""})
    assert load_config().providers.bedrock.profile is None
    assert load_config().providers.bedrock.region == "eu-west-3"


def test_a_wrong_region_is_refused():
    save_config(Config())
    with pytest.raises(WebUISettingsError):
        _update({"provider": "bedrock", "aws_region": "paris"})


class _FakeBedrock:
    def __init__(self, denied: bool = False) -> None:
        self.denied = denied

    def list_inference_profiles(self, **_kw):
        if self.denied:
            raise RuntimeError("AccessDeniedException: not authorized to perform: bedrock:ListInferenceProfiles")
        return {"inferenceProfileSummaries": [
            {"inferenceProfileId": "eu.anthropic.claude-sonnet-5-5", "inferenceProfileName": "EU Claude Sonnet 5.5", "status": "ACTIVE"},
        ]}

    def list_foundation_models(self, **_kw):
        return {"modelSummaries": [
            {"modelId": "zai.glm-4.7-flash", "modelName": "GLM 4.7 Flash", "providerName": "Z.ai", "outputModalities": ["TEXT"]},
            {"modelId": "amazon.titan-embed", "modelName": "Embed", "outputModalities": ["EMBEDDING"]},
        ]}


def _fake_boto3(client):
    from types import SimpleNamespace

    return SimpleNamespace(Session=lambda **_kw: SimpleNamespace(client=lambda *_a, **_k: client))


def test_bedrock_lists_its_profiles_and_on_demand_models():
    from types import SimpleNamespace

    from navin.webui.settings_api import bedrock_model_rows

    config = SimpleNamespace(region="eu-west-3", api_key=None, profile=None)
    with patch.dict("sys.modules", {"boto3": _fake_boto3(_FakeBedrock())}):
        rows, error = bedrock_model_rows(config)
    assert error is None
    assert [row["id"] for row in rows] == ["eu.anthropic.claude-sonnet-5-5", "zai.glm-4.7-flash"]

    with patch.dict("sys.modules", {"boto3": _fake_boto3(_FakeBedrock(denied=True))}):
        rows, error = bedrock_model_rows(config)
    assert rows == [] and "Type a model ID" in error


def test_bedrock_lists_ohio_models_without_a_region(monkeypatch):
    from types import SimpleNamespace

    from navin.webui.settings_api import bedrock_model_rows

    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    regions = []

    def session(**_kw):
        def client(*_a, **kw):
            regions.append(kw.get("region_name"))
            return _FakeBedrock()
        return SimpleNamespace(client=client)

    with patch.dict("sys.modules", {"boto3": SimpleNamespace(Session=session)}):
        rows, error = bedrock_model_rows(SimpleNamespace(region=None, api_key=None, profile=None))
    assert error is None and rows
    assert set(regions) == {"us-east-2"}


def test_a_missing_profile_is_ignored_when_a_bedrock_key_is_given():
    from types import SimpleNamespace

    from navin.webui.settings_api import bedrock_model_rows

    sessions = []

    def session(**kw):
        sessions.append(kw)
        if kw.get("profile_name") == "default":
            raise RuntimeError("ProfileNotFound: The config profile (default) could not be found")
        return SimpleNamespace(client=lambda *_a, **_k: _FakeBedrock())

    fake = SimpleNamespace(Session=session)
    with patch.dict("sys.modules", {"boto3": fake}):
        rows, error = bedrock_model_rows(SimpleNamespace(region="eu-west-1", api_key="bedrock-api-key", profile="default"))
        assert error is None and rows
        rows, error = bedrock_model_rows(SimpleNamespace(region="eu-west-1", api_key=None, profile="default"))
        assert rows == [] and "not found" in error


def test_bedrock_defaults_to_ohio_unless_a_region_is_known(monkeypatch):
    """us-east-2 is the only region the inference account may call."""
    from types import SimpleNamespace

    from navin.providers.bedrock_provider import BedrockProvider

    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    calls = []

    def session(region_name):
        return SimpleNamespace(
            region_name=region_name,
            client=lambda *_a, **kw: calls.append(kw["region_name"]),
        )

    for profile_region, expected in ((None, "us-east-2"), ("eu-west-1", "eu-west-1")):
        with patch("navin.providers.bedrock_media.aws_session", return_value=session(profile_region)):
            BedrockProvider(default_model="global.anthropic.claude-opus-5-5")
            BedrockProvider(default_model="global.anthropic.claude-opus-5-5", region="us-west-2")
        assert calls[-2:] == [expected, "us-west-2"]
