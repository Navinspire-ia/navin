// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Force the Tauri host chrome (title drag strip, sidebar glass, native-host
 * CSS) inside a plain browser tab. Same WebUI as the desktop shell, without a
 * rebuild: catch explorateur / overlay / drag regressions early.
 *
 * Enable:
 *   - hash ``?hostChrome=1`` (consumed once, then stored)
 *   - localStorage ``navin-webui.host-chrome-force=1``
 * Disable:
 *   - ``?hostChrome=0`` or clear the storage key
 */
import { consumeUrlHostChromeFlag } from "./bootstrap";
import { isDesktopShell } from "./desktop";

export const HOST_CHROME_FORCE_KEY = "navin-webui.host-chrome-force";

function truthyFlag(raw: string): boolean {
  const value = raw.trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "on";
}

function falsyFlag(raw: string): boolean {
  const value = raw.trim().toLowerCase();
  return value === "0" || value === "false" || value === "no" || value === "off";
}

export function readForcedHostChrome(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(HOST_CHROME_FORCE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeForcedHostChrome(enabled: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (enabled) window.localStorage.setItem(HOST_CHROME_FORCE_KEY, "1");
    else window.localStorage.removeItem(HOST_CHROME_FORCE_KEY);
  } catch {
    // Preview must keep working when storage is blocked.
  }
}

/**
 * True when the UI should paint as the desktop shell: real Tauri/Electron, or
 * a browser tab that opted into the host chrome preview.
 */
export function shouldShowHostChrome(runtimeIsNative: boolean): boolean {
  if (runtimeIsNative || isDesktopShell()) return true;
  return readForcedHostChrome();
}

/** Call before first paint so ``?hostChrome=1`` sticks across reloads. */
export function initHostChromePreview(): void {
  if (typeof window === "undefined") return;
  const fromUrl = consumeUrlHostChromeFlag();
  if (!fromUrl) return;
  if (truthyFlag(fromUrl)) writeForcedHostChrome(true);
  else if (falsyFlag(fromUrl)) writeForcedHostChrome(false);
}
