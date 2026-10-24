// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  firstOutputLine,
  outputPreviewLines,
  previewGenericArgsObject,
  previewGenericToolArgs,
  tailToolOutput,
  toolEventOutputText,
} from "./activity-preview";

describe("previewGenericToolArgs", () => {
  it("shows test_run target and runner instead of an empty line", () => {
    expect(
      previewGenericToolArgs('{"action":"run","target":"webui","runner":"vitest"}'),
    ).toBe("target: webui · runner: vitest");
  });

  it("keeps a detect-only call readable", () => {
    expect(previewGenericToolArgs('{"action":"detect"}')).toBe("action: detect");
  });

  it("still previews search-style keys", () => {
    expect(previewGenericToolArgs('{"query":"login","path":"src"}')).toBe(
      "query: login · path: src",
    );
  });

  it("joins a short path list", () => {
    expect(
      previewGenericArgsObject({ paths: ["a.ts", "b.ts", "c.ts"], action: "check" }),
    ).toBe("paths: a.ts, b.ts +1");
  });
});

describe("toolEventOutputText", () => {
  it("prefers the finished result over the live tail", () => {
    expect(
      toolEventOutputText({
        result: "pytest: 12 passed (800 ms)",
        output: "collecting...",
      }),
    ).toBe("pytest: 12 passed (800 ms)");
  });

  it("reads wrapped content from a result object", () => {
    expect(toolEventOutputText({ result: { content: "PASS" } })).toBe("PASS");
  });

  it("falls back to live output while the tool is still running", () => {
    expect(toolEventOutputText({ output: "running suite..." })).toBe("running suite...");
  });
});

describe("firstOutputLine", () => {
  it("returns the first non-empty line so the row can show the verdict", () => {
    expect(firstOutputLine("\npytest: 2 failed (1200 ms)\n\nFailures:")).toBe(
      "pytest: 2 failed (1200 ms)",
    );
  });
});

describe("tailToolOutput", () => {
  it("keeps short text intact", () => {
    expect(tailToolOutput("ok", 20)).toBe("ok");
  });

  it("keeps the end of a long log", () => {
    const text = `${"x".repeat(40)}\nFAILED`;
    expect(tailToolOutput(text, 10)).toContain("FAILED");
  });
});

describe("outputPreviewLines", () => {
  it("keeps the last non-empty lines and the total, without ANSI codes", () => {
    const { lines, total } = outputPreviewLines("\u001b[32mok\u001b[0m 1\n\nok 2\r\nok 3\n  \n", 2);
    expect(lines).toEqual(["ok 2", "ok 3"]);
    expect(total).toBe(3);
    expect(outputPreviewLines("", 2)).toEqual({ lines: [], total: 0 });
    expect(outputPreviewLines(undefined)).toEqual({ lines: [], total: 0 });
  });
});
