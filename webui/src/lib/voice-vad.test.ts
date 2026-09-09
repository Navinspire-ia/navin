import { describe, expect, it } from "vitest";

import {
  createVadState,
  displayLevel,
  rmsToDb,
  updateNoiseFloor,
  VAD_CALIBRATION_MS,
  VAD_SPEECH_MIN_DB,
  vadStep,
  vadThresholds,
  type VadAction,
  type VadState,
} from "./voice-vad";

const FRAME_MS = 32;

/** Feed `frames` frames at `levelDb`; return the actions in order. */
function run(state: VadState, clock: { now: number }, levelDb: number, frames: number, options = {}) {
  const actions: VadAction[] = [];
  for (let index = 0; index < frames; index += 1) {
    clock.now += FRAME_MS;
    const { action } = vadStep(state, levelDb, clock.now, options);
    if (action !== "none") actions.push(action);
  }
  return actions;
}

function calibrated(roomDb: number): { state: VadState; clock: { now: number } } {
  const state = createVadState();
  const clock = { now: 1_000 };
  run(state, clock, roomDb, Math.ceil(VAD_CALIBRATION_MS / FRAME_MS) + 1);
  return { state, clock };
}

describe("rmsToDb / displayLevel", () => {
  it("maps RMS to dBFS and clamps the extremes", () => {
    expect(rmsToDb(1)).toBe(0);
    expect(rmsToDb(0.1)).toBeCloseTo(-20, 5);
    expect(rmsToDb(0)).toBe(-90);
    expect(rmsToDb(Number.NaN)).toBe(-90);
  });

  it("puts speech in the visible range whatever the floor is", () => {
    expect(displayLevel(-70, -60)).toBe(0);
    expect(displayLevel(-60 + 12, -60)).toBeCloseTo(0.5, 5);
    expect(displayLevel(-20, -60)).toBe(1);
    // A noisy room: same speech-to-floor distance, same bar height.
    expect(displayLevel(-40 + 12, -40)).toBeCloseTo(0.5, 5);
  });
});

describe("vadThresholds", () => {
  it("sits a fixed distance above the floor, never below the absolute minimum", () => {
    const quiet = vadThresholds(-75);
    expect(quiet.speechDb).toBe(VAD_SPEECH_MIN_DB);
    expect(quiet.armDb).toBeLessThan(quiet.speechDb);
    expect(quiet.releaseDb).toBeLessThan(quiet.speechDb);
    const noisy = vadThresholds(-40);
    expect(noisy.speechDb).toBe(-28);
    expect(noisy.armDb).toBe(-34);
  });

  it("asks for louder speech while the assistant voice is playing", () => {
    const idle = vadThresholds(-60);
    const busy = vadThresholds(-60, { ttsPlaying: true });
    expect(busy.speechDb).toBeGreaterThan(idle.speechDb);
    // The recorder still arms a little early so the first syllable is kept.
    expect(busy.armDb).toBeGreaterThan(idle.armDb);
    expect(busy.armDb).toBeLessThan(busy.speechDb);
  });
});

describe("updateNoiseFloor", () => {
  it("falls fast and rises slowly", () => {
    expect(updateNoiseFloor(-50, -70, 32)).toBeLessThan(-54);
    const risen = updateNoiseFloor(-60, -40, 1_000);
    expect(risen).toBeCloseTo(-58.5, 5);
    expect(updateNoiseFloor(-60, -40, 1_000, { speaking: true })).toBeCloseTo(-59.6, 5);
  });

  it("never climbs above the level it hears or the ceiling", () => {
    expect(updateNoiseFloor(-60, -59.9, 5_000)).toBeCloseTo(-59.9, 5);
    expect(updateNoiseFloor(-31, -10, 60_000)).toBe(-30);
  });
});

describe("vadStep", () => {
  it("calibrates on the room before detecting anything", () => {
    const state = createVadState();
    const clock = { now: 0 };
    // Loud frames during calibration are not an utterance.
    expect(run(state, clock, -30, 3)).toEqual([]);
    expect(state.calibrationMs).toBeGreaterThan(0);
  });

  it("arms, confirms and closes a normal sentence", () => {
    const { state, clock } = calibrated(-65);
    const speech = run(state, clock, -30, 20); // ~640 ms of speech
    expect(speech).toEqual(["arm", "confirm"]);
    expect(state.phase).toBe("speaking");
    const silence = run(state, clock, -68, 40); // ~1.3 s of silence
    expect(silence).toEqual(["close"]);
    expect(state.phase).toBe("idle");
  });

  it("discards a click that never turns into speech", () => {
    const { state, clock } = calibrated(-65);
    expect(run(state, clock, -35, 1)).toEqual(["arm"]);
    expect(run(state, clock, -68, 30)).toEqual(["discard"]);
  });

  it("hears a quiet headset speaker", () => {
    const { state, clock } = calibrated(-75);
    // -45 dBFS is soft speech; the fixed old threshold missed it.
    expect(run(state, clock, -45, 10)).toEqual(["arm", "confirm"]);
  });

  it("ignores a noisy room at the level it calibrated on", () => {
    const { state, clock } = calibrated(-40);
    expect(run(state, clock, -39, 40)).toEqual([]);
    expect(run(state, clock, -22, 10)).toEqual(["arm", "confirm"]);
  });

  it("needs louder speech to barge in while the assistant talks", () => {
    const { state, clock } = calibrated(-60);
    // -47 dBFS is speech in a quiet room, but not over the assistant's voice.
    expect(run(state, clock, -47, 10, { ttsPlaying: true })).toEqual([]);
    // Loud enough for barge-in, but the recorder armed one frame earlier so
    // the first syllable is kept.
    expect(run(state, clock, -44, 1, { ttsPlaying: true })).toEqual(["arm"]);
    expect(run(state, clock, -30, 10, { ttsPlaying: true })).toEqual(["confirm"]);
  });

  it("cuts an endless utterance so it gets transcribed", () => {
    const { state, clock } = calibrated(-65);
    const actions = run(state, clock, -30, Math.ceil(31_000 / FRAME_MS));
    expect(actions[0]).toBe("arm");
    expect(actions[1]).toBe("confirm");
    expect(actions).toContain("close");
  });

  it("muting drops the take in progress and reports nothing", () => {
    const { state, clock } = calibrated(-65);
    run(state, clock, -30, 10);
    expect(run(state, clock, -30, 1, { muted: true })).toEqual(["discard"]);
    expect(run(state, clock, -30, 5, { muted: true })).toEqual([]);
  });

  it("reports hearing on the current frame", () => {
    const { state, clock } = calibrated(-65);
    clock.now += FRAME_MS;
    expect(vadStep(state, -30, clock.now).hearing).toBe(true);
    clock.now += FRAME_MS;
    expect(vadStep(state, -70, clock.now).hearing).toBe(false);
  });
});
