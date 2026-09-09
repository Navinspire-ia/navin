// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Native OS folder picker, when the UI runs inside the desktop shell.
 *
 * The desktop window navigates to the local gateway, so the workbench is remote
 * content: Tauri injects its IPC bridge but refuses every command that the
 * `gateway-dialog` capability does not list. `dialog:allow-open` is the only one
 * granted. In a plain browser there is no bridge at all, and callers fall back
 * to the in-app folder browser.
 */

import { tauriIpcAvailable } from "@/lib/desktop";

/** True when the Tauri IPC bridge is present, i.e. inside the desktop shell. */
export function nativeFolderPickerAvailable(): boolean {
  return tauriIpcAvailable();
}

/**
 * Open the OS "Select Folder" dialog.
 *
 * Resolves to the chosen path, or `null` when the user cancelled - which is a
 * real answer, not a failure, so callers must not open a second picker for it.
 * Rejects when the bridge is missing or the command was refused, which is the
 * signal to fall back to the in-app browser.
 */
export async function pickNativeFolder(defaultPath?: string | null): Promise<string | null> {
  if (!nativeFolderPickerAvailable()) {
    throw new Error("native dialog unavailable");
  }
  // Imported lazily so the browser build never pulls the Tauri bindings into
  // the main chunk for a picker it can never open.
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: true,
    multiple: false,
    defaultPath: defaultPath ?? undefined,
  });
  return typeof selected === "string" ? selected : null;
}
