// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Microphone and speaker discovery for the live conversation.
 *
 * Works the same in a browser tab and in the desktop shells (Chromium /
 * WebView2 / WKWebView / WebKitGTK): `enumerateDevices` lists what the OS
 * exposes, labels appear once the microphone permission is granted, and the
 * choice is remembered per browser profile. Output selection exists only where
 * `HTMLMediaElement.setSinkId` does (Chromium family); elsewhere the OS default
 * output is used and the picker hides the speaker list.
 */

export interface VoiceDevice {
  deviceId: string;
  label: string;
}

export interface VoiceDeviceList {
  inputs: VoiceDevice[];
  outputs: VoiceDevice[];
  /** The page can route the assistant voice to a chosen speaker. */
  outputSelectable: boolean;
}

export type VoiceDeviceKind = "input" | "output";
export type MicrophonePermission = "unknown" | "prompt" | "granted" | "denied";

export const VOICE_INPUT_DEVICE_STORAGE_KEY = "navin.voice.inputDevice";
export const VOICE_OUTPUT_DEVICE_STORAGE_KEY = "navin.voice.outputDevice";

export const EMPTY_VOICE_DEVICES: VoiceDeviceList = {
  inputs: [],
  outputs: [],
  outputSelectable: false,
};

function storageKey(kind: VoiceDeviceKind): string {
  return kind === "input" ? VOICE_INPUT_DEVICE_STORAGE_KEY : VOICE_OUTPUT_DEVICE_STORAGE_KEY;
}

export function readStoredDevice(kind: VoiceDeviceKind): string | null {
  try {
    const value = window.localStorage.getItem(storageKey(kind));
    return value && value.trim() ? value : null;
  } catch {
    return null;
  }
}

export function writeStoredDevice(kind: VoiceDeviceKind, deviceId: string | null): void {
  try {
    if (deviceId) window.localStorage.setItem(storageKey(kind), deviceId);
    else window.localStorage.removeItem(storageKey(kind));
  } catch {
    // Private mode / storage disabled: the choice lasts for the session only.
  }
}

export function outputSelectionSupported(): boolean {
  if (typeof HTMLMediaElement === "undefined") return false;
  return typeof (HTMLMediaElement.prototype as { setSinkId?: unknown }).setSinkId === "function";
}

/** Human label for a device whose label the platform hid or left empty. */
export function deviceLabel(
  device: Pick<MediaDeviceInfo, "deviceId" | "label">,
  index: number,
  fallback: (index: number) => string,
): string {
  const label = (device.label || "").trim();
  if (label) return label;
  return fallback(index + 1);
}

/** The Chromium "default" / "communications" pseudo-devices come first. */
function sortDevices(devices: VoiceDevice[]): VoiceDevice[] {
  const rank = (device: VoiceDevice) =>
    device.deviceId === "default" ? 0 : device.deviceId === "communications" ? 1 : 2;
  return [...devices].sort((a, b) => rank(a) - rank(b));
}

export function toVoiceDeviceList(
  devices: readonly Pick<MediaDeviceInfo, "deviceId" | "label" | "kind">[],
  fallbacks: { input: (index: number) => string; output: (index: number) => string },
  outputSelectable = outputSelectionSupported(),
): VoiceDeviceList {
  const inputs: VoiceDevice[] = [];
  const outputs: VoiceDevice[] = [];
  for (const device of devices) {
    if (!device.deviceId) continue;
    if (device.kind === "audioinput") {
      inputs.push({ deviceId: device.deviceId, label: deviceLabel(device, inputs.length, fallbacks.input) });
    } else if (device.kind === "audiooutput") {
      outputs.push({ deviceId: device.deviceId, label: deviceLabel(device, outputs.length, fallbacks.output) });
    }
  }
  return {
    inputs: sortDevices(inputs),
    outputs: outputSelectable ? sortDevices(outputs) : [],
    outputSelectable: outputSelectable && outputs.length > 0,
  };
}

export async function listVoiceDevices(fallbacks: {
  input: (index: number) => string;
  output: (index: number) => string;
}): Promise<VoiceDeviceList> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) {
    return EMPTY_VOICE_DEVICES;
  }
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return toVoiceDeviceList(devices, fallbacks);
  } catch {
    return EMPTY_VOICE_DEVICES;
  }
}

/**
 * The device to ask for: the remembered one when it is still plugged in,
 * otherwise the platform default (null).
 */
export function resolveDeviceChoice(
  devices: readonly VoiceDevice[],
  preferredId: string | null,
): string | null {
  if (!preferredId) return null;
  return devices.some((device) => device.deviceId === preferredId) ? preferredId : null;
}

/** getUserMedia constraints for the chosen microphone. */
export function microphoneConstraints(deviceId: string | null): MediaStreamConstraints {
  const audio: MediaTrackConstraints = {
    // Echo cancellation keeps the assistant voice (played by this page) out
    // of the microphone as much as the platform allows.
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  if (deviceId) audio.deviceId = { exact: deviceId };
  return { audio };
}

/** Map a getUserMedia failure to the user-facing reason. */
export function microphoneErrorKind(
  error: unknown,
): "permission" | "noMicrophone" | "busy" | "unsupported" | "failed" {
  const name = error instanceof Error ? error.name : "";
  const message = error instanceof Error ? error.message : "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return "permission";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError") {
    return "noMicrophone";
  }
  if (name === "NotReadableError" || name === "AbortError" || name === "TrackStartError") return "busy";
  if (name === "TypeError" && /getUserMedia|mediaDevices/.test(message)) return "unsupported";
  if (message.toLowerCase().includes("permission")) return "permission";
  return "failed";
}

/** Read the microphone permission without prompting (Chromium family; others report unknown). */
export async function queryMicrophonePermission(): Promise<MicrophonePermission> {
  if (typeof navigator === "undefined" || !navigator.permissions?.query) return "unknown";
  try {
    const status = await navigator.permissions.query({
      name: "microphone" as PermissionName,
    });
    if (status.state === "granted" || status.state === "denied" || status.state === "prompt") {
      return status.state;
    }
    return "unknown";
  } catch {
    return "unknown";
  }
}
