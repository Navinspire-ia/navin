// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComputerPermissionRules } from "./ComputerPermissionRules";

const api = vi.hoisted(() => ({
  fetchComputerPermissionRules: vi.fn(),
  saveComputerPermissionRule: vi.fn(),
  deleteComputerPermissionRule: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

const LEVELS = ["see", "click", "type", "launch"];

describe("ComputerPermissionRules", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => cleanup());

  it("shows the empty state", async () => {
    api.fetchComputerPermissionRules.mockResolvedValue({ levels: LEVELS, rules: [] });
    render(<ComputerPermissionRules />);
    expect(await screen.findByText("settings.computer.permRules.empty")).toBeTruthy();
  });

  it("lists rules, adds and removes", async () => {
    api.fetchComputerPermissionRules.mockResolvedValue({ levels: LEVELS, rules: [{ app: "Firefox", level: "see" }] });
    api.saveComputerPermissionRule.mockResolvedValue({
      levels: LEVELS, rules: [{ app: "Firefox", level: "see" }, { app: "Gimp", level: "click" }],
    });
    api.deleteComputerPermissionRule.mockResolvedValue({ levels: LEVELS, rules: [] });
    render(<ComputerPermissionRules />);
    expect(await screen.findByText("Firefox")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("settings.computer.permRules.appLabel"), { target: { value: "Gimp" } });
    fireEvent.click(screen.getByText("settings.computer.permRules.add"));
    await waitFor(() => expect(api.saveComputerPermissionRule).toHaveBeenCalledWith("tok", "Gimp", "click"));
    expect(await screen.findByText("Gimp")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("settings.computer.permRules.remove Firefox"));
    await waitFor(() => expect(api.deleteComputerPermissionRule).toHaveBeenCalledWith("tok", "Firefox"));
  });

  it("shows an error", async () => {
    api.fetchComputerPermissionRules.mockRejectedValue(new Error("boom"));
    render(<ComputerPermissionRules />);
    expect(await screen.findByText("boom")).toBeTruthy();
  });
});
