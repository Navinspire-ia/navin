// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { voiceGender } from "@/lib/live-voices";

/** Body of the Live avatar: it matches the voice the agent speaks with. */
export type AvatarBody = "F" | "M";

/**
 * Avatar files (public/avatars, see its README). Any TalkingHead-compatible
 * GLB works: Mixamo rig, ARKit and Oculus viseme blend shapes. Without
 * male.glb the female avatar is shown for every voice.
 */
export const LIVE_AVATAR_URLS: Record<AvatarBody, string> = {
  F: "/avatars/female.glb",
  M: "/avatars/male.glb",
};

/**
 * "auto", empty and unknown voices are female: without a chosen voice Nova
 * Sonic speaks with Ambre or Tiffany, Gemini Live and Polly with a female voice.
 */
export function avatarBodyForVoice(voice: string | null | undefined): AvatarBody {
  return voiceGender(voice) === "M" ? "M" : "F";
}
