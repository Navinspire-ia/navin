// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useEffect, useRef, useState } from "react";

import type { NavinClient } from "@/lib/navin-client";
import { PcmPlayer, startPcmCapture, type PcmCapture } from "@/lib/realtime-audio";
import {
  RealtimeLiveSession,
  liveInstructions,
  realtimeBridgeUrl,
  type RealtimeToolHandler,
} from "@/lib/realtime-live";
import { microphoneConstraints, microphoneErrorKind } from "@/lib/voice-devices";
import { displayLevel, rmsToDb } from "@/lib/voice-vad";
import type { VoiceSessionErrorKey, VoiceSessionState } from "@/hooks/useVoiceSession";

const BAR_COUNT = 32;
const LEVEL_UI_MS = 50;
/** Assumed room noise floor for the meter (the model does its own detection). */
const METER_FLOOR_DB = -60;
/** Louder than this is the user talking (for the model watchdog, not the meter). */
const LOCAL_SPEECH_DB = -38;
/** Consecutive loud level reports before the user counts as speaking. */
const LOCAL_SPEECH_FRAMES = 3;

export interface RealtimeVoiceOptions {
  client: NavinClient;
  token: string;
  language: string;
  voice?: string;
  provider?: string;
  inputDeviceId: string | null;
  outputDeviceId: string | null;
  onTool: RealtimeToolHandler;
  onUserTranscript?: (text: string) => void;
  onAssistantTranscript?: (text: string) => void;
  /** The realtime engine cannot serve this call: the caller switches to the pipeline. */
  onFallback: (code: string, detail?: string) => void;
  onError: (key: VoiceSessionErrorKey, detail?: string) => void;
}

/**
 * The realtime engine behind the Live: same surface as useVoiceSession (state,
 * levels, speak, stop...) so the Live loop drives either engine.
 */
export function useRealtimeVoiceSession(options: RealtimeVoiceOptions) {
  const [state, setState] = useState<VoiceSessionState>("idle");
  const [muted, setMutedState] = useState(false);
  const [levels, setLevels] = useState<number[]>(() => Array(BAR_COUNT).fill(0));
  const [inputLevel, setInputLevel] = useState(0);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [awaitingPermission, setAwaitingPermission] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [info, setInfo] = useState<{ provider: string; model: string } | null>(null);

  const optionsRef = useRef(options);
  optionsRef.current = options;
  const sessionRef = useRef<RealtimeLiveSession | null>(null);
  const captureRef = useRef<PcmCapture | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const mutedRef = useRef(false);
  const epochRef = useRef(0);
  const levelBufferRef = useRef<number[]>(Array(BAR_COUNT).fill(0));
  const lastLevelPushRef = useRef(0);

  const teardown = useCallback(() => {
    sessionRef.current?.close();
    sessionRef.current = null;
    captureRef.current?.stop();
    captureRef.current = null;
    for (const track of streamRef.current?.getTracks() ?? []) track.stop();
    streamRef.current = null;
    playerRef.current?.close();
    playerRef.current = null;
    setTtsPlaying(false);
    setUserSpeaking(false);
    setThinking(false);
    setInputLevel(0);
    levelBufferRef.current = Array(BAR_COUNT).fill(0);
    setLevels(levelBufferRef.current);
  }, []);

  const stop = useCallback(async () => {
    epochRef.current += 1;
    teardown();
    setInfo(null);
    setState("idle");
  }, [teardown]);

  const loudFramesRef = useRef(0);
  const onLevel = useCallback((rms: number) => {
    // The model must react when the user speaks; its own voice in the room does not count.
    const loud = !mutedRef.current && !playerRef.current?.playing && rmsToDb(rms) > LOCAL_SPEECH_DB;
    loudFramesRef.current = loud ? loudFramesRef.current + 1 : 0;
    // Every loud report pushes the deadline: it counts from when the user stops talking.
    if (loudFramesRef.current >= LOCAL_SPEECH_FRAMES) sessionRef.current?.noteLocalSpeech();
    const level = mutedRef.current ? 0 : displayLevel(rmsToDb(rms), METER_FLOOR_DB);
    const buffer = levelBufferRef.current;
    buffer.push(level);
    if (buffer.length > BAR_COUNT) buffer.shift();
    const now = performance.now();
    if (now - lastLevelPushRef.current < LEVEL_UI_MS) return;
    lastLevelPushRef.current = now;
    setLevels([...buffer]);
    setInputLevel(level);
  }, []);

  const start = useCallback(async () => {
    const epoch = ++epochRef.current;
    teardown();
    setState("starting");
    const opts = optionsRef.current;
    const player = new PcmPlayer(opts.outputDeviceId);
    player.onPlayingChange = setTtsPlaying;
    playerRef.current = player;
    const session = new RealtimeLiveSession(
      {
        url: realtimeBridgeUrl(opts.client.socketUrl, opts.token),
        socketFactory: (url) => opts.client.openSocket(url),
        instructions: liveInstructions(opts.language),
        language: opts.language,
        voice: opts.voice,
        transcribeInput: opts.provider === "openai",
      },
      {
        onAudio: (chunk) => {
          setThinking(false);
          playerRef.current?.play(chunk);
        },
        onUserSpeechStart: () => {
          setUserSpeaking(true);
          // Barge-in: the user talks over the voice, it stops at once.
          playerRef.current?.flush();
        },
        onUserSpeechStop: () => {
          setUserSpeaking(false);
          setThinking(true);
        },
        onUserTranscript: (text) => optionsRef.current.onUserTranscript?.(text),
        onAssistantTranscript: (text) => optionsRef.current.onAssistantTranscript?.(text),
        onResponseStart: () => setThinking(true),
        onResponseDone: () => setThinking(false),
        onTool: (name, args) => optionsRef.current.onTool(name, args),
        onError: (code, message) => {
          if (epoch !== epochRef.current) return;
          epochRef.current += 1;
          teardown();
          setState("idle");
          if (code.startsWith("realtime_")) optionsRef.current.onFallback(code, message);
          else optionsRef.current.onError("failed", message || code);
        },
      },
    );
    sessionRef.current = session;
    try {
      const ready = await session.open();
      if (epoch !== epochRef.current) return;
      setInfo(ready);
    } catch (error) {
      if (epoch !== epochRef.current) return;
      teardown();
      setState("idle");
      const code = (error as { code?: string }).code ?? "realtime_unreachable";
      optionsRef.current.onFallback(code, error instanceof Error ? error.message : undefined);
      return;
    }

    setAwaitingPermission(true);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(microphoneConstraints(opts.inputDeviceId));
    } catch (error) {
      setAwaitingPermission(false);
      if (epoch !== epochRef.current) return;
      teardown();
      setState("error");
      const kind = microphoneErrorKind(error);
      optionsRef.current.onError(kind === "busy" ? "failed" : kind, error instanceof Error ? error.message : undefined);
      return;
    }
    setAwaitingPermission(false);
    if (epoch !== epochRef.current) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    streamRef.current = stream;
    try {
      const capture = await startPcmCapture(
        stream,
        (chunk) => {
          if (epoch === epochRef.current && !mutedRef.current) session.appendAudio(chunk);
        },
        onLevel,
      );
      if (epoch !== epochRef.current) {
        capture.stop();
        return;
      }
      captureRef.current = capture;
    } catch (error) {
      if (epoch !== epochRef.current) return;
      teardown();
      setState("error");
      optionsRef.current.onError("unsupported", error instanceof Error ? error.message : undefined);
      return;
    }
    setState("listening");
  }, [onLevel, teardown]);

  const setMuted = useCallback((value: boolean) => {
    mutedRef.current = value;
    setMutedState(value);
  }, []);

  const speak = useCallback((text: string) => {
    sessionRef.current?.announce(text);
  }, []);

  const greet = useCallback((text: string) => {
    sessionRef.current?.greet(text);
  }, []);

  const stopSpeaking = useCallback(() => {
    playerRef.current?.flush();
    sessionRef.current?.cancelResponse();
  }, []);

  /** Loudness of the model's voice right now, 0..1 (Live avatar). */
  const speechLevel = useCallback(() => playerRef.current?.level() ?? 0, []);

  useEffect(() => () => {
    epochRef.current += 1;
    teardown();
  }, [teardown]);

  return {
    state,
    muted,
    setMuted,
    levels,
    inputLevel,
    hearing: userSpeaking,
    isSpeaking: userSpeaking,
    isTranscribing: thinking,
    ttsPlaying,
    awaitingPermission,
    info,
    start,
    stop,
    speak,
    greet,
    stopSpeaking,
    speechLevel,
  };
}
