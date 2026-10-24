// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";

import { GitDeviceFlow } from "./GitDeviceFlow";

const api = vi.hoisted(() => ({
  startGitDeviceFlow: vi.fn(),
  pollGitDeviceFlow: vi.fn(),
  openInOsBrowser: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("@/lib/clipboard", () => ({ copyTextToClipboard: vi.fn(async () => true) }));

const FLOW = {
  flow_id: "flow-1",
  user_code: "ABCD-1234",
  verification_uri: "https://github.com/login/device",
  interval: 5,
  expires_in: 900,
};

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function startFlow() {
  fireEvent.click(screen.getByRole("button", { name: /Sign in with GitHub/ }));
  await advance(0);
}

beforeEach(async () => {
  await i18n.changeLanguage("en");
  vi.useFakeTimers();
  api.startGitDeviceFlow.mockResolvedValue(FLOW);
  api.openInOsBrowser.mockResolvedValue({ opened: true, url: FLOW.verification_uri });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("GitDeviceFlow", () => {
  it("shows the code, opens GitHub, then goes from pending to ok", async () => {
    const onConnected = vi.fn();
    api.pollGitDeviceFlow
      .mockResolvedValueOnce({ status: "pending" })
      .mockResolvedValueOnce({ status: "ok", login: "octocat" });
    render(<GitDeviceFlow token="t" onConnected={onConnected} />);
    await startFlow();

    expect(screen.getByTestId("git-device-code").textContent).toBe("ABCD-1234");
    expect(api.openInOsBrowser).toHaveBeenCalledWith("t", FLOW.verification_uri);
    expect(api.pollGitDeviceFlow).not.toHaveBeenCalled();

    await advance(5000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("git-device-waiting")).toBeTruthy();

    await advance(5000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("git-device-ok").textContent).toContain("Signed in as octocat");
    expect(onConnected).toHaveBeenCalledWith("octocat");

    await advance(30_000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(2);
  });

  it("waits 5 seconds longer after slow_down", async () => {
    api.pollGitDeviceFlow
      .mockResolvedValueOnce({ status: "slow_down" })
      .mockResolvedValue({ status: "pending" });
    render(<GitDeviceFlow token="t" />);
    await startFlow();

    await advance(5000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(1);
    // The next poll now waits 10 s, not 5.
    await advance(9000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(1);
    await advance(1000);
    expect(api.pollGitDeviceFlow).toHaveBeenCalledTimes(2);
  });

  it("says the code expired and starts a fresh flow on Try again", async () => {
    api.pollGitDeviceFlow.mockResolvedValueOnce({ status: "expired" });
    render(<GitDeviceFlow token="t" />);
    await startFlow();
    await advance(5000);

    expect(screen.getByTestId("git-device-expired").textContent).toContain("expired");
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    await advance(0);
    expect(api.startGitDeviceFlow).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("git-device-code")).toBeTruthy();
  });

  it("handles a denied sign-in with a retry", async () => {
    api.pollGitDeviceFlow.mockResolvedValueOnce({ status: "denied" });
    render(<GitDeviceFlow token="t" />);
    await startFlow();
    await advance(5000);
    expect(screen.getByTestId("git-device-denied").textContent).toContain("denied");
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
  });

  it("stops polling on Cancel", async () => {
    api.pollGitDeviceFlow.mockResolvedValue({ status: "pending" });
    render(<GitDeviceFlow token="t" />);
    await startFlow();
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    await advance(20_000);
    expect(api.pollGitDeviceFlow).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Sign in with GitHub/ })).toBeTruthy();
  });
});
