// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

import type { LiveShare } from "@/lib/api";

let shares: LiveShare[] = [];
const share = (access: LiveShare["access"]): LiveShare => ({
  share_id: "s1",
  chat_id: "c1",
  link: "https://relay.example/s/s1#key",
  expires_at: 1_900_000_000,
  max_guests: 5,
  access,
  online: true,
  guests: [],
});
const createLiveShare = vi.fn(async (_t: string, _c: string, _ttl: string, access: LiveShare["access"]) => {
  shares = [share(access)];
  return shares[0];
});
const setLiveShareAccess = vi.fn(async (_t: string, _id: string, access: LiveShare["access"]) => {
  shares = [share(access)];
  return shares[0];
});

const setLiveShareSite = vi.fn(async (_t: string, _id: string, enabled: boolean, url: string) => {
  shares = [{ ...shares[0], site_url: enabled ? url : "" }];
  return shares[0];
});

vi.mock("@/lib/api", () => ({
  listLiveShares: vi.fn(async () => shares),
  createLiveShare: (...args: Parameters<typeof createLiveShare>) => createLiveShare(...args),
  setLiveShareAccess: (...args: Parameters<typeof setLiveShareAccess>) => setLiveShareAccess(...args),
  setLiveShareSite: (...args: Parameters<typeof setLiveShareSite>) => setLiveShareSite(...args),
  revokeLiveShare: vi.fn(),
  updateLiveShareGuest: vi.fn(),
}));
vi.mock("@/providers/ClientProvider", () => ({ useClient: () => ({ token: "tok" }) }));

import { LiveSharePanel } from "./LiveSharePanel";

describe("LiveSharePanel", () => {
  it("creates the link with the access chosen up front, then changes it for everyone", async () => {
    shares = [];
    render(<LiveSharePanel chatId="c1" open />);
    fireEvent.click(await screen.findByRole("radio", { name: /Read and write/ }));
    fireEvent.click(screen.getByRole("button", { name: /Create the link/ }));
    await waitFor(() => expect(createLiveShare).toHaveBeenCalledWith("tok", "c1", "24h", "write"));
    expect(await screen.findByText("https://relay.example/s/s1#key")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: /Full/ }));
    await waitFor(() => expect(setLiveShareAccess).toHaveBeenCalledWith("tok", "s1", "full"));
    await waitFor(() => expect(screen.getByRole("radio", { name: /Full/ }).getAttribute("aria-checked")).toBe("true"));
  });

  it("shares the running site from the Browser panel, and stops on request", async () => {
    shares = [];
    createLiveShare.mockClear();
    render(<LiveSharePanel chatId="c1" open siteUrl="http://localhost:5175" />);
    const toggle = await screen.findByRole("checkbox", { name: /Share the running site/ });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /Create the link/ }));
    await waitFor(() =>
      expect(createLiveShare).toHaveBeenCalledWith("tok", "c1", "24h", "read", "", { url: "http://localhost:5175" }),
    );

    shares = [{ ...share("read"), site_url: "http://localhost:5175" }];
    fireEvent.click(await screen.findByRole("checkbox", { name: /Share the running site/ }));
    await waitFor(() => expect(setLiveShareSite).toHaveBeenCalledWith("tok", "s1", false, ""));
  });
});
