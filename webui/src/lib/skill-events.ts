// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

export const SKILLS_CHANGED_EVENT = "navin:skills-changed";

export function notifySkillsChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(SKILLS_CHANGED_EVENT));
}
