// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Browser audio helpers shared by the composer voice recorder and the meeting desk.
 *
 * Providers accept a narrow set of container formats, and the transcription
 * ingress caps every request by duration and payload size, so long recordings
 * have to be decoded and re-cut client side before they are sent.
 */

export function audioContextConstructor(): typeof AudioContext | undefined {
  if (typeof window === "undefined") return undefined;
  return window.AudioContext
    ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
}

export function blobToDataUrl(blob: Blob): Promise<string> {
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

/**
 * Convert any browser-recorded audio blob (typically webm/opus) to WAV
 * using the Web Audio API. This avoids sending unsupported formats
 * (e.g. webm) to ASR providers that only accept wav/mp3/mpeg.
 */
export async function convertBlobToWav(blob: Blob): Promise<string> {
  const AudioCtx = audioContextConstructor();
  if (!AudioCtx) return blobToDataUrl(blob);

  const arrayBuffer = await blob.arrayBuffer();
  const ctx = new AudioCtx();
  try {
    const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
    return blobToDataUrl(audioBufferToWav(audioBuffer));
  } finally {
    void ctx.close();
  }
}

/**
 * Decode an audio blob, optionally resampling through the context sample rate.
 * ASR models work at 16 kHz, so decoding there keeps payloads small.
 */
export async function decodeAudioBlob(
  blob: Blob,
  options: { sampleRate?: number } = {},
): Promise<AudioBuffer | null> {
  const AudioCtx = audioContextConstructor();
  if (!AudioCtx) return null;
  const arrayBuffer = await blob.arrayBuffer();
  let ctx: AudioContext;
  try {
    ctx = options.sampleRate
      ? new AudioCtx({ sampleRate: options.sampleRate })
      : new AudioCtx();
  } catch {
    ctx = new AudioCtx();
  }
  try {
    return await ctx.decodeAudioData(arrayBuffer);
  } finally {
    void ctx.close();
  }
}

export type AudioSegment = {
  /** WAV payload ready for the transcription ingress. */
  dataUrl: string;
  /** Offset of the segment inside the source file. */
  startMs: number;
  durationMs: number;
};

/**
 * Cut a decoded buffer into mono WAV segments no longer than `segmentSec`.
 *
 * Downmixing to mono roughly halves the payload, which keeps a 90 s segment
 * well under the default 25 MB upload cap.
 */
export type IncrementalWav = {
  dataUrl: string;
  durationMs: number;
  nextFrame: number;
};

/**
 * Decode a complete recording blob and return only the audio after ``startFrame``.
 *
 * MediaRecorder timeslices are not standalone files. The caller must assemble
 * every part from the start of the take, then pass that blob here so each
 * flush can send a clean 16 kHz mono WAV of the new speech only.
 */
export async function incrementalWavFromBlob(
  blob: Blob,
  options: {
    startFrame?: number;
    sampleRate?: number;
    minDurationMs?: number;
  } = {},
): Promise<IncrementalWav | null> {
  const startFrame = Math.max(0, options.startFrame ?? 0);
  const buffer = await decodeAudioBlob(blob, {
    sampleRate: options.sampleRate ?? 16_000,
  });
  if (!buffer) throw new Error("decode");
  const mono = downmixToMono(buffer);
  if (mono.length <= startFrame) return null;
  const slice = mono.subarray(startFrame);
  const durationMs = Math.round((slice.length / buffer.sampleRate) * 1000);
  if (durationMs < (options.minDurationMs ?? 0)) return null;
  return {
    dataUrl: await blobToDataUrl(pcmToWavBlob(slice, buffer.sampleRate)),
    durationMs,
    nextFrame: mono.length,
  };
}

export async function segmentAudioBlob(
  blob: Blob,
  options: { segmentSec: number; sampleRate?: number },
): Promise<AudioSegment[]> {
  const buffer = await decodeAudioBlob(blob, { sampleRate: options.sampleRate ?? 16_000 });
  if (!buffer) throw new Error("decode");

  const segmentSec = Math.max(5, options.segmentSec);
  const sampleRate = buffer.sampleRate;
  const frames = buffer.length;
  const framesPerSegment = Math.max(1, Math.floor(segmentSec * sampleRate));
  const mono = downmixToMono(buffer);

  const segments: AudioSegment[] = [];
  for (let offset = 0; offset < frames; offset += framesPerSegment) {
    const slice = mono.subarray(offset, Math.min(frames, offset + framesPerSegment));
    if (slice.length === 0) continue;
    segments.push({
      dataUrl: await blobToDataUrl(pcmToWavBlob(slice, sampleRate)),
      startMs: Math.round((offset / sampleRate) * 1000),
      durationMs: Math.round((slice.length / sampleRate) * 1000),
    });
  }
  return segments;
}

function downmixToMono(buffer: AudioBuffer): Float32Array {
  if (buffer.numberOfChannels === 1) return buffer.getChannelData(0);
  const length = buffer.length;
  const mono = new Float32Array(length);
  for (let channel = 0; channel < buffer.numberOfChannels; channel += 1) {
    const data = buffer.getChannelData(channel);
    for (let index = 0; index < length; index += 1) mono[index] += data[index];
  }
  for (let index = 0; index < length; index += 1) mono[index] /= buffer.numberOfChannels;
  return mono;
}

/** Encode an AudioBuffer as a 16-bit PCM WAV Blob. */
export function audioBufferToWav(buffer: AudioBuffer): Blob {
  const numChannels = buffer.numberOfChannels;
  const length = buffer.length;
  const channels: Float32Array[] = [];
  for (let channel = 0; channel < numChannels; channel += 1) {
    channels.push(buffer.getChannelData(channel));
  }

  const interleaved = new Float32Array(length * numChannels);
  for (let index = 0; index < length; index += 1) {
    for (let channel = 0; channel < numChannels; channel += 1) {
      interleaved[index * numChannels + channel] = channels[channel][index];
    }
  }
  return pcmToWavBlob(interleaved, buffer.sampleRate, numChannels);
}

function pcmToWavBlob(
  samples: Float32Array,
  sampleRate: number,
  numChannels = 1,
): Blob {
  const bitsPerSample = 16;
  const pcm = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }

  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const dataSize = pcm.byteLength;
  const headerSize = 44;
  const output = new ArrayBuffer(headerSize + dataSize);
  const view = new DataView(output);

  writeString(view, 0, "RIFF");
  view.setUint32(4, headerSize + dataSize - 8, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataSize, true);
  new Int16Array(output, headerSize).set(pcm);

  return new Blob([output], { type: "audio/wav" });
}

function writeString(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}
