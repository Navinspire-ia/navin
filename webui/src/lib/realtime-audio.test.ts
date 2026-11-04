// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => {
  const browser = { clicked: false, started: [] as number[] };
  /** A speaker under an autoplay policy: only a click or key press opens it. */
  class HeldContext extends EventTarget {
    state = "suspended";
    currentTime = 0;
    destination = {};
    resume() {
      if (browser.clicked && this.state !== "running") {
        this.state = "running";
        this.dispatchEvent(new Event("statechange"));
      }
      return Promise.resolve();
    }
    close() {
      return Promise.resolve();
    }
    createBuffer(_channels: number, length: number, rate: number) {
      return { duration: length / rate, copyToChannel() {} };
    }
    createBufferSource() {
      return {
        buffer: null,
        onended: null,
        connect() {},
        start(at: number) {
          browser.started.push(at);
        },
        stop() {},
      };
    }
  }
  return { browser, HeldContext };
});

vi.mock("@/lib/audio", () => ({ audioContextConstructor: () => fake.HeldContext }));

import { PcmPlayer } from "./realtime-audio";

afterEach(() => {
  vi.restoreAllMocks();
});

it("does not show a voice the browser holds as speaking, and lets it out at the next click", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const player = new PcmPlayer();
  const playing = vi.fn();
  player.onPlayingChange = playing;
  player.prime();

  player.play(btoa(String.fromCharCode(...new Uint8Array(4800))));
  expect(fake.browser.started).toHaveLength(1);
  expect(player.playing).toBe(false);
  expect(playing).not.toHaveBeenCalledWith(true);
  expect(warn).toHaveBeenCalledOnce();

  fake.browser.clicked = true;
  window.dispatchEvent(new Event("pointerdown"));
  expect(player.playing).toBe(true);
  expect(playing).toHaveBeenLastCalledWith(true);
  player.close();
});
