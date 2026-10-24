// @vitest-environment jsdom
// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LiveVoiceControl } from "@/hooks/useLiveVoice";

const loaded: string[] = [];
const disposed: string[] = [];

vi.mock("@met4citizen/talkinghead", () => ({
  TalkingHead: class {
    mtAvatar = { jawOpen: { realtime: null, needsUpdate: false } };
    async showAvatar({ url }: { url: string }) {
      loaded.push(url);
      if (url.endsWith("male.glb") && !url.endsWith("female.glb")) throw new Error("404");
    }
    dispose() {
      disposed.push("head");
    }
  },
}));

import { LiveAvatar, LiveAvatarDock } from "./LiveAvatar";

const calls: string[] = [];

function control(state: LiveVoiceControl["state"], muted = false): LiveVoiceControl {
  return {
    state,
    muted,
    speechLevel: () => 0.5,
    setMuted: (next: boolean) => calls.push(`muted:${next}`),
    skipSpeech: () => calls.push("skip"),
    stop: () => calls.push("stop"),
  } as unknown as LiveVoiceControl;
}

afterEach(() => {
  cleanup();
  calls.length = 0;
  loaded.length = 0;
  disposed.length = 0;
});

describe("LiveAvatar", () => {
  it("pops out during a Live call only", () => {
    const view = render(<LiveAvatar control={control("off")} voice="Lea" />);
    expect(screen.queryByTestId("live-avatar")).toBeNull();
    view.rerender(<LiveAvatar control={control("listening")} voice="Lea" />);
    expect(screen.getByTestId("live-avatar")).toBeTruthy();
  });

  it("collapses to a chip and comes back", () => {
    render(<LiveAvatar control={control("speaking")} voice="Lea" />);
    fireEvent.click(screen.getByRole("button", { name: /hide the avatar/i }));
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /show the avatar/i }));
    expect(screen.getByRole("img")).toBeTruthy();
  });

  it("asks for the male avatar for a male voice, then falls back to the female one", async () => {
    render(<LiveAvatar control={control("speaking")} voice="Matthew" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(loaded).toEqual(["/avatars/male.glb", "/avatars/female.glb"]);
  });

  it("loads the female avatar for a female voice and frees it when it goes away", async () => {
    const view = render(<LiveAvatar control={control("speaking")} voice="ambre" />);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(loaded).toEqual(["/avatars/female.glb"]);
    view.unmount();
    expect(disposed).toEqual(["head"]);
  });

  it("docks under the rail's Collapse button when the Code rail is on screen", () => {
    const view = render(
      <>
        <LiveAvatar control={control("listening")} voice="Lea" />
        <LiveAvatarDock iconsOnly={false} />
      </>,
    );
    const shown = screen.getAllByTestId("live-avatar");
    expect(shown).toHaveLength(1);
    expect(shown[0].dataset.placement).toBe("rail");
    // The rail goes away (chat restored): the chat shows the avatar again.
    view.rerender(<LiveAvatar control={control("listening")} voice="Lea" />);
    expect(screen.getByTestId("live-avatar").dataset.placement).toBe("floating");
  });

  it("puts the Live controls under the docked avatar", () => {
    const settings: string[] = [];
    render(
      <>
        <LiveAvatar control={control("speaking")} voice="Lea" onOpenSettings={() => settings.push("open")} />
        <LiveAvatarDock iconsOnly={false} />
      </>,
    );
    const controls = screen.getByTestId("live-dock-controls");
    expect(controls).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /mute microphone/i }));
    fireEvent.click(screen.getByRole("button", { name: /skip what is being said/i }));
    fireEvent.click(screen.getByRole("button", { name: /end voice conversation/i }));
    expect(calls).toEqual(["muted:true", "skip", "stop"]);
    fireEvent.click(screen.getByRole("button", { name: /settings\.live\.configure|configure/i }));
    expect(settings).toEqual(["open"]);
  });
});
