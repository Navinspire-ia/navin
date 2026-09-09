import { describe, expect, it } from "vitest";

import { computerChatUrl, computerPermissionGranted, computerSettingsUrl, computerSetupIssue, computerSetupStep } from "./computer-setup";
import type { ComputerDiagnostics, SettingsPayload } from "./types";

const config: NonNullable<SettingsPayload["computer"]> = {
  enabled: true, ask: "never", session_mode: "shared", live_view: true, audit_log: false,
  anthropic_native: false, protected_apps: [], route_preset: "vision", stopped: null,
  backend: "macos", backend_reason: "macOS", model: "test-vision", model_vision: true,
};
const check = (name: string, ok = true) => ({ name, ok, detail: "", fix: "" });
const diagnostics: ComputerDiagnostics = {
  backend: "macos", backend_reason: "macOS", enabled: true, ready: true, notes: [],
  checks: [check("screen_recording"), check("accessibility"), check("windows"), check("input"), check("screenshot")],
  screen: { width: 1440, height: 900, left: 0, top: 0 }, stopped: null,
};

describe("Computer setup", () => {
  it("does not confuse default activation with a ready vision model", () => {
    expect(computerSetupStep({ ...config, model_vision: false }, diagnostics)).toBe("model");
    expect(computerSetupStep(config, null)).toBe("permissions");
  });

  it("keeps mouse and keyboard access visible after Screen is allowed", () => {
    const partial = { ...diagnostics, checks: [check("screen_recording"), check("accessibility", false)] };
    expect(computerPermissionGranted(partial, "screen_recording")).toBe(true);
    expect(computerSetupStep(config, partial)).toBe("permissions");
    expect(computerSetupStep(config, diagnostics)).toBe("ready");
  });

  it("passive checks cannot prove applications or desktop readiness", () => {
    const passive = { ...diagnostics, passive: true };
    expect(computerPermissionGranted(passive, "automation")).toBeUndefined();
    expect(computerSetupStep(config, passive)).toBe("permissions");
  });

  it("a stale check for a different backend cannot mark the current desktop ready", () => {
    expect(computerSetupStep({ ...config, backend: "windows" }, diagnostics)).toBe("check");
    expect(computerSetupStep({ ...config, stopped: "paused" }, diagnostics)).toBe("stopped");
  });

  it("recognizes old and new permission messages only for Computer", () => {
    expect(computerSetupIssue("computer", "Error: computer left_click failed: Accessibility is not granted.")).toBe("permissions");
    expect(computerSetupIssue("computer", "Error: computer screenshot failed: Screen Recording is not granted.")).toBe("permissions");
    expect(computerSetupIssue("computer", "Computer needs a vision model")).toBe("vision");
    expect(computerSetupIssue("exec", "permission denied")).toBeNull();
    expect(computerSetupIssue("computer", "invalid click coordinates")).toBeNull();
  });

  it("returns to the same chat after setup", () => {
    const settings = computerSettingsUrl("permissions", "#/chat/websocket%3Atest");
    expect(settings).toBe("#/settings?section=computer&step=permissions&chat=websocket%3Atest");
    expect(computerChatUrl(settings)).toBe("#/chat/websocket%3Atest");
    expect(computerChatUrl(computerSettingsUrl("vision"))).toBe("#/new");
  });
});
