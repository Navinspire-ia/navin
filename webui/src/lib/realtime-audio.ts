// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Audio for the realtime Live engine: the microphone as 24 kHz mono PCM16
 * (what the OpenAI Realtime protocol and LiteLLM's bridges expect), and a
 * gapless player for the model's PCM16 replies that can be cut at once when
 * the user speaks over it.
 */

import { audioContextConstructor } from "@/lib/audio";
import { ENVELOPE_FRAME_S, envelopeLevel, speechEnvelope } from "@/lib/speech-envelope";

export const REALTIME_SAMPLE_RATE = 24_000;
/** ~100 ms per upload: small enough for a snappy reply, few enough frames. */
const CHUNK_MS = 100;

const WORKLET_NAME = "navin-pcm-capture";
const WORKLET_SOURCE = `
class NavinPcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor(${JSON.stringify(WORKLET_NAME)}, NavinPcmCapture);
`;

let workletModuleUrl: string | null = null;
function workletUrl(): string {
  workletModuleUrl ??= URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
  return workletModuleUrl;
}

/** Linear resampling of a mono block to 24 kHz. */
export function resampleTo24k(input: Float32Array, fromRate: number): Float32Array {
  if (fromRate === REALTIME_SAMPLE_RATE) return input;
  const ratio = fromRate / REALTIME_SAMPLE_RATE;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i += 1) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(left + 1, input.length - 1);
    const weight = position - left;
    out[i] = input[left] * (1 - weight) + input[right] * weight;
  }
  return out;
}

export function floatToPcm16Base64(samples: Float32Array): string {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function pcm16Base64ToFloat(base64: string): Float32Array<ArrayBuffer> {
  const binary = atob(base64);
  const samples = new Float32Array(binary.length >> 1);
  for (let i = 0; i < samples.length; i += 1) {
    const lo = binary.charCodeAt(i * 2);
    const hi = binary.charCodeAt(i * 2 + 1);
    let value = (hi << 8) | lo;
    if (value >= 0x8000) value -= 0x10000;
    samples[i] = value / 0x8000;
  }
  return samples;
}

export interface PcmCapture {
  context: AudioContext;
  stop(): void;
}

/**
 * Stream the microphone as ~100 ms PCM16 chunks (base64) and its level
 * (RMS, 0..1). Prefers an AudioWorklet; falls back to a ScriptProcessorNode.
 */
export async function startPcmCapture(
  stream: MediaStream,
  onChunk: (base64: string) => void,
  onLevel: (rms: number) => void,
): Promise<PcmCapture> {
  const Ctor = audioContextConstructor();
  if (!Ctor) throw new Error("audio_unsupported");
  const context = new Ctor();
  const source = context.createMediaStreamSource(stream);
  const sink = context.createGain();
  sink.gain.value = 0;
  sink.connect(context.destination);
  const target = Math.round((context.sampleRate * CHUNK_MS) / 1000);
  let pending: Float32Array[] = [];
  let pendingLength = 0;

  const push = (block: Float32Array) => {
    let sum = 0;
    for (let i = 0; i < block.length; i += 1) sum += block[i] * block[i];
    onLevel(Math.sqrt(sum / Math.max(1, block.length)));
    pending.push(block);
    pendingLength += block.length;
    if (pendingLength < target) return;
    const merged = new Float32Array(pendingLength);
    let offset = 0;
    for (const part of pending) {
      merged.set(part, offset);
      offset += part.length;
    }
    pending = [];
    pendingLength = 0;
    onChunk(floatToPcm16Base64(resampleTo24k(merged, context.sampleRate)));
  };

  let stopNode: () => void;
  try {
    if (!context.audioWorklet || typeof AudioWorkletNode === "undefined") throw new Error("no_worklet");
    await context.audioWorklet.addModule(workletUrl());
    const node = new AudioWorkletNode(context, WORKLET_NAME, { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
    node.port.onmessage = (event: MessageEvent<Float32Array>) => push(event.data);
    source.connect(node);
    node.connect(sink);
    stopNode = () => {
      node.port.onmessage = null;
      node.disconnect();
      node.port.close();
    };
  } catch {
    const node = context.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = (event) => push(event.inputBuffer.getChannelData(0).slice(0));
    source.connect(node);
    node.connect(sink);
    stopNode = () => {
      node.onaudioprocess = null;
      node.disconnect();
    };
  }
  if (context.state === "suspended") await context.resume().catch(() => undefined);
  return {
    context,
    stop() {
      stopNode();
      try {
        source.disconnect();
      } catch {
        // already gone
      }
      sink.disconnect();
      void context.close().catch(() => undefined);
    },
  };
}

/** Plays the model's PCM16 chunks back to back; `flush()` cuts it mid-word. */
export class PcmPlayer {
  private context: AudioContext | null = null;
  private playhead = 0;
  private sources = new Set<AudioBufferSourceNode>();
  /** Loudness of each scheduled chunk, by start time: the avatar's mouth. */
  private envelopes: { start: number; frames: Float32Array }[] = [];
  onPlayingChange: ((playing: boolean) => void) | null = null;

  constructor(private sinkId: string | null = null) {}

  private ensure(): AudioContext {
    if (!this.context) {
      const Ctor = audioContextConstructor();
      if (!Ctor) throw new Error("audio_unsupported");
      this.context = new Ctor();
      const withSink = this.context as AudioContext & { setSinkId?: (id: string) => Promise<void> };
      if (this.sinkId && typeof withSink.setSinkId === "function") {
        void withSink.setSinkId(this.sinkId).catch(() => undefined);
      }
    }
    if (this.context.state === "suspended") void this.context.resume().catch(() => undefined);
    return this.context;
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  play(base64: string): void {
    const samples = pcm16Base64ToFloat(base64);
    if (!samples.length) return;
    const context = this.ensure();
    const buffer = context.createBuffer(1, samples.length, REALTIME_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const node = context.createBufferSource();
    node.buffer = buffer;
    node.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.02, this.playhead);
    node.start(startAt);
    this.playhead = startAt + buffer.duration;
    this.envelopes = this.envelopes.filter((item) => item.start + item.frames.length * ENVELOPE_FRAME_S > context.currentTime);
    this.envelopes.push({ start: startAt, frames: speechEnvelope(samples, REALTIME_SAMPLE_RATE) });
    const wasPlaying = this.playing;
    this.sources.add(node);
    if (!wasPlaying) this.onPlayingChange?.(true);
    node.onended = () => {
      this.sources.delete(node);
      if (!this.playing) this.onPlayingChange?.(false);
    };
  }

  /** The user talks over the voice: stop now and drop what was queued. */
  flush(): void {
    for (const node of this.sources) {
      node.onended = null;
      try {
        node.stop();
      } catch {
        // already ended
      }
    }
    const was = this.playing;
    this.sources.clear();
    this.envelopes = [];
    this.playhead = 0;
    if (was) this.onPlayingChange?.(false);
  }

  /** Loudness of what is heard right now, 0..1. */
  level(): number {
    const now = this.context?.currentTime;
    if (now === undefined || !this.playing) return 0;
    const chunk = this.envelopes.find((item) => now >= item.start && now < item.start + item.frames.length * ENVELOPE_FRAME_S);
    return chunk ? envelopeLevel(chunk.frames, now - chunk.start) : 0;
  }

  close(): void {
    this.flush();
    void this.context?.close().catch(() => undefined);
    this.context = null;
  }
}
