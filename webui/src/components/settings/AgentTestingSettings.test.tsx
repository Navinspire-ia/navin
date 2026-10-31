// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SettingsPayload } from "@/lib/types";

const mock = vi.hoisted(() => ({
  updateSettings: vi.fn(),
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal<typeof import("react-i18next")>(),
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
    i18n: { language: "en" },
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  updateSettings: mock.updateSettings,
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));

import { AgentTestingSettings } from "./SettingsView";

afterEach(() => {
  cleanup();
  mock.updateSettings.mockReset();
});

function payload(depth?: "essential" | "thorough"): SettingsPayload {
  return { agent: { testing_depth: depth } } as unknown as SettingsPayload;
}

describe("AgentTestingSettings", () => {
  it("defaults to essential, saves thorough, and goes back to the saved value on error", async () => {
    render(<AgentTestingSettings settings={payload()} />);
    const essential = screen.getByRole("button", { name: "Essential" });
    const thorough = screen.getByRole("button", { name: "Thorough" });
    expect(essential.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/One or two tests per change/)).toBeTruthy();

    mock.updateSettings.mockResolvedValueOnce(payload("thorough"));
    fireEvent.click(thorough);
    await waitFor(() => expect(mock.updateSettings).toHaveBeenCalledWith("tok", { testingDepth: "thorough" }));
    await waitFor(() => expect(thorough.getAttribute("aria-pressed")).toBe("true"));
    expect(screen.getByText(/failure cases included/)).toBeTruthy();

    mock.updateSettings.mockRejectedValueOnce(new Error("testing_depth must be essential or thorough"));
    fireEvent.click(essential);
    expect((await screen.findByRole("alert")).textContent).toContain("testing_depth must be essential or thorough");
    expect(thorough.getAttribute("aria-pressed")).toBe("true");
    expect(mock.updateSettings).toHaveBeenCalledTimes(2);
  });
});
