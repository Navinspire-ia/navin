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
