// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, expect, it } from "vitest";

import { livePresenceMode } from "@/components/thread/LivePresence";

const base = {
  state: "listening" as const,
  userSpeaking: false,
  hearing: false,
  isTranscribing: false,
  isRewriting: false,
};

describe("livePresenceMode", () => {
  it("is hidden when the call is off or broken", () => {
    expect(livePresenceMode({ ...base, state: "off" }, false)).toBeNull();
    expect(livePresenceMode({ ...base, state: "error" }, false)).toBeNull();
  });

  it("follows who has the floor", () => {
    expect(livePresenceMode(base, false)).toBe("listening");
    expect(livePresenceMode({ ...base, userSpeaking: true }, false)).toBe("hearing");
    expect(livePresenceMode({ ...base, state: "speaking" }, false)).toBe("speaking");
    expect(livePresenceMode({ ...base, state: "working" }, false)).toBe("thinking");
    expect(livePresenceMode({ ...base, state: "muted" }, true)).toBe("muted");
    expect(livePresenceMode({ ...base, state: "starting" }, false)).toBe("connecting");
  });

  it("never looks idle between the user's last word and the agent's first", () => {
    expect(livePresenceMode({ ...base, isTranscribing: true }, false)).toBe("thinking");
    expect(livePresenceMode({ ...base, isRewriting: true }, false)).toBe("thinking");
    expect(livePresenceMode(base, true)).toBe("thinking");
  });
});
