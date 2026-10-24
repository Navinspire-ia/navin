// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./MonacoDiff", () => ({
  default: ({ original, modified, sideBySide }: { original: string; modified: string; sideBySide: boolean }) => (
    <div data-testid="monaco-diff" data-side={String(sideBySide)}>
      {original}|{modified}
    </div>
  ),
}));

import { ActivityInspector } from "@/components/thread/activity/ActivityInspector";
import { pairSideBySide, hunksToTexts } from "@/components/thread/activity/sideBySideDiff";
import { closeInspector, openInspector } from "@/lib/activity-inspector";

afterEach(() => {
  act(() => closeInspector());
  cleanup();
});

describe("ActivityInspector", () => {
  it("stays closed until something is inspected", () => {
    render(<ActivityInspector />);
    expect(screen.queryByTestId("activity-inspector")).toBeNull();
  });

  it("opens a test run on its report, masks secrets in the output and closes on Escape", async () => {
    render(<ActivityInspector />);
    act(() =>
      openInspector({
        kind: "command",
        id: "c1",
        command: "pytest -q",
        status: "error",
        output: "API_KEY=hunter2\nFAILED tests/test_a.py::test_x - boom\n1 failed, 3 passed in 0.2s",
      }),
    );
    expect(screen.getByTestId("activity-test-report")).toBeTruthy();
    expect(screen.getByText("tests/test_a.py")).toBeTruthy();
    fireEvent.click(screen.getByTestId("activity-inspector-tab-output"));
    expect(screen.getByText(/API_KEY=\[masque\]/)).toBeTruthy();
    expect(screen.queryByText(/hunter2/)).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("activity-inspector")).toBeNull());
  });

  it("shows a plain command without a test tab", () => {
    render(<ActivityInspector />);
    act(() => openInspector({ kind: "command", id: "c2", command: "ls", status: "done", output: "a.txt" }));
    expect(screen.queryByTestId("activity-inspector-tab-tests")).toBeNull();
    expect(screen.getByText("a.txt")).toBeTruthy();
  });

  it("renders a diff in Monaco and toggles side by side", async () => {
    render(<ActivityInspector />);
    act(() => openInspector({ kind: "diff", id: "d1", path: "src/a.ts", original: "old", modified: "new" }));
    const diff = await screen.findByTestId("monaco-diff");
    expect(diff.textContent).toBe("old|new");
    expect(diff.getAttribute("data-side")).toBe("true");
    fireEvent.click(screen.getByTestId("activity-inspector-diff-mode"));
    expect(screen.getByTestId("monaco-diff").getAttribute("data-side")).toBe("false");
    fireEvent.click(screen.getByTestId("activity-inspector-close"));
    await waitFor(() => expect(screen.queryByTestId("activity-inspector")).toBeNull());
  });
});

describe("hunksToTexts", () => {
  it("rebuilds the old and new side of each hunk", () => {
    const lines = [
      { kind: "context" as const, content: "a" },
      { kind: "delete" as const, content: "b" },
      { kind: "add" as const, content: "B" },
    ];
    expect(hunksToTexts([{ lines }, { lines: [{ kind: "add" as const, content: "z" }] }])).toEqual({
      original: "a\nb\n\n",
      modified: "a\nB\n\nz",
    });
    expect(pairSideBySide(lines)).toHaveLength(2);
  });
});
