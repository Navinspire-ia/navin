import { useCallback, useEffect, useRef, useState } from "react";

import { fetchTeammateSpeech, type TeammateSpeechClip } from "@/lib/api";

/** Who speaks: a teammate's name and the voice it should have. */
export interface TeammateSpeaker {
  id: string;
  name: string;
  gender?: string;
  voice?: string;
}

const TEAM_VOICES_KEY = "navin.live.teamVoices";
/** One request at most; a longer reply is said in several clips. */
const CLIP_MAX_CHARS = 600;
/** A realtime model starts speaking a moment after it was asked to. */
const LEAD_GRACE_MS = 1500;

/** Live: teammates speak with their own voice (on unless switched off). */
export function teamVoicesEnabled(): boolean {
  try { return globalThis.localStorage?.getItem(TEAM_VOICES_KEY) !== "0"; } catch { return true; }
}

export function setTeamVoices(on: boolean): void {
  try {
    if (on) globalThis.localStorage?.removeItem(TEAM_VOICES_KEY);
    else globalThis.localStorage?.setItem(TEAM_VOICES_KEY, "0");
  } catch {
    // Private mode without storage: the switch lasts for this page only.
  }
}

/** Text cut on sentence ends into clips of at most *max* characters. */
export function speechClips(text: string, max = CLIP_MAX_CHARS): string[] {
  const clean = text.replace(/\s+/g, " ").trim();
  if (!clean) return [];
  const sentences = clean.match(/[^.!?]+[.!?]*\s*/g) ?? [clean];
  const clips: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (sentence.length > max) {
      if (current.trim()) clips.push(current.trim());
      current = "";
      for (let start = 0; start < sentence.length; start += max) clips.push(sentence.slice(start, start + max).trim());
      continue;
    }
    if ((current + sentence).length > max) {
      clips.push(current.trim());
      current = "";
    }
    current += sentence;
  }
  if (current.trim()) clips.push(current.trim());
  return clips.filter(Boolean);
}

type Fetched = { clip: TeammateSpeechClip } | { error: unknown };

function playClip(
  clip: TeammateSpeechClip,
  sink: string | null,
  audioRef: { current: HTMLAudioElement | null },
): Promise<void> {
  return new Promise((resolve) => {
    const audio = new Audio(`data:${clip.mime};base64,${clip.audio_base64}`);
    audioRef.current = audio;
    const done = () => {
      audio.onended = null;
      audio.onerror = null;
      audio.onpause = null;
      if (audioRef.current === audio) audioRef.current = null;
      resolve();
    };
    audio.onended = done;
    audio.onerror = done;
    audio.onpause = done;
    const routable = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };
    // The speaker picked for Live; the default one when the browser cannot route.
    const routed = sink && typeof routable.setSinkId === "function"
      ? routable.setSinkId(sink).catch(() => undefined)
      : Promise.resolve();
    void routed.then(() => audio.play()).catch(done);
  });
}

/**
 * Live: teammates' replies said with their own voice, one after the other.
 *
 * ``busy`` is true from the moment a reply is queued until its last clip
 * ends: the lead waits meanwhile, and a teammate waits while the lead
 * (``leadSpeaking``) talks. A clip that cannot be made goes to ``onFailed``
 * (said with the lead's voice) rather than being lost.
 */
export function useTeammateVoices({
  token,
  language,
  outputDeviceId,
  leadSpeaking,
  onFailed,
}: {
  token: string;
  language: string;
  outputDeviceId: string | null;
  leadSpeaking: () => boolean;
  onFailed: (speaker: TeammateSpeaker, text: string, error: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const queueRef = useRef<Array<{ speaker: TeammateSpeaker; text: string }>>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const epochRef = useRef(0);
  const runningRef = useRef(false);
  const leadSpokeAtRef = useRef(0);
  const optionsRef = useRef({ token, language, outputDeviceId, leadSpeaking, onFailed });
  optionsRef.current = { token, language, outputDeviceId, leadSpeaking, onFailed };

  const fetchClip = useCallback(async (speaker: TeammateSpeaker, text: string, first: boolean): Promise<Fetched> => {
    const { token: auth, language: lang } = optionsRef.current;
    try {
      return {
        clip: await fetchTeammateSpeech(auth, {
          text, key: speaker.id, gender: speaker.gender ?? "", voice: speaker.voice ?? "", lang,
          // Said first only when it can sound like nobody but Navin.
          name: first ? speaker.name : "",
        }),
      };
    } catch (error) {
      return { error };
    }
  }, []);

  const waitForLead = useCallback(async (epoch: number) => {
    while (epoch === epochRef.current) {
      const recent = Date.now() - leadSpokeAtRef.current < LEAD_GRACE_MS;
      if (!recent && !optionsRef.current.leadSpeaking()) return;
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    }
  }, []);

  const pump = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    const epoch = epochRef.current;
    try {
      while (queueRef.current.length > 0 && epoch === epochRef.current) {
        const item = queueRef.current.shift();
        if (!item) break;
        const clips = speechClips(item.text);
        // The next clip is made while the current one plays.
        let next: Promise<Fetched> | null = clips.length ? fetchClip(item.speaker, clips[0], true) : null;
        for (let index = 0; index < clips.length && next && epoch === epochRef.current; index += 1) {
          const fetched = await next;
          next = index + 1 < clips.length ? fetchClip(item.speaker, clips[index + 1], false) : null;
          if (epoch !== epochRef.current) break;
          if ("error" in fetched) {
            optionsRef.current.onFailed(item.speaker, clips.slice(index).join(" "), fetched.error);
            break;
          }
          await waitForLead(epoch);
          if (epoch !== epochRef.current) break;
          await playClip(fetched.clip, optionsRef.current.outputDeviceId, audioRef);
        }
      }
    } finally {
      runningRef.current = false;
      if (epoch === epochRef.current) {
        if (queueRef.current.length > 0) void pump();
        else setBusy(false);
      }
    }
  }, [fetchClip, waitForLead]);

  const say = useCallback((speaker: TeammateSpeaker, text: string) => {
    if (!text.trim()) return;
    queueRef.current.push({ speaker, text });
    setBusy(true);
    void pump();
  }, [pump]);

  const stop = useCallback(() => {
    epochRef.current += 1;
    queueRef.current = [];
    runningRef.current = false;
    audioRef.current?.pause();
    audioRef.current = null;
    setBusy(false);
  }, []);

  /** The lead was just asked to speak (a realtime model starts a moment later). */
  const leadSpoke = useCallback(() => {
    leadSpokeAtRef.current = Date.now();
  }, []);

  useEffect(() => stop, [stop]);

  return { busy, say, stop, leadSpoke };
}
