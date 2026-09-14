// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { ComputerDiagnostics, SettingsPayload } from "./types";

// Settings lives in overflow-y-auto. Fluent lists dismiss on the first
// scroll/resize unless these locks stay on (same as Career/Tenders).
export const COMPUTER_MODEL_CALLOUT = {
  calloutMaxHeight: 280,
  // 5 = Fluent DirectionalHint.bottomLeftEdge. Keep the number here so this
  // file stays free of @fluentui/react. Fixed hint stops the list bouncing
  // between above and below the field in the scrolling settings panel.
  directionalHint: 5,
  directionalHintFixed: true,
  preventDismissOnScroll: true,
  preventDismissOnResize: true,
};

export const COMPUTER_PERMISSIONS = ["screen_recording", "accessibility", "automation"] as const;
export type ComputerPermission = typeof COMPUTER_PERMISSIONS[number];
export type ComputerSetupIssue = "vision" | "permissions" | "desktop" | "disabled" | "stopped";
export type ComputerSetupStep = "model" | "permissions" | "check" | "ready" | "disabled" | "stopped";

export function computerPermissionGranted(diagnostics: ComputerDiagnostics | null, kind: ComputerPermission): boolean | undefined {
  const check = diagnostics?.checks.find((item) => item.name === kind);
  if (check) return check.ok;
  // The full macOS check reads windows through System Events. Passive checks
  // never ask that app to run, so its permission stays unknown until tested.
  if (kind === "automation" && diagnostics && !diagnostics.passive) {
    return diagnostics.checks.find((item) => item.name === "windows")?.ok;
  }
  return undefined;
}

export function computerSetupStep(config: SettingsPayload["computer"], diagnostics: ComputerDiagnostics | null): ComputerSetupStep {
  if (!config?.enabled) return "disabled";
  if (config.stopped) return "stopped";
  if (!config.model || config.model_vision !== true) return "model";
  if (config.backend === "macos" && COMPUTER_PERMISSIONS.some((kind) => computerPermissionGranted(diagnostics, kind) !== true)) {
    return "permissions";
  }
  return diagnostics?.ready && !diagnostics.passive && diagnostics.backend === config.backend ? "ready" : "check";
}

export function computerSetupIssue(tool: string | undefined, error: string | undefined): ComputerSetupIssue | null {
  if (tool?.split(".").pop()?.toLowerCase() !== "computer" || !error) return null;
  if (/vision model|cannot read images|no vision/i.test(error)) return "vision";
  if (/permission|screen recording|accessibility|assistive access|automation.*not granted|consent/i.test(error)) return "permissions";
  if (/disabled/i.test(error)) return "disabled";
  if (/stopped|kill switch/i.test(error)) return "stopped";
  if (/no desktop|no display|headless|no input driver|portal.*refus|desktop.*locked|secure UAC/i.test(error)) return "desktop";
  return null;
}

function chatFromHash(hash: string): string | null {
  const [path, query = ""] = hash.replace(/^#/, "").split("?", 2);
  if (path.startsWith("/chat/")) {
    try { return decodeURIComponent(path.slice(6)); } catch { return null; }
  }
  return new URLSearchParams(query).get("chat");
}

export function computerSettingsUrl(issue?: ComputerSetupIssue, currentHash = ""): string {
  const step = issue === "vision" ? "model" : issue === "permissions" ? "permissions" : "check";
  const query = new URLSearchParams({ section: "computer", step });
  const chat = chatFromHash(currentHash);
  if (chat) query.set("chat", chat);
  return `#/settings?${query}`;
}

export function computerChatUrl(currentHash: string): string {
  const chat = chatFromHash(currentHash);
  return chat ? `#/chat/${encodeURIComponent(chat)}` : "#/new";
}
