import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { NavinClient } from "@/lib/navin-client";
import type { InboundEvent } from "@/lib/types";

/** Energy threshold for simple VAD (RMS-ish level 0..1). */
export const VOICE_SESSION_SPEECH_THRESHOLD = 0.04;
/** Silence duration before flushing a speech segment. */
export const VOICE_SESSION_SILENCE_MS = 900;
const VOICE_SESSION_MAX_SEGMENT_MS = 30_000;
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
  | "planRequired"
  | "unsupported"
  | "notConfigured";

export interface VoiceSessionOptions {
  client: NavinClient | null;
  /** When false, start() fails with planRequired (UI gate). */
  realtimeAllowed?: boolean;
  autoSpeak?: boolean;
  disabled?: boolean;
  onTranscript?: (text: string, meta: { final: boolean; sessionId: string }) => void;
  onError?: (key: VoiceSessionErrorKey, detail?: string) => void;
  onTtsStart?: () => void;
  onTtsEnd?: () => void;
}

/**
 * Pure barge-in helper: cancel TTS playback when the user starts speaking.
 * Exported for unit tests.
 */
export function shouldCancelTtsOnSpeech(playing: boolean, userSpeaking: boolean): boolean {
  return Boolean(playing) && Boolean(userSpeaking);
}

export function useVoiceSession({
  client,
  realtimeAllowed = true,
  disabled,
  onTranscript,
  onError,
  onTtsStart,
  onTtsEnd,
}: VoiceSessionOptions) {
  // `autoSpeak` from options is reserved for composer wiring against settings.voice.auto_speak.
  const [state, setState] = useState<VoiceSessionState>("idle");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>(() => Array.from({ length: 32 }, () => 3));
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [ttsPlaying, setTtsPlaying] = useState(false);

  const streamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const audioRef = useRef<VadAudioState | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const speakingRef = useRef(false);
  const silenceStartedRef = useRef<number | null>(null);
  const segmentStartedRef = useRef<number | null>(null);
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);
  const ttsObjectUrlRef = useRef<string | null>(null);
  const ttsPlayingRef = useRef(false);

  const stopTtsPlayback = useCallback(() => {
    const audio = ttsAudioRef.current;
    ttsAudioRef.current = null;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    if (ttsObjectUrlRef.current) {
      URL.revokeObjectURL(ttsObjectUrlRef.current);
      ttsObjectUrlRef.current = null;
    }
    if (ttsPlayingRef.current) {
      ttsPlayingRef.current = false;
      setTtsPlaying(false);
      onTtsEnd?.();
    }
  }, [onTtsEnd]);

  const playTtsBase64 = useCallback(
    (audioBase64: string, mime = "audio/mpeg") => {
      stopTtsPlayback();
      try {
        const binary = atob(audioBase64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
        const blob = new Blob([bytes], { type: mime });
        const url = URL.createObjectURL(blob);
        ttsObjectUrlRef.current = url;
        const audio = new Audio(url);
        ttsAudioRef.current = audio;
        ttsPlayingRef.current = true;
        setTtsPlaying(true);
        onTtsStart?.();
        audio.onended = () => stopTtsPlayback();
        audio.onerror = () => stopTtsPlayback();
        void audio.play().catch(() => stopTtsPlayback());
      } catch {
        stopTtsPlayback();
      }
    },
    [onTtsEnd, onTtsStart, stopTtsPlayback],
  );

  const bargeInCancelTts = useCallback(() => {
    if (!shouldCancelTtsOnSpeech(ttsPlayingRef.current, true)) return;
    stopTtsPlayback();
    const sid = sessionIdRef.current;
    if (client && sid) {
      client.sendVoiceAudioChunk(sid, { bargeIn: true });
    }
  }, [client, stopTtsPlayback]);

  const flushSegment = useCallback(async () => {
    const recorder = mediaRecorderRef.current;
    const sid = sessionIdRef.current;
    if (!recorder || !sid || !client) return;
    const chunks = chunksRef.current.splice(0);
    const started = segmentStartedRef.current ?? Date.now();
    segmentStartedRef.current = null;
    silenceStartedRef.current = null;
    if (chunks.length === 0) return;
    const mimeType = recorder.mimeType || "audio/webm";
    const blob = new Blob(chunks, { type: mimeType });
    if (blob.size < 256) return;
    const durationMs = Math.max(0, Date.now() - started);
    try {
      const dataUrl = await blobToDataUrl(blob);
      client.sendVoiceAudioChunk(sid, { dataUrl, durationMs, final: true });
    } catch {
      onError?.("failed");
    }
  }, [client, onError]);

  const stopWaveform = useCallback(() => {
    const audio = audioRef.current;
    audioRef.current = null;
    if (!audio) return;
    if (audio.frame !== null) cancelAnimationFrame(audio.frame);
    audio.source.disconnect();
    audio.analyser.disconnect();
    void audio.context.close().catch(() => undefined);
  }, []);

  const startWaveform = useCallback(
    (stream: MediaStream) => {
      const AudioContextCtor = audioContextConstructor();
      if (!AudioContextCtor) return;
      stopWaveform();
      try {
        const context = new AudioContextCtor();
        const source = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.65;
        source.connect(analyser);
        const audio: VadAudioState = {
          analyser,
          context,
          data: new Uint8Array(analyser.fftSize),
          frame: null,
          source,
        };
        const tick = () => {
          const current = audioRef.current;
          if (!current) return;
          if (current.context.state !== "running") {
            void current.context.resume().catch(() => undefined);
            current.frame = requestAnimationFrame(tick);
            return;
          }
          current.analyser.getByteTimeDomainData(current.data);
          const level = voiceLevelFromSamples(current.data);
          setLevels((prev) => [...prev.slice(1), Math.max(3, Math.round(level * 34))]);
          const speaking = level >= VOICE_SESSION_SPEECH_THRESHOLD;
          if (speaking) {
            if (!speakingRef.current) {
              speakingRef.current = true;
              setIsSpeaking(true);
              // Drop pre-roll silence so the STT chunk starts near speech onset.
              chunksRef.current = [];
              segmentStartedRef.current = Date.now();
              bargeInCancelTts();
            }
            silenceStartedRef.current = null;
            if (
              segmentStartedRef.current
              && Date.now() - segmentStartedRef.current >= VOICE_SESSION_MAX_SEGMENT_MS
            ) {
              void flushSegment();
            }
          } else if (speakingRef.current) {
            if (silenceStartedRef.current === null) {
              silenceStartedRef.current = Date.now();
            } else if (Date.now() - silenceStartedRef.current >= VOICE_SESSION_SILENCE_MS) {
              speakingRef.current = false;
              setIsSpeaking(false);
              void flushSegment();
            }
          }
          current.frame = requestAnimationFrame(tick);
        };
        audioRef.current = audio;
        void context.resume().catch(() => undefined);
        audio.frame = requestAnimationFrame(tick);
      } catch {
        stopWaveform();
      }
    },
    [bargeInCancelTts, flushSegment, stopWaveform],
  );

  const cleanupMedia = useCallback(() => {
    stopWaveform();
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // ignore
      }
    }
    mediaRecorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    chunksRef.current = [];
    speakingRef.current = false;
    silenceStartedRef.current = null;
    segmentStartedRef.current = null;
    setIsSpeaking(false);
  }, [stopWaveform]);

  const stop = useCallback(async () => {
    const sid = sessionIdRef.current;
    cleanupMedia();
    stopTtsPlayback();
    if (client && sid) {
      client.endVoiceSession(sid);
    }
    sessionIdRef.current = null;
    setSessionId(null);
    setState("idle");
  }, [cleanupMedia, client, stopTtsPlayback]);

  const start = useCallback(async () => {
    if (!client || disabled || state === "starting" || state === "listening") return;
    if (!realtimeAllowed) {
      onError?.("planRequired");
      setState("error");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onError?.("unsupported");
      setState("error");
      return;
    }
    setState("starting");
    try {
      const sid = await client.startVoiceSession();
      sessionIdRef.current = sid;
      setSessionId(sid);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream, mediaRecorderOptions());
      chunksRef.current = [];
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        // Segment flush is driven by VAD; ignore bare stops during teardown.
      };
      recorder.start(250);
      startWaveform(stream);
      setState("listening");
    } catch (error) {
      cleanupMedia();
      sessionIdRef.current = null;
      setSessionId(null);
      const detail = error instanceof Error ? error.message : "";
      if (detail === "plan_required") onError?.("planRequired", detail);
      else if (detail.includes("Permission") || detail === "permission") onError?.("permission");
      else if (detail.includes("not_configured") || detail === "stt_not_configured") {
        onError?.("notConfigured", detail);
      } else onError?.("failed", detail);
      setState("error");
    }
  }, [
    cleanupMedia,
    client,
    disabled,
    onError,
    realtimeAllowed,
    startWaveform,
    state,
  ]);

  const speak = useCallback(
    (text: string) => {
      const sid = sessionIdRef.current;
      if (!client || !sid || !text.trim()) return;
      client.sendVoiceAudioChunk(sid, { speakText: text });
    },
    [client],
  );

  useEffect(() => {
    if (!client) return;
    return client.onVoiceSessionEvent((ev: InboundEvent) => {
      if (ev.event === "transcript_partial") {
        if (sessionIdRef.current && ev.session_id !== sessionIdRef.current) return;
        onTranscript?.(ev.text, { final: Boolean(ev.final), sessionId: ev.session_id });
        return;
      }
      if (ev.event === "tts_audio") {
        if (sessionIdRef.current && ev.session_id !== sessionIdRef.current) return;
        if (!ev.audio_base64) return;
        playTtsBase64(ev.audio_base64, ev.mime || "audio/mpeg");
        return;
      }
      if (ev.event === "tts_cancelled") {
        stopTtsPlayback();
        return;
      }
      if (ev.event === "voice_session_error") {
        const detail = ev.detail || "failed";
        if (detail === "plan_required") onError?.("planRequired", detail);
        else if (detail.includes("not_configured")) onError?.("notConfigured", detail);
        else onError?.("failed", detail);
      }
    });
  }, [client, onError, onTranscript, playTtsBase64, stopTtsPlayback]);

  useEffect(() => () => {
    cleanupMedia();
    stopTtsPlayback();
  }, [cleanupMedia, stopTtsPlayback]);

  return {
    bargeInCancelTts,
    isListening: state === "listening",
    isSpeaking,
    levels,
    sessionId,
    speak,
    start,
    state,
    stop,
    ttsPlaying,
  };
}

interface VadAudioState {
  analyser: AnalyserNode;
  context: AudioContext;
  data: Uint8Array<ArrayBuffer>;
  frame: number | null;
  source: MediaStreamAudioSourceNode;
}

function mediaRecorderOptions(): MediaRecorderOptions | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  const mimeType = VOICE_MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type));
  return mimeType ? { mimeType } : undefined;
}

function audioContextConstructor(): typeof AudioContext | undefined {
  if (typeof window === "undefined") return undefined;
  return window.AudioContext
    ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
}

function voiceLevelFromSamples(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const centered = (samples[index] - 128) / 128;
    sum += centered * centered;
  }
  const rms = Math.sqrt(sum / samples.length);
  return Math.min(1, Math.pow(rms * 4.2, 0.72));
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") resolve(reader.result);
      else reject(new Error("invalid_data_url"));
    };
    reader.onerror = () => reject(reader.error ?? new Error("read_failed"));
    reader.readAsDataURL(blob);
  });
}
