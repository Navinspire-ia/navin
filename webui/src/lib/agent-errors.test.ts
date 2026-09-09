// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { AGENT_ERROR_SENTINELS, agentErrorKey } from "./agent-errors";

describe("agentErrorKey", () => {
  it("recognizes the generic backend error sentinel", () => {
    expect(agentErrorKey("Sorry, I encountered an error.")).toEqual({
      key: "message.agentErrorGeneric",
      fallback: "Sorry, I encountered an error.",
    });
  });

  it("recognizes the model-call error sentinel, whitespace included", () => {
    expect(
      agentErrorKey("  Sorry, I encountered an error calling the AI model. \n"),
    ).toEqual({
      key: "message.agentErrorModel",
      fallback: "Sorry, I encountered an error calling the AI model.",
    });
  });

  it("strips an Error: prefix so double-wrapped bubbles still localize", () => {
    expect(
      agentErrorKey("Error: Sorry, I encountered an error calling the AI model."),
    ).toEqual({
      key: "message.agentErrorModel",
      fallback: "Sorry, I encountered an error calling the AI model.",
    });
  });

  it("leaves normal assistant prose alone", () => {
    expect(agentErrorKey("Sorry, I misread your question.")).toBeNull();
    expect(agentErrorKey("All tests pass.")).toBeNull();
    expect(agentErrorKey("")).toBeNull();
  });

  it("does not match a sentence merely containing the sentinel", () => {
    expect(
      agentErrorKey("Sorry, I encountered an error. Retrying with a fix."),
    ).toBeNull();
  });

  it("covers the whole user_facing_errors table with unique keys", () => {
    // Mirrors tests/test_user_facing_error_sentinels.py on the backend.
    const keys = AGENT_ERROR_SENTINELS.map((s) => s.key);
    expect(new Set(keys).size).toBe(keys.length);
    for (const sentinel of AGENT_ERROR_SENTINELS) {
      expect(agentErrorKey(sentinel.text)).toEqual({
        key: sentinel.key,
        fallback: sentinel.text,
      });
    }
  });

  it("recognizes the rate-limit and connection sentinels", () => {
    expect(
      agentErrorKey(
        "The model is at capacity right now. Navin already retried "
        + "automatically. Wait a few seconds and send your message again, "
        + "or pick another model.",
      )?.key,
    ).toBe("message.agentErrorRateLimit");
    expect(
      agentErrorKey(
        "Could not reach the model provider. Navin already retried "
        + "automatically. Check your internet connection, and any VPN, "
        + "proxy or firewall that could block the request, then send your "
        + "message again.",
      )?.key,
    ).toBe("message.agentErrorConnection");
    expect(
      agentErrorKey(
        "The model provider hit a temporary error. Navin already retried "
        + "automatically. Send your message again, or pick another model.",
      )?.key,
    ).toBe("message.agentErrorUpstream");
  });
});
