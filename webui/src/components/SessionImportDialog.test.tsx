// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as { ResizeObserver?: unknown }).ResizeObserver =
  ResizeObserverStub;

const savedRootsState: Record<string, string[]> = {};
const addImportRoot = vi.fn(async (_token: string, source: string, path: string) => {
  savedRootsState[source] = [...(savedRootsState[source] ?? []), path];
  return { saved_roots: { ...savedRootsState } };
});
const removeImportRoot = vi.fn(
  async (_token: string, source: string, path: string) => {
    savedRootsState[source] = (savedRootsState[source] ?? []).filter(
      (p) => p !== path,
    );
    return { saved_roots: { ...savedRootsState } };
  },
);
const importExternalSessions = vi.fn(async (token: string) => ({
  token,
  sources: [],
}));
const scanExternalSessions = vi.fn(async (token: string) => ({
  token,
  sources: [
    { name: "cursor", label: "Cursor", found: 2, discovered: 2, status: "ready" },
    { name: "codex", label: "Codex", found: 1, discovered: 1, status: "ready" },
  ],
  saved_roots: { ...savedRootsState },
}));

vi.mock("@/lib/api", () => ({
  addImportRoot: (...args: unknown[]) =>
    addImportRoot(...(args as [string, string, string])),
  removeImportRoot: (...args: unknown[]) =>
    removeImportRoot(...(args as [string, string, string])),
  importExternalSessions: (...args: unknown[]) =>
    importExternalSessions(...(args as [string])),
  scanExternalSessions: (...args: unknown[]) =>
    scanExternalSessions(...(args as [string])),
}));

vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ token: "test-token" }),
}));

vi.mock("@/components/dev/DevProjectSelector", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/components/dev/DevProjectSelector")
  >();
  return {
    ...actual,
    FolderBrowserDialog: ({
      open,
      onPick,
    }: {
      open: boolean;
      onPick: (path: string) => void;
    }) =>
      open ? (
        <button type="button" onClick={() => onPick("/tmp/picked")}>
          fake-browser-pick
        </button>
      ) : null,
  };
});

import { SessionImportDialog } from "@/components/SessionImportDialog";

describe("SessionImportDialog custom roots", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    for (const key of Object.keys(savedRootsState)) {
      delete savedRootsState[key];
    }
  });

  it("picks a folder with the Projects browser and adds it as a root", async () => {
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    await screen.findByRole("button", { name: /Cursor/ });
    fireEvent.click(screen.getByText("Browse folders..."));
    fireEvent.click(screen.getByText("fake-browser-pick"));

    await waitFor(() => {
      expect(addImportRoot).toHaveBeenCalledWith(
        "test-token",
        "cursor",
        "/tmp/picked",
      );
    });
    await waitFor(() => {
      expect(document.body.textContent).toContain("/tmp/picked");
    });
  });

  it("renders the source picker as a themed dropdown, not a native select", async () => {
    const { container } = render(
      <SessionImportDialog open onOpenChange={() => undefined} />,
    );

    await screen.findByRole("button", { name: /Cursor/ });
    expect(container.querySelector("select")).toBeNull();
    expect(container.querySelector('input[type="text"]')).toBeNull();

    const trigger = screen.getByRole("button", { name: /Cursor/ });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const codexItem = await screen.findByRole(
      "menuitem",
      { name: "Codex" },
      { timeout: 2000 },
    );
    fireEvent.click(codexItem);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Codex/ })).toBeTruthy();
    });
  });

  it("imports only the selected sources", async () => {
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    await screen.findByRole("button", { name: /Cursor/ });
    // Both sources default to selected; uncheck Codex.
    fireEvent.click(screen.getByRole("checkbox", { name: "Codex" }));

    const importButton = screen.getByRole("button", {
      name: /Import 2 sessions/,
    });
    fireEvent.click(importButton);

    await waitFor(() => {
      expect(importExternalSessions).toHaveBeenCalledWith("test-token", {
        source: "cursor",
      });
    });
  });

  it("disables the import button when no source is selected", async () => {
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    await screen.findByRole("button", { name: /Cursor/ });
    fireEvent.click(screen.getByRole("checkbox", { name: "Cursor" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Codex" }));

    const importButton = screen.getByRole("button", { name: /Import/ });
    expect(importButton.hasAttribute("disabled")).toBe(true);
    expect(importExternalSessions).not.toHaveBeenCalled();
  });
});
