// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Loudness of the agent's voice, 0..1 per 20 ms frame, to move the Live
 * avatar's mouth. Computed from the audio being played, so it follows any
 * voice, engine and language without touching the playback path.
 */
export const ENVELOPE_FRAME_S = 0.02;

export function speechEnvelope(samples: Float32Array, sampleRate: number): Float32Array {
  const size = Math.max(1, Math.round(sampleRate * ENVELOPE_FRAME_S));
  const frames = new Float32Array(Math.ceil(samples.length / size));
  for (let frame = 0; frame < frames.length; frame += 1) {
    const start = frame * size;
    const end = Math.min(samples.length, start + size);
    let sum = 0;
    for (let i = start; i < end; i += 1) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, end - start));
    // Speech RMS sits around 0.03-0.25: below the floor the mouth rests.
    frames[frame] = Math.min(1, Math.max(0, (rms - 0.012) * 5));
  }
  return frames;
}

/** Level at `seconds` into the audio the envelope was computed from. */
export function envelopeLevel(frames: Float32Array, seconds: number): number {
  if (!(seconds >= 0)) return 0;
  return frames[Math.floor(seconds / ENVELOPE_FRAME_S)] ?? 0;
}

/** Envelope of an encoded clip (mp3, ogg, wav...), or null if it cannot be decoded. */
export async function decodeSpeechEnvelope(bytes: Uint8Array): Promise<Float32Array | null> {
  const Offline =
    globalThis.OfflineAudioContext
    ?? (globalThis as unknown as { webkitOfflineAudioContext?: typeof OfflineAudioContext }).webkitOfflineAudioContext;
  if (!Offline) return null;
  try {
    const context = new Offline(1, 1, 44_100);
    const copy = bytes.slice().buffer;
    const decoded = await context.decodeAudioData(copy);
    return speechEnvelope(decoded.getChannelData(0), decoded.sampleRate);
  } catch {
    return null;
  }
}

/** Without an envelope (clip not decodable): a plausible talking motion. */
export function syntheticSpeechLevel(seconds: number): number {
  const wave = Math.sin(seconds * 17) * 0.5 + Math.sin(seconds * 7.3) * 0.5;
  return Math.max(0, 0.35 + wave * 0.3);
}
