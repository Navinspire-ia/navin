// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import type { UIMessage } from "@/lib/types";

import {
  displayedPromptText,
  layoutScrollTop,
  plainAnswerText,
  userPromptAnchors,
} from "./promptNavigation";

function message(role: UIMessage["role"], content: string, id = `${role}-${content.length}`): UIMessage {
  return { id, role, content, createdAt: 0 };
}

describe("displayedPromptText", () => {
  it("hides the mode-routing slash the composer injects in Agent mode", () => {
    expect(displayedPromptText("/forge Submitting digest for signing...\n\nOperationId 93aa"))
      .toBe("Submitting digest for signing...\n\nOperationId 93aa");
    expect(displayedPromptText("/ask what is a DLL?")).toBe("what is a DLL?");
    expect(displayedPromptText("/blueprint plan the signing step")).toBe("plan the signing step");
  });

  it("keeps a bare command and non-routing commands as typed", () => {
    expect(displayedPromptText("/forge")).toBe("/forge");
    expect(displayedPromptText("/seo audit the landing page")).toBe("/seo audit the landing page");
    expect(displayedPromptText("plain text")).toBe("plain text");
  });
});

describe("userPromptAnchors", () => {
  it("labels and previews the prompt without the routing slash", () => {
    const anchors = userPromptAnchors([
      message("user", "/forge Submitting digest for signing...", "u1"),
      message("assistant", "**The Windows build died on helper DLLs.**\n\nTauri walks every `.exe`.", "a1"),
    ]);
    expect(anchors).toHaveLength(1);
    expect(anchors[0].label).toBe("Submitting digest for signing...");
    expect(anchors[0].preview).toBe("Submitting digest for signing...");
    expect(anchors[0].answerPreview).toBe(
      "The Windows build died on helper DLLs.\n\nTauri walks every .exe.",
    );
  });

  it("falls back to a numbered label when the prompt is only a routing slash", () => {
    const anchors = userPromptAnchors([message("user", "/forge", "u1")]);
    expect(anchors[0].label).toBe("/forge");
    expect(anchors[0].preview).toBe("/forge");
  });
});

describe("plainAnswerText", () => {
  it("drops markdown syntax but keeps the words", () => {
    expect(plainAnswerText("## Result\n\n**Done** in `2s` - see [docs](https://x.y/z).")).toBe(
      "Result\n\nDone in 2s - see docs.",
    );
  });

  it("removes code fences but keeps the code lines", () => {
    expect(plainAnswerText("```bash\nsigntool verify app.exe\n```")).toBe(
      "\nsigntool verify app.exe\n",
    );
  });

  it("does not eat lone asterisks or underscores", () => {
    expect(plainAnswerText("winpty-* and snake_case stay")).toBe("winpty-* and snake_case stay");
  });
});

describe("layoutScrollTop", () => {
  it("keeps unscaled measurements unchanged", () => {
    expect(layoutScrollTop(40, 100, 180, 1)).toBe(120);
  });

  it("undoes a CSS scale so the prompt does not scroll off-screen", () => {
    // 80 layout px below the scroller, painted at 1.25x (100 visual px).
    expect(layoutScrollTop(0, 0, 100, 1.25)).toBe(80);
  });

  it("rejects a zero scale", () => {
    expect(layoutScrollTop(10, 0, 50, 0)).toBe(60);
  });
});
