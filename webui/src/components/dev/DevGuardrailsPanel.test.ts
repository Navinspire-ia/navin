// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { SettingsPayload } from "@/lib/types";

import { projectPermissionBlocked, securitySettingsHash } from "./DevGuardrailsPanel";
import {
  networkSafetyDirty,
  networkSafetyForm,
  nextNetworkSafetyForm,
  runtimeRestartPending,
} from "./DevGuardrailsSecurity";

describe("projectPermissionBlocked", () => {
  it("does not show Blocked when Autonomy is off but the machine switch is on", () => {
    // effective.open_pr_on_done is false whenever Autonomy is off. That used
    // to light the Blocked badge and blame the machine switch even when the
    // machine switch was ON (the Guardrails screenshot contradiction).
    expect(projectPermissionBlocked(true, true)).toBe(false);
    expect(projectPermissionBlocked(true, undefined)).toBe(false);
  });

  it("shows Blocked only when the project wants it and the machine forbids it", () => {
    expect(projectPermissionBlocked(true, false)).toBe(true);
    expect(projectPermissionBlocked(false, false)).toBe(false);
  });
});

describe("securitySettingsHash", () => {
  it("opens Settings > Security for the current chat", () => {
    expect(securitySettingsHash("websocket:abc")).toBe(
      "#/settings?chat=websocket%3Aabc&section=advanced",
    );
    expect(securitySettingsHash(null)).toBe("#/settings?section=advanced");
  });
});

function payload(overrides: Partial<SettingsPayload["advanced"]>, extra?: Partial<SettingsPayload>) {
  return {
    advanced: {
      webui_allow_local_service_access: true,
      webui_default_access_mode: "default",
      ...overrides,
    },
    requires_restart: false,
    ...extra,
  } as unknown as SettingsPayload;
}

describe("web safety form", () => {
  it("reads the gateway payload and falls back to the legacy flag", () => {
    expect(networkSafetyForm(payload({ webui_default_access_mode: "full" }))).toEqual({
      webuiAllowLocalServiceAccess: true,
      webuiDefaultAccessMode: "full",
    });
    expect(
      networkSafetyForm(
        payload({
          webui_allow_local_service_access: undefined as unknown as boolean,
          allow_local_preview_access: false,
        }),
      ).webuiAllowLocalServiceAccess,
    ).toBe(false);
    expect(networkSafetyForm(null)).toEqual({
      webuiAllowLocalServiceAccess: true,
      webuiDefaultAccessMode: "default",
    });
  });

  it("is dirty only when a switch differs from the saved state", () => {
    const saved = payload({});
    expect(networkSafetyDirty(networkSafetyForm(saved), saved)).toBe(false);
    expect(
      networkSafetyDirty(
        { webuiAllowLocalServiceAccess: false, webuiDefaultAccessMode: "default" },
        saved,
      ),
    ).toBe(true);
    expect(
      networkSafetyDirty(
        { webuiAllowLocalServiceAccess: true, webuiDefaultAccessMode: "full" },
        saved,
      ),
    ).toBe(true);
  });

  it("follows the first payload instead of calling the defaults unsaved", () => {
    // The panel mounts before the settings arrive: the form starts from the
    // defaults. Full Access saved on the gateway must land in the form, not
    // light "Unsaved changes" with a Save button nobody asked for.
    const saved = payload({ webui_default_access_mode: "full" });
    const initial = networkSafetyForm(null);
    const next = nextNetworkSafetyForm(initial, null, saved);
    expect(next).toEqual({ webuiAllowLocalServiceAccess: true, webuiDefaultAccessMode: "full" });
    expect(networkSafetyDirty(next, saved)).toBe(false);
  });

  it("keeps the user's edits when another card refreshes the payload", () => {
    const saved = payload({});
    const edited = { webuiAllowLocalServiceAccess: false, webuiDefaultAccessMode: "default" as const };
    const refreshed = payload({}, { requires_restart: true });
    expect(nextNetworkSafetyForm(edited, saved, refreshed)).toBe(edited);
    // Untouched and unchanged: the same object, no pointless re-render.
    const untouched = networkSafetyForm(saved);
    expect(nextNetworkSafetyForm(untouched, saved, refreshed)).toBe(untouched);
  });

  it("knows when a saved change still waits for a restart", () => {
    expect(runtimeRestartPending(null)).toBe(false);
    expect(runtimeRestartPending(payload({}))).toBe(false);
    expect(runtimeRestartPending(payload({}, { requires_restart: true }))).toBe(true);
    expect(
      runtimeRestartPending(payload({}, { restart_required_sections: ["runtime"] })),
    ).toBe(true);
    expect(
      runtimeRestartPending(payload({}, { restart_required_sections: ["browser"] })),
    ).toBe(false);
  });
});

describe("Guardrails panel layout", () => {
  const panel = readFileSync(resolve(__dirname, "DevGuardrailsPanel.tsx"), "utf8");
  const security = readFileSync(resolve(__dirname, "DevGuardrailsSecurity.tsx"), "utf8");
  const workbench = readFileSync(resolve(__dirname, "DevWorkbench.tsx"), "utf8");

  it("uses real switches, not bare checkboxes", () => {
    expect(panel).toContain("<ToggleButton");
    expect(panel).not.toContain('type="checkbox"');
    expect(security).toContain("<ToggleButton");
  });

  it("hosts the Settings > Security switches with the same endpoints", () => {
    expect(panel).toContain("<DevGuardrailsSecurity");
    expect(security).toContain("updateNetworkSafetySettings(token, form)");
    expect(security).toContain("<ExecPolicySettings />");
    expect(security).toContain("browserHeadless: !next");
    expect(security).toContain("browserLiveView: next");
    expect(security).toContain('data-testid="dev-guardrails-web-safety-save"');
    expect(security).toContain('data-testid="dev-guardrails-restart-link"');
  });

  it("writes only the switch that was touched and keeps the answer", () => {
    expect(panel).toContain("const next = await updateBoardAutonomy(token, boardKey, fields)");
    expect(panel).toContain("setAutonomy(next)");
    // Machine switches change the effective permissions: refresh quietly.
    expect(panel).toContain("void load({ silent: true })");
  });

  it("keeps the autopilot loop tied to a successful write", () => {
    const write = panel.indexOf("await updateBoardAutonomy(token, boardKey, fields)");
    const loop = panel.indexOf('key === "autopilot_loop" && onRunAction');
    expect(write).toBeGreaterThan(0);
    expect(loop).toBeGreaterThan(write);
  });

  it("keeps This project / This machine / Security in a real scrollport", () => {
    expect(panel).toContain("bindMenuListWheel");
    expect(panel).toContain('data-panel-scroll=""');
    expect(panel).toContain('data-testid="dev-guardrails-scroll"');
    expect(panel).toContain("overflow-y-auto");
    const mount = workbench.slice(workbench.indexOf(') : mode === "guardrails" ? ('));
    expect(mount.length).toBeGreaterThan(0);
    expect(mount.slice(0, 400)).toContain(
      "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden",
    );
  });

  it("reads the branch banner and the three sections", () => {
    for (const id of [
      "dev-guardrails-branch-banner",
      "dev-guardrails-project",
      "dev-guardrails-machine",
      "dev-guardrails-security-section",
      "dev-guardrails-enabled",
      "dev-guardrails-machineAutoBranch",
    ]) {
      expect(panel).toContain(`"${id}"`);
    }
  });

  it("no longer hosts memory: it points at the AGI panel instead", () => {
    // S2.5: what the agent may learn (episodic memory, skills evolution)
    // moved to the AGI panel. Guardrails keeps autonomy and machine
    // kill-switches only, plus a link so nobody hunts for the switches.
    expect(panel).not.toContain("updateCognition(");
    expect(panel).not.toContain("fetchCognition(");
    expect(panel).not.toContain("dev-guardrails-cognition-enabled");
    expect(panel).not.toContain("dev-guardrails-memory");
    expect(panel).toContain('data-testid="dev-guardrails-agi-link"');
    expect(panel).toContain('data-testid="dev-guardrails-open-agi"');
    expect(panel).toContain("onOpenAgi");
    // The three sentences: off by default, auto-draft, publish = you.
    expect(panel).toContain("dev.guardrails.agiMovedDetail");
    // The workbench wires the link to the AGI mode.
    expect(workbench).toContain('onOpenAgi={() => showMode("agi")}');
  });
});
