import navin.audio  # noqa: F401 - app import order
from navin.config.schema import Config
from navin.providers.managed_catalog import apply_catalog, parse_catalog, pick_role_default_slug

PAYLOAD = {
    "provider": "litellm",
    "defaultModel": "anthropic/claude-sonnet-4.5",
    "models": [
        {"slug": "anthropic/claude-sonnet-4.5", "tier": "main"},
        {"slug": "amazon/nova-lite", "tier": "light"},
        {"slug": "moonshotai/kimi-k3", "tier": "expert"},
    ],
    "roles": {"deep": "moonshotai/kimi-k3", "fast": "amazon/nova-lite", "plan": "gone/model"},
    "media": {
        "audio": {"defaultModel": "aws/polly-neural", "models": [{"slug": "aws/polly-neural"}]},
        "stt": {"defaultModel": "mistral/voxtral-small", "models": [{"slug": "mistral/voxtral-small"}]},
        "live": {"defaultModel": "amazon/nova-2-sonic", "models": [{"slug": "amazon/nova-2-sonic"}]},
        "image": {"defaultModel": "stability/stable-image-core", "models": [{"slug": "stability/stable-image-core"}]},
    },
}


def _subscriber() -> Config:
    config = Config()
    config.license.plan = "pro"
    config.license.managed_api_key = "sk"
    config.providers.navin.api_key = "sk"
    return config


def test_site_routing_wins_and_unknown_slugs_are_dropped():
    catalog = parse_catalog(PAYLOAD, plan="pro")
    assert catalog.roles == {"deep": "moonshotai/kimi-k3", "fast": "amazon/nova-lite"}
    assert pick_role_default_slug(catalog, "deep") == "moonshotai/kimi-k3"
    assert pick_role_default_slug(catalog, "plan") == "anthropic/claude-sonnet-4.5"  # tier "main"


def test_navin_media_follow_the_catalog():
    config = _subscriber()
    config.voice.tts_provider = "navin"
    config.voice.tts_model = "qwen/qwen-audio-3.0-tts-flash"
    config.voice.voice = "loongjohn"
    config.transcription.provider = "navin"
    config.transcription.model = "qwen/qwen3-asr-flash-2026-02-10"
    config.tools.image_generation.provider = "navin"
    config.tools.image_generation.model = "google/gemini-3.1-flash-image"
    apply_catalog(config, parse_catalog(PAYLOAD, plan="pro"))
    assert (config.voice.tts_model, config.voice.voice) == ("aws/polly-neural", "auto")
    assert config.transcription.model == "mistral/voxtral-small"
    assert (config.voice.live_realtime_provider, config.voice.live_realtime_model) == ("navin", "amazon/nova-2-sonic")
    assert config.tools.image_generation.model == "stability/stable-image-core"


def test_user_choices_are_kept():
    config = _subscriber()
    config.voice.tts_provider = "openai"
    config.voice.tts_model = "tts-1"
    config.voice.selection_explicit = True
    config.providers.openai.api_key = "k"
    config.voice.live_realtime_provider = "gemini"
    config.voice.live_realtime_model = "gemini-live"
    apply_catalog(config, parse_catalog(PAYLOAD, plan="pro"))
    assert (config.voice.tts_provider, config.voice.tts_model) == ("openai", "tts-1")
    assert config.voice.live_realtime_provider == "gemini"


def test_litellm_without_image_or_video_never_points_to_openrouter():
    payload = {**PAYLOAD, "media": {k: v for k, v in PAYLOAD["media"].items() if k not in {"image", "video"}}}
    config = _subscriber()
    apply_catalog(config, parse_catalog(payload, plan="pro"), force_managed=True, steer_tools=True)
    assert config.tools.image_generation.model != "google/gemini-3.1-flash-image"
    assert config.tools.video_generation.model != "minimax/hailuo-3"


# What the production gateway really serves: GLM 4.7, not GLM 5.3.
PROD_LIKE = {
    "provider": "litellm",
    "defaultModel": "z-ai/glm-4.7-flash",
    "models": [
        {"slug": "z-ai/glm-4.7", "tier": "main"},
        {"slug": "z-ai/glm-4.7-flash", "tier": "light"},
        {"slug": "moonshotai/kimi-k3", "tier": "expert"},
        {"slug": "x-ai/grok-4.6", "tier": "main"},
    ],
}


def test_roles_resolve_to_a_served_model_when_the_preferred_ones_are_absent():
    catalog = parse_catalog(PROD_LIKE, plan="pro")
    for role in ("fast", "code", "search", "docs"):
        assert pick_role_default_slug(catalog, role) == "z-ai/glm-4.7-flash", role
    for role in ("dev", "plan", "review"):
        assert pick_role_default_slug(catalog, role) == "z-ai/glm-4.7", role
    # A model the account cannot call must not win over a served one.
    assert pick_role_default_slug(catalog, "security") == "z-ai/glm-4.7"


def test_the_preferred_model_still_wins_once_it_is_served():
    payload = {**PROD_LIKE, "models": [*PROD_LIKE["models"], {"slug": "z-ai/glm-5.3-flash", "tier": "executor"}]}
    catalog = parse_catalog(payload, plan="pro")
    assert pick_role_default_slug(catalog, "fast") == "z-ai/glm-5.3-flash"
    assert pick_role_default_slug(catalog, "dev") == "z-ai/glm-5.3-flash"


def test_flash_does_not_inject_a_model_the_gateway_does_not_serve():
    catalog = parse_catalog(PROD_LIKE, plan="flash")
    slugs = {m.slug for m in catalog.models}
    assert "z-ai/glm-5.3-flash" not in slugs
    assert catalog.default_model == "z-ai/glm-4.7-flash"


def test_flash_still_injects_its_default_when_the_catalog_offers_none():
    payload = {"provider": "litellm", "defaultModel": "moonshotai/kimi-k3",
               "models": [{"slug": "moonshotai/kimi-k3", "tier": "expert"}]}
    catalog = parse_catalog(payload, plan="flash")
    assert "z-ai/glm-5.3-flash" in {m.slug for m in catalog.models}
    assert catalog.default_model == "moonshotai/kimi-k3"


# Limits come from the gateway catalog (litellm_models), not from the code.
LIMITS_PAYLOAD = {
    "provider": "litellm",
    "defaultModel": "z-ai/glm-5.3",
    "models": [
        {"slug": "z-ai/glm-5.3", "tier": "main", "contextWindow": 1_000_000, "maxOutputTokens": 128_000},
        {"slug": "z-ai/glm-4.7", "tier": "executor", "contextWindow": 200_000, "maxOutputTokens": 16_000},
        {"slug": "moonshotai/kimi-k3", "tier": "expert", "contextWindow": 1_048_576},
        {"slug": "amazon/nova-micro", "tier": "light"},
        {"slug": "openai/gpt-5.6-terra", "tier": "executor", "contextWindow": "bad", "maxOutputTokens": -5},
    ],
}


def _presets(payload=LIMITS_PAYLOAD):
    from navin.providers.managed_catalog import catalog_presets

    return catalog_presets(parse_catalog(payload, plan="pro"))


def test_the_catalog_limits_reach_the_presets():
    presets = _presets()
    glm = presets["glm-5-3"]
    assert (glm.effective_context_window_tokens, glm.effective_max_tokens) == (1_000_000, 128_000)
    # A smaller window declared by the catalog is honoured, not replaced by a family guess.
    small = presets["glm-4-7"]
    assert (small.effective_context_window_tokens, small.effective_max_tokens) == (200_000, 16_000)


def test_a_limit_the_catalog_does_not_declare_falls_back_to_the_family_table():
    presets = _presets()
    # kimi-k3 declares a window but no output: the output comes from the family table.
    kimi = presets["kimi-k3"]
    assert kimi.effective_context_window_tokens == 1_048_576
    assert kimi.effective_max_tokens == 128_000
    # Nothing declared and no known family: the generic defaults.
    nova = presets["nova-micro"]
    assert (nova.effective_context_window_tokens, nova.effective_max_tokens) == (200_000, 32_000)


def test_bad_values_in_the_payload_are_ignored():
    terra = _presets()["gpt-5-6-terra"]
    assert terra.catalog_context_window_tokens is None
    assert terra.catalog_max_output_tokens is None


def test_a_value_set_on_the_preset_wins_over_the_catalog():
    from navin.config.schema import ModelPresetConfig

    preset = ModelPresetConfig(
        model="z-ai/glm-5.3", context_window_tokens=500_000, max_tokens=64_000,
        catalog_context_window_tokens=1_000_000, catalog_max_output_tokens=128_000,
    )
    assert (preset.effective_context_window_tokens, preset.effective_max_tokens) == (500_000, 64_000)


def test_applying_the_catalog_updates_limits_but_keeps_user_edited_presets():
    config = _subscriber()
    assert apply_catalog(config, parse_catalog(LIMITS_PAYLOAD, plan="pro"), force_managed=True)
    assert config.model_presets["glm-5-3"].effective_context_window_tokens == 1_000_000
    # The operator changes the row by hand: the next sync must not overwrite it.
    config.model_presets["glm-5-3"].user_edited = True
    config.model_presets["glm-5-3"].context_window_tokens = 300_000
    changed = {**LIMITS_PAYLOAD, "models": [
        {**LIMITS_PAYLOAD["models"][0], "contextWindow": 900_000}, *LIMITS_PAYLOAD["models"][1:],
    ]}
    apply_catalog(config, parse_catalog(changed, plan="pro"), force_managed=True)
    assert config.model_presets["glm-5-3"].effective_context_window_tokens == 300_000
    # An untouched row follows the catalog.
    assert config.model_presets["glm-4-7"].catalog_max_output_tokens == 16_000


def test_only_navin_presets_receive_the_catalog_limits():
    from navin.config.schema import ModelPresetConfig

    config = _subscriber()
    config.model_presets["my-glm"] = ModelPresetConfig(model="z-ai/glm-5.3", provider="bedrock")
    apply_catalog(config, parse_catalog(LIMITS_PAYLOAD, plan="pro"), force_managed=True)
    assert config.model_presets["glm-5-3"].provider == "navin"
    assert config.model_presets["glm-5-3"].effective_context_window_tokens == 1_000_000
    # A preset on another provider keeps its own values, whatever the catalog says.
    mine = config.model_presets["my-glm"]
    assert mine.catalog_context_window_tokens is None
    assert mine.effective_context_window_tokens == 200_000
