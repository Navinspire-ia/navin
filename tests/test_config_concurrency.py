# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Slow account/catalog requests must not undo newly saved Computer settings."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from unittest.mock import patch

import pytest

from navin.config.loader import get_config_path, load_config, save_config
from navin.config.schema import ModelPresetConfig


def _select_byok(config):
    config.model_presets["my-computer"] = ModelPresetConfig(
        provider="openai", model="tenant/vision", input_modalities=["text", "image"])
    config.model_routes["computer"] = "my-computer"
    config.tools.computer.ask = "always"
    config.tools.computer.blocked_apps = ["Mail"]


def test_background_save_preserves_computer_model_options_and_added_preset():
    save_config(load_config())
    background = load_config()
    settings = load_config()
    _select_byok(settings)
    save_config(settings)
    background.license.plan = "pro"
    save_config(background)
    final = load_config()
    assert final.model_routes["computer"] == "my-computer"
    assert final.model_presets["my-computer"].model == "tenant/vision"
    assert (final.tools.computer.ask, final.tools.computer.blocked_apps) == ("always", ["Mail"])
    assert final.license.plan == "pro"
    # A caller that saves again starts from the merged state too.
    assert background.model_routes["computer"] == "my-computer"
    assert not any(key.startswith("_loaded") for key in json.loads(get_config_path().read_text()))


def test_deleting_a_route_and_preset_survives_a_stale_background_save():
    initial = load_config()
    _select_byok(initial)
    save_config(initial)
    background, settings = load_config(), load_config()
    del settings.model_routes["computer"]
    del settings.model_presets["my-computer"]
    save_config(settings)
    background.tools.browser.headless = False
    save_config(background)
    final = load_config()
    assert "computer" not in final.model_routes
    assert "my-computer" not in final.model_presets
    assert final.tools.browser.headless is False


def _worker(path, barrier, field, value):
    config = load_config(Path(path))
    setattr(config.tools.computer, field, value)
    barrier.wait(timeout=15)
    save_config(config, Path(path))


def test_parallel_processes_merge_distinct_edits():
    save_config(load_config())
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    jobs = [ctx.Process(target=_worker, args=(str(get_config_path()), barrier, field, value))
            for field, value in (("enabled", False), ("settle_ms", 175))]
    try:
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(timeout=25)
            assert job.exitcode == 0
    finally:
        for job in jobs:
            if job.is_alive():
                job.terminate()
                job.join(timeout=5)
    final = load_config().tools.computer
    assert final.enabled is False
    assert final.settle_ms == 175


def test_failed_replacement_keeps_previous_file_and_removes_staging_file():
    config = load_config()
    save_config(config)
    path = get_config_path()
    before = path.read_bytes()
    config.tools.computer.enabled = False
    with patch("navin.config.loader.os.replace", side_effect=OSError("write failed")), pytest.raises(OSError):
        save_config(config)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_saving_to_another_path_replaces_that_configuration(tmp_path):
    original = load_config()
    _select_byok(original)
    save_config(original)
    target = tmp_path / "second.json"
    second = load_config(target)
    second.tools.computer.settle_ms = 1
    save_config(second, target)
    save_config(original, target)
    final = load_config(target)
    assert final.tools.computer.settle_ms == original.tools.computer.settle_ms
    assert final.model_routes["computer"] == "my-computer"


def test_license_response_arriving_after_model_choice_keeps_that_choice():
    from navin import license_client

    config = load_config()
    config.license.device = "a" * 32
    config.license.activation_token = "test-token"
    config.license.plan = "pro"
    save_config(config)
    background = load_config()

    def response(*args, **kwargs):
        settings = load_config()
        _select_byok(settings)
        save_config(settings)
        return 200, {"valid": True, "plan": "team"}

    with patch.object(license_client, "_post", side_effect=response):
        license_client.validate(background)
    final = load_config()
    assert final.model_routes["computer"] == "my-computer"
    assert final.model_presets["my-computer"].input_modalities == ["text", "image"]
    assert final.license.plan == "team"
