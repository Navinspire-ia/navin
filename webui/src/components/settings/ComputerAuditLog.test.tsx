// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComputerAuditLog } from "./ComputerAuditLog";

const api = vi.hoisted(() => ({
  fetchComputerAuditSessions: vi.fn(),
  fetchComputerAuditSession: vi.fn(),
}));

vi.mock("@/lib/api", () => api);
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) => (options?.count !== undefined ? `${key}:${options.count}` : key),
  }),
}));

const SUMMARY = {
  id: "s1", chat: "websocket:a", started: "2026-10-23 10:00:00", ask: "destructive",
  backend: "x11", steps: 2, denied: 1, broken: 0,
};

describe("ComputerAuditLog", () => {
  beforeEach(() => vi.resetAllMocks());
  afterEach(() => cleanup());

  it("shows the empty state", async () => {
    api.fetchComputerAuditSessions.mockResolvedValue({ root: "/m/computer", sessions: [] });
    render(<ComputerAuditLog />);
    expect(await screen.findByText("settings.computer.audit.empty")).toBeTruthy();
  });

  it("lists sessions and opens the steps of one", async () => {
    api.fetchComputerAuditSessions.mockResolvedValue({ root: "/m/computer", sessions: [SUMMARY] });
    api.fetchComputerAuditSession.mockResolvedValue({
      root: "/m/computer",
      session: {
        id: "s1", meta: {}, total: 2, broken: 0,
        steps: [
          { step: 1, t: 0.4, action: "screenshot", args: {}, result: "ok", denied: "", rule: "", screenshot: "step-0001.png" },
          { step: 2, t: 1.25, action: "type", args: { text: "x" }, result: "", denied: "level see", rule: "Firefox", screenshot: "" },
        ],
      },
    });
    render(<ComputerAuditLog />);
    expect(await screen.findByText("2026-10-23 10:00:00 - websocket:a")).toBeTruthy();
    expect(screen.getByText("settings.computer.audit.deniedCount:1")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("settings.computer.audit.open s1"));
    await waitFor(() => expect(api.fetchComputerAuditSession).toHaveBeenCalledWith("tok", "s1"));
    expect(await screen.findByText("step-0001.png")).toBeTruthy();
    expect(screen.getByText('{"text":"x"} [Firefox] level see')).toBeTruthy();
    expect(screen.getByText("type (settings.computer.audit.denied)")).toBeTruthy();
    fireEvent.click(screen.getByText("settings.computer.audit.close"));
    expect(screen.queryByText("step-0001.png")).toBeNull();
  });

  it("shows an error and reloads", async () => {
    api.fetchComputerAuditSessions.mockRejectedValueOnce(new Error("boom"));
    api.fetchComputerAuditSessions.mockResolvedValueOnce({ root: "/m", sessions: [SUMMARY] });
    render(<ComputerAuditLog />);
    expect(await screen.findByText("boom")).toBeTruthy();
    fireEvent.click(screen.getByText("settings.computer.audit.reload"));
    expect(await screen.findByText("2026-10-23 10:00:00 - websocket:a")).toBeTruthy();
    expect(api.fetchComputerAuditSessions).toHaveBeenCalledTimes(2);
  });
});
