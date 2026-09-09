// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  addChoice,
  choiceAnswerPayload,
  choiceCanContinue,
  CUSTOM_CHOICE_MAX_CHARS,
  dropExpiredChoices,
  OTHER_CHOICE_ID,
  removeChoice,
  toPendingChoice,
  type ChoiceRequestFrame,
} from "./choices";

const NOW = 1_700_000_000_000;

function frame(overrides: Partial<ChoiceRequestFrame> = {}): ChoiceRequestFrame {
  return {
    request_id: "r1",
    question: "Where do we start?",
    options: [
      { id: "a", label: "Full phase", detail: "3 to 4 days", recommended: true },
      { id: "b", label: "Profiler only", detail: "See real data first" },
    ],
    allow_skip: true,
    recommended_id: "a",
    expires_at_ms: NOW + 600_000,
    ...overrides,
  };
}

describe("toPendingChoice", () => {
  it("fills missing option details and keeps the recommended id", () => {
    const request = toPendingChoice(frame({ options: [{ id: "a", label: "One" }, { id: "b", label: "Two" }] }), NOW);
    expect(request.options[0]?.detail).toBe("");
    expect(request.recommendedId).toBe("a");
    expect(request.allowSkip).toBe(true);
  });

  it("drops options without an id or label", () => {
    const request = toPendingChoice(
      frame({
        options: [
          { id: "a", label: "Keep" },
          { id: "", label: "No id" },
          { id: "c", label: "" },
        ],
      }),
      NOW,
    );
    expect(request.options.map((entry) => entry.id)).toEqual(["a"]);
  });
});

describe("addChoice", () => {
  it("adds a request", () => {
    expect(addChoice([], frame(), NOW)).toHaveLength(1);
  });

  it("ignores a card with fewer than two options", () => {
    expect(addChoice([], frame({ options: [{ id: "a", label: "Only" }] }), NOW)).toEqual([]);
  });

  it("keeps one entry when the same request arrives twice", () => {
    const once = addChoice([], frame(), NOW);
    const twice = addChoice(once, frame(), NOW + 10);
    expect(twice).toHaveLength(1);
    expect(twice[0]?.receivedAt).toBe(NOW + 10);
  });

  it("ignores a request that was already answered", () => {
    expect(addChoice([], frame(), NOW, new Set(["r1"]))).toEqual([]);
  });

  it("ignores a replayed request that has already expired", () => {
    expect(addChoice([], frame({ expires_at_ms: NOW - 1 }), NOW)).toEqual([]);
  });
});

describe("removeChoice", () => {
  it("drops the answered request and leaves the others", () => {
    const list = addChoice(addChoice([], frame(), NOW), frame({ request_id: "r2" }), NOW);
    expect(removeChoice(list, "r1").map((entry) => entry.requestId)).toEqual(["r2"]);
  });
});

describe("dropExpiredChoices", () => {
  it("drops only the stale ones", () => {
    const list = addChoice(
      addChoice([], frame(), NOW),
      frame({ request_id: "r2", expires_at_ms: NOW + 1_000 }),
      NOW,
    );
    expect(dropExpiredChoices(list, NOW + 2_000).map((entry) => entry.requestId)).toEqual(["r1"]);
  });
});

describe("OTHER_CHOICE_ID", () => {
  it("is the reserved id the card uses for a typed answer", () => {
    expect(OTHER_CHOICE_ID).toBe("__other__");
  });
});

describe("choiceCanContinue", () => {
  it("blocks Continue on Other until there is real text", () => {
    expect(choiceCanContinue(OTHER_CHOICE_ID, "")).toBe(false);
    expect(choiceCanContinue(OTHER_CHOICE_ID, "   ")).toBe(false);
    expect(choiceCanContinue(OTHER_CHOICE_ID, "NodePort")).toBe(true);
  });

  it("allows Continue on a listed option even if Other has leftover text", () => {
    expect(choiceCanContinue("a", "")).toBe(true);
    expect(choiceCanContinue("a", "leftover")).toBe(true);
    expect(choiceCanContinue("", "typed")).toBe(false);
  });
});

describe("choiceAnswerPayload", () => {
  it("sends only the typed answer when Other is selected", () => {
    expect(choiceAnswerPayload(OTHER_CHOICE_ID, false, "  NodePort  ")).toEqual({
      optionId: OTHER_CHOICE_ID,
      skipped: false,
      customText: "NodePort",
    });
  });

  it("drops leftover Other text when a listed option is chosen", () => {
    expect(choiceAnswerPayload("a", false, "leftover")).toEqual({
      optionId: "a",
      skipped: false,
      customText: "",
    });
  });

  it("drops a typed answer on Skip", () => {
    expect(choiceAnswerPayload(OTHER_CHOICE_ID, true, "ignore")).toEqual({
      optionId: OTHER_CHOICE_ID,
      skipped: true,
      customText: "",
    });
  });

  it("caps a typed answer", () => {
    const long = "x".repeat(CUSTOM_CHOICE_MAX_CHARS + 20);
    expect(choiceAnswerPayload(OTHER_CHOICE_ID, false, long).customText.length).toBe(
      CUSTOM_CHOICE_MAX_CHARS,
    );
  });
});
