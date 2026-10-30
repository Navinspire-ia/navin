// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchChatDefaults: vi.fn(),
  updateChatDefaults: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

import {
  CHAT_DEFAULTS_EVENT,
  hydrateChatDefaults,
  loadLastTeamPick,
  rememberChatDefault,
  resetChatDefaultsForTests,
} from "./chat-defaults";
import { loadLastModelPick, saveLastModelPick } from "./thread-model-preset";

describe("chat defaults shared through the gateway", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetChatDefaultsForTests();
    api.fetchChatDefaults.mockReset();
    api.updateChatDefaults.mockReset().mockResolvedValue({ model_preset: null, team: null });
  });
  afterEach(() => vi.restoreAllMocks());

  it("a new window opens on the model and team picked in another one", async () => {
    api.fetchChatDefaults.mockResolvedValue({ model_preset: "glm-5-2", team: "sales" });
    const announced = vi.fn();
    window.addEventListener(CHAT_DEFAULTS_EVENT, announced);

    await hydrateChatDefaults("tok");

    window.removeEventListener(CHAT_DEFAULTS_EVENT, announced);
    expect(loadLastModelPick()).toBe("glm-5-2");
    expect(loadLastTeamPick()).toBe("sales");
    expect(announced).toHaveBeenCalledTimes(1);
    expect(api.updateChatDefaults).not.toHaveBeenCalled();
  });

  it("sends up a pick only this window knew, so the others get it", async () => {
    window.localStorage.setItem("navin.thread-model-last-pick", "opus");
    api.fetchChatDefaults.mockResolvedValue({ model_preset: null, team: null });

    await hydrateChatDefaults("tok");

    expect(api.updateChatDefaults).toHaveBeenCalledWith("tok", { model_preset: "opus" });
    expect(loadLastModelPick()).toBe("opus");
  });

  it("every new pick is saved here and on the gateway", async () => {
    api.fetchChatDefaults.mockResolvedValue({ model_preset: null, team: null });
    await hydrateChatDefaults("tok");

    saveLastModelPick("gpt-5-6");
    rememberChatDefault("team", "off");

    expect(loadLastModelPick()).toBe("gpt-5-6");
    expect(loadLastTeamPick()).toBe("off");
    expect(api.updateChatDefaults).toHaveBeenNthCalledWith(1, "tok", { model_preset: "gpt-5-6" });
    expect(api.updateChatDefaults).toHaveBeenNthCalledWith(2, "tok", { team: "off" });
  });

  it("an unreachable gateway keeps this window's picks and says why", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    window.localStorage.setItem("navin.chat-team-last-pick", "dev");
    api.fetchChatDefaults.mockRejectedValue(new Error("offline"));

    expect(await hydrateChatDefaults("tok")).toBeNull();

    expect(loadLastTeamPick()).toBe("dev");
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("could not read"), expect.any(Error));
  });

  it("a failed save is reported, the local pick is kept", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    api.fetchChatDefaults.mockResolvedValue({ model_preset: null, team: null });
    await hydrateChatDefaults("tok");
    api.updateChatDefaults.mockRejectedValue(new Error("500"));

    rememberChatDefault("team", "infra");
    await vi.waitFor(() => expect(warn).toHaveBeenCalled());

    expect(loadLastTeamPick()).toBe("infra");
  });
});
