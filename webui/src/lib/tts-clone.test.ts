import { describe, expect, it } from "vitest";

import { ttsModelSupportsReference } from "./tts-clone";

describe("ttsModelSupportsReference", () => {
  it("accepts a Fish Audio slug on Navin or OpenRouter", () => {
    expect(ttsModelSupportsReference("fish-audio/s2.1-pro", "navin")).toBe(true);
    expect(ttsModelSupportsReference("fish-audio/s2.1-pro", "openrouter")).toBe(true);
  });

  it("lets a BYOK user type ElevenLabs in the free field", () => {
    expect(ttsModelSupportsReference("elevenlabs/eleven-multilingual-v2")).toBe(true);
  });

  it("never clones on catalogue-only providers", () => {
    expect(ttsModelSupportsReference("fish-audio/s2.1-pro", "openai")).toBe(false);
    expect(ttsModelSupportsReference("fish-audio/s2.1-pro", "ollama")).toBe(false);
    expect(ttsModelSupportsReference("google/gemini-3.1-flash-tts-preview", "navin")).toBe(
      false,
    );
  });
});
