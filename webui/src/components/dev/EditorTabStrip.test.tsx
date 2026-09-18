// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EditorTabStrip, type WorkbenchTab } from "./EditorTabStrip";

// vitest.config.ts does not set globals:true, so RTL auto-cleanup never
// runs: without this, tabs from earlier tests leak into later assertions.
afterEach(cleanup);

const TABS: WorkbenchTab[] = [
  { path: "/repo/src/main.ts", displayPath: "src/main.ts", name: "main.ts" },
  { path: "/repo/src/app.tsx", displayPath: "src/app.tsx", name: "app.tsx" },
];

function renderStrip(overrides?: Partial<Parameters<typeof EditorTabStrip>[0]>) {
  const props = {
    tabs: TABS,
    isTabActive: (tab: WorkbenchTab) => tab.name === "main.ts",
    isDirty: (tab: WorkbenchTab) => tab.name === "app.tsx",
    textClassName: (tab: WorkbenchTab) =>
      tab.name === "main.ts" ? "text-red-500" : undefined,
    onSelect: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  render(<EditorTabStrip {...props} />);
  return props;
}

describe("EditorTabStrip", () => {
  it("renders one row per open tab", () => {
    renderStrip();
    expect(screen.getByTestId("editor-tabs").children.length).toBe(TABS.length);
    expect(screen.getByTitle("src/main.ts").textContent).toBe("main.ts");
    expect(screen.getByTitle("src/app.tsx").textContent).toBe("app.tsx");
  });

  it("renders nothing when no file is open", () => {
    renderStrip({ tabs: [] });
    expect(screen.queryByTestId("editor-tabs")).toBeNull();
  });

  it("selects a tab on click and closes via the X button", () => {
    const props = renderStrip();
    fireEvent.click(screen.getByTitle("src/app.tsx"));
    expect(props.onSelect).toHaveBeenCalledWith(TABS[1]);
    // Every tab carries a close button; scope to the active row.
    const activeRow = screen.getByTitle("src/main.ts").closest("div")!;
    fireEvent.click(
      within(activeRow as HTMLElement).getByRole("button", { name: "Close tab" }),
    );
    expect(props.onClose).toHaveBeenCalledWith(TABS[0].path);
  });

  it("middle-click closes the tab like the toolbar X", () => {
    const props = renderStrip();
    // fireEvent.auxClick is not exported by this RTL version: dispatch the
    // same DOM event the onAuxClick handler listens for.
    fireEvent(
      screen.getByTitle("src/app.tsx"),
      new MouseEvent("auxclick", { button: 1, bubbles: true }),
    );
    expect(props.onClose).toHaveBeenCalledWith(TABS[1].path);
  });

  it("marks the active tab and surfaces the dirty dot and color cascade", () => {
    renderStrip();
    const activeRow = screen.getByTitle("src/main.ts").closest("div")!;
    expect(activeRow.className).toContain("bg-background");
    const idleRow = screen.getByTitle("src/app.tsx").closest("div")!;
    expect(idleRow.className).toContain("text-muted-foreground");
    // Dirty dot only on the dirty tab.
    expect(screen.getAllByLabelText("Unsaved changes").length).toBe(1);
    // textClassName from the shell (error cascade) applies.
    expect(screen.getByTitle("src/main.ts").className).toContain("text-red-500");
  });
});
