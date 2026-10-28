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

describe("App factory entries", () => {
  it("adds Mobile/Desktop apps and drops Tests/Mobile from Other", () => {
    expect(source).toContain('key: kind === "mobile" ? "appsMobile" : "appsDesktop"');
    // The explanation modal is mounted once in each layout (collapsed rail and full workbench).
    expect(source.split("{appFactoryDialogEl}").length - 1).toBe(2);
    expect(source).not.toMatch(/\{appFactoryDialogEl\}\s*\{appFactoryDialogEl\}/);
    expect(source).toContain("appFactoryCommand(");
    // A target in a menu only opens the confirmation; the build starts on its button.
    expect(source).toContain("onSelect: () => setAppFactoryConfirm(target.id)");
    expect(source).not.toContain("onRunAction(appFactoryCommand(target.id))");
    expect(source).toContain("onStart={setAppFactoryConfirm}");
    expect(source.split("<AppFactoryConfirmDialog").length - 1).toBe(1);
    // Real platform marks; no NEW pill on the Mobile/Desktop apps entries,
    // the "What is this?" item stays in their menu instead.
    expect(source).toContain('icon: <PlatformLogo id={target.id} className="h-3.5 w-3.5" />');
    const appFactoryItems = source.slice(
      source.indexOf('key: kind === "mobile" ? "appsMobile" : "appsDesktop"'),
      source.indexOf('key: "learnMore"'),
    );
    expect(appFactoryItems).not.toContain("reverseNew");
    const appEntriesStart = source.indexOf("...appFactoryMenuItems().map((item) => ({");
    const appEntries = source.slice(appEntriesStart, appEntriesStart + 400);
    expect(appEntries).not.toContain("reverseNew");
    expect(appEntries).not.toContain("onBadgeClick");
    expect(appEntries).toContain("menu: item.children ?? []");
    const railBadge = source.slice(source.indexOf("dev-rail-${entry.key}-badge"), source.indexOf("dev-rail-${entry.key}-badge") + 700);
    expect(railBadge).toContain("{entry.badge.text}");
    expect(railBadge).not.toContain("h-1.5 w-1.5");
    const overflow = source.slice(
      source.indexOf("const overflowMenuItems"),
      source.indexOf("const restoreAnd"),
    );
    expect(overflow).not.toContain('key: "tests"');
    expect(overflow).not.toContain('key: "mobile"');
    expect(source).not.toContain("onSeed={onSeedChat}\n");
  });

  it("starts the build in the chat without reopening the workbench column", () => {
    const confirm = source.slice(
      source.indexOf("<AppFactoryConfirmDialog"),
      source.indexOf("appFactoryCommand(id)"),
    );
    expect(confirm).not.toContain("onMaximizeChat");
  });
});

describe("Collapsed rail launchers", () => {
  const railStart = source.indexOf('key: "team"');
  const railEnd = source.indexOf("<DevOtherMenu", railStart);
  const rail = source.slice(railStart, railEnd);

  it("shows a single Other entry: no inline sub menu next to DevOtherMenu", () => {
    // Regression: an inline `key: "other"` rail entry duplicated the Other button.
    expect(rail).not.toContain('key: "other"');
    expect(source.split('key: "other"').length - 1).toBe(0);
    // The rail's DevOtherMenu keeps Install skill / Rules / Guardrails / Graph,
    // only hiding the modules that already have their own rail tab.
    const railOther = source.slice(railEnd, source.indexOf("/>", railEnd));
    expect(railOther).toContain('["evolve", "bench", "board"]');
    for (const key of ["skills", "rules", "guardrails", "graph"]) {
      expect(railOther, key).toContain(`mode === "${key}"`);
    }
  });

  it("keeps the layout for Team, Cyber and Reverse: the action lives in the chat or Settings", () => {
    for (const key of ['key: "team"', 'key: "cyber"', 'key: "reverse"']) {
      const entry = rail.slice(rail.indexOf(key), rail.indexOf("go: () =>", rail.indexOf(key)));
      expect(entry, key).toContain("keepLayout: true");
    }
  });

  it("only restores the workbench for entries that render in it", () => {
    expect(source).toContain("onClick={entry.keepLayout ? entry.go : restoreAnd(entry.go)}");
    expect(source).not.toContain("onClick={restoreAnd(entry.go)}");
    const agi = source.slice(
      source.indexOf('key: "agi"'),
      source.indexOf('key: "computer"', source.indexOf('key: "agi"')),
    );
    expect(agi).not.toContain("keepLayout");
  });
});
