// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { audioContextConstructor, blobToDataUrl, convertBlobToWav } from "@/lib/audio";
import { chunkSpeechText } from "@/lib/live-voice";
import type { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";
import {
  EMPTY_VOICE_DEVICES,
  listVoiceDevices,
  microphoneConstraints,
  microphoneErrorKind,
  queryMicrophonePermission,
  readStoredDevice,
  resolveDeviceChoice,
  writeStoredDevice,
  type MicrophonePermission,
  type VoiceDeviceList,
} from "@/lib/voice-devices";
import { createVoiceMeter, type VoiceMeter } from "@/lib/voice-meter";
import {
  createVadState,
  displayLevel,
  rmsToDb,
  VAD_MIN_UTTERANCE_MS,
  vadStep,
  type VadState,
} from "@/lib/voice-vad";

const VOICE_SESSION_STT_TIMEOUT_MS = 60_000;
const VOICE_SESSION_TTS_TIMEOUT_MS = 45_000;
const VOICE_SESSION_PROMPT_TIMEOUT_MS = 14_000;
/** How often the meter state is pushed to React (the detector runs on every frame). */
const VOICE_SESSION_LEVEL_UI_MS = 50;
const VOICE_SESSION_BAR_COUNT = 32;
const VOICE_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
] as const;

export type VoiceSessionState = "idle" | "starting" | "listening" | "error";
export type VoiceSessionErrorKey =
  | "failed"
  | "permission"
  | "noMicrophone"
  | "planRequired"
  | "unsupported"
  | "notConfigured";

export interface VoiceSessionOptions {
  client: NavinClient | null;
  disabled?: boolean;
  /** Convert utterances to WAV before STT (providers that reject WebM). */
  wantsWav?: boolean;
  /** Labels for devices whose name the platform hides. */
  deviceFallbackLabels?: { input: (index: number) => string; output: (index: number) => string };
  onTranscript?: (text: string, meta: { final: boolean; sessionId: string }) => void;
  onError?: (key: VoiceSessionErrorKey, detail?: string) => void;
  onTtsStart?: () => void;
  onTtsEnd?: () => void;
}

export interface VoicePromptResult {
  text: string;
  raw: string;
  rewritten: boolean;
}

/**
 * Pure barge-in helper: cancel TTS playback when the user starts speaking.
 * Exported for unit tests.
 */
export function shouldCancelTtsOnSpeech(playing: boolean, userSpeaking: boolean): boolean {
  return Boolean(playing) && Boolean(userSpeaking);
}

export interface TtsQueueItem {
  requestId: string;
  audio?: { base64: string; mime: string };
  failed?: boolean;
}

/** Attach synthesized audio to its request (or the oldest one still waiting). */
export function attachTtsAudio(
  queue: readonly TtsQueueItem[],
  requestId: string | undefined,
  audio: { base64: string; mime: string },
): TtsQueueItem[] {
  let index = requestId ? queue.findIndex((item) => item.requestId === requestId) : -1;
  if (index < 0 && !requestId) index = queue.findIndex((item) => !item.audio && !item.failed);
  if (index < 0) return [...queue];
  return queue.map((item, position) => (position === index ? { ...item, audio } : item));
}

/**
 * Pop the next chunk to play. Failed heads are dropped; a head whose audio
 * has not arrived yet blocks the queue so chunks always play in order.
 */
export function takePlayableTts(
  queue: readonly TtsQueueItem[],
): { item: TtsQueueItem | null; rest: TtsQueueItem[] } {
  const rest = [...queue];
  while (rest.length > 0 && rest[0].failed) rest.shift();
  if (rest.length === 0) return { item: null, rest };
  if (!rest[0].audio) return { item: null, rest };
  const item = rest.shift() ?? null;
  return { item, rest };
}

interface Utterance {
  recorder: MediaRecorder;
  chunks: BlobPart[];
  startedAt: number;
  endedAt: number | null;
  confirmed: boolean;
  discard: boolean;
}

interface MeterHandle {
  context: AudioContext;
  meter: VoiceMeter;
}

interface PendingPrompt {
  resolve: (result: VoicePromptResult) => void;
  raw: string;
  timer: ReturnType<typeof setTimeout>;
}

const DEFAULT_DEVICE_LABELS = {
  input: (index: number) => `Microphone ${index}`,
  output: (index: number) => `Speaker ${index}`,
};

export function useVoiceSession({
  client,
  disabled,
  wantsWav = false,
  deviceFallbackLabels = DEFAULT_DEVICE_LABELS,
  onTranscript,
  onError,
  onTtsStart,
  onTtsEnd,
}: VoiceSessionOptions) {
  const [state, setState] = useState<VoiceSessionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>(() =>
    Array.from({ length: VOICE_SESSION_BAR_COUNT }, () => 0),
  );
  const [inputLevel, setInputLevel] = useState(0);
  const [hearing, setHearing] = useState(false);
  const [noiseFloorDb, setNoiseFloorDb] = useState<number | null>(null);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [muted, setMutedState] = useState(false);
  const [pendingTranscripts, setPendingTranscripts] = useState(0);
  const [awaitingPermission, setAwaitingPermission] = useState(false);
  const [permission, setPermission] = useState<MicrophonePermission>("unknown");
  const [devices, setDevices] = useState<VoiceDeviceList>(EMPTY_VOICE_DEVICES);
  const [inputDeviceId, setInputDeviceIdState] = useState<string | null>(() => readStoredDevice("input"));
  const [outputDeviceId, setOutputDeviceIdState] = useState<string | null>(() => readStoredDevice("output"));
  const [activeInputLabel, setActiveInputLabel] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const meterRef = useRef<MeterHandle | null>(null);
  const vadRef = useRef<VadState>(createVadState());
  const sessionIdRef = useRef<string | null>(null);
  const sessionEpochRef = useRef(0);
  const startingRef = useRef(false);
  const inputSwitchRef = useRef(0);
  const utteranceRef = useRef<Utterance | null>(null);
  const mutedRef = useRef(false);
  const levelUiAtRef = useRef(0);
  const hearingRef = useRef(false);
  const sttPendingRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const promptPendingRef = useRef<Map<string, PendingPrompt>>(new Map());
  const inputDeviceIdRef = useRef<string | null>(inputDeviceId);
  const outputDeviceIdRef = useRef<string | null>(outputDeviceId);
  const deviceLabelsRef = useRef(deviceFallbackLabels);
  deviceLabelsRef.current = deviceFallbackLabels;

  const ttsQueueRef = useRef<TtsQueueItem[]>([]);
  const ttsTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);
  const ttsObjectUrlRef = useRef<string | null>(null);
  const ttsActiveRef = useRef(false);
  const audioPlayingRef = useRef(false);
  const reviveRef = useRef<Promise<boolean> | null>(null);

  // Callbacks live in refs so the WebSocket subscription and the VAD loop
  // never restart because a parent re-rendered with a new closure.
  const onTranscriptRef = useRef(onTranscript);
  const onErrorRef = useRef(onError);
  const onTtsStartRef = useRef(onTtsStart);
  const onTtsEndRef = useRef(onTtsEnd);
  onTranscriptRef.current = onTranscript;
  onErrorRef.current = onError;
  onTtsStartRef.current = onTtsStart;
  onTtsEndRef.current = onTtsEnd;
  const wantsWavRef = useRef(wantsWav);
  wantsWavRef.current = wantsWav;

  // -- Devices ----------------------------------------------------------------

  const refreshDevices = useCallback(async (): Promise<VoiceDeviceList> => {
    const list = await listVoiceDevices(deviceLabelsRef.current);
    setDevices(list);
    return list;
  }, []);

  useEffect(() => {
    const media = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
    if (!media?.addEventListener) return;
    const onChange = () => {
      void refreshDevices();
    };
    media.addEventListener("devicechange", onChange);
    return () => media.removeEventListener("devicechange", onChange);
  }, [refreshDevices]);

  useEffect(() => {
    void queryMicrophonePermission().then(setPermission);
  }, []);

  // -- TTS playback ---------------------------------------------------------

  const releaseAudioElement = useCallback(() => {
    const audio = ttsAudioRef.current;
    ttsAudioRef.current = null;
    audioPlayingRef.current = false;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (ttsObjectUrlRef.current) {
      URL.revokeObjectURL(ttsObjectUrlRef.current);
      ttsObjectUrlRef.current = null;
    }
  }, []);

  const clearTtsTimer = useCallback((requestId: string) => {
    const timer = ttsTimersRef.current.get(requestId);
    if (timer !== undefined) {
      clearTimeout(timer);
      ttsTimersRef.current.delete(requestId);
    }
  }, []);

  const finishTts = useCallback(() => {
    if (!ttsActiveRef.current) return;
    ttsActiveRef.current = false;
    setTtsPlaying(false);
    onTtsEndRef.current?.();
  }, []);

  const drainTts = useCallback(() => {
    if (ttsAudioRef.current) return;
    const { item, rest } = takePlayableTts(ttsQueueRef.current);
    ttsQueueRef.current = rest;
    if (!item) {
      if (rest.length === 0) finishTts();
      return;
    }
    clearTtsTimer(item.requestId);
    try {
      const binary = atob(item.audio!.base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([bytes], { type: item.audio!.mime }));
      ttsObjectUrlRef.current = url;
      const audio = new Audio(url);
      ttsAudioRef.current = audio;
      audioPlayingRef.current = true;
      const next = () => {
        releaseAudioElement();
        drainTts();
      };
      audio.onended = next;
      audio.onerror = next;
      const sink = outputDeviceIdRef.current;
      const routable = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };
      const routed =
        sink && typeof routable.setSinkId === "function"
          ? routable.setSinkId(sink).catch(() => undefined)
          : Promise.resolve();
      void routed.then(() => {
        if (ttsAudioRef.current !== audio) return;
        void audio.play().catch(next);
      });
    } catch {
      releaseAudioElement();
      drainTts();
    }
  }, [clearTtsTimer, finishTts, releaseAudioElement]);

  const stopSpeaking = useCallback(
    (options: { notifyServer?: boolean } = {}) => {
      for (const timer of ttsTimersRef.current.values()) clearTimeout(timer);
      ttsTimersRef.current.clear();
      ttsQueueRef.current = [];
      releaseAudioElement();
      const sid = sessionIdRef.current;
      if (options.notifyServer !== false && client && sid) {
        client.sendVoiceAudioChunk(sid, { bargeIn: true });
      }
      finishTts();
    },
    [client, finishTts, releaseAudioElement],
  );

  const speak = useCallback(
    (text: string) => {
      const sid = sessionIdRef.current;
      if (!client || !sid) return;
      const chunks = chunkSpeechText(text);
      if (chunks.length === 0) return;
      if (!ttsActiveRef.current) {
        ttsActiveRef.current = true;
        setTtsPlaying(true);
        onTtsStartRef.current?.();
      }
      for (const chunk of chunks) {
        const requestId = client.sendVoiceAudioChunk(sid, { speakText: chunk });
        ttsQueueRef.current = [...ttsQueueRef.current, { requestId }];
        const timer = setTimeout(() => {
          ttsTimersRef.current.delete(requestId);
          ttsQueueRef.current = ttsQueueRef.current.map((item) =>
            item.requestId === requestId && !item.audio ? { ...item, failed: true } : item,
          );
          drainTts();
        }, VOICE_SESSION_TTS_TIMEOUT_MS);
        ttsTimersRef.current.set(requestId, timer);
      }
    },
    [client, drainTts],
  );

  // -- Transcript -> prompt ---------------------------------------------------

  /**
   * Ask the gateway to turn a raw transcript into the message the user would
   * have typed. Always resolves: on timeout or error the raw text comes back.
   */
  const requestPrompt = useCallback(
    (raw: string, context?: string): Promise<VoicePromptResult> => {
      const sid = sessionIdRef.current;
      const fallback: VoicePromptResult = { text: raw, raw, rewritten: false };
      if (!client || !sid || !raw.trim()) return Promise.resolve(fallback);
      return new Promise<VoicePromptResult>((resolve) => {
        const requestId = client.requestVoicePrompt(sid, raw, context ? { context } : {});
        const timer = setTimeout(() => {
          promptPendingRef.current.delete(requestId);
          resolve(fallback);
        }, VOICE_SESSION_PROMPT_TIMEOUT_MS);
        promptPendingRef.current.set(requestId, { resolve, raw, timer });
      });
    },
    [client],
  );

  const settlePrompt = useCallback((requestId: string, result?: VoicePromptResult) => {
    const pending = promptPendingRef.current.get(requestId);
    if (!pending) return false;
    clearTimeout(pending.timer);
    promptPendingRef.current.delete(requestId);
    pending.resolve(result ?? { text: pending.raw, raw: pending.raw, rewritten: false });
    return true;
  }, []);

  const flushPrompts = useCallback(() => {
    for (const [requestId] of promptPendingRef.current) settlePrompt(requestId);
  }, [settlePrompt]);

  // -- Microphone / VAD -----------------------------------------------------

  const clearSttPending = useCallback((requestId: string) => {
    const timer = sttPendingRef.current.get(requestId);
    if (timer === undefined) return;
    clearTimeout(timer);
    sttPendingRef.current.delete(requestId);
    setPendingTranscripts(sttPendingRef.current.size);
  }, []);

  const submitUtterance = useCallback(
    (utterance: Utterance) => {
      const sid = sessionIdRef.current;
      if (!client || !sid || utterance.discard) return;
      const endedAt = utterance.endedAt ?? Date.now();
      const durationMs = Math.max(0, endedAt - utterance.startedAt);
      if (durationMs < VAD_MIN_UTTERANCE_MS) return;
      const mimeType = utterance.recorder.mimeType || "audio/webm";
      const blob = new Blob(utterance.chunks, { type: mimeType });
      if (blob.size < 256) return;
      const encode = wantsWavRef.current ? convertBlobToWav(blob) : blobToDataUrl(blob);
      void encode
        .then((dataUrl) => {
          if (sessionIdRef.current !== sid) return;
          const requestId = client.sendVoiceAudioChunk(sid, { dataUrl, durationMs, final: true });
          const timer = setTimeout(() => {
            sttPendingRef.current.delete(requestId);
            setPendingTranscripts(sttPendingRef.current.size);
          }, VOICE_SESSION_STT_TIMEOUT_MS);
          sttPendingRef.current.set(requestId, timer);
          setPendingTranscripts(sttPendingRef.current.size);
        })
        .catch(() => onErrorRef.current?.("failed"));
    },
    [client],
  );

  const startUtterance = useCallback((now: number) => {
    const stream = streamRef.current;
    if (!stream || utteranceRef.current) return;
    try {
      const recorder = new MediaRecorder(stream, mediaRecorderOptions());
      const utterance: Utterance = {
        recorder,
        chunks: [],
        startedAt: now,
        endedAt: null,
        confirmed: false,
        discard: false,
      };
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) utterance.chunks.push(event.data);
      };
      recorder.onstop = () => submitUtterance(utterance);
      recorder.start();
      utteranceRef.current = utterance;
    } catch {
      utteranceRef.current = null;
    }
  }, [submitUtterance]);

  const closeUtterance = useCallback((options: { discard: boolean }) => {
    const utterance = utteranceRef.current;
    utteranceRef.current = null;
    if (!utterance) return;
    utterance.discard = options.discard || !utterance.confirmed;
    utterance.endedAt = Date.now();
    try {
      if (utterance.recorder.state !== "inactive") utterance.recorder.stop();
    } catch {
      // Recorder already gone (device unplugged): nothing to flush.
    }
    setIsSpeaking(false);
  }, []);

  const stopMeter = useCallback(() => {
    const handle = meterRef.current;
    meterRef.current = null;
    if (!handle) return;
    handle.meter.stop();
    void handle.context.close().catch(() => undefined);
    hearingRef.current = false;
    setHearing(false);
  }, []);

  /** One level frame from the meter: drive the detector and the recorder. */
  const onLevelFrame = useCallback(
    (rms: number) => {
      if (!meterRef.current) return;
      const now = Date.now();
      const levelDb = rmsToDb(rms);
      const vad = vadRef.current;
      const { action, hearing: hearingNow } = vadStep(vad, levelDb, now, {
        ttsPlaying: audioPlayingRef.current,
        muted: mutedRef.current,
      });

      if (action === "arm") {
        startUtterance(now);
      } else if (action === "confirm") {
        const utterance = utteranceRef.current;
        if (utterance) {
          utterance.confirmed = true;
          setIsSpeaking(true);
        } else {
          // The recorder could not start on arm (device hiccup): start now.
          startUtterance(now);
          if (utteranceRef.current) {
            utteranceRef.current.confirmed = true;
            setIsSpeaking(true);
          }
        }
        if (shouldCancelTtsOnSpeech(ttsActiveRef.current, true)) stopSpeaking();
      } else if (action === "discard") {
        closeUtterance({ discard: true });
      } else if (action === "close") {
        closeUtterance({ discard: false });
      }

      if (hearingNow !== hearingRef.current) {
        hearingRef.current = hearingNow;
        setHearing(hearingNow);
      }
      if (now - levelUiAtRef.current >= VOICE_SESSION_LEVEL_UI_MS) {
        levelUiAtRef.current = now;
        const shown = mutedRef.current ? 0 : displayLevel(levelDb, vad.floorDb);
        setInputLevel(shown);
        setLevels((prev) => [...prev.slice(1), shown]);
        setNoiseFloorDb(Math.round(vad.floorDb));
      }
    },
    [closeUtterance, startUtterance, stopSpeaking],
  );

  const startMeter = useCallback(
    async (stream: MediaStream) => {
      const AudioContextCtor = audioContextConstructor();
      if (!AudioContextCtor) return;
      stopMeter();
      vadRef.current = createVadState();
      let context: AudioContext | null = null;
      try {
        context = new AudioContextCtor();
        await context.resume().catch(() => undefined);
        const meter = await createVoiceMeter(context, stream, onLevelFrame);
        if (streamRef.current !== stream) {
          meter.stop();
          void context.close().catch(() => undefined);
          return;
        }
        meterRef.current = { context, meter };
      } catch {
        if (context) void context.close().catch(() => undefined);
        onErrorRef.current?.("failed", "meter_unavailable");
      }
    },
    [onLevelFrame, stopMeter],
  );

  const releaseStream = useCallback(() => {
    closeUtterance({ discard: true });
    stopMeter();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setActiveInputLabel(null);
    setIsSpeaking(false);
    setInputLevel(0);
    setLevels(Array.from({ length: VOICE_SESSION_BAR_COUNT }, () => 0));
  }, [closeUtterance, stopMeter]);

  const cleanupMedia = useCallback(() => {
    releaseStream();
    for (const timer of sttPendingRef.current.values()) clearTimeout(timer);
    sttPendingRef.current.clear();
    setPendingTranscripts(0);
    flushPrompts();
    setAwaitingPermission(false);
  }, [flushPrompts, releaseStream]);

  /**
   * Open the microphone (remembered device when present, else the default),
   * start the meter, and refresh the device list now that labels are visible.
   */
  const openMicrophone = useCallback(
    async (preferredId: string | null): Promise<MediaStream> => {
      const known = await listVoiceDevices(deviceLabelsRef.current);
      let deviceId = resolveDeviceChoice(known.inputs, preferredId);
      // Before permission, enumerateDevices may hide ids: trust the stored
      // choice and fall back to the default if the browser rejects it.
      if (!deviceId && preferredId && known.inputs.every((device) => !device.deviceId)) {
        deviceId = preferredId;
      }
      const pending = setTimeout(() => setAwaitingPermission(true), 350);
      let stream: MediaStream;
      try {
        try {
          stream = await navigator.mediaDevices.getUserMedia(microphoneConstraints(deviceId));
        } catch (error) {
          if (deviceId && microphoneErrorKind(error) === "noMicrophone") {
            stream = await navigator.mediaDevices.getUserMedia(microphoneConstraints(null));
            deviceId = null;
          } else {
            throw error;
          }
        }
      } finally {
        clearTimeout(pending);
        setAwaitingPermission(false);
      }
      setPermission("granted");
      const track = stream.getAudioTracks()[0];
      const settings = track?.getSettings?.();
      const usedId = settings?.deviceId ?? deviceId;
      setActiveInputLabel(track?.label?.trim() || null);
      const list = await refreshDevices();
      // Sticky choice: an explicit pick stays; the default follows the OS.
      if (deviceId) {
        inputDeviceIdRef.current = deviceId;
        setInputDeviceIdState(deviceId);
      } else if (usedId && list.inputs.some((device) => device.deviceId === usedId) && !preferredId) {
        inputDeviceIdRef.current = null;
        setInputDeviceIdState(null);
      }
      track?.addEventListener?.("ended", () => {
        // Device unplugged or revoked: try the default microphone once.
        if (streamRef.current !== stream) return;
        void (async () => {
          releaseStream();
          try {
            const next = await openMicrophone(null);
            if (!sessionIdRef.current) {
              next.getTracks().forEach((t) => t.stop());
              return;
            }
            streamRef.current = next;
            await startMeter(next);
          } catch (error) {
            onErrorRef.current?.(microphoneErrorKind(error) === "permission" ? "permission" : "noMicrophone");
            setState("error");
          }
        })();
      });
      return stream;
    },
    [refreshDevices, releaseStream, startMeter],
  );

  const stop = useCallback(async () => {
    sessionEpochRef.current += 1;
    startingRef.current = false;
    const sid = sessionIdRef.current;
    sessionIdRef.current = null;
    cleanupMedia();
    stopSpeaking({ notifyServer: false });
    if (client && sid) client.endVoiceSession(sid);
    setSessionId(null);
    setState("idle");
  }, [cleanupMedia, client, stopSpeaking]);

  const start = useCallback(async () => {
    if (!client || disabled || startingRef.current || sessionIdRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onErrorRef.current?.("unsupported");
      setState("error");
      return;
    }
    const epoch = ++sessionEpochRef.current;
    startingRef.current = true;
    setState("starting");
    let sid: string | null = null;
    try {
      sid = await client.startVoiceSession();
      if (epoch !== sessionEpochRef.current) {
        client.endVoiceSession(sid);
        return;
      }
      sessionIdRef.current = sid;
      setSessionId(sid);
      const stream = await openMicrophone(inputDeviceIdRef.current);
      if (sessionIdRef.current !== sid) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      await startMeter(stream);
      if (sessionIdRef.current !== sid) return;
      setState("listening");
    } catch (error) {
      if (epoch !== sessionEpochRef.current) return;
      cleanupMedia();
      if (sid) client.endVoiceSession(sid);
      sessionIdRef.current = null;
      setSessionId(null);
      const detail = error instanceof Error ? error.message : "";
      if (detail === "plan_required") onErrorRef.current?.("planRequired", detail);
      else if (detail.includes("not_configured") || detail === "stt_disabled" || detail === "voice_disabled") {
        onErrorRef.current?.("notConfigured", detail);
      } else {
        const kind = microphoneErrorKind(error);
        if (kind === "permission") {
          setPermission("denied");
          onErrorRef.current?.("permission");
        } else if (kind === "noMicrophone") onErrorRef.current?.("noMicrophone", detail);
        else if (kind === "busy") onErrorRef.current?.("noMicrophone", "busy");
        else if (kind === "unsupported") onErrorRef.current?.("unsupported");
        else onErrorRef.current?.("failed", detail);
      }
      setState("error");
    } finally {
      if (epoch === sessionEpochRef.current) startingRef.current = false;
    }
  }, [cleanupMedia, client, disabled, openMicrophone, startMeter]);

  /** Switch microphone; live sessions re-open the stream without hanging up. */
  const selectInputDevice = useCallback(
    async (deviceId: string | null) => {
      const switching = ++inputSwitchRef.current;
      inputDeviceIdRef.current = deviceId;
      setInputDeviceIdState(deviceId);
      writeStoredDevice("input", deviceId);
      if (!sessionIdRef.current || !streamRef.current) return;
      const sid = sessionIdRef.current;
      releaseStream();
      try {
        const stream = await openMicrophone(deviceId);
        if (sessionIdRef.current !== sid || switching !== inputSwitchRef.current) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        await startMeter(stream);
      } catch (error) {
        if (sessionIdRef.current !== sid || switching !== inputSwitchRef.current) return;
        const kind = microphoneErrorKind(error);
        onErrorRef.current?.(kind === "permission" ? "permission" : "noMicrophone", kind);
        setState("error");
      }
    },
    [openMicrophone, releaseStream, startMeter],
  );

  const selectOutputDevice = useCallback((deviceId: string | null) => {
    outputDeviceIdRef.current = deviceId;
    setOutputDeviceIdState(deviceId);
    writeStoredDevice("output", deviceId);
    const audio = ttsAudioRef.current as
      | (HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> })
      | null;
    if (audio && typeof audio.setSinkId === "function") {
      void audio.setSinkId(deviceId ?? "").catch(() => undefined);
    }
  }, []);

  const setMuted = useCallback((value: boolean) => {
    mutedRef.current = value;
    setMutedState(value);
    if (value && utteranceRef.current) closeUtterance({ discard: true });
    if (value) {
      setInputLevel(0);
      hearingRef.current = false;
      setHearing(false);
    }
  }, [closeUtterance]);

  // The server forgot the session (gateway restart, WebSocket reconnect):
  // register the same id again and keep the microphone open instead of
  // ending the call. One attempt in flight at a time.
  const reviveSession = useCallback((): Promise<boolean> => {
    const sid = sessionIdRef.current;
    if (!client || !sid) return Promise.resolve(false);
    if (reviveRef.current) return reviveRef.current;
    const attempt = client
      .startVoiceSession({ sessionId: sid, timeoutMs: 10_000 })
      .then((returned) => {
        if (sessionIdRef.current !== sid) {
          client.endVoiceSession(returned);
          return false;
        }
        if (returned !== sid) {
          sessionIdRef.current = returned;
          setSessionId(returned);
        }
        return true;
      })
      .catch(() => false)
      .finally(() => {
        reviveRef.current = null;
      });
    reviveRef.current = attempt;
    return attempt;
  }, [client]);

  useEffect(() => {
    if (!client) return;
    return client.onVoiceSessionEvent((ev: InboundEvent) => {
      const sid = sessionIdRef.current;
      if (ev.event === "transcript_partial") {
        if (!sid || ev.session_id !== sid || ev.final === false) return;
        if (ev.request_id && !sttPendingRef.current.has(ev.request_id)) return;
        if (ev.request_id) clearSttPending(ev.request_id);
        const text = (ev.text || "").trim();
        if (text) onTranscriptRef.current?.(text, { final: true, sessionId: ev.session_id });
        return;
      }
      if (ev.event === "voice_prompt_ready") {
        if (!sid || ev.session_id !== sid) return;
        if (!ev.request_id) return;
        const text = (ev.text || "").trim();
        const raw = (ev.raw_text || "").trim();
        settlePrompt(
          ev.request_id,
          text ? { text, raw: raw || text, rewritten: ev.rewritten === true } : undefined,
        );
        return;
      }
      if (ev.event === "tts_audio") {
        if (!sid || ev.session_id !== sid) return;
        if (!ev.audio_base64) return;
        if (!ttsActiveRef.current) return;
        ttsQueueRef.current = attachTtsAudio(ttsQueueRef.current, ev.request_id, {
          base64: ev.audio_base64,
          mime: ev.mime || "audio/mpeg",
        });
        drainTts();
        return;
      }
      if (ev.event === "tts_cancelled") {
        return;
      }
      if (ev.event === "voice_session_error") {
        if (!sid || (ev.session_id && ev.session_id !== sid)) return;
        const detail = ev.detail || "failed";
        const lostSession = detail === "invalid_session" && Boolean(sid);
        if (lostSession) {
          void reviveSession().then((revived) => {
            if (!revived) onErrorRef.current?.("failed", "session_lost");
          });
        }
        if (ev.request_id && settlePrompt(ev.request_id)) {
          // The rewrite failed: the raw transcript was just resolved instead.
          return;
        }
        if (ev.request_id && sttPendingRef.current.has(ev.request_id)) {
          clearSttPending(ev.request_id);
          // Noise the transcriber heard no words in is not an error, just silence.
          if (detail === "empty") return;
          // One failed utterance is not the end of the call; surface it softly.
          onErrorRef.current?.("failed", detail);
          return;
        }
        if (ev.request_id && ttsQueueRef.current.some((item) => item.requestId === ev.request_id)) {
          clearTtsTimer(ev.request_id);
          ttsQueueRef.current = ttsQueueRef.current.map((item) =>
            item.requestId === ev.request_id ? { ...item, failed: true } : item,
          );
          drainTts();
          if (detail.includes("not_configured")) onErrorRef.current?.("notConfigured", detail);
          return;
        }
        // Revival already decides between "carry on" and "session_lost".
        if (lostSession) return;
        if (detail === "plan_required") onErrorRef.current?.("planRequired", detail);
        else if (detail.includes("not_configured")) onErrorRef.current?.("notConfigured", detail);
        else onErrorRef.current?.("failed", detail);
      }
    });
  }, [clearSttPending, clearTtsTimer, client, drainTts, reviveSession, settlePrompt]);

  useEffect(
    () => () => {
      sessionEpochRef.current += 1;
      startingRef.current = false;
      const sid = sessionIdRef.current;
      sessionIdRef.current = null;
      if (client && sid) client.endVoiceSession(sid);
      cleanupMedia();
      for (const timer of ttsTimersRef.current.values()) clearTimeout(timer);
      ttsTimersRef.current.clear();
      ttsQueueRef.current = [];
      releaseAudioElement();
    },
    [cleanupMedia, client, releaseAudioElement],
  );

  return {
    isListening: state === "listening",
    isSpeaking,
    isTranscribing: pendingTranscripts > 0,
    levels,
    inputLevel,
    hearing,
    noiseFloorDb,
    muted,
    sessionId,
    awaitingPermission,
    permission,
    devices,
    inputDeviceId,
    outputDeviceId,
    activeInputLabel,
    refreshDevices,
    selectInputDevice,
    selectOutputDevice,
    requestPrompt,
    setMuted,
    speak,
    start,
    state,
    stop,
    stopSpeaking,
    ttsPlaying,
  };
}

function mediaRecorderOptions(): MediaRecorderOptions | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  const mimeType = VOICE_MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type));
  return mimeType ? { mimeType } : undefined;
}
