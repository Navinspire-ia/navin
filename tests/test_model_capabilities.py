"""Vision capability detection: provider declaration first, family name second."""

from __future__ import annotations

from navin.providers.model_capabilities import (
    input_modalities_from_row,
    supports_vision,
    vision_flag_for_row,
)


class TestDeclaredModalities:
    def test_declaration_beats_the_name_heuristic(self):
        # A text-only name that the provider says reads images.
        assert supports_vision("acme/mystery-7b", input_modalities=["text", "image"])
        # A vision-looking name the provider says is text-only.
        assert not supports_vision("openai/gpt-4o", input_modalities=["text"])

    def test_empty_declaration_falls_back_to_the_heuristic(self):
        assert supports_vision("openai/gpt-4o", input_modalities=[])
        assert supports_vision("openai/gpt-4o", input_modalities=None)

    def test_video_counts_as_vision(self):
        assert supports_vision("acme/x", input_modalities=["text", "video"])


class TestInputModalitiesFromRow:
    def test_reads_openrouter_architecture(self):
        row = {"architecture": {"input_modalities": ["text", "image"]}}
        assert input_modalities_from_row(row) == ["text", "image"]

    def test_reads_legacy_modality_string(self):
        row = {"architecture": {"modality": "text+image->text"}}
        assert input_modalities_from_row(row) == ["text", "image"]

    def test_reads_top_level_field(self):
        assert input_modalities_from_row({"input_modalities": ["image"]}) == ["image"]

    def test_returns_none_when_absent(self):
        assert input_modalities_from_row({}) is None
        assert input_modalities_from_row(None) is None
        assert input_modalities_from_row({"architecture": {}}) is None


class TestFamilyHeuristic:
    def test_recognizes_the_major_vision_families(self):
        slugs = [
            "openai/gpt-4o",
            "openai/gpt-4.1-mini",
            "openai/gpt-5",
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-sonnet-4.5",
            "google/gemini-3.6-flash",
            "google/gemini-3.7-flash",
            "x-ai/grok-4",
            "meta-llama/llama-4-scout",
            "qwen/qwen2.5-vl-72b-instruct",
            "mistralai/pixtral-large",
            "opengvlab/internvl3-78b",
            "xiaomi/mimo-v2.5",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ]
        for slug in slugs:
            assert supports_vision(slug), slug

    def test_rejects_text_only_models(self):
        slugs = [
            "deepseek/deepseek-v4-flash",
            "mistralai/mistral-7b-instruct",
            "meta-llama/llama-3.1-70b-instruct",
            "qwen/qwen3-coder",
            "moonshotai/kimi-k2",
        ]
        for slug in slugs:
            assert not supports_vision(slug), slug

    def test_rejects_audio_and_embedding_models(self):
        slugs = [
            "openai/whisper-1",
            "openai/text-embedding-3-large",
            "google/gemini-3-flash-tts",
            "x-ai/grok-voice",
            "nvidia/parakeet-transcribe",
            "cohere/rerank-v3",
        ]
        for slug in slugs:
            assert not supports_vision(slug), slug

    def test_free_variant_suffix_is_ignored(self):
        assert supports_vision("google/gemini-3.6-flash:free")
        assert not supports_vision("deepseek/deepseek-v4-flash:free")

    def test_blank_slug_is_not_vision(self):
        assert not supports_vision("")
        assert not supports_vision(None)
        assert not supports_vision("   ")


class TestVisionFlagForRow:
    def test_prefers_the_row_declaration(self):
        row = {"id": "acme/mystery", "architecture": {"input_modalities": ["text", "image"]}}
        assert vision_flag_for_row("acme/mystery", row)

    def test_falls_back_to_the_slug(self):
        assert vision_flag_for_row("openai/gpt-4o", {"id": "openai/gpt-4o"})
        assert not vision_flag_for_row("deepseek/deepseek-v4-flash", None)
