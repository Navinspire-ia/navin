// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import {
  designModePrompt,
  matchSourceFile,
  parsePickedElement,
  pickedElementLabel,
  popoverPlacement,
  sourceLookup,
  type PickedElement,
} from "./designMode";

const raw = {
  tag: "BUTTON",
  id: "",
  classes: "flex items-center gap-2 px-2 hover:bg-muted",
  testId: "project-group-header",
  text: "navin-ai-v2",
  selector: 'nav > button[data-testid="project-group-header"]',
  rect: { x: 16.4, y: 64, width: 250, height: 28 },
  viewport: { width: 1280, height: 800 },
  component: "ProjectGroupHeader",
  ancestors: ["ProjectGroupHeader", "ChatList2", "Sidebar", "App"],
  framework: "react",
  source: { file: "/src/components/sidebar/ProjectGroupHeader.tsx", line: 42 },
  attributes: { "aria-label": "Toggle project", type: "button" },
  url: "http://127.0.0.1:5173/#/code",
  title: "Navin",
};

describe("parsePickedElement", () => {
  it("normalizes what the probe posts and rejects garbage", () => {
    const element = parsePickedElement(raw);
    expect(element).not.toBeNull();
    expect(element!.tag).toBe("button");
    expect(element!.rect).toEqual({ x: 16, y: 64, width: 250, height: 28 });
    expect(element!.source).toEqual({ file: "/src/components/sidebar/ProjectGroupHeader.tsx", line: 42 });
    expect(element!.attributes).toEqual({ "aria-label": "Toggle project", type: "button" });
    expect(parsePickedElement(null)).toBeNull();
    expect(parsePickedElement({ rect: {} })).toBeNull();
    expect(parsePickedElement({ tag: "div", source: { line: 3 } })!.source).toBeNull();
  });
});

describe("pickedElementLabel", () => {
  it("prefers the component, then the id, then the first plain class", () => {
    const element = parsePickedElement(raw)!;
    expect(pickedElementLabel(element)).toBe("ProjectGroupHeader \u00b7 button");
    expect(pickedElementLabel({ ...element, component: "", id: "root" })).toBe("button#root");
    expect(pickedElementLabel({ ...element, component: "", classes: "hover:bg-muted flex" })).toBe(
      "button.flex",
    );
    expect(pickedElementLabel({ ...element, component: "", classes: "" })).toBe("button");
  });
});

describe("popoverPlacement", () => {
  const frame = { width: 800, height: 600 };
  const popover = { width: 340, height: 150 };

  it("sits below the element, left-aligned, clamped to the frame", () => {
    expect(popoverPlacement({ x: 20, y: 40, width: 100, height: 30 }, frame, popover)).toEqual({
      left: 20,
      top: 78,
      above: false,
    });
    expect(popoverPlacement({ x: 700, y: 40, width: 100, height: 30 }, frame, popover).left).toBe(
      800 - 340 - 8,
    );
  });

  it("flips above when there is no room below, and clamps when neither fits", () => {
    expect(popoverPlacement({ x: 20, y: 500, width: 100, height: 30 }, frame, popover)).toEqual({
      left: 20,
      top: 500 - 8 - 150,
      above: true,
    });
    const tiny = popoverPlacement({ x: 0, y: 100, width: 10, height: 10 }, { width: 400, height: 200 }, popover);
    expect(tiny.above).toBe(false);
    expect(tiny.top).toBe(200 - 150 - 8);
  });
});

describe("sourceLookup / matchSourceFile", () => {
  it("relativizes absolute paths under the project", () => {
    const lookup = sourceLookup("/home/me/proj/webui/src/App.tsx", "/home/me/proj/");
    expect(lookup).toEqual({ relative: "webui/src/App.tsx", suffix: "webui/src/App.tsx", name: "App.tsx" });
    expect(matchSourceFile(lookup, [])).toEqual({ path: "webui/src/App.tsx", name: "App.tsx", kind: "file" });
  });

  it("strips dev-server URLs and Vite /@fs/ prefixes down to a suffix", () => {
    expect(sourceLookup("http://localhost:5173/src/components/A.tsx?t=123", null)).toEqual({
      relative: null,
      suffix: "src/components/A.tsx",
      name: "A.tsx",
    });
    expect(sourceLookup("/@fs/home/me/proj/webui/src/B.vue", "/home/me/proj").relative).toBe(
      "webui/src/B.vue",
    );
  });

  it("matches the project file whose path ends with the suffix", () => {
    const lookup = sourceLookup("/src/components/A.tsx", null);
    const items = [
      { path: "docs/src/components/A.tsx.md", name: "A.tsx.md", kind: "file" as const },
      { path: "webui/src/components/A.tsx", name: "A.tsx", kind: "file" as const },
      { path: "webui/src/components", name: "components", kind: "directory" as const },
    ];
    expect(matchSourceFile(lookup, items)?.path).toBe("webui/src/components/A.tsx");
    expect(matchSourceFile(sourceLookup("/src/Nope.tsx", null), items)).toBeNull();
  });
});

describe("designModePrompt", () => {
  const element: PickedElement = parsePickedElement(raw)!;

  it("puts the request first, then the facts the agent needs to find the code", () => {
    const text = designModePrompt(element, "  make it bold  ", "webui/src/components/sidebar/ProjectGroupHeader.tsx");
    const lines = text.split("\n");
    expect(lines[0]).toBe("make it bold");
    expect(text).toContain("- Component: ProjectGroupHeader <button>, inside ChatList2 > Sidebar > App");
    expect(text).toContain("- Source: @webui/src/components/sidebar/ProjectGroupHeader.tsx (around line 42)");
    expect(text).toContain('- Selector: nav > button[data-testid="project-group-header"]');
    expect(text).toContain('- Text: "navin-ai-v2"');
    expect(text).toContain('- Attributes: aria-label="Toggle project", type="button"');
    expect(text).toContain("- Box: 250 x 28 px at (16, 64) in a 1280 x 800 viewport");
    expect(text).not.toMatch(/[\u2013\u2014]/);
  });

  it("falls back to the served path and a default request", () => {
    const text = designModePrompt({ ...element, text: "", attributes: {} }, "", null);
    expect(text.startsWith("Improve this element.")).toBe(true);
    expect(text).toContain("- Source (as served by the dev server): /src/components/sidebar/ProjectGroupHeader.tsx");
    expect(text).not.toContain("- Text:");
    expect(text).not.toContain("- Attributes:");
  });
});
