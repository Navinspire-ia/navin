/**
 * Two-note completion chime for the live voice conversation.
 *
 * Played right before the assistant announces that a long run is over, so a
 * user who looked away hears the "call" even before the sentence starts.
 * Synthesized on the fly: no asset to ship, nothing to preload.
 */

import { audioContextConstructor } from "@/lib/audio";

const NOTES: ReadonlyArray<readonly [frequencyHz: number, offsetSec: number]> = [
  [659.25, 0],
  [880, 0.14],
];
const NOTE_LENGTH_SEC = 0.3;
const PEAK_GAIN = 0.16;

/** Short single blip: the sentence the user just said is on its way to the agent. */
const SENT_NOTES: ReadonlyArray<readonly [frequencyHz: number, offsetSec: number]> = [[1046.5, 0]];
const SENT_NOTE_LENGTH_SEC = 0.09;
const SENT_PEAK_GAIN = 0.07;

export function playCompletionChime(): Promise<void> {
  return playNotes(NOTES, NOTE_LENGTH_SEC, PEAK_GAIN);
}

export function playSentCue(): Promise<void> {
  return playNotes(SENT_NOTES, SENT_NOTE_LENGTH_SEC, SENT_PEAK_GAIN);
}

async function playNotes(
  notes: ReadonlyArray<readonly [frequencyHz: number, offsetSec: number]>,
  noteLengthSec: number,
  peakGain: number,
): Promise<void> {
  const AudioContextCtor = audioContextConstructor();
  if (!AudioContextCtor) return;
  let context: AudioContext;
  try {
    context = new AudioContextCtor();
  } catch {
    return;
  }
  try {
    await context.resume().catch(() => undefined);
    const start = context.currentTime;
    for (const [frequency, offset] of notes) {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start + offset);
      gain.gain.linearRampToValueAtTime(peakGain, start + offset + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + offset + noteLengthSec);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start(start + offset);
      oscillator.stop(start + offset + noteLengthSec + 0.02);
    }
    const lastOffset = notes[notes.length - 1][1];
    await new Promise((resolve) => setTimeout(resolve, (lastOffset + noteLengthSec) * 1000 + 80));
  } finally {
    void context.close().catch(() => undefined);
  }
}
