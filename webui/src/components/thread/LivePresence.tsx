// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import type { TFunction } from "i18next";

import type { LiveVoiceControl } from "@/hooks/useLiveVoice";

/** What the presence shows, from the voice session pieces. */
export type LivePresenceMode = "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "muted";

export function livePresenceMode(
  control: Pick<LiveVoiceControl, "state" | "userSpeaking" | "hearing" | "isTranscribing" | "isRewriting">,
  agentBusy: boolean,
): LivePresenceMode | null {
  switch (control.state) {
    case "off":
    case "error":
      return null;
    case "starting":
      return "connecting";
    case "muted":
      return "muted";
    case "speaking":
      return "speaking";
    case "working":
      return "thinking";
    default:
      if (control.userSpeaking || control.hearing) return "hearing";
      // Between the user's last word and the agent's first: the transcript is
      // being read or the turn is running. Show the agent as busy, not idle.
      if (control.isTranscribing || control.isRewriting || agentBusy) return "thinking";
      return "listening";
  }
}

/** One wording for the Live state, shown once, in the Live bar. */
export function livePresenceCaption(mode: LivePresenceMode, t: TFunction): string {
  return {
    connecting: t("thread.composer.liveVoice.presence.connecting", { defaultValue: "Joining the call..." }),
    listening: t("thread.composer.liveVoice.presence.listening", { defaultValue: "Listening" }),
    hearing: t("thread.composer.liveVoice.presence.hearing", { defaultValue: "I hear you" }),
    thinking: t("thread.composer.liveVoice.presence.thinking", { defaultValue: "Thinking and working" }),
    speaking: t("thread.composer.liveVoice.presence.speaking", { defaultValue: "Speaking" }),
    muted: t("thread.composer.liveVoice.presence.muted", { defaultValue: "Your microphone is off" }),
  }[mode];
}
