// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  displayModelLeaf,
  formatTaskModelHint,
  taskHintFromMessages,
} from "@/lib/task-route-label";
import type { UIMessage } from "@/lib/types";

const t = ((key: string, options?: { defaultValue?: string }) =>
  options?.defaultValue ?? key) as typeof import("i18next").t;

describe("task-route-label", () => {
  it("prefers the display label over a raw slug", () => {
    expect(displayModelLeaf("Qwen 3.8 Max", "qwen/qwen3.8-max")).toBe("Qwen 3.8 Max");
    expect(displayModelLeaf("", "qwen/qwen3.8-max")).toBe("qwen3.8-max");
  });

  it("shows model and short task together, never on their own unless one is missing", () => {
    expect(formatTaskModelHint("Qwen 3.8 Max", "deep", t)).toBe("Qwen 3.8 Max · Complex");
    expect(formatTaskModelHint("DeepSeek V4 Flash", "fast", t)).toBe("DeepSeek V4 Flash · Simple");
    expect(formatTaskModelHint("GLM 5.3 Flash", "", t)).toBe("GLM 5.3 Flash");
    expect(formatTaskModelHint("", "review", t)).toBe("Review");
  });

  it("merges a role on the thinking row with the model on the answer", () => {
    expect(
      taskHintFromMessages(
        [
          {
            id: "r1",
            role: "assistant",
            content: "",
            reasoning: "...",
            taskRole: "deep",
            createdAt: 1,
          } as UIMessage,
        ],
        t,
        { modelLabel: "Qwen 3.8 Max" },
      ),
    ).toBe("Qwen 3.8 Max · Complex");
  });

  it("falls back to the following answer stamp", () => {
    expect(
      taskHintFromMessages(
        [{ id: "r1", role: "assistant", content: "", reasoning: "...", createdAt: 1 } as UIMessage],
        t,
        { modelLabel: "GLM 5.3 Flash", taskRole: "dev" },
      ),
    ).toBe("GLM 5.3 Flash · Coding");
  });

  it("reads the routed model from activity rows", () => {
    const messages = [
      {
        id: "r1",
        role: "assistant",
        content: "",
        reasoning: "thinking",
        modelLabel: "Qwen 3.8 Max",
        taskRole: "deep",
        createdAt: 1,
      },
    ] as UIMessage[];
    expect(taskHintFromMessages(messages, t)).toBe("Qwen 3.8 Max · Complex");
  });
});
