# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings for the semantic code search (tools.semanticSearch)."""

from __future__ import annotations

import pytest

from navin.config.loader import load_config, save_config
from navin.config.schema import Config
from navin.webui.settings_api import (
    WebUISettingsError,
    update_semantic_search_settings,
)


@pytest.fixture
def config():
    cfg = Config()
    save_config(cfg)
    return cfg


def update(**params: str) -> dict[str, list[str]]:
    return {key: [value] for key, value in params.items()}


def test_auto_mode_is_the_default(config):
    assert load_config().tools.semantic_search.enabled is None


def test_enabled_accepts_auto_on_and_off(config):
    update_semantic_search_settings(update(enabled="on"))
    assert load_config().tools.semantic_search.enabled is True

    update_semantic_search_settings(update(enabled="off"))
    assert load_config().tools.semantic_search.enabled is False

    update_semantic_search_settings(update(enabled="auto"))
    assert load_config().tools.semantic_search.enabled is None


def test_enabled_rejects_garbage(config):
    with pytest.raises(WebUISettingsError):
        update_semantic_search_settings(update(enabled="maybe"))


def test_provider_model_and_limits_round_trip(config):
    update_semantic_search_settings(
        update(provider="lm_studio", model="bge-m3", dimensions="768", max_chunks="5000")
    )
    sem = load_config().tools.semantic_search
    assert sem.provider == "lm_studio"
    assert sem.model == "bge-m3"
    assert sem.dimensions == 768
    assert sem.max_chunks == 5000


def test_limits_are_bounded(config):
    with pytest.raises(WebUISettingsError):
        update_semantic_search_settings(update(dimensions="5000"))
    with pytest.raises(WebUISettingsError):
        update_semantic_search_settings(update(max_chunks="50"))


def test_empty_provider_is_refused(config):
    with pytest.raises(WebUISettingsError):
        update_semantic_search_settings(update(provider="  "))


def test_noop_update_does_not_touch_the_config(config):
    before = load_config().tools.semantic_search.model_dump()
    update_semantic_search_settings({})
    assert load_config().tools.semantic_search.model_dump() == before
