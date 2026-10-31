// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { SettingsSectionKey } from "@/components/settings/SettingsView";

/**
 * Open one Settings section from anywhere (a panel deep inside Code, a
 * dropdown in the composer) without threading a callback down to it. App
 * listens and routes exactly like the sidebar's Settings entry.
 */
export const OPEN_SETTINGS_SECTION_EVENT = "navin:open-settings-section";

export function openSettingsSection(section: SettingsSectionKey): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<{ section: SettingsSectionKey }>(OPEN_SETTINGS_SECTION_EVENT, {
      detail: { section },
    }),
  );
}

/**
 * Sections that open in a window over the current screen (chat, Code) instead
 * of leaving it for the Settings page: the user connects an app or edits the
 * team and stays where the work is.
 */
export const SETTINGS_DIALOG_SECTIONS = ["channels", "team"] as const;
export type SettingsDialogSection = (typeof SETTINGS_DIALOG_SECTIONS)[number];

export const OPEN_SETTINGS_DIALOG_EVENT = "navin:open-settings-dialog";

export function isSettingsDialogSection(value: unknown): value is SettingsDialogSection {
  return typeof value === "string" && (SETTINGS_DIALOG_SECTIONS as readonly string[]).includes(value);
}

export function openSettingsDialog(section: SettingsDialogSection): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<{ section: SettingsDialogSection }>(OPEN_SETTINGS_DIALOG_EVENT, {
      detail: { section },
    }),
  );
}
