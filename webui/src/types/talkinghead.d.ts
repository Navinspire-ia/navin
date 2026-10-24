// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

// The part of TalkingHead (MIT, github.com/met4citizen/TalkingHead) the Live
// avatar uses; the package ships no type declarations.
declare module "@met4citizen/talkinghead" {
  export interface TalkingHeadOptions {
    ttsEndpoint?: string | null;
    lipsyncModules?: string[];
    lipsyncLang?: string;
    cameraView?: "full" | "mid" | "upper" | "head";
    cameraDistance?: number;
    cameraX?: number;
    cameraY?: number;
    cameraRotateEnable?: boolean;
    cameraPanEnable?: boolean;
    cameraZoomEnable?: boolean;
    avatarMood?: string;
    modelFPS?: number;
    modelPixelRatio?: number;
    lightAmbientIntensity?: number;
    lightDirectIntensity?: number;
    avatarIdleEyeContact?: number;
    avatarSpeakingEyeContact?: number;
  }

  export interface TalkingHeadAvatar {
    url: string;
    body: "F" | "M";
    avatarMood?: string;
    lipsyncLang?: string;
  }

  export class TalkingHead {
    constructor(node: HTMLElement, options?: TalkingHeadOptions);
    showAvatar(avatar: TalkingHeadAvatar, onprogress?: ((event: ProgressEvent) => void) | null): Promise<void>;
    setValue(morphTarget: string, value: number, ms?: number | null): void;
    setMood(mood: string): void;
    lookAtCamera(ms: number): void;
    start(): void;
    stop(): void;
    /** Frees the renderer, the WebGL context and the audio graph. */
    dispose(): void;
    /**
     * Morph targets by name. `realtime` overrides animations at once (the
     * channel TalkingHead's own streaming lip-sync uses); null releases it.
     */
    mtAvatar?: Record<string, { realtime: number | null; needsUpdate: boolean }>;
  }
}
