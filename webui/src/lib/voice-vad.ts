// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Voice activity detection for the live conversation.
 *
 * Levels are RMS in dBFS. The detector learns the room's noise floor and puts
 * its thresholds a fixed distance above it, so a quiet headset and a noisy
 * laptop microphone both work without a settings screen. Every function here
 * is pure so the state machine can be unit tested with synthetic levels.
 */

export const VAD_MIN_DB = -90;
/** Floor assumed before any audio was measured. */
export const VAD_FLOOR_INITIAL_DB = -60;
/** The room is never assumed quieter than this (digital silence would over-trigger). */
export const VAD_FLOOR_MIN_DB = -75;
/** Louder than this is not "room noise" any more; speech still has to clear it. */
export const VAD_FLOOR_MAX_DB = -30;
/** First frames only measure the room; no detection yet. */
export const VAD_CALIBRATION_MS = 400;

export const VAD_SPEECH_ABOVE_FLOOR_DB = 12;
export const VAD_ARM_ABOVE_FLOOR_DB = 6;
export const VAD_RELEASE_ABOVE_FLOOR_DB = 5;
/** Absolute lower bound for speech (very quiet microphones). */
export const VAD_SPEECH_MIN_DB = -52;
/** While the assistant voice plays, speech must be this much louder to barge in. */
export const VAD_BARGE_IN_EXTRA_DB = 8;

/** Floor climbs slowly when the room gets noisier... */
export const VAD_FLOOR_RISE_DB_PER_S = 1.5;
/** ...much slower while an utterance runs (speech must not become "noise")... */
export const VAD_FLOOR_RISE_SPEAKING_DB_PER_S = 0.4;
/** ...and drops quickly when it gets quieter. */
export const VAD_FLOOR_FALL_ALPHA = 0.25;

/** Consecutive frames above the speech threshold before an utterance is confirmed. */
export const VAD_CONFIRM_FRAMES = 2;
/** An armed take that never confirms is dropped after this long. */
export const VAD_ARM_TIMEOUT_MS = 700;
/** Silence that closes an utterance. */
export const VAD_SILENCE_MS = 900;
/** Longest single utterance; longer speech is cut and sent in pieces. */
export const VAD_MAX_UTTERANCE_MS = 30_000;
/** Shorter confirmed takes are noise (a cough, a click). */
export const VAD_MIN_UTTERANCE_MS = 350;

export type VadPhase = "idle" | "armed" | "speaking";
export type VadAction = "none" | "arm" | "confirm" | "discard" | "close";

export interface VadThresholds {
  armDb: number;
  speechDb: number;
  releaseDb: number;
}

export interface VadState {
  floorDb: number;
  calibrationMs: number;
  calibrationSumDb: number;
  calibrationFrames: number;
  phase: VadPhase;
  /** Timestamp (ms) the current phase started. */
  phaseSince: number;
  silenceSince: number | null;
  aboveCount: number;
  lastAt: number | null;
}

export function createVadState(): VadState {
  return {
    floorDb: VAD_FLOOR_INITIAL_DB,
    calibrationMs: 0,
    calibrationSumDb: 0,
    calibrationFrames: 0,
    phase: "idle",
    phaseSince: 0,
    silenceSince: null,
    aboveCount: 0,
    lastAt: null,
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** RMS (0..1) to dBFS, clamped to the detector range. */
export function rmsToDb(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return VAD_MIN_DB;
  return clamp(20 * Math.log10(rms), VAD_MIN_DB, 0);
}

export function vadThresholds(floorDb: number, options: { ttsPlaying?: boolean } = {}): VadThresholds {
  const extra = options.ttsPlaying ? VAD_BARGE_IN_EXTRA_DB : 0;
  const speechDb = Math.max(VAD_SPEECH_MIN_DB, floorDb + VAD_SPEECH_ABOVE_FLOOR_DB) + extra;
  // Arming only starts the recorder (dropped if nothing confirms), so it stays
  // a few dB under the barge-in level: the first syllable is on tape too.
  const armDb = options.ttsPlaying
    ? speechDb - VAD_SPEECH_ABOVE_FLOOR_DB + VAD_ARM_ABOVE_FLOOR_DB
    : Math.max(VAD_SPEECH_MIN_DB - 6, floorDb + VAD_ARM_ABOVE_FLOOR_DB);
  const releaseDb = Math.max(VAD_SPEECH_MIN_DB - 7, floorDb + VAD_RELEASE_ABOVE_FLOOR_DB) + extra / 2;
  return { armDb, speechDb, releaseDb };
}

/** Next noise floor after one frame at *levelDb*. */
export function updateNoiseFloor(
  floorDb: number,
  levelDb: number,
  dtMs: number,
  options: { speaking?: boolean } = {},
): number {
  const level = Math.max(VAD_FLOOR_MIN_DB, levelDb);
  if (level < floorDb) {
    return Math.max(VAD_FLOOR_MIN_DB, floorDb + (level - floorDb) * VAD_FLOOR_FALL_ALPHA);
  }
  const rate = options.speaking ? VAD_FLOOR_RISE_SPEAKING_DB_PER_S : VAD_FLOOR_RISE_DB_PER_S;
  const next = floorDb + (rate * Math.max(0, dtMs)) / 1000;
  return Math.min(VAD_FLOOR_MAX_DB, next, level);
}

/**
 * Level for the meter, 0..1: 0 a little under the floor, 1 thirty dB above it.
 * Speech sits around 0.5-0.8 whatever the microphone gain is.
 */
export function displayLevel(levelDb: number, floorDb: number): number {
  return clamp((levelDb - (floorDb - 3)) / 30, 0, 1);
}

export interface VadStepOptions {
  ttsPlaying?: boolean;
  muted?: boolean;
}

export interface VadStepResult {
  action: VadAction;
  thresholds: VadThresholds;
  /** The current frame is loud enough to be speech. */
  hearing: boolean;
}

/**
 * Advance the detector by one level frame. Mutates *state* and returns what
 * the recorder should do. Phases: idle -> armed (recorder starts so the first
 * syllable is on tape) -> speaking (confirmed) -> close, or armed -> discard.
 */
export function vadStep(state: VadState, levelDb: number, now: number, options: VadStepOptions = {}): VadStepResult {
  const dt = state.lastAt === null ? 0 : Math.max(0, now - state.lastAt);
  state.lastAt = now;

  if (options.muted) {
    const thresholds = vadThresholds(state.floorDb, options);
    if (state.phase !== "idle") {
      state.phase = "idle";
      state.phaseSince = now;
      state.silenceSince = null;
      state.aboveCount = 0;
      return { action: "discard", thresholds, hearing: false };
    }
    return { action: "none", thresholds, hearing: false };
  }

  // Calibration: the first frames describe the room, not the user.
  if (state.calibrationMs < VAD_CALIBRATION_MS) {
    state.calibrationMs += dt || 32;
    state.calibrationSumDb += Math.max(VAD_FLOOR_MIN_DB, levelDb);
    state.calibrationFrames += 1;
    const mean = state.calibrationSumDb / state.calibrationFrames;
    state.floorDb = clamp(mean, VAD_FLOOR_MIN_DB, VAD_FLOOR_MAX_DB);
    return { action: "none", thresholds: vadThresholds(state.floorDb, options), hearing: false };
  }

  const thresholds = vadThresholds(state.floorDb, options);
  const speaking = state.phase === "speaking";
  state.floorDb = updateNoiseFloor(state.floorDb, levelDb, dt, { speaking: state.phase !== "idle" });
  const hearing = levelDb >= thresholds.speechDb;

  if (state.phase === "idle") {
    if (levelDb >= thresholds.armDb) {
      state.phase = "armed";
      state.phaseSince = now;
      state.aboveCount = hearing ? 1 : 0;
      state.silenceSince = null;
      return { action: "arm", thresholds, hearing };
    }
    return { action: "none", thresholds, hearing };
  }

  if (state.phase === "armed") {
    state.aboveCount = hearing ? state.aboveCount + 1 : 0;
    if (state.aboveCount >= VAD_CONFIRM_FRAMES) {
      state.phase = "speaking";
      state.phaseSince = now;
      state.silenceSince = null;
      return { action: "confirm", thresholds, hearing };
    }
    if (now - state.phaseSince >= VAD_ARM_TIMEOUT_MS) {
      state.phase = "idle";
      state.phaseSince = now;
      state.aboveCount = 0;
      return { action: "discard", thresholds, hearing };
    }
    return { action: "none", thresholds, hearing };
  }

  // speaking
  if (levelDb >= thresholds.releaseDb) {
    state.silenceSince = null;
  } else if (state.silenceSince === null) {
    state.silenceSince = now;
  }
  const tooLong = speaking && now - state.phaseSince >= VAD_MAX_UTTERANCE_MS;
  const silentLongEnough = state.silenceSince !== null && now - state.silenceSince >= VAD_SILENCE_MS;
  if (tooLong || silentLongEnough) {
    state.phase = "idle";
    state.phaseSince = now;
    state.silenceSince = null;
    state.aboveCount = 0;
    return { action: "close", thresholds, hearing };
  }
  return { action: "none", thresholds, hearing };
}
