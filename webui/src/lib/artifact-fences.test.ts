// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { extractFencedArtifacts } from "./artifact-fences";

describe("extractFencedArtifacts", () => {
  it("returns empty for plain text", () => {
    expect(extractFencedArtifacts("hello")).toEqual([]);
  });

  it("extracts html and mermaid fences", () => {
    const text = [
      "Here is a preview:",
      "```html",
      "<h1>Hi</h1>",
      "```",
      "",
      "And a diagram:",
      "```mermaid",
      "graph TD; A-->B;",
      "```",
    ].join("\n");
    const found = extractFencedArtifacts(text);
    expect(found).toHaveLength(2);
    expect(found[0]?.type).toBe("html");
    expect(found[0]?.content).toContain("<h1>Hi</h1>");
    expect(found[1]?.type).toBe("mermaid");
    expect(found[1]?.content).toContain("graph TD");
  });

  it("dedupes identical fences", () => {
    const block = "```html\n<p>x</p>\n```";
    const found = extractFencedArtifacts(`${block}\n\n${block}`);
    expect(found).toHaveLength(1);
  });
});
