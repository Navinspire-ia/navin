// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * One-shot text-to-speech playback.
 *
 * Navin only synthesizes speech inside a realtime voice session, so reading a
 * block of text aloud means opening a session, emitting a speak-only chunk, and
 * closing it again without ever touching the microphone.
 */

import type { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";

const SPEAK_TIMEOUT_MS = 45_000;
/** Server-side cap on a single speak_text envelope. */
export const MAX_SPEAK_TEXT_LENGTH = 8_000;

export type SpeechHandle = {
  /** Resolves once playback ends or the request fails. */
  done: Promise<void>;
  stop: () => void;
};

export async function synthesizeOnce(
  client: NavinClient,
  text: string,
): Promise<{ bytes: Uint8Array; mime: string }> {
  const trimmed = text.trim().slice(0, MAX_SPEAK_TEXT_LENGTH);
  if (!trimmed) return { bytes: new Uint8Array(), mime: "audio/mpeg" };
  const sessionId = await client.startVoiceSession();
  try {
    const payload = await new Promise<{ base64: string; mime: string }>(
      (resolve, reject) => {
        const timer = setTimeout(() => {
          unsubscribe();
          reject(new Error("tts_timeout"));
        }, SPEAK_TIMEOUT_MS);
        const unsubscribe = client.onVoiceSessionEvent((event: InboundEvent) => {
          if (event.event === "tts_audio" && event.session_id === sessionId) {
            clearTimeout(timer);
            unsubscribe();
            resolve({ base64: event.audio_base64, mime: event.mime || "audio/mpeg" });
            return;
          }
          if (event.event === "voice_session_error") {
            clearTimeout(timer);
            unsubscribe();
            reject(new Error(event.detail || "failed"));
          }
        });
        client.sendVoiceAudioChunk(sessionId, { speakText: trimmed });
      },
    );
    const binary = atob(payload.base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return { bytes, mime: payload.mime };
  } finally {
    client.endVoiceSession(sessionId);
  }
}

export function speakOnce(client: NavinClient, text: string): SpeechHandle {
  const trimmed = text.trim().slice(0, MAX_SPEAK_TEXT_LENGTH);
  let audio: HTMLAudioElement | null = null;
  let objectUrl: string | null = null;
  let stopped = false;

  const cleanup = () => {
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audio = null;
    }
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
  };

  const stop = () => {
    stopped = true;
    cleanup();
  };

  const done = (async () => {
    if (!trimmed) return;
    const sessionId = await client.startVoiceSession();
    try {
      const payload = await new Promise<{ base64: string; mime: string }>(
        (resolve, reject) => {
          const timer = setTimeout(() => {
            unsubscribe();
            reject(new Error("tts_timeout"));
          }, SPEAK_TIMEOUT_MS);
          const unsubscribe = client.onVoiceSessionEvent((event: InboundEvent) => {
            if (event.event === "tts_audio" && event.session_id === sessionId) {
              clearTimeout(timer);
              unsubscribe();
              resolve({ base64: event.audio_base64, mime: event.mime || "audio/mpeg" });
              return;
            }
            if (event.event === "voice_session_error") {
              clearTimeout(timer);
              unsubscribe();
              reject(new Error(event.detail || "failed"));
            }
          });
          client.sendVoiceAudioChunk(sessionId, { speakText: trimmed });
        },
      );
      if (stopped) return;
      await playBase64Audio(payload.base64, payload.mime, (element, url) => {
        audio = element;
        objectUrl = url;
      });
    } finally {
      client.endVoiceSession(sessionId);
      cleanup();
    }
  })();

  return { done, stop };
}

function playBase64Audio(
  base64: string,
  mime: string,
  register: (audio: HTMLAudioElement, objectUrl: string) => void,
): Promise<void> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const element = new Audio(url);
  register(element, url);
  return new Promise<void>((resolve, reject) => {
    element.onended = () => resolve();
    element.onerror = () => reject(new Error("playback_failed"));
    void element.play().catch(reject);
  });
}
