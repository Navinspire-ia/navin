from navin.config.schema import Config, ModelPresetConfig
from navin.providers.factory import build_provider_snapshot, snapshot_context_window


def _config() -> Config:
    cfg = Config()
    cfg.model_presets = {
        "big": ModelPresetConfig(model="acme/big", provider="navin", context_window_tokens=1_000_000),
        "small": ModelPresetConfig(model="acme/small", provider="navin", context_window_tokens=200_000),
        "other": ModelPresetConfig(model="acme/other", provider="navin", context_window_tokens=131_072),
    }
    return cfg


def test_auto_fallbacks_never_shrink_a_configured_window():
    cfg = _config()
    assert snapshot_context_window(cfg, preset_name="big") == 1_000_000


def test_small_primary_stays_small_when_others_are_bigger():
    cfg = _config()
    assert snapshot_context_window(cfg, preset_name="other") == 131_072


def test_explicit_fallback_still_caps_the_window():
    cfg = _config()
    cfg.agents.defaults.fallback_models = ["small"]
    assert snapshot_context_window(cfg, preset_name="big") == 200_000


def test_snapshot_uses_the_same_formula():
    cfg = _config()
    cfg.providers.navin.api_key = "test-key"
    snap = build_provider_snapshot(cfg, preset_name="big")
    assert snap.context_window_tokens == snapshot_context_window(cfg, preset_name="big")
