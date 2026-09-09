// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = readFileSync(resolve(__dirname, "DevWorkbench.tsx"), "utf8");

describe("Code toolbar overflow", () => {
  it("opens the terminal maximized instead of as a docked strip", () => {
    const open = source.slice(source.indexOf("const openTerminalPanel"));
    expect(open.slice(0, 280)).toContain("setTerminalMaximized(true)");
  });

  it("keeps Debug and later modules in the Other list, not the main bar", () => {
    expect(source).toContain('key: "debug"');
    expect(source).toContain('key: "skills"');
    expect(source).toContain('key: "evolve"');
    expect(source).not.toContain('data-testid="dev-toolbar-debug"');
    expect(source).not.toContain('data-testid="dev-toolbar-installSkill"');
    expect(source).not.toContain("dev-rail-debug");
    expect(source).toContain("overflowMenuItems");
    expect(source).toContain('data-testid={`dev-rail-${entry.key}`}');
  });

  it("does not duplicate the chat review bar above the editor", () => {
    expect(source).not.toContain('data-testid="dev-review-bar"');
  });

  it("opens every pending review file as a colored diff and can return", () => {
    expect(source).toContain("const openReviewChanges");
    expect(source).toContain("mergeReviewTabs");
    expect(source).toContain("REVIEW_VISIBLE_FILE_LIMIT");
    expect(source).toContain("showDiff(relativeOf(first.path)");
    expect(source).toContain("onClick={() => openReviewChanges()}");
    expect(source).toContain('tx("dev.change", "Change")');
    expect(source).toContain("onBack={closeDiff}");
    expect(source).toContain('data-testid="dev-review-diff-files"');
    expect(source).toContain('data-testid="dev-review-files-menu"');
    expect(source).toContain('data-testid="dev-rail-files"');
    expect(source).toContain('data-testid="dev-editor-change"');
    expect(source).toContain("tabs.length > 0 && mode !== \"diff\"");
    expect(source).toContain('data-testid="dev-diff-shell"');
    expect(source).toContain("defaultEditorPreviewOn");
    expect(source).toContain("revealReviewedSource");
  });

  it("hides open files on the collapsed rail until File is clicked", () => {
    expect(source).toContain('data-testid="dev-rail-files"');
    expect(source).toContain("tabs.length > 0");
    expect(source).toContain("setRailFilesOpen");
    expect(source).toContain('tx("dev.rail.file", "File")');
  });

  it("pins Other next to Browser instead of letting them overlap", () => {
    expect(source).toContain('key: "rules"');
    const toolbar = source.slice(source.indexOf('data-testid="dev-workbench-toolbar"'));
    const other = toolbar.indexOf("<DevOtherMenu");
    const scrollClose = toolbar.lastIndexOf("</div>", other);
    expect(other).toBeGreaterThan(0);
    expect(scrollClose).toBeGreaterThan(0);
    expect(scrollClose).toBeLessThan(other);
  });
});
