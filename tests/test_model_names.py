from navin.providers.model_names import (
    bedrock_display_name,
    bedrock_region_scope,
    is_bedrock_model_id,
)


def test_bedrock_profile_names_drop_region_and_company():
    assert bedrock_display_name("global.anthropic.claude-sonnet-5-5", "GLOBAL Anthropic Claude Sonnet 5.5") == "Claude Sonnet 5.5"
    assert bedrock_display_name("eu.anthropic.claude-opus-5-5", "EU Anthropic Claude Opus 5.5") == "Claude Opus 5.5"
    assert bedrock_display_name("us.amazon.nova-pro-v1:0", "US Amazon Nova Pro") == "Nova Pro"
    assert bedrock_display_name("eu.meta.llama3-2-3b-instruct-v1:0", "EU Meta Llama 3.2 3B Instruct") == "Llama 3.2 3B Instruct"
    assert bedrock_display_name("global.openai.gpt-6-sol", "GLOBAL OpenAI GPT 6 Sol") == "GPT 6 Sol"
    assert bedrock_display_name("deepseek.r1-v1:0", "DeepSeek-R1") == "DeepSeek-R1"
    assert bedrock_display_name("minimax.minimax-m2", "MiniMax M2") == "MiniMax M2"


def test_bedrock_ids_without_names():
    assert bedrock_display_name("global.anthropic.claude-sonnet-5-5") == "Claude Sonnet 5.5"
    assert bedrock_display_name("eu.anthropic.claude-sonnet-4-5-20250929-v1:0") == "Claude Sonnet 4.5"
    assert bedrock_display_name("bedrock/global.anthropic.claude-fable-5-1") == "Claude Fable 5.1"
    assert bedrock_display_name("openai.gpt-oss-120b-1:0") == "GPT OSS 120b"
    assert bedrock_display_name("moonshotai.kimi-k3") == "Kimi K3"
    # A saved label that is just the ID is treated like no label.
    assert bedrock_display_name("eu.anthropic.claude-opus-5", "eu.anthropic.claude-opus-5") == "Claude Opus 5"


def test_other_providers_untouched():
    assert bedrock_display_name("z-ai/glm-5.3-flash", "GLM 5.3 Flash") == "GLM 5.3 Flash"
    assert bedrock_display_name("anthropic/claude-sonnet-5.5", None) == "anthropic/claude-sonnet-5.5"
    assert bedrock_display_name("gpt-6-sol", "") == "gpt-6-sol"
    assert not is_bedrock_model_id("z-ai/glm-5.3")


def test_region_scope():
    assert bedrock_region_scope("global.anthropic.claude-sonnet-5-5") == "Global"
    assert bedrock_region_scope("eu.anthropic.claude-sonnet-5-5") == "EU"
    assert bedrock_region_scope("anthropic.claude-sonnet-5-5") is None


def test_navin_catalog_names():
    # Navin (LiteLLM) slugs are not Bedrock IDs: a clean name stays, an ugly one is cleaned.
    assert bedrock_display_name("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5") == "Claude Sonnet 5.5"
    assert bedrock_display_name("anthropic/claude-sonnet-5.5", "GLOBAL Anthropic Claude Sonnet 5.5") == "Claude Sonnet 5.5"
    assert bedrock_display_name("anthropic/claude-opus-5.5", "eu.anthropic.claude-opus-5-5") == "Claude Opus 5.5"
    assert bedrock_display_name("openai/gpt-6-sol", "GPT 6 Sol") == "GPT 6 Sol"
