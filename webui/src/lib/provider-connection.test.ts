import { describe, expect, it } from "vitest";

import { lookupConnectionBase } from "./provider-connection";

const qwen = {
  default_region: "singapore",
  default_plan: "payg",
  default_protocol: "openai",
  bases: {
    "china|*|openai": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "singapore|*|openai": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "us|*|openai": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "china|*|native": "https://dashscope.aliyuncs.com/api/v1",
  },
};

describe("lookupConnectionBase", () => {
  it("fills the default Qwen Singapore OpenAI host", () => {
    expect(lookupConnectionBase(qwen)).toBe(
      "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    );
  });

  it("keeps China keys on the China host", () => {
    expect(lookupConnectionBase(qwen, "china", "token", "openai")).toBe(
      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    );
  });

  it("switches Qwen to native DashScope", () => {
    expect(lookupConnectionBase(qwen, "china", "payg", "native")).toBe(
      "https://dashscope.aliyuncs.com/api/v1",
    );
  });
});
