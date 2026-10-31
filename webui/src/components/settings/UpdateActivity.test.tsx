// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({
  fetchUpdateHistory: vi.fn(),
  updatePreferences: vi.fn(),
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; engine?: string }) =>
      (options?.defaultValue ?? key).replace("{{engine}}", options?.engine ?? ""),
    i18n: { language: "en" },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  fetchUpdateHistory: mock.fetchUpdateHistory,
  updatePreferences: mock.updatePreferences,
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));

import { VersionCheckRow } from "./SettingsView";
import { UpdateActivity } from "./UpdateActivity";

afterEach(() => {
  cleanup();
  mock.fetchUpdateHistory.mockReset();
  mock.updatePreferences.mockReset();
});

const status = {
  state: "ready",
  progress: 100,
  downloadedBytes: 0,
  totalBytes: 0,
  strategy: { installKind: "windows-setup", engine: "tauri", mode: "passive" },
};

describe("UpdateActivity", () => {
  it("lists checks, installs and errors newest first with the engine that installs", async () => {
    mock.fetchUpdateHistory.mockResolvedValue({
      status,
      history: [
        { at: 1793300000, event: "error", version: "2.4.4", engine: "tauri", ok: false, detail: "download: timed out" },
        { at: 1793290000, event: "installed", version: "2.4.3", engine: "tauri", ok: true, detail: "" },
        { at: 1793280000, event: "checked", version: "", engine: "navin", ok: true, detail: "" },
      ],
    });
    render(<UpdateActivity token="tok" />);
    const list = await screen.findByTestId("update-history");
    const rows = list.querySelectorAll("li");
    expect(Array.from(rows).map((row) => row.getAttribute("data-event"))).toEqual(["error", "installed", "checked"]);
    expect(rows[0].textContent).toContain("Failed");
    expect(rows[0].textContent).toContain("v2.4.4");
    expect(rows[0].textContent).toContain("download: timed out");
    expect(rows[1].textContent).toContain("Installed");
    expect(screen.getByTestId("update-engine").textContent).toBe("Installed by: Desktop updater");
    expect(mock.fetchUpdateHistory).toHaveBeenCalledWith("tok", 8);
  });

  it("says so when nothing happened yet", async () => {
    mock.fetchUpdateHistory.mockResolvedValue({ status: { ...status, strategy: undefined }, history: [] });
    render(<UpdateActivity token="tok" />);
    expect(await screen.findByText("No update activity yet.")).toBeTruthy();
    expect(screen.queryByTestId("update-engine")).toBeNull();
  });

  it("shows the error and loads again from Refresh", async () => {
    mock.fetchUpdateHistory.mockRejectedValueOnce(new Error("HTTP 503"));
    render(<UpdateActivity token="tok" />);
    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 503");
    mock.fetchUpdateHistory.mockResolvedValueOnce({
      status,
      history: [{ at: 1793280000, event: "downloaded", version: "2.4.4", engine: "navin", ok: true, detail: "" }],
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect((await screen.findByTestId("update-history")).textContent).toContain("Downloaded");
    expect(mock.fetchUpdateHistory).toHaveBeenCalledTimes(2);
  });
});

describe("VersionCheckRow update preferences", () => {
  const preferences = {
    enabled: true,
    autoCheck: true,
    autoInstall: true,
    channel: "stable" as const,
    configured: true,
    skippedVersion: "",
  };

  it("offers the enterprise channel and saves automatic installs", async () => {
    mock.updatePreferences.mockImplementation(async (_token: string, values: Record<string, unknown>) => ({
      autoCheck: true,
      autoInstall: true,
      channel: "stable",
      skippedVersion: "",
      ...values,
    }));
    render(<VersionCheckRow currentVersion="2.4.3" preferences={preferences} />);
    const channel = screen.getByTestId("update-channel") as HTMLSelectElement;
    expect(Array.from(channel.options).map((option) => option.value)).toEqual(["stable", "beta", "enterprise"]);
    expect(screen.getByText(/install when no agent is working/)).toBeTruthy();

    fireEvent.change(channel, { target: { value: "enterprise" } });
    await waitFor(() => expect(mock.updatePreferences).toHaveBeenCalledWith("tok", { channel: "enterprise" }));
    expect(channel.value).toBe("enterprise");

    const autoInstall = screen.getByTestId("update-auto-install") as HTMLInputElement;
    fireEvent.click(autoInstall);
    await waitFor(() => expect(mock.updatePreferences).toHaveBeenCalledWith("tok", { autoInstall: false }));
    expect(autoInstall.checked).toBe(false);
    expect(screen.queryByText(/install when no agent is working/)).toBeNull();
  });

  it("goes back to the saved choice when saving fails", async () => {
    mock.updatePreferences.mockRejectedValue(new Error("invalid update channel"));
    render(<VersionCheckRow currentVersion="2.4.3" preferences={preferences} />);
    const channel = screen.getByTestId("update-channel") as HTMLSelectElement;
    fireEvent.change(channel, { target: { value: "beta" } });
    expect(await screen.findByText("invalid update channel")).toBeTruthy();
    expect(channel.value).toBe("stable");
  });

  it("needs automatic checks for automatic installs", () => {
    render(<VersionCheckRow currentVersion="2.4.3" preferences={{ ...preferences, autoCheck: false }} />);
    expect((screen.getByTestId("update-auto-install") as HTMLInputElement).disabled).toBe(true);
  });
});
