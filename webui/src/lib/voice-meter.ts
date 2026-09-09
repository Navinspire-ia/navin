// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Microphone level source for the live conversation.
 *
 * Runs on the audio rendering thread (AudioWorklet), so levels keep coming
 * when the tab is hidden or the desktop window is behind another one:
 * requestAnimationFrame stops there and timers are throttled, which is how a
 * voice detector goes deaf. Older WebKit builds fall back to a
 * ScriptProcessorNode, which is driven by the audio clock as well.
 */

export const VOICE_METER_FRAME_MS = 32;

export interface VoiceMeter {
  /** Disconnect everything; the callback never fires again. */
  stop(): void;
  kind: "worklet" | "script-processor";
}

const WORKLET_NAME = "navin-voice-meter";

// Kept as a string so it can be loaded from a Blob URL: no extra asset, works
// from the packaged desktop shells and the dev server alike.
const WORKLET_SOURCE = `
class NavinVoiceMeter extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.frame = Math.max(128, Math.round((options.processorOptions && options.processorOptions.frame) || 1536));
    this.sum = 0;
    this.count = 0;
  }
  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (channel) {
      let sum = 0;
      for (let i = 0; i < channel.length; i += 1) sum += channel[i] * channel[i];
      this.sum += sum;
      this.count += channel.length;
    } else {
      // No input yet (track muted / not started): count the silence so the
      // cadence stays regular.
      this.count += 128;
    }
    if (this.count >= this.frame) {
      this.port.postMessage(Math.sqrt(this.sum / this.count));
      this.sum = 0;
      this.count = 0;
    }
    return true;
  }
}
registerProcessor(${JSON.stringify(WORKLET_NAME)}, NavinVoiceMeter);
`;

let workletModuleUrl: string | null = null;

function workletUrl(): string {
  if (!workletModuleUrl) {
    workletModuleUrl = URL.createObjectURL(
      new Blob([WORKLET_SOURCE], { type: "application/javascript" }),
    );
  }
  return workletModuleUrl;
}

/** Silent sink: keeps the graph rendering without playing the microphone back. */
function silentSink(context: AudioContext): GainNode {
  const sink = context.createGain();
  sink.gain.value = 0;
  sink.connect(context.destination);
  return sink;
}

export function rmsOfSamples(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) sum += samples[index] * samples[index];
  return Math.sqrt(sum / samples.length);
}

async function createWorkletMeter(
  context: AudioContext,
  source: MediaStreamAudioSourceNode,
  onLevel: (rms: number) => void,
): Promise<VoiceMeter> {
  if (!context.audioWorklet || typeof AudioWorkletNode === "undefined") {
    throw new Error("audio_worklet_unavailable");
  }
  await context.audioWorklet.addModule(workletUrl());
  const frame = Math.round((context.sampleRate * VOICE_METER_FRAME_MS) / 1000);
  const node = new AudioWorkletNode(context, WORKLET_NAME, {
    numberOfInputs: 1,
    numberOfOutputs: 1,
    channelCount: 1,
    channelCountMode: "explicit",
    processorOptions: { frame },
  });
  const sink = silentSink(context);
  node.port.onmessage = (event: MessageEvent<number>) => {
    if (typeof event.data === "number") onLevel(event.data);
  };
  source.connect(node);
  node.connect(sink);
  return {
    kind: "worklet",
    stop() {
      node.port.onmessage = null;
      try {
        source.disconnect(node);
      } catch {
        // already gone
      }
      node.disconnect();
      sink.disconnect();
      node.port.close();
    },
  };
}

function createScriptProcessorMeter(
  context: AudioContext,
  source: MediaStreamAudioSourceNode,
  onLevel: (rms: number) => void,
): VoiceMeter {
  const ctx = context as AudioContext & {
    createScriptProcessor?: (size: number, inputs: number, outputs: number) => ScriptProcessorNode;
  };
  if (typeof ctx.createScriptProcessor !== "function") {
    throw new Error("script_processor_unavailable");
  }
  // 2048 frames is 43 ms at 48 kHz, close enough to the worklet cadence.
  const node = ctx.createScriptProcessor(2048, 1, 1);
  const sink = silentSink(context);
  node.onaudioprocess = (event: AudioProcessingEvent) => {
    onLevel(rmsOfSamples(event.inputBuffer.getChannelData(0)));
  };
  source.connect(node);
  node.connect(sink);
  return {
    kind: "script-processor",
    stop() {
      node.onaudioprocess = null;
      try {
        source.disconnect(node);
      } catch {
        // already gone
      }
      node.disconnect();
      sink.disconnect();
    },
  };
}

/**
 * Start measuring *stream* and call *onLevel* with the RMS (0..1) of each
 * ~32 ms frame. Prefers the worklet, falls back to a ScriptProcessorNode.
 */
export async function createVoiceMeter(
  context: AudioContext,
  stream: MediaStream,
  onLevel: (rms: number) => void,
): Promise<VoiceMeter> {
  const source = context.createMediaStreamSource(stream);
  try {
    return await createWorkletMeter(context, source, onLevel);
  } catch {
    return createScriptProcessorMeter(context, source, onLevel);
  }
}
