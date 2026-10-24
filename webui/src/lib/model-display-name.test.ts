import { describe, expect, it } from "vitest";
import { modelDisplayName } from "./model-display-name";

describe("modelDisplayName", () => {
  it("cleans Bedrock IDs", () => {
    expect(modelDisplayName("global.anthropic.claude-sonnet-5-5")).toBe("Claude Sonnet 5.5");
    expect(modelDisplayName("bedrock/eu.anthropic.claude-opus-5-5")).toBe("Claude Opus 5.5");
    expect(modelDisplayName("eu.anthropic.claude-sonnet-4-5-20250929-v1:0")).toBe("Claude Sonnet 4.5");
    expect(modelDisplayName("openai.gpt-oss-120b-1:0")).toBe("GPT OSS 120b");
  });
  it("leaves other IDs alone", () => {
    expect(modelDisplayName("z-ai/glm-5.3-flash")).toBe("glm-5.3-flash");
    expect(modelDisplayName("gpt-6-sol")).toBe("gpt-6-sol");
  });
});
