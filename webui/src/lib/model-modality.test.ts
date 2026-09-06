import { describe, expect, it } from "vitest";

import {
  canBeChatDefault,
  canHidePickerOption,
  isMediaModelSlug,
  isVisionChatModel,
  settingsModelSection,
  visiblePickerOptions,
} from "./model-modality";

describe("isVisionChatModel", () => {
  it("recognizes the major vision families", () => {
    const slugs = [
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
    ];
    for (const slug of slugs) {
      expect(isVisionChatModel(slug), slug).toBe(true);
    }
  });

  it("rejects text-only models", () => {
    const slugs = [
      "deepseek/deepseek-v4-flash",
      "mistralai/mistral-7b-instruct",
      "meta-llama/llama-3.1-70b-instruct",
      "qwen/qwen3-coder",
      "moonshotai/kimi-k2",
    ];
    for (const slug of slugs) {
      expect(isVisionChatModel(slug), slug).toBe(false);
    }
  });

  it("rejects audio, speech and embedding models", () => {
    const slugs = [
      "openai/whisper-1",
      "openai/text-embedding-3-large",
      "google/gemini-3-flash-tts",
      "x-ai/grok-voice",
      "nvidia/parakeet-transcribe",
      "cohere/rerank-v3",
    ];
    for (const slug of slugs) {
      expect(isVisionChatModel(slug), slug).toBe(false);
    }
  });

  it("ignores a free variant suffix", () => {
    expect(isVisionChatModel("google/gemini-3.6-flash:free")).toBe(true);
    expect(isVisionChatModel("deepseek/deepseek-v4-flash:free")).toBe(false);
  });

  it("lets a provider declaration win over the name", () => {
    expect(isVisionChatModel("acme/mystery-7b", true)).toBe(true);
    expect(isVisionChatModel("openai/gpt-4o", false)).toBe(false);
  });

  it("falls back to the heuristic when nothing is declared", () => {
    expect(isVisionChatModel("openai/gpt-4o", undefined)).toBe(true);
    expect(isVisionChatModel("openai/gpt-4o", null)).toBe(true);
  });

  it("handles blank input", () => {
    expect(isVisionChatModel("")).toBe(false);
    expect(isVisionChatModel(null)).toBe(false);
    expect(isVisionChatModel("   ")).toBe(false);
  });
});

describe("settingsModelSection", () => {
  it("buckets chat text and multimodal first, then media", () => {
    expect(settingsModelSection("text")).toBe("text");
    expect(settingsModelSection(null)).toBe("text");
    expect(settingsModelSection("image")).toBe("image");
    expect(settingsModelSection("video")).toBe("video");
    expect(settingsModelSection("audio")).toBe("audio");
    expect(settingsModelSection("music")).toBe("audio");
    expect(settingsModelSection("stt")).toBe("audio");
  });
});

describe("vision models stay usable as chat default", () => {
  it("does not confuse a vision reader with a media generator", () => {
    expect(isMediaModelSlug("google/gemini-3.6-flash")).toBe(false);
    expect(isMediaModelSlug("google/gemini-3.7-flash")).toBe(false);
    expect(canBeChatDefault("text", "google/gemini-3.6-flash")).toBe(true);
    expect(canBeChatDefault("text", "google/gemini-3.7-flash")).toBe(true);
    expect(canBeChatDefault("text", "google/lyria-2")).toBe(false);
  });
});

describe("canHidePickerOption", () => {
  it("lets the user hide specialty-active image, video and audio rows", () => {
    expect(
      canHidePickerOption({
        name: "nano-banana",
        active: true,
        modality: "image",
      }),
    ).toBe(true);
    expect(
      canHidePickerOption({
        name: "veo-3-1-fast",
        active: true,
        modality: "video",
      }),
    ).toBe(true);
    expect(
      canHidePickerOption({
        name: "gemini-tts",
        active: true,
        modality: "audio",
      }),
    ).toBe(true);
  });

  it("keeps the current chat model and the account default pinned", () => {
    expect(
      canHidePickerOption({
        name: "deepseek-v4-flash",
        active: true,
        modality: "text",
      }),
    ).toBe(false);
    expect(
      canHidePickerOption({
        name: "default",
        active: false,
        modality: "text",
        isDefault: true,
      }),
    ).toBe(false);
  });

  it("lets unused text models be hidden", () => {
    expect(
      canHidePickerOption({
        name: "nemotron-3-ultra",
        active: false,
        modality: "text",
      }),
    ).toBe(true);
  });
});

describe("visiblePickerOptions", () => {
  const rows = [
    { name: "deepseek", label: "DeepSeek", model: "deepseek/v4", active: true, modality: "text" },
    { name: "nemotron", label: "Nemotron", model: "nvidia/nemotron", active: false, modality: "text" },
    { name: "gemini", label: "Gemini Flash", model: "google/gemini-3.6-flash", active: false, modality: "text" },
    { name: "banana", label: "Nano Banana", model: "google/flash-image", active: true, modality: "image" },
    { name: "seedream", label: "Seedream", model: "bytedance/seedream", active: false, modality: "image" },
    { name: "veo", label: "Veo", model: "google/veo", active: true, modality: "video" },
    { name: "tts", label: "Gemini TTS", model: "google/tts", active: true, modality: "audio" },
    { name: "lyria", label: "Lyria", model: "google/lyria-3", active: true, modality: "music" },
  ];

  it("keeps only text and vision chat models", () => {
    expect(visiblePickerOptions(rows, "").map((row) => row.name)).toEqual([
      "deepseek",
      "nemotron",
      "gemini",
    ]);
  });

  it("does not surface specialty media after a hide", () => {
    const afterHide = rows.filter((row) => row.name !== "banana");
    expect(visiblePickerOptions(afterHide, "").map((row) => row.name)).toEqual([
      "deepseek",
      "nemotron",
      "gemini",
    ]);
  });

  it("search still ignores video, audio and image generators", () => {
    expect(visiblePickerOptions(rows, "seed").map((row) => row.name)).toEqual([]);
    expect(visiblePickerOptions(rows, "veo").map((row) => row.name)).toEqual([]);
    expect(visiblePickerOptions(rows, "gemini").map((row) => row.name)).toEqual([
      "gemini",
    ]);
  });
});
