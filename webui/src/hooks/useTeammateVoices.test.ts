// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchTeammateSpeech } from "@/lib/api";

import { setTeamVoices, speechClips, teamVoicesEnabled, useTeammateVoices } from "./useTeammateVoices";

vi.mock("@/lib/api", () => ({ fetchTeammateSpeech: vi.fn() }));

/** An <audio> that "plays" until the test ends it. */
class FakeAudio {
  static played: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onpause: (() => void) | null = null;
  constructor(public src: string) {}
  play() {
    FakeAudio.played.push(this);
    return Promise.resolve();
  }
  pause() {
    this.onpause?.();
  }
  end() {
    this.onended?.();
  }
}

const mark = { id: "mark", name: "Mark", gender: "male" };
const salma = { id: "salma", name: "Salma", gender: "female", voice: "Lea" };
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  FakeAudio.played = [];
  vi.stubGlobal("Audio", FakeAudio);
  vi.mocked(fetchTeammateSpeech).mockReset();
  vi.mocked(fetchTeammateSpeech).mockImplementation(async (_token, value) => ({
    audio_base64: btoa(value.text), mime: "audio/mpeg", voice: value.voice || `${value.gender}-voice`,
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setTeamVoices(true);
});

function setup(leadSpeaking = () => false) {
  const onFailed = vi.fn();
  const hook = renderHook(() => useTeammateVoices({
    token: "t", language: "fr", outputDeviceId: null, leadSpeaking, onFailed,
  }));
  return { ...hook, onFailed };
}

describe("teammates' voices in Live", () => {
  it("says each reply with its teammate's voice, one after the other", async () => {
    const app = setup();
    act(() => {
      app.result.current.say(mark, "Login fixed.");
      app.result.current.say(salma, "Tests are green.");
    });
    expect(app.result.current.busy).toBe(true);
    await flush();
    expect(FakeAudio.played.map((audio) => atob(audio.src.split(",")[1]))).toEqual(["Login fixed."]);
    act(() => FakeAudio.played[0].end());
    await flush();
    expect(FakeAudio.played).toHaveLength(2);
    expect(vi.mocked(fetchTeammateSpeech).mock.calls.map(([, value]) => [value.key, value.gender, value.voice, value.lang])).toEqual([
      ["mark", "male", "", "fr"],
      ["salma", "female", "Lea", "fr"],
    ]);
    expect(vi.mocked(fetchTeammateSpeech).mock.calls.map(([, value]) => value.name)).toEqual(["Mark", "Salma"]);
    act(() => FakeAudio.played[1].end());
    await flush();
    expect(app.result.current.busy).toBe(false);
  });

  it("waits while the lead is talking", async () => {
    vi.useFakeTimers();
    try {
      let talking = true;
      const app = setup(() => talking);
      act(() => app.result.current.say(mark, "Ready."));
      await flush();
      await act(async () => { await vi.advanceTimersByTimeAsync(600); });
      expect(FakeAudio.played).toHaveLength(0);
      talking = false;
      await act(async () => { await vi.advanceTimersByTimeAsync(200); });
      expect(FakeAudio.played).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("hands a reply it cannot voice to the lead instead of losing it", async () => {
    vi.mocked(fetchTeammateSpeech).mockRejectedValueOnce(new Error("speech provider down"));
    const app = setup();
    act(() => app.result.current.say(mark, "Login fixed. Salma tests it."));
    await flush();
    expect(app.onFailed).toHaveBeenCalledWith(mark, "Login fixed. Salma tests it.", expect.any(Error));
    expect(FakeAudio.played).toHaveLength(0);
    expect(app.result.current.busy).toBe(false);
  });

  it("stops at once, queue included", async () => {
    const app = setup();
    act(() => {
      app.result.current.say(mark, "One.");
      app.result.current.say(salma, "Two.");
    });
    await flush();
    act(() => app.result.current.stop());
    await flush();
    expect(app.result.current.busy).toBe(false);
    expect(FakeAudio.played).toHaveLength(1);
  });
});

describe("speech clips and the switch", () => {
  it("cuts on sentence ends, never above the limit", () => {
    const clips = speechClips(`${"Une phrase courte. ".repeat(60)}${"x".repeat(1300)}`, 600);
    expect(clips.every((clip) => clip.length <= 600)).toBe(true);
    expect(clips.join(" ").replace(/\s+/g, "")).toBe(`${"Une phrase courte. ".repeat(60)}${"x".repeat(1300)}`.replace(/\s+/g, ""));
    expect(speechClips("   ")).toEqual([]);
  });

  it("is on unless switched off", () => {
    expect(teamVoicesEnabled()).toBe(true);
    setTeamVoices(false);
    expect(teamVoicesEnabled()).toBe(false);
    setTeamVoices(true);
    expect(teamVoicesEnabled()).toBe(true);
  });
});
