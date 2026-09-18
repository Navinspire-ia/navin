// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vitest.config.ts does not set globals:true, so RTL auto-cleanup never
// runs: without this, dialogs from earlier tests stay mounted and their
// stale state leaks into later assertions.
afterEach(cleanup);

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
    FolderBrowserDialog: (props: {
      open: boolean;
      onPick: (path: string) => void;
      showHidden?: boolean;
    }) => {
      latestBrowserProps = props;
      return props.open ? (
        <button type="button" onClick={() => props.onPick("/tmp/picked")}>
          fake-browser-pick
        </button>
      ) : null;
    },
  };
});

import { SessionImportDialog } from "@/components/SessionImportDialog";

let latestBrowserProps: { showHidden?: boolean } = {};

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

  it("shows hidden folders in the import browser (dotfile stores)", async () => {
    // Regression #4: ~/.claude, ~/.codex, ~/.omp are hidden dirs; the
    // import picker must opt into showHidden or they are unusable.
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    await screen.findByRole("button", { name: /Cursor/ });
    fireEvent.click(screen.getAllByText("Browse folders...")[0]);
    fireEvent.click(screen.getByText("fake-browser-pick"));
    expect(latestBrowserProps.showHidden).toBe(true);
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

  it("shows a scan-slow message, not the generic engine timeout (issue #3)", async () => {
    // Regression #3: a 20s read timeout surfaced "engine took too long"
    // copy; the scan now gets the slow timeout and the dialog explains.
    scanExternalSessions.mockRejectedValueOnce(
      new Error("Request took too long"),
    );
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    const alert = await screen.findByRole("alert", {}, { timeout: 2000 });
    expect(alert.textContent).toContain("lent");
    expect(alert.textContent).not.toContain("took too long");
    // The source dropdown stays usable despite the failed scan.
    expect(screen.getByRole("button", { name: /Cursor/ })).toBeTruthy();
  });

  it("keeps the source dropdown usable after a scan failure (issue #3)", async () => {
    // Regression #3: a scan timeout used to leave the dropdown stuck on
    // the raw default "cursor" with no items to pick from.
    scanExternalSessions.mockRejectedValueOnce(new Error("scan timeout"));
    render(<SessionImportDialog open onOpenChange={() => undefined} />);

    // The trigger still shows a human label, not the raw source id.
    const trigger = await screen.findByRole("button", { name: /Cursor/ });
    fireEvent.pointerDown(trigger);
    fireEvent.click(trigger);
    // The fallback list offers every known source, not just cursor.
    const item = await screen.findByRole(
      "menuitem",
      { name: "oh-my-pi" },
      { timeout: 2000 },
    );
    fireEvent.click(item);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /oh-my-pi/ })).toBeTruthy();
    });

    fireEvent.click(screen.getAllByText("Browse folders...")[0]);
    fireEvent.click(screen.getByText("fake-browser-pick"));
    await waitFor(() => {
      expect(addImportRoot).toHaveBeenCalledWith(
        "test-token",
        "oh-my-pi",
        "/tmp/picked",
      );
    });
  });
});
