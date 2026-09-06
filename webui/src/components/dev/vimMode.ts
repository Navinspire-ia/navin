/**
 * Persist Vim keybindings preference and lazily load @replit/codemirror-vim.
 */

import type { Extension } from "@codemirror/state";

export const VIM_STORAGE_KEY = "navin.code.vim";

export function readVimModeEnabled(): boolean {
  try {
    return window.localStorage.getItem(VIM_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeVimModeEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(VIM_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // Quota / private mode: preference is session-only.
  }
}

/** Dynamic import keeps the Vim bundle out of the default editor path. */
export async function loadVimExtension(): Promise<Extension> {
  const mod = await import("@replit/codemirror-vim");
  return mod.vim({ status: true });
}
