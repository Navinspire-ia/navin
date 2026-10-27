// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchRemoteChannels: vi.fn(),
  updateRemoteChannel: vi.fn(),
  testRemoteChannels: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));

import i18n from "@/i18n";
import RemoteChannelsPanel from "./RemoteChannelsPanel";

const row = (channel: string, extra: Record<string, unknown> = {}) => ({
  channel,
  notify: false,
  approve: false,
  chat: true,
  to: "",
  enabled: false,
  configured: false,
  ready: false,
  hint: "",
  ...extra,
});

const payload = {
  channels: [
    row("email"),
    row("whatsapp", { to: "+33600000000", configured: true, ready: true, enabled: true, approve: true }),
    row("telegram", { configured: true }),
    row("teams"),
  ],
};

beforeEach(async () => {
  for (const fn of Object.values(api)) fn.mockReset();
  await i18n.changeLanguage("en");
});
afterEach(() => cleanup());

describe("RemoteChannelsPanel", () => {
  it("is translated: French labels come from the locale files", async () => {
    await i18n.changeLanguage("fr");
    api.fetchRemoteChannels.mockResolvedValue(payload);
    render(<RemoteChannelsPanel />);
    expect(await screen.findAllByRole("button", { name: "Autorisations" })).toHaveLength(4);
    expect(screen.getByLabelText("Destinataire Telegram")).toBeTruthy();
    expect(screen.getAllByText("Configurer dans Réglages > Canaux")).toHaveLength(2);
  });

  it("lists the four channels with their state and a setup link when not configured", async () => {
    api.fetchRemoteChannels.mockResolvedValue(payload);
    render(<RemoteChannelsPanel />);
    expect(await screen.findByTestId("remote-channels-panel")).toBeTruthy();
    for (const name of ["Email", "WhatsApp", "Telegram", "Microsoft Teams"]) expect(screen.getByText(name)).toBeTruthy();
    expect(screen.getByText("ready")).toBeTruthy();
    expect(screen.getByText("offline")).toBeTruthy();
    expect(screen.getAllByText("to set up")).toHaveLength(2);
    expect(screen.getAllByText("Set up in Settings > Channels")).toHaveLength(2);
    const approvals = screen.getAllByRole("button", { name: "Approvals" });
    expect(approvals.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "true", "false", "false"]);
    expect(api.fetchRemoteChannels).toHaveBeenCalledWith("tok");
  });

  it("saves a switch and the recipient, and shows the saved state", async () => {
    api.fetchRemoteChannels.mockResolvedValue(payload);
    const updated = { channels: payload.channels.map((r) => (r.channel === "telegram" ? { ...r, notify: true } : r)) };
    api.updateRemoteChannel.mockResolvedValue(updated);
    render(<RemoteChannelsPanel />);
    await screen.findByTestId("remote-channels-panel");
    fireEvent.click(screen.getAllByRole("button", { name: "Notifications" })[2]);
    await waitFor(() => expect(api.updateRemoteChannel).toHaveBeenCalledWith("tok", "telegram", { notify: true }));
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Notifications" })[2].getAttribute("aria-pressed")).toBe("true"),
    );
    const input = screen.getByLabelText("Telegram recipient");
    fireEvent.change(input, { target: { value: "123456" } });
    fireEvent.blur(input);
    await waitFor(() => expect(api.updateRemoteChannel).toHaveBeenCalledWith("tok", "telegram", { to: "123456" }));
  });

  it("reports the test result and the error states", async () => {
    api.fetchRemoteChannels.mockResolvedValue(payload);
    api.testRemoteChannels.mockResolvedValue({ channels: payload.channels, queued: 0 });
    api.updateRemoteChannel.mockRejectedValue(new Error("500"));
    render(<RemoteChannelsPanel />);
    await screen.findByTestId("remote-channels-panel");
    fireEvent.click(screen.getByRole("button", { name: "Send a test" }));
    expect(await screen.findByText("No ready channel with Notifications on")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Chat" })[0]);
    expect(await screen.findByText("Could not save")).toBeTruthy();
  });

  it("shows loading, then an error when the channels cannot be read", async () => {
    let reject: (error: Error) => void = () => undefined;
    api.fetchRemoteChannels.mockReturnValue(new Promise((_, no) => (reject = no)));
    render(<RemoteChannelsPanel />);
    expect(screen.getByRole("status").textContent).toContain("Loading channels...");
    reject(new Error("offline"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Channels unavailable");
  });
});
